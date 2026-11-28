"""Login-only Shiloh adapter using the site's observed dealer-login protocol."""
from html.parser import HTMLParser
from http.client import HTTPSConnection, HTTPException
from http.cookiejar import CookieJar
import ipaddress
import re
import socket
import ssl
from urllib.parse import urlencode, urlsplit
from urllib.request import Request

from commerce_hub.connector_contracts import ConnectorLoginError

HOSTS = frozenset(('www.shilohtack.com', 'shilohtack.com'))
CANONICAL_HOST = 'www.shilohtack.com'
MAX_RESPONSE = 2 * 1024 * 1024
TOKEN_PATTERN = r'[A-Za-z0-9._-]{1,256}'


def approved_host(url):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.port not in (None,443)
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ('','/','/search.php','/clientlogin.php')):
            raise ValueError
        # Normalize before sending cookies or credentials; do not follow redirects.
        return CANONICAL_HOST
    except (ValueError, TypeError):
        raise ConnectorLoginError('vendor_url_not_approved') from None


def public_addresses(host):
    addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ConnectorLoginError('vendor_network_not_approved')
    return addresses


class PinnedHTTPS(HTTPSConnection):
    def connect(self):
        # Validate DNS once, then connect directly to that IP. TLS still verifies
        # the original hostname. A second DNS lookup cannot redirect credentials.
        addresses = public_addresses(self.host)
        last_error = None
        for address in addresses:
            sock = None
            try:
                sock = socket.create_connection((address,443),timeout=self.timeout)
                self.sock = ssl.create_default_context().wrap_socket(sock,server_hostname=self.host)
                return
            except OSError as exc:
                if sock is not None:
                    sock.close()
                self.sock = None
                last_error = exc
            except BaseException:
                if sock is not None:
                    sock.close()
                self.sock = None
                raise
        if last_error is not None:
            raise last_error


class VendorSession:
    def __init__(self, host):
        if host not in HOSTS:
            raise ConnectorLoginError('vendor_url_not_approved')
        self.host = CANONICAL_HOST
        self.cookies = CookieJar()

    def request(self, method, path, body=None):
        # Only these observed endpoints are used; never follow a redirect or an
        # arbitrary vendor-supplied URL, and never persist the session cookie jar.
        parsed = urlsplit(path)
        if method not in ('GET','POST') or parsed.scheme or parsed.netloc or (
            method == 'GET' and (parsed.path not in ('/clientlogin.php','/search.php') or parsed.query)
        ) or (method == 'POST' and parsed.path != '/vsadmin/ajaxservice.php'):
            raise ConnectorLoginError('vendor_url_not_approved')
        url = 'https://' + self.host + path
        headers = {'User-Agent':'CodeBI-CommerceHub/1.0', 'Accept':'text/html,text/plain',
                   'Origin':'https://'+self.host, 'Referer':'https://'+self.host+'/clientlogin.php'}
        if body is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        cookie_request = Request(url, data=body, headers=headers, method=method)
        self.cookies.add_cookie_header(cookie_request)
        connection = PinnedHTTPS(self.host,timeout=15)
        try:
            connection.request(method,path,body=body,headers=dict(cookie_request.header_items()))
            response = connection.getresponse()
            raw = response.read(MAX_RESPONSE+1)
            if len(raw) > MAX_RESPONSE:
                raise ConnectorLoginError('vendor_response_too_large')
            self.cookies.extract_cookies(response,cookie_request)
            if response.status != 200:
                raise ConnectorLoginError('vendor_access_blocked' if response.status in (401,403) else 'vendor_service_unavailable')
            return raw.decode('utf-8',errors='replace')
        finally:
            connection.close()

    def close(self):
        self.cookies.clear()


class AccountPage(HTMLParser):
    VOID_TAGS = frozenset(('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'))
    LOGOUT = re.compile(r'log\s*out|sign\s*out',re.I)

    def __init__(self):
        super().__init__()
        self.logout = False
        self._elements = []
        self._controls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden = bool(self._elements and self._elements[-1][1]) or (
            tag in ('script','style','template') or 'hidden' in attrs
            or (attrs.get('aria-hidden') or '').lower() == 'true'
            or re.search(r'(?:display\s*:\s*none|visibility\s*:\s*hidden)',attrs.get('style') or '',re.I)
        )
        if tag not in self.VOID_TAGS:
            self._elements.append((tag,hidden))
        if hidden or tag not in ('a','button','input'):
            return
        if tag == 'input' and (attrs.get('type') or '').lower() not in ('submit','button','image','reset'):
            return
        evidence = ' '.join(attrs.get(key) or '' for key in ('onclick','href','value','id','name'))
        if self.LOGOUT.search(evidence):
            self.logout = True
        if tag in ('a','button'):
            self._controls.append((len(self._elements),[]))

    def handle_data(self, data):
        if self._elements and not self._elements[-1][1]:
            for _depth, text in self._controls:
                text.append(data)

    def handle_endtag(self, tag):
        index = next((i for i in range(len(self._elements)-1,-1,-1) if self._elements[i][0] == tag),None)
        if index is None:
            return
        remaining = []
        for depth, text in self._controls:
            if depth > index:
                if self.LOGOUT.search(''.join(text)):
                    self.logout = True
            else:
                remaining.append((depth,text))
        self._controls = remaining
        del self._elements[index:]


class HiddenLoginTokens(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tokens = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if (tag == 'input' and (attrs.get('type') or '').lower() == 'hidden'
            and (attrs.get('name') or '').lower() == 'lc'
            and re.fullmatch(TOKEN_PATTERN,attrs.get('value') or '')):
            self.tokens.add(attrs['value'])


def login_token(page):
    tokens = {match.group(2) for match in re.finditer(r'\bloginhash\s*=\s*([\"\'])('+TOKEN_PATTERN+r')\1',page)}
    inputs = HiddenLoginTokens()
    inputs.feed(page)
    tokens.update(inputs.tokens)
    # Ambiguous token values indicate changed markup; never guess with credentials.
    if len(tokens) != 1:
        return None
    return tokens.pop()


def verify(url, credentials, session_factory=VendorSession):
    host = approved_host(url)
    if not credentials.username or not credentials.password:
        raise ConnectorLoginError('connection_not_configured')
    session = session_factory(host)
    try:
        page = session.request('GET','/clientlogin.php')
        # If a fresh unauthenticated page already has a logout control, its
        # structure changed and that control is no longer a safe success signal.
        before = AccountPage(); before.feed(page)
        token = login_token(page)
        if before.logout or not token:
            raise ConnectorLoginError('vendor_login_structure_changed')
        endpoint = '/vsadmin/ajaxservice.php?' + urlencode({'action':'loginaccount','lc':token})
        payload = urlencode({'email':credentials.username,'pw':credentials.password}).encode('utf-8')
        reply = session.request('POST',endpoint,payload).strip()
        if re.search(r'captcha|verify you are human|challenge-platform',reply,re.I):
            raise ConnectorLoginError('vendor_interactive_verification_required')
        if reply.startswith('ERROR'):
            raise ConnectorLoginError('vendor_login_rejected')
        if not re.fullmatch(r'SUCCESS(?::[^\r\n]{0,256})?',reply):
            raise ConnectorLoginError('vendor_login_response_unrecognized')
        after = AccountPage(); after.feed(session.request('GET','/search.php'))
        if not after.logout:
            raise ConnectorLoginError('vendor_authenticated_session_not_confirmed')
    except (OSError, HTTPException, ValueError):
        # No exception text, response body, cookies, login hash or credentials
        # may escape to worker logs or job result JSON. Do not retry a login POST.
        raise ConnectorLoginError('vendor_service_unavailable') from None
    finally:
        session.close()
