"""Synthetic protocol/HTTP tests. Network access is forbidden in this suite."""
import importlib
from copy import deepcopy
from dataclasses import replace
import imaplib
import io
import json
import socket
import ssl
import sys
import time
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from api.inboxes import imap_sent as discovery, imap_sent_snapshot as reader
from api.inboxes.imap_network_policy import _PreconnectedIMAP4SSL
from api.inboxes import imap_sent_preferences as preferences
from api.auth.runtime import AuthenticatedMemberContext
import imap_connect_preview as preview
route = importlib.import_module('api.inboxes.fetch-imap-sent')
attachment = importlib.import_module('api.inboxes.download-attachment')


def mail(uid=1, message_id=None, references=None, attachment=False):
    m = EmailMessage()
    m['From'], m['To'], m['Subject'] = 'self@example.test', 'other@example.test', 'Same subject'
    m['Date'], m['Message-ID'] = 'Wed, 18 Nov 2026 10:00:00 +0000', message_id or f'<{uid}@example.test>'
    if references:
        m['References'] = references
    m.set_content('Synthetic message ' + str(uid))
    if attachment:
        m.add_attachment(b'synthetic attachment', maintype='application', subtype='octet-stream', filename='fixture.bin')
    return m.as_bytes()


class Mailbox:
    def __init__(self, rows=None, folders=None, special=True, ceiling=None):
        self.rows = rows if rows is not None else {n: mail(n) for n in range(1, 26)}
        self.folders = folders if folders is not None else [b'(\\Sent) "/" "Sent Messages"']
        self.capabilities = (b'IMAP4rev1', b'SPECIAL-USE') if special else (b'IMAP4rev1',)
        self.uv, self.ceiling = '101', ceiling if ceiling is not None else max(self.rows, default=0)
        self.calls, self.sock, self.file = [], Mock(), io.BytesIO()
        self.flags, self.disappear, self.bad_uid, self.bad_literal, self.size_override = {}, set(), False, False, None
        self.select_count, self.changed_uv = 0, False

    def list(self, *args):
        self.calls.append(('LIST', *args)); return 'OK', self.folders

    def _simple_command(self, command, *args):
        assert (command, *args) == ('LIST', '""', '"*"', 'RETURN', '(SPECIAL-USE)')
        self.calls.append((command, *args)); return 'OK', [b'complete']

    def _untagged_response(self, status, data, name):
        assert name == 'LIST'
        return status, self.folders

    def capability(self):
        self.calls.append(('CAPABILITY',)); return 'OK', [b' '.join(self.capabilities)]

    def select(self, folder, readonly=False):
        if readonly is not True:
            raise AssertionError('writable selection')
        self.select_count += 1
        self.calls.append(('EXAMINE', folder))
        return 'OK', [str(len(self.rows)).encode()]

    def response(self, name):
        if name == 'CAPABILITY':
            return name, [None]
        if name == 'UIDVALIDITY':
            return name, [str(int(self.uv) + (1 if self.changed_uv and self.select_count > 1 else 0)).encode()]
        if name == 'UIDNEXT':
            return name, [str(self.ceiling + 1).encode()]
        raise AssertionError(name)

    def uid(self, command, *args):
        self.calls.append((command, *args))
        if command == 'SEARCH':
            assert args[0:2] == (None, 'UID')
            low, high = map(int, args[2].split(':'))
            assert 0 < low <= high and high - low < reader.WINDOW
            return 'OK', [' '.join(str(u) for u in sorted(self.rows) if low <= u <= high).encode()]
        if command != 'FETCH':
            raise AssertionError('mutating command ' + command)
        uid, fields = int(args[0]), args[1]
        assert 'BODY.PEEK[' in fields
        if uid in self.disappear or uid not in self.rows:
            return 'OK', [None]
        full = self.rows[uid]
        raw = full.split(b'\n\n')[0] + b'\n\n' if 'HEADER' in fields else full
        metadata = f'1 (UID {uid + 1 if self.bad_uid else uid}'
        if 'HEADER' in fields:
            metadata += f' FLAGS ({self.flags.get(uid, "")}) RFC822.SIZE {self.size_override if self.size_override is not None else len(full)} INTERNALDATE "18-Nov-2026 09:00:00 +0000" BODY[HEADER]<0>'
        else:
            metadata += ' BODY[]<0>'
        metadata += f' {{{len(raw) + int(self.bad_literal)}}}'
        return 'OK', [(metadata.encode(), raw), b')']

    def read(self, size): return b'x' * size
    def readline(self): return b'line\r\n'
    def logout(self): self.calls.append(('LOGOUT',))
    def shutdown(self): self.calls.append(('SHUTDOWN',))


class Handler:
    def __init__(self, payload):
        raw = json.dumps(payload).encode()
        self.headers, self.rfile, self.wfile = {'content-length': str(len(raw))}, io.BytesIO(raw), io.BytesIO()
        self.response_headers, self.status, self.command = [], None, 'POST'
    def send_response(self, status): self.status = status
    def send_header(self, k, v): self.response_headers.append((k, v))
    def end_headers(self): pass
    def _handle_post(self): route.handler._handle_post(self)
    def body(self): return json.loads(self.wfile.getvalue())


CONTEXT = {'mailboxId': 'box', 'ownerEmail': 'owner@example.test', 'email': 'self@example.test',
           'imap': {'host': 'imap.example.invalid', 'port': 993, 'ssl': True, 'username': 'self', 'password': 'fixture-secret'}}
with patch.object(reader, '_resolve_encryption_key', return_value=(b'k' * 32, None)):
    SCOPE, KEY = reader.connection_scope(CONTEXT, 'generation-1')

MEMBER = AuthenticatedMemberContext(user_id='user-1', workspace_id='workspace-1', email=CONTEXT['ownerEmail'], name='Fixture', membership_role='owner')
OWNED = {'status': 'ok', 'memberAuthority': MEMBER, 'user': {'email': CONTEXT['ownerEmail']},
         'inbox': {'id': 'box', 'credentialVersion': 'generation-1'},
         'config': {'v': 1, 'email': CONTEXT['ownerEmail'], 'managedInboxes': [{'id': 'box', 'credentialVersion': 'generation-1'}]}}


class SyntheticOnly(unittest.TestCase):
    def setUp(self):
        key = patch.object(reader, '_resolve_encryption_key', return_value=(b'k' * 32, None))
        key.start(); self.addCleanup(key.stop)
        store = patch.object(preferences.config_store, 'resolve_user_config_store', return_value=(None, {'code': 'offline'}))
        store.start(); self.addCleanup(store.stop)
        connect = patch.object(socket.socket, 'connect', side_effect=AssertionError('real network forbidden'))
        connect.start(); self.addCleanup(connect.stop)
        for name in ('create_connection', 'getaddrinfo'):
            p = patch.object(socket, name, side_effect=AssertionError('real network forbidden'))
            p.start(); self.addCleanup(p.stop)

    def read(self, mailbox=None, **kwargs):
        return reader.read_page(mailbox or Mailbox(), 'box', 'Sent Messages', SCOPE, KEY, **kwargs)


class DiscoveryTests(SyntheticOnly):
    def test_fresh_capability_validation_still_fails_closed(self):
        valid = b'IMAP4rev1 SPECIAL-USE'
        for status, data in [('NO', [valid]), ('BAD', [valid]), ('OK', None),
                             ('OK', []), ('OK', [None]), ('OK', ['IMAP4rev1']),
                             ('OK', [valid, valid]), ('OK', [b'x' * 16385]),
                             ('OK', [b'IMAP4rev1 \xff'])]:
            with self.subTest(status=status, data=data):
                m = Mailbox()
                with patch.object(m, 'capability', return_value=(status, data)), \
                        self.assertRaises(discovery.SentError) as raised:
                    discovery.discover_sent(m)
                self.assertEqual(raised.exception.code, 'sent_inventory_unavailable')
                self.assertEqual(raised.exception.message, 'IMAP folder capabilities could not be verified.')
                self.assertEqual(raised.exception.status, 502)
                self.assertEqual(m.calls, [])  # No LIST or mailbox read after rejection.

    def test_capability_case_and_unknown_extensions_preserve_special_use(self):
        m = Mailbox()
        m.capabilities = (b'imap4rev1', b'special-use', b'AUTH=PLAIN', b'X-SYNTHETIC')
        self.assertEqual(discovery.discover_sent(m)['providerFolder'], 'Sent Messages')
        self.assertEqual(m.calls[-1], ('LIST', '""', '"*"', 'RETURN', '(SPECIAL-USE)'))

    def test_special_use_icloud_dovecot_and_generic(self):
        for folder in ['Sent Messages', 'INBOX.Sent', 'Sent Items']:
            m = Mailbox(folders=[f'(\\Sent) "." "{folder}"'.encode()])
            result = discovery.discover_sent(m)
            self.assertEqual(result['providerFolder'], folder)
            self.assertEqual(m.calls, [('CAPABILITY',), ('LIST', '""', '"*"', 'RETURN', '(SPECIAL-USE)')])

    def test_names_are_only_suggestions_and_choice_is_exact(self):
        m = Mailbox(special=False, folders=[b'() "." "INBOX.Sent"', b'() "/" "Sent Items"', b'() "/" "INBOX"'])
        result = discovery.discover_sent(m)
        self.assertEqual(result['mode'], 'choose')
        self.assertTrue(all(x['suggested'] for x in result['folders']))
        self.assertEqual(discovery.discover_sent(m, 'INBOX.Sent')['providerFolder'], 'INBOX.Sent')
        with self.assertRaises(discovery.SentError): discovery.discover_sent(m, 'inbox.sent')

    def test_multiple_special_roles_need_choice(self):
        m = Mailbox(folders=[b'(\\Sent) "/" "Sent"', b'(\\Sent) "/" "Other Sent"', b'() "/" "Other"'])
        self.assertEqual(discovery.discover_sent(m)['mode'], 'choose')
        with self.assertRaises(discovery.SentError): discovery.discover_sent(m, 'Other')

    def test_wire_encoding_quotes_literals_and_delimiters(self):
        name = 'Team "Sent"\\2026'
        wire = name.encode()
        m = Mailbox(folders=[(f'(\\Sent) NIL {{{len(wire)}}}'.encode(), wire), b''])
        result = discovery.discover_sent(m)
        self.assertEqual(result['providerFolder'], name)
        self.assertEqual(discovery.quote_folder(name), '"Team \\"Sent\\"\\\\2026"')
        self.assertEqual(discovery.folder_label('&AMk-l&AOk-ments &- Sent'), 'Éléments & Sent')
        self.assertEqual(discovery.folder_label('&AAk-Sent'), ' Sent')
        self.assertEqual(discovery.discover_sent(Mailbox(folders=[b'(\\Sent) NIL &AMk-l&AOk-ments']))['providerFolder'], '&AMk-l&AOk-ments')

    def test_unusable_missing_malformed_and_duplicate_inventory(self):
        self.assertEqual(discovery.discover_sent(Mailbox(folders=[]))['mode'], 'unavailable')
        for flag in ['\\Noselect', '\\NonExistent', '\\Trash', '\\Junk', '\\Drafts', '\\All', '\\Archive']:
            self.assertEqual(discovery.discover_sent(Mailbox(folders=[f'(\\Sent {flag}) "/" "Sent"'.encode()]))['mode'], 'unavailable')
        for folders in [[b'malformed'], [b'() "/" "Sent"'] * 2]:
            with self.assertRaises(discovery.SentError): discovery.discover_sent(Mailbox(folders=folders))


class ReaderTests(SyntheticOnly):
    def test_cursor_signing_uses_server_secret_and_fails_closed(self):
        token = self.read()['nextPageToken']
        with patch.object(reader, '_resolve_encryption_key', return_value=(b'z' * 32, None)):
            scope, key = reader.connection_scope(CONTEXT, 'generation-1')
        self.assertNotEqual(scope, SCOPE)
        with self.assertRaises(discovery.SentError): reader.decode_cursor(token, key, scope)
        with patch.object(reader, '_resolve_encryption_key', return_value=(None, {'code': 'missing'})):
            with self.assertRaises(discovery.SentError): reader.connection_scope(CONTEXT, 'generation-1')

    def test_uid_pages_and_readonly_commands(self):
        m = Mailbox(); first = self.read(m)
        self.assertEqual([int(x['imapUid']) for x in first['messages']], list(range(25, 5, -1)))
        cursor = reader.decode_cursor(first['nextPageToken'], KEY, SCOPE)
        second = self.read(m, cursor=cursor)
        self.assertEqual([x['imapUid'] for x in second['messages']], ['5', '4', '3', '2', '1'])
        self.assertIsNone(second['nextPageToken'])
        self.assertTrue(all(c[0] in {'EXAMINE', 'SEARCH', 'FETCH'} for c in m.calls))
        self.assertTrue(all(c[1] == '"Sent Messages"' for c in m.calls if c[0] == 'EXAMINE'))
        self.assertTrue(first['messages'][0]['unread'])

    def test_sparse_empty_window_keeps_progress(self):
        m = Mailbox(rows={1: mail()}, ceiling=10000)
        p = self.read(m)
        self.assertEqual(p['messages'], []); self.assertIsNotNone(p['nextPageToken'])
        seen = set()
        while p['nextPageToken']:
            self.assertNotIn(p['nextPageToken'], seen); seen.add(p['nextPageToken'])
            p = self.read(m, cursor=reader.decode_cursor(p['nextPageToken'], KEY, SCOPE))
        self.assertEqual(p['messages'][0]['imapUid'], '1')
        self.assertEqual(self.read(Mailbox(rows={}))['messages'], [])

    def test_new_messages_expunge_and_deleted_flags(self):
        m = Mailbox(); p = self.read(m)
        m.rows[26] = mail(26); m.ceiling = 26; m.disappear.add(3); m.flags[4] = '\\Deleted'; m.flags[2] = '\\Draft'
        p = self.read(m, cursor=reader.decode_cursor(p['nextPageToken'], KEY, SCOPE))
        self.assertEqual([x['imapUid'] for x in p['messages']], ['5', '1'])

    def test_uidvalidity_changes_before_and_during_read(self):
        m = Mailbox(); p = self.read(m); cursor = reader.decode_cursor(p['nextPageToken'], KEY, SCOPE)
        m.uv = '102'
        with self.assertRaisesRegex(discovery.SentError, 'Sent changed'): self.read(m, cursor=cursor)
        m = Mailbox(); m.changed_uv = True
        with self.assertRaisesRegex(discovery.SentError, 'Sent changed'): self.read(m)
        m = Mailbox(); m.uv = '0'
        with self.assertRaises(discovery.SentError): self.read(m)

    def test_cursor_signature_expiry_scope_generation_and_bounds(self):
        p = self.read(); t = p['nextPageToken']; cursor = reader.decode_cursor(t, KEY, SCOPE)
        for value in [t[:-1], 'x', '!' * 100, t + 'x']:
            with self.assertRaises(discovery.SentError): reader.decode_cursor(value, KEY, SCOPE)
        for field, value in [('before', 0), ('before', cursor['ceiling']), ('expires', 0), ('scope', 'wrong')]:
            with self.assertRaises(discovery.SentError): reader.decode_cursor(reader.encode_cursor({**cursor, field: value}, KEY), KEY, SCOPE)
        for context, generation in [(CONTEXT, 'generation-2'), ({**CONTEXT, 'ownerEmail': 'other@example.test'}, 'generation-1')]:
            scope, key = reader.connection_scope(context, generation)
            with self.assertRaises(discovery.SentError): reader.decode_cursor(t, key, scope)

    def test_oversized_and_malformed_mime_remain_visible(self):
        m = Mailbox(rows={1: mail()}); m.size_override = reader.MAX_BODY + 1
        row = self.read(m)['messages'][0]
        self.assertIn('too large', row['body'][0]); self.assertEqual(row['attachments'], [])
        self.assertFalse(any('BODY.PEEK[]' in str(c) for c in m.calls))
        row = self.read(Mailbox(rows={1: b'not a valid message'}))['messages'][0]
        self.assertIn('could not be displayed', row['body'][0])
        for field in ['bad_uid', 'bad_literal']:
            m = Mailbox(rows={1: mail()}); setattr(m, field, True)
            with self.assertRaises(discovery.SentError): self.read(m)

    def test_threads_duplicates_and_attachments_do_not_classify(self):
        import imap_connect_preview as mime
        m = Mailbox(rows={1: mail(1, '<root@example.test>'), 2: mail(2, references='<root@example.test>', attachment=True), 3: mail(3, '<duplicate@example.test>'), 4: mail(4, '<duplicate@example.test>')})
        with patch.object(mime, 'to_message_preview', side_effect=AssertionError('classification')), patch.object(mime, 'build_priority_candidate_render_source', side_effect=AssertionError('priority')):
            p = self.read(m)
        rows = {x['imapUid']: x for x in p['messages']}
        self.assertEqual(rows['1']['threadId'], rows['2']['threadId'])
        self.assertNotEqual(rows['3']['threadId'], rows['4']['threadId'])
        self.assertEqual(rows['2']['attachments'][0]['name'], 'fixture.bin')
        self.assertEqual(len(rows), 4)

    def test_transport_and_execution_budget(self):
        m = Mailbox(); budget = reader.ReadBudget(m, time.monotonic() + 10)
        with self.assertRaises(discovery.SentError): m.read(reader.MAX_BODY + 2)
        budget.deadline = 0
        with self.assertRaises(discovery.SentError): m.readline()
        with self.assertRaises(discovery.SentError): self.read(check=budget.check)

    def test_chunked_wire_reads_preserve_buffering_and_bound_slow_servers(self):
        m = Mailbox(); m.file = io.BytesIO(b'header\r\nabcde)\r\nnext\r\n')
        budget = reader.ReadBudget(m, time.monotonic() + 10)
        self.assertEqual(m.readline(), b'header\r\n')
        self.assertEqual(m.read(5), b'abcde')
        self.assertEqual(m.readline(), b')\r\n')
        self.assertEqual(m.readline(), b'next\r\n')
        self.assertEqual(m.readline(), b'')
        for line in [b'x' * 65537, b'x' * 65536 + b'\n']:
            m = Mailbox(); m.file = io.BytesIO(line)
            reader.ReadBudget(m, time.monotonic() + 10)
            with self.assertRaises(discovery.SentError): m.readline()
        m = Mailbox(); m.file = Mock(); m.file.read1.return_value = b'x'
        with patch.object(reader.time, 'monotonic', side_effect=range(20)):
            reader.ReadBudget(m, 5)
            with self.assertRaisesRegex(discovery.SentError, 'too long'): m.read(100)
        self.assertLess(m.file.read1.call_count, 5)
        m = Mailbox(); m.file = None
        with self.assertRaises(discovery.SentError): reader.ReadBudget(m, time.monotonic() + 10)
        m = Mailbox(); m._readbuf = [b'header\r\nab']; m.sock.recv.side_effect = io.BytesIO(b'cde)\r\n').read
        reader.ReadBudget(m, time.monotonic() + 10)
        self.assertEqual(m.readline(), b'header\r\n')
        self.assertEqual(m.read(5), b'abcde')
        self.assertEqual(m.readline(), b')\r\n')
        self.assertEqual(m._readbuf, [])

    def test_real_imaplib_parser_on_in_memory_protocol_transcript(self):
        raw = mail(1); header = raw.split(b'\n\n')[0] + b'\n\n'
        def examined(tag):
            return b'* 1 EXISTS\r\n* OK [UIDVALIDITY 101] stable\r\n* OK [UIDNEXT 2] next\r\n' + tag + b' OK examined\r\n'
        wire = (b'* PREAUTH IMAP4rev1\r\n* CAPABILITY IMAP4rev1 SPECIAL-USE\r\nT0 OK capability\r\n'
                b'* CAPABILITY IMAP4rev1 SPECIAL-USE\r\nT1 OK capability\r\n'
                b'* LIST (\\Sent) "/" "Sent Messages"\r\nT2 OK list\r\n' + examined(b'T3') +
                b'* SEARCH 1\r\nT4 OK search\r\n' +
                f'* 1 FETCH (UID 1 FLAGS () RFC822.SIZE {len(raw)} INTERNALDATE "18-Nov-2026 09:00:00 +0000" BODY[HEADER]<0> {{{len(header)}}}\r\n'.encode() + header + b')\r\nT5 OK fetched\r\n' +
                f'* 1 FETCH (UID 1 BODY[]<0> {{{len(raw)}}}\r\n'.encode() + raw + b')\r\nT6 OK fetched\r\n' + examined(b'T7'))
        sock = Mock(); sock.makefile.return_value = io.BytesIO(wire)
        with patch.object(imaplib.IMAP4, '_create_socket', return_value=sock), patch.object(imaplib, 'Int2AP', return_value=b'T'):
            m = imaplib.IMAP4('synthetic.invalid')
        reader.ReadBudget(m, time.monotonic() + 10)
        self.assertEqual(discovery.discover_sent(m)['providerFolder'], 'Sent Messages')
        self.assertEqual(self.read(m)['messages'][0]['imapUid'], '1')
        commands = [c.args[0] for c in sock.sendall.call_args_list]
        self.assertIn(b'T2 LIST "" "*" RETURN (SPECIAL-USE)\r\n', commands)
        self.assertIn(b'T3 EXAMINE "Sent Messages"\r\n', commands)
        self.assertTrue(all(b'BODY.PEEK[' in c for c in commands if b' FETCH ' in c))


class RouteTests(SyntheticOnly):
    def invoke(self, payload, contexts=None, mailbox=None):
        h = Handler(payload); m = mailbox or Mailbox()
        with patch.object(route, 'resolve_context', side_effect=contexts or [(CONTEXT, SCOPE, KEY, OWNED)] * 2) as auth, patch.object(route, 'connect_mailbox_with_settings', return_value=m) as connect:
            route.handler.do_POST(h)
        return h, m, auth, connect

    def test_authenticated_endpoint_boundaries_and_cleanup(self):
        h, m, auth, connect = self.invoke({'mailboxId': 'box'})
        self.assertEqual(h.status, 200, h.body())
        self.assertIn(('Cache-Control', 'no-store'), h.response_headers)
        self.assertEqual(auth.call_count, 2)
        self.assertEqual(connect.call_args.kwargs['host'], 'imap.example.invalid')
        self.assertNotIn('fixture-secret', json.dumps(h.body()))
        self.assertEqual(m.calls[-1][0], 'LOGOUT')

    def test_login_capability_cache_does_not_break_fresh_discovery(self):
        # Use the production TLS IMAP class and real login/parser, without sockets.
        # LOGIN's optional capability response is cached separately by imaplib.
        automatic = b'* CAPABILITY IMAP4rev1 SPECIAL-USE\r\n'
        for unsolicited, cached, special in [
                (b'', None, True), (b'', b'IMAP4rev1 SPECIAL-USE', True),
                (automatic, None, True), (automatic, b'IMAP4rev1 SPECIAL-USE', True),
                (b'', b'IMAP4rev1', True), (b'', b'IMAP4rev1 SPECIAL-USE', False),
                (automatic, b'IMAP4rev1 SPECIAL-USE', False)]:
            with self.subTest(unsolicited=bool(unsolicited), cached=cached, special=special):
                capabilities = b'IMAP4rev1 SPECIAL-USE' if special else b'IMAP4rev1'
                login_code = b'[CAPABILITY ' + cached + b'] ' if cached else b''
                listing = b'(\\Sent)' if special else b'()'
                def examined(tag):
                    return (b'* 0 EXISTS\r\n* OK [UIDVALIDITY 101] stable\r\n'
                            b'* OK [UIDNEXT 1] next\r\n' + tag + b' OK [READ-ONLY] examined\r\n')
                wire = (b'* OK synthetic ready\r\n* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\nT0 OK capability\r\n'
                        + unsolicited + b'T1 OK ' + login_code + b'authenticated\r\n'
                        b'* CAPABILITY ' + capabilities + b'\r\nT2 OK capability\r\n'
                        b'* LIST ' + listing + b' "/" "Sent Messages"\r\nT3 OK list\r\n')
                wire += examined(b'T4')
                if special:
                    wire += examined(b'T5')
                wire += b'* BYE logout\r\n' + (b'T6' if special else b'T5') + b' OK logout\r\n'
                sock = Mock(); sock.makefile.return_value = io.BufferedReader(io.BytesIO(wire))
                with patch.object(imaplib, 'Int2AP', return_value=b'T'):
                    mailbox = _PreconnectedIMAP4SSL('synthetic.invalid', 993, sock,
                        ssl_context=ssl.create_default_context(), timeout=5)
                h = Handler({'mailboxId': 'box'})
                with patch.object(preview, 'open_public_imap_connection', return_value=mailbox), \
                        patch.object(route, 'resolve_context', return_value=(CONTEXT, SCOPE, KEY, OWNED)):
                    route.handler.do_POST(h)
                self.assertEqual(h.status, 200, h.body())
                self.assertEqual(h.body()['discovery']['mode'], 'ready' if special else 'choose')
                commands = [c.args[0] for c in sock.sendall.call_args_list]
                expected_list = b'T3 LIST "" "*"' + (b' RETURN (SPECIAL-USE)' if special else b'') + b'\r\n'
                self.assertIn(expected_list, commands)
                self.assertEqual([c.split()[1] for c in commands],
                    [b'CAPABILITY', b'LOGIN', b'CAPABILITY', b'LIST']
                    + ([b'EXAMINE', b'EXAMINE'] if special else [b'EXAMINE']) + [b'LOGOUT'])

    def test_untrusted_request_fields_never_connect(self):
        for extra in [{'host': 'bad'}, {'password': 'bad'}, {'provider': 'google'}, {'limit': True}, {'limit': 21}, {'limit': 0}, {'folderChoice': None}, {'folderChoice': 'bad\r\n'}, {'pageToken': None}]:
            h, _, auth, connect = self.invoke({'mailboxId': 'box', **extra})
            self.assertEqual(h.status, 400, extra); auth.assert_not_called(); connect.assert_not_called()

    def test_ownership_change_and_failure_never_publish_history(self):
        h, m, _, _ = self.invoke({'mailboxId': 'box'}, [(CONTEXT, SCOPE, KEY, OWNED), (CONTEXT, 'changed', KEY, OWNED)])
        self.assertEqual(h.status, 409); self.assertNotIn('messages', json.dumps(h.body()))
        self.assertEqual(m.calls[-1][0], 'LOGOUT')
        h, _, _, connect = self.invoke({'mailboxId': 'box'}, [discovery.SentError('unauthorized', 'Sign in.', 401)])
        self.assertEqual(h.status, 401); connect.assert_not_called()

    def test_workspace_change_during_read_never_publishes_or_saves(self):
        changed = {**OWNED, 'memberAuthority': replace(MEMBER, workspace_id='other-workspace')}
        with patch.object(route, 'remember_folder') as save:
            h, m, _, _ = self.invoke({'mailboxId': 'box'},
                [(CONTEXT, SCOPE, KEY, OWNED), (CONTEXT, SCOPE, KEY, changed)])
        self.assertEqual(h.status, 409)
        self.assertNotIn('messages', json.dumps(h.body()))
        self.assertEqual(m.calls[-1][0], 'LOGOUT'); save.assert_not_called()

    def test_choice_without_persisting_and_response_limit(self):
        m = Mailbox(rows={}, special=False, folders=[b'() "/" "Sent"'])
        h, _, _, _ = self.invoke({'mailboxId': 'box'}, mailbox=m)
        self.assertEqual(h.body()['discovery']['mode'], 'choose'); self.assertIsNone(h.body()['page'])
        h, _, _, _ = self.invoke({'mailboxId': 'box', 'folderChoice': {'providerFolder': 'Sent', 'scope': SCOPE}}, mailbox=m)
        self.assertEqual(h.body()['page']['providerFolder'], 'Sent')
        h, _, _, connect = self.invoke({'mailboxId': 'box', 'folderChoice': {'providerFolder': 'Sent', 'scope': 'old-connection'}}, mailbox=m)
        self.assertEqual(h.status, 409); connect.assert_not_called()
        with patch.object(route, 'MAX_RESPONSE', 1):
            h, _, _, _ = self.invoke({'mailboxId': 'box'})
        self.assertEqual(h.status, 502)

    def test_real_boundary_generation_is_read_without_schema_changes(self):
        owned = deepcopy(OWNED)
        with patch.object(route, 'resolve_owned_managed_inbox_record', return_value=owned), patch.object(route, 'resolve_authenticated_imap_mailbox', return_value={'status': 'ok', 'mailbox': CONTEXT}) as auth:
            context, scope, key, authority = route.resolve_context({}, 'box')
        self.assertEqual((context, scope, key, authority), (CONTEXT, SCOPE, KEY, OWNED)); auth.assert_called_once_with({}, 'box')

    def test_attachment_folder_is_quoted_without_identity_changes(self):
        raw = mail(1, attachment=True)
        for folder in ['Sent Messages', 'Team "Sent"\\2026', 'INBOX.Sent', '&AMk-l&AOk-ments']:
            m = Mock(); m.select.return_value = ('OK', [b'1']); m.uid.return_value = ('OK', [(b'1 (BODY[] {1}', raw), b')'])
            h = Handler({})
            with patch.object(attachment, 'resolve_authenticated_imap_mailbox', return_value={'status': 'ok', 'mailbox': CONTEXT}), patch.object(attachment, 'connect_mailbox_with_settings', return_value=m), patch.object(attachment, '_read_uid_validity', return_value='101'), patch.object(attachment, '_binary_response') as output:
                attachment._download_imap_attachment(h, {'mailboxId': 'box', 'folder': folder, 'uid': '1', 'uidValidity': '101', 'attachmentId': 'attachment-2'})
            m.select.assert_called_once_with(discovery.quote_folder(folder), readonly=True)
            m.uid.assert_called_once_with('fetch', '1', '(BODY.PEEK[])')
            output.assert_called_once()


class IntelligentDiscoveryTests(SyntheticOnly):
    def mailbox(self, counts, special=False):
        folders = [f'({chr(92) + "Sent" if special else ""}) "/" {discovery.quote_folder(name)}'.encode() for name in counts]
        m = Mailbox(folders=folders, special=special)
        def select(folder, readonly=False):
            self.assertTrue(readonly)
            m.calls.append(('EXAMINE', folder))
            name = next(n for n in counts if discovery.quote_folder(n) == folder)
            value = counts[name]
            if isinstance(value, Exception): raise value
            return value if isinstance(value, tuple) else ('OK', [str(value).encode()])
        m.select = select
        return m

    def test_one_populated_candidate_and_empty_alternatives(self):
        m = self.mailbox({'Sent': 0, 'Sent Messages': 19})
        self.assertEqual(discovery.discover_sent(m)['providerFolder'], 'Sent Messages')
        self.assertEqual([c[0] for c in m.calls], ['CAPABILITY', 'LIST', 'EXAMINE', 'EXAMINE'])
        m = self.mailbox({'Enviados': 19, 'Verzonden': 0}, special=True)
        self.assertEqual(discovery.discover_sent(m)['providerFolder'], 'Enviados')

    def test_ambiguous_empty_unknown_and_malformed_counts(self):
        for counts in [{'Sent': 1, 'Sent Messages': 19}, {'Sent': 0, 'Sent Messages': 0},
                       {'Sent': 0, 'Sent Messages': ('NO', [b'unsupported'])},
                       {'Sent': 0, 'Sent Messages': ('OK', [None])},
                       {'Sent': 0, 'Sent Messages': ('OK', [b'19', b'0'])},
                       {'Sent': 0, 'Sent Messages': ('OK', [b'-1'])},
                       {'Sent': 0, 'Sent Messages': imaplib.IMAP4.error('unsupported')},
                       {'Sent': 0, 'Sent Messages': TimeoutError()}]:
            with self.subTest(counts=counts):
                result = discovery.discover_sent(self.mailbox(counts))
                self.assertEqual(result['mode'], 'choose'); self.assertIsNone(result['providerFolder'])
        self.assertEqual(discovery.discover_sent(self.mailbox({'Projects': 19}))['mode'], 'choose')

    def test_authority_saved_choice_and_explicit_choice_skip_counts(self):
        for kwargs in [{'choice': 'Sent'}, {'remembered': 'Sent'}]:
            m = self.mailbox({'Sent': 0, 'Sent Messages': 19})
            self.assertEqual(discovery.discover_sent(m, **kwargs)['providerFolder'], 'Sent')
            self.assertFalse(any(c[0] == 'EXAMINE' for c in m.calls))
        m = self.mailbox({'Envoyes': 0}, special=True)
        self.assertEqual(discovery.discover_sent(m)['providerFolder'], 'Envoyes')
        self.assertFalse(any(c[0] == 'EXAMINE' for c in m.calls))
        m = self.mailbox({'Sent': 19})
        self.assertEqual(discovery.discover_sent(m, remembered='Deleted folder')['mode'], 'choose')
        self.assertFalse(any(c[0] == 'EXAMINE' for c in m.calls))

    def test_count_probe_bound_and_exact_folder_quoting(self):
        m = self.mailbox({f'Sent {n}': 0 for n in range(5)}, special=True)
        self.assertEqual(discovery.discover_sent(m)['mode'], 'choose')
        self.assertFalse(any(c[0] == 'EXAMINE' for c in m.calls))
        m = self.mailbox({'Team "Sent"\\2026': 19, 'Other Sent': 0}, special=True)
        self.assertEqual(discovery.discover_sent(m)['providerFolder'], 'Team "Sent"\\2026')
        with self.assertRaises(discovery.SentError):
            discovery.discover_sent(self.mailbox({'Sent': 0}), check=lambda: (_ for _ in ()).throw(discovery.SentError('sent_timeout', 'deadline')))


class PreferenceTests(SyntheticOnly):
    def test_conditional_save_reload_login_and_no_repeated_write(self):
        owned = deepcopy(OWNED); persisted = {}
        def save(store, owner, expected, replacement):
            self.assertEqual(owner, MEMBER.email)
            self.assertEqual(expected, OWNED['config'])
            persisted.update(deepcopy(replacement))
            return {'status': 'ok'}
        with patch.object(preferences.config_store, 'resolve_user_config_store', return_value=({'fixture': True}, None)), \
             patch.object(preferences.config_store, 'write_user_config_record_if_unchanged', side_effect=save) as write:
            self.assertTrue(preferences.remember_folder(owned, 'box', SCOPE, 'Team "Sent"\\2026'))
            reloaded = deepcopy(OWNED); reloaded['config'] = deepcopy(persisted)
            reloaded['memberAuthority'] = replace(MEMBER)  # New login, same authority.
            self.assertEqual(preferences.remembered_folder(reloaded, 'box', SCOPE), 'Team "Sent"\\2026')
            self.assertTrue(preferences.remember_folder(reloaded, 'box', SCOPE, 'Team "Sent"\\2026'))
            self.assertEqual(write.call_count, 1)
            self.assertEqual(persisted['managedInboxes'], OWNED['config']['managedInboxes'])

    def test_user_workspace_mailbox_connection_and_generation_isolation(self):
        owned = deepcopy(OWNED)
        owned['config'][preferences.FIELD] = {preferences.preference_id(owned, 'box'): {'scope': SCOPE, 'folder': 'Sent Messages'}}
        for member in [replace(MEMBER, user_id='other'), replace(MEMBER, workspace_id='other')]:
            changed = {**owned, 'memberAuthority': member}
            self.assertIsNone(preferences.remembered_folder(changed, 'box', SCOPE))
            self.assertNotEqual(preferences.preference_id(changed, 'box'), preferences.preference_id(owned, 'box'))
        self.assertIsNone(preferences.remembered_folder(owned, 'other-box', SCOPE))
        for mailbox, generation in [(CONTEXT, 'generation-2'), ({**CONTEXT, 'imap': {**CONTEXT['imap'], 'host': 'new.invalid'}}, 'generation-1')]:
            scope, _ = reader.connection_scope(mailbox, generation)
            self.assertIsNone(preferences.remembered_folder(owned, 'box', scope))

    def test_conflict_outage_capacity_and_server_only_configuration(self):
        for result in ['conflict', 'missing', 'unavailable']:
            with patch.object(preferences.config_store, 'resolve_user_config_store', return_value=({'fixture': True}, None)), \
                 patch.object(preferences.config_store, 'write_user_config_record_if_unchanged', return_value={'status': result}) as write:
                self.assertFalse(preferences.remember_folder(OWNED, 'box', SCOPE, 'Sent'))
                self.assertEqual(write.call_count, 1)
        owned = deepcopy(OWNED)
        owned['config'][preferences.FIELD] = {str(i): {} for i in range(preferences.MAX_PREFERENCES)}
        with patch.object(preferences.config_store, 'write_user_config_record_if_unchanged') as write:
            self.assertFalse(preferences.remember_folder(owned, 'box', SCOPE, 'Sent')); write.assert_not_called()
        api = importlib.import_module('api.user.config')
        stored = deepcopy(OWNED['config'])
        stored[preferences.FIELD] = {'fixture': {'scope': SCOPE, 'folder': 'Private folder'}}
        self.assertNotIn(preferences.FIELD, api._sanitize_stored_user_config(stored))
        update = {'v': 1, 'email': MEMBER.email, 'updatedAt': 'fixture'}
        self.assertEqual(api._merge_user_config(stored, update)[preferences.FIELD], stored[preferences.FIELD])
        attempted = api._sanitize_user_config({'config': {preferences.FIELD: {'injected': True}}}, MEMBER.email)
        self.assertNotIn(preferences.FIELD, attempted)

    def test_automatic_choice_survives_a_new_login_without_count_probes(self):
        current = deepcopy(OWNED['config'])
        writes = []
        def save(store, owner, expected, replacement):
            nonlocal current
            self.assertEqual(expected, current)
            self.assertEqual(m.calls[-1][0], 'LOGOUT')
            current = deepcopy(replacement); writes.append(owner)
            return {'status': 'ok'}
        def fresh_mailbox():
            m = Mailbox(rows={n: mail(n) for n in range(1, 20)}, special=False,
                folders=[b'() "/" "Sent"', b'() "/" "Sent Messages"'])
            original = m.select
            def select(folder, readonly=False):
                status, data = original(folder, readonly=readonly)
                return status, [b'0'] if folder == '"Sent"' else data
            m.select = select
            return m
        with patch.object(preferences.config_store, 'resolve_user_config_store', return_value=({'fixture': True}, None)), \
             patch.object(preferences.config_store, 'write_user_config_record_if_unchanged', side_effect=save):
            for login in range(2):
                owned = deepcopy(OWNED); owned['config'] = deepcopy(current)
                owned['memberAuthority'] = replace(MEMBER)
                m = fresh_mailbox(); h = Handler({'mailboxId': 'box'})
                with patch.object(route, 'resolve_context', return_value=(CONTEXT, SCOPE, KEY, owned)), \
                     patch.object(route, 'connect_mailbox_with_settings', return_value=m):
                    route.handler.do_POST(h)
                self.assertEqual(h.status, 200, h.body())
                self.assertEqual(h.body()['page']['providerFolder'], 'Sent Messages')
                self.assertEqual(len(h.body()['page']['messages']), 19)
                self.assertTrue(h.body()['preferenceSaved'])
                self.assertEqual(sum(c[0] == 'EXAMINE' for c in m.calls), 4 if login == 0 else 2)
            self.assertEqual(writes, [MEMBER.email])

    def test_route_saves_only_verified_choice_after_logout_and_restores_it(self):
        owned = deepcopy(OWNED); owned['config'][preferences.FIELD] = {preferences.preference_id(owned, 'box'): {'scope': SCOPE, 'folder': 'Sent Messages'}}
        m = Mailbox(rows={1: mail()}, special=False, folders=[b'() "/" "Sent"', b'() "/" "Sent Messages"'])
        def remember(*args):
            self.assertEqual(m.calls[-1][0], 'LOGOUT')
            self.assertEqual(args[3], 'Sent Messages'); return False
        h = Handler({'mailboxId': 'box'})
        with patch.object(route, 'resolve_context', return_value=(CONTEXT, SCOPE, KEY, owned)), \
             patch.object(route, 'connect_mailbox_with_settings', return_value=m), \
             patch.object(route, 'remember_folder', side_effect=remember):
            route.handler.do_POST(h)
        self.assertEqual(h.status, 200, h.body())
        self.assertEqual(h.body()['page']['providerFolder'], 'Sent Messages')
        self.assertFalse(h.body()['preferenceSaved'])
        self.assertEqual(sum(c[0] == 'EXAMINE' for c in m.calls), 2)  # Page identity checks only.
        m = Mailbox(folders=[b'() "/" "Sent"'], special=False)
        with patch.object(route, 'resolve_context', return_value=(CONTEXT, SCOPE, KEY, owned)), \
             patch.object(route, 'connect_mailbox_with_settings', return_value=m), \
             patch.object(route, 'remember_folder') as save:
            h = Handler({'mailboxId': 'box'}); route.handler.do_POST(h)
            self.assertEqual(h.body()['discovery']['mode'], 'choose'); save.assert_not_called()


if __name__ == '__main__': unittest.main()
