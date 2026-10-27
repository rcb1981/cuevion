# Microsoft Drafts offline verification

Run from the restored project checkout:

```sh
python3 frontend/scripts/test-microsoft-offline.py --drafts-only
python3 frontend/scripts/test-microsoft-offline.py
```

The first command runs Discard, plain-text editing, HTML metadata editing and
the HTTP integration harness. The second runs every `test_microsoft*.py` backend
suite. Tests need the project's Python dependencies installed in the selected
interpreter; the runner never installs dependencies or starts services.

The runner clears inherited environment values, including credentials, in its
own process before importing the application. It blocks socket creation, DNS,
network connections and child-process execution, and fails if these are attempted
even when application code catches the exception. No mailbox, database or OAuth
configuration is changed. Bytecode writes are disabled.

`test_microsoft_draft_local_http.py` passes raw HTTP request bytes through the
actual route handlers using memory buffers. There is no listening server or
socket. Synthetic Preview headers and inert environment values exercise the
existing origin validator. Fake authority, lease storage, token retrieval and a
stateful Graph transport replace external services; payload validation,
Microsoft owner/folder checks, conditional ETags, one-shot writes and independent
readback still execute. The harness covers text and HTML saves, an HTTP 412 race,
lost write acknowledgements, exact draft discard, session revocation, held leases
and origin/Production boundaries.

This enables local verification only. It does **not** enable the live Drafts API
for localhost, create a Vercel Preview, or weaken the Production origin policy.
The existing `MICROSOFT_OAUTH_REDIRECT_URI` configuration is untouched; test values
exist only in the test process. Production release flags remain `False`.

The Discard boundary tests temporarily patch its release gate inside the test to
prove that a future release still requires the canonical host and same origin.
They restore the gate afterwards. The source remains unreleased.

Passing these tests is evidence from fake services. It does not demonstrate live
Microsoft Graph acceptance or authorize a deployment, mailbox mutation or release.
