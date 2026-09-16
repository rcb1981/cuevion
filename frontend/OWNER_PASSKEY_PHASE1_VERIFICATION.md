# Phase 1 verification and baseline differential

Baseline: `9dfae08ab329614961e66b77a156603a43fd8080`.

The legacy tests and fixtures are unchanged. The user's revised commit gate
permits the verified baseline failures only when the failing tests, subtests,
failure classifications and assertion causes match, with no Phase 1 regression.

## Reproduction and isolation

The baseline run used an isolated export of the baseline commit. All 674 allowed
tracked frontend files were checked byte-for-byte against their Git blob IDs.
The baseline root `.gitignore` was also restored from the same commit. A private
Git index marks omitted root files as skip-worktree; the export has clean Git
status. The export excluded the protected files before extraction. No baseline source
was patched. Both runs used the same isolated Python 3.11.1 environment,
pytest 9.1.1, cryptography 46.0.7, psycopg 3.3.5, PostgreSQL 16.2 and local Redis.
The repository specifies Python 3.12; no 3.12 runtime was available locally.
No dependency or runtime-version file was changed.

Both full runs used this selection from their respective frontend directories:

```text
python -m pytest --import-mode=importlib -q --tb=short
  api/auth api/team api/collaboration tests/cuevion_auth tests/cuevion_db
  cuevion_db/test_postgresql_team_invitee_repository.py
  tools/test_collaboration_allowlist.py tools/test_collaboration_allowlist_authority.py
  --deselect=api/collaboration/test_import_safety.py::CollaborationV2ImportSafetyTests::test_active_inbox_routes_and_frontend_do_not_reference_inactive_application_modules
```

The Phase 1 selection additionally includes `tests/cuevion_migration`. Its new
PostgreSQL tests are already included by `tests/cuevion_db`. The baseline export
uses private Git metadata at the same baseline commit for existing `git ls-files`
and `git status` inventory tests. No new baseline commit was made. The application
checkout's staging area remained empty throughout the comparison runs.

An earlier baseline run had one additional harness failure: its frontend-only
export shared the full application index, so omitted root dependencies appeared
as deletions to the filename guard. Restoring the baseline root ignore file and
using the private sparse index corrected that artifact. The affected test then
passed; the entire baseline suite was rerun using the corrected metadata.

The one deselection is identical in both runs and is necessary to honor the
user's prohibition on reading the protected OAuth file: that test traverses all
active inbox Python files. It is not counted as a passing test. All other selected
tests ran, including the real local Redis scenarios and disposable PostgreSQL
migration tests. The PostgreSQL harness cannot accept an application database URL.

An external pytest recorder captures every failed report, including unittest
subtests. The differential compares node ID, subtest parameters, call/setup phase,
exception type, failure message, crash location and every assertion/traceback
location. The machine-readable record is `OWNER_PASSKEY_PHASE1_DIFFERENTIAL.json`.

One old import-inactivity assertion embeds the entire `api/auth/runtime.py` source
in its error message. That source operand necessarily differs after implementation;
only that operand is normalized for comparison. Both raw-message SHA-256 values
are retained. The assertion's actual trigger is identical: the same two existing
`from cuevion_db.postgresql_team_invitee_repository import (` lines. The new
migration branch imports `cuevion_migration`, so it adds no trigger to that failure.
All other failure messages must compare byte-for-byte.

## Passing Phase 1 gates

- All 45 newly added tests passed: 31 migration/configuration/Redis tests and
  14 actual PostgreSQL tests. No existing test file was modified.
- The combined focused auth and migration run passed 142 tests and 187 subtests.
- `npm run test:base` passed.
- `git diff --check` and `git diff --cached --check` passed; all protected paths were excluded from inspection.
- `npm run build` passed. Existing browser-data-age and bundle-size warnings remain.
- `npm run test:team-roster` passed, including 43 route scenarios.
- The original session restoration and retained inventory diagnostic passed
  after a signed migration callback using the real PostgreSQL writer.

The local PostgreSQL evidence establishes insert-only behavior and rollback,
not live Production privileges or provider enrollment. Before a later deployment,
the operator must confirm the existing writer can take the documented table locks.
Auth0 connection/passkey changes and final session revocation remain later work.

## Complete broad-suite differential

The completed run totals and each of the 17 baseline failures are recorded below.

| Run | Passed tests | Failed reports | Passed subtests | Deselected |
| --- | ---: | ---: | ---: | ---: |
| Untouched baseline | 1,735 | 17 | 9,044 | 1 |
| Phase 1 | 1,780 | 17 | 9,077 | 1 |

All 45 additional tests pass. There are no new, removed, or changed failure
causes. Eleven failed reports are record-specific subtests; six are ordinary
test assertions. Both broad runs intentionally return exit status 1 because
these pre-existing failures remain.

The baseline and Phase 1 comparison fingerprints are both:

```text
94f79275c5d9aa8fd0d9fd622060dc14aab2e1d3e489cd5c48d54b061403f4f0
```

Every failure below is pre-existing at the baseline commit, with the same
exception and assertion location in Phase 1.

1. `api/collaboration/test_summary.py::SummaryContractTests::test_actual_team_snapshot_uses_two_exact_reads_and_rejects_removed_or_rebound_members`

   `AssertionError`, assertion `api/collaboration/test_summary.py:442`. The older Team snapshot fixture expects `ok`; current baseline authority returns `storage_unavailable`.

2. `tests/cuevion_auth/test_account_repository_contract.py::PublicSurfaceTests::test_records_have_exact_fields_and_exact_public_signatures` — `record='InitialAccountCreationReceipt'`

   `TypeError`, assertion `tests/cuevion_auth/test_account_repository_contract.py:608`. Frozen string-valued `__signature__` metadata is rejected by `inspect.signature`.

3. `tests/cuevion_auth/test_account_repository_contract.py::PublicSurfaceTests::test_records_have_exact_fields_and_exact_public_signatures` — `record='InitialAccountCreationRequest'`

   `TypeError`, assertion `tests/cuevion_auth/test_account_repository_contract.py:608`. Frozen string-valued `__signature__` metadata is rejected by `inspect.signature`.

4. `tests/cuevion_auth/test_account_repository_contract.py::PublicSurfaceTests::test_records_have_exact_fields_and_exact_public_signatures` — `record='InitialAccountCreationResult'`

   `TypeError`, assertion `tests/cuevion_auth/test_account_repository_contract.py:608`. Frozen string-valued `__signature__` metadata is rejected by `inspect.signature`.

5. `tests/cuevion_auth/test_account_repository_contract.py::PublicSurfaceTests::test_records_have_exact_fields_and_exact_public_signatures` — `record='InitialAccountOperationReference'`

   `TypeError`, assertion `tests/cuevion_auth/test_account_repository_contract.py:608`. Frozen string-valued `__signature__` metadata is rejected by `inspect.signature`.

6. `tests/cuevion_auth/test_account_repository_contract.py::PublicSurfaceTests::test_records_have_exact_fields_and_exact_public_signatures` — `record='InitialSecurityEventRequest'`

   `TypeError`, assertion `tests/cuevion_auth/test_account_repository_contract.py:608`. Frozen string-valued `__signature__` metadata is rejected by `inspect.signature`.

7. `tests/cuevion_auth/test_account_repository_contract.py::PublicSurfaceTests::test_records_have_exact_fields_and_exact_public_signatures` — `record='VerifiedAuthenticationEvidence'`

   `TypeError`, assertion `tests/cuevion_auth/test_account_repository_contract.py:608`. Frozen string-valued `__signature__` metadata is rejected by `inspect.signature`.

8. `tests/cuevion_auth/test_current_account_repository_contract.py::PublicSurfaceTests::test_records_have_exact_fields_signatures_and_hints` — `record='AuthenticationIdentityLookupKey'`

   `TypeError`, assertion `tests/cuevion_auth/test_current_account_repository_contract.py:326`. Frozen string-valued `__signature__` metadata is rejected by `inspect.signature`.

9. `tests/cuevion_auth/test_current_account_repository_contract.py::PublicSurfaceTests::test_records_have_exact_fields_signatures_and_hints` — `record='CurrentAccountAuthority'`

   `TypeError`, assertion `tests/cuevion_auth/test_current_account_repository_contract.py:326`. Frozen string-valued `__signature__` metadata is rejected by `inspect.signature`.

10. `tests/cuevion_auth/test_current_account_repository_contract.py::PublicSurfaceTests::test_records_have_exact_fields_signatures_and_hints` — `record='CurrentAccountAuthorityResult'`

   `TypeError`, assertion `tests/cuevion_auth/test_current_account_repository_contract.py:326`. Frozen string-valued `__signature__` metadata is rejected by `inspect.signature`.

11. `tests/cuevion_auth/test_current_account_repository_contract.py::PublicSurfaceTests::test_records_have_exact_fields_signatures_and_hints` — `record='CurrentAccountByUserAuthority'`

   `TypeError`, assertion `tests/cuevion_auth/test_current_account_repository_contract.py:326`. Frozen string-valued `__signature__` metadata is rejected by `inspect.signature`.

12. `tests/cuevion_auth/test_current_account_repository_contract.py::PublicSurfaceTests::test_records_have_exact_fields_signatures_and_hints` — `record='CurrentAccountByUserAuthorityResult'`

   `TypeError`, assertion `tests/cuevion_auth/test_current_account_repository_contract.py:326`. Frozen string-valued `__signature__` metadata is rejected by `inspect.signature`.

13. `tests/cuevion_auth/test_relational_account_store_contract.py::InactivityAndDocumentationTests::test_requirements_allow_only_the_reviewed_foundation_dependencies`

   `AssertionError`, assertion `tests/cuevion_auth/test_relational_account_store_contract.py:2644`. The frozen dependency inventory omits the already-existing `openai==3.3.1` dependency.

14. `tests/cuevion_auth/test_session_credentials.py::ModuleIdentityAndInactivityTests::test_canonical_identity_namespace_and_exact_file_scope`

   `AssertionError`, assertion `tests/cuevion_auth/test_session_credentials.py:513`. The frozen module inventory omits the already-existing `identity_inventory_diagnostic.py`.

15. `tests/cuevion_db/test_inactivity.py::DatabaseFoundationInactivityTests::test_python_and_dependency_contract_is_exact`

   `AssertionError`, assertion `tests/cuevion_db/test_inactivity.py:88`. The frozen dependency inventory omits the already-existing `openai==3.3.1` dependency.

16. `tests/cuevion_db/test_inactivity.py::DatabaseFoundationInactivityTests::test_tracked_active_api_scan_never_reads_protected_untracked_file`

   `AssertionError`, assertion `tests/cuevion_db/test_inactivity.py:51`. The older inactivity guard rejects the two already-existing Team repository imports in `runtime.py`.

17. `tests/cuevion_db/test_provision_initial_auth0_account.py::ConflictClassificationTests::test_wrong_authentication_method_is_rejected`

   `AssertionError`, assertion `tests/cuevion_db/test_provision_initial_auth0_account.py:755`. The old fixture expects OIDC to conflict; baseline authority already accepts the canonical OIDC graph.

The JSON proof includes all comparison messages, assertion/traceback locations,
raw-message hashes, tested source hashes, run-log hashes, and the explicit rule
for the single embedded source operand. Sixteen raw failure messages are identical;
the seventeenth has the same assertion and trigger with the expected edited-source
dump. No legacy test or fixture was repaired.
