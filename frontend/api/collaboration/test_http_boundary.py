from __future__ import annotations

import re
import unittest
from collections.abc import Mapping, Sequence

from .http_boundary import (
    BoundaryError,
    decode_strict_utf8,
    get_security_header,
    parse_json_object,
    require_ascii_identifier,
    require_bounded_body,
    require_bounded_utf8_string,
    require_exact_empty_object,
    require_exact_string,
    require_json_content_type,
    require_method,
    validate_raw_headers,
    validate_security_headers,
)


class BoundaryTestCase(unittest.TestCase):
    def assert_boundary_error(
        self,
        code: str,
        status: int,
        function,
        *args,
        **kwargs,
    ) -> BoundaryError:
        with self.assertRaises(BoundaryError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(raised.exception.status, status)
        self.assertEqual(str(raised.exception), code)
        self.assertIsNone(raised.exception.__context__)
        self.assertIsNone(raised.exception.__cause__)
        return raised.exception

    def assert_marker_not_retained(
        self,
        error: BoundaryError,
        marker: str,
    ) -> None:
        for exposed in (
            str(error),
            repr(error),
            repr(error.args),
            repr(error.code),
            repr(error.status),
        ):
            self.assertNotIn(marker, exposed)
        self.assertEqual(vars(error), {})


class _ExplosiveSequence(Sequence):
    def __len__(self):
        raise AssertionError("custom length executed")

    def __getitem__(self, _index):
        raise AssertionError("custom indexing executed")

    def __iter__(self):
        raise AssertionError("custom iteration executed")


class _ExplosiveIterable:
    def __iter__(self):
        raise AssertionError("custom iteration executed")


class _MappingSequenceHybrid(Mapping, Sequence):
    def __len__(self):
        raise AssertionError("hybrid length executed")

    def __getitem__(self, _key):
        raise AssertionError("hybrid indexing executed")

    def __iter__(self):
        raise AssertionError("hybrid iteration executed")


class _ListSubclass(list):
    pass


class _TupleSubclass(tuple):
    pass


class _StringSubclass(str):
    pass


class RawHeaderTests(BoundaryTestCase):
    def test_valid_ordered_pairs_are_preserved_without_normalization(self):
        headers = [
            ("x-Trace", "first"),
            ("Origin", "https://app.cuevion.com"),
            ("x-Trace", "second"),
        ]
        validated = validate_raw_headers(headers)
        self.assertEqual(validated, tuple(headers))
        self.assertIsInstance(validated, tuple)
        self.assertEqual(
            get_security_header(validated, "origin"),
            "https://app.cuevion.com",
        )
        self.assertEqual(validated, tuple(headers))

    def test_exact_builtin_header_and_pair_containers_are_accepted(self):
        list_pair = ["X-Test", "one"]
        tuple_pair = ("X-Test", "one")
        list_headers = [list_pair, tuple_pair]
        tuple_headers = (list_pair, tuple_pair)
        expected = (("X-Test", "one"), ("X-Test", "one"))
        self.assertEqual(validate_raw_headers(list_headers), expected)
        self.assertEqual(validate_raw_headers(tuple_headers), expected)

    def test_mapping_is_not_an_equivalent_header_input(self):
        self.assert_boundary_error(
            "invalid_headers",
            400,
            validate_raw_headers,
            {"Origin": "https://app.cuevion.com"},
        )

    def test_custom_and_subclassed_header_containers_are_rejected_without_use(self):
        valid_pair = ("Origin", "https://app.cuevion.com")
        rejected = (
            ("mapping-sequence hybrid", _MappingSequenceHybrid()),
            ("custom sequence", _ExplosiveSequence()),
            ("custom iterable", _ExplosiveIterable()),
            ("generator", (pair for pair in (valid_pair,))),
            ("list subclass", _ListSubclass([valid_pair])),
            ("tuple subclass", _TupleSubclass((valid_pair,))),
            ("ordinary mapping", {"Origin": "https://app.cuevion.com"}),
            ("string", "Origin"),
            ("bytes", b"Origin"),
            ("bytearray", bytearray(b"Origin")),
        )
        for label, headers in rejected:
            with self.subTest(label=label):
                self.assert_boundary_error(
                    "invalid_headers", 400, validate_raw_headers, headers
                )

    def test_custom_and_subclassed_pair_containers_are_rejected_without_use(self):
        rejected_pairs = (
            ("list pair subclass", _ListSubclass(["Origin", "value"])),
            ("tuple pair subclass", _TupleSubclass(("Origin", "value"))),
            ("custom pair sequence", _ExplosiveSequence()),
            ("mapping-sequence pair", _MappingSequenceHybrid()),
        )
        for label, pair in rejected_pairs:
            with self.subTest(label=label):
                self.assert_boundary_error(
                    "invalid_headers", 400, validate_raw_headers, [pair]
                )

    def test_duplicate_security_headers_are_rejected_case_insensitively(self):
        for name, value in (
            ("Origin", "https://app.cuevion.com"),
            ("Content-Type", "application/json"),
            ("Cookie", "session=value"),
            ("X-Cuevion-CSRF", "csrf-value"),
            ("Content-Length", "0"),
        ):
            with self.subTest(name=name):
                self.assert_boundary_error(
                    "ambiguous_headers",
                    400,
                    validate_security_headers,
                    [(name, value), (name.lower(), value)],
                )

    def test_comma_combined_security_values_are_rejected(self):
        for name, value in (
            ("Origin", "https://one.example, https://two.example"),
            ("Content-Type", "application/json, application/json"),
            ("Cookie", "a=1, b=2"),
            ("X-Cuevion-CSRF", "one,two"),
            ("Content-Length", "1,1"),
            ("Transfer-Encoding", "gzip, chunked"),
        ):
            with self.subTest(name=name):
                self.assert_boundary_error(
                    "ambiguous_headers",
                    400,
                    validate_security_headers,
                    [(name, value)],
                )

    def test_malformed_pairs_are_rejected(self):
        for headers in (
            [("Origin",)],
            [("Origin", "value", "extra")],
            ["Origin: value"],
            [("Origin", b"value")],
            [(b"Origin", "value")],
            [(_StringSubclass("Origin"), "value")],
            [("Origin", _StringSubclass("value"))],
        ):
            with self.subTest(headers=headers):
                self.assert_boundary_error(
                    "invalid_headers", 400, validate_raw_headers, headers
                )

    def test_invalid_header_names_are_rejected(self):
        for name in ("", "Bad Name", "Bad:Name", "\N{LATIN SMALL LETTER E WITH ACUTE}"):
            with self.subTest(name=name):
                self.assert_boundary_error(
                    "invalid_headers",
                    400,
                    validate_raw_headers,
                    [(name, "value")],
                )

    def test_header_control_injection_is_rejected(self):
        for headers in (
            [("Bad\rName", "value")],
            [("Name", "line\rbreak")],
            [("Name", "line\nbreak")],
            [("Name", "nul\x00byte")],
            [("Name", "control\x85byte")],
        ):
            with self.subTest(headers=headers):
                self.assert_boundary_error(
                    "invalid_headers", 400, validate_raw_headers, headers
                )

    def test_unicode_format_controls_and_surrogates_are_rejected(self):
        for value in (
            "right-to-left\u202eoverride",
            "zero-width\u200bspace",
            "high-surrogate\ud800",
            "low-surrogate\udfff",
        ):
            with self.subTest(value=ascii(value)):
                self.assert_boundary_error(
                    "invalid_headers",
                    400,
                    validate_raw_headers,
                    [("X-Test", value)],
                )

    def test_visible_non_ascii_header_value_remains_unchanged(self):
        value = (
            "Gr\N{LATIN SMALL LETTER U WITH DIAERESIS}"
            "\N{LATIN SMALL LETTER SHARP S}e"
        )
        headers = [("X-Test", value)]
        self.assertEqual(validate_raw_headers(headers), tuple(headers))


class MethodTests(BoundaryTestCase):
    def test_exact_uppercase_method_is_returned(self):
        self.assertEqual(require_method("POST", "POST"), "POST")

    def test_wrong_or_non_exact_methods_are_rejected(self):
        for method in (
            "GET",
            "post",
            "Post",
            " POST",
            "POST ",
            "PO ST",
            "P\N{LATIN CAPITAL LETTER O WITH STROKE}ST",
            "POST\r",
            1,
        ):
            with self.subTest(method=method):
                self.assert_boundary_error(
                    "method_not_allowed", 405, require_method, method, "POST"
                )


class FramingTests(BoundaryTestCase):
    @staticmethod
    def length_headers(value: str) -> list[tuple[str, str]]:
        return [("Content-Length", value)]

    def test_valid_canonical_content_length_is_accepted(self):
        body = b'{"x":"y"}'
        self.assertIs(
            require_bounded_body(
                self.length_headers(str(len(body))),
                body,
                maximum_bytes=len(body),
            ),
            body,
        )
        self.assertEqual(
            require_bounded_body(self.length_headers("0"), b"", maximum_bytes=0),
            b"",
        )

    def test_missing_required_content_length_is_rejected(self):
        self.assert_boundary_error(
            "invalid_framing",
            400,
            require_bounded_body,
            [],
            b"{}",
            maximum_bytes=10,
        )

    def test_noncanonical_content_lengths_are_rejected(self):
        for value in (
            "00",
            "01",
            "+1",
            "-1",
            " 1",
            "1 ",
            "1.0",
            "1e0",
            "\N{ARABIC-INDIC DIGIT ONE}",
            "",
        ):
            with self.subTest(value=value):
                self.assert_boundary_error(
                    "invalid_framing",
                    400,
                    require_bounded_body,
                    self.length_headers(value),
                    b"x",
                    maximum_bytes=10,
                )

    def test_duplicate_content_length_is_never_selected(self):
        self.assert_boundary_error(
            "ambiguous_headers",
            400,
            require_bounded_body,
            [("Content-Length", "1"), ("content-length", "1")],
            b"x",
            maximum_bytes=10,
        )

    def test_body_must_exactly_match_declared_length(self):
        for declared, body in (("2", b"x"), ("1", b"xy")):
            with self.subTest(declared=declared, actual=len(body)):
                self.assert_boundary_error(
                    "invalid_framing",
                    400,
                    require_bounded_body,
                    self.length_headers(declared),
                    body,
                    maximum_bytes=10,
                )

    def test_body_at_limit_is_allowed_and_over_limit_is_distinct(self):
        body = b"1234"
        self.assertEqual(
            require_bounded_body(
                self.length_headers("4"), body, maximum_bytes=4
            ),
            body,
        )
        self.assert_boundary_error(
            "payload_too_large",
            413,
            require_bounded_body,
            self.length_headers("4"),
            body,
            maximum_bytes=3,
        )
        self.assert_boundary_error(
            "payload_too_large",
            413,
            require_bounded_body,
            [],
            body,
            maximum_bytes=3,
        )
        self.assert_boundary_error(
            "payload_too_large",
            413,
            require_bounded_body,
            self.length_headers("999999999999999999999999999999"),
            b"",
            maximum_bytes=10,
        )

    def test_every_transfer_encoding_is_rejected(self):
        for value in ("chunked", "identity", "", "gzip"):
            with self.subTest(value=value):
                self.assert_boundary_error(
                    "invalid_framing",
                    400,
                    require_bounded_body,
                    [("Transfer-Encoding", value)],
                    b"",
                    maximum_bytes=10,
                )

    def test_transfer_encoding_with_content_length_is_rejected(self):
        self.assert_boundary_error(
            "invalid_framing",
            400,
            require_bounded_body,
            [("Content-Length", "0"), ("Transfer-Encoding", "chunked")],
            b"",
            maximum_bytes=10,
        )


class ContentTypeTests(BoundaryTestCase):
    def test_only_the_two_exact_content_types_are_accepted(self):
        for value in (
            "application/json",
            "application/json; charset=utf-8",
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    require_json_content_type([("Content-Type", value)]), value
                )

    def test_missing_or_alternate_content_types_are_rejected(self):
        for headers in (
            [],
            [("Content-Type", "Application/JSON")],
            [("Content-Type", "application/json ")],
            [("Content-Type", "application/json; charset=utf-8; version=1")],
            [("Content-Type", "application/json; charset=UTF-8")],
            [("Content-Type", "application/json; charset=iso-8859-1")],
        ):
            with self.subTest(headers=headers):
                self.assert_boundary_error(
                    "unsupported_content_type",
                    415,
                    require_json_content_type,
                    headers,
                )

    def test_duplicate_or_combined_content_type_is_ambiguous(self):
        for headers in (
            [
                ("Content-Type", "application/json"),
                ("content-type", "application/json"),
            ],
            [("Content-Type", "application/json, application/json")],
        ):
            with self.subTest(headers=headers):
                self.assert_boundary_error(
                    "ambiguous_headers",
                    400,
                    require_json_content_type,
                    headers,
                )


class Utf8Tests(BoundaryTestCase):
    def test_valid_utf8_is_decoded_strictly(self):
        value = (
            "Gr\N{LATIN SMALL LETTER U WITH DIAERESIS}"
            "\N{LATIN SMALL LETTER SHARP S}e"
        )
        self.assertEqual(decode_strict_utf8(value.encode()), value)

    def test_invalid_utf8_is_rejected_without_replacement(self):
        marker = "private-utf8-marker"
        error = self.assert_boundary_error(
            "invalid_utf8",
            400,
            decode_strict_utf8,
            marker.encode("ascii") + b"-\xff-token",
        )
        self.assert_marker_not_retained(error, marker)
        self.assertNotIn("\N{REPLACEMENT CHARACTER}", str(error))

    def test_utf8_bom_is_explicitly_rejected(self):
        self.assert_boundary_error(
            "invalid_utf8", 400, decode_strict_utf8, b"\xef\xbb\xbf{}"
        )


class JsonObjectTests(BoundaryTestCase):
    def parse(self, text: str, **kwargs):
        return parse_json_object(
            text,
            allowed_fields=kwargs.pop(
                "allowed_fields", {"name", "nested", "items", "enabled"}
            ),
            required_fields=kwargs.pop("required_fields", ()),
            **kwargs,
        )

    def test_valid_empty_and_allowlisted_objects_are_accepted(self):
        self.assertEqual(self.parse("{}"), {})
        self.assertEqual(
            self.parse('{"name":"review","nested":{"ok":true}}'),
            {"name": "review", "nested": {"ok": True}},
        )

    def test_duplicate_keys_at_every_level_are_rejected(self):
        for text in (
            '{"name":"one","name":"two"}',
            '{"nested":{"name":"one","name":"two"}}',
            r'{"name":"one","na\u006de":"two"}',
        ):
            with self.subTest(text=text):
                self.assert_boundary_error("invalid_json", 400, self.parse, text)

    def test_non_object_roots_are_rejected(self):
        for text in ('[]', '"value"', "1", "true", "null"):
            with self.subTest(text=text):
                self.assert_boundary_error("invalid_json", 400, self.parse, text)

    def test_trailing_non_whitespace_json_is_rejected(self):
        self.assert_boundary_error("invalid_json", 400, self.parse, "{} {}")
        self.assertEqual(self.parse("{} \r\n\t"), {})

    def test_nonstandard_numeric_constants_are_rejected(self):
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                self.assert_boundary_error(
                    "invalid_json",
                    400,
                    self.parse,
                    '{"name":' + constant + "}",
                    reject_numbers=False,
                )

    def test_unknown_and_missing_fields_are_rejected(self):
        self.assert_boundary_error(
            "invalid_json_fields",
            400,
            self.parse,
            '{"unknown":"value"}',
        )
        self.assert_boundary_error(
            "invalid_json_fields",
            400,
            self.parse,
            "{}",
            allowed_fields={"name"},
            required_fields={"name"},
        )

    def test_exact_empty_object_helper_rejects_every_other_value(self):
        parsed = self.parse("{}")
        self.assertIs(require_exact_empty_object(parsed), parsed)
        for value in ({"name": "value"}, [], None, False):
            with self.subTest(value=value):
                self.assert_boundary_error(
                    "invalid_value", 400, require_exact_empty_object, value
                )

    def test_non_string_schema_field_names_are_configuration_errors(self):
        with self.assertRaises(ValueError):
            parse_json_object("{}", allowed_fields={1})

    def test_configuration_conversion_error_drops_underlying_context(self):
        marker = "private-configuration-marker"
        with self.assertRaises(ValueError) as raised:
            parse_json_object("{}", allowed_fields=[[marker]])
        self.assertIsNone(raised.exception.__context__)
        self.assertIsNone(raised.exception.__cause__)
        self.assertNotIn(marker, str(raised.exception))
        self.assertNotIn(marker, repr(raised.exception))


class JsonNumberPolicyTests(BoundaryTestCase):
    def parse(self, text: str, *, reject_numbers: bool = True):
        return parse_json_object(
            text,
            allowed_fields={"value", "nested", "items", "enabled"},
            reject_numbers=reject_numbers,
        )

    def test_numeric_tokens_are_rejected_anywhere(self):
        for text in (
            '{"value":1}',
            '{"value":-1}',
            '{"value":1.5}',
            '{"value":1e2}',
            '{"nested":{"value":1}}',
            '{"items":["ok",1]}',
        ):
            with self.subTest(text=text):
                self.assert_boundary_error("invalid_json", 400, self.parse, text)

    def test_number_policy_is_an_explicit_mode(self):
        parsed = self.parse('{"value":1}', reject_numbers=False)
        self.assertEqual(parsed, {"value": 1})
        self.assertIs(type(parsed["value"]), int)

    def test_boolean_remains_distinct_and_never_passes_string_validation(self):
        parsed = self.parse('{"enabled":true}')
        self.assertIs(parsed["enabled"], True)
        self.assertIs(type(parsed["enabled"]), bool)
        self.assert_boundary_error(
            "invalid_value", 400, require_exact_string, parsed["enabled"]
        )


class ExactValueHelperTests(BoundaryTestCase):
    def test_exact_string_and_expected_value(self):
        self.assertEqual(require_exact_string("reply"), "reply")
        self.assertEqual(require_exact_string("reply", expected="reply"), "reply")
        for value in (True, 1, None, ["reply"]):
            with self.subTest(value=value):
                self.assert_boundary_error(
                    "invalid_value", 400, require_exact_string, value
                )
        self.assert_boundary_error(
            "invalid_value",
            400,
            require_exact_string,
            "read",
            expected="reply",
        )

    def test_bounded_utf8_string_uses_encoded_byte_length(self):
        value = "\N{EURO SIGN}"
        self.assertEqual(
            require_bounded_utf8_string(value, maximum_bytes=3), value
        )
        self.assert_boundary_error(
            "invalid_value",
            400,
            require_bounded_utf8_string,
            value,
            maximum_bytes=2,
        )
        self.assert_boundary_error(
            "invalid_value",
            400,
            require_bounded_utf8_string,
            "",
            maximum_bytes=0,
            allow_empty=False,
        )

    def test_ascii_identifier_uses_caller_supplied_exact_syntax(self):
        syntax = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
        self.assertEqual(
            require_ascii_identifier(
                "Mailbox_1", syntax=syntax, maximum_bytes=16
            ),
            "Mailbox_1",
        )
        for value in ("1Mailbox", "mail.box", "m\N{LATIN SMALL LETTER E WITH ACUTE}"):
            with self.subTest(value=value):
                self.assert_boundary_error(
                    "invalid_value",
                    400,
                    require_ascii_identifier,
                    value,
                    syntax=syntax,
                    maximum_bytes=16,
                )


class SafeErrorTests(BoundaryTestCase):
    def test_errors_expose_only_stable_safe_request_details(self):
        secret = "private-json-marker"
        error = self.assert_boundary_error(
            "invalid_json",
            400,
            parse_json_object,
            '{"token":"' + secret,
            allowed_fields={"token"},
        )
        self.assertEqual(error.args, ("invalid_json",))
        self.assert_marker_not_retained(error, secret)

        header_error = self.assert_boundary_error(
            "invalid_headers",
            400,
            validate_raw_headers,
            [("Authorization", secret + "\r\ninjected")],
        )
        self.assert_marker_not_retained(header_error, secret)

    def test_surrogate_encoding_error_does_not_retain_rejected_string(self):
        marker = "private-string-marker"
        error = self.assert_boundary_error(
            "invalid_value",
            400,
            require_bounded_utf8_string,
            marker + "\ud800",
            maximum_bytes=100,
        )
        self.assert_marker_not_retained(error, marker)


if __name__ == "__main__":
    unittest.main()
