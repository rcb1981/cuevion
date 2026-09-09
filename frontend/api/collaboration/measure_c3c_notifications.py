"""Reproducible command accounting on a disposable local Unix-socket Redis.

Run with PYTHONDONTWRITEBYTECODE=1 and PYTHONPATH=frontend. This module never
resolves hosted Redis credentials: every operation receives the local fixture
transport. It prints aggregate counters only, never notification/message data.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path

from . import redis_store, test_notifications as fixture
from api.notifications import store


def baseline_scripts(path: str) -> tuple[dict[str, str], str]:
    """Read only an explicitly supplied baseline; evaluate string concatenations."""
    source = Path(path).read_text(encoding="utf-8")
    assignments = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            assignments[node.targets[0].id] = node.value
    cache = {}

    def string_value(node):
        if isinstance(node, ast.Constant) and type(node.value) is str:
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in cache:
                cache[node.id] = string_value(assignments[node.id])
            return cache[node.id]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return string_value(node.left) + string_value(node.right)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "strip" and not node.args and not node.keywords):
            return string_value(node.func.value).strip()
        raise ValueError("Baseline Lua must be literal string concatenations")

    names = ("_CREATE_V2_THREAD_LUA", "_APPEND_V2_OWNER_IDEMPOTENT_LUA")
    return ({name: string_value(assignments[name]) for name in names},
            hashlib.sha256(source.encode("utf-8")).hexdigest())


def instrument_script(script: str) -> str:
    # Count successful notification-key write commands in memory. No extra
    # Redis command is introduced, so INFO commandstats remains comparable.
    return r"""
local actualRedis = redis
local notificationWrites = {}
local writes = {SET=true,HSET=true,HDEL=true,ZADD=true,ZREM=true,PEXPIRE=true,PEXPIREAT=true,EXPIRE=true,DEL=true}
local prefix = 'cuevion:collab:v2:{cuevion-collab-v2}:notifications:'
local redis = {sha1hex=actualRedis.sha1hex}
redis.call = function(...)
  local args = {...}
  local result = actualRedis.call(unpack(args))
  if writes[args[1]] and type(args[2]) == 'string' and string.sub(args[2],1,#prefix) == prefix then
    local recipient = string.gsub(args[2], ':[^:]+$', '')
    local counts = notificationWrites[recipient] or {}
    counts[args[1]] = (counts[args[1]] or 0) + 1
    notificationWrites[recipient] = counts
  end
  return result
end
local function operation()
""" + script + r"""
end
local result = operation()
return cjson.encode({result=result,notificationWrites=notificationWrites})
"""


class Measurement:
    def __init__(self, case, baseline=None):
        self.case = case
        self.baseline = baseline
        self.results = []
        self.labels = {
            store.build_notification_keys(fixture.WORKSPACE, user)[0].rsplit(":", 1)[0]: label
            for user, label in ((fixture.OWNER, "owner"), (fixture.FIRST, "participant"), (fixture.SECOND, "second_participant"))
        }

    def baseline_transport(self, command):
        command = list(command)
        if command[0] == "EVAL":
            if command[1] == redis_store._CREATE_V2_THREAD_LUA:
                command[1] = self.baseline["_CREATE_V2_THREAD_LUA"]
            elif command[1] == redis_store._APPEND_V2_OWNER_IDEMPOTENT_LUA:
                command[1] = self.baseline["_APPEND_V2_OWNER_IDEMPOTENT_LUA"]
                command = command[:3 + int(command[2]) + 15]
        return self.case.client.transport(command)

    def measure(self, name, action, *, baseline=False):
        self.case.client.command(["CONFIG", "RESETSTAT"])
        outer = []
        notification_writes = {}
        result_bytes = 0

        def transport(command):
            nonlocal result_bytes
            command = list(command)
            outer.append(command[0])
            if baseline and command[0] == "EVAL" and command[1] == redis_store._APPEND_V2_OWNER_IDEMPOTENT_LUA:
                command[1] = self.baseline["_APPEND_V2_OWNER_IDEMPOTENT_LUA"]
                command = command[:3 + int(command[2]) + 15]
            is_eval = command[0] == "EVAL"
            if is_eval:
                command[1] = instrument_script(command[1])
            payload = self.case.client.transport(command)
            if is_eval:
                measured = json.loads(payload["result"])
                for recipient, counts in measured["notificationWrites"].items():
                    label = self.labels[recipient]
                    accumulator = notification_writes.setdefault(label, Counter())
                    accumulator.update(counts)
                payload = {"result": measured["result"]}
            result_bytes += len(json.dumps(payload["result"], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            return payload

        result = action(transport)
        if result.get("status") != "ok":
            raise AssertionError({"operation": name, "status": result.get("status"), "error": result.get("error")})
        stats = self.case.client.command(["INFO", "commandstats"])
        total = {}
        for line in stats.splitlines():
            if line.startswith("cmdstat_"):
                command, fields = line.split(":", 1)
                command = command.removeprefix("cmdstat_")
                if command == "config|resetstat":
                    continue
                total[command] = int(fields.split("calls=", 1)[1].split(",", 1)[0])
        nested = Counter(total)
        nested.subtract(Counter(command.lower() for command in outer))
        if any(count < 0 for count in nested.values()):
            raise AssertionError("Command accounting underflow")
        entry = {
            "operation": name, "outer_calls": len(outer), "outer_commands": outer,
            "eval_count": outer.count("EVAL"), "nested_commands": sum(nested.values()),
            "nested_by_command": {key: value for key, value in sorted(nested.items()) if value},
            "notification_writes_per_recipient": {label: dict(sorted(counts.items())) for label, counts in sorted(notification_writes.items())},
            "redis_result_bytes": result_bytes,
        }
        if "notifications" in result:
            entry["returned_rows"] = len(result["notifications"])
        self.results.append(entry)
        return result

    def reset(self, *, participants=(fixture.FIRST,), baseline=False):
        self.case.client.command(["FLUSHALL"])
        self.case.transport = self.baseline_transport if baseline else self.case.client.transport
        self.case.create(participants)

    def seed_capacity(self):
        prototype = self.case.notifications(fixture.FIRST)[0]
        keys = store.build_notification_keys(fixture.WORKSPACE, fixture.FIRST)
        self.case.client.command(["DEL", *keys])
        args = []
        for index in range(1000):
            row = {**prototype, "recipientUserId": fixture.FIRST,
                   "notificationId": "ntf_" + f"{index:040x}", "kind": "shared_message",
                   "activityId": f"{index:022d}", "createdAt": prototype["createdAt"] - 2000 + index,
                   "expiresAt": prototype["expiresAt"] - 3000}
            for field in ("v", "createdAt", "expiresAt"):
                row[field] = str(row[field])
            args.extend((row["notificationId"], json.dumps(row, separators=(",", ":")), row["createdAt"], row["expiresAt"]))
        script = "for i=1,#ARGV,4 do redis.call('HSET',KEYS[1],ARGV[i],ARGV[i+1]); redis.call('ZADD',KEYS[2],ARGV[i+2],ARGV[i]); redis.call('ZADD',KEYS[3],ARGV[i+3],ARGV[i]); end; for _,key in ipairs(KEYS) do redis.call('PEXPIREAT',key,ARGV[4]) end; return 1"
        self.case.client.command(["EVAL", script, 3, *keys, *args])


def run(baseline_path=None):
    baseline, baseline_hash = baseline_scripts(baseline_path) if baseline_path else (None, None)
    case = fixture.NotificationMutationRedisTests("test_create_start_owner_suppression_and_replay")
    case.setUpClass()
    try:
        case.setUp()
        measured = Measurement(case, baseline)
        version = next(line.removeprefix("redis_version:") for line in case.client.command(["INFO", "server"]).splitlines() if line.startswith("redis_version:"))
        if baseline:
            for visibility in ("shared", "internal"):
                measured.reset(baseline=True)
                measured.measure("before_c3c_owner_" + visibility, lambda t, visibility=visibility: case.append(visibility=visibility, transport=t), baseline=True)
                measured.measure("before_c3c_owner_" + visibility + "_retry", lambda t, visibility=visibility: case.append(visibility=visibility, transport=t), baseline=True)
        for visibility in ("shared", "internal"):
            measured.reset()
            measured.measure("owner_" + visibility, lambda t, visibility=visibility: case.append(visibility=visibility, transport=t))
            measured.measure("owner_" + visibility + "_retry", lambda t, visibility=visibility: case.append(visibility=visibility, transport=t))
        measured.reset()
        guest = case.guest()
        measured.measure("guest_reply", lambda t: case.guest_reply(guest, transport=t))
        measured.measure("guest_reply_retry", lambda t: case.guest_reply(guest, transport=t))
        measured.reset(participants=())
        participant = case.thread()["participants"][0]
        def add(transport):
            return fixture.mutations.add_v2_participant(case.capability(action="manage_participants"), participant, command_transport=transport)
        measured.measure("participant_add", add)
        measured.measure("participant_add_retry", add)
        measured.reset()
        for index in range(49):
            result = case.append(key="page-" + str(index))
            if result["status"] != "ok":
                raise AssertionError("Could not prepare list fixture")
        measured.measure("summary", lambda t: store.summary(fixture.WORKSPACE, fixture.FIRST, command_transport=t))
        page = measured.measure("list_first_50", lambda t: store.list_notifications(fixture.WORKSPACE, fixture.FIRST, command_transport=t))
        if len(page["notifications"]) != 50:
            raise AssertionError("Expected exactly 50 list fixture rows")
        notification_id = page["notifications"][0]["notificationId"]
        measured.measure("mark_read", lambda t: store.mark_read(fixture.WORKSPACE, fixture.FIRST, notification_id, command_transport=t))
        measured.measure("mark_read_retry", lambda t: store.mark_read(fixture.WORKSPACE, fixture.FIRST, notification_id, command_transport=t))
        measured.reset()
        measured.seed_capacity()
        capacity_result = measured.measure("owner_shared_at_1000_capacity", lambda t: case.append(key="capacity", transport=t))
        keys = store.build_notification_keys(fixture.WORKSPACE, fixture.FIRST)
        if case.client.command(["HLEN", keys[0]]) != 1000 or case.client.command(["HGET", keys[0], "ntf_" + "0" * 40]) is not None:
            raise AssertionError("Capacity did not prune the oldest record deterministically")
        canonical = redis_store._load_v2_thread("A" * 22, command_transport=case.client.transport)
        if (canonical.get("status") != "ok" or len(canonical.record["messages"]) != 1
                or canonical.record["messages"][0]["id"] != capacity_result["message"]["id"]):
            raise AssertionError("Capacity-path message was not committed canonically")
        latest = case.notifications(fixture.FIRST)[0]
        if latest["activityId"] != capacity_result["message"]["id"]:
            raise AssertionError("Capacity-path notification does not target the committed activity")
        measured.measure("owner_shared_at_capacity_retry", lambda t: case.append(key="capacity", transport=t))
        return {"local_only": True, "redis_version": version, "baseline_source_sha256": baseline_hash,
                "team_authority": "isolated fixture active membership resolver", "measurements": measured.results}
    finally:
        case.doCleanups()
        case.tearDownClass()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-store", help="Exact saved baseline redis_store.py file for before/after Lua comparison")
    args = parser.parse_args()
    print(json.dumps(run(args.baseline_store), indent=2, sort_keys=True))
