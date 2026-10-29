import asyncio
import base64
import importlib.util
import json
from pathlib import Path
from contextlib import contextmanager
import sys
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator
from pydantic import TypeAdapter, ValidationError

root = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('finn_contract_tests', root / 'finn_torget_mcp.py')
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = {t.name: t for t in asyncio.run(m.mcp.list_tools())}

    def test_all_tools_publish_valid_output_schemas(self):
        for name, tool in self.tools.items():
            with self.subTest(tool=name):
                self.assertIsNotNone(tool.outputSchema)
                Draft202012Validator.check_schema(tool.outputSchema)
                Draft202012Validator.check_schema(tool.inputSchema)

    def test_schema_rejects_bad_inputs_before_execution(self):
        cases = [
            ('get_listing', {'listing_id':'../123'}),
            ('get_listing_seller', {'listing_id':123}),
            ('get_listings_many', {'listing_ids':[]}),
            ('get_listings_many', {'listing_ids':['123'], 'include_fields':[]}),
            ('get_listings_many', {'listing_ids':['123'], 'include_fields':['unknown']}),
            ('get_listings_many', {'listing_ids':['123']*51}),
            ('get_conversation', {'conversation_id':'abc/def'}),
            ('get_conversation', {'conversation_id':'abc_def', 'before':'yesterday'}),
            ('list_listings', {'limit':0}),
            ('list_listings', {'offset':-1}),
            ('list_listings', {'status_facet':'UNKNOWN'}),
            ('search_categories', {'query':'chair', 'limit':51}),
            ('list_categories', {'limit':251}),
            ('search_listings', {'query':'chair', 'price_to':-1}),
            ('search_listings', {'query':'chair', 'location':['Oslo']}),
            ('update_listing', {'listing_id':'123','price':-1}),
            ('update_listing', {'listing_id':'123','price':True}),
            ('create_listing', {'title':' ', 'description':'Test', 'category_id':1, 'price':0}),
            ('send_listing_message', {'listing_id':'123','body':' '}),
            ('update_saved_search', {'search_id':1,'notifications':['sms']}),
        ]
        for tool, args in cases:
            with self.subTest(tool=tool, args=args):
                self.assertTrue(list(Draft202012Validator(self.tools[tool].inputSchema).iter_errors(args)))

    def test_direct_calls_reject_invalid_values_without_network_or_writes(self):
        calls = [
            lambda: m.list_listings(limit=0),
            lambda: m.list_listings(limit='10'),
            lambda: m.list_categories(offset=-1),
            lambda: m.search_categories('chair',limit=100),
            lambda: m.list_conversations(limit=101),
            lambda: m.get_conversation('abc_def',limit=0),
            lambda: m.search_messages('test',conversation_limit=51),
            lambda: m.update_listing('123',price=-1),
            lambda: m.update_listing('123',price=True),
            lambda: m.create_listing('Test','Test',-1,1),
            lambda: m.get_listing('123/456'),
            lambda: m.search_listings('chair',price_from=200,price_to=100),
            lambda: m.create_saved_search('chair',price_from=200,price_to=100),
        ]
        with patch.object(m,'_request') as request, patch.object(m,'_message_context') as context, patch.object(m,'_html_state') as owner:
            for call in calls:
                with self.assertRaises(ValueError): call()
            request.assert_not_called()
            context.assert_not_called()
            owner.assert_not_called()

    def test_real_conversation_id_alphabet_is_accepted_for_read_and_send(self):
        # Synthetic opaque ID includes both URL-safe punctuation characters.
        conversation_id='test_Conversation-ID_123'
        args={'conversation_id':conversation_id}
        self.assertFalse(list(Draft202012Validator(self.tools['get_conversation'].inputSchema).iter_errors(args)))
        with patch.object(m,'_message_context',return_value=('https://www.finn.no/messages','123')), patch.object(m,'_finn_messages',return_value=[]) as read, patch.object(m,'_message_json'), patch.object(m,'_send_text',return_value={}) as send:
            result=m.get_conversation(conversation_id)
            self.assertEqual(result['conversation_id'],conversation_id)
            self.assertEqual(read.call_args.args[2],conversation_id)
            m.send_message(conversation_id,'Test')
            self.assertEqual(send.call_args.kwargs['conversation_id'],conversation_id)

    def test_invalid_cursor_positions_are_not_silently_clamped(self):
        cursor=base64.urlsafe_b64encode(json.dumps({'offset':-1,'group':0,'conversation':0}).encode()).decode().rstrip('=')
        with patch.object(m,'_message_context') as context:
            with self.assertRaises(ValueError): m.list_conversations(cursor=cursor)
            context.assert_not_called()

    def test_mcp_roundtrip_preserves_selected_fields(self):
        with patch.object(m,'_request',return_value=(200,'https://www.finn.no',b'{"total":1,"summaries":[{"id":"123","data":{"title":"Test"}}]}')):
            content, structured=asyncio.run(m.mcp.call_tool('list_listings',{}))
        self.assertEqual(structured['listings'],[{'id':'123','title':'Test'}])
        self.assertNotIn('price',structured['listings'][0])

    def test_owner_result_does_not_invent_optional_null_fields(self):
        @contextmanager
        def form(_):
            yield 'test-form'
        state={'state':'Active','state_raw':'ACTIVE','state_source':'owner_by_id',
               'publication_status':'published','publication_status_verified':True}
        with patch.object(m,'_torget_owner_lookup',return_value=(200,{'id':'123'})), patch.object(m,'_html_state',form), patch.object(m.api,'_item_put_body_from_create_html',return_value=('123',None,{'title':'Test'})), patch.object(m,'_listing_state',return_value=state):
            content, structured=asyncio.run(m.mcp.call_tool('get_listing',{'listing_id':'123','include_description':False,'include_search_metadata':False}))
        self.assertEqual(structured['id'],'123')
        for field in ('listing_id','description','search_metadata','snippets'):
            self.assertNotIn(field,structured)
        tool=self.tools['get_listing']
        self.assertFalse(list(Draft202012Validator(tool.outputSchema).iter_errors(structured)))

    def test_output_contract_preserves_missing_and_false_seller_values(self):
        payload={'listing_id':'123','seller':dict.fromkeys(['name','profile_url','type','member_since','identity_verified','rating_score','rating_count'])}
        payload['seller']['identity_verified']=False
        payload['seller']['rating_count']=0
        adapter=TypeAdapter(m.ListingSeller)
        self.assertEqual(adapter.validate_python(payload),payload)
        del payload['seller']['rating_count']
        with self.assertRaises(ValidationError): adapter.validate_python(payload)

    def test_write_result_retains_uncertain_outcome_and_requires_retry_policy(self):
        result={'listing_id':'123','conversation_id':None,'client_message_id':'test-client-id',
                'message_id':None,'verified':False,'outcome':'unconfirmed','write_attempted':True,
                'write_request_accepted':False,'request_error':'TimeoutError','verification_error':None,
                'recovered_after_write_error':False,'retry_send':False}
        adapter=TypeAdapter(m.MessageSend)
        self.assertEqual(adapter.validate_python(result),result)
        result['retry_send']=True
        with self.assertRaises(ValidationError): adapter.validate_python(result)
        result['retry_send']=False
        result['outcome']='sent'
        with self.assertRaises(ValidationError): adapter.validate_python(result)

    def test_owner_scope_is_discoverable(self):
        self.assertIn("own FINN Torget listings",self.tools['get_listing'].description)


if __name__=='__main__': unittest.main()
