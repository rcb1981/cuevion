import json
import sys
from imap_connect_preview import build_connect_preview_response


def read_payload() -> dict[str, Any]:
    raw_payload = sys.stdin.read()

    if not raw_payload.strip():
        raise ValueError("Missing request payload")

    return json.loads(raw_payload)


def main():
    payload = read_payload()
    status_code, response_payload = build_connect_preview_response(payload)
    sys.stdout.write(json.dumps(response_payload))

    if status_code >= 400:
        sys.exit(1)


if __name__ == "__main__":
    main()
