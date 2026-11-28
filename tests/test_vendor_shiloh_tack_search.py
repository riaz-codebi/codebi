import unittest
from urllib.parse import parse_qs, urlencode

from commerce_hub.vendors.shiloh_tack.search import catalog_selection, categories, parse_categories, search_parameters


class ShilohSearchTests(unittest.TestCase):
    def test_category_parser_preserves_vendor_ids_and_labels(self):
        html = '''<select name="sortby"><option value="9">Date added</option></select>
        <select name="scat"><option value="">All Categories</option>
        <option value="4">Rider Attire<option value="440">&raquo; Outerwear</option>
        <option value="441" disabled>Unavailable</option></select>'''
        self.assertEqual(parse_categories(html), [
            {'value': '4', 'label': 'Rider Attire'}, {'value': '440', 'label': '\u00bb Outerwear'}])

    def test_missing_or_duplicate_categories_fail_closed(self):
        for html in ('<html></html>', '<select name="scat"><option value="4">One</option><option value="4">Two</option></select>'):
            with self.subTest(html=html), self.assertRaises(ValueError):
                parse_categories(html)

    def test_outerwear_scope_builds_vendor_filter_and_keeps_other_defaults(self):
        chosen = catalog_selection('category', '440')
        query = parse_qs(urlencode(search_parameters(chosen, page=2)), keep_blank_values=True)
        self.assertEqual(query, {'pg':['2'], 'stext':[''], 'sprice':[''], 'stype':[''],
                                 'scat':['440'], 'sortby':['9'], 'sfil0':[''], 'sfil1':[''], 'sfil2':['']})

    def test_all_products_clears_previous_category(self):
        chosen = catalog_selection('all', '440')
        self.assertEqual(chosen, {'mode':'all'})
        self.assertEqual(dict(search_parameters(chosen))['scat'], '')

    def test_unknown_category_and_invalid_scope_cannot_become_an_all_product_run(self):
        for mode, category in [('category',''), ('category','99999999'), ('category','440&scat='), ('other','440')]:
            with self.subTest(mode=mode,category=category), self.assertRaises(ValueError):
                catalog_selection(mode, category)

    def test_snapshot_contains_unique_real_vendor_options(self):
        options = categories()
        self.assertEqual(len(options), 350)
        self.assertEqual(len({item['value'] for item in options}), 350)
        self.assertEqual(catalog_selection('category','440')['category_label'], '\u00bb Outerwear')


if __name__ == '__main__':
    unittest.main()
