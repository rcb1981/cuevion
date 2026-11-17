"""Offline recipient-routing regressions; no provider or SMTP network calls.

Gmail's fake models its documented To/Cc/Bcc routing and Bcc removal. It checks
our submission contract, not Google's implementation: real delivered headers
still require a controlled production acceptance test.
"""

import base64
import io
import json
import smtplib
import sys
import unittest
from contextlib import ExitStack
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from itertools import product
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import test_gmail_ownership_hardening as fixtures
import smtp_connection

send_gmail = fixtures.send_gmail


def addresses(message, field):
    return [address for _, address in getaddresses(message.get_all(field, []))]


def recipient_cases():
    for flags in product((False, True), repeat=3):
        if any(flags):
            yield {
                field: f'{field}@example.com' if enabled else ''
                for field, enabled in zip(('to', 'cc', 'bcc'), flags)
            }


class GmailDeliveryTransport:
    """Explicit provider-contract simulation, never a live delivery test."""

    def __init__(self):
        self.submissions = []
        self.recipients = []
        self.delivered_mime = None

    def __call__(self, request, timeout):
        assert request.get_method() == 'POST'
        assert request.full_url == f'{send_gmail.GMAIL_API_BASE_URL}/messages/send'
        payload = json.loads(request.data)
        encoded = payload['raw']
        message = BytesParser(policy=policy.default).parsebytes(
            base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))
        )
        self.submissions.append((payload, message))
        self.recipients.extend(
            address
            for field in ('To', 'Cc', 'Bcc')
            for address in addresses(message, field)
        )
        # Model the provider's recipient copy, separately from submitted MIME.
        delivered = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
        del delivered['Bcc']
        self.delivered_mime = delivered.as_bytes()
        return fixtures.BoundaryResponse(
            json.dumps({'id': 'sent-msg', 'threadId': payload.get('threadId', 'sent-thread')})
        )


class BccDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('socket.create_connection', side_effect=AssertionError('Network forbidden')))
        self.stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')))
        self.matrix = fixtures.RealHandlerOwnershipMatrixTests()

    def payload(self, **updates):
        return {
            'mailboxId': 'gmail-1',
            'subject': 'Subject',
            'bodyText': 'Authored body\nSignature',
            **updates,
        }

    def invoke(self, payload, **kwargs):
        # Reuse session/ownership fixtures, but model the current exact-snapshot
        # token transport; the legacy suite's default mock predates this contract.
        record = fixtures.oauth_token_store.build_google_token_record(
            email='verified@gmail.com', owner_email='owner@example.com',
            token_payload={'access_token': 'access-secret', 'refresh_token': 'refresh-secret'},
        )
        record.update(kwargs.get('token_record', {}))
        record.pop('_storage_durable', None)
        raw_record = json.dumps(record)

        def token_transport(request, timeout):
            command = json.loads(request.data)
            self.assertEqual(command[0], 'EVAL')
            if command[1] == fixtures.oauth_token_store.GOOGLE_TOKEN_READ_EXACT_SCRIPT:
                result = fixtures.oauth_token_store.GOOGLE_TOKEN_RAW_SNAPSHOT_PREFIX + raw_record
            else:
                self.assertEqual(command[1], fixtures.oauth_token_store.GOOGLE_TOKEN_PERSIST_IF_UNCHANGED_SCRIPT)
                self.assertEqual(command[4], raw_record)
                result = 1
            return fixtures.BoundaryResponse(json.dumps({'result': result}))

        return self.matrix._invoke(
            send_gmail, payload, {}, token_transport_override=Mock(side_effect=token_transport), **kwargs
        )

    def invoke_gmail(self, payload, **kwargs):
        transport = GmailDeliveryTransport()
        result = self.invoke(
            payload, provider_transport_override=Mock(side_effect=transport), **kwargs
        )
        return result, transport

    def test_gmail_all_seven_recipient_combinations_and_delivered_privacy(self):
        for fields in recipient_cases():
            with self.subTest(fields=fields):
                (request, _, _, provider), transport = self.invoke_gmail(self.payload(**fields))
                self.assertEqual(request.status, 200)
                provider.assert_called_once()
                self.assertEqual(transport.recipients, [value for value in fields.values() if value])
                payload, submitted = transport.submissions[0]
                self.assertEqual(set(payload), {'raw'})
                self.assertEqual(submitted['From'], 'verified@gmail.com')
                for field, value in fields.items():
                    self.assertEqual(addresses(submitted, field), [value] if value else [])
                delivered = BytesParser(policy=policy.default).parsebytes(transport.delivered_mime)
                self.assertIsNone(delivered['Bcc'])
                if fields['bcc']:
                    self.assertNotIn(fields['bcc'].encode(), transport.delivered_mime)
                    self.assertNotIn(fields['bcc'], json.dumps(request.payload()))

    def test_gmail_repeated_addresses_are_sent_once_without_promoting_hidden_recipients(self):
        (request, _, _, provider), transport = self.invoke_gmail(self.payload(
            to='Visible <to@example.com>, to@example.com',
            cc='to@example.com, cc@example.com, cc@example.com',
            bcc='to@example.com, cc@example.com, Hidden <hidden@example.com>, hidden@example.com, second@example.com',
        ))
        self.assertEqual(request.status, 200)
        provider.assert_called_once()
        self.assertEqual(transport.recipients, ['to@example.com', 'cc@example.com', 'hidden@example.com', 'second@example.com'])
        submitted = transport.submissions[0][1]
        self.assertEqual(addresses(submitted, 'To'), ['to@example.com'])
        self.assertEqual(addresses(submitted, 'Cc'), ['cc@example.com'])
        self.assertEqual(addresses(submitted, 'Bcc'), ['hidden@example.com', 'second@example.com'])
        for hidden in (b'hidden@example.com', b'second@example.com'):
            self.assertNotIn(hidden, transport.delivered_mime)

    def test_reply_reply_all_and_forward_preserve_content_and_threading(self):
        for mode in ('reply', 'reply_all', 'forward'):
            with self.subTest(mode=mode):
                transport = GmailDeliveryTransport()
                reply = mode != 'forward'
                payload = self.payload(
                    to='to@example.com', cc='cc@example.com' if mode == 'reply_all' else '',
                    bcc='hidden@example.com', subject='Re: Subject' if reply else 'Fwd: Subject',
                    bodyHtml='<p>Authored body</p><p>Signature</p><blockquote>Quoted source</blockquote>',
                    attachments=[{'name': 'sample.txt', 'mimeType': 'text/plain',
                                  'contentBase64': base64.b64encode(b'synthetic attachment').decode()}],
                )
                if reply:
                    payload['replyContext'] = {'sourceProviderMessageId': 'source-msg'}
                source = fixtures.GmailReplyThreadContinuityTests().source_payload()
                # Source participants are never authorization to add recipients.
                source['payload']['headers'].append({'name': 'Bcc', 'value': 'unauthorized@example.com'})

                def route(request, timeout):
                    if request.get_method() == 'GET':
                        return fixtures.BoundaryResponse(json.dumps(source))
                    return transport(request, timeout)

                provider = Mock(side_effect=route)
                with patch.object(send_gmail, '_prepare_semantic_event_context', return_value=None):
                    request, _, _, _ = self.invoke(payload, provider_transport_override=provider)
                self.assertEqual(request.status, 200)
                self.assertEqual(provider.call_count, 2 if reply else 1)
                self.assertEqual(len(transport.submissions), 1)
                submitted_payload, message = transport.submissions[0]
                self.assertEqual(submitted_payload.get('threadId'), 'thread-123' if reply else None)
                self.assertEqual(message['In-Reply-To'], '<source@example.com>' if reply else None)
                self.assertEqual(message['References'], '<root@example.com> <source@example.com>' if reply else None)
                self.assertEqual(message.get_body(preferencelist=('plain',)).get_content().strip(), payload['bodyText'])
                self.assertEqual(message.get_body(preferencelist=('html',)).get_content().strip(), payload['bodyHtml'])
                attachment, = message.iter_attachments()
                self.assertEqual(attachment.get_filename(), 'sample.txt')
                self.assertEqual(attachment.get_payload(decode=True), b'synthetic attachment')
                self.assertEqual(transport.recipients, ['to@example.com', *(['cc@example.com'] if mode == 'reply_all' else []), 'hidden@example.com'])
                self.assertNotIn(b'hidden@example.com', transport.delivered_mime)

    def test_invalid_recipients_and_limits_fail_before_transport(self):
        cases = [
            {}, {'bcc': 'not-an-address'},
            {'bcc': 'safe@example.com\r\nCc: injected@example.com'},
            {'to': 'to@example.com', 'bcc': 'safe@example.com\nBcc: injected@example.com'},
            {'bcc': ','.join(f'r{i}@example.com' for i in range(101))},
            # Duplicate elimination must not bypass the existing request limit.
            {'bcc': ','.join(['same@example.com'] * 101)},
        ]
        for fields in cases:
            with self.subTest(fields=fields):
                (request, _, _, provider), _ = self.invoke_gmail(self.payload(**fields))
                self.assertEqual(request.status, 400)
                provider.assert_not_called()

    def test_bcc_send_preserves_session_ownership_connection_and_token_restrictions(self):
        cases = [
            ({'cookie': ''}, 401),
            ({'config': {'v': 1, 'email': 'owner@example.com', 'managedInboxes': [fixtures.inbox(id='other-mailbox')]}}, 404),
            ({'config': {'v': 1, 'email': 'owner@example.com', 'managedInboxes': [fixtures.inbox(connected=False)]}}, 409),
            ({'config': {'v': 1, 'email': 'owner@example.com', 'managedInboxes': [fixtures.inbox(provider='microsoft')]}}, 400),
            ({'token_record': fixtures.token(owner_email='other@example.com')}, 401),
        ]
        for overrides, expected in cases:
            with self.subTest(overrides=overrides):
                (request, _, _, provider), _ = self.invoke_gmail(self.payload(bcc='hidden@example.com'), **overrides)
                self.assertEqual(request.status, expected)
                provider.assert_not_called()

    def test_bcc_cannot_supply_identity_credentials_or_raw_headers(self):
        for field, value in {
            'from': 'forged@example.com', 'provider': 'google', 'email': 'forged@example.com',
            'accessToken': 'untrusted', 'smtpHost': 'untrusted.example',
            'raw': 'untrusted MIME', 'headers': {'Bcc': 'injected@example.com'},
        }.items():
            with self.subTest(field=field):
                (request, config, token, provider), _ = self.invoke_gmail(self.payload(bcc='hidden@example.com', **{field: value}))
                self.assertEqual(request.status, 400)
                config.assert_not_called()
                token.assert_not_called()
                provider.assert_not_called()

    def test_gmail_permission_denied_does_not_retry_or_fall_back_to_smtp(self):
        provider = Mock(side_effect=HTTPError('https://gmail.googleapis.com', 403, 'denied', {}, io.BytesIO()))
        with patch.object(send_gmail, 'send_public_smtp_message') as smtp:
            request, _, _, _ = self.invoke(self.payload(bcc='hidden@example.com'), provider_transport_override=provider)
        self.assertEqual(request.status, 403)
        self.assertEqual(request.payload()['error']['code'], 'gmail_permission_denied')
        provider.assert_called_once()
        smtp.assert_not_called()

    def test_token_rejection_retries_same_recipients_once_after_refresh(self):
        transport = GmailDeliveryTransport()
        attempts = []

        def provider(request, timeout):
            attempts.append(json.loads(request.data))
            if len(attempts) == 1:
                raise HTTPError(request.full_url, 401, 'expired', {}, io.BytesIO())
            return transport(request, timeout)

        with patch.object(send_gmail, 'refresh_gmail_context', return_value={
            'status': 'ok', 'context': {'access_token': 'refreshed', 'refresh_attempted': True},
        }) as refresh:
            request, _, _, _ = self.invoke(
                self.payload(to='to@example.com', cc='cc@example.com', bcc='hidden@example.com'),
                provider_transport_override=Mock(side_effect=provider),
            )
        self.assertEqual(request.status, 200)
        refresh.assert_called_once()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(len(transport.submissions), 1)
        self.assertEqual(transport.recipients, ['to@example.com', 'cc@example.com', 'hidden@example.com'])

    def test_ambiguous_transport_failure_does_not_retry_bcc_delivery(self):
        provider = Mock(side_effect=TimeoutError('synthetic timeout'))
        request, _, _, _ = self.invoke(
            self.payload(bcc='hidden@example.com'), provider_transport_override=provider,
        )
        self.assertEqual(request.status, 502)
        provider.assert_called_once()

    def invoke_smtp(self, fields):
        request = fixtures.FakeHandler(self.payload(**fields))
        client = smtplib.SMTP()  # No host, so no connection; exercise real MIME serialization.
        client.ehlo_or_helo_if_needed = Mock()
        client.sendmail = Mock(return_value={})
        mailbox = {
            'email': 'owner@example.com',
            'smtp': {'host': 'smtp.example.com', 'port': 465, 'security': 'ssl',
                     'username': 'stored-user', 'password': 'stored-password'},
        }
        with patch.object(send_gmail, 'resolve_owned_mailbox', return_value={'status': 'ok', 'inbox': {'provider': 'custom_imap'}}), patch.object(
            send_gmail, 'resolve_authenticated_imap_mailbox', return_value={'status': 'ok', 'mailbox': mailbox}
        ) as resolve, patch.object(smtp_connection, 'open_authenticated_public_smtp_connection') as connection, patch.object(
            send_gmail, 'urlopen', side_effect=AssertionError('Gmail must not be called')
        ):
            connection.return_value.__enter__.return_value = client
            send_gmail.handler.do_POST(request)
        self.assertEqual(request.status, 200)
        resolve.assert_called_once_with(request.headers, 'gmail-1', require_smtp=True)
        connection.assert_called_once_with('smtp.example.com', 465, 'ssl', 'stored-user', 'stored-password', timeout=30)
        client.sendmail.assert_called_once()
        sender, envelope, delivered = client.sendmail.call_args.args[:3]
        self.assertEqual(sender, 'owner@example.com')
        return envelope, BytesParser(policy=policy.default).parsebytes(delivered), delivered

    def test_smtp_all_seven_combinations_keep_separate_envelope_and_private_mime(self):
        for fields in recipient_cases():
            with self.subTest(fields=fields):
                envelope, message, delivered = self.invoke_smtp(fields)
                self.assertEqual(envelope, [value for value in fields.values() if value])
                for field in ('to', 'cc'):
                    self.assertEqual(addresses(message, field), [fields[field]] if fields[field] else [])
                self.assertIsNone(message['Bcc'])
                if fields['bcc']:
                    self.assertNotIn(fields['bcc'].encode(), delivered)

    def test_smtp_existing_duplicate_envelope_behavior_is_unchanged(self):
        envelope, message, _ = self.invoke_smtp({
            'to': 'to@example.com, to@example.com', 'cc': 'to@example.com',
            'bcc': 'hidden@example.com, hidden@example.com',
        })
        self.assertEqual(envelope, ['to@example.com'] * 3 + ['hidden@example.com'] * 2)
        self.assertEqual(addresses(message, 'To'), ['to@example.com'] * 2)
        self.assertIsNone(message['Bcc'])


if __name__ == '__main__':
    unittest.main()
