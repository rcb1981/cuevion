"""Bounded raw Gmail detail transport against the frozen pre-P1.1 reader.

The sequential_reference function below is copied from commit
bd2ee3edfa8d8e13ac237957cf479bd920fd06c2. Only its name and references to
unchanged module constants/helpers have been qualified. It never invokes the
production reader or its concurrency=1 fallback.
"""
from __future__ import annotations

import copy
import itertools
import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from email import message_from_bytes
from unittest.mock import patch
from urllib.parse import quote

try:
    from .test_gmail_snapshot import gmail_context, gmail_detail, gmail_snapshot
except ImportError:
    from test_gmail_snapshot import gmail_context, gmail_detail, gmail_snapshot


def sequential_reference(
    context: dict,
    *,
    provider_folder: str,
    request_with_one_refresh: gmail_snapshot.GmailRequestWithOneRefresh,
    limit: int = gmail_snapshot.DEFAULT_GMAIL_SNAPSHOT_LIMIT,
    focus_preferences: dict | None = None,
    strict: bool = False,
    required_message_id: str | None = None,
    message_parser=message_from_bytes,
) -> dict:
    """Read and normalize one bounded Gmail folder snapshot.

    The injected request callback owns authenticated transport and the single
    permitted token-refresh attempt. Every return includes the latest context,
    a snapshot or provider error, and any structured refresh failure.
    """

    if (
        provider_folder not in {"Inbox", "Archive", "Trash"}
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or limit < 1
        or limit > gmail_snapshot.MAX_GMAIL_SNAPSHOT_LIMIT
        or not callable(request_with_one_refresh)
        or (
            required_message_id is not None
            and (
                provider_folder != "Archive"
                or not gmail_snapshot.valid_identifier(required_message_id)
            )
        )
    ):
        return gmail_snapshot._result(
            context,
            error={"code": "gmail_snapshot_invalid_request"},
        )

    transport_retry_available = True

    def request_snapshot_path(
        request_context: dict,
        path: str,
    ) -> tuple[dict | None, dict | None, dict, dict | None]:
        nonlocal transport_retry_available

        payload, error, next_context, refresh_failure = (
            request_with_one_refresh(request_context, path)
        )
        if (
            transport_retry_available
            and refresh_failure is None
            and isinstance(error, dict)
            and error.get("code") == "gmail_unavailable"
        ):
            transport_retry_available = False
            return request_with_one_refresh(next_context, path)
        return payload, error, next_context, refresh_failure

    list_payload, list_error, context, refresh_failure = (
        request_snapshot_path(
            context,
            gmail_snapshot._list_path(provider_folder, limit),
        )
    )
    if refresh_failure is not None:
        return gmail_snapshot._result(
            context,
            error=list_error,
            refresh_failure=refresh_failure,
        )
    if list_error is not None:
        return gmail_snapshot._result(context, error=list_error)
    if not isinstance(list_payload, dict):
        return gmail_snapshot._invalid_response(context)

    message_ids, list_is_valid = gmail_snapshot._message_ids_from_list(
        list_payload,
        strict=strict,
        limit=limit,
    )
    if not list_is_valid or message_ids is None:
        return gmail_snapshot._invalid_response(context)

    if (
        required_message_id is not None
        and required_message_id not in message_ids
    ):
        if len(message_ids) >= limit:
            message_ids = message_ids[: limit - 1]
        message_ids.append(required_message_id)

    messages: list[dict] = []
    priority_candidate_sources: list[dict] = []
    snapshot = {
        "providerFolder": provider_folder,
        "serverMailboxId": context.get("mailbox_id"),
        "messages": messages,
        "uidValidity": gmail_snapshot.GMAIL_API_UID_VALIDITY,
    }
    snapshot_size: int | None = None
    message_separator_size = len(
        json.JSONEncoder().item_separator.encode("utf-8")
    )
    for index, requested_message_id in enumerate(message_ids):
        detail_payload, detail_error, context, refresh_failure = (
            request_snapshot_path(
                context,
                (
                    f"/messages/{quote(requested_message_id, safe='')}"
                    "?format=raw"
                ),
            )
        )
        if refresh_failure is not None:
            return gmail_snapshot._result(
                context,
                error=detail_error,
                refresh_failure=refresh_failure,
            )
        if detail_error is not None:
            return gmail_snapshot._result(context, error=detail_error)
        parsed = gmail_snapshot._parse_gmail_message_detail_with_candidate_source(
            detail_payload,
            context=context,
            provider_folder=provider_folder,
            requested_message_id=requested_message_id,
            index=index,
            focus_preferences=focus_preferences,
            strict=strict,
            message_parser=message_parser,
        )
        if parsed is None:
            if strict:
                return gmail_snapshot._invalid_response(context)
            continue
        preview, priority_candidate_source = parsed

        try:
            if snapshot_size is None:
                # Keep serialization lazy: empty/all-invalid snapshots were
                # never size-checked. The empty wrapper already includes [].
                snapshot_size = len(json.dumps(snapshot).encode("utf-8"))
            # Default json.dumps encodes each list element identically on its
            # own, with item_separator only between elements. Preserve those
            # exact UTF-8 bytes without serializing accepted prefixes again.
            candidate_size = (
                snapshot_size
                + len(json.dumps(preview).encode("utf-8"))
                + (message_separator_size if messages else 0)
            )
        except (TypeError, ValueError):
            raise
        if candidate_size > gmail_snapshot.MAX_GMAIL_RESPONSE_BYTES:
            if strict:
                return gmail_snapshot._result(
                    context,
                    error={"code": "gmail_response_too_large"},
                )
            break
        messages.append(preview)
        snapshot_size = candidate_size
        if provider_folder == "Inbox":
            priority_candidate_sources.append(priority_candidate_source)

    return gmail_snapshot._result(
        context,
        snapshot=snapshot,
        priority_candidate_sources=priority_candidate_sources,
    )


class SyntheticWorkerError(RuntimeError):
    pass


class DeterministicTransport:
    """Thread-safe fake keyed by message, token and attempt, never arrival order."""

    def __init__(
        self, count, *, rule=None, list_rule=None, timing=(0, 1, 2, 3),
        delay_unit=0.0003, fixed_delay=0, concurrent=False, refresh_failure=None,
        list_refs=None, initial_refresh_attempted=False, refresh_access_token='T2',
        refresh_exception=None,
    ):
        self.count = count
        self.rule = rule
        self.list_rule = list_rule
        self.timing = timing
        self.delay_unit = delay_unit
        self.fixed_delay = fixed_delay
        self.refresh_failure = refresh_failure
        self.refresh_access_token = refresh_access_token
        self.refresh_exception = refresh_exception
        self.list_refs = list_refs
        self.initial_context = gmail_context(refresh_attempted=initial_refresh_attempted)
        self.initial_context['access_token'] = 'T1'
        self.caller = threading.current_thread()
        self.lock = threading.Lock()
        self.attempts = {}
        self.calls = []
        self.completions = []
        self.refreshes = []
        self.active = 0
        self.max_active = 0
        self.workers = set()
        self.barrier = threading.Barrier(4) if concurrent and count >= 4 else None

    @staticmethod
    def message_id(index):
        return f'message-{index}'

    def raw(self, token, path):
        is_list = path.startswith('/messages?')
        index = None if is_list else int(path.split('/message-')[1].split('?')[0])
        thread = threading.current_thread()
        key = (index, token)
        with self.lock:
            attempt = self.attempts.get(key, 0) + 1
            self.attempts[key] = attempt
            self.calls.append((index, token, attempt, thread))
            if not is_list:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                if thread is not self.caller:
                    self.workers.add(thread)
        try:
            if is_list:
                if self.list_rule is not None:
                    outcome = self.list_rule(token, attempt)
                    if outcome is not None:
                        return outcome
                refs = self.list_refs
                if refs is None:
                    refs = [{'id': self.message_id(i)} for i in range(self.count)]
                return {'messages': copy.deepcopy(refs)}, None
            if self.barrier is not None and index < 4 and token == 'T1' and attempt == 1:
                self.barrier.wait(timeout=5)
            delay = self.fixed_delay + self.timing[index % len(self.timing)] * self.delay_unit
            if delay:
                time.sleep(delay)
            if self.rule is not None:
                outcome = self.rule(index, token, attempt)
                if isinstance(outcome, BaseException):
                    raise outcome
                if outcome is not None:
                    return outcome
            return {'id': self.message_id(index), 'token': token}, None
        finally:
            if not is_list:
                with self.lock:
                    self.active -= 1
                    self.completions.append((index, token, attempt))

    def refresh(self, context):
        self.refreshes.append((copy.deepcopy(context), threading.current_thread()))
        if self.refresh_exception is not None:
            raise self.refresh_exception
        if self.refresh_failure is not None:
            return copy.deepcopy(self.refresh_failure)
        return {
            'status': 'ok',
            'context': {**context, 'access_token': self.refresh_access_token, 'refresh_attempted': True,
                        'scope': 'refreshed-scope'},
        }

    def combined(self, context, path):
        payload, error = self.raw(context['access_token'], path)
        if error and error.get('code') == 'gmail_token_invalid' and not context['refresh_attempted']:
            refreshed = self.refresh(context)
            if refreshed['status'] != 'ok':
                return None, error, context, refreshed
            context = refreshed['context']
            payload, error = self.raw(context['access_token'], path)
        return payload, error, context, None

    @property
    def detail_calls(self):
        return [entry for entry in self.calls if entry[0] is not None]


class GmailDetailConcurrencyTests(unittest.TestCase):
    """Semantic parity, retry ownership, bounded speculation and shutdown."""

    @classmethod
    def setUpClass(cls):
        cls.differential_comparisons = 0

    @classmethod
    def tearDownClass(cls):
        print(f'P1.1 differential: full_result_comparisons={cls.differential_comparisons}', flush=True)

    @staticmethod
    def preview(index, token='T1'):
        return {
            'providerMessageId': f'message-{index}',
            'parserIndex': index,
            'subject': f'Message {index}: café 😀 "quoted"',
            'body': ('\\\n中文' * (index + 1)),
            'tokenEvidence': token,
        }

    @staticmethod
    def source(index, token='T1'):
        return {'providerMessageId': f'message-{index}', 'parserIndex': index,
                'private': 'priority-only', 'tokenEvidence': token}

    def run_reader(
        self, *, concurrent=True, count=8, folder='Inbox', strict=False,
        invalid=(), size_limit=10**9, parser_error=None, serialization_error=None,
        required_message_id=None, limit=50, concurrency=4, **transport_options,
    ):
        fake = DeterministicTransport(
            count, concurrent=concurrent and concurrency == 4,
            **transport_options,
        )
        parsed = []
        original_context = copy.deepcopy(fake.initial_context)

        def parse(payload, *, requested_message_id, index, context, **_kwargs):
            parsed.append((requested_message_id, index, threading.current_thread(),
                           copy.deepcopy(context)))
            if parser_error is not None and index == parser_error:
                raise SyntheticWorkerError('ordered parser exception')
            if index in invalid:
                return None
            token = payload.get('token', 'T1')
            preview = self.preview(index, token)
            preview['providerMessageId'] = requested_message_id
            if serialization_error == index:
                preview['unserializable'] = object()
            source = self.source(index, token)
            source['providerMessageId'] = requested_message_id
            return preview, source

        kwargs = {
            'provider_folder': folder, 'strict': strict,
            'request_with_one_refresh': fake.combined,
            'required_message_id': required_message_id, 'limit': limit,
        }
        reader = sequential_reference
        if concurrent:
            reader = gmail_snapshot.read_gmail_folder_snapshot
            kwargs.update(gmail_request=fake.raw, refresh_context=fake.refresh)
        try:
            with patch.object(gmail_snapshot, 'GMAIL_DETAIL_CONCURRENCY', concurrency), patch.object(
                gmail_snapshot, 'MAX_GMAIL_RESPONSE_BYTES', size_limit
            ), patch.object(
                gmail_snapshot, '_parse_gmail_message_detail_with_candidate_source',
                side_effect=parse,
            ):
                result = reader(fake.initial_context, **kwargs)
        finally:
            self.assertEqual(fake.initial_context, original_context)
            self.assertEqual(fake.active, 0, 'raw transport still running after reader returned')
            self.assertTrue(all(not thread.is_alive() for thread in fake.workers))
            self.assertLessEqual(fake.max_active, concurrency if concurrent else 1)
            self.assertTrue(all(event[2] is fake.caller for event in parsed))
            self.assertTrue(all(event[1] is fake.caller for event in fake.refreshes))
        return result, fake, parsed

    def assert_parity(self, **options):
        expected, reference, reference_parsed = self.run_reader(concurrent=False, **options)
        actual, fake, parsed = self.run_reader(concurrent=True, **options)
        self.assertEqual(actual, expected)
        self.assertEqual(json.dumps(actual).encode('utf-8'), json.dumps(expected).encode('utf-8'))
        self.assertEqual(
            [(row[0], row[1], row[3]) for row in parsed],
            [(row[0], row[1], row[3]) for row in reference_parsed],
        )
        self.assertEqual(len(fake.refreshes), len(reference.refreshes))
        self.assertLessEqual(len(fake.refreshes), 1)
        # Initial speculative work is independently bounded from token replays.
        actual_initial = {entry[0] for entry in fake.detail_calls}
        reference_initial = {entry[0] for entry in reference.detail_calls}
        self.assertLessEqual(len(actual_initial - reference_initial), 3)
        self.assertEqual(
            [(call[0], call[1], call[2]) for call in fake.calls if call[0] is None],
            [(call[0], call[1], call[2]) for call in reference.calls if call[0] is None],
        )
        self.assertTrue(all(call[3] is fake.caller for call in fake.calls if call[0] is None))
        self.__class__.differential_comparisons += 1
        return actual, fake, parsed, reference

    def test_generated_timing_permutations_match_frozen_sequential_reader(self):
        comparisons = 0
        failure = {'status': 'error', 'status_code': 503,
                   'error': {'code': 'oauth_token_store_unavailable'}}
        scenarios = (
            {},
            {'invalid': (1, 4)},
            {'rule': lambda i, _token, _attempt: (None, {'code': 'gmail_message_not_found'}) if i == 2 else None},
            {'rule': lambda i, _token, attempt: (None, {'code': 'gmail_unavailable'}) if i in (0, 1) and attempt == 1 else None},
            {'rule': lambda _i, token, _attempt: (None, {'code': 'gmail_token_invalid'}) if token == 'T1' else None},
            {'rule': lambda _i, token, _attempt: (None, {'code': 'gmail_token_invalid'}) if token == 'T1' else None,
             'refresh_failure': failure},
        )
        for timing in itertools.permutations(range(4)):
            for folder, strict in (('Inbox', False), ('Archive', True), ('Trash', True)):
                for scenario_index, scenario in enumerate(scenarios):
                    with self.subTest(timing=timing, folder=folder, scenario=scenario_index):
                        self.assert_parity(count=8, folder=folder, strict=strict,
                                           timing=timing, **scenario)
                        comparisons += 1
        self.assertEqual(comparisons, 432)

    def test_twenty_four_details_overlap_and_publish_in_original_order(self):
        result, fake, parsed, reference = self.assert_parity(
            count=24, timing=(3, 2, 1, 0), delay_unit=0.002,
        )
        self.assertEqual(fake.max_active, 4)
        self.assertGreater(len(fake.workers), 1)
        self.assertNotEqual([row[0] for row in fake.completions], list(range(24)))
        self.assertEqual([row[1] for row in parsed], list(range(24)))
        self.assertEqual(result['snapshot']['messages'], [self.preview(i) for i in range(24)])
        self.assertEqual(result['_priorityCandidateSources'], [self.source(i) for i in range(24)])
        self.assertEqual(len(fake.detail_calls), len(reference.detail_calls))

    def test_slow_head_does_not_allow_unbounded_refill_before_consumption(self):
        entered = set()
        observed = []
        lock = threading.Lock()

        def rule(index, _token, _attempt):
            with lock:
                entered.add(index)
            if index == 0:
                time.sleep(0.02)
                with lock:
                    observed.append(set(entered))
            return None

        _result, fake, _parsed = self.run_reader(count=24, rule=rule, delay_unit=0)
        self.assertEqual(observed, [{0, 1, 2, 3}])
        self.assertEqual(fake.max_active, 4)

    def test_size_boundaries_match_inbox_archive_and_trash(self):
        comparisons = 0
        for folder, strict in (('Inbox', False), ('Archive', True), ('Trash', True)):
            wrapper = {'providerFolder': folder, 'serverMailboxId': gmail_context()['mailbox_id'],
                       'messages': [], 'uidValidity': 'gmail-api'}
            for accepted in range(7):
                wrapper['messages'] = [self.preview(i) for i in range(accepted)]
                boundary = len(json.dumps(wrapper).encode('utf-8'))
                for offset in (-1, 0, 1):
                    for timing in ((0, 3, 1, 2), (3, 2, 1, 0)):
                        with self.subTest(folder=folder, accepted=accepted, offset=offset, timing=timing):
                            self.assert_parity(count=8, folder=folder, strict=strict,
                                               size_limit=boundary + offset, timing=timing)
                            comparisons += 1
        self.assertEqual(comparisons, 126)

    def test_invalid_parser_rows_skip_or_fail_without_changing_indices(self):
        for folder in ('Inbox', 'Archive', 'Trash'):
            for strict in (False, True):
                with self.subTest(folder=folder, strict=strict):
                    result, _fake, parsed, _reference = self.assert_parity(
                        count=8, folder=folder, strict=strict, invalid=(0, 2, 5),
                    )
                    if strict:
                        self.assertEqual(result['error'], {'code': 'gmail_response_invalid'})
                        self.assertEqual([row[1] for row in parsed], [0])
                    else:
                        self.assertEqual([row['parserIndex'] for row in result['snapshot']['messages']],
                                         [1, 3, 4, 6, 7])

    def test_provider_failure_at_each_window_position_bounds_speculation(self):
        for failure_index in (0, 1, 3, 4, 9, 19):
            for code in ('gmail_message_not_found', 'gmail_permission_denied',
                         'gmail_rate_limited', 'gmail_response_invalid', 'gmail_response_too_large'):
                with self.subTest(index=failure_index, code=code):
                    result, fake, parsed, reference = self.assert_parity(
                        count=24, timing=(3, 2, 1, 0),
                        rule=lambda i, _token, _attempt: (None, {'code': code}) if i == failure_index else None,
                    )
                    self.assertEqual(result['error'], {'code': code})
                    self.assertIsNone(result['snapshot'])
                    self.assertEqual([row[1] for row in parsed], list(range(failure_index)))
                    self.assertLessEqual(len(fake.detail_calls) - len(reference.detail_calls), 3)
                    self.assertLessEqual(max(call[0] for call in fake.detail_calls), failure_index + 3)

    def test_later_fast_auth_failure_cannot_refresh_before_earlier_terminal_error(self):
        def rule(index, _token, _attempt):
            if index == 0:
                return None, {'code': 'gmail_permission_denied'}
            return None, {'code': 'gmail_token_invalid'}

        result, fake, _parsed, _reference = self.assert_parity(
            count=20, rule=rule, timing=(3, 2, 1, 0), delay_unit=0.002,
        )
        self.assertEqual(result['error'], {'code': 'gmail_permission_denied'})
        self.assertEqual(fake.refreshes, [])
        self.assertEqual(len(fake.detail_calls), 4)

    def test_four_simultaneous_auth_failures_have_one_ordered_refresh(self):
        result, fake, _parsed, reference = self.assert_parity(
            count=20,
            rule=lambda _i, token, _attempt: (None, {'code': 'gmail_token_invalid'}) if token == 'T1' else None,
            timing=(3, 2, 1, 0),
        )
        self.assertEqual(sorted(call[0] for call in fake.detail_calls if call[1] == 'T1'), [0, 1, 2, 3])
        self.assertEqual(len(fake.refreshes), 1)
        self.assertEqual(result['context']['access_token'], 'T2')
        self.assertTrue(result['context']['refresh_attempted'])
        self.assertEqual([row['tokenEvidence'] for row in result['snapshot']['messages']], ['T2'] * 20)
        self.assertEqual(len(fake.detail_calls), len(reference.detail_calls) + 3)
        self.assertEqual({call[0] for call in fake.detail_calls if call[1] == 'T2'}, set(range(20)))
        self.assertTrue(all(count == 1 for (index, _token), count in fake.attempts.items() if index is not None))

    def test_refresh_failure_matches_reference_after_simultaneous_old_token_failures(self):
        failure = {'status': 'error', 'status_code': 503,
                   'error': {'code': 'oauth_token_store_unavailable'}}
        result, fake, parsed, reference = self.assert_parity(
            count=20, refresh_failure=failure,
            rule=lambda _i, _token, _attempt: (None, {'code': 'gmail_token_invalid'}),
        )
        self.assertEqual(result['refresh_failure'], failure)
        self.assertEqual(result['context'], fake.initial_context)
        self.assertEqual(len(fake.refreshes), 1)
        self.assertEqual(len(fake.detail_calls) - len(reference.detail_calls), 3)
        self.assertFalse(any(call[1] == 'T2' for call in fake.detail_calls))
        self.assertEqual(parsed, [])

    def test_refreshed_generation_is_distinct_even_when_access_token_is_unchanged(self):
        result, fake, _parsed, reference = self.assert_parity(
            count=12, refresh_access_token='T1',
            rule=lambda i, _token, attempt: (None, {'code': 'gmail_token_invalid'})
            if i == 0 and attempt == 1 else None,
        )
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['context']['access_token'], 'T1')
        self.assertTrue(result['context']['refresh_attempted'])
        self.assertEqual(result['context']['scope'], 'refreshed-scope')
        self.assertEqual(len(fake.refreshes), 1)
        self.assertEqual(len(fake.detail_calls) - len(reference.detail_calls), 3)
        self.assertEqual({index for (index, _token), count in fake.attempts.items()
                          if index is not None and count == 2}, {0, 1, 2, 3})

    def test_refresh_then_failure_bounds_initial_speculation_and_stale_replays_separately(self):
        def rule(index, token, _attempt):
            if index == 0 and token == 'T1':
                return None, {'code': 'gmail_token_invalid'}
            if index == 3 and token == 'T2':
                return None, {'code': 'gmail_permission_denied'}
            return None

        result, fake, parsed, reference = self.assert_parity(count=20, rule=rule)
        self.assertEqual(result['error'], {'code': 'gmail_permission_denied'})
        self.assertEqual([row[1] for row in parsed], [0, 1, 2])
        actual_ids = {call[0] for call in fake.detail_calls}
        reference_ids = {call[0] for call in reference.detail_calls}
        self.assertEqual(actual_ids - reference_ids, {4, 5, 6})
        # T2 replay of 0 is the original permitted auth retry. Only 1/2/3
        # are extra stale-generation replays compared with the reference.
        stale_replay_ids = {i for i in range(1, 4)
                            if (i, 'T1') in fake.attempts and (i, 'T2') in fake.attempts}
        self.assertEqual(stale_replay_ids, {1, 2, 3})
        self.assertEqual(len(fake.detail_calls) - len(reference.detail_calls), 6)
        self.assertEqual(len(fake.refreshes), 1)

    def test_stale_success_error_and_exception_are_replaced_after_refresh(self):
        for stale_kind in ('success', 'provider_error', 'exception'):
            with self.subTest(stale_kind=stale_kind):
                def rule(index, token, _attempt):
                    if token != 'T1':
                        return None
                    if index == 0:
                        return None, {'code': 'gmail_token_invalid'}
                    if stale_kind == 'provider_error':
                        return None, {'code': 'gmail_permission_denied'}
                    if stale_kind == 'exception':
                        return SyntheticWorkerError('stale transport exception')
                    return None

                result, fake, _parsed, reference = self.assert_parity(count=12, rule=rule)
                self.assertEqual(result['status'], 'ok')
                self.assertEqual([row['tokenEvidence'] for row in result['snapshot']['messages']], ['T2'] * 12)
                self.assertEqual(len(fake.detail_calls) - len(reference.detail_calls), 3)

    def test_auth_retry_bound_is_shared_with_list_and_second_invalid_token(self):
        cases = (
            {'list_rule': lambda token, _attempt: (None, {'code': 'gmail_token_invalid'}) if token == 'T1' else None,
             'rule': lambda _i, _token, _attempt: (None, {'code': 'gmail_token_invalid'})},
            {'rule': lambda i, token, _attempt: (None, {'code': 'gmail_token_invalid'}) if token == 'T1' or i == 4 else None},
            {'initial_refresh_attempted': True,
             'rule': lambda _i, _token, _attempt: (None, {'code': 'gmail_token_invalid'})},
        )
        for case in cases:
            with self.subTest(case=tuple(case)):
                result, fake, _parsed, _reference = self.assert_parity(count=12, **case)
                self.assertEqual(result['error'], {'code': 'gmail_token_invalid'})
                self.assertLessEqual(len(fake.refreshes), 1)
                if case.get('initial_refresh_attempted'):
                    self.assertEqual(fake.refreshes, [])

    def test_simultaneous_unavailable_responses_retry_only_earliest_ordered_request(self):
        result, fake, parsed, _reference = self.assert_parity(
            count=20,
            rule=lambda i, _token, attempt: (None, {'code': 'gmail_unavailable'}) if i < 4 and attempt == 1 else None,
            timing=(3, 2, 1, 0), delay_unit=0.002,
        )
        retries = [(index, token, attempt) for index, token, attempt, _thread in fake.detail_calls if attempt > 1]
        self.assertEqual(retries, [(0, 'T1', 2)])
        self.assertEqual(result['error'], {'code': 'gmail_unavailable'})
        self.assertEqual([row[1] for row in parsed], [0])
        self.assertTrue(all(thread is fake.caller for _index, _token, attempt, thread in fake.detail_calls if attempt > 1))

    def test_list_consumed_transport_retry_leaves_zero_detail_retries(self):
        result, fake, parsed, _reference = self.assert_parity(
            count=20,
            list_rule=lambda _token, attempt: (None, {'code': 'gmail_unavailable'}) if attempt == 1 else None,
            rule=lambda _i, _token, _attempt: (None, {'code': 'gmail_unavailable'}),
        )
        self.assertEqual(result['error'], {'code': 'gmail_unavailable'})
        self.assertEqual(len([call for call in fake.calls if call[0] is None]), 2)
        self.assertTrue(all(call[2] == 1 for call in fake.detail_calls))
        self.assertEqual(parsed, [])

    def test_transport_retry_and_refresh_keep_both_sequential_transition_orders(self):
        for auth_first in (False, True):
            def rule(index, token, attempt):
                if index != 0:
                    return None
                if auth_first:
                    if token == 'T1':
                        return None, {'code': 'gmail_token_invalid'}
                    if attempt == 1:
                        return None, {'code': 'gmail_unavailable'}
                elif token == 'T1':
                    return None, {'code': 'gmail_unavailable' if attempt == 1 else 'gmail_token_invalid'}
                return None

            with self.subTest(auth_first=auth_first):
                result, fake, _parsed, reference = self.assert_parity(count=12, rule=rule)
                self.assertEqual(result['status'], 'ok')
                self.assertEqual(len(fake.refreshes), 1)
                self.assertEqual(len([call for call in fake.detail_calls if call[2] > 1]), 1)
                self.assertEqual(len(fake.detail_calls) - len(reference.detail_calls), 3)

    def test_list_failure_performs_no_detail_work(self):
        for code in ('gmail_permission_denied', 'gmail_rate_limited', 'gmail_response_invalid', 'gmail_unavailable'):
            with self.subTest(code=code):
                result, fake, _parsed, _reference = self.assert_parity(
                    count=20, list_rule=lambda _token, _attempt: (None, {'code': code}),
                )
                self.assertEqual(result['error'], {'code': code})
                self.assertEqual(fake.detail_calls, [])
                self.assertEqual(fake.workers, set())

    def test_empty_single_and_short_lists_use_only_needed_workers(self):
        for count in (0, 1, 2, 3):
            with self.subTest(count=count):
                result, fake, parsed, _reference = self.assert_parity(count=count)
                self.assertEqual(result['status'], 'ok')
                self.assertEqual(len(fake.detail_calls), count)
                self.assertEqual(len(parsed), count)
                self.assertLessEqual(len(fake.workers), count)
                if not count:
                    self.assertEqual(fake.max_active, 0)

    def test_concurrency_one_is_a_trivial_equivalent_fallback(self):
        expected, reference, expected_parsed = self.run_reader(concurrent=False, count=12)
        actual, fake, parsed = self.run_reader(count=12, concurrency=1)
        self.assertEqual(actual, expected)
        self.assertEqual(fake.max_active, 1)
        self.assertEqual([call[:3] for call in fake.calls], [call[:3] for call in reference.calls])
        self.assertEqual([row[1] for row in parsed], [row[1] for row in expected_parsed])
        self.__class__.differential_comparisons += 1

    def test_unexpected_refresh_exception_matches_reference_and_waits_for_workers(self):
        for concurrent in (False, True):
            with self.assertRaisesRegex(SyntheticWorkerError, 'unexpected refresh failure'):
                self.run_reader(
                    concurrent=concurrent, count=12,
                    refresh_exception=SyntheticWorkerError('unexpected refresh failure'),
                    rule=lambda _index, _token, _attempt: (None, {'code': 'gmail_token_invalid'}),
                )

    def test_worker_exception_and_parser_exception_wait_for_shutdown(self):
        for exception_index in (0, 2, 5):
            with self.subTest(kind='worker', index=exception_index):
                def rule(index, _token, _attempt):
                    if index == exception_index:
                        return SyntheticWorkerError('ordered transport exception')
                    time.sleep(0.002)
                    return None

                for concurrent in (False, True):
                    with self.assertRaisesRegex(SyntheticWorkerError, 'ordered transport exception'):
                        self.run_reader(concurrent=concurrent, count=12, rule=rule)
            with self.subTest(kind='parser', index=exception_index):
                for concurrent in (False, True):
                    with self.assertRaisesRegex(SyntheticWorkerError, 'ordered parser exception'):
                        self.run_reader(concurrent=concurrent, count=12, parser_error=exception_index)
            with self.subTest(kind='serialization', index=exception_index):
                for concurrent in (False, True):
                    with self.assertRaises(TypeError):
                        self.run_reader(concurrent=concurrent, count=12, serialization_error=exception_index)

    def test_later_worker_exception_is_ignored_after_earlier_provider_failure(self):
        def rule(index, _token, _attempt):
            if index == 0:
                return None, {'code': 'gmail_permission_denied'}
            return SyntheticWorkerError('later failure must remain unobserved')

        result, _fake, parsed, _reference = self.assert_parity(count=20, rule=rule)
        self.assertEqual(result['error'], {'code': 'gmail_permission_denied'})
        self.assertEqual(parsed, [])

    def test_pending_executor_work_is_cancelled_on_ordered_failure(self):
        executors = []
        cancellation_finished = threading.Event()

        class QueuedExecutor(ThreadPoolExecutor):
            def __init__(self, *args, **kwargs):
                kwargs['max_workers'] = 1
                super().__init__(**kwargs)
                self.submitted = []
                executors.append(self)

            def submit(self, *args, **kwargs):
                future = super().submit(*args, **kwargs)
                ordinal = len(self.submitted)
                original_cancel = future.cancel

                def cancel():
                    cancelled = original_cancel()
                    if ordinal == 3:
                        cancellation_finished.set()
                    return cancelled

                future.cancel = cancel
                self.submitted.append(future)
                return future

        def rule(index, _token, _attempt):
            if index == 0:
                return None, {'code': 'gmail_permission_denied'}
            if not cancellation_finished.wait(timeout=5):
                raise AssertionError('ordered failure did not cancel queued futures')
            return None

        fake = DeterministicTransport(20, rule=rule, delay_unit=0, concurrent=False)
        with patch.object(gmail_snapshot, 'ThreadPoolExecutor', QueuedExecutor):
            result = gmail_snapshot.read_gmail_folder_snapshot(
                fake.initial_context, provider_folder='Inbox', request_with_one_refresh=fake.combined,
                gmail_request=fake.raw, refresh_context=fake.refresh,
            )
        self.assertEqual(result['error'], {'code': 'gmail_permission_denied'})
        self.assertEqual(len(executors), 1)
        self.assertLessEqual(len(executors[0].submitted), 4)
        self.assertTrue(any(future.cancelled() for future in executors[0].submitted))
        self.assertEqual(fake.active, 0)
        self.assertTrue(all(not thread.is_alive() for thread in fake.workers))

    def test_detail_capabilities_must_be_callable_and_supplied_together(self):
        for raw, refresh in ((None, lambda _context: None),
                             (lambda _token, _path: None, None),
                             (object(), lambda _context: None),
                             (lambda _token, _path: None, object())):
            with self.subTest(raw=type(raw).__name__, refresh=type(refresh).__name__):
                fake = DeterministicTransport(4)
                result = gmail_snapshot.read_gmail_folder_snapshot(
                    fake.initial_context, provider_folder='Inbox',
                    request_with_one_refresh=fake.combined,
                    gmail_request=raw, refresh_context=refresh,
                )
                self.assertEqual(result['error'], {'code': 'gmail_snapshot_invalid_request'})
                self.assertEqual(fake.calls, [])
                self.assertEqual(fake.refreshes, [])

    def test_list_validation_required_archive_id_and_limits_match_reference(self):
        refs = [{'id': 'message-0'}, {'id': 'message-0'}, None,
                {'id': 'message-1'}, {'id': 'message-2'}]
        for strict in (False, True):
            with self.subTest(strict=strict):
                self.assert_parity(count=3, list_refs=refs, strict=strict)
        result, fake, _parsed, _reference = self.assert_parity(
            count=3, folder='Archive', strict=True, limit=3, required_message_id='message-9',
        )
        self.assertEqual([row['providerMessageId'] for row in result['snapshot']['messages']],
                         ['message-0', 'message-1', 'message-9'])
        self.assertEqual({call[0] for call in fake.detail_calls}, {0, 1, 9})
        for folder, strict in (('Inbox', False), ('Archive', True), ('Trash', True)):
            with self.subTest(folder=folder, limit=100):
                result, fake, _parsed, _reference = self.assert_parity(
                    count=100, folder=folder, strict=strict, limit=100, delay_unit=0,
                )
                self.assertEqual(len(result['snapshot']['messages']), 100)
                self.assertEqual(len(fake.detail_calls), 100)

    def test_real_mime_parser_result_and_priority_sources_match_reference(self):
        def run(concurrent):
            fake = DeterministicTransport(8, concurrent=concurrent)
            raw = fake.raw

            def mime_raw(token, path):
                payload, error = raw(token, path)
                if path.startswith('/messages?') or error:
                    return payload, error
                detail = gmail_detail(payload['id'])
                # Fixed RFC date avoids wall-clock-dependent preview fallback.
                import base64
                content = base64.urlsafe_b64decode(detail['raw'] + '=' * (-len(detail['raw']) % 4))
                content = b'Date: Tue, 01 Jul 2025 12:00:00 +0200\r\n' + content
                detail['raw'] = base64.urlsafe_b64encode(content).rstrip(b'=').decode('ascii')
                detail['internalDate'] = '1751364000000'
                return detail, None

            fake.raw = mime_raw
            reader = gmail_snapshot.read_gmail_folder_snapshot if concurrent else sequential_reference
            kwargs = {'provider_folder': 'Inbox', 'request_with_one_refresh': fake.combined}
            if concurrent:
                kwargs.update(gmail_request=mime_raw, refresh_context=fake.refresh)
            result = reader(fake.initial_context, **kwargs)
            self.assertEqual(fake.active, 0)
            self.assertTrue(all(not thread.is_alive() for thread in fake.workers))
            return result

        expected = run(False)
        actual = run(True)
        self.assertEqual(actual, expected)
        self.assertEqual(json.dumps(actual), json.dumps(expected))
        self.__class__.differential_comparisons += 1
        self.assertEqual([row['providerMessageId'] for row in actual['_priorityCandidateSources']],
                         [f'message-{index}' for index in range(8)])

    def test_synthetic_latency_for_four_twenty_and_fifty_messages(self):
        for count in (4, 20, 50):
            start = time.perf_counter()
            expected, reference, _ = self.run_reader(concurrent=False, count=count,
                                                      fixed_delay=0.02, delay_unit=0)
            sequential_seconds = time.perf_counter() - start
            start = time.perf_counter()
            actual, fake, _ = self.run_reader(count=count, fixed_delay=0.02, delay_unit=0)
            concurrent_seconds = time.perf_counter() - start
            self.assertEqual(actual, expected)
            self.__class__.differential_comparisons += 1
            self.assertEqual(fake.max_active, 4)
            self.assertEqual(reference.max_active, 1)
            self.assertLess(concurrent_seconds, sequential_seconds * 0.85)
            print(f'P1.1 synthetic: messages={count} delay_ms=20 '
                  f'sequential_s={sequential_seconds:.4f} concurrent_s={concurrent_seconds:.4f} '
                  f'max_in_flight={fake.max_active}', flush=True)


if __name__ == '__main__':
    unittest.main()
