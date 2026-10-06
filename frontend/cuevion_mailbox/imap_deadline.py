"""Awaited IMAP-only I/O deadlines; no background persistence or global settings."""
from __future__ import annotations

import asyncio
import time

import psycopg
from psycopg import waiting


DURABLE_BUDGET_SECONDS = 5.0
CONNECTION_BUDGET_SECONDS = 1.0
STATEMENT_TIMEOUT_MILLIS = 1000
LOCK_TIMEOUT_MILLIS = 250
RECOVERY_FETCH_SECONDS = 0.5
RECOVERY_UID_LIMIT = 10
MUTATION_LIMIT = 20


class ImapDeadlineExceeded(TimeoutError):
    pass


class ImapDurableBudget:
    """One cumulative budget, paused during the existing visible provider read."""
    def __init__(self, seconds=DURABLE_BUDGET_SECONDS):
        if not 0 < seconds <= DURABLE_BUDGET_SECONDS:
            raise ValueError("invalid IMAP durable budget")
        self.left = seconds
        self.deadline = time.monotonic() + seconds

    def remaining(self):
        value = self.deadline - time.monotonic()
        if value <= 0:
            raise ImapDeadlineExceeded("IMAP durable deadline")
        return value

    def check(self):
        self.remaining()

    def pause(self):
        self.left = max(0, self.deadline - time.monotonic())

    def resume(self):
        self.deadline = time.monotonic() + self.left
        self.check()


class _Cursor:
    def __init__(self, connection, cursor):
        self.connection, self.cursor = connection, cursor

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def execute(self, *args, **kwargs):
        self.connection.run(self.cursor.execute(*args, **kwargs))
        return self

    def fetchall(self):
        return self.connection.run(self.cursor.fetchall())

    def close(self):
        # Cursor close is local cleanup, including after a hard connection close.
        self.connection.loop.run_until_complete(self.cursor.close())


class _Connection:
    """Small synchronous facade for the unchanged shared repository interface."""
    def __init__(self, connection, loop, budget):
        self.connection, self.loop, self.budget = connection, loop, budget

    @property
    def pgconn(self):
        return self.connection.pgconn

    @property
    def info(self):
        return self.connection.info

    @property
    def autocommit(self):
        return self.connection.autocommit

    def run(self, operation):
        try:
            remaining = self.budget.remaining()
        except ImapDeadlineExceeded:
            operation.close()
            self.pgconn.finish()
            raise
        try:
            return self.loop.run_until_complete(asyncio.wait_for(operation, remaining))
        except TimeoutError:
            self.pgconn.finish()
            raise ImapDeadlineExceeded("IMAP durable deadline") from None

    def cursor(self):
        self.budget.check()
        return _Cursor(self, self.connection.cursor())

    def commit(self):
        # Reserve response/cleanup time; an expired plan never sends COMMIT.
        if self.budget.remaining() < 0.1:
            self.pgconn.finish()
            raise ImapDeadlineExceeded("IMAP durable commit deadline")
        return self.run(self.connection.commit())

    def rollback(self):
        if self.connection.closed:
            return
        try:
            self.run(self.connection.rollback())
        except (ImapDeadlineExceeded, psycopg.Error):
            self.pgconn.finish()

    def close(self):
        self.pgconn.finish()
        self.loop.close()


def bounded_imap_connect(budget):
    """Connector injected only into IMAP's existing reader/writer composition."""
    class DeadlineConnection(psycopg.AsyncConnection):
        async def wait(self, gen, interval=0.05, timeout=None):
            # Direct bounded polling avoids the driver's cancellation path,
            # which otherwise grants an additional five-second cleanup wait.
            try:
                return await asyncio.wait_for(waiting.wait_async(
                    gen, self.pgconn.socket, interval=interval,
                ), budget.remaining())
            except (asyncio.CancelledError, TimeoutError):
                self.pgconn.finish()
                raise

    def connect(conninfo, *, autocommit=False, connect_timeout=1):
        loop = asyncio.new_event_loop()
        try:
            remaining = min(CONNECTION_BUDGET_SECONDS, budget.remaining())
            connection = loop.run_until_complete(asyncio.wait_for(
                DeadlineConnection.connect(
                    conninfo, autocommit=autocommit, prepare_threshold=None,
                    connect_timeout=connect_timeout,
                    options=(f"-c statement_timeout={STATEMENT_TIMEOUT_MILLIS} "
                             f"-c lock_timeout={LOCK_TIMEOUT_MILLIS} "
                             f"-c idle_in_transaction_session_timeout={STATEMENT_TIMEOUT_MILLIS}"),
                ), remaining,
            ))
            return _Connection(connection, loop, budget)
        except BaseException:
            loop.close()
            raise

    return connect


class DeadlineImapFile:
    """Keep buffered provider bytes, but bound every underlying socket read.

    BufferedReader.read(n) can make many socket reads under trickle traffic.
    read1 performs at most one: check the absolute deadline between chunks.
    """
    def __init__(self, file, sock, budget):
        self.file, self.sock, self.budget = file, sock, budget
        self.deadline = time.monotonic() + min(RECOVERY_FETCH_SECONDS, budget.remaining())

    def _read(self, size):
        remaining = min(self.budget.remaining(), self.deadline - time.monotonic())
        if remaining <= 0:
            raise ImapDeadlineExceeded("IMAP recovery deadline")
        self.sock.settimeout(remaining)
        return self.file.read1(size)

    def read(self, size):
        if size > 25 * 1024 * 1024:
            raise ImapDeadlineExceeded("IMAP recovery literal bound")
        parts = []
        while size:
            part = self._read(min(size, 65536))
            if not part:
                break
            parts.append(part)
            size -= len(part)
        return b"".join(parts)

    def readline(self, size=-1):
        parts = []
        while size != 0:
            part = self._read(1)
            if not part:
                break
            parts.append(part)
            if part == b"\n":
                break
            size -= 1
        return b"".join(parts)
