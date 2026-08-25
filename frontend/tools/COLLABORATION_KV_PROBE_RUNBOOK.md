# Collaboration v2 KV compatibility probe runbook

This is an operator-only probe for the dormant Collaboration v2 owner boundary. It is not an application route and is never imported by application startup. **Do not execute it against production as part of ordinary development.** A production run is a separate, explicitly authorized operation.

## Preconditions

- Use an approved commit and an unchanged copy of `tools/collaboration_kv_probe.py`.
- Obtain explicit authorization for the named remote KV target and the single ephemeral run.
- Verify `CUEVION_COLLAB_V2_HTTP_MODE` remains off; this probe does not require or change it.
- Inject the correct `KV_REST_API_URL` and `KV_REST_API_TOKEN` securely into only the operator process. Do not place either value in a command-line argument, dotenv file, transcript, or ticket.
- Do not use user data. The probe generates its own random, non-PII run ID and fixed synthetic records.
- Confirm no other process imports or exposes this module as an HTTP handler.

## Dry run

From the `frontend/` directory:

```sh
python3 -m tools.collaboration_kv_probe
```

This is the default. It reads no KV credentials, performs no network operations and no KV writes, and prints the planned owner-read and owner-write cases plus hard load limits.

## Local validation

Run the same scenario engine against a fresh isolated local Redis process:

```sh
python3 -m tools.collaboration_kv_probe --local
```

The local process listens only on a temporary Unix socket, has persistence disabled, and is terminated after the run. A successful result must report both `OWNER_READ_KV_COMPATIBLE` and `OWNER_WRITE_KV_COMPATIBLE`, no failed tests, bounded TTLs, and successful explicit cleanup with TTL fallback.

## Remote arming

Remote execution has two independent guards:

1. the `--execute-remote` command-line mode; and
2. `CUEVION_COLLAB_KV_PROBE_CONFIRM` set exactly to `EXECUTE_EPHEMERAL_COLLAB_V2_KV_PROBE`.

The URL and token are accepted only from `KV_REST_API_URL` and `KV_REST_API_TOKEN` after both guards are present. The URL must be a bare HTTPS origin without user-info, query, or fragment. The token is never accepted on the command line or rendered in output.

## Remote execution

This section is documentation for a later authorized task only. Do not perform it during ordinary development.

In a securely provisioned operator environment, enter `frontend/`, inject the three environment values without echoing or persisting them, and run:

```sh
python3 -m tools.collaboration_kv_probe --execute-remote
```

Do not add retries, parallel invocations, shell tracing, output piping to shared logs, or alternate Redis commands. There are no automatic transport retries. One complete successful scenario set has this fixed request calculation: 55 owner-read commands + 52 owner-write commands + 26 final PTTL audits + 1 exact-list cleanup DEL = 134 commands/HTTP requests. Its fixed EVAL calculation is 43 owner-read + 22 owner-write = 65 EVAL calls, and it registers 26 keys. The immutable safety ceilings are 32 registered keys, 160 commands/HTTP requests, 96 EVAL calls, 8 concurrent requests, a 40-second cutoff for starting remote commands, and a 60-second remote runtime target. The headroom is bounded and cannot expand a scenario loop.

## Expected output

The tool prints one redacted JSON object. It includes the probe version, commit, random run ID, UTC start/end, normalized scenario results, response type summaries, owner-read and owner-write verdicts, failure names, counts, configured limits, and cleanup/TTL status.

Only an unambiguous report with the intended compatibility verdict, no failed tests, valid response types, `bounded_or_expired` TTL status, and `explicit_cleanup_succeeded_with_ttl_fallback` is compatible evidence. Owner-write can fail while owner-read passes. Any `INCOMPATIBLE`, `INCONCLUSIVE`, nonzero exit, malformed/missing output, timeout, or cleanup uncertainty is not proof.

## Abort conditions

Abort without another attempt if authorization, commit identity, target identity, secure credential injection, owner-mode-off status, or output hygiene cannot be established. Abort on any configuration, transport, response-shape, Lua, race, TTL, command-budget, runtime-cutoff, or cleanup failure. Do not broaden the command set, inspect database contents, use production records, or change production Redis/application semantics to make the probe pass.

## Cleanup and TTL

Every write is limited to the exact namespace:

```text
cuevion:collab:v2:{cuevion-collab-v2}:probe:<random-run-id>:<validated-suffix>
```

Every key receives a TTL of at most 120 seconds. TTL expiry is the primary cleanup guarantee. The tool also retains the exact registered key list in memory, revalidates every key, and issues one bounded `DEL`; it never uses patterns, `SCAN`, or `KEYS`. If explicit cleanup is uncertain, stop and allow the TTL fallback to expire—do not perform manual broad cleanup.

## Post-run secret and log hygiene

- Do not retain shell history, environment dumps, traces, screenshots, or raw transport errors containing credentials.
- Unset the confirmation, URL, and token in the operator process immediately after the run and follow the approved credential-rotation policy if exposure is suspected.
- Retain only the redacted JSON report through the approved evidence channel.
- Treat an unexpected URL, authorization header, token, real identity, mailbox ID, or record value in any output as a secret-handling incident; stop distribution and follow the incident process.
- A successful probe is compatibility evidence only. It does not activate owner routes, approve retention, authorize frontend migration, or resolve any other activation blocker.
