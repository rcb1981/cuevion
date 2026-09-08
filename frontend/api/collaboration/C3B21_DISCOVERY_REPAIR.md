# C3B2.1 exact Collaboration discovery repair

This document records local code evidence and isolated test results. It does not
identify the exact Production request or authorize Production access, a migration,
push, or deployment. Final verification passed 502 backend tests and 32 frontend
suites. Repository/commit gates are recorded in the task's final report.

## Evidence and limits

The pre-edit reference is `3523845b719e79a3c475f84001c3cb6adab64834` on `perf-1`.
The supplied Production observation was three active Google summaries in
`gmail-carltricksmusic`, no tested Collaboration in those summaries, successful
owner requests, and contemporaneous custom-IMAP mailbox loading. That establishes
the reported absent projection; it does not identify the tested source locator,
HTTP operation, `created` outcome, canonical ID, record age, or historical
migration request scope. The tested Production source remains **unknown**.
Custom IMAP is a plausible explanation based on circumstantial mailbox activity.

Locally, the same observable condition is executable: an active canonical IMAP
Collaboration can be read successfully while the viewer's summary page contains
only three unrelated Google entries. The direct-read path omitted discovery
repair. Separately, existing-thread guest creation returned clean success without
required discovery enrollment, and ordinary duplicate creation extended canonical
and source-pointer retention. These are proven code defects; attributing the
particular Production event to one of them would exceed the available evidence.

## Pre-edit authority trace A–W

Line references in this table refer to the pre-edit reference above. Paths in
the first three columns are relative to `frontend/`. Function names remain useful
anchors after the repair shifts line numbers.

| Item | Exact source | Pre-edit behavior |
| --- | --- | --- |
| A. Owner HTTP create | `api/collaboration/owner_http.py:762–799` | Strict `create` body, owner-write mode, existing rate limit, verified application call; 201 for new and 200 for existing. |
| B. Owner HTTP guest create | `api/collaboration/owner_http.py:802–831` | Strict `create_with_guest` body and the same verified owner boundary; invitation projection and created status remain distinct. |
| C. Application create | `api/collaboration/application.py:974–1180` | Verifies current owner/mailbox, requires an explicit Team participant on the verified ordinary route, fetches the exact provider source, constructs canonical identity, and calls `_create_v2_thread`. |
| D. Redis create Lua | `api/collaboration/redis_store.py:2361–2412` | New canonical thread, source pointer, and required discovery are committed in one EVAL after preflight. Guest new creation does the equivalent plus invitation records at `2669–2685`. |
| E. Existing/idempotent path | `api/collaboration/redis_store.py:2374–2399,2640–2658`; `application.py:1148–1166` | Ordinary duplicate prepares discovery but refreshes canonical/source TTL; guest existing branch returns before discovery preparation. Application reuses the canonical ID. |
| F. Owner identity | `api/collaboration/application.py:321–335`; `authorization.py:468–535` | `_canonical_owner_authority` requires canonical member ID from an internal owner capability. Email, canonical member, and owned inbox must agree; no email-to-ID invention. |
| G. Workspace | `api/collaboration/authorization.py:355,392,479,515`; `application.py:303–317,1104–1122` | Current account/workspace must match verified context and canonical thread. The proposed canonical workspace comes from capability authority. |
| H. Mailbox | `api/collaboration/authorization.py:484–510`; `application.py:303–317` | Exact owned inbox ID and supported provider; returned canonical mailbox/source must match the request capability. |
| I. Google source | `src/lib/collaborationOwnerSourceLocator.ts:165–176`; `api/collaboration/source_message.py:571–582,629–655` | Trusted managed mailbox and exact `providerMessageId`; backend adds provider `google`, verifies provider authority, and resolves the source. |
| J. IMAP source | `src/lib/collaborationOwnerSourceLocator.ts:179–213`; `api/collaboration/source_message.py:583–591,650–655` | Exact `custom_imap`, `INBOX`, positive canonical `uidValidity`/`imapUid`; conflicting UIDVALIDITY or mailbox context fails. No subject/sender/time matching. |
| K. Recipients | `api/collaboration/redis_store.py:2286–2307` | `discoveryPrepare` derives owner plus canonical explicit participants, or a separately verified one-recipient subset. Missing legacy owner identity yields no automatic enrollment. Guests are not discovery recipients. |
| L. HASH write | `api/collaboration/redis_store.py:2310–2357` | Viewer HASH is `discovery:<workspace>:<user>`; field is exact Collaboration ID. Preflight validates type, capacity and prior binding; commit uses HSET and necessary HDEL/PEXPIRE. |
| M. Value/digest | `api/collaboration/redis_store.py:2271–2297` | Exactly `ownerUserId`, `mailboxId`, `sourceRef`, and SHA1 `threadHash` of canonical wire bytes. Digest is integrity/version evidence, not an authorization token. |
| N. TTL | `api/collaboration/redis_store.py:2266–2267,2289–2293,2316–2318,2350–2357` | Positive bounded canonical retention; discovery HASH retains the longest required canonical lifetime. Healthy equal-value enrollment avoids HSET, but ordinary duplicate incorrectly supplies fresh retention and refreshes authority expiry. |
| O. Summary read | `api/collaboration/owner_http.py:650–666`; `application.py:2099–2124`; `redis_store.py:5202–5282` | One verified viewer request, bounded discovery enumeration, sorted 50-ID page, one MGET, strict summary projection. No historical enumeration is performed. |
| P. Entitlement filtering | `api/collaboration/authorization.py:538–582`; `redis_store.py:5263–5270`; `application.py:2119–2124` | Current account plus one current Team snapshot; owner ID/email or explicit participant with exact membership reference. Owner summaries retain the rollout mailbox allowlist check. |
| Q. Source binding | `api/collaboration/redis_store.py:2278–2280,5255–5262` | Canonical ID, workspace, owner ID, mailbox, and exact source must match the discovery binding. Listing does not fetch provider messages or reread HMAC source pointers. Source-pointer proof belongs to producer/exact repair. |
| R. Stale/digest checks | `api/collaboration/redis_store.py:5214–5227,5239–5262` | Invalid index type/TTL/schema fails the page. Missing canonical records may be pruned; expired/oversized/wrong-type/mismatched-digest or invalid canonical records are omitted. Existing corrupt records are not evicted. |
| S. `created=true` | `api/collaboration/application.py:1144–1147,1177–1180`; `owner_http.py:799` | Requires returned record/ID equal the proposed canonical record; HTTP 201. Required discovery is already part of the new-create EVAL. |
| T. `created=false` | `api/collaboration/application.py:1148–1180`; `owner_http.py:799` | Loads the existing canonical record and returns HTTP 200. Existing ordinary participant authority must match; it is not permission to mutate participants. |
| U. Guest equivalents | `api/collaboration/application.py:1205–1507`; `redis_store.py:2640–2685` | New guest creation enrolls owner atomically; existing branch skips discovery and reuses existing identity before ordinary guest invitation processing. Owner-only canonical participants remain `[]`. |
| V. Historical enrollment | `api/collaboration/discovery_migration.py:60–80,422–452,517–575`; `C3B1C_DISCOVERY_MIGRATION.md:49–54` | Owner pass is bound to one exact mailbox/provider; participant path independently requires current membership. Separate owned mailboxes require separate passes. |
| W. Frontend create refresh | `src/components/collaboration/CollaborationAccessPanel.tsx:355–393`; `src/lib/collaborationSummaryStore.ts:125–140`; `src/lib/useCollaborationSummaries.ts:6–21` | Successful ordinary/guest creation publishes for both created booleans before modal-close fencing. Unknown IDs trigger a bounded authoritative summary refresh. Failed creation publishes nothing. |

The existing frontend menu calls exact v2 lookup/read at
`src/components/workspace/WorkspaceShell.tsx:23754–23825`. Lookup success selects
access mode, not create mode (`21191–21196`); successful read originally only set
the modal projection. The backend lookup already called `_enrich_v2_discovery`
(`application.py:1862`), whereas direct verified read did not. A valid trusted
locator prevents falling through to legacy draft creation (`24397–24400`). An
invalid locator can still expose legacy/local UI, so a visible modal alone does
not prove an authoritative v2 thread exists.

## Failure-mode decisions A–L

| Candidate | Evidence-based conclusion |
| --- | --- |
| A. Truly new create skips enrollment | Rejected for supported verified v2 creates with canonical identity: both new-create scripts prepare and commit discovery atomically. |
| B. Existing create fails repair | Proven for guest existing-thread create and direct verified reads. Ordinary compatible duplicate already repaired missing valid entries but violated TTL preservation; malformed bindings blocked its repair. |
| C. Custom-IMAP owner enrollment defect | No provider-specific defect found. IMAP reaches the shared failing existing/access branches. |
| D. Google works but IMAP differs | Rejected as a general implementation explanation: both providers reproduce existing guest/access failures locally. |
| E. Canonical/discovery source mismatch | Exact mismatch correctly fails closed. Invalid stored discovery binding needs repair from proven canonical/source authority; no source-construction divergence was found. |
| F. Owner/workspace recipient wrong | Supported verified new creates derive the canonical owner/workspace correctly. Legacy records may lack owner identity; only existing compatibility proof may enrich them. |
| G. Historical thread outside migration scope | Reachable. A Gmail owner pass cannot enroll that owner's historical IMAP threads. Actual prior Production pass scope remains unproven. |
| H. Lua enrolls only some outcomes | Proven: guest new branch enrolls, existing branch previously returned before enrollment. Ordinary duplicate enrollment existed but refreshed retention. |
| I. Guest differs from ordinary | Proven for existing-thread enrollment and TTL behavior. Guest participants are not internal discovery recipients. |
| J. `participants=[]` differs from Team | Both are valid canonical records. The frozen verified ordinary HTTP create requires a Team participant; no new owner-only HTTP mode is introduced. Owner-only canonical storage and guest create/access are covered. |
| K. Listing filters written entries | Proven reachable for stale digest/binding, missing/expired canonical authority, invalid record, or lost entitlement. Malformed discovery may fail the whole page. These checks remain unchanged. |
| L. UI opened non-v2 state | Possible when no valid trusted v2 locator is available; valid live locators exclusively use the owner-v2 path. Available Production evidence cannot determine this. |

## Local failure proof

Before runtime changes, four new isolated Redis test methods produced **11
assertion failures and zero errors**:

| Regression | Pre-fix failures |
| --- | --- |
| `test_production_three_google_summaries_and_openable_imap_repairs_on_exact_read` | 1: successful active IMAP canonical read left only three unrelated Google summaries. |
| `test_existing_guest_create_repairs_missing_discovery_for_both_providers` | 2: Google and IMAP guest create returned `created=false` while discovery remained absent. |
| `test_ordinary_repeated_create_never_refreshes_canonical_source_or_discovery_expiry` | 2: both provider fixtures extended canonical, pointer, owner index, and participant index expiries from a short remaining lifetime to fresh retention. |
| `test_exact_read_repairs_missing_malformed_and_wrong_digest_values` | 6: both providers left missing/invalid discovery unrepaired after a successful direct read. |

The initial baseline variant named "digest" supplied an extra `digest` field,
which was schema-invalid. That baseline proves invalid-discovery nonrepair; the
final test was corrected to corrupt the actual `threadHash` field and therefore
also exercises a well-formed digest mismatch. Baseline evidence is not relabeled
as a stronger test than was actually run.

The frontend regression also failed before the consumer adjustment:
`google/repaired: exact successful access refreshes once`, actual refresh count
1 versus expected 2 (initial hydration plus one access-triggered refresh). It
executes the actual owner-read function with the real summary store, initially
containing only unrelated authority.

## Repair design

`redis_store.py` adds `discoveryRepairExisting` and an explicit repair-only option
to the shared discovery preparation helper. An exact create/access boundary may
replace that Collaboration's missing or invalid HASH field after proving the
current canonical identity, source pointer, entitlement, schema, and remaining
expiry. Ordinary publishing mutations retain their prior strict preflight.

Ordinary compatible duplicate creation retains its current canonical ID,
participants, lifecycle, messages, and timestamps. It now uses remaining PTTL
and does not EXPIRE the canonical thread, rewrite the source pointer, or remove
the previous pointer generation. The guest existing branch prepares owner
discovery before returning existing success. Verified guest creation remains
responsible for its already-existing invitation behavior; discovery repair does
not remove or redesign that behavior.

The verified direct read now invokes `_enrich_v2_discovery` before returning a
successful DTO. The existing lookup boundary uses the strengthened same helper.
Owner access repairs only owner discovery. Participant access additionally checks
current Team membership and repairs only that exact entitled viewer's discovery;
it does not enroll unrelated participants or memberships already stale at that
final Team snapshot. Configured current/previous
source-pointer generations are validated without migration or expiry extension.

The repair EVAL rereads and strictly validates the canonical record, performs
semantic CAS against expected canonical content, verifies every present source
pointer, and derives the digest from current wire bytes immediately before the
discovery write. It rejects changed lifecycle/content/identity/participants,
conflicting pointers, missing or expired authority, malformed records, and
invalid capacity/type/TTL. Failure is surfaced by the existing response contract.
Permitted legacy owner enrichment uses `SET KEEPTTL`. Existing canonical content
and array semantics remain unchanged; source pointer retention is preserved.

New-create atomicity remains within the original EVAL. Discovery repair uses
only necessary HSET/HDEL/PEXPIRE under the existing bounded retention/capacity
rules, with canonical SET KEEPTTL only for permitted legacy identity enrichment.
There is no PERSIST, new key topology, periodic repair, workspace SCAN, per-row
repair, or new migration. The existing bounded full-index capacity check may
inspect up to 1,000 exact canonical references; this is not new enumeration.

The frontend adds only a successful exact-read publication event. It checks the
returned Collaboration ID and mailbox against lookup/request binding before
calling the existing account-fenced summary store, ahead of modal cancellation.
An unknown ID refreshes `list_summaries`; a read DTO cannot invent routing or
promote absent server authority. Closing the modal or selecting another message
during an already-started read does not lose publication or reopen stale UI.
An account/workspace change fences the old callback. Closing during lookup still
cancels the subsequent read under the existing cancellation contract.

Priority meaning, ranking, work-state ordering, Newest/Oldest, provider authority,
Done/remove, and noise suppression are unchanged. No modal/local cache,
localStorage, fuzzy source matching, optimistic fake summary, or fallback is added.

## Atomicity boundary and historical scope

Canonical/source/discovery validation and writes are atomic within Collaboration
Redis. Current Team membership and managed-mailbox/account configuration are
external request-snapshot authorities in the frozen architecture. They cannot
be atomically serialized with the Collaboration Lua script. A change observed
before authority resolution/participant revalidation is denied; a canonical or
pointer race before EVAL is rejected by the script. A Team/config change after
the final external snapshot is not claimed to be transactionally fenced.

Summary requests independently resolve current account/Team membership and
compare the exact membership reference. They preserve the existing owner
allowlist check, rather than performing a fresh managed-inbox ownership or
source-pointer lookup per summary. This slice does not claim a new cross-store
linearizability guarantee or redesign those frozen filtering semantics.

Migration's owner mailbox/provider restriction is proven by code. A historical
owner pass for `gmail-carltricksmusic` could not repair the same owner's IMAP
records. The prior first-owner-write runbook names that Gmail mailbox, and the
C3B1F document records a three-candidate dry run, but neither proves which
mailbox was used in the actual previous migration. No migration is reopened.
Lazy repair closes exact access/create flows for historical records when their
existing authority can be proven; untouched historical records remain outside
enumerable discovery until an authorized exact access. This is not a claim of
complete historical backfill.

## Test matrix scope and execution

`test_discovery_repair.py` covers new Google/IMAP canonical owner-only, Team, and
guest enrollment; healthy and missing/invalid-discovery repeats; legacy owner
enrichment; current and stale Team entitlement; owner-only versus viewer-only
repair; lifecycle resolve/reopen; canonical/source/discovery absolute expiry;
wrong scopes; pre-EVAL canonical/source races; and previous HMAC generation.
External authorities are injected, while the actual application/storage code
and Lua run against disposable local Redis. No Production provider or Redis is
used. An existing retention assertion in `test_lua_redis_integration.py` now
asserts duplicate creation preserves the exact canonical/pointer expiries.

The frontend matrix has 14 new access scenarios (Google/IMAP crossed with
repair, close, selection change, wrong ID, wrong mailbox, read failure, and absent
authoritative summary). Existing actual Priority integration expressions remain
executable. The create matrix explicitly covers both `created` values, Team and
external branches, failure, and modal-close behavior. The real hook suite verifies
old callbacks cannot refresh after account/workspace changes.

**32 isolated frontend suites passed**: summary API/store/hook and C3B2 Priority
integration; owner source/read/write/guest APIs; access panel, guest view and guest
route; frozen Priority source/runtime/reason/Normal/noise/workflow/semantic and
identity engines; ranking, workflow, semantic-shadow, waiting, and mailbox-polling
integration. A temporary filesystem guard blocked protected paths and unmocked
network was disabled. An additional collaboration-identity suite was excluded
after the guard stopped a transitive transport read before file access. The
AccessPanel TSX harness initially lacked `global.React`; its corrected isolated
rerun passed. Exact changed-file TypeScript syntax diagnostics and scoped
`git diff --check` passed. No full build/typecheck traversing protected sources
was run.

## Inspected file inventory

Runtime/backend trace used exact files: `owner.py`, `owner_http.py`, `application.py`,
`authorization.py`, `source_message.py`, `redis_store.py`, and
`discovery_migration.py` in this directory. Evidence/test inspection included
`test_discovery_repair.py`, `test_lua_redis_integration.py`, and focused references
to `test_application.py` and `test_summary.py`. Contract evidence included
`C3B1B_SUMMARY_AUTHORITY.md`, `C3B1C_DISCOVERY_MIGRATION.md`,
`C3B1D_OPERATOR_GRANT.md`, `C3B1E_RUNTIME_DRY_RUN.md`, `C3B1F_RUNTIME_APPLY.md`,
`frontend/tools/COLLABORATION_OWNER_WRITE_RUNBOOK.md`, and
`frontend/tools/COLLABORATION_RUNTIME_ALLOWLIST_BOOTSTRAP_RUNBOOK.md`.

The backend regression review additionally inspected `test_lifecycle.py`,
`test_discovery_migration.py`, `test_discovery_migration_runtime.py`,
`test_discovery_migration_runtime_apply.py`, `test_models.py`, `test_mutations.py`,
`test_authorization.py`, `test_redis_store.py`, `test_owner_http.py`,
`test_guest_http.py`, `test_guest_session.py`, `test_owner_idempotency.py`,
`test_source_message.py`, and `v2_stateful_test_store.py`; external authority
review used `frontend/api/team/authority.py` and
`frontend/api/user_config_store.py`. The one source-message test importing the
actual provider chain was excluded; that module was not in the final run.

Frontend inspection covered the repository and frontend `package.json` files;
`frontend/docs/c3b2-priority-collaboration.md`; `WorkspaceShell.tsx` and its
collaboration Priority/owner-read and narrowly selected frozen Priority tests;
`CollaborationAccessPanel.tsx` and tests; owner transport/read/write/source-locator
APIs and tests; summary API/store/hook and tests; guest APIs/components/route
tests; and exact Priority/identity engine test imports listed in the execution
scope above. Ancestor `AGENTS.md` probes for the inspected directories found none.
After the attachment was loaded, searches/history inspection were scoped to
explicitly named permitted files, with these procedure exceptions: before the
attachment contents were available, initial filename discovery used `rg --files` restricted
to manifest/README/guidance patterns, despite the attachment prohibiting that
command. The initial baseline gate also used an unscoped quiet cached-diff
emptiness check, despite the global cached-diff prohibition; the index was empty.
Neither command read protected contents. No further filename discovery ran;
subsequent final staging verification uses status and explicitly scoped paths.
Protected repository files were not read, diffed, changed, or staged.

## Redis work measured locally

The quota regression executes twelve audience/outcome cases. Counts exclude
authentication/provider I/O and test observation commands. Owner-only measures
canonical storage; Team and guest measure the verified application with external
authority/provider/guest DTO fixtures. Current HMAC generation is used here.

| Path | Redis network commands | Healthy repeat writes | Missing field, existing healthy HASH | Missing HASH |
| --- | --- | --- | --- | --- |
| Owner-only canonical create | New: 1; repeated: 3 | 0 | 1 HSET | 1 HSET + 1 PEXPIRE |
| Verified Team create (owner + one participant) | New: 1; repeated: 4 | 0 | 2 HSET | 2 HSET + 2 PEXPIRE |
| Verified guest create | New: 1; repeated: 6 | Existing 1 guest-history SET; 0 discovery writes | Existing 1 SET + 1 HSET | Existing 1 SET + 1 HSET + 1 PEXPIRE |

Fresh owner-only and Team creates use two canonical/source SET commands plus
HSET/PEXPIRE for each recipient. Fresh guest create uses its existing six graph
SET commands plus owner HSET/PEXPIRE. No additional network command is introduced
for create or duplicate repair. The ordinary duplicate uses one additional nested
PTTL to obtain remaining retention, and removes its previous retention writes.
Guest existing creation adds the bounded current-thread PTTL and owner discovery
preflight/commit inside the existing EVAL.

Exact authenticated ID reads add one repair EVAL. Participants also perform one
fresh exact Team lookup (at most two GETs) immediately before repair, in addition
to the original authorization snapshot. Owner access does not add a Team lookup.
Existing source-HMAC rotation remains in the source loader and may coalesce
pointer generations with bounded remaining expiry; healthy current-generation
duplicates do not perform those rotation writes.

Every repeated quota case asserts unchanged canonical/source bytes and absolute
expiry. Healthy cases additionally assert unchanged discovery bytes/expiry.
Discovery TTL may increase only to the canonical remaining retention when the
existing HASH would otherwise expire too early; a longer valid HASH TTL covering
other entries is retained. Capacity pruning remains the existing bounded rule.

## Existing fixture adjustments and verification environment

Two application-only projection tests now stub and assert the newly required
discovery boundary alongside their existing canonical/authority stubs. Actual
repair behavior is covered by the isolated Redis tests. The stateful test double
now preserves duplicate retention, matching the production Lua contract.

One older guest Unicode integration fixture expected six keys and omitted the
already-existing C3B1B owner discovery key. Its exact baseline Lua and baseline
test method from `3523845` reproduced all four failing subcases before the
expected key set was corrected. No new key family was introduced by this repair.

Final verification uses the repository's existing `venv/bin/python` (Python
3.11.1, Unicode 14). An initial run with the desktop's newer bundled Python had
different Unicode categories and rejected fixtures that the repository runtime
accepts; runtime validators were not changed to accommodate that environment.
Every Python invocation sets `PYTHONDONTWRITEBYTECODE=1`; caches are not deleted.
The temporary test runner blocks protected reads and nonlocal network access.

## Final local validation

The final repository-Python run passed **502 tests in 381.648 seconds**, exit 0:
`test_lifecycle`, `test_discovery_migration`, `test_discovery_migration_runtime`,
`test_discovery_migration_runtime_apply`, `test_models`, `test_mutations`,
`test_authorization`, `test_application`, `test_redis_store`, `test_owner_http`,
`test_guest_http`, `test_guest_session`, `test_owner_idempotency`, the complete
`test_lua_redis_integration`, `test_summary` (22 methods), and
`test_discovery_repair` (20 methods, including the twelve-case quota measurement).
All module names are under `api.collaboration`. This covers the requested
C3B1A/B/C, external guest, C2G5/5.2, and current participant/access regressions.
C2G6 access/projection and C3B2/frozen Priority checks also passed in the 32
permitted frontend suites. Counts identify distinct methods/suites, not retries
or each parameterized subcase.

The original Production condition is locally reproducible and repaired with the
same canonical ID, unchanged canonical/source bytes and absolute expiry, and
unchanged unrelated discovery fields. Legacy enrichment changes only the allowed
owner authority group with KEEPTTL. Both exact source providers pass. New creates
remain atomic, repeated successful guest creates now publish owner discovery,
and direct authorized access no longer reports a clean success while its proven
discovery repair fails.

The local implementation is **GO under the frozen authority contract**. The
specific Production incident's source/cause remains unverified; complete untouched
historical backfill and instantaneous external-store revocation were not claimed.
No Production HTTP/Redis, environment change, migration, push, or deploy ran.
Push/deploy remain **NO-GO** under this task's explicit instruction.
