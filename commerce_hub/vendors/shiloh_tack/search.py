"""Vendor category choices and validated catalog search parameters."""
from html.parser import HTMLParser
import json
from pathlib import Path
import re


class CategoryOptions(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active = False
        self.option = None
        self.categories = []

    def finish_option(self):
        if self.option is not None:
            value, text, disabled = self.option
            label = ' '.join(''.join(text).split())
            if not disabled and value and re.fullmatch(r'[0-9]{1,8}', value) and label:
                self.categories.append({'value': value, 'label': label})
            self.option = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'select':
            self.active = attrs.get('name') == 'scat'
        elif tag == 'option' and self.active:
            self.finish_option()
            self.option = (attrs.get('value', ''), [], 'disabled' in attrs)

    def handle_data(self, data):
        if self.option is not None:
            self.option[1].append(data)

    def handle_endtag(self, tag):
        if tag == 'option':
            self.finish_option()
        elif tag == 'select':
            self.finish_option()
            self.active = False


def parse_categories(html):
    parser = CategoryOptions()
    parser.feed(html)
    categories = parser.categories
    if not categories or len(categories) > 2000 or len({c['value'] for c in categories}) != len(categories):
        raise ValueError('Vendor category list was not recognized.')
    return categories


def categories():
    path = Path(__file__).with_name('categories.json')
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding='utf-8'))['categories']


def catalog_selection(mode='all', category_id='', choices=None):
    if mode == 'all':
        return {'mode': 'all'}
    if mode != 'category' or not isinstance(category_id, str):
        raise ValueError('Select all products or a valid category.')
    option = next((c for c in (categories() if choices is None else choices) if c['value'] == category_id), None)
    if option is None:
        raise ValueError('Choose a category from the list.')
    return {'mode': 'category', 'category_id': option['value'], 'category_label': option['label']}


def search_parameters(selection, page=1, choices=None):
    validated = catalog_selection(selection.get('mode'), selection.get('category_id', ''), choices)
    if type(page) is not int or not 1 <= page <= 10000:
        raise ValueError('Invalid catalog page.')
    return [('pg', str(page)), ('stext', ''), ('sprice', ''), ('stype', ''),
            ('scat', validated.get('category_id', '')), ('sortby', '9'),
            ('sfil0', ''), ('sfil1', ''), ('sfil2', '')]
