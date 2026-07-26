# Cuevion verification-code email operator runbook

## Scope and immutable contract

This runbook is for a later, separately approved production change to the Verification Email (Code) template only.

- Template ID: `verify_email_by_code`
- Subject: `Your Cuevion verification code`
- From display name: `Cuevion`
- Message source: `frontend/auth0/email-templates/verify_email_by_code.html`
- Required Liquid value: `{{ code }}`
- Manifest state: `enabled: true`

This repository has no existing email-template Management API operator or infrastructure-as-code contract. This foundation intentionally adds no writer and performs no live change. Do not change the login flow, verification mechanism, code expiration, connection, application, callbacks, sessions, account authority, route, database, provisioning, or MFA policy while applying this template.

## A. Check the current provider without changing it

1. Open the intended production environment in the Auth0 Dashboard.
2. Navigate to **Branding → Email Provider**.
3. Inspect only; do not select, save, disconnect, or rotate anything.
4. Treat the built-in provider as active when the page identifies the current provider as the built-in/default Auth0 provider and no external provider configuration is active. If the page is ambiguous, stop and have the environment owner confirm the current state.
5. Record only the provider category, environment, reviewer, and check time in the approved change record. Never copy credentials, provider secrets, or full configuration screenshots into the record.

## B. External provider gate

An external provider is required before customized templates can become active. Do not select a provider on the user's behalf in this slice. A later approved change may use one of these neutral categories:

- a dedicated SMTP provider;
- a supported email integration;
- a custom provider.

Before touching the template, prove all of the following:

1. The dedicated sender address or its domain exists at the chosen provider.
2. SPF is configured for that provider.
3. DKIM is configured and signs provider test mail successfully.
4. The provider can send a successful test message from the intended sender.

The concrete From address is operator-supplied only after those checks. Do not infer or hardcode an address.

## C. Production security conditions

- Use a dedicated Cuevion sender, not a personal mailbox.
- Configure SPF and DKIM; configure DMARC where possible.
- Keep mailbox passwords, provider credentials, and all secrets out of Git.
- Do not reuse Cuevion application mailbox credentials.
- Do not reuse Gmail, IMAP, or SMTP credentials from any application configuration.
- Do not expose secrets in screenshots, logs, tickets, shell history, or copied dashboard output.
- Keep production and non-production providers, senders, credentials, and test accounts separated.

Stop the change if the provider identity, sender verification, SPF, DKIM, or test-send evidence is missing.

## D. Set the template in a later approved change

1. Before editing, export the current Verification Email (Code) values for **From**, **Subject**, **Message**, and **Enabled** to an access-controlled rollback record.
2. In the Auth0 Dashboard, navigate to **Branding → Email Templates → Verification Email (Code)**.
3. Set **From** using the provider's supported format:
   - display name: `Cuevion`;
   - address: the dedicated, operator-supplied address proven by the provider gate above.
4. Set **Subject** to exactly `Your Cuevion verification code`.
5. Set **Message** from the canonical `.html` file. Preserve the single `{{ code }}` placeholder exactly.
6. Set **Enabled** on only after the provider and sender gates are satisfied.
7. Review the four fields against the manifest before saving.

Never paste `verify_email_by_code.preview.html` into the dashboard. It contains sample data, is marked `deployable: false`, and has no live Liquid placeholder. Do not use the preview as an API or dashboard payload.

## Local preview

The preview is deterministic and offline. Its generator substitutes only `{{ code }}` with the sample code in the canonical template:

```sh
cd /Users/rutger/cuevion-app
PYTHON=/Users/rutger/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
"$PYTHON" frontend/auth0/email-templates/generate_verify_email_by_code_preview.py
```

Open `frontend/auth0/email-templates/verify_email_by_code.preview.html` directly in a browser. The `.preview.html` filename and browser title identify it as local preview output. Regenerate it after every canonical-template edit; the test suite rejects drift.

## E. Test through the real verification flow

A generic dashboard preview is not sufficient. After an approved save, use a controlled production-safe test account and initiate the real Cuevion login/email-verification flow. Verify:

- the From display name is **Cuevion** and the address is the approved dedicated sender;
- the subject is exactly **Your Cuevion verification code**;
- no Auth0 logo or other default branding is visible;
- no broken image block appears;
- no environment label or `cuevion-dev` appears;
- the delivered code is complete, centered, selectable, and readable;
- the code completes the intended verification flow;
- desktop and mobile rendering;
- light and dark mail-client appearance;
- SPF and DKIM pass in the received message headers;
- no spam or sender warning appears.

Record the mail client/version, viewport class, header-authentication result, and pass/fail outcome without recording the code, recipient address, cookies, tokens, credentials, or message headers containing personal data.

## F. Rollback

1. Keep the pre-change template export available throughout rollout.
2. If rendering, substitution, or deliverability regresses, restore the previous **From**, **Subject**, **Message**, and **Enabled** values from that export.
3. Do not remove or disconnect the provider configuration during a template rollback.
4. Do not disable the verification flow as a cosmetic rollback.
5. Re-run the real-flow test after restoration and record the result.
6. Escalate provider or sender-authentication failures separately; never work around them with a personal mailbox or application mailbox credentials.
