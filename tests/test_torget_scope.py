import importlib.util
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

root = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('finn_scope_tests', root / 'finn_torget_mcp.py')
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


class TorgetScopeTests(unittest.TestCase):
    def test_list_uses_native_type_filter_and_filtered_total(self):
        with patch.object(m, '_request', return_value=(200, {}, b'{"total":189,"summaries":[]}')) as request:
            result = m.list_listings(status_facet='ALL')
        params = parse_qs(urlparse(request.call_args.args[0]).query)
        self.assertEqual(params['type'], ['RECOMMERCE'])
        self.assertEqual(params['facet'], ['ALL'])
        self.assertEqual(result['total'], 189)

    def test_foreign_types_never_fetch_torget_form(self):
        for vertical, model in [('MOBILITY', 'car-used'), ('REAL_ESTATE', 'realestate-leisure-sale'), ('REAL_ESTATE', 'realestate-letting')]:
            with self.subTest(model=model), patch.object(m, '_owner_listing_by_id', return_value=(200, {'id': 123, 'vertical': vertical, 'model': model})), patch.object(m, '_html_state') as form:
                with self.assertRaisesRegex(m.UnsupportedListingType, 'Annonsetypen støttes ikke'):
                    m.get_listing('123', include_search_metadata=False)
                form.assert_not_called()
                result = m.get_listings_many(['123'])
                self.assertEqual(result['results'][0]['error']['code'], 'unsupported_listing_type')
                self.assertNotIn('Safari', result['results'][0]['error']['message'])
                form.assert_not_called()

    def test_supported_read_reuses_owner_lookup(self):
        row = {'id': '123', 'vertical': 'RECOMMERCE', 'model': 'recommerce', 'state': {'type': 'ACTIVE'}}
        @contextmanager
        def form(_):
            yield 'test.html'
        with patch.object(m, '_owner_listing_by_id', return_value=(200, row)) as lookup, patch.object(m, '_html_state', form), patch.object(m.api, '_item_put_body_from_create_html', return_value=('123', None, {'description': 'Test'})), patch.object(m, '_form_config', return_value={'item': {}}):
            result = m.get_listing('123', include_search_metadata=False)
        self.assertEqual(result['description'], 'Test')
        self.assertEqual(result['publication_status'], 'published')
        lookup.assert_called_once_with('123')

    def test_missing_type_metadata_is_not_claimed_unsupported(self):
        for row in [None, {'id': '123'}, {'id': '123', 'model': 'unknown'}]:
            with patch.object(m, '_owner_listing_by_id', return_value=(404 if row is None else 200, row)):
                self.assertEqual(m._torget_owner_lookup('123')[1], row)

    def test_fulltext_inherits_torget_inventory(self):
        with patch.object(m, '_request', return_value=(200, {}, b'{"total":0,"summaries":[]}')) as request:
            result = m.search_listings_fulltext('http')
        self.assertEqual(result['total_available'], 0)
        self.assertEqual(parse_qs(urlparse(request.call_args.args[0]).query)['type'], ['RECOMMERCE'])


if __name__ == '__main__':
    unittest.main()
