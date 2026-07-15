from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import unittest
from collections.abc import Mapping, Sequence
from dataclasses import FrozenInstanceError
from http.client import HTTPMessage
from pathlib import Path
from unittest.mock import patch

from .http_adapter import (
    HTTP_MODE_ENVIRONMENT_NAME,
    HTTP_MODE_OFF,
    HTTP_MODES,
    PUBLIC_JSON_MAXIMUM_DEPTH,
    PublicResponse,
    RouteDisabled,
    empty_success,
    extract_raw_headers,
    invoke_if_http_mode,
    invoke_safely,
    json_failure,
    json_success,
    normalize_boundary_error,
    parse_http_mode,
    parse_http_mode_mapping,
    preflight_content_length,
    read_json_object,
    require_enabled_http_mode,
    require_request_method,
    validate_no_body_request,
    write_public_response,
)
from .http_boundary import BoundaryError


CURRENT_DIR = Path(__file__).resolve().parent
FRONTEND_ROOT = CURRENT_DIR.parents[1]
_UNSET = object()
_CUSTOM_METHOD_CALLS = []
_LIST_ITERATOR_TYPE_FOR_TEST = type(iter([]))


class _StringSubclass(str):
    pass


def _record_custom_method(name):
    _CUSTOM_METHOD_CALLS.append(name)
    raise AssertionError("custom method executed")


class _ExplosiveDict(dict):
    def items(self):
        return _record_custom_method("dict.items")

    def keys(self):
        return _record_custom_method("dict.keys")

    def __iter__(self):
        return _record_custom_method("dict.__iter__")

    def __eq__(self, _other):
        return _record_custom_method("dict.__eq__")


class _ExplosiveList(list):
    def __iter__(self):
        return _record_custom_method("list.__iter__")

    def __eq__(self, _other):
        return _record_custom_method("list.__eq__")


class _ExplosiveString(str):
    def __str__(self):
        return _record_custom_method("str.__str__")

    def __eq__(self, _other):
        return _record_custom_method("str.__eq__")

    def __hash__(self):
        return _record_custom_method("str.__hash__")


class _TrackedStringKey(str):
    explode = False

    def __eq__(self, other):
        if type(self).explode:
            return _record_custom_method("key.__eq__")
        return str.__eq__(self, other)

    def __hash__(self):
        if type(self).explode:
            return _record_custom_method("key.__hash__")
        return str.__hash__(self)


class _ExplosiveInt(int):
    def __int__(self):
        return _record_custom_method("int.__int__")

    def __index__(self):
        return _record_custom_method("int.__index__")

    def __eq__(self, _other):
        return _record_custom_method("int.__eq__")

    def __hash__(self):
        return _record_custom_method("int.__hash__")


class _ExplosiveFloat(float):
    def __float__(self):
        return _record_custom_method("float.__float__")

    def __eq__(self, _other):
        return _record_custom_method("float.__eq__")

    def __hash__(self):
        return _record_custom_method("float.__hash__")


class _ExplosiveBoolLike:
    def __bool__(self):
        return _record_custom_method("bool-like.__bool__")

    def __int__(self):
        return _record_custom_method("bool-like.__int__")

    def __eq__(self, _other):
        return _record_custom_method("bool-like.__eq__")

    def __hash__(self):
        return _record_custom_method("bool-like.__hash__")


class _ExplosiveMapping(Mapping):
    def __getitem__(self, _key):
        return _record_custom_method("mapping.__getitem__")

    def __iter__(self):
        return _record_custom_method("mapping.__iter__")

    def __len__(self):
        return _record_custom_method("mapping.__len__")


class _ExplosiveSequence(Sequence):
    def __getitem__(self, _key):
        return _record_custom_method("sequence.__getitem__")

    def __len__(self):
        return _record_custom_method("sequence.__len__")


class _ExplosiveIterator:
    def __iter__(self):
        return _record_custom_method("iterator.__iter__")

    def __next__(self):
        return _record_custom_method("iterator.__next__")


class _ExplosiveObject:
    def __bool__(self):
        return _record_custom_method("object.__bool__")

    def __str__(self):
        return _record_custom_method("object.__str__")

    def __repr__(self):
        return _record_custom_method("object.__repr__")

    def __iter__(self):
        return _record_custom_method("object.__iter__")

    def __eq__(self, _other):
        return _record_custom_method("object.__eq__")

    def __hash__(self):
        return _record_custom_method("object.__hash__")


class _ExplosiveBytes(bytes):
    def __bool__(self):
        return _record_custom_method("bytes.__bool__")

    def __iter__(self):
        return _record_custom_method("bytes.__iter__")

    def __str__(self):
        return _record_custom_method("bytes.__str__")


class _ExplosiveIterable:
    def __iter__(self):
        raise AssertionError("custom raw header iterable executed")


class _Headers:
    def __init__(self, pairs=(), *, raw_result=_UNSET, error=None):
        self._pairs = list(pairs)
        self._raw_result = raw_result
        self._error = error
        self.raw_calls = 0
        self.items_calls = 0
        self.get_calls = 0

    def raw_items(self):
        self.raw_calls += 1
        if self._error is not None:
            raise self._error
        if self._raw_result is not _UNSET:
            return self._raw_result
        return iter(self._pairs)

    def items(self):
        self.items_calls += 1
        raise AssertionError("items() must not be used")

    def get(self, _name, _default=None):
        self.get_calls += 1
        raise AssertionError("get() must not be used")


class _ReadStream:
    def __init__(self, result=b""):
        self.result = result
        self.calls = []

    def read(self, size=-1):
        self.calls.append(size)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class _ExplosiveReadStream:
    def __init__(self):
        self.calls = []

    def read(self, size=-1):
        self.calls.append(size)
        raise AssertionError("request stream was read")


class _Request:
    def __init__(self, pairs=(), body=b"", *, headers=None, stream=None):
        self.headers = headers if headers is not None else _Headers(pairs)
        self.rfile = stream if stream is not None else _ReadStream(body)


class _Writer:
    def __init__(self):
        self.statuses = []
        self.headers = []
        self.end_count = 0
        self.body_writes = []
        self.wfile = self

    def send_response(self, status):
        self.statuses.append(status)

    def send_header(self, name, value):
        self.headers.append((name, value))

    def end_headers(self):
        self.end_count += 1

    def write(self, body):
        self.body_writes.append(body)


class _FailingWriter(_Writer):
    def __init__(self, phase, *, header_failure_call=1):
        super().__init__()
        self.phase = phase
        self.header_failure_call = header_failure_call
        self.failure = RuntimeError("private writer failure")

    def send_response(self, status):
        super().send_response(status)
        if self.phase == "send_response":
            raise self.failure

    def send_header(self, name, value):
        super().send_header(name, value)
        if (
            self.phase == "send_header"
            and len(self.headers) == self.header_failure_call
        ):
            raise self.failure

    def end_headers(self):
        super().end_headers()
        if self.phase == "end_headers":
            raise self.failure

    def write(self, body):
        super().write(body)
        if self.phase == "write":
            raise self.failure


class AdapterTestCase(unittest.TestCase):
    def assert_boundary_error(self, code, status, function, *args, **kwargs):
        with self.assertRaises(BoundaryError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(raised.exception.status, status)
        return raised.exception

    def assert_public_error(self, response, status, code):
        self.assertIs(type(response), PublicResponse)
        self.assertEqual(response.status, status)
        self.assertEqual(
            response.body,
            f'{{"ok":false,"error":{{"code":"{code}"}}}}'.encode(),
        )


class ImportAndIdentityTests(AdapterTestCase):
    def test_canonical_import_succeeds(self):
        import api.collaboration.http_adapter as adapter

        self.assertEqual(adapter.__name__, "api.collaboration.http_adapter")
        self.assertFalse(any(name.lower() == "handler" for name in vars(adapter)))

    def test_top_level_identity_is_rejected_in_either_order(self):
        for order in ("canonical_first", "top_level_first"):
            with self.subTest(order=order):
                script = textwrap.dedent(
                    f"""
                    import importlib
                    import sys

                    sys.path.insert(0, {str(CURRENT_DIR)!r})
                    if {order!r} == "canonical_first":
                        canonical = importlib.import_module("api.collaboration.http_adapter")
                    try:
                        importlib.import_module("http_adapter")
                    except ImportError:
                        pass
                    else:
                        raise AssertionError("top-level adapter identity accepted")
                    if {order!r} == "top_level_first":
                        canonical = importlib.import_module("api.collaboration.http_adapter")
                    assert canonical.__name__ == "api.collaboration.http_adapter"
                    assert "http_adapter" not in sys.modules
                    """
                )
                environment = dict(os.environ)
                environment.pop("PYTHONPATH", None)
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    cwd=FRONTEND_ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
                )

    def test_source_has_only_the_approved_collaboration_dependency(self):
        source = (CURRENT_DIR / "http_adapter.py").read_text()
        for forbidden in (
            "import application",
            "import authorization",
            "import redis_store",
            "import source_message",
            "import guest_session",
            "import mutations",
            "api.inboxes",
            "sys.path",
            "os.environ",
            "os.getenv",
            "class handler",
        ):
            self.assertNotIn(forbidden, source)


class FeatureModeTests(AdapterTestCase):
    def test_every_recognized_exact_value_is_preserved(self):
        self.assertEqual(
            HTTP_MODES,
            frozenset({"off", "owner_read", "owner_write", "guest", "frontend"}),
        )
        for mode in HTTP_MODES:
            with self.subTest(mode=mode):
                self.assertEqual(parse_http_mode(mode), mode)

    def test_missing_empty_whitespace_case_unknown_and_non_string_fail_closed(self):
        for value in (
            None,
            "",
            " owner_read",
            "owner_read ",
            "OWNER_READ",
            "Owner_Read",
            "admin",
            1,
            True,
            b"guest",
            _StringSubclass("guest"),
        ):
            with self.subTest(value=repr(value)):
                self.assertEqual(parse_http_mode(value), HTTP_MODE_OFF)

    def test_explicit_mapping_parser_never_reads_global_environment(self):
        self.assertEqual(parse_http_mode_mapping({}), HTTP_MODE_OFF)
        self.assertEqual(
            parse_http_mode_mapping({HTTP_MODE_ENVIRONMENT_NAME: "guest"}),
            "guest",
        )
        self.assertEqual(
            parse_http_mode_mapping({HTTP_MODE_ENVIRONMENT_NAME: 7}),
            HTTP_MODE_OFF,
        )
        self.assertEqual(parse_http_mode_mapping(None), HTTP_MODE_OFF)

    def test_mapping_failure_fails_closed(self):
        class BrokenMapping(dict):
            def get(self, *_args, **_kwargs):
                raise RuntimeError("private mapping failure")

        self.assertEqual(parse_http_mode_mapping(BrokenMapping()), HTTP_MODE_OFF)

    def test_allowed_modes_use_exact_membership_without_ordering(self):
        self.assertEqual(
            require_enabled_http_mode(
                "owner_write", allowed_modes={"owner_write", "frontend"}
            ),
            "owner_write",
        )
        for value in ("off", "owner_read", "guest", "owner_write ", 4):
            with self.subTest(value=value):
                with self.assertRaises(RouteDisabled):
                    require_enabled_http_mode(
                        value, allowed_modes={"owner_write", "frontend"}
                    )

    def test_invalid_allowed_mode_configuration_is_a_programmer_error(self):
        for value in (
            (),
            {"off"},
            {"owner_read", "unknown"},
            "owner_read",
            {_StringSubclass("owner_read")},
            None,
        ):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    require_enabled_http_mode("owner_read", allowed_modes=value)

    def test_allowed_mode_elements_are_validated_before_hashing_or_equality(self):
        for value in (_ExplosiveString("owner_read"), _ExplosiveObject()):
            with self.subTest(value_type=type(value).__name__):
                _CUSTOM_METHOD_CALLS.clear()
                with self.assertRaises(ValueError):
                    require_enabled_http_mode(
                        "owner_read",
                        allowed_modes=[value],
                    )
                self.assertEqual(_CUSTOM_METHOD_CALLS, [])

    def test_disabled_invocation_does_not_execute_callback_or_read_stream(self):
        stream = _ExplosiveReadStream()
        request = _Request(stream=stream)
        before_modules = set(sys.modules)
        callback_calls = []

        def callback():
            callback_calls.append(True)
            request.rfile.read(1)
            __import__("api.collaboration.application")
            return json_success({})

        response = invoke_if_http_mode(
            "off",
            allowed_modes={"owner_read"},
            callback=callback,
        )
        self.assert_public_error(response, 404, "not_found")
        self.assertEqual(callback_calls, [])
        self.assertEqual(stream.calls, [])
        self.assertEqual(set(sys.modules), before_modules)

    def test_active_invocation_calls_callback_once(self):
        calls = []

        def callback():
            calls.append(True)
            return json_success({"active": True})

        response = invoke_if_http_mode(
            "owner_read",
            allowed_modes={"owner_read"},
            callback=callback,
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(calls, [True])


class HeaderExtractionTests(AdapterTestCase):
    def test_order_and_duplicates_are_preserved_using_raw_items_only(self):
        pairs = [
            ("X-Trace", "one"),
            ("X-Other", "value"),
            ("x-trace", "two"),
        ]
        headers = _Headers(pairs)
        request = _Request(headers=headers, stream=_ExplosiveReadStream())
        snapshot = extract_raw_headers(request)
        self.assertEqual(snapshot, tuple(pairs))
        self.assertIs(type(snapshot), tuple)
        self.assertEqual(headers.raw_calls, 1)
        self.assertEqual(headers.items_calls, 0)
        self.assertEqual(headers.get_calls, 0)
        self.assertEqual(request.rfile.calls, [])

    def test_raw_items_may_return_standard_iterators_lists_or_tuples(self):
        pairs = [("X-Test", "one")]
        for raw_result in (iter(pairs), list(pairs), tuple(pairs)):
            with self.subTest(result_type=type(raw_result).__name__):
                request = _Request(headers=_Headers(raw_result=raw_result))
                self.assertEqual(extract_raw_headers(request), tuple(pairs))

    def test_real_http_message_raw_items_preserve_duplicate_order(self):
        headers = HTTPMessage()
        headers["X-Trace"] = "one"
        headers["X-Other"] = "value"
        headers["X-Trace"] = "two"
        stream = _ExplosiveReadStream()
        snapshot = extract_raw_headers(
            _Request(headers=headers, stream=stream)
        )
        self.assertEqual(
            snapshot,
            (
                ("X-Trace", "one"),
                ("X-Other", "value"),
                ("X-Trace", "two"),
            ),
        )
        self.assertIs(type(headers.raw_items()), _LIST_ITERATOR_TYPE_FOR_TEST)
        self.assertEqual(stream.calls, [])

    def test_missing_noncallable_and_raising_raw_items_fail_closed(self):
        class Missing:
            pass

        class NonCallable:
            raw_items = []

        requests = (
            _Request(headers=Missing()),
            _Request(headers=NonCallable()),
            _Request(headers=_Headers(error=RuntimeError("private header failure"))),
        )
        for request in requests:
            with self.subTest(headers=type(request.headers).__name__):
                error = self.assert_boundary_error(
                    "invalid_headers", 400, extract_raw_headers, request
                )
                self.assertNotIn("private", str(error))

    def test_unsupported_raw_results_and_malformed_pairs_fail_closed(self):
        raw_results = (
            None,
            "X-Test",
            b"X-Test",
            bytearray(b"X-Test"),
            {"X-Test": "value"},
            _ExplosiveIterable(),
            [("X-Test",)],
            [("X-Test", "value", "extra")],
            ["X-Test: value"],
            [(b"X-Test", "value")],
            [("X-Test", b"value")],
        )
        for raw_result in raw_results:
            with self.subTest(raw_result=repr(raw_result)):
                request = _Request(headers=_Headers(raw_result=raw_result))
                self.assert_boundary_error(
                    "invalid_headers", 400, extract_raw_headers, request
                )

    def test_control_characters_and_invalid_names_are_rejected(self):
        for pair in (
            ("Bad Name", "value"),
            ("Bad:Name", "value"),
            ("Bad\rName", "value"),
            ("X-Test", "line\nbreak"),
            ("X-Test", "nul\x00value"),
            ("X-Test", "format\u202evalue"),
        ):
            with self.subTest(pair=repr(pair)):
                self.assert_boundary_error(
                    "invalid_headers",
                    400,
                    extract_raw_headers,
                    _Request(headers=_Headers([pair])),
                )

    def test_duplicate_and_comma_combined_security_headers_are_rejected(self):
        for pairs in (
            [("Content-Length", "1"), ("content-length", "1")],
            [("Content-Type", "application/json, application/json")],
            [("Origin", "https://one.example,https://two.example")],
            [("Cookie", "a=1,b=2")],
            [("X-Cuevion-CSRF", "one,two")],
        ):
            with self.subTest(pairs=pairs):
                self.assert_boundary_error(
                    "ambiguous_headers",
                    400,
                    extract_raw_headers,
                    _Request(headers=_Headers(pairs)),
                )

    def test_every_transfer_encoding_is_rejected(self):
        for value in ("chunked", "identity", "", "gzip"):
            with self.subTest(value=value):
                self.assert_boundary_error(
                    "invalid_framing",
                    400,
                    extract_raw_headers,
                    _Request(headers=_Headers([("Transfer-Encoding", value)])),
                )


class ContentLengthPreflightTests(AdapterTestCase):
    def test_absent_zero_normal_exact_limit_and_one_over(self):
        self.assertIsNone(
            preflight_content_length([], maximum_bytes=10, required=False)
        )
        self.assertEqual(
            preflight_content_length(
                [("Content-Length", "0")], maximum_bytes=10
            ),
            0,
        )
        self.assertEqual(
            preflight_content_length(
                [("Content-Length", "9")], maximum_bytes=10
            ),
            9,
        )
        self.assertEqual(
            preflight_content_length(
                [("Content-Length", "10")], maximum_bytes=10
            ),
            10,
        )
        self.assert_boundary_error(
            "payload_too_large",
            413,
            preflight_content_length,
            [("Content-Length", "11")],
            maximum_bytes=10,
        )

    def test_absent_required_length_is_invalid_framing(self):
        self.assert_boundary_error(
            "invalid_framing",
            400,
            preflight_content_length,
            [],
            maximum_bytes=10,
        )

    def test_noncanonical_content_lengths_are_rejected(self):
        for value in (
            "",
            "00",
            "01",
            "+1",
            "-1",
            " 1",
            "1 ",
            "1.0",
            "1e0",
            "\N{ARABIC-INDIC DIGIT ONE}",
            "1,1",
        ):
            with self.subTest(value=value):
                expected = "ambiguous_headers" if "," in value else "invalid_framing"
                self.assert_boundary_error(
                    expected,
                    400,
                    preflight_content_length,
                    [("Content-Length", value)],
                    maximum_bytes=10,
                )

    def test_duplicate_content_lengths_are_rejected(self):
        self.assert_boundary_error(
            "ambiguous_headers",
            400,
            preflight_content_length,
            [("Content-Length", "1"), ("content-length", "1")],
            maximum_bytes=10,
        )

    def test_extremely_long_decimal_is_rejected_without_integer_conversion(self):
        value = "9" * 10000
        self.assert_boundary_error(
            "payload_too_large",
            413,
            preflight_content_length,
            [("Content-Length", value)],
            maximum_bytes=1024,
        )

    def test_invalid_maximum_configuration_is_not_treated_as_request_input(self):
        for maximum in (True, False, -1, 1.0, "1", None):
            with self.subTest(maximum=maximum):
                with self.assertRaises(ValueError):
                    preflight_content_length(
                        [("Content-Length", "0")],
                        maximum_bytes=maximum,
                    )
        with self.assertRaises(ValueError):
            preflight_content_length(
                [("Content-Length", "0")], maximum_bytes=1, required=1
            )

    def test_oversized_declared_body_is_rejected_before_stream_read(self):
        stream = _ExplosiveReadStream()
        request = _Request(
            [
                ("Content-Type", "application/json"),
                ("Content-Length", "11"),
            ],
            stream=stream,
        )
        self.assert_boundary_error(
            "payload_too_large",
            413,
            read_json_object,
            request,
            maximum_bytes=10,
            allowed_fields=(),
        )
        self.assertEqual(stream.calls, [])


class ExactBodyReadingTests(AdapterTestCase):
    def request_for(self, body, *, content_type="application/json", declared=None):
        length = str(len(body)) if declared is None and type(body) is bytes else declared
        pairs = [("Content-Type", content_type)]
        if length is not None:
            pairs.append(("Content-Length", length))
        return _Request(pairs, body=body)

    def read(self, request):
        return read_json_object(
            request,
            maximum_bytes=1024,
            allowed_fields={"name", "nested", "enabled"},
            required_fields={"name"},
        )

    def test_exact_body_is_read_once_with_exact_declared_length(self):
        body = b'{"name":"review"}'
        request = self.request_for(body)
        self.assertEqual(self.read(request), {"name": "review"})
        self.assertEqual(request.rfile.calls, [len(body)])

    def test_both_exact_json_content_types_are_accepted(self):
        body = b'{"name":"review"}'
        for content_type in (
            "application/json",
            "application/json; charset=utf-8",
        ):
            with self.subTest(content_type=content_type):
                request = self.request_for(body, content_type=content_type)
                self.assertEqual(self.read(request), {"name": "review"})
                self.assertEqual(request.rfile.calls, [len(body)])

    def test_truncated_and_non_bytes_reads_are_rejected_without_retry(self):
        cases = (
            (_Request(
                [("Content-Type", "application/json"), ("Content-Length", "2")],
                body=b"{",
            ), "invalid_framing"),
            (_Request(
                [("Content-Type", "application/json"), ("Content-Length", "2")],
                stream=_ReadStream("{}"),
            ), "invalid_framing"),
        )
        for request, code in cases:
            with self.subTest(result_type=type(request.rfile.result).__name__):
                self.assert_boundary_error(code, 400, self.read, request)
                self.assertEqual(request.rfile.calls, [2])

    def test_stream_exception_is_not_retried(self):
        marker = "private stream marker"
        request = _Request(
            [("Content-Type", "application/json"), ("Content-Length", "2")],
            stream=_ReadStream(RuntimeError(marker)),
        )
        response = invoke_safely(lambda: json_success(self.read(request)))
        self.assert_public_error(response, 500, "internal_error")
        self.assertNotIn(marker.encode(), response.body)
        self.assertEqual(request.rfile.calls, [2])

    def test_invalid_utf8_and_bom_are_rejected(self):
        for body in (b'{"name":"\xff"}', b"\xef\xbb\xbf{}"):
            with self.subTest(body=body):
                request = self.request_for(body)
                self.assert_boundary_error("invalid_utf8", 400, self.read, request)
                self.assertEqual(request.rfile.calls, [len(body)])

    def test_invalid_json_shapes_and_tokens_are_rejected(self):
        bodies = (
            b"{",
            b'[{"name":"review"}]',
            b'"review"',
            b'{"name":"one","name":"two"}',
            b'{"name":"review","nested":{"x":1}}',
            b'{"name":"review","enabled":NaN}',
            b'{"name":"review","enabled":Infinity}',
            b'{"name":"\\ud800"}',
        )
        for body in bodies:
            with self.subTest(body=body):
                request = self.request_for(body)
                self.assert_boundary_error("invalid_json", 400, self.read, request)
                self.assertEqual(request.rfile.calls, [len(body)])

    def test_missing_required_and_unknown_fields_are_rejected(self):
        for body in (b"{}", b'{"name":"review","unknown":true}'):
            with self.subTest(body=body):
                request = self.request_for(body)
                self.assert_boundary_error(
                    "invalid_json_fields", 400, self.read, request
                )

    def test_unsupported_content_type_is_rejected_before_stream_read(self):
        for content_type in (
            "Application/JSON",
            "application/json ",
            "application/json; charset=UTF-8",
            "text/json",
        ):
            with self.subTest(content_type=content_type):
                stream = _ExplosiveReadStream()
                body = b'{"name":"review"}'
                request = _Request(
                    [
                        ("Content-Type", content_type),
                        ("Content-Length", str(len(body))),
                    ],
                    stream=stream,
                )
                self.assert_boundary_error(
                    "unsupported_content_type", 415, self.read, request
                )
                self.assertEqual(stream.calls, [])

    def test_missing_content_length_is_rejected_before_stream_read(self):
        stream = _ExplosiveReadStream()
        request = _Request([("Content-Type", "application/json")], stream=stream)
        self.assert_boundary_error("invalid_framing", 400, self.read, request)
        self.assertEqual(stream.calls, [])

    def test_schema_configuration_errors_remain_programmer_errors(self):
        body = b'{"name":"review"}'
        request = self.request_for(body)
        with self.assertRaises(ValueError):
            read_json_object(
                request,
                maximum_bytes=1024,
                allowed_fields={"name"},
                required_fields={"missing"},
            )
        self.assertEqual(request.rfile.calls, [len(body)])


class NoBodyRequestTests(AdapterTestCase):
    def test_absent_and_zero_content_length_are_accepted_immutably(self):
        for pairs in ([], [("Content-Length", "0")]):
            with self.subTest(pairs=pairs):
                stream = _ExplosiveReadStream()
                snapshot = validate_no_body_request(
                    _Request(pairs, stream=stream)
                )
                self.assertEqual(snapshot, tuple(pairs))
                self.assertIs(type(snapshot), tuple)
                self.assertEqual(stream.calls, [])

    def test_nonzero_and_malformed_content_length_are_rejected_without_read(self):
        for value in ("1", "999999999999999999999", "01", "+1", " 0", "0,0"):
            with self.subTest(value=value):
                stream = _ExplosiveReadStream()
                request = _Request([("Content-Length", value)], stream=stream)
                with self.assertRaises(BoundaryError):
                    validate_no_body_request(request)
                self.assertEqual(stream.calls, [])

    def test_transfer_encoding_is_rejected_without_read(self):
        stream = _ExplosiveReadStream()
        request = _Request([("Transfer-Encoding", "chunked")], stream=stream)
        self.assert_boundary_error(
            "invalid_framing", 400, validate_no_body_request, request
        )
        self.assertEqual(stream.calls, [])

    def test_claimed_supplied_body_is_rejected_before_header_or_stream_use(self):
        class ExplosiveHeaders:
            def __getattribute__(self, _name):
                raise AssertionError("headers inspected")

        request = _Request(headers=ExplosiveHeaders(), stream=_ExplosiveReadStream())
        for supplied in (b"x", "", bytearray()):
            with self.subTest(supplied_type=type(supplied).__name__):
                self.assert_boundary_error(
                    "invalid_framing",
                    400,
                    validate_no_body_request,
                    request,
                    supplied_body=supplied,
                )

    def test_supplied_body_subclass_and_custom_object_are_rejected_without_use(self):
        class ExplosiveHeaders:
            def __getattribute__(self, _name):
                raise AssertionError("headers inspected")

        request = _Request(headers=ExplosiveHeaders(), stream=_ExplosiveReadStream())
        for supplied in (_ExplosiveBytes(b"private"), _ExplosiveObject()):
            with self.subTest(supplied_type=type(supplied).__name__):
                _CUSTOM_METHOD_CALLS.clear()
                self.assert_boundary_error(
                    "invalid_framing",
                    400,
                    validate_no_body_request,
                    request,
                    supplied_body=supplied,
                )
                self.assertEqual(_CUSTOM_METHOD_CALLS, [])
                self.assertEqual(request.rfile.calls, [])


class MethodTests(AdapterTestCase):
    def test_exact_method_uses_the_existing_boundary(self):
        self.assertEqual(
            require_request_method("POST", expected_method="POST"), "POST"
        )
        self.assert_boundary_error(
            "method_not_allowed",
            405,
            require_request_method,
            "GET",
            expected_method="POST",
        )

    def test_invalid_expected_method_is_a_programmer_error(self):
        for expected in ("post", "POST ", "PO ST", 1, _StringSubclass("POST")):
            with self.subTest(expected=expected):
                with self.assertRaises(ValueError):
                    require_request_method("POST", expected_method=expected)


class ResponseTests(AdapterTestCase):
    def assert_common_json_headers(self, response):
        self.assertEqual(
            response.headers,
            (
                ("Content-Type", "application/json; charset=utf-8"),
                ("Cache-Control", "no-store"),
                ("X-Content-Type-Options", "nosniff"),
                ("Content-Length", str(len(response.body))),
            ),
        )
        self.assertFalse(
            any(name.lower().startswith("access-control-") for name, _ in response.headers)
        )

    def test_exact_compact_deterministic_success_json_and_unicode(self):
        response = json_success(
            {"z": "Gr\N{LATIN SMALL LETTER U WITH DIAERESIS}\N{LATIN SMALL LETTER SHARP S}e", "a": True},
            status=201,
        )
        self.assertEqual(response.status, 201)
        self.assertEqual(
            response.body,
            '{"ok":true,"data":{"a":true,"z":"Grüße"}}'.encode(),
        )
        self.assert_common_json_headers(response)

    def test_nested_exact_json_tree_is_copied_into_fresh_exact_containers(self):
        nested_dict = {
            "text": "value",
            "enabled": True,
            "count": 3,
            "ratio": 1.25,
            "none": None,
        }
        nested_list = [nested_dict]
        original = {"nested": nested_list}
        captured = []
        real_dumps = json.dumps

        def capture_dumps(value, **kwargs):
            captured.append(value)
            return real_dumps(value, **kwargs)

        with patch(
            "api.collaboration.http_adapter.json.dumps",
            side_effect=capture_dumps,
        ):
            response = json_success(original)

        self.assertEqual(response.status, 200)
        self.assertEqual(len(captured), 1)
        copied = captured[0]
        self.assertIs(type(copied), dict)
        self.assertIs(type(copied["nested"]), list)
        self.assertIs(type(copied["nested"][0]), dict)
        self.assertIsNot(copied, original)
        self.assertIsNot(copied["nested"], nested_list)
        self.assertIsNot(copied["nested"][0], nested_dict)
        self.assertEqual(copied, original)

    def test_nested_unsupported_values_are_rejected_without_custom_methods(self):
        dict_subclass = _ExplosiveDict()
        dict.__setitem__(dict_subclass, "private", "value")
        list_subclass = _ExplosiveList()
        list.append(list_subclass, "private")
        cases = (
            ("dict subclass", dict_subclass),
            ("list subclass", list_subclass),
            ("str subclass", _ExplosiveString("private")),
            ("int subclass", _ExplosiveInt(7)),
            ("float subclass", _ExplosiveFloat(1.5)),
            ("bool-like object", _ExplosiveBoolLike()),
            ("tuple", ("private",)),
            ("mapping", _ExplosiveMapping()),
            ("sequence", _ExplosiveSequence()),
            ("iterator", _ExplosiveIterator()),
            ("bytes", b"private"),
            ("bytes subclass", _ExplosiveBytes(b"private")),
            ("bytearray", bytearray(b"private")),
            ("set", {"private"}),
            ("frozenset", frozenset({"private"})),
        )
        for label, value in cases:
            with self.subTest(case=label):
                _CUSTOM_METHOD_CALLS.clear()
                with self.assertRaises(ValueError) as raised:
                    json_success({"value": value})
                self.assertEqual(_CUSTOM_METHOD_CALLS, [])
                self.assertNotIn("private", str(raised.exception))

    def test_bool_is_exact_and_python_does_not_permit_bool_subclasses(self):
        self.assertEqual(json_success({"value": True}).status, 200)
        with self.assertRaises(TypeError):
            type("_ImpossibleBoolSubclass", (bool,), {})
        _CUSTOM_METHOD_CALLS.clear()
        with self.assertRaises(ValueError):
            json_success({"value": _ExplosiveBoolLike()})
        self.assertEqual(_CUSTOM_METHOD_CALLS, [])

    def test_exact_finite_float_is_accepted_and_nonfinite_values_are_rejected(self):
        self.assertEqual(json_success({"value": 1.25}).status, 200)
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(nonfinite=value):
                with self.assertRaises(ValueError):
                    json_success({"value": value})

    def test_dict_key_subclass_is_rejected_without_hash_or_equality(self):
        key = _TrackedStringKey("private-key")
        payload = {}
        dict.__setitem__(payload, key, "value")
        _CUSTOM_METHOD_CALLS.clear()
        _TrackedStringKey.explode = True
        try:
            with self.assertRaises(ValueError) as raised:
                json_success(payload)
            self.assertEqual(_CUSTOM_METHOD_CALLS, [])
            self.assertNotIn("private-key", str(raised.exception))
        finally:
            _TrackedStringKey.explode = False

    def test_direct_and_indirect_json_container_cycles_are_rejected(self):
        direct = {}
        direct["self"] = direct
        indirect = {}
        child = [indirect]
        indirect["child"] = child
        for label, value in (("direct", direct), ("indirect", indirect)):
            with self.subTest(cycle=label):
                with self.assertRaises(ValueError):
                    json_success(value)

    def test_json_tree_depth_limit_accepts_maximum_and_rejects_next_level(self):
        def payload_with_list_depth(depth):
            value = "leaf"
            for _ in range(depth):
                value = [value]
            return {"value": value}

        accepted = payload_with_list_depth(PUBLIC_JSON_MAXIMUM_DEPTH)
        self.assertEqual(json_success(accepted).status, 200)
        with self.assertRaises(ValueError):
            json_success(
                payload_with_list_depth(PUBLIC_JSON_MAXIMUM_DEPTH + 1)
            )

    def test_malformed_callback_dto_becomes_generic_internal_error(self):
        marker = "private-dto-marker"
        malformed = _ExplosiveList()
        list.append(malformed, marker)
        _CUSTOM_METHOD_CALLS.clear()

        response = invoke_safely(
            lambda: json_success({"value": malformed})
        )

        self.assert_public_error(response, 500, "internal_error")
        self.assertNotIn(marker.encode(), response.body)
        self.assertEqual(_CUSTOM_METHOD_CALLS, [])

    def test_json_success_status_allowlist_is_exact(self):
        for status in (200, 201):
            with self.subTest(accepted=status):
                self.assertEqual(json_success({}, status=status).status, status)
        for status in (199, 202, 204, 205, 206, 299, 400):
            with self.subTest(rejected=status):
                with self.assertRaises(ValueError):
                    json_success({}, status=status)

    def test_exact_compact_error_json(self):
        response = json_failure("invalid_request", status=400)
        self.assert_public_error(response, 400, "invalid_request")
        self.assert_common_json_headers(response)

    def test_response_is_immutable(self):
        response = json_success({})
        with self.assertRaises(FrozenInstanceError):
            response.status = 201

    def test_empty_204_has_no_content_type_or_body(self):
        response = empty_success()
        self.assertEqual(response.status, 204)
        self.assertEqual(response.body, b"")
        self.assertEqual(
            response.headers,
            (
                ("Cache-Control", "no-store"),
                ("X-Content-Type-Options", "nosniff"),
                ("Content-Length", "0"),
            ),
        )

    def test_405_has_exactly_one_trusted_allow_header(self):
        response = normalize_boundary_error(
            BoundaryError("method_not_allowed", 405), allow_method="POST"
        )
        self.assert_public_error(response, 405, "method_not_allowed")
        self.assertEqual(response.headers[-1], ("Allow", "POST"))
        self.assertEqual(
            sum(name.lower() == "allow" for name, _ in response.headers), 1
        )

    def test_allow_is_not_emitted_for_unrelated_errors(self):
        response = normalize_boundary_error(
            BoundaryError("invalid_json", 400), allow_method="POST"
        )
        self.assert_public_error(response, 400, "invalid_request")
        self.assertFalse(any(name.lower() == "allow" for name, _ in response.headers))

    def test_malformed_allow_is_rejected_before_callback_or_writing(self):
        for allow in (
            "",
            "post",
            "POST, GET",
            "POST ",
            "PO ST",
            1,
            _StringSubclass("POST"),
        ):
            with self.subTest(allow=repr(allow)):
                calls = []
                with self.assertRaises(ValueError):
                    invoke_safely(
                        lambda: calls.append(True) or json_success({}),
                        allow_method=allow,
                    )
                self.assertEqual(calls, [])

    def test_method_normalization_requires_allow(self):
        with self.assertRaises(ValueError):
            normalize_boundary_error(BoundaryError("method_not_allowed", 405))

    def test_invalid_public_error_codes_and_statuses_are_rejected(self):
        for code in (
            "",
            "Invalid_Request",
            "invalid-request",
            "invalid request",
            1,
            _StringSubclass("invalid_request"),
        ):
            with self.subTest(code=repr(code)):
                with self.assertRaises(ValueError):
                    json_failure(code, status=400)
        for status in (True, 399, 600, "400"):
            with self.subTest(status=status):
                with self.assertRaises(ValueError):
                    json_failure("invalid_request", status=status)

    def test_invalid_success_data_and_nonfinite_numbers_fail_safely(self):
        for data in ([], "private", {"value": float("nan")}, {"value": float("inf")}):
            with self.subTest(data_type=type(data).__name__):
                with self.assertRaises(ValueError):
                    json_success(data)

        response = invoke_safely(
            lambda: json_success({"private": object()})
        )
        self.assert_public_error(response, 500, "internal_error")
        self.assertNotIn(b"private", response.body)

    def test_writer_writes_headers_once_and_body_at_most_once(self):
        writer = _Writer()
        response = json_success({"ok": "value"})
        write_public_response(writer, response)
        self.assertEqual(writer.statuses, [200])
        self.assertEqual(writer.headers, list(response.headers))
        self.assertEqual(writer.end_count, 1)
        self.assertEqual(writer.body_writes, [response.body])

        empty_writer = _Writer()
        response = empty_success()
        write_public_response(empty_writer, response)
        self.assertEqual(empty_writer.statuses, [204])
        self.assertEqual(empty_writer.headers, list(response.headers))
        self.assertEqual(empty_writer.end_count, 1)
        self.assertEqual(empty_writer.body_writes, [])

    def test_writer_failures_propagate_without_retry_or_second_response(self):
        response = json_success({"ok": "value"})
        cases = (
            ("send_response", 1, 0, 0, 0),
            ("send_header_first", 1, 1, 0, 0),
            ("send_header_later", 1, 3, 0, 0),
            ("end_headers", 1, len(response.headers), 1, 0),
            ("write", 1, len(response.headers), 1, 1),
        )
        for phase, statuses, headers, end_count, body_writes in cases:
            with self.subTest(phase=phase):
                writer_phase = (
                    "send_header" if phase.startswith("send_header") else phase
                )
                header_failure_call = 3 if phase == "send_header_later" else 1
                writer = _FailingWriter(
                    writer_phase,
                    header_failure_call=header_failure_call,
                )
                with self.assertRaises(RuntimeError) as raised:
                    write_public_response(writer, response)
                self.assertIs(raised.exception, writer.failure)
                self.assertEqual(writer.statuses, [response.status] * statuses)
                self.assertEqual(len(writer.headers), headers)
                self.assertEqual(writer.end_count, end_count)
                self.assertEqual(len(writer.body_writes), body_writes)
                if body_writes:
                    self.assertEqual(writer.body_writes, [response.body])

    def test_writer_rejects_forged_responses_before_partial_write(self):
        forged = (
            PublicResponse(200, (), b"private"),
            PublicResponse(
                200,
                (
                    ("Content-Type", "application/json; charset=utf-8"),
                    ("Cache-Control", "public"),
                    ("X-Content-Type-Options", "nosniff"),
                    ("Content-Length", "7"),
                ),
                b"private",
            ),
            PublicResponse(
                204,
                (("Content-Length", "0"), ("Access-Control-Allow-Origin", "*")),
                b"",
            ),
        )
        for response in forged:
            with self.subTest(response=response):
                writer = _Writer()
                with self.assertRaises(ValueError):
                    write_public_response(writer, response)
                self.assertEqual(writer.statuses, [])
                self.assertEqual(writer.headers, [])
                self.assertEqual(writer.end_count, 0)
                self.assertEqual(writer.body_writes, [])

    def test_writer_interface_is_validated_before_status_write(self):
        class MissingWriter:
            pass

        with self.assertRaises(ValueError):
            write_public_response(MissingWriter(), json_success({}))


class ExceptionNormalizationTests(AdapterTestCase):
    def test_boundary_errors_map_to_approved_public_families(self):
        cases = (
            (BoundaryError("invalid_headers", 400), None, 400, "invalid_request"),
            (BoundaryError("ambiguous_headers", 400), None, 400, "invalid_request"),
            (BoundaryError("invalid_framing", 400), None, 400, "invalid_request"),
            (BoundaryError("missing_header", 400), None, 400, "invalid_request"),
            (BoundaryError("invalid_utf8", 400), None, 400, "invalid_request"),
            (BoundaryError("invalid_json", 400), None, 400, "invalid_request"),
            (BoundaryError("invalid_json_fields", 400), None, 400, "invalid_request"),
            (BoundaryError("invalid_value", 400), None, 400, "invalid_request"),
            (BoundaryError("payload_too_large", 413), None, 413, "payload_too_large"),
            (
                BoundaryError("unsupported_content_type", 415),
                None,
                415,
                "unsupported_media_type",
            ),
            (
                BoundaryError("method_not_allowed", 405),
                "GET",
                405,
                "method_not_allowed",
            ),
        )
        for error, allow, status, code in cases:
            with self.subTest(pair=(error.code, error.status)):
                response = normalize_boundary_error(error, allow_method=allow)
                self.assert_public_error(response, status, code)

    def test_noncanonical_exact_boundary_pairs_become_internal_error(self):
        for code, status in (
            ("invalid_headers", 401),
            ("method_not_allowed", 400),
            ("payload_too_large", 400),
            ("unsupported_content_type", 400),
            ("private_unknown", 400),
            ("private_unknown", 599),
        ):
            with self.subTest(pair=(code, status)):
                response = normalize_boundary_error(
                    BoundaryError(code, status),
                    allow_method="GET",
                )
                self.assert_public_error(response, 500, "internal_error")
                self.assertNotIn(code.encode(), response.body)

    def test_malformed_boundary_fields_do_not_execute_custom_behavior(self):
        cases = (
            ("code subclass", _ExplosiveString("invalid_headers"), 400),
            ("status subclass", "invalid_headers", _ExplosiveInt(400)),
            ("bool status", "invalid_headers", True),
            ("custom code", _ExplosiveObject(), 400),
            ("custom status", "invalid_headers", _ExplosiveObject()),
        )
        for label, code, status in cases:
            with self.subTest(case=label):
                error = BoundaryError("invalid_headers", 400)
                error.code = code
                error.status = status
                _CUSTOM_METHOD_CALLS.clear()
                response = normalize_boundary_error(error, allow_method="GET")
                self.assert_public_error(response, 500, "internal_error")
                self.assertEqual(_CUSTOM_METHOD_CALLS, [])

    def test_every_malformed_boundary_error_is_contained_by_invoke_safely(self):
        class BoundaryErrorSubclass(BoundaryError):
            pass

        malformed = []
        for code, status in (
            ("invalid_headers", 401),
            ("private_unknown", 400),
            (_ExplosiveString("invalid_headers"), 400),
            ("invalid_headers", _ExplosiveInt(400)),
            ("invalid_headers", True),
            (_ExplosiveObject(), 400),
            ("invalid_headers", _ExplosiveObject()),
        ):
            error = BoundaryError("invalid_headers", 400)
            error.code = code
            error.status = status
            malformed.append(error)
        malformed.append(BoundaryErrorSubclass("invalid_headers", 400))

        for error in malformed:
            with self.subTest(error_type=type(error).__name__):
                _CUSTOM_METHOD_CALLS.clear()
                response = invoke_safely(
                    lambda error=error: (_ for _ in ()).throw(error),
                    allow_method="GET",
                )
                self.assert_public_error(response, 500, "internal_error")
                self.assertEqual(_CUSTOM_METHOD_CALLS, [])

    def test_boundary_args_cause_context_and_traceback_never_serialize(self):
        marker = "private-boundary-marker"
        error = BoundaryError("private_unknown", 400)
        error.args = (marker,)
        error.__cause__ = RuntimeError(marker)
        error.__context__ = ValueError(marker)

        response = invoke_safely(
            lambda: (_ for _ in ()).throw(error)
        )

        self.assert_public_error(response, 500, "internal_error")
        self.assertNotIn(marker.encode(), response.body)

    def test_unexpected_exception_becomes_internal_error_without_retry_or_leak(self):
        marker = "private storage/provider marker"
        calls = []

        def callback():
            calls.append(True)
            raise RuntimeError(marker)

        response = invoke_safely(callback)
        self.assert_public_error(response, 500, "internal_error")
        self.assertEqual(calls, [True])
        self.assertNotIn(marker.encode(), response.body)

    def test_expected_boundary_error_is_normalized(self):
        response = invoke_safely(
            lambda: (_ for _ in ()).throw(BoundaryError("invalid_json", 400))
        )
        self.assert_public_error(response, 400, "invalid_request")

    def test_base_exception_subclasses_propagate(self):
        for exception in (KeyboardInterrupt(), SystemExit(7)):
            with self.subTest(exception=type(exception).__name__):
                with self.assertRaises(type(exception)):
                    invoke_safely(lambda exception=exception: (_ for _ in ()).throw(exception))

    def test_invalid_wrapper_configuration_is_not_normalized(self):
        with self.assertRaises(ValueError):
            invoke_safely(None)
        with self.assertRaises(ValueError):
            normalize_boundary_error(RuntimeError("private"))

    def test_route_disabled_is_generic_not_found(self):
        response = invoke_safely(
            lambda: (_ for _ in ()).throw(RouteDisabled())
        )
        self.assert_public_error(response, 404, "not_found")


if __name__ == "__main__":
    unittest.main()
