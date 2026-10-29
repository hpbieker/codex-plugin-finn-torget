import base64
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import URLError

root = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('finn_message_tests', root / 'finn_torget_mcp.py')
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


class MessageContextTests(unittest.TestCase):
    def page(self, tag='main', config=None):
        config = {'basePath': '/messages', 'loginId': '123'} if config is None else config
        value = base64.b64encode(json.dumps(config).encode()).decode()
        return f'<{tag} id="app" data-config="{value}"></{tag}>'.encode()

    def read(self, body, status=200, final='https://www.finn.no/messages'):
        with patch.object(m, '_request', return_value=(status, final, body)):
            return m._message_context()

    def test_old_and_current_html_and_base_paths(self):
        for tag in ('div', 'main'):
            for base in ('/messages', 'https://www.finn.no/messages', '/messages/'):
                with self.subTest(tag=tag, base=base):
                    self.assertEqual(self.read(self.page(tag, {'basePath': base, 'loginId': '123'})),
                                     ('https://www.finn.no/messages', '123'))

    def test_missing_config_does_not_claim_auth_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'messaging_config_missing') as caught:
            self.read(b'<main id="app"></main>')
        self.assertNotIn('sign in', str(caught.exception))

    def test_bad_config(self):
        for body in (b'<main id="app" data-config="bad">', self.page(config=[]),
                     self.page(config={'loginId': '123'})):
            with self.subTest(body=body), self.assertRaisesRegex(RuntimeError, 'messaging_config_invalid'):
                self.read(body)

    def test_unsafe_or_unexpected_base_rejected(self):
        for base in ('https://evil.example/messages', '//evil.example/messages',
                     'http://www.finn.no/messages', 'https://evil@www.finn.no/messages',
                     '/other', '/messages?token=secret', '/messages#secret'):
            with self.subTest(base=base), self.assertRaisesRegex(RuntimeError, 'messaging_config_invalid') as caught:
                self.read(self.page(config={'basePath': base, 'loginId': '123'}))
            self.assertNotIn('secret', str(caught.exception))

    def test_missing_login_id(self):
        with self.assertRaisesRegex(RuntimeError, 'messaging_login_missing'):
            self.read(self.page(config={'basePath': '/messages'}))

    def test_http_failures_are_distinct(self):
        for status, code in ((401, 'auth_required'), (403, 'messaging_access_denied'), (503, 'messaging_http_error')):
            with self.subTest(status=status), self.assertRaisesRegex(RuntimeError, code):
                self.read(b'', status=status)

    def test_login_redirect_and_unexpected_page(self):
        for final, code in (('https://login.vend.no/?secret=1', 'auth_required'),
                            ('https://www.finn.no/login', 'auth_required'),
                            ('https://other.example/', 'messaging_page_unexpected')):
            with self.subTest(final=final), self.assertRaisesRegex(RuntimeError, code):
                self.read(b'', final=final)

    def test_session_and_network_errors_are_sanitized_tool_errors(self):
        for error, code in ((SystemExit('Safari has no FINN cookies'), 'auth_required'),
                            (SystemExit('socket secret'), 'safari_session_unavailable'),
                            (URLError('secret'), 'messaging_network_error'),
                            (TimeoutError('secret'), 'messaging_network_error')):
            with self.subTest(code=code), patch.object(m, '_request', side_effect=error):
                with self.assertRaisesRegex(RuntimeError, code) as caught:
                    m._message_context()
                self.assertNotIn('secret', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
