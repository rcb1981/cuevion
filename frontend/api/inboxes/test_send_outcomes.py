"""Offline send outcome tests. All provider/SMTP transports are mocked."""
import io
import json
import smtplib
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import test_send_bcc_delivery as bcc

route = bcc.send_gmail
smtp = bcc.smtp_connection


class SendOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.harness = bcc.BccDeliveryTests()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)

    def gmail(self, transport):
        return self.harness.invoke(
            self.harness.payload(to='to@example.com', bcc='hidden@example.com'),
            provider_transport_override=transport,
        )[0]

    def test_gmail_acceptance_and_missing_identity_after_provider_ack_are_successful(self):
        for body in ('{"id":"sent-id","threadId":"thread-id"}', 'invalid-json'):
            with self.subTest(body=body):
                transport = Mock(return_value=bcc.fixtures.BoundaryResponse(body))
                response = self.gmail(transport)
                self.assertEqual(response.status, 200)
                self.assertTrue(response.payload()['ok'])
                transport.assert_called_once()

    def test_gmail_explicit_rejection_is_not_sent_and_never_falls_back(self):
        for status in (400, 403, 429):
            with self.subTest(status=status), patch.object(route, 'send_public_smtp_message') as smtp_send:
                transport = Mock(side_effect=HTTPError('https://gmail.example', status, 'rejected', {}, io.BytesIO()))
                response = self.gmail(transport)
                self.assertFalse(response.payload()['ok'])
                self.assertEqual(response.payload()['sendOutcome'], 'not_sent')
                transport.assert_called_once()
                smtp_send.assert_not_called()

    def test_gmail_lost_acknowledgement_and_server_errors_are_uncertain_without_retry(self):
        errors = [TimeoutError('private'), URLError('private'), ConnectionResetError('private')]
        errors += [HTTPError('https://gmail.example', code, 'private', {}, io.BytesIO()) for code in (408, 500, 502, 503, 504)]
        for error in errors:
            with self.subTest(error=type(error).__name__, code=getattr(error, 'code', None)), patch.object(route, 'refresh_gmail_context') as refresh:
                transport = Mock(side_effect=error)
                response = self.gmail(transport)
                self.assertEqual(response.status, 502)
                self.assertFalse(response.payload()['ok'])
                self.assertEqual(response.payload()['sendOutcome'], 'uncertain')
                self.assertEqual(response.payload()['error']['code'], 'send_uncertain')
                self.assertNotIn('private', json.dumps(response.payload()))
                self.assertNotIn('try again', response.payload()['error']['message'])
                transport.assert_called_once()
                refresh.assert_not_called()

    def test_preflight_validation_is_confirmed_not_sent(self):
        transport = Mock()
        response = self.harness.invoke(self.harness.payload(to='bad-address'), provider_transport_override=transport)[0]
        self.assertEqual(response.status, 400)
        self.assertEqual(response.payload()['sendOutcome'], 'not_sent')
        transport.assert_not_called()

    def test_unexpected_route_failure_cannot_claim_non_delivery(self):
        response = bcc.fixtures.FakeHandler({})
        with patch.object(route, 'read_json_body', side_effect=RuntimeError('private')):
            route.handler.do_POST(response)
        self.assertEqual(response.status, 500)
        self.assertEqual(response.payload()['sendOutcome'], 'uncertain')

    def smtp_send(self, *, error=None, refused=None, connect_error=None):
        client = Mock()
        client.send_message = Mock(side_effect=error, return_value=refused or {})
        @contextmanager
        def connection(*args, **kwargs):
            if connect_error:
                raise connect_error
            yield client
        message = route._build_message({
            'provider': 'custom_imap', 'email': 'sender@example.com',
            'username': 'sender@example.com', 'password': 'synthetic',
            'to': 'to@example.com', 'cc': 'cc@example.com', 'bcc': 'hidden@example.com',
        })[3]
        code = None
        with patch.object(smtp, 'open_authenticated_public_smtp_connection', connection):
            try:
                smtp.send_public_smtp_message('smtp.example.com', 465, 'ssl', 'sender@example.com', 'synthetic', message,
                                              ['to@example.com', 'cc@example.com', 'hidden@example.com'])
            except smtp.SmtpConnectionError as exc:
                code = exc.code
        return code, client, message

    def test_smtp_acceptance_preserves_envelope_and_bcc_privacy(self):
        code, client, message = self.smtp_send()
        self.assertIsNone(code)
        client.send_message.assert_called_once_with(message, to_addrs=['to@example.com', 'cc@example.com', 'hidden@example.com'])
        self.assertIsNone(message['Bcc'])

    def test_smtp_explicit_rejections_are_confirmed_failures(self):
        errors = [smtplib.SMTPRecipientsRefused({'to@example.com': (550, b'private')}),
                  smtplib.SMTPSenderRefused(550, b'private', 'sender@example.com'),
                  smtplib.SMTPDataError(554, b'private'), smtplib.SMTPNotSupportedError('private')]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                code, client, _ = self.smtp_send(error=error)
                self.assertEqual(code, 'smtp_send_failed')
                client.send_message.assert_called_once()

    def test_smtp_connection_or_authentication_failure_precedes_submission(self):
        for code in ('smtp_connection_failed', 'smtp_authentication_failed'):
            actual, client, _ = self.smtp_send(connect_error=smtp.SmtpConnectionError(code))
            self.assertEqual(actual, code)
            client.send_message.assert_not_called()

    def test_smtp_disconnect_timeout_and_partial_acceptance_are_uncertain(self):
        for error in (TimeoutError('private'), smtplib.SMTPServerDisconnected('private'), OSError('private')):
            with self.subTest(error=type(error).__name__):
                code, client, _ = self.smtp_send(error=error)
                self.assertEqual(code, 'smtp_send_uncertain')
                client.send_message.assert_called_once()
        code, client, _ = self.smtp_send(refused={'hidden@example.com': (550, b'private')})
        self.assertEqual(code, 'smtp_send_uncertain')
        client.send_message.assert_called_once()

    def test_smtp_route_propagates_uncertain_and_confirmed_errors(self):
        for code, expected in [('smtp_send_uncertain', 'uncertain'), ('smtp_send_failed', 'not_sent'), ('smtp_authentication_failed', 'not_sent')]:
            response = bcc.fixtures.FakeHandler(self.harness.payload(to='to@example.com', bcc='hidden@example.com'))
            mailbox = {'email': 'sender@example.com', 'smtp': {'host': 'smtp.example.com', 'port': 465, 'security': 'ssl', 'username': 'sender@example.com', 'password': 'synthetic'}}
            with patch.object(route, 'resolve_owned_mailbox', return_value={'status': 'ok', 'inbox': {'provider': 'custom_imap'}}), patch.object(
                route, 'resolve_authenticated_imap_mailbox', return_value={'status': 'ok', 'mailbox': mailbox}
            ), patch.object(route, 'send_public_smtp_message', side_effect=smtp.SmtpConnectionError(code)) as send:
                route.handler.do_POST(response)
            self.assertEqual(response.payload()['sendOutcome'], expected)
            send.assert_called_once()


if __name__ == '__main__':
    unittest.main()
