"""Bounded notification storage sharing Collaboration's atomic Redis slot.

Each recipient has a HASH of strict records, an ordered ZSET (creation time),
and an unread ZSET (expiry time). The mutation helper performs every fallible
read/validation/encoding before the caller makes its first canonical write.
No independent notification writer exists: only Collaboration Lua commits it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re

from api.collaboration.models import normalize_v2_user_id, normalize_v2_workspace_id
from .models import (
    MAX_NOTIFICATION_PAGE_SIZE,
    MAX_NOTIFICATIONS_PER_RECIPIENT,
    decode_notification_wire,
    is_notification_id,
    notification_dto,
)

NOTIFICATION_KEY_PREFIX = "cuevion:collab:v2:{cuevion-collab-v2}:notifications:"


def build_notification_keys(workspace_id: str, user_id: str) -> list[str] | None:
    if normalize_v2_workspace_id(workspace_id) is None or normalize_v2_user_id(user_id) is None:
        return None
    root = NOTIFICATION_KEY_PREFIX + workspace_id + ":" + user_id
    return [root + ":records", root + ":order", root + ":unread"]


# Concatenated after _V2_LUA_COMMON by Collaboration and the read scripts below.
# Redis wire integers stay decimal strings, including under hosted cjson where
# null keys may be omitted on decode. Serialization explicitly restores nulls.
NOTIFICATION_LUA_HELPERS = r"""
local NOTIFICATION_PREFIX = 'cuevion:collab:v2:{cuevion-collab-v2}:notifications:'
local NOTIFICATION_MAX = 1000
local NOTIFICATION_RETENTION = 15552000000
local function notificationNow()
  local clock = redis.call('TIME')
  return tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
end
local function notificationInteger(value)
  return string.format('%.0f', value)
end
local function notificationIdValid(value)
  return type(value) == 'string' and #value == 44 and string.sub(value, 1, 4) == 'ntf_'
    and string.match(string.sub(value, 5), '^[0-9a-f]+$') ~= nil
end
local function notificationNull(value)
  return value == nil or value == JSON_NULL
end
local function notificationActorValid(actor)
  if type(actor) ~= 'table' or not displayString(actor.displayName, 256, false) then return false end
  if actor.type == 'cuevion_user' then return keyCount(actor) == 3 and canonicalUserId(actor.userId) end
  return actor.type == 'external_guest' and keyCount(actor) == 2
end
local function notificationKindValid(kind)
  return kind == 'collaboration_started' or kind == 'participant_added'
    or kind == 'shared_message' or kind == 'internal_note'
end
local function notificationKeys(workspace, user)
  local root = NOTIFICATION_PREFIX .. workspace .. ':' .. user
  return {root .. ':records', root .. ':order', root .. ':unread'}
end
local function notificationRecordValid(record, raw, workspace, user)
  if type(record) ~= 'table' or type(raw) ~= 'string' or #raw > 4096 then return false end
  local fields = rawInviteMembers(raw)
  if not fields or keyCount(fields) ~= 13 or not fields.readAt or not fields.activityId then return false end
  local allowed = {v=true,notificationId=true,workspaceId=true,recipientUserId=true,kind=true,
    collaborationId=true,mailboxId=true,sourceRef=true,activityId=true,actor=true,
    createdAt=true,expiresAt=true,readAt=true}
  for field, _ in pairs(fields) do if not allowed[field] then return false end end
  if record.v ~= '1' or not notificationIdValid(record.notificationId)
    or record.workspaceId ~= workspace or not canonicalWorkspaceId(record.workspaceId)
    or record.recipientUserId ~= user or not canonicalUserId(record.recipientUserId)
    or not notificationKindValid(record.kind) or not opaqueId(record.collaborationId)
    or not mailboxId(record.mailboxId) or not sourceValid(record.sourceRef)
    or not notificationActorValid(record.actor)
    or not timestampMilliseconds(record.createdAt) or not timestampMilliseconds(record.expiresAt)
    or (not notificationNull(record.activityId) and not opaqueId(record.activityId)) then return false end
  if (record.kind == 'shared_message' or record.kind == 'internal_note') and notificationNull(record.activityId) then return false end
  if record.actor.type == 'external_guest' and record.kind ~= 'shared_message' then return false end
  if record.actor.type == 'cuevion_user' and record.actor.userId == user then return false end
  local created, expires = integerValue(record.createdAt), integerValue(record.expiresAt)
  if expires <= created or expires > created + NOTIFICATION_RETENTION then return false end
  if not notificationNull(record.readAt) and (not timestampMilliseconds(record.readAt)
    or integerValue(record.readAt) < created or integerValue(record.readAt) >= expires) then return false end
  return true
end
local function notificationEncode(record)
  local fields = {}
  for _, key in ipairs({'v','notificationId','workspaceId','recipientUserId','kind','collaborationId',
    'mailboxId','sourceRef','actor','createdAt','expiresAt'}) do
    fields[#fields+1] = cjson.encode(key) .. ':' .. cjson.encode(record[key])
  end
  for _, key in ipairs({'activityId','readAt'}) do
    fields[#fields+1] = cjson.encode(key) .. ':' .. (notificationNull(record[key]) and 'null' or cjson.encode(record[key]))
  end
  return '{' .. table.concat(fields, ',') .. '}'
end
local function notificationTopology(keys)
  local expected, counts, ttls = {'hash','zset','zset'}, {}, {}
  for index, key in ipairs(keys) do
    local kind = redisStringType(key)
    if kind ~= 'none' and kind ~= expected[index] then return nil end
    counts[index] = kind == 'none' and 0 or redis.call(index == 1 and 'HLEN' or 'ZCARD', key)
    if counts[index] > NOTIFICATION_MAX then return nil end
    ttls[index] = kind == 'none' and -2 or redis.call('PTTL', key)
    if kind ~= 'none' and (ttls[index] <= 0 or ttls[index] > NOTIFICATION_RETENTION) then return nil end
  end
  if counts[1] ~= counts[2] or counts[3] > counts[1] then return nil end
  return counts
end
local function notificationLoad(workspace, user)
  local keys = notificationKeys(workspace, user)
  local counts = notificationTopology(keys)
  if not counts then return nil end
  local rawEntries = redis.call('HGETALL', keys[1])
  local orderEntries = redis.call('ZRANGE', keys[2], 0, NOTIFICATION_MAX, 'WITHSCORES')
  local unreadEntries = redis.call('ZRANGE', keys[3], 0, NOTIFICATION_MAX, 'WITHSCORES')
  if #rawEntries ~= counts[1] * 2 or #orderEntries ~= counts[2] * 2 or #unreadEntries ~= counts[3] * 2 then return nil end
  local order, unread = {}, {}
  for index = 1, #orderEntries, 2 do
    local id, score = orderEntries[index], orderEntries[index+1]
    if not notificationIdValid(id) or not timestampMilliseconds(score) then return nil end
    order[id] = score
  end
  for index = 1, #unreadEntries, 2 do
    local id, score = unreadEntries[index], unreadEntries[index+1]
    if not notificationIdValid(id) or not timestampMilliseconds(score) then return nil end
    unread[id] = score
  end
  local records, expectedUnread = {}, 0
  for index = 1, #rawEntries, 2 do
    local id, raw = rawEntries[index], rawEntries[index+1]
    if type(raw) ~= 'string' or #raw > 4096 then return nil end
    local ok, record = decodeWire(raw)
    if not ok or not notificationRecordValid(record, raw, workspace, user)
      or record.notificationId ~= id or order[id] ~= record.createdAt
      or (notificationNull(record.readAt) and unread[id] ~= record.expiresAt)
      or (not notificationNull(record.readAt) and unread[id] ~= nil) then return nil end
    if notificationNull(record.readAt) then expectedUnread = expectedUnread + 1 end
    records[#records+1] = {id=id, record=record, raw=raw}
  end
  if expectedUnread ~= counts[3] then return nil end
  table.sort(records, function(a,b)
    if a.record.createdAt == b.record.createdAt then return a.id < b.id end
    return a.record.createdAt < b.record.createdAt
  end)
  return {keys=keys, records=records}
end
local function notificationRecipients(thread, actorUserId)
  local users, seen = {}, {}
  local function add(user)
    if canonicalUserId(user) and user ~= actorUserId and not seen[user] then
      seen[user] = true; users[#users+1] = user
    end
  end
  add(thread.ownerUserId)
  for _, participant in ipairs(thread.participants or {}) do add(participant.userId) end
  return users
end
local function notificationPrepare(thread, kind, actor, activityId, recipients, ttl, createdAt, eventIdentity, threadKey)
  if not threadValid(thread) or not canonicalWorkspaceId(thread.workspaceId)
    or not canonicalUserId(thread.ownerUserId) or not notificationKindValid(kind)
    or not notificationActorValid(actor) or not timestampMilliseconds(createdAt)
    or type(ttl) ~= 'number' or ttl <= 0 or ttl > NOTIFICATION_RETENTION
    or type(recipients) ~= 'table' or #recipients > 16 or keyCount(recipients) ~= #recipients then return nil, 'malformed' end
  if eventIdentity ~= nil and (type(eventIdentity) ~= 'string' or #eventIdentity > 128
    or not asciiSecurityString(eventIdentity, 128, false)) then return nil, 'malformed' end
  local allowed = {}
  for _, user in ipairs(notificationRecipients(thread, nil)) do allowed[user] = true end
  if actor.type == 'cuevion_user' and not allowed[actor.userId] then return nil, 'malformed' end
  local now = notificationNow()
  -- Notification time belongs to this Redis commit. Collaboration's monotonic
  -- activity clock can be slightly ahead; the activityId retains exact routing.
  createdAt = notificationInteger(now)
  if threadKey ~= nil then
    if threadKey ~= 'cuevion:collab:v2:{cuevion-collab-v2}:thread:' .. thread.collaborationId then return nil, 'malformed' end
    ttl = math.min(ttl, redis.call('PTTL', threadKey))
    if ttl <= 0 then return nil, 'malformed' end
  end
  local expiry = math.min(now + math.floor(ttl), integerValue(createdAt) + NOTIFICATION_RETENTION)
  if expiry <= now or expiry <= integerValue(createdAt) or expiry > MAX_MILLISECONDS then return nil, 'malformed' end
  local plan, seen = {}, {}
  for _, user in ipairs(recipients) do
    if not canonicalUserId(user) or not allowed[user] then return nil, 'malformed' end
    if not seen[user] and not (actor.type == 'cuevion_user' and actor.userId == user) then
      seen[user] = true
      local loaded = notificationLoad(thread.workspaceId, user)
      if not loaded then return nil, 'malformed' end
      local identity = eventIdentity or (notificationNull(activityId) and '' or activityId)
      local parts = {thread.workspaceId,user,kind,thread.collaborationId,identity}
      local material = 'cuevion.notification.v1'
      for _, value in ipairs(parts) do material = material .. ':' .. #value .. ':' .. value end
      local id = 'ntf_' .. redis.sha1hex(material)
      local record = {v='1',notificationId=id,workspaceId=thread.workspaceId,recipientUserId=user,
        kind=kind,collaborationId=thread.collaborationId,mailboxId=thread.mailboxId,
        sourceRef=thread.sourceRef,activityId=activityId,actor=actor,createdAt=createdAt,
        expiresAt=notificationInteger(expiry),readAt=JSON_NULL}
      local encoded = notificationEncode(record)
      if not notificationRecordValid(record, encoded, thread.workspaceId, user) then return nil, 'malformed' end
      local prune, survivors, prior, latest = {}, {}, nil, expiry
      for _, entry in ipairs(loaded.records) do
        if entry.id == id then prior = entry end
        if integerValue(entry.record.expiresAt) <= now then prune[#prune+1] = entry.id
        else survivors[#survivors+1] = entry end
      end
      local insert = not prior
      if prior then
        -- Replay never changes first actor snapshot, read state or retention.
        local existing = prior.record
        if existing.kind ~= kind or existing.collaborationId ~= thread.collaborationId
          or existing.mailboxId ~= thread.mailboxId or not sourceEqual(existing.sourceRef, thread.sourceRef)
          or (not notificationNull(activityId) and existing.activityId ~= activityId)
          or existing.actor.type ~= actor.type or existing.actor.userId ~= actor.userId then return nil, 'malformed' end
      elseif #survivors == NOTIFICATION_MAX and (createdAt < survivors[1].record.createdAt
        or (createdAt == survivors[1].record.createdAt and id < survivors[1].id)) then
        -- An event from an older canonical activity can arrive late. Prune the
        -- oldest across the complete union, including this new event itself.
        insert = false
      else
        while #survivors >= NOTIFICATION_MAX do
          prune[#prune+1] = survivors[1].id; table.remove(survivors, 1)
        end
      end
      for _, entry in ipairs(survivors) do latest = math.max(latest, integerValue(entry.record.expiresAt)) end
      plan[#plan+1] = {keys=loaded.keys,prune=prune,record=record,raw=encoded,
        insert=insert,expires=notificationInteger(latest)}
    end
  end
  return plan, nil
end
local function notificationCommit(plan)
  for _, item in ipairs(plan) do
    if #item.prune > 0 then
      redis.call('HDEL', item.keys[1], unpack(item.prune))
      redis.call('ZREM', item.keys[2], unpack(item.prune))
      redis.call('ZREM', item.keys[3], unpack(item.prune))
    end
    if item.insert then
      redis.call('HSET', item.keys[1], item.record.notificationId, item.raw)
      redis.call('ZADD', item.keys[2], item.record.createdAt, item.record.notificationId)
      redis.call('ZADD', item.keys[3], item.record.expiresAt, item.record.notificationId)
      for _, key in ipairs(item.keys) do redis.call('PEXPIREAT', key, item.expires) end
    end
  end
end
"""


def _redis():
    # Lazy to allow Collaboration to import the raw helper without a cycle.
    from api.collaboration import redis_store
    return redis_store


def _unavailable() -> dict:
    return {"status": "unavailable", "error": {"code": "storage_protocol_error"}}


def _script(body: str) -> str:
    return _redis()._V2_LUA_COMMON + NOTIFICATION_LUA_HELPERS + body


def _eval(body: str, keys: list[str], args: list[str], shapes: dict, transport) -> dict:
    result = _redis()._v2_eval(["EVAL", _script(body), len(keys), *keys, *args],
                             transport, response_shapes=shapes)
    return _unavailable() if result["status"] == "malformed" else result


_SUMMARY = r"""
if not canonicalWorkspaceId(ARGV[1]) or not canonicalUserId(ARGV[2]) then return cjson.encode({status='malformed'}) end
local expected = notificationKeys(ARGV[1], ARGV[2])
for index = 1, 3 do if KEYS[index] ~= expected[index] then return cjson.encode({status='malformed'}) end end
if not notificationTopology(KEYS) then return cjson.encode({status='malformed'}) end
local now = notificationNow()
local count = redis.call('ZCOUNT', KEYS[3], '(' .. notificationInteger(now), '+inf')
return cjson.encode({status='ok',unreadCount=notificationInteger(count)})
"""


def summary(workspace_id: str, user_id: str, *, command_transport=None) -> dict:
    keys = build_notification_keys(workspace_id, user_id)
    if keys is None:
        return {"status": "malformed"}
    result = _eval(_SUMMARY, keys, [workspace_id, user_id],
                   {"ok": {"unreadCount"}, "malformed": set()}, command_transport)
    if result["status"] != "ok":
        return result
    count = _count(result["unreadCount"])
    return {"status": "ok", "v": 1, "unreadCount": count} if count is not None else _unavailable()


def _count(value: object) -> int | None:
    if type(value) is not str or re.fullmatch(r"(?:0|[1-9][0-9]{0,3})", value) is None:
        return None
    return int(value) if int(value) <= MAX_NOTIFICATIONS_PER_RECIPIENT else None


def _cursor_mac(payload: str, workspace: str, user: str, key: bytes) -> bytes:
    material = json.dumps(["cuevion.notification.cursor.v1", workspace, user, payload], separators=(",", ":"))
    return hmac.new(key, material.encode("utf-8"), hashlib.sha256).digest()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _cursor_encode(position: str, workspace: str, user: str, key: bytes) -> str:
    payload = _b64(position.encode("ascii"))
    return payload + "." + _b64(_cursor_mac(payload, workspace, user, key))


def _position_valid(position: object) -> bool:
    return type(position) is str and re.fullmatch(r"[1-4][0-9]{12}:ntf_[0-9a-f]{40}", position) is not None


def _cursor_decode(cursor: object, workspace: str, user: str, key: bytes) -> str | None:
    if cursor is None:
        return ""
    if type(cursor) is not str or len(cursor) > 256 or re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}", cursor) is None:
        return None
    payload, signature = cursor.split(".")
    if not hmac.compare_digest(_b64(_cursor_mac(payload, workspace, user, key)), signature):
        return None
    try:
        position = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("ascii")
    except (ValueError, UnicodeError):
        return None
    return position if _position_valid(position) and _b64(position.encode("ascii")) == payload else None


_LIST = r"""
local loaded = notificationLoad(ARGV[1], ARGV[2])
if not loaded then return cjson.encode({status='malformed'}) end
local now, count, rows, nextPosition = notificationNow(), 0, {}, ''
local limit = tonumber(ARGV[3])
for index = #loaded.records, 1, -1 do
  local entry = loaded.records[index]
  if integerValue(entry.record.expiresAt) > now then
    if notificationNull(entry.record.readAt) then count = count + 1 end
    local position = entry.record.createdAt .. ':' .. entry.id
    if ARGV[4] == '' or position < ARGV[4] then
      if #rows < limit then rows[#rows+1] = entry.raw
      elseif nextPosition == '' then
        nextPosition = 'more'
      end
    end
  end
end
if nextPosition == 'more' then
  local _, last = decodeWire(rows[#rows])
  nextPosition = last.createdAt .. ':' .. last.notificationId
end
return cjson.encode({status='ok',items='[' .. table.concat(rows, ',') .. ']',
  nextPosition=nextPosition,unreadCount=notificationInteger(count)})
"""


def list_notifications(workspace_id: str, user_id: str, *, limit: int = MAX_NOTIFICATION_PAGE_SIZE,
                       cursor: str | None = None, command_transport=None) -> dict:
    keys = build_notification_keys(workspace_id, user_id)
    if keys is None or type(limit) is not int or not 1 <= limit <= MAX_NOTIFICATION_PAGE_SIZE:
        return {"status": "malformed"}
    key = _redis().resolve_v2_index_hmac_key()
    if key is None:
        return _unavailable()
    position = _cursor_decode(cursor, workspace_id, user_id, key)
    if position is None:
        return {"status": "malformed"}
    result = _eval(_LIST, keys, [workspace_id, user_id, str(limit), position],
                   {"ok": {"items", "nextPosition", "unreadCount"}, "malformed": set()}, command_transport)
    if result["status"] != "ok":
        return result
    count = _count(result["unreadCount"])
    next_position = result["nextPosition"]
    try:
        raw_rows = _redis()._strict_json_loads(result["items"])
        if type(raw_rows) is not list or len(raw_rows) > limit:
            return _unavailable()
        rows = [decode_notification_wire(json.dumps(row, ensure_ascii=False, separators=(",", ":"))) for row in raw_rows]
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return _unavailable()
    if count is None or any(row is None or row["workspaceId"] != workspace_id or row["recipientUserId"] != user_id for row in rows):
        return _unavailable()
    positions = [str(row["createdAt"]) + ":" + row["notificationId"] for row in rows]
    if positions != sorted(set(positions), reverse=True) or (position and any(item >= position for item in positions)):
        return _unavailable()
    if next_position != "" and (not positions or len(rows) != limit or next_position != positions[-1]
                                or (position and next_position >= position)):
        return _unavailable()
    return {"status": "ok", "v": 1, "notifications": [notification_dto(row) for row in rows],
            "nextCursor": _cursor_encode(next_position, workspace_id, user_id, key) if next_position else None,
            "unreadCount": count}


_MARK_READ = r"""
local loaded = notificationLoad(ARGV[1], ARGV[2])
if not loaded then return cjson.encode({status='malformed'}) end
local now, target, count = notificationNow(), nil, 0
for _, entry in ipairs(loaded.records) do
  if integerValue(entry.record.expiresAt) > now then
    if notificationNull(entry.record.readAt) then count = count + 1 end
    if entry.id == ARGV[3] then target = entry end
  end
end
if not target then return cjson.encode({status='not_found'}) end
local encoded = target.raw
if notificationNull(target.record.readAt) then
  target.record.readAt = notificationInteger(now)
  encoded = notificationEncode(target.record)
  if not notificationRecordValid(target.record, encoded, ARGV[1], ARGV[2]) then return cjson.encode({status='malformed'}) end
  redis.call('HSET', loaded.keys[1], target.id, encoded)
  redis.call('ZREM', loaded.keys[3], target.id)
  count = count - 1
end
return cjson.encode({status='ok',notification=encoded,unreadCount=notificationInteger(count)})
"""


def mark_read(workspace_id: str, user_id: str, notification_id: str, *, command_transport=None) -> dict:
    keys = build_notification_keys(workspace_id, user_id)
    if keys is None or not is_notification_id(notification_id):
        return {"status": "malformed"}
    result = _eval(_MARK_READ, keys, [workspace_id, user_id, notification_id],
                   {"ok": {"notification", "unreadCount"}, "malformed": set(), "not_found": set()}, command_transport)
    if result["status"] != "ok":
        return result
    record = decode_notification_wire(result["notification"])
    count = _count(result["unreadCount"])
    if record is None or record["workspaceId"] != workspace_id or record["recipientUserId"] != user_id or record["notificationId"] != notification_id or count is None:
        return _unavailable()
    return {"status": "ok", "v": 1, "notification": notification_dto(record), "unreadCount": count}
