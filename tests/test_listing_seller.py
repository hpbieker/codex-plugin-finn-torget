import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import URLError

root = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('finn_seller_tests', root / 'finn_torget_mcp.py')
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
BASE = 'https://www.finn.no/messages'

class SellerTests(unittest.TestCase):
    def fetch(self, contact, ads=None):
        with patch.object(m, '_message_context', return_value=(BASE, '123')), patch.object(m, '_seller_json', side_effect=[{'ads': [{'vertical':'recommerce'}] if ads is None else ads}, contact]) as read:
            return m.get_listing_seller('456'), read

    def test_mapping(self):
        result, read = self.fetch({'ownerName':'Test Seller','owner':789,'segment':'PRIVATE','ownerMemberSince':2009,'ownerEidVerified':True,'ownerRatingScore':'9,8','ownerRatingCount':49})
        self.assertEqual(result, {'listing_id':'456','seller':{'name':'Test Seller','profile_url':'https://www.finn.no/profile?userId=789','type':'private','member_since':2009,'identity_verified':True,'rating_score':9.8,'rating_count':49}})
        self.assertEqual([c.args[0] for c in read.call_args_list], [BASE+'/api/listing?ids=ad456', BASE+'/api/contact/configuration/456'])

    def test_missing_fields_are_null(self):
        result,_=self.fetch({})
        self.assertTrue(all(v is None for v in result['seller'].values()))

    def test_false_and_zero_preserved(self):
        result,_=self.fetch({'ownerEidVerified':False,'ownerRatingScore':0,'ownerRatingCount':0})
        self.assertIs(result['seller']['identity_verified'],False)
        self.assertEqual(result['seller']['rating_score'],0.0)
        self.assertEqual(result['seller']['rating_count'],0)

    def test_unknown_segment_anonymous_and_bad_profile_id(self):
        result,_=self.fetch({'segment':'FUTURE','owner':789,'anonymous':True})
        self.assertIsNone(result['seller']['type'])
        self.assertIsNone(result['seller']['profile_url'])
        for owner in [True, '../789', '789&other=1', {}, 1.1]:
            result,_=self.fetch({'owner':owner})
            self.assertIsNone(result['seller']['profile_url'])

    def test_malformed_fields(self):
        for score in [True, 'NaN', float('inf'), -1, '11', {}, []]:
            result,_=self.fetch({'ownerRatingScore':score,'ownerRatingCount':True,'ownerMemberSince':'2009','ownerEidVerified':'false','ownerName':[],'segment':{}})
            self.assertTrue(all(v is None for v in result['seller'].values()))
        for score in ['9.8',9.8]:
            result,_=self.fetch({'ownerRatingScore':score})
            self.assertEqual(result['seller']['rating_score'],9.8)

    def test_validation_before_network(self):
        with patch.object(m,'_message_context') as ctx:
            for value in ['','0','../456','１２３',123]:
                with self.assertRaises(ValueError): m.get_listing_seller(value)
            ctx.assert_not_called()

    def test_non_torget_and_unavailable(self):
        for ads,code in [([], 'seller_unavailable'), ([{'vertical':'mobility'}], 'seller_not_torget'), ([None], 'seller_response_invalid')]:
            with self.assertRaisesRegex(RuntimeError,code): self.fetch({},ads)

    def test_context_auth_error_propagates_without_requests(self):
        with patch.object(m,'_message_context',side_effect=RuntimeError('auth_required')), patch.object(m,'_seller_json') as read:
            with self.assertRaisesRegex(RuntimeError,'auth_required'): m.get_listing_seller('456')
            read.assert_not_called()

    def test_http_errors_and_login_redirect(self):
        for status,code in [(401,'auth_required'),(403,'seller_access_denied'),(404,'seller_unavailable'),(410,'seller_unavailable'),(503,'seller_http_error')]:
            with patch.object(m,'_request',return_value=(status,BASE,b'secret')):
                with self.assertRaisesRegex(RuntimeError,code) as caught: m._seller_json(BASE)
                self.assertNotIn('secret',str(caught.exception))
        with patch.object(m,'_request',return_value=(200,'https://login.vend.no/?secret',b'secret')):
            with self.assertRaisesRegex(RuntimeError,'auth_required'): m._seller_json(BASE)

    def test_invalid_json_and_transport_errors_sanitized(self):
        for body in [b'<html>secret</html>', b'[]', b'null', b'\xff']:
            with patch.object(m,'_request',return_value=(200,BASE,body)):
                with self.assertRaisesRegex(RuntimeError,'seller_response_invalid') as caught: m._seller_json(BASE)
                self.assertNotIn('secret',str(caught.exception))
        for error in [URLError('secret'),TimeoutError('secret'),SystemExit('secret')]:
            with patch.object(m,'_request',side_effect=error):
                with self.assertRaises(RuntimeError) as caught: m._seller_json(BASE)
                self.assertNotIn('secret',str(caught.exception))

    def test_only_get_requests(self):
        responses=[(200,BASE,b'{"ads":[{"vertical":"recommerce"}]}'), (200,BASE,b'{"ownerName":"Test Seller"}')]
        with patch.object(m,'_message_context',return_value=(BASE,'123')),patch.object(m,'_request',side_effect=responses) as request:
            m.get_listing_seller('456')
        self.assertEqual(request.call_count,2)
        self.assertTrue(all(c.kwargs.get('method','GET')=='GET' and 'data' not in c.kwargs for c in request.call_args_list))

    def test_tool_schema(self):
        tools=asyncio.run(m.mcp.list_tools())
        tool=next(t for t in tools if t.name=='get_listing_seller')
        self.assertEqual(tool.inputSchema['required'],['listing_id'])
        self.assertEqual(set(tool.inputSchema['properties']),{'listing_id'})

if __name__=='__main__': unittest.main()
