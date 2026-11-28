"""Authenticated, rate-limited catalog reads using the pinned HTTPS transport."""
from copy import copy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from threading import Lock
import time
from urllib.parse import urlencode, urlsplit, parse_qs
from urllib.request import Request

from commerce_hub.connector_contracts import Credentials, SourceRecord
from . import login
from .details import detail_record
from .html import tree, product_cards, pagination, path_of
from .search import catalog_selection, parse_categories, search_parameters


class CatalogError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class CatalogRetryable(CatalogError):
    def __init__(self, code='vendor_temporarily_unavailable', retry_after=30):
        self.retry_after = max(1, min(86400, retry_after))
        super().__init__(code)


def retry_seconds(value):
    try:
        if value and value.isdigit():
            return max(1, min(86400, int(value)))
        stamp = parsedate_to_datetime(value)
        return max(1, min(86400, int((stamp-datetime.now(timezone.utc)).total_seconds())))
    except (ValueError, TypeError, OverflowError):
        return 60


class RequestGate:
    def __init__(self, interval=1):
        self.interval = interval
        self.lock = Lock()
        self.next_at = 0

    def wait(self, check):
        with self.lock:
            due = max(time.monotonic(), self.next_at)
            self.next_at = due + self.interval
        while time.monotonic() < due:
            check()
            time.sleep(max(0,min(.2,due-time.monotonic())))
        check()


class _LoginSession(login.VendorSession):
    def request(self, method, path, body=None):
        result = super().request(method, path, body)
        if method == 'GET' and path == '/search.php':
            self.landing = result
        return result

    def close(self):
        # verify() closes its session; the catalog owns this same session until exit.
        pass


class CatalogClient:
    def __init__(self, url, credentials, selection, check):
        self.url = url
        self.credentials = credentials
        self.selection = selection
        self.check = check
        self.session = _LoginSession(login.approved_host(url))
        self.gate = RequestGate()

    def __enter__(self):
        try:
            self.check()
            login.verify(self.url, self.credentials, session_factory=lambda _host:self.session)
            self.choices = parse_categories(self.session.landing)
            self.session.landing = ''
            self.selection = catalog_selection(self.selection['mode'], self.selection.get('category_id',''), self.choices)
            return self
        except BaseException:
            self.__exit__(None,None,None)
            raise

    def __exit__(self, *_args):
        login.VendorSession.close(self.session)
        self.session.landing = ''
        self.credentials = None

    def get(self, path):
        parsed=urlsplit(path)
        if parsed.path=='/search.php':
            query=parse_qs(parsed.query,keep_blank_values=True)
            allowed={'pg','stext','sprice','stype','scat','sortby','sfil0','sfil1','sfil2'}
            valid=(not parsed.scheme and not parsed.netloc and not parsed.fragment and set(query)==allowed
                   and all(len(v)==1 for v in query.values())
                   and query['pg'][0].isdigit() and query['sortby']==['9']
                   and (not query['scat'][0] or query['scat'][0].isdigit())
                   and all(query[k]==[''] for k in ('stext','sprice','stype','sfil0','sfil1','sfil2')))
        else:
            valid=path_of(path)==path
        if not valid:
            raise CatalogError('catalog_url_not_approved')
        self.gate.wait(self.check)
        request = Request('https://'+self.session.host+path, headers={'User-Agent':'CodeBI-CommerceHub/1.0'})
        # Each request gets a private jar; concurrent fetches never mutate a shared jar.
        jar = type(self.session.cookies)()
        for cookie in self.session.cookies:
            jar.set_cookie(copy(cookie))
        jar.add_cookie_header(request)
        connection = login.PinnedHTTPS(self.session.host, timeout=15)
        try:
            connection.request('GET',path,headers=dict(request.header_items()))
            response = connection.getresponse()
            if response.status == 429:
                raise CatalogRetryable('vendor_rate_limited',retry_seconds(response.getheader('Retry-After')))
            if response.status >= 500:
                raise CatalogRetryable()
            if response.status != 200:
                raise CatalogError('vendor_catalog_access_changed')
            data = response.read(login.MAX_RESPONSE+1)
            if len(data)>login.MAX_RESPONSE:
                raise CatalogError('vendor_response_too_large')
            html = data.decode('utf-8',errors='replace')
            account = login.AccountPage()
            account.feed(html)
            if not account.logout:
                raise CatalogError('vendor_catalog_session_lost')
            self.check()
            return html
        except (OSError, HTTPException):
            raise CatalogRetryable() from None
        finally:
            connection.close()
            jar.clear()

    def listing(self, page):
        root = tree(self.get('/search.php?'+urlencode(search_parameters(self.selection,page,self.choices))))
        cards = product_cards(root)
        paths = [card['detail_path'] for card in cards]
        if any(not path for path in paths):
            raise CatalogError('catalog_detail_link_missing')
        pager = pagination(root)
        later = sorted({link['page'] for link in pager['links'] if link['page']>page})
        if not paths:
            raise CatalogError('catalog_unexpected_empty_search')
        if later and later[0] != page+1:
            raise CatalogError('catalog_pagination_unrecognized')
        return {'page':page,'paths':paths,'next_page':later[0] if later else None,
                'last_page_hint':max([page]+[link['page'] for link in pager['links']])}

    def product(self, path):
        if path_of(path)!=path or path.startswith('/search.php'):
            raise CatalogError('catalog_url_not_approved')
        try:
            record = detail_record(self.get(path),path)
        except (ValueError, RecursionError):
            raise CatalogError('catalog_detail_unrecognized') from None
        if not record.get('name') or not record.get('id'):
            raise CatalogError('catalog_required_fields_missing')
        for debug in ('product_dom','classes','tab_controls','display_to_original_image_map'):
            record.pop(debug,None)
        return SourceRecord(record['id'],record['id'],record,'https://www.shilohtack.com'+path)
