# C3D1 — Notifications route packaging

Baseline: branch `perf-1`; HEAD and remote main both
`ad3752c0a1ceb1f0f1e81fcb4579aacfb5c7186c`; staging empty; status matched the
three user-specified pre-existing changes. Remote verification required a
read-only network escalation after sandbox DNS failed.

## Cause and correction

`frontend/api/notifications.py` is the public Vercel entrypoint. Its import of
`api.notifications.http` collided with the internal package at
`frontend/api/notifications/`. Registering the route file as `api.notifications`
before execution makes that name a module without `__path__`, so importing its
supposed child fails before the handler is available.

Move the internal package to `frontend/api/notification_service/` and update the
route plus four Collaboration consumers. Public `POST /api/notifications`, all
operations, response shapes, frontend URLs and behavior are unchanged. No
runtime `sys.path` or dynamic import workaround was added.

The ten existing package files are byte-identical to their baseline versions:
`__init__.py`, `http.py`, `models.py`, `rate_limit.py`, `recipients.py`, `store.py`,
`test_http.py`, `test_rate_limit.py`, `test_recipients.py`, `test_store.py`.
The only differences in the five consumer files are the import name replacement:

- `frontend/api/notifications.py`
- `frontend/api/collaboration/mutations.py`
- `frontend/api/collaboration/redis_store.py`
- `frontend/api/collaboration/test_notifications.py`
- `frontend/api/collaboration/measure_c3c_notifications.py`

There are no changes to schemas, Redis/Lua, recipients, retention, IDs,
idempotency, summary/list/pagination/read behavior, authentication, Team
authority, origin/CSRF, rate limits, or frontend code.

## Import and packaging evidence

The new `test_route_import.py` was executed against the original package layout
before the rename and failed with the exact reported error:
`No module named 'api.notifications.http'; 'api.notifications' is not a package`.
It now passes in a fresh subprocess whose working directory/app import root is
`frontend`, with a minimal credential-free environment and network calls blocked.
It loads the actual route file under `api.notifications`, verifies a callable
handler, verifies the expected HTTP function identity, and checks that normal
imports select the same route file. It does not preload or mock application
imports. The former local route test used an unrelated module alias after the
internal package was already imported, hiding the collision.

Exact-path checks prove the public route and all new implementation files exist,
the old package's Python source files and `__init__.py` are absent, and the known
consumers have no old package imports. A residual old directory was left intact
without inspecting its remaining contents; it is not selected as a package by
either tested loader. No protected content was accessed or changed.

Both existing Vercel configs were inspected. The internal Python files remain
under the same app `api` tree with no new exclusion rule. These are local source
layout/import checks, not inspection of a newly built Vercel deployment artifact.
No Vercel build, remote action, dependency installation, push, or deploy was run.

## Verification

All Python invocations used `PYTHONDONTWRITEBYTECODE=1`. Use the existing
`venv/bin/python`; the system Python lacks `cryptography`. Existing Redis suites
use a disposable `/tmp` directory and a Unix socket with TCP disabled. Their
initial sandbox startup failed; the authorized local rerun passed.

| Suite | Result |
| --- | --- |
| C3C notifications, atomic emissions, new import and route HTTP regressions | 102 passed |
| Auth models/account/session/routes, guest HTTP/session, Collaboration authorization/request security | 279 passed |
| C3D0 backend exact-message HTTP/provider/rate limits | 58 passed |
| C3D frontend API/store/navigation/owner-read/rows/WorkspaceShell integration | 148 passed |
| C3D0 frontend exact-message API/publication | 53 passed |
| WorkspaceShell performance assertion suite | Passed |
| Full frontend application TypeScript (`tsc -p frontend/tsconfig.app.json --noEmit`) | Passed |
| Standalone import smoke rerun | 3 passed (already included above) |

Total: **640 distinct counted tests**, plus the WorkspaceShell performance suite.
The new route HTTP suite reuses the existing HTTP tests through the real handler
and serializer, adding explicit unsupported operation, default list/null cursor,
remaining methods, query-string and security-header assertions. Summary, list,
mark-read, guest-cookie denial, active/stale Team membership, malformed JSON,
authentication, no-store/nosniff and origin policy all pass with local mocks.

The performance suite retained its measured work bounds: 100 identities instead
of at least 10,000 repeated resolutions, and 2,900 keyword-family scans instead
of at least 29,000. No performance code changed.

Standalone smoke command, from `frontend`:

```sh
env -i PATH=/usr/local/bin:/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 ../venv/bin/python api/notification_service/test_route_import.py -v
```

Notification matrix command, from `frontend` (local Redis must be allowed):

```sh
PYTHONDONTWRITEBYTECODE=1 ../venv/bin/python -m unittest api.notification_service.test_route_import api.notification_service.test_route_http api.notification_service.test_http api.notification_service.test_rate_limit api.notification_service.test_recipients api.notification_service.test_store api.collaboration.test_notifications
```

No expected interpreter `.pyc` files exist at the exact old/new notification
source cache paths or the public route cache path. No broad cache scan was used.

## Inspection scope and handoff

Direct inspection covered both `package.json` and `vercel.json` files, frontend
TypeScript configs, the ten notification package files above, the five consumer
files, `api/collaboration/owner.py`, `test_import_safety.py`, the local Redis
harness, `api/auth/runtime.py`, `account_authority.py`, `session_store.py`, the
relevant auth/guest/authorization tests, exact-message HTTP/provider/test files,
and frontend notification/exact-message/performance tests and API clients.
Paths in this paragraph without a leading `frontend/` are relative to `frontend`.
Scoped searches covered notification imports in Collaboration/auth/Team Python
files and C3C/C3D references in frontend documentation. No applicable AGENTS.md
was present at the checked ancestor/package/documentation paths.

Before reading the attached request, one file-discovery command prohibited by
that request had already run. It did not expose protected filenames or contents.
After reading the request, searches and Git content checks stayed scoped to
non-protected paths. Protected contents accessed: **NO**.

C3D1 is ready for local handoff. Commit message: `fix notifications route packaging`.
Required parent and unchanged remote main:
`ad3752c0a1ceb1f0f1e81fcb4579aacfb5c7186c`.
Post-commit SHA and final gates are reported in the task response.
**PUSH: NO-GO under this task's explicit restriction. Production activity: NONE.**
Next action: review the local commit and request separate authorization for any
push/deployment/release verification. Production remains unverified by this slice.
