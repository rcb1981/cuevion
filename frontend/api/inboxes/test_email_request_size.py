"""Synthetic uploads only: complete wire-byte budget and pre-dispatch rejection."""
import base64
import io
import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import test_send_bcc_delivery as bcc
from api.inboxes.email_request_size import MAX_EMAIL_REQUEST_BYTES as LIMIT, EMAIL_REQUEST_TOO_LARGE_MESSAGE
from api.inboxes import test_microsoft_draft_attachment_http as ms

route = bcc.send_gmail


def attachment(n, name='synthetic.bin'):
    return {'name': name, 'mimeType': 'application/octet-stream',
            'contentBase64': base64.b64encode(b'x' * n).decode('ascii')}


def wire(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


class EmailRequestSizeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')))
        self.stack.enter_context(patch('socket.create_connection', side_effect=AssertionError('Network forbidden')))

    def payload(self, files):
        return {'mailboxId': 'synthetic-box', 'to': 'to@example.com', 'cc': 'cc@example.com', 'bcc': 'hidden@example.com',
                'subject': 'Subject', 'bodyText': 'Body and signature', 'bodyHtml': '<p>Body and signature</p>', 'attachments': files}

    def send(self, raw, provider):
        request = bcc.fixtures.FakeHandler(raw_body=raw, headers={'content-length': str(len(raw))})
        mailbox = {'email': 'sender@example.com', 'smtp': {'host': 'smtp.example.com', 'port': 465, 'security': 'ssl', 'username': 'sender@example.com', 'password': 'synthetic'}}
        with patch.object(route, 'resolve_owned_mailbox', return_value={'status': 'ok', 'inbox': {'provider': provider}}) as owned, patch.object(
            route, 'resolve_gmail_context', return_value={'status': 'ok', 'context': {'mailbox_email': 'sender@example.com'}}
        ) as gmail_context, patch.object(route, 'resolve_authenticated_imap_mailbox', return_value={'status': 'ok', 'mailbox': mailbox}) as smtp_context, patch.object(
            route, '_send_with_gmail_oauth', return_value=({'id': 'sent-id', 'threadId': 'thread-id'}, None, None)
        ) as gmail_send, patch.object(route, 'send_public_smtp_message') as smtp_send:
            route.handler.do_POST(request)
        return request, owned, gmail_context, smtp_context, gmail_send, smtp_send

    def test_small_multiple_and_exact_limit_send_once_with_content_and_recipients_intact(self):
        for files in ([attachment(3)], [attachment(1), attachment(2)], [attachment(1_499_000), attachment(1_499_000, 'résumé.bin')]):
            raw = wire(self.payload(files))
            for provider in ('google', 'custom_imap'):
                for bounded in (raw, raw + b' ' * (LIMIT - len(raw))):
                    with self.subTest(provider=provider, files=len(files), size=len(bounded)):
                        request, _, _, _, gmail, smtp = self.send(bounded, provider)
                        self.assertEqual(request.status, 200)
                        transport, other = (gmail, smtp) if provider == 'google' else (smtp, gmail)
                        transport.assert_called_once(); other.assert_not_called()
                        message = transport.call_args.args[1 if provider == 'google' else 5]
                        self.assertEqual([p.get_payload(decode=True) for p in message.iter_attachments()],
                                         [base64.b64decode(f['contentBase64']) for f in files])
                        self.assertEqual(str(message['To']), 'to@example.com')
                        self.assertEqual(str(message['Cc']), 'cc@example.com')
                        if provider == 'google':
                            self.assertEqual(str(message['Bcc']), 'hidden@example.com')
                        else:
                            self.assertIsNone(message['Bcc'])
                            self.assertEqual(transport.call_args.args[6], ['to@example.com', 'cc@example.com', 'hidden@example.com'])

    def test_over_limit_includes_base64_json_text_and_utf8_before_any_provider_access(self):
        cases = [wire(self.payload([attachment(1_500_000), attachment(1_500_000)])),
                 wire({**self.payload([]), 'bodyText': 'é' * 2_000_000}), b'{}' + b' ' * (LIMIT - 1)]
        for raw in cases:
            self.assertGreater(len(raw), LIMIT)
            for provider in ('google', 'custom_imap'):
                request, *calls = self.send(raw, provider)
                self.assertEqual(request.status, 413)
                self.assertEqual(request.payload(), {'ok': False, 'sendOutcome': 'not_sent', 'error': {
                    'code': 'request_too_large', 'message': EMAIL_REQUEST_TOO_LARGE_MESSAGE}})
                for call in calls:
                    call.assert_not_called()

    def test_oversized_body_not_read_or_decoded(self):
        request = bcc.fixtures.FakeHandler(raw_body=b'{}', headers={'content-length': str(LIMIT + 1)})
        request.rfile = None
        route.handler.do_POST(request)
        self.assertEqual(request.status, 413)


class MicrosoftRequestSizeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ms.AttachmentHTTPTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        p = patch('socket.socket.connect', side_effect=AssertionError('Network forbidden'))
        p.start(); self.addCleanup(p.stop)

    def test_gated_upload_exact_limit_and_multiple_attachments(self):
        files = [{'name': f'{n}.bin', 'contentType': 'application/octet-stream', 'base64': attachment(1_499_000)['contentBase64']} for n in range(2)]
        data = ms.payload(files=files)
        raw = wire(data)
        request = ms._request(data, header_pairs=ms.ORIGIN)
        request.headers.replace_header('Host', ms.HOST)
        request.headers.replace_header('Content-Length', str(LIMIT))
        request.rfile = io.BytesIO(raw + b' ' * (LIMIT - len(raw)))
        expected = {'requestId': ms.OP, 'providerMessageId': 'draft-1', 'outcome': 'confirmed_performed', 'count': 2, 'completed': 2, 'blocked': False}
        with patch.object(ms.executor, 'execute_owned_attachment_request', return_value=expected) as execute:
            self.assertEqual(ms.http.microsoft_draft_attachment_response(request).status, 200)
            execute.assert_called_once()
            self.assertEqual([len(f['content']) for f in execute.call_args.kwargs['files']], [1_499_000] * 2)

    def test_encoded_overflow_rejected_before_owner_or_executor(self):
        files = [{'name': f'{n}.bin', 'contentType': 'application/octet-stream', 'base64': attachment(1_500_000)['contentBase64']} for n in range(2)]
        with patch.object(ms.owned, 'resolve_owned_microsoft_mailbox') as owned, patch.object(ms.executor, 'execute_owned_attachment_request') as execute:
            result = self.fixture.call(ms.payload(files=files))
            self.assertEqual(result.status, 413)
            body = ms._body(result)
            self.assertEqual(body['error']['message'], EMAIL_REQUEST_TOO_LARGE_MESSAGE)
            self.assertEqual(body['outcome'], 'confirmed_not_performed')
            self.assertFalse(body['blocked'])
            owned.assert_not_called(); execute.assert_not_called()

    def test_gate_still_precedes_size_validation(self):
        with patch.object(ms.policy, '_DRAFT_ATTACHMENTS_RELEASED', False), patch.object(ms.executor, 'execute_owned_attachment_request') as execute:
            request = ms._request(ms.payload(), header_pairs=ms.ORIGIN)
            request.headers.replace_header('Content-Length', str(LIMIT + 1))
            request.rfile = None
            self.assertEqual(ms.http.microsoft_draft_attachment_response(request).status, 403)
            execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
