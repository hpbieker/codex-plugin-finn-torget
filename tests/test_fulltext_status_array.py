import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

root = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('finn_status_array_tests', root / 'finn_torget_mcp.py')
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


class FulltextStatusArrayTests(unittest.TestCase):
    def run_search(self, inventory, **kwargs):
        reads, pages = [], []
        def listing(limit, offset, status_facet, include_fields):
            pages.append((status_facet, offset, limit))
            ids = inventory.get(status_facet, [])
            return {'total': len(ids), 'listings': [{'id': str(i), 'url': f'https://www.finn.no/{i}'} for i in ids[offset:offset+limit]]}
        def many(ids, include_fields):
            reads.extend(ids)
            return {'results': [{'listing_id': i, 'listing': {'id': i, 'title': f'Title {i}', 'description': 'matching text'}} for i in ids]}
        with patch.object(m, 'list_listings', side_effect=listing), patch.object(m, 'get_listings_many', side_effect=many):
            result = m.search_listings_fulltext('matching', **kwargs)
        return result, reads, pages

    def test_default_only_scans_active(self):
        result, reads, pages = self.run_search({'ACTIVE': [1], 'DRAFT': [2]})
        self.assertEqual(reads, ['1'])
        self.assertEqual(result['status_facet'], ['ACTIVE'])
        self.assertTrue(result['complete'])
        self.assertEqual(len(pages), 1)

    def test_or_combination_and_paging(self):
        result, reads, pages = self.run_search({'ACTIVE': list(range(1, 52)), 'DRAFT': [52]}, status_facet=['ACTIVE', 'DRAFT'], limit=100)
        self.assertEqual(len(reads), 52)
        self.assertEqual(len(set(reads)), 52)
        self.assertEqual(result['total_available'], 52)
        self.assertEqual(result['returned'], 52)
        self.assertTrue(result['complete'])
        self.assertEqual([(facet, offset) for facet, offset, _ in pages], [('ACTIVE', 0), ('ACTIVE', 50), ('DRAFT', 0)])

    def test_shared_cap_and_totals_for_unscanned_status(self):
        result, reads, _ = self.run_search({'ACTIVE': [1, 2, 3], 'DRAFT': [4, 5], 'DISPOSED': [6]}, status_facet=['ACTIVE', 'DRAFT', 'DISPOSED'], scan_limit=4)
        self.assertEqual(reads, ['1', '2', '3', '4'])
        self.assertEqual(result['total_available'], 6)
        self.assertEqual(result['selected'], 4)
        self.assertFalse(result['complete'])

    def test_cross_status_change_does_not_duplicate_detail_reads(self):
        result, reads, _ = self.run_search({'ACTIVE': [1], 'DRAFT': [1, 2]}, status_facet=['ACTIVE', 'DRAFT'])
        self.assertEqual(reads, ['1', '2'])
        self.assertFalse(result['complete'])

    def test_invalid_input_never_fetches_inventory(self):
        for value in [[], ['ALL'], ['ACTIVE', 'ACTIVE'], 'ACTIVE', ['active'], ['UNKNOWN'], [None]]:
            with self.subTest(value=value), patch.object(m, 'list_listings') as listing:
                with self.assertRaisesRegex(ValueError, 'non-empty array'):
                    m.search_listings_fulltext('matching', status_facet=value)
                listing.assert_not_called()

    def test_machine_readable_contract_and_listing_all_unchanged(self):
        tools = asyncio.run(m.mcp.list_tools())
        schema = next(t.inputSchema for t in tools if t.name == 'search_listings_fulltext')
        status = schema['properties']['status_facet']
        self.assertEqual(status['type'], 'array')
        self.assertEqual(status['default'], ['ACTIVE'])
        self.assertEqual(status['items']['enum'], ['ACTIVE', 'DRAFT', 'DISPOSED'])
        self.assertEqual(status['minItems'], 1)
        listing = next(t.inputSchema for t in tools if t.name == 'list_listings')
        self.assertEqual(listing['properties']['status_facet']['type'], 'string')


if __name__ == '__main__':
    unittest.main()
