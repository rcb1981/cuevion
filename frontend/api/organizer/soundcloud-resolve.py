from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from beta_auth import parse_beta_session_token, read_beta_session_cookie  # noqa: E402

ALLOWED_INPUT_HOSTS = {"soundcloud.com", "www.soundcloud.com", "on.soundcloud.com"}
ALLOWED_FINAL_HOSTS = {"soundcloud.com", "www.soundcloud.com"}
PRESERVED_SOUNDCLOUD_QUERY_PARAMS = {"secret_token"}
SOUNDCLOUD_TRACKING_QUERY_PARAMS = {
    "si",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}
SOUNDCLOUD_OEMBED_ENDPOINT = "https://soundcloud.com/oembed"
RESERVED_SOUNDCLOUD_PATHS = {
    "about",
    "charts",
    "discover",
    "for",
    "imprint",
    "jobs",
    "pages",
    "popular",
    "premium",
    "search",
    "settings",
    "stream",
    "terms-of-use",
    "upload",
    "you",
}


class _IframeSrcParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.iframe_src = ""

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() != "iframe" or self.iframe_src:
            return

        for name, value in attrs:
            if name.lower() == "src" and value:
                self.iframe_src = value.strip()
                return


def _send_json(handler: BaseHTTPRequestHandler, status_code: int, payload: dict):
    response_body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(response_body)))
    handler.end_headers()
    handler.wfile.write(response_body)


def _build_error(code: str, message: str) -> dict:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
    }


def _generated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_authenticated_user(headers) -> dict | None:
    session_token = read_beta_session_cookie(headers)
    return parse_beta_session_token(session_token or "")


def _read_json_body(handler: BaseHTTPRequestHandler) -> tuple[dict | None, dict | None]:
    try:
        content_length = int(handler.headers.get("content-length", "0"))
    except ValueError:
        return None, _build_error("invalid_request", "Request body is invalid.")

    if content_length > 8192:
        return None, _build_error("invalid_request", "Request body is too large.")

    raw_body = handler.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""

    try:
        payload = json.loads(raw_body or "{}")
    except json.JSONDecodeError:
        return None, _build_error("invalid_request", "Request body must be valid JSON.")

    if not isinstance(payload, dict):
        return None, _build_error("invalid_request", "Request body must be a JSON object.")

    return payload, None


def _normalize_url(value: object) -> str:
    normalized = str(value or "").strip().replace("&amp;", "&")
    if normalized.lower().startswith("www.") or normalized.lower().startswith(
        "on.soundcloud.com/"
    ):
        return f"https://{normalized}"
    return normalized


def _hostname(parsed_url) -> str:
    return (parsed_url.hostname or "").lower()


def _is_allowed_http_url(parsed_url, allowed_hosts: set[str]) -> bool:
    return (
        parsed_url.scheme in {"http", "https"}
        and _hostname(parsed_url) in allowed_hosts
        and not parsed_url.username
        and not parsed_url.password
    )


def _is_embeddable_soundcloud_permalink(parsed_url) -> bool:
    if not _is_allowed_http_url(parsed_url, ALLOWED_FINAL_HOSTS):
        return False

    path_parts = [part for part in parsed_url.path.split("/") if part]
    if len(path_parts) < 2:
        return False

    profile_or_collection = path_parts[0].lower()
    second_part = path_parts[1].lower()
    if not profile_or_collection or profile_or_collection in RESERVED_SOUNDCLOUD_PATHS:
        return False

    if second_part == "sets":
        return len(path_parts) >= 3

    return bool(second_part)


def _canonicalize_soundcloud_url(parsed_url) -> str:
    canonical_query = [
        (name, value)
        for name, value in parse_qsl(parsed_url.query, keep_blank_values=False)
        if name in PRESERVED_SOUNDCLOUD_QUERY_PARAMS and value
    ]
    return urlunparse(
        (
            "https",
            "soundcloud.com",
            parsed_url.path.rstrip("/"),
            "",
            urlencode(canonical_query),
            "",
        )
    )


def _strip_soundcloud_tracking_params(parsed_url) -> str:
    cleaned_query = [
        (name, value)
        for name, value in parse_qsl(parsed_url.query, keep_blank_values=False)
        if name not in SOUNDCLOUD_TRACKING_QUERY_PARAMS and value
    ]
    return urlunparse(
        (
            "https",
            "soundcloud.com",
            parsed_url.path.rstrip("/"),
            "",
            urlencode(cleaned_query),
            "",
        )
    )


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen = set()
    deduped_urls = []
    for url in urls:
        if not url or url in seen:
            continue
        seen.add(url)
        deduped_urls.append(url)
    return deduped_urls


def _url_contains_set(value: str) -> bool:
    parsed_url = urlparse(value)
    return any(part.lower() == "sets" for part in parsed_url.path.split("/") if part)


def _sanitize_oembed_iframe_src(html: object) -> str:
    parser = _IframeSrcParser()
    parser.feed(str(html or ""))
    iframe_src = parser.iframe_src.strip()
    if not iframe_src:
        return ""

    parsed_src = urlparse(iframe_src)
    if (
        parsed_src.scheme != "https"
        or _hostname(parsed_src) != "w.soundcloud.com"
        or not parsed_src.path.startswith("/player/")
    ):
        return ""

    return iframe_src


def _safe_oembed_height(value: object) -> int | None:
    try:
        parsed_height = int(value)
    except (TypeError, ValueError):
        return None

    return parsed_height if parsed_height > 0 else None


def _iframe_target_url(iframe_src: str) -> str:
    parsed_iframe_src = urlparse(iframe_src)
    query_params = dict(parse_qsl(parsed_iframe_src.query, keep_blank_values=True))
    return query_params.get("url", "")


def _iframe_uses_soundcloud_api_target(iframe_src: str) -> bool:
    return _hostname(urlparse(_iframe_target_url(iframe_src))) == "api.soundcloud.com"


class _LimitedSoundCloudRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirects = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirects = getattr(req, "redirect_count", 0) + 1
        if redirects > self.max_redirects:
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "Too many redirects while resolving SoundCloud URL.",
                headers,
                fp,
            )

        parsed_new_url = urlparse(newurl)
        if not _is_allowed_http_url(parsed_new_url, ALLOWED_INPUT_HOSTS):
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "SoundCloud URL redirected to an unsupported host.",
                headers,
                fp,
            )

        next_request = super().redirect_request(req, fp, code, msg, headers, newurl)
        if next_request:
            next_request.redirect_count = redirects
        return next_request


def _resolve_redirect_url(url: str) -> str:
    opener = urllib.request.build_opener(_LimitedSoundCloudRedirectHandler)
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "CuevionBundle/1.0 SoundCloudPreview",
        },
    )
    try:
        with opener.open(request, timeout=4) as response:
            resolved_url = response.geturl()
            if _hostname(urlparse(resolved_url)) != "on.soundcloud.com":
                return resolved_url
    except urllib.error.HTTPError as error:
        if error.code not in {403, 405, 501}:
            raise

    fallback_request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Range": "bytes=0-0",
            "User-Agent": "CuevionBundle/1.0 SoundCloudPreview",
        },
    )
    with opener.open(fallback_request, timeout=4) as response:
        return response.geturl()


def _call_soundcloud_oembed(
    url: str,
    maxheight: int | None = None,
) -> tuple[dict | None, str | None]:
    query_params = {
        "format": "json",
        "url": url,
        "auto_play": "false",
        "show_comments": "false",
        "visual": "false",
    }
    if maxheight:
        query_params["maxheight"] = str(maxheight)

    query = urlencode(query_params)
    request = urllib.request.Request(
        f"{SOUNDCLOUD_OEMBED_ENDPOINT}?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0 Safari/537.36 CuevionBundle/1.0"
            ),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except (json.JSONDecodeError, TimeoutError, urllib.error.URLError, ValueError):
        return None, "oembed_failed"

    if not isinstance(payload, dict):
        return None, "oembed_failed"

    iframe_src = _sanitize_oembed_iframe_src(payload.get("html"))
    if not iframe_src:
        return None, "invalid_oembed_iframe"

    return {
        "iframeSrc": iframe_src,
        "height": _safe_oembed_height(payload.get("height")),
        "title": str(payload.get("title") or "").strip(),
    }, None


def _validate_candidate_url(value: object) -> tuple[str | None, str | None, bool]:
    original_url = _normalize_url(value)
    parsed_url = urlparse(original_url)
    if not _is_allowed_http_url(parsed_url, ALLOWED_INPUT_HOSTS):
        return None, "unsupported_host", False

    hostname = _hostname(parsed_url)
    if hostname == "on.soundcloud.com":
        if not parsed_url.path.strip("/"):
            return None, "generic_shortlink", False
        return original_url, None, True

    if not _is_embeddable_soundcloud_permalink(parsed_url):
        return None, "not_embeddable", False

    return original_url, None, False


def _resolve_soundcloud_preview(value: object) -> tuple[dict | None, str | None]:
    accepted_url, reason, needs_redirect_fallback = _validate_candidate_url(value)
    if not accepted_url:
        return None, reason or "not_embeddable"

    if needs_redirect_fallback:
        resolved_url = _resolve_redirect_url(accepted_url)
        resolved_parsed_url = urlparse(resolved_url)
        if not _is_embeddable_soundcloud_permalink(resolved_parsed_url):
            return None, "not_embeddable"

        oembed_candidates = _dedupe_urls(
            [
                resolved_url,
                _strip_soundcloud_tracking_params(resolved_parsed_url),
                accepted_url,
            ]
        )
        last_oembed_reason = None
        for candidate_url in oembed_candidates:
            oembed_payload, oembed_reason = _call_soundcloud_oembed(
                candidate_url,
                maxheight=None if _url_contains_set(candidate_url) else 166,
            )
            if not oembed_payload:
                last_oembed_reason = oembed_reason
                continue

            parsed_candidate_url = urlparse(candidate_url)
            canonical_url = (
                accepted_url
                if _hostname(parsed_candidate_url) == "on.soundcloud.com"
                else _strip_soundcloud_tracking_params(parsed_candidate_url)
            )
            return {
                **oembed_payload,
                "canonicalUrl": canonical_url,
            }, None

        return None, last_oembed_reason or "oembed_failed"

    original_maxheight = None if _url_contains_set(accepted_url) else 166
    oembed_payload, oembed_reason = _call_soundcloud_oembed(
        accepted_url,
        maxheight=original_maxheight,
    )
    if oembed_payload:
        parsed_accepted_url = urlparse(accepted_url)
        canonical_url = (
            accepted_url
            if _hostname(parsed_accepted_url) == "on.soundcloud.com"
            else _canonicalize_soundcloud_url(parsed_accepted_url)
        )
        if (
            canonical_url != accepted_url
            and _iframe_uses_soundcloud_api_target(oembed_payload["iframeSrc"])
        ):
            canonical_oembed_payload, _ = _call_soundcloud_oembed(
                canonical_url,
                maxheight=None if _url_contains_set(canonical_url) else 166,
            )
            if canonical_oembed_payload and not _iframe_uses_soundcloud_api_target(
                canonical_oembed_payload["iframeSrc"]
            ):
                oembed_payload = canonical_oembed_payload

        return {
            **oembed_payload,
            "canonicalUrl": canonical_url,
        }, None

    return None, oembed_reason or "oembed_failed"


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        session_user = _get_authenticated_user(self.headers)
        if not session_user:
            _send_json(self, 401, _build_error("unauthorized", "A valid beta session is required."))
            return

        payload, error = _read_json_body(self)
        if error:
            _send_json(self, 400, error)
            return

        original_url = _normalize_url(payload.get("url"))
        try:
            preview, reason = _resolve_soundcloud_preview(original_url)
        except (TimeoutError, urllib.error.URLError, ValueError):
            _send_json(
                self,
                200,
                {
                    "ok": False,
                    "generatedAt": _generated_at(),
                    "originalUrl": original_url,
                    "reason": "resolve_failed",
                },
            )
            return

        if not preview:
            _send_json(
                self,
                200,
                {
                    "ok": False,
                    "generatedAt": _generated_at(),
                    "originalUrl": original_url,
                    "reason": reason or "not_embeddable",
                },
            )
            return

        _send_json(
            self,
            200,
            {
                "ok": True,
                "generatedAt": _generated_at(),
                "originalUrl": original_url,
                "canonicalUrl": preview["canonicalUrl"],
                "height": preview.get("height"),
                "iframeSrc": preview["iframeSrc"],
                "title": preview.get("title") or None,
            },
        )

    def do_GET(self):
        self.send_response(405)
        self.send_header("Allow", "POST")
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(_build_error("method_not_allowed", "Use POST.")).encode("utf-8"))

    def do_OPTIONS(self):
        _send_json(self, 200, {"ok": True})

    def log_message(self, format, *args):
        return
