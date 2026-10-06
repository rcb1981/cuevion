"""Authenticated Custom IMAP refresh composition; no provider action authority.

The callback is private to the provider adapter. It never adds response fields
or consumes frontend identities. Errors are isolated from the working client.
"""
from __future__ import annotations

import re
import time
from email import message_from_bytes
from email.feedparser import BytesFeedParser

from cuevion_mailbox.imap_sync import prepare_imap_state, sync_imap_snapshot
from cuevion_mailbox.imap_deadline import (
    DeadlineImapFile, ImapDeadlineExceeded, ImapDurableBudget,
    MUTATION_LIMIT, RECOVERY_FETCH_SECONDS, RECOVERY_UID_LIMIT, bounded_imap_connect,
)
from cuevion_mailbox.repository_contract import (
    MailboxProvider, MailboxReadAuthority,
)
from cuevion_mailbox.runtime import (
    build_active_write_mailbox_repositories,
    build_production_bootstrap_mailbox_repositories,
    production_bootstrap_authority_enabled,
)


def imap_durable_write_enabled(environment):
    return (
        environment.get("VERCEL_ENV") == "preview"
        and environment.get("CUEVION_MAILBOX_POSTGRES_MODE") == "active_write"
    ) or production_bootstrap_authority_enabled(environment)


def _close_private_provider_connection(mailbox):
    if getattr(mailbox, "_cuevion_durable_closed", False) is True:
        return
    sock = getattr(mailbox, "sock", None)
    if sock is None:
        return
    mailbox._cuevion_durable_closed = True
    for resource in (sock, getattr(mailbox, "file", None)):
        if resource is not None:
            try:
                resource.close()
            except Exception:
                pass


def _recover_selected_uid(mailbox, uid, budget=None):
    """Read an exact copy on the already selected read-only folder, with flags."""
    original_file = original_timeout = None
    sock = None
    recovered = False
    try:
        if budget is not None:
            budget.check()
            sock = getattr(mailbox, "sock", None)
            original_file = getattr(mailbox, "file", None)
            if sock is None or original_file is None or not callable(getattr(original_file, "read1", None)):
                return None
            original_timeout = sock.gettimeout()
            mailbox.file = DeadlineImapFile(original_file, sock, budget)
            sock.settimeout(min(RECOVERY_FETCH_SECONDS, budget.remaining()))
        status, values = mailbox.uid("FETCH", uid, "(UID FLAGS BODY.PEEK[])")
        if budget is not None:
            budget.check()
        if status != "OK" or type(values) not in (list, tuple) or len(values) not in (1, 2):
            return None
        literal = values[0]
        if type(literal) is not tuple or len(literal) != 2:
            return None
        metadata, raw = literal
        if type(metadata) is not bytes or len(metadata) > 4096 or type(raw) is not bytes or len(raw) > 25 * 1024 * 1024:
            return None
        metadata = metadata.decode("ascii", errors="strict")
        # Like the existing recent-fetch adapter, accept attributes before or
        # after the literal. Still require one exact UID, FLAGS, and literal.
        match = re.fullmatch(r"[1-9][0-9]* \((.*?)BODY\[\] \{([0-9]+)\}(\))?", metadata)
        if match is None or int(match[2]) != len(raw):
            return None
        if match[1] and not match[1].endswith(" "):
            return None
        if len(values) == 1:
            if match[3] != ")":
                return None
            trailing = ""
        else:
            if match[3] is not None or type(values[1]) is not bytes or len(values[1]) > 4096:
                return None
            trailing = values[1].decode("ascii", errors="strict")
            if not trailing.endswith(")"):
                return None
            trailing = trailing[:-1]
        fields = " ".join((match[1].strip(), trailing.strip())).strip()
        uid_fields = re.findall(r"(?:^| )UID ([1-9][0-9]*)(?= |$)", fields)
        flag_fields = re.findall(r"(?:^| )FLAGS \(([^()]*)\)(?= |$)", fields)
        if uid_fields != [uid] or len(flag_fields) != 1:
            return None
        remainder = re.sub(r"(?:^| )UID [1-9][0-9]*(?= |$)", "", fields)
        remainder = re.sub(r"(?:^| )FLAGS \([^()]*\)(?= |$)", "", remainder)
        if remainder.strip():
            return None
        flags = flag_fields[0].split()
        if budget is None:
            message = message_from_bytes(raw)
        else:
            parser = BytesFeedParser()
            for offset in range(0, len(raw), 16384):
                budget.check()
                parser.feed(raw[offset:offset + 16384])
            message = parser.close()
            budget.check()
        recovered = True
        return (message, "\\Seen" not in flags, uid, "\\Flagged" in flags)
    except (TimeoutError, OSError):
        raise ImapDeadlineExceeded("IMAP recovery deadline") from None
    except Exception:
        return None
    finally:
        if original_file is not None:
            mailbox.file = original_file
        if budget is not None and not recovered:
            # Failed extra work must not leave a partially consumed connection
            # or add an unbounded logout wait to the already-built response.
            _close_private_provider_connection(mailbox)
        elif sock is not None and sock.fileno() != -1:
            sock.settimeout(original_timeout)


def prepare_authenticated_imap_refresh(*, environment, member, mailbox, repositories=None, budget=None):
    """Fence provider evidence against state captured BEFORE the provider read."""
    if not imap_durable_write_enabled(environment):
        return None
    budget = ImapDurableBudget() if budget is None else budget
    try:
        authority = MailboxReadAuthority(
            workspace_id=getattr(member, "workspace_id"),
            owner_user_id=getattr(member, "user_id"), mailbox_id=mailbox["mailboxId"],
            provider=MailboxProvider.CUSTOM_IMAP,
            provider_account_identity=mailbox["email"].casefold(),
        )
        if repositories is None:
            repositories = (
                build_production_bootstrap_mailbox_repositories(environment, connect=bounded_imap_connect(budget))
                if production_bootstrap_authority_enabled(environment)
                else build_active_write_mailbox_repositories(environment, connect=bounded_imap_connect(budget))
            )
        state, initialize_state = prepare_imap_state(repositories, authority)
        budget.check()
    except (TimeoutError, ImapDeadlineExceeded):
        print("cuevion_mailbox_active_write custom_imap prepare_timeout")
        return None
    except Exception:
        print("cuevion_mailbox_active_write custom_imap prepare_failed")
        return None
    finally:
        budget.pause()

    def consume(*, mailbox, folder, uid_validity, uid_search_response, messages, warnings, limit):
        from api.inboxes.imap_snapshot import _parse_uid_search_response

        try:
            budget.resume()
            uid_set = _parse_uid_search_response(uid_search_response)
            if uid_set is None or warnings or uid_validity is None:
                print("cuevion_mailbox_active_write custom_imap incomplete_snapshot")
                return None
            fetched = [item[2] for item in messages]
            if fetched != list(reversed(uid_set[-limit:])):
                print("cuevion_mailbox_active_write custom_imap incomplete_snapshot")
                return None
            uidnext = None
            try:
                tag, values = mailbox.response("UIDNEXT")
                if tag == "UIDNEXT" and type(values) in (list, tuple) and len(values) == 1:
                    value = values[0].decode("ascii") if type(values[0]) is bytes else values[0]
                    if type(value) is str and re.fullmatch(r"[1-9][0-9]{0,9}", value):
                        number = int(value)
                        # SELECT's UIDNEXT may precede arrivals observed by the
                        # later SEARCH; inconsistent evidence is unavailable.
                        if 1 <= number <= 4_294_967_296 and (not uid_set or number > int(uid_set[-1])):
                            uidnext = number
            except Exception:
                pass
            result = sync_imap_snapshot(
                repositories=repositories, authority=authority, folder=folder,
                uid_validity=uid_validity, uid_set=uid_set, uidnext_observed=uidnext,
                messages=messages, committed_at_millis=time.time_ns() // 1_000_000,
                recover_uid=lambda uid: _recover_selected_uid(mailbox, uid, budget),
                prepared_state=state, initialize_state=initialize_state, budget=budget,
                recovery_limit=RECOVERY_UID_LIMIT,
                mutation_limit=MUTATION_LIMIT,
            )
            print("cuevion_mailbox_active_write custom_imap " + result.status)
            return result
        except (TimeoutError, ImapDeadlineExceeded):
            _close_private_provider_connection(mailbox)
            print("cuevion_mailbox_active_write custom_imap timeout")
            return None
        except Exception as exc:
            _close_private_provider_connection(mailbox)
            import psycopg
            if isinstance(exc, (psycopg.errors.LockNotAvailable, psycopg.errors.QueryCanceled)):
                print("cuevion_mailbox_active_write custom_imap database_timeout")
            else:
                print("cuevion_mailbox_active_write custom_imap failed")
            return None
        finally:
            budget.pause()

    return consume
