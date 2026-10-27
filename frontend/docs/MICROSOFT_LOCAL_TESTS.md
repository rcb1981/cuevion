# Local Microsoft recovery verification

The runner executes local unit tests in separate processes with an explicit environment that contains no inherited service credentials. HTTP/socket transport guards fail the run if an unmocked network operation is attempted. No live mailbox, OAuth, Redis, Neon, SMTP, deployment or browser acceptance test is included. The Python selection is an explicit set of mock-based regression modules; it excludes the Collaboration Redis integration harnesses.

The test runner does not install dependencies, enable release flags, change package manifests, or suppress known failures. Its default discovers all Microsoft-named `src/**/*.test.ts`, `.tsx`, `.mjs` and `.cjs` files, excluding browser `.web.test.*` files. Each file has a fresh process so asynchronous tests cannot overwrite another suite's fetch mock.

## Prerequisites

- Node.js 24.14.0 was used. Native TypeScript loading is needed by `microsoftComposeRequest.test.mjs`.
- Python 3.11.1 was used, with the existing local Python dependencies for auth cryptography and the application modules.
- A locally provisioned `frontend/node_modules` containing `sucrase@3.35.1`, `react@18.3.1`, `react-dom@18.3.1`, and `react-test-renderer@18.3.1` is required for frontend verification. Renderer and components must resolve the same React installation.
- `react-test-renderer` was provisioned as a local test dependency only. It is not declared in the preserved product `package.json` or lockfile. A fresh clone therefore requires explicit local test dependency preparation before running this command. The runner never downloads packages automatically.
- The runner uses the project's automatic JSX runtime and `Europe/Amsterdam`, matching the existing reply-quote fixture's timezone assumption.

## Commands

Run from the complete recovered checkout:

```sh
python3 frontend/tests/microsoft_run_local_tests.py
python3 frontend/tests/microsoft_run_local_tests.py --group frontend
python3 frontend/tests/microsoft_run_local_tests.py --group python
```

The default runs the Microsoft frontend suites. `frontend` additionally runs the suite files registered in package test scripts, the non-browser Collaboration unit suites, and the Auth0 login unit suite. `python` runs the reviewed Gmail/IMAP, Auth0 and Collaboration regression modules. It does not replace the separate Microsoft backend suite.

Use `--suite src/components/workspace/MicrosoftDraftEditors.interaction.test.cjs` for a targeted frontend run. Use `--output /private/tmp/cuevion-microsoft-verification` to choose an external evidence directory. Logs, transport guards and a JSON result are written outside the checkout. `--root` can select an independently prepared baseline checkout. Every nonzero child exit or timeout makes the runner exit nonzero; baseline failures are never treated as passes.

## Verified baseline failures on 23 October 2026

The following five frontend failures were reproduced on unchanged Git commit `d61c33d90a7b10cb17e5827c6c19991f6b543dfa` with the same local dependencies:

| Suite | Failing expectation |
|---|---|
| `App.collaborationGuestRoute.test.ts` | Guest-route source ordering before an old `preview` conditional |
| `MicrosoftFilteredSenderAction.test.ts` | Source still contains `<MicrosoftFilteredSenderAction` |
| `MicrosoftMailboxFolderRail.test.ts` | Rendered folder rail contains `lg:flex` |
| `inboxConnectionApi.gmailOwnership.test.ts` | Gmail payload omits the established `knownBodyProviderMessageIds: []` field |
| `mailboxDisplayName.test.ts` | Old source assertion for truncation on long mailbox email identity surfaces |

The separate Python `api.inboxes.test_gmail_ownership_hardening` suite also reproduces 74 failures and 13 errors in its 131 cases on that exact baseline. All 87 failing case identities match the restored checkout. Examples include old callback doubles without `_release_callback_mailbox_lease` and handler expectations that now return 503. These results have not been waived or repaired by altering Gmail/provider code.

Initial harness-only failures from classic JSX and UTC were rerun successfully with automatic JSX and Europe/Amsterdam. Those are separate from the baseline failures above.

Source byte equality and passing mock tests do not establish real-Graph acceptance or complete Gmail/IMAP/Microsoft feature parity. Keep production write gates disabled until a separate approved release process completes.
