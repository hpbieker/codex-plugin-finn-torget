import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

root = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('finn_send_tests', root / 'finn_torget_mcp.py')
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
BASE = 'https://www.finn.no/messages'
MID = 'test-client-id'

class SendingTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(m, '_message_context', return_value=(BASE, '123')),
                       patch.object(m.uuid, 'uuid4', return_value=MID)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def rows(self):
        return [{'id': MID, 'body': 'Hei', 'outgoing': True}]

    def listing(self, *, existing=None, error=None, response=None, rows=None, contact=None, vertical='recommerce'):
        with patch.object(m, '_message_json', side_effect=[
            contact if contact is not None else {'type': 'MESSAGING', 'isOwner': False},
            {'ads': [{'vertical': vertical}]}]), \
             patch.object(m, '_existing_listing_conversation', return_value=existing), \
             patch.object(m, '_request', side_effect=error, return_value=(200, BASE, json.dumps(response or {'id': 'conv'}).encode())) as req, \
             patch.object(m, '_finn_messages', return_value=self.rows() if rows is None else rows):
            result = m.send_listing_message('456', 'Hei')
            return result, req

    def test_new_conversation_payload_and_verified(self):
        result, req = self.listing()
        self.assertTrue(result['verified'])
        self.assertEqual(result['conversation_id'], 'conv')
        self.assertEqual(req.call_count, 1)
        self.assertEqual(req.call_args.args[0], BASE + '/api/conversations/users/123/conversations')
        payload = json.loads(req.call_args.kwargs['data'])
        self.assertEqual(payload['item'], {'id': '456', 'type': 'ad'})
        self.assertEqual(payload['message']['clientMessageId'], MID)

    def test_existing_uses_message_endpoint(self):
        result, req = self.listing(existing='old')
        self.assertTrue(result['verified'])
        self.assertTrue(req.call_args.args[0].endswith('/old/messages'))

    def test_timeout_recovered_by_lookup_and_readback(self):
        result, req = self.listing(existing='old', error=TimeoutError('secret'))
        self.assertTrue(result['verified'])
        self.assertTrue(result['recovered_after_write_error'])
        self.assertNotIn('secret', str(result))
        self.assertEqual(req.call_count, 1)

    def test_creation_timeout_resolves_conversation(self):
        with patch.object(m, '_message_json', side_effect=[{'type':'MESSAGING','isOwner':False}, {'ads':[{'vertical':'recommerce'}]}]), \
             patch.object(m, '_existing_listing_conversation', side_effect=[None, 'recovered']), \
             patch.object(m, '_request', side_effect=TimeoutError()), \
             patch.object(m, '_finn_messages', return_value=self.rows()):
            result = m.send_listing_message('456', 'Hei')
        self.assertTrue(result['verified'])
        self.assertEqual(result['conversation_id'], 'recovered')

    def test_no_matching_readback_never_claims_sent(self):
        for rows in [[], [{'id':'other','body':'Hei','outgoing':True}],
                     [{'id':MID,'body':'wrong','outgoing':True}],
                     [{'id':MID,'body':'Hei','outgoing':False}]]:
            result, req = self.listing(rows=rows)
            self.assertEqual(result['outcome'], 'unconfirmed')
            self.assertTrue(result['write_request_accepted'])
            self.assertFalse(result['retry_send'])
            self.assertEqual(req.call_count, 1)

    def test_invalid_input_does_not_connect(self):
        with patch.object(m, '_message_context') as ctx:
            for listing, body in [('x','Hei'), ('456',' '), ('../456','Hei')]:
                with self.assertRaises(ValueError):
                    m.send_listing_message(listing, body)
            ctx.assert_not_called()

    def test_own_unknown_or_disabled_contact_never_writes(self):
        for contact in [{'type':'MESSAGING','isOwner':True}, {'type':'MESSAGING'}, {'type':'EMAIL','isOwner':False}]:
            with patch.object(m, '_request') as req, patch.object(m, '_message_json', return_value=contact):
                with self.assertRaises(RuntimeError):
                    m.send_listing_message('456','Hei')
                req.assert_not_called()

    def test_other_vertical_never_writes(self):
        with patch.object(m, '_request') as req, patch.object(m, '_message_json', side_effect=[{'type':'MESSAGING','isOwner':False}, {'ads':[{'vertical':'mobility'}]}]):
            with self.assertRaisesRegex(RuntimeError, 'not_torget'):
                m.send_listing_message('456','Hei')
            req.assert_not_called()

    def test_auth_failure_never_writes(self):
        with patch.object(m, '_message_context', side_effect=RuntimeError('auth_required')), patch.object(m, '_request') as req:
            with self.assertRaisesRegex(RuntimeError, 'auth_required'):
                m.send_listing_message('456','Hei')
            req.assert_not_called()

    def test_lookup_404_and_ambiguous(self):
        with patch.object(m, '_request', return_value=(404, BASE, b'')):
            self.assertIsNone(m._existing_listing_conversation(BASE, '123', '456'))
        with patch.object(m, '_request', return_value=(200, BASE, b'{"obfuscatedExternalConversationIds":["a","b"]}')):
            with self.assertRaisesRegex(RuntimeError, 'ambiguous'):
                m._existing_listing_conversation(BASE, '123', '456')

    def test_existing_tool_resolves_current_account(self):
        with patch.object(m, '_message_json') as read, patch.object(m, '_send_text', return_value={}) as send:
            m.send_message('abc/def', 'Hei')
            self.assertIn('/users/123/conversations/abc%2Fdef', read.call_args.args[0])
            send.assert_called_once_with(BASE, '123', 'Hei', conversation_id='abc/def')

    def test_readback_failure_is_unconfirmed(self):
        with patch.object(m, '_request', return_value=(200, BASE, b'{}')), patch.object(m, '_finn_messages', side_effect=RuntimeError('secret')):
            result = m._send_text(BASE, '123', 'Hei', conversation_id='old')
        self.assertFalse(result['verified'])
        self.assertNotIn('secret', str(result))

    def test_http_failure_is_not_accepted(self):
        with patch.object(m, '_request', return_value=(403, BASE, b'secret')), patch.object(m, '_finn_messages', return_value=[]):
            result = m._send_text(BASE, '123', 'Hei', conversation_id='old')
        self.assertFalse(result['write_request_accepted'])
        self.assertFalse(result['verified'])
        self.assertNotIn('secret', str(result))

if __name__ == '__main__':
    unittest.main()
