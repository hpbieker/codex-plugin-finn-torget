from argparse import Namespace
from contextlib import contextmanager
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

root = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('finn_location_tests', root / 'finn_torget_mcp.py')
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def address():
    # Synthetic fixture, unrelated to a seller or account location.
    return {'postalcode':'0001','postalname':'Test location','country':'NO',
            'latitude':'1.25','longitude':'2.5','accuracy':1,
            'metadata':{'test':True}}


def args():
    return Namespace(title='Test listing',description='Test description',description_file=None,
                     trade_type='SELL',price=100,category=23,select_html_path='test-form')


class CreateLocationTests(unittest.TestCase):
    def test_population_preserves_complete_draft_address(self):
        data={'address':address()}
        original=data['address']
        expected=deepcopy(original)
        with patch.object(m.api,'_apply_select_fields'):
            snapshot=m.api._populate_create_data(data,args())
        self.assertIs(data['address'],original)
        self.assertEqual(data['address'],expected)
        self.assertEqual(snapshot,expected)
        self.assertIsNot(snapshot,original)
        self.assertEqual(data['title'],'Test listing')

    def test_invalid_location_fails_before_mutating_fields(self):
        invalid=[None,{},'not-an-address']
        for field in ('postalcode','postalname','country','latitude','longitude'):
            value=address();value.pop(field);invalid.append(value)
        for field,value in [('postalcode',' '),('latitude',True),('latitude','NaN'),
                            ('latitude',float('inf')),('latitude','91'),('longitude','181'),
                            ('longitude','not-a-number')]:
            value_address=address();value_address[field]=value;invalid.append(value_address)
        for location in invalid:
            with self.subTest(location=location):
                data={'title':'Original','address':location}
                before=deepcopy(data)
                with patch.object(m.api,'_apply_select_fields') as select:
                    with self.assertRaisesRegex(ValueError,'Set its location in FINN'):
                        m.api._populate_create_data(data,args())
                    select.assert_not_called()
                self.assertEqual(data,before)

    def run_create(self, location, *, resume=False, mutate_readback=False):
        draft={'address':deepcopy(location)}
        saved={}
        @contextmanager
        def form(_):
            yield 'test-form'
        def commit(listing_id,etag,body_path,response_path):
            saved.update(deepcopy(draft))
            if mutate_readback:
                saved['address']['longitude']='3.5'
            return {}
        state={'state':'Draft','state_raw':'draft','state_source':'owner_by_id',
               'publication_status':'draft','publication_status_verified':True}
        with patch.object(m,'categories',return_value=[{'kind':'sub_category','value':'1.23'}]), \
             patch.object(m.api,'_new_recommerce_draft',return_value='123') as create, \
             patch.object(m.api,'_fetch_create_data_with_retry',return_value=('123','etag',draft)), \
             patch.object(m.api,'_apply_select_fields'), \
             patch.object(m.api,'_write_json') as write_json, \
             patch.object(m.api,'_post_item_body',side_effect=commit) as write, \
             patch.object(m.api,'_upload_item_image') as upload, \
             patch.object(m,'_html_state',form), \
             patch.object(m.api,'_item_put_body_from_create_html',side_effect=lambda _:('123','etag',saved)), \
             patch.object(m,'_listing_state',return_value=state), \
             patch.object(m.api,'_publish_free_standard') as publish:
            try:
                result=m.create_listing('Test listing','Test description',100,23,
                                        draft_id='123' if resume else None)
            except RuntimeError:
                if location is None:
                    write.assert_not_called();write_json.assert_not_called();upload.assert_not_called();publish.assert_not_called()
                raise
            if resume:
                create.assert_not_called()
            self.assertEqual(write.call_count,1)
            self.assertEqual(draft['address'],location)
            publish.assert_not_called()
            return result

    def test_new_draft_uses_its_own_location(self):
        self.assertTrue(self.run_create(address())['content_verified'])

    def test_resumed_draft_keeps_its_location(self):
        self.assertTrue(self.run_create(address(),resume=True)['resumed_draft'])

    def test_missing_location_returns_reusable_draft_id_without_saving(self):
        for resume in (False,True):
            with self.subTest(resume=resume):
                with self.assertRaisesRegex(RuntimeError,"ad 123.*Set its location in FINN"):
                    self.run_create(None,resume=resume)

    def test_readback_allows_equivalent_coordinate_formatting(self):
        expected=address()
        actual=deepcopy(expected)
        actual['latitude']=1.25
        actual['longitude']='2.5000'
        self.assertTrue(m.api._listing_address_matches(actual,expected))
        actual['postalcode']='0002'
        self.assertFalse(m.api._listing_address_matches(actual,expected))

    def test_location_change_in_readback_is_not_verified(self):
        with self.assertRaisesRegex(RuntimeError,'did not match'):
            self.run_create(address(),mutate_readback=True)


if __name__=='__main__': unittest.main()
