# Collaboration v2 owner/mailbox allowlist runbook

## Purpose

This runbook covers offline validation and generation of deployment-ready digest
lists for the dormant Collaboration v2 owner route. The tool is
`tools.collaboration_allowlist`, located outside `api/`; it exposes no HTTP
handler, has no Vercel route, performs no network operation, and never updates
Vercel. Configuration and deployment require a later, separately authorized
operator change.

Keep `CUEVION_COLLAB_V2_HTTP_MODE` absent or `off` throughout allowlist
preparation. Generating lists does not make `owner_read` or `owner_write` ready.

## Security model

The tool imports the canonical production validators and HMAC derivation from
`api.collaboration.owner_request_security`. Owner entries bind the exact Auth0
issuer, authentication version, and Auth0 subject. Mailbox entries bind the
same owner tuple plus one canonical lowercase mailbox ID. The domains, length
framing, SHA-256 HMAC, and `v1_` plus canonical unpadded base64url output are
owned by that production module and are not duplicated in the tool.

The allowlist HMAC key must be independently generated and must never be reused
as any of the following:

- `CUEVION_COLLAB_V2_OWNER_CSRF_KEY` or its previous key
- `CUEVION_COLLAB_V2_RATE_LIMIT_HMAC_KEY`
- `CUEVION_COLLAB_INDEX_HMAC_KEY` or its previous key
- `MAILBOX_SECRET_ENCRYPTION_KEY`
- `CUEVION_AUTH_SESSION_SECRET`
- any guest-session or invitation secret

Distinctness is enforced through the approved secret-management review. Do not
provide other secrets to this tool: it deliberately reads only the allowlist
key and therefore cannot compare against or expose unrelated keys.

## Preconditions

- Work on a controlled operator machine with trusted local process and shell
  access.
- Obtain the canonical values from approved server-side Cuevion/Auth0/account
  state, with access logged under the applicable operating procedure.
- Confirm the selected mailbox scope explicitly. Do not infer or select every
  configured mailbox.
- Confirm `CUEVION_COLLAB_V2_HTTP_MODE` is staying absent/off.
- Do not use production values in tickets, source control, chat, test fixtures,
  or terminal transcripts.

## Prepare canonical input

Prefer stdin. The closed JSON schema is:

```json
{
  "owners": [
    {
      "issuer": "synthetic-auth-v1",
      "authenticationVersion": 1,
      "subject": "synthetic:user_0000000001",
      "mailboxes": ["synthetic.mailbox-1"]
    }
  ]
}
```

The example is synthetic. Replace it only in the controlled offline operator
session. There are no optional or additional fields. Owners and mailbox arrays
must be non-empty. Values are validated exactly; whitespace is not trimmed and
mailbox case is not changed. Duplicate JSON fields, owner tuples, or mailbox IDs
within an owner are rejected.

The trusted sources are the server-side records that feed the reviewed owner
authentication adapter: the revalidated Auth0 session identity supplies
`issuer`, `authenticationVersion`, and `subject`; the canonical Cuevion account
and mailbox authority supplies the mailbox ID. Never copy identity values from
browser localStorage or other client-controlled state. This repository does not
currently expose a reviewed operator identity-report command. If the operator
cannot read these exact canonical fields from an approved Auth0/Cuevion
administrative source, abort: that is the next operational gap and must not be
worked around by weakening validation or adding an introspection endpoint.

If `--input PATH` is used instead of stdin, the tool reads only that explicit
file and creates no copy or transformed identity file. Store it only in an
approved encrypted temporary location, restrict access, and securely remove it
under the applicable data-handling procedure after review. Never place it in
the repository.

## Validate / dry run

From the `frontend/` directory, provide the JSON on stdin:

```text
python -m tools.collaboration_allowlist --dry-run
```

Or name one controlled local file explicitly:

```text
python -m tools.collaboration_allowlist --dry-run --input /approved/path/input.json
```

Dry-run does not read or require an HMAC key and performs no HMAC operation. It
prints only validation status and counts; it never prints raw identities.

## Generate a new HMAC key

In an approved secret-generation environment, use a cryptographically secure
random generator to create at least 32 independent random bytes and encode them
as canonical unpadded base64url. Do not generate a production key as part of
ordinary development or testing. Store it directly in approved secret
management, never in a repository file, shell argument, ticket, or chat. Review
its distinctness from every secret listed above.

## Generate digest lists

Make the key available only to the generator process as
`CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY`; never pass it as a CLI argument. Then
provide the validated JSON via stdin or the explicit controlled file:

```text
python -m tools.collaboration_allowlist --generate
```

Generate mode requires the canonical key and valid bounded input. It prints
safe counts and exactly these deployment-ready assignments, with sorted,
comma-separated digest entries and no spaces:

```text
CUEVION_COLLAB_V2_OWNER_ALLOWLIST=v1_<digest>,v1_<digest>
CUEVION_COLLAB_V2_MAILBOX_ALLOWLIST=v1_<digest>,v1_<digest>
```

It does not print the key, issuer, subject, or mailbox ID. Avoid shell tracing,
command-output logging, and terminal capture. Clear the process-scoped key from
the operator environment when the controlled session ends.

## Vercel variables involved

The later, separately authorized configuration change must treat these as one
matched set:

- `CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY`
- `CUEVION_COLLAB_V2_OWNER_ALLOWLIST`
- `CUEVION_COLLAB_V2_MAILBOX_ALLOWLIST`

This tool does not read or write Vercel variables and uses no Vercel token or
CLI. `CUEVION_COLLAB_V2_HTTP_MODE` remains absent/off during this procedure.

## Initial private-beta rollout

The reviewed tool maximums are 25 owners and 50 total owner/mailbox entries.
The initial rollout is intentionally much smaller: exactly one owner and only
the explicitly selected test mailbox or mailboxes. Do not automatically
allowlist every configured mailbox. Any expansion requires scope review even
when it remains below the hard maximums.

## Coordinated deployment

1. Review dry-run counts, selected identities, and selected mailbox scope using
   approved secure channels.
2. Generate the key and both digest lists offline.
3. Prepare the key and both lists as one indivisible configuration set.
4. In a separately authorized change, deploy all three matching values
   together. Never combine a key with lists produced by another key.
5. Only after all other activation blockers are closed may a separately
   reviewed activation change alter `CUEVION_COLLAB_V2_HTTP_MODE`.
6. Verify the explicitly allowed owner and selected mailboxes remain reachable,
   and verify a synthetic or approved non-allowlisted case remains denied,
   without exposing raw values in logs.

## Rotation

There is no previous-key fallback for this allowlist design. Rotation is a
coordinated configuration replacement:

1. Generate a new independent HMAC key offline.
2. Regenerate both owner and mailbox digest lists using the new key.
3. Prepare the new key plus both new lists as one configuration set.
4. Deploy the new key and both new lists together.
5. Verify the allowed owner and selected mailboxes remain reachable.
6. Retain the previous matched configuration only through approved secret
   management and rollback controls.

Never deploy a new key with old lists or an old key with new lists.

## Rollback

Rollback restores the old key, old owner list, and old mailbox list together as
one matched set. Restoring only one or two values fails every mismatched digest
closed. Follow the approved configuration rollback procedure and re-verify the
selected scope afterward.

## Cardinality limits

The generator refuses more than 25 owners or more than 50 mailboxes total. It
also rejects duplicates and sorts digest output deterministically so reviewed
configuration and rollback sets compare reliably. These are hard generation
limits, not rollout targets.

## Abort conditions

Abort without generating or deploying when any of the following is true:

- canonical identity values cannot be obtained from an approved trusted source;
- input validation or count review fails;
- owner or mailbox scope is ambiguous or broader than explicitly approved;
- the key is malformed, shorter than 32 decoded bytes, reused, or may be exposed;
- key and both digest lists cannot be deployed or rolled back together;
- `CUEVION_COLLAB_V2_HTTP_MODE` would be changed by this preparation task;
- any remaining activation requirement is being treated as resolved by this
  offline generation procedure.

## Secret and input hygiene

The tool has no telemetry, persistence, DNS, HTTP, Auth0, Cuevion, Vercel, KV,
Gmail, or IMAP access. It never writes input or output to a file. Raw Auth0 and
mailbox identifiers remain sensitive operational data even though they are not
secrets; minimize their lifetime and audience. Digest lists are deployment
configuration, not public artifacts. Handle the HMAC key as a production
secret, and handle matched key/list rollback sets under approved secret
management.
