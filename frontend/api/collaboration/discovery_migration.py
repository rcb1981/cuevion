"""Explicit, operator-invoked historical discovery repair. Never import in routes.

One call does at most one SCAN and processes five candidates. There is no CLI,
startup hook, background loop, default execution, or public HTTP operation.
See C3B1C_DISCOVERY_MIGRATION.md for invocation and checkpoint semantics.
"""
from __future__ import annotations

if __name__ != "api.collaboration.discovery_migration":
    raise ImportError("Import the manual migration as api.collaboration.discovery_migration")

import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile

from . import authorization as auth
from . import redis_store as store
from .models import is_v2_opaque_id, normalize_v2_source_ref

PAGE_SIZE = 5
SCAN_COUNT = 100
MAX_PENDING = 1000
MAX_CHECKPOINT_BYTES = 400_000
SCAN_PATTERN = store.V2_THREAD_KEY_PREFIX + "*"
_COUNTERS = ("examined", "enrolled", "wouldEnroll", "alreadyPresent", "alreadyPlanned",
             "skippedInvalid", "skippedUnauthorized", "unresolvedIdentity",
             "staleEntitlement", "missing", "capacity", "retry", "deferred")


class MigrationError(Exception):
    """Only a fixed, content-free code may leave the manual operation."""


_BATCH_COMMON = store._V2_DISCOVERY_LUA + r"""
local MIGRATION_SOURCE_PREFIX = 'cuevion:collab:v2:{cuevion-collab-v2}:source-thread:'
local function boundedBatch(keys)
  local records, states, readKeys, positions = {}, {}, {}, {}
  for i, key in ipairs(keys) do
    local kind = redis.call('TYPE', key).ok
    states[i] = kind == 'none' and 'missing' or 'skippedInvalid'
    if kind == 'string' and redis.call('STRLEN', key) <= 262144 then
      readKeys[#readKeys+1] = key; positions[#positions+1] = i
    end
  end
  local values = #readKeys > 0 and redis.call('MGET', unpack(readKeys)) or {}
  for j, i in ipairs(positions) do
    local ttl = redis.call('PTTL', keys[i])
    if ttl > 0 and ttl <= DISCOVERY_RETENTION_MS then
      records[i] = values[j]; states[i] = 'ready'
    elseif ttl == -2 then states[i] = 'missing' end
  end
  return records, states
end
local function viewerAccess(t, cfg)
  if t.workspaceId ~= cfg.workspaceId then return 'skippedUnauthorized' end
  if t.ownerUserId == nil then
    if t.ownerEmail ~= cfg.email then return 'skippedUnauthorized' end
    if t.mailboxId ~= cfg.ownerMailboxId or t.sourceRef.provider ~= cfg.ownerProvider then
      return 'unresolvedIdentity'
    end
    return 'owner'
  end
  if t.ownerUserId == cfg.userId then
    if t.ownerEmail == cfg.email and t.mailboxId == cfg.ownerMailboxId
      and t.sourceRef.provider == cfg.ownerProvider then return 'owner' end
    return 'skippedUnauthorized'
  end
  for _, p in ipairs(t.participants) do
    if p.userId == cfg.userId then
      if cfg.membershipRef ~= '' and p.membershipRef == cfg.membershipRef then return 'participant' end
      return 'staleEntitlement'
    end
  end
  return 'skippedUnauthorized'
end
local function configValid(cfg)
  return type(cfg) == 'table' and canonicalWorkspaceId(cfg.workspaceId)
    and canonicalUserId(cfg.userId) and canonicalEmail(cfg.email)
    and (cfg.membershipRef == '' or membershipRef(cfg.membershipRef))
    and ((cfg.ownerMailboxId == '' and cfg.ownerProvider == '') or
      (mailboxId(cfg.ownerMailboxId) and (cfg.ownerProvider == 'google' or cfg.ownerProvider == 'custom_imap')))
    and displayString(cfg.ownerDisplayName, 256, cfg.ownerMailboxId == '')
end
"""

# Probe transports only routing metadata and an exact-byte digest to the trusted
# Python wrapper. Native JSON decoding here grants no authority: the commit phase
# performs full strict wire/schema validation before trusting these same bytes.
_PROBE_LUA = _BATCH_COMMON + r"""
if #KEYS < 1 or #KEYS > 5 or #ARGV ~= 1 then return cjson.encode({status='malformed'}) end
local ok, cfg = pcall(cjson.decode, ARGV[1])
if not ok or not configValid(cfg) then return cjson.encode({status='malformed'}) end
for _, key in ipairs(KEYS) do
  if string.sub(key, 1, #DISCOVERY_THREAD_PREFIX) ~= DISCOVERY_THREAD_PREFIX
    or not opaqueId(string.sub(key, #DISCOVERY_THREAD_PREFIX+1)) then
    return cjson.encode({status='malformed'})
  end
end
local records, states = boundedBatch(KEYS)
local items = {}
for i, key in ipairs(KEYS) do
  local item = {status=states[i]}
  if states[i] == 'ready' then
    local decoded, t = pcall(cjson.decode, records[i])
    if not decoded or type(t) ~= 'table' or t.v ~= '2'
      or not participantAuthorityValid(t) or not canonicalWorkspaceId(t.workspaceId)
      or not canonicalEmail(t.ownerEmail) or not mailboxId(t.mailboxId)
      or not sourceValid(t.sourceRef) or not opaqueId(t.collaborationId)
      or key ~= DISCOVERY_THREAD_PREFIX .. t.collaborationId then
      item.status = 'skippedInvalid'
    else
      local access = viewerAccess(t, cfg)
      if access == 'owner' or access == 'participant' then
        item = {status='ready', id=t.collaborationId, hash=redis.sha1hex(records[i]),
          ownerEmail=t.ownerEmail, mailboxId=t.mailboxId, sourceRef=t.sourceRef}
      else item.status = access end
    end
  end
  items[#items+1] = cjson.encode(item)
end
return cjson.encode({status='ok', items='[' .. table.concat(items, ',') .. ']'})
"""

_ENROLL_LUA = _BATCH_COMMON + r"""
if #KEYS < 3 or #KEYS > 15 or #KEYS % 3 ~= 0 or #ARGV ~= 2 then
  return cjson.encode({status='malformed'})
end
local cfgOk, cfg = pcall(cjson.decode, ARGV[1])
local itemsOk, expected = pcall(cjson.decode, ARGV[2])
if not cfgOk or not configValid(cfg) or type(cfg.dryRun) ~= 'boolean'
  or type(cfg.reserved) ~= 'table' or #cfg.reserved > 1000
  or not itemsOk or type(expected) ~= 'table' or #expected ~= #KEYS / 3 then
  return cjson.encode({status='malformed'})
end
local keys = {}
for i, item in ipairs(expected) do
  keys[i] = KEYS[3*i-2]
  if type(item) ~= 'table' or not opaqueId(item.id) or type(item.hash) ~= 'string'
    or #item.hash ~= 40 or not string.match(item.hash, '^[0-9a-f]+$')
    or keys[i] ~= DISCOVERY_THREAD_PREFIX .. item.id then return cjson.encode({status='malformed'}) end
  for j=3*i-1,3*i do
    if not string.match(KEYS[j], '^cuevion:collab:v2:{cuevion%-collab%-v2}:source%-thread:[0-9a-f]+$')
      or #KEYS[j] ~= #MIGRATION_SOURCE_PREFIX + 64 then return cjson.encode({status='malformed'}) end
  end
end
local indexKey = discoveryKey(cfg.workspaceId, cfg.userId)
local indexType = redis.call('TYPE', indexKey).ok
if indexType ~= 'none' and indexType ~= 'hash' then return cjson.encode({status='malformed'}) end
if indexType == 'hash' and redis.call('HLEN', indexKey) > 1000 then
  return cjson.encode({status='malformed'})
end
local virtual, reserved, virtualCount = {}, {}, 0
local existingIds = indexType == 'hash' and redis.call('HKEYS', indexKey) or {}
for _, id in ipairs(existingIds) do
  if not opaqueId(id) then return cjson.encode({status='malformed'}) end
  virtual[id] = true; virtualCount = virtualCount + 1
end
for _, id in ipairs(cfg.reserved) do
  if not opaqueId(id) or not cfg.dryRun then return cjson.encode({status='malformed'}) end
  reserved[id] = true
  if not virtual[id] then virtual[id] = true; virtualCount = virtualCount + 1 end
end
local records, states = boundedBatch(keys)
local results, commits, rewrites, capacityCache = {}, {}, {}, {}
local strictBytes = 0
for i, item in ipairs(expected) do
  local status, raw = states[i], records[i]
  if status == 'ready' then
    if redis.sha1hex(raw) ~= item.hash then status = 'retry'
    elseif strictBytes + #raw > 262144 then status = 'deferred'
    else
      -- The legacy strict content parser is deliberately unchanged. Bound its
      -- aggregate input as well as candidate count to avoid long Redis scripts.
      strictBytes = strictBytes + #raw
      local ok, t = decodeWire(raw)
      if not ok or not discoveryCanonical(t, raw) or not canonicalWorkspaceId(t.workspaceId)
        or t.collaborationId ~= item.id then status = 'skippedInvalid'
      else
        status = viewerAccess(t, cfg)
        if status == 'owner' or status == 'participant' then
          -- Check both current and previous HMAC pointers without migrating,
          -- deleting, refreshing, or inventing either pointer.
          local pointerOk, pointerFound = true, false
          for j=3*i-1,3*i do
            if j == 3*i-1 or KEYS[j] ~= KEYS[j-1] then
              local kind = redis.call('TYPE', KEYS[j]).ok
              if kind ~= 'none' then
                if kind ~= 'string' or redis.call('STRLEN', KEYS[j]) > 128 then pointerOk = false
                else
                  local p = redis.call('GET', KEYS[j])
                  local ttl = redis.call('PTTL', KEYS[j])
                  if p ~= item.id or ttl <= 0 or ttl > DISCOVERY_RETENTION_MS then pointerOk = false
                  else pointerFound = true end
                end
              end
            end
          end
          if not pointerOk or not pointerFound then status = 'skippedInvalid'
          else
            local enriched = t.ownerUserId == nil
            if enriched then
              -- Preserve every original byte/value outside the newly verified
              -- authority group, including empty arrays and exact source refs.
              raw = string.match(raw, '^(.*)}%s*$') .. ',"ownerUserId":' .. cjson.encode(cfg.userId)
                .. ',"ownerDisplayName":' .. cjson.encode(cfg.ownerDisplayName) .. ',"participants":[]}'
              ok, t = decodeWire(raw)
            end
            if not ok or #raw > 262144 or not discoveryCanonical(t, raw) then status = 'skippedInvalid'
            else
              local ttl = redis.call('PTTL', keys[i])
              local plan, err = discoveryPrepare(t, keys[i], ttl, raw, {cfg.userId}, capacityCache)
              if err == 'discovery_capacity_reached' then status = 'capacity'
              elseif err then status = 'skippedInvalid'
              else
                local p = plan[1]
                if not p then return cjson.encode({status='malformed'}) end
                if not p.changed and p.ttl <= p.priorTtl then status = 'alreadyPresent'
                elseif cfg.dryRun and reserved[item.id] then status = 'alreadyPlanned'
                else
                  -- Reserve capacity across this whole page before any write.
                  -- Dry-run additionally reserves earlier pages locally.
                  for _, id in ipairs(p.prune) do
                    if virtual[id] and not reserved[id] then virtual[id] = nil; virtualCount = virtualCount - 1 end
                  end
                  if not virtual[item.id] and virtualCount >= 1000 then status = 'capacity'
                  else
                    if not virtual[item.id] then virtual[item.id] = true; virtualCount = virtualCount + 1 end
                    status = cfg.dryRun and 'wouldEnroll' or 'enrolled'
                    commits[#commits+1] = p
                    if enriched then rewrites[#rewrites+1] = {key=keys[i], raw=raw} end
                  end
                end
              end
            end
          end
        end
      end
    end
  end
  results[#results+1] = cjson.encode({status=status})
end
if not cfg.dryRun then
  -- Several candidates share the same recipient hash. Never let a later,
  -- shorter-lived candidate shorten the expiry established by an earlier one.
  local retainedTtl, didPrune = nil, false
  for _, p in ipairs(commits) do
    if #p.prune > 0 then
      if didPrune then p.prune = {} else didPrune = true end
    end
    if retainedTtl then p.priorTtl = retainedTtl end
    retainedTtl = math.max(p.priorTtl, p.ttl)
  end
  discoveryCommit(commits)
  for _, rewrite in ipairs(rewrites) do redis.call('SET', rewrite.key, rewrite.raw, 'KEEPTTL') end
end
return cjson.encode({status='ok', items='[' .. table.concat(results, ',') .. ']'})
"""


def _json(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _eval_items(script, keys, arguments, transport):
    result = store._v2_eval(["EVAL", script, len(keys), *keys, *arguments], transport,
                            response_shapes={"ok": {"items"}, "malformed": set()})
    if result.get("status") != "ok":
        raise MigrationError("storage_unavailable" if result.get("status") == "unavailable" else "invalid_index")
    raw = result.get("items")
    if type(raw) is not str or len(raw) > 32_768:
        raise MigrationError("storage_protocol_error")
    try:
        items = store._strict_json_loads(raw)
    except (ValueError, RecursionError):
        raise MigrationError("storage_protocol_error") from None
    if type(items) is not list or len(items) > PAGE_SIZE:
        raise MigrationError("storage_protocol_error")
    return items


def _process(keys, config, transport):
    outcomes = ["skippedInvalid"] * len(keys)
    positions = [i for i, key in enumerate(keys) if is_v2_opaque_id(key[len(store.V2_THREAD_KEY_PREFIX):])]
    if not positions:
        return outcomes
    probe = _eval_items(_PROBE_LUA, [keys[i] for i in positions], [_json(config)], transport)
    if len(probe) != len(positions):
        raise MigrationError("storage_protocol_error")
    ready, commit_keys, expected = [], [], []
    hmac_keys = None
    for position, item in zip(positions, probe):
        if type(item) is not dict or type(item.get("status")) is not str:
            raise MigrationError("storage_protocol_error")
        if item["status"] == "ready":
            hmac_keys = hmac_keys or store.resolve_v2_index_hmac_keys()
            if hmac_keys is None:
                raise MigrationError("index_hmac_unavailable")
            if (set(item) != {"status", "id", "hash", "ownerEmail", "mailboxId", "sourceRef"}
                or keys[position] != store.build_v2_thread_key(item["id"])
                or type(item["hash"]) is not str or re.fullmatch(r"[0-9a-f]{40}", item["hash"]) is None
                or normalize_v2_source_ref(item["sourceRef"]) != item["sourceRef"]):
                raise MigrationError("storage_protocol_error")
            pointers = [store.build_v2_source_thread_key(item["ownerEmail"], item["mailboxId"], item["sourceRef"], hmac_key=k)
                        for k in (hmac_keys[0], hmac_keys[1] or hmac_keys[0])]
            if any(p is None for p in pointers):
                raise MigrationError("storage_protocol_error")
            ready.append(position)
            commit_keys.extend([keys[position], *pointers])
            expected.append({"id": item["id"], "hash": item["hash"]})
        elif set(item) == {"status"} and item["status"] in _COUNTERS[5:10]:
            outcomes[position] = item["status"]
        else:
            raise MigrationError("storage_protocol_error")
    if ready:
        result = _eval_items(_ENROLL_LUA, commit_keys, [_json(config), _json(expected)], transport)
        if len(result) != len(ready):
            raise MigrationError("storage_protocol_error")
        for position, item in zip(ready, result):
            if type(item) is not dict or set(item) != {"status"} or item["status"] not in _COUNTERS[1:]:
                raise MigrationError("storage_protocol_error")
            outcomes[position] = item["status"]
    return outcomes


def _token(value):
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9_-]{32}", value) is not None


def _scan_cursor(value):
    return (type(value) is str and re.fullmatch(r"0|[1-9][0-9]{0,19}", value) is not None
            and int(value) <= 18_446_744_073_709_551_615)


def _scan_key(value):
    return (type(value) is str and value.isascii() and len(value) <= 256
            and value.startswith(store.V2_THREAD_KEY_PREFIX)
            and all(32 <= ord(c) < 127 for c in value))


def _private_file(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise MigrationError("checkpoint_permissions")


def _save(path, state):
    raw = _json(state).encode("ascii")
    if len(raw) > MAX_CHECKPOINT_BYTES:
        raise MigrationError("checkpoint_limit")
    fd, name = tempfile.mkstemp(prefix=".discovery-migration-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _load(path, scope, scan_budget):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as file:
        _private_file(file.fileno())
        raw = file.read(MAX_CHECKPOINT_BYTES + 1)
    if len(raw) > MAX_CHECKPOINT_BYTES:
        raise MigrationError("checkpoint_limit")
    try:
        state = store._strict_json_loads(raw.decode("ascii"))
    except (UnicodeError, ValueError, RecursionError):
        raise MigrationError("invalid_checkpoint") from None
    fields = {"v", "runId", "scope", "scanBudget", "scanCalls", "scanCursor", "scanFinished",
              "pending", "reserved", "cursor", "previousCursor", "previousResult", "done"}
    if (type(state) is not dict or set(state) != fields or type(state["v"]) is not int or state["v"] != 1
        or not _token(state["runId"]) or state["scope"] != scope
        or type(state["scanBudget"]) is not int or state["scanBudget"] != scan_budget
        or type(state["scanCalls"]) is not int or not 0 <= state["scanCalls"] <= scan_budget
        or not _scan_cursor(state["scanCursor"]) or type(state["scanFinished"]) is not bool
        or type(state["done"]) is not bool
        or (state["cursor"] is not None and not _token(state["cursor"]))
        or (state["previousCursor"] is not None and not _token(state["previousCursor"]))
        or type(state["pending"]) is not list or len(state["pending"]) > MAX_PENDING
        or any(not _scan_key(k) for k in state["pending"])
        or len(set(state["pending"])) != len(state["pending"])
        or type(state["reserved"]) is not list or len(state["reserved"]) > 1000
        or any(not is_v2_opaque_id(i) for i in state["reserved"])
        or len(set(state["reserved"])) != len(state["reserved"])
        or (not scope["dryRun"] and state["reserved"])
        or (state["done"] and (not state["scanFinished"] or state["pending"] or state["previousResult"] is None))
        or (state["scanFinished"] and (state["scanCursor"] != "0" or state["scanCalls"] == 0))):
        raise MigrationError("invalid_checkpoint")
    previous = state["previousResult"]
    if previous is not None:
        fields = set(_COUNTERS) | {"status", "error", "nextCursor", "done", "dryRun", "scanCalls"}
        if (type(previous) is not dict or set(previous) != fields
            or previous["status"] not in {"ok", "blocked"}
            or previous["error"] not in (None, {"code": "capacity"}, {"code": "retry"})
            or previous["done"] != state["done"] or previous["dryRun"] != scope["dryRun"]
            or previous["scanCalls"] != state["scanCalls"]
            or previous["nextCursor"] != (None if state["done"] else state["cursor"])
            or any(type(previous[c]) is not int or not 0 <= previous[c] <= PAGE_SIZE for c in _COUNTERS)
            or sum(previous[c] for c in _COUNTERS[1:]) != previous["examined"]):
            raise MigrationError("invalid_checkpoint")
    return state


def _verified_config(owner_context, headers, configuration, owner_mailbox_id):
    try:
        viewer = auth.resolve_verified_summary_viewer(
            owner_context, headers, owner_security_configuration=configuration)
    except Exception:
        raise MigrationError("forbidden") from None
    if type(viewer) is not dict or viewer.get("status") != "ok":
        raise MigrationError("auth_unavailable" if type(viewer) is dict and viewer.get("status") == "unavailable" else "forbidden")
    member = viewer["member"]
    config = {"workspaceId": member.workspace_id, "userId": member.user_id, "email": member.email,
              "membershipRef": viewer["membershipRef"] or "", "ownerMailboxId": "",
              "ownerProvider": "", "ownerDisplayName": ""}
    if owner_mailbox_id is not None:
        try:
            result = auth.resolve_verified_owner_collaboration_context(
                owner_context, headers, owner_mailbox_id, required_action="read",
                owner_security_configuration=configuration)
        except Exception:
            raise MigrationError("forbidden") from None
        capability = result.get("context") if type(result) is dict else None
        if (not auth._is_internal_capability(capability, actions={"read"})
            or capability.viewer_access != "owner" or capability.actor_kind != "owner"
            or capability.actor_user_id != member.user_id or capability.owner_user_id != member.user_id
            or capability.workspace_id != member.workspace_id or capability.owner_email != member.email
            or capability.collaboration_id is not None or capability.mailbox_id != owner_mailbox_id):
            raise MigrationError("forbidden")
        config.update(ownerMailboxId=capability.mailbox_id, ownerProvider=capability.mailbox_provider,
                      ownerDisplayName=capability.owner_display_name)
    return config


def run_page(owner_context, headers, *, enabled=False, checkpoint_path, cursor=None,
             dry_run=True, owner_mailbox_id=None, scan_budget=1000,
             owner_security_configuration, command_transport=None):
    """Manually execute one page; persist a private checkpoint before enrollment.

    Supply a fresh verified owner request context AND current authenticated
    headers each time. owner_mailbox_id additionally enables owner enrollment
    for that one currently owned mailbox. None is a participant-only pass.
    Mode, user, workspace and mailbox cannot change within a checkpoint.
    Retry the same cursor for a lost response; use nextCursor to advance/retry a
    blocked candidate. Completed checkpoints are sealed. New dry/apply passes
    require separate files. No production transport is invoked by importing.
    """
    return _run_page(
        lambda: _verified_config(
            owner_context, headers, owner_security_configuration, owner_mailbox_id),
        enabled=enabled, checkpoint_path=checkpoint_path, cursor=cursor,
        dry_run=dry_run, scan_budget=scan_budget, command_transport=command_transport,
    )


def run_page_with_operator_grant(grant, *, enabled=False, checkpoint_path,
                                 cursor=None, dry_run=True, owner_mailbox_id,
                                 scan_budget=25, owner_security_configuration,
                                 command_transport=None):
    """Manually run one dry page using a freshly verified, bounded bearer grant.

    Signature, expiry, current account, owned mailbox and Team authority are
    revalidated before reading a checkpoint, including a cached page replay.
    This adapter never creates browser-session or generic owner authority.
    """
    def resolve_config():
        if dry_run is not True or type(scan_budget) is not int or not 1 <= scan_budget <= 25:
            raise MigrationError("grant_scope_invalid")
        from . import operator_grant
        try:
            verified = operator_grant.verify_operator_grant(
                grant, owner_security_configuration=owner_security_configuration)
            return operator_grant.resolve_current_operator_config(
                verified, owner_mailbox_id,
                owner_security_configuration=owner_security_configuration)
        except operator_grant.OperatorGrantError as exc:
            raise MigrationError(exc.code) from None
        except Exception:
            raise MigrationError("grant_invalid") from None

    return _run_page(
        resolve_config, enabled=enabled, checkpoint_path=checkpoint_path,
        cursor=cursor, dry_run=dry_run, scan_budget=scan_budget,
        command_transport=command_transport,
    )


def _run_page(config_resolver, *, enabled, checkpoint_path, cursor,
              dry_run, scan_budget, command_transport):
    """Shared manual engine; trusted config is always resolved before I/O."""
    zero = {name: 0 for name in _COUNTERS}
    lock_fd = None
    state = None
    try:
        if enabled is not True:
            raise MigrationError("migration_disabled")
        if (type(dry_run) is not bool or type(scan_budget) is not int or not 1 <= scan_budget <= 100_000
            or (cursor is not None and not _token(cursor))):
            raise MigrationError("invalid_request")
        config = config_resolver()
        scope = {k: config[k] for k in ("workspaceId", "userId", "email", "ownerMailboxId", "ownerProvider")}
        scope["dryRun"] = dry_run
        path = Path(checkpoint_path)
        parent = path.parent.stat()
        if not path.is_absolute() or not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o077:
            raise MigrationError("checkpoint_permissions")
        lock_fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        _private_file(lock_fd)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise MigrationError("checkpoint_busy") from None
        state = _load(path, scope, scan_budget)
        if state is None:
            if cursor is not None:
                raise MigrationError("invalid_cursor")
            state = {"v": 1, "runId": secrets.token_urlsafe(24), "scope": scope,
                     "scanBudget": scan_budget, "scanCalls": 0, "scanCursor": "0", "scanFinished": False,
                     "pending": [], "reserved": [], "cursor": None, "previousCursor": None,
                     "previousResult": None, "done": False}
            _save(path, state)
        if state["previousResult"] is not None and cursor == state["previousCursor"]:
            return state["previousResult"]
        if state["done"]:
            raise MigrationError("migration_complete")
        if cursor != state["cursor"]:
            raise MigrationError("invalid_cursor")
        if not state["pending"] and not state["scanFinished"]:
            if state["scanCalls"] >= scan_budget:
                raise MigrationError("scan_budget_exhausted")
            # Count attempts before issuing Redis work. Interrupted/failed scans
            # may consume budget, but cannot create an uncounted retry loop.
            state["scanCalls"] += 1
            state["previousResult"] = None
            _save(path, state)
            scanned = store._v2_command(["SCAN", state["scanCursor"], "MATCH", SCAN_PATTERN, "COUNT", SCAN_COUNT], command_transport)
            value = scanned.get("result")
            if scanned.get("status") != "ok":
                raise MigrationError("storage_unavailable")
            if (type(value) is not list or len(value) != 2 or not _scan_cursor(value[0])
                or type(value[1]) is not list or len(value[1]) > MAX_PENDING
                or any(not _scan_key(k) for k in value[1])):
                raise MigrationError("scan_response_limit_or_protocol")
            state["scanCursor"], state["pending"] = value[0], sorted(set(value[1]))
            state["scanFinished"] = value[0] == "0"
            # The complete SCAN response is durable before its cursor can be
            # advanced again or any canonical/discovery mutation is attempted.
            _save(path, state)
        keys = state["pending"][:PAGE_SIZE]
        config.update(dryRun=dry_run, reserved=state["reserved"])
        outcomes = _process(keys, config, command_transport) if keys else []
        counts = dict(zero, examined=len(keys))
        retry = []
        reserved = set(state["reserved"])
        for key, outcome in zip(keys, outcomes):
            counts[outcome] += 1
            if outcome in {"capacity", "retry", "deferred"}:
                retry.append(key)
            if outcome == "wouldEnroll":
                reserved.add(key[len(store.V2_THREAD_KEY_PREFIX):])
        state["reserved"] = sorted(reserved)
        state["pending"] = retry + state["pending"][len(keys):]
        state["done"] = state["scanFinished"] and not state["pending"]
        state["previousCursor"] = cursor
        state["cursor"] = secrets.token_urlsafe(24)
        blocked = "capacity" if counts["capacity"] else "retry" if counts["retry"] else None
        result = {**counts, "status": "blocked" if blocked else "ok",
                  "error": {"code": blocked} if blocked else None,
                  "nextCursor": None if state["done"] else state["cursor"], "done": state["done"],
                  "dryRun": dry_run, "scanCalls": state["scanCalls"]}
        state["previousResult"] = result
        _save(path, state)
        return result
    except MigrationError as exc:
        return {**zero, "status": "blocked", "error": {"code": str(exc)},
                "nextCursor": cursor, "done": False, "dryRun": dry_run,
                "scanCalls": state["scanCalls"] if state is not None else 0}
    except (OSError, ValueError, TypeError):
        return {**zero, "status": "blocked", "error": {"code": "checkpoint_unavailable"},
                "nextCursor": cursor, "done": False, "dryRun": dry_run,
                "scanCalls": state["scanCalls"] if state is not None else 0}
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
