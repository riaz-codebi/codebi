"""Acumatica inventory sign-in through the normal JavaScript/MFA browser flow."""
from pathlib import Path
import re
import sys
import time
from urllib.parse import unquote,urlsplit,urlunsplit,urljoin

from commerce_hub.connector_contracts import ConnectorLoginError
from commerce_hub.secure_browser import pinned_browser_proxy
from commerce_hub.vendors.shiloh_tack.login import PinnedHTTPS

HOST='shilohtack-portal.acumatica.com'
TENANT='Company'  # Explicit tenant shown in the user's portal; never use the site's Test default.
LOGIN_PATH='/(W(1))/Frames/Login.aspx'
HUB='/signalr/hubs/multifactorhub'


def clean_path(path):
    decoded=unquote(path)
    if any(ord(char)<32 for char in decoded) or '\\' in decoded or '..' in decoded.split('/'):
        raise ValueError
    return re.sub(r'^/\(W\(\d+\)\)','',decoded,flags=re.I).lower()


def allowed_url(url,method='GET'):
    try:
        p=urlsplit(url)
        if p.scheme!='https' or p.hostname!=HOST or p.port not in (None,443) or p.username or p.password:return False
        path=clean_path(p.path)
        if method=='GET':return not path.startswith('/entity/')
        return method=='POST' and (path=='/frames/login.aspx' or path in (HUB,HUB+'/negotiate'))
    except (ValueError,TypeError):return False


def approved_url(url):
    if not allowed_url(url):raise ConnectorLoginError('vendor_url_not_approved')
    p=urlsplit(url)
    if p.query or p.fragment or clean_path(p.path) not in ('/','/frames/login.aspx'):
        raise ConnectorLoginError('vendor_url_not_approved')
    return urlunsplit(('https',HOST,LOGIN_PATH,'',''))


def main_page(url):
    return allowed_url(url) and clean_path(urlsplit(url).path) in ('/main','/main.aspx','/default.aspx')


def error_code(text):
    """Classify vendor messages in memory; never return the message itself."""
    if re.search(r'captcha|verify.{0,20}human|challenge-platform',text,re.I):return 'vendor_interactive_verification_required'
    if re.search(r'password.{0,30}(expired|change)|change.{0,20}password',text,re.I):return 'vendor_password_change_required'
    if re.search(r'license|concurrent.{0,20}session|too many.{0,20}(login|session)',text,re.I):return 'vendor_session_limit_reached'
    if re.search(r'two.factor|multi.factor|one.time|verification code|confirmation request',text,re.I):return 'vendor_mfa_required'
    if re.search(r'incorrect|invalid|not valid|login failed|sign.in failed|locked',text,re.I):return 'vendor_login_rejected'
    return 'vendor_login_response_unrecognized'


def close_authenticated_session(cookies,connection_factory=PinnedHTTPS):
    """Use Acumatica's documented cookie-session logout, never its API login."""
    selected=[]
    for cookie in cookies:
        domain=cookie.get('domain','').lstrip('.').lower()
        if not domain or not (HOST==domain or HOST.endswith('.'+domain)):continue
        if not '/entity/auth/logout'.startswith(cookie.get('path') or '/'):continue
        name,value=cookie.get('name',''),cookie.get('value','')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',name) or re.search(r'[\r\n;]',value):continue
        selected.append(name+'='+value)
    if not selected:return False
    connection=connection_factory(HOST,timeout=15)
    try:
        connection.request('POST','/entity/auth/logout',body=b'',headers={
            'Cookie':'; '.join(selected),'Accept':'application/json','Origin':'https://'+HOST,
            'User-Agent':'CodeBI-CommerceHub/1.0'})
        response=connection.getresponse();body=response.read(32769)
        return response.status==204 and len(body)<=32768
    except Exception:return False
    finally:connection.close()


class InventoryBrowser:
    session_seconds=120
    request_limit=300
    def __init__(self):self.browser=self.context=self.page=None;self.playwright=None;self.proxy_context=None;self.authenticated=False;self.signing_out=False

    def open(self,url):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:raise ConnectorLoginError('inventory_browser_not_installed') from None
        self.proxy_context=pinned_browser_proxy(HOST,**({'lifetime':self.session_seconds,'max_bytes':512*1024*1024} if self.session_seconds>120 else {}))
        proxy,self.proxy_server=self.proxy_context.__enter__()
        self.playwright=sync_playwright().start()
        options={'headless':True,'proxy':proxy,'args':['--proxy-bypass-list=<-loopback>',
            '--disable-quic','--disable-background-networking','--force-webrtc-ip-handling-policy=disable_non_proxied_udp']}
        if sys.platform=='win32' and Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe').is_file():options['channel']='msedge'
        self.browser=self.playwright.chromium.launch(**options)
        self.context=self.browser.new_context(ignore_https_errors=False,service_workers='block',accept_downloads=False)
        self.requests=0
        def guard(route):
            self.requests+=1
            extra=self.additional_request_allowed(route.request)
            permitted=allowed_url(route.request.url,route.request.method) or extra or (self.signing_out and route.request.method=='POST' and main_page(route.request.url))
            if self.requests>self.request_limit or not permitted:route.abort()
            else:route.continue_()
        self.context.route('**/*',guard)
        self.page=self.context.new_page();self.page.set_default_timeout(20000)
        def dialog_handler(dialog):
            message=dialog.message
            if (self.signing_out and dialog.type=='confirm'
                and not re.search(r'other|all.sessions',message,re.I)
                and re.search(r'sign\s*out|log\s*(?:out|off)|are you sure',message,re.I)):
                dialog.accept()
            else:dialog.dismiss()
        self.page.on('dialog',dialog_handler)
        response=self.page.goto(url,wait_until='domcontentloaded',timeout=30000)
        if response is None or response.status!=200:raise ConnectorLoginError('vendor_service_unavailable')
        if self.page.locator('#txtUser').count()!=1 or self.page.locator('#txtPass').count()!=1 or self.page.locator('#btnLogin').count()!=1:
            raise ConnectorLoginError('vendor_login_structure_changed')
        tenant=self.page.locator('#cmbCompany')
        if tenant.count()!=1 or TENANT not in tenant.locator('option').evaluate_all('(options)=>options.map(option=>option.value)'):
            raise ConnectorLoginError('vendor_tenant_not_available')
        tenant.select_option(value=TENANT)
        if tenant.input_value()!=TENANT:raise ConnectorLoginError('vendor_tenant_not_available')

    def additional_request_allowed(self,request):
        return False

    def visible(self,selector):
        return self.page.locator(selector).first.is_visible()

    def authenticated_page(self):
        if not main_page(self.page.url) or self.visible('#txtPass'):return False
        logout=self.page.locator('a[href*="logout" i], [id*="logout" i], [id*="signout" i], [class~="logout" i]')
        logout_text=self.page.get_by_text(re.compile(r'^(?:sign\s*out|log\s*out)$',re.I))
        auth_cookie=any('auth' in cookie['name'].lower() and 'csrf' not in cookie['name'].lower() for cookie in self.context.cookies())
        return bool((logout.count() or logout_text.count()) and auth_cookie)

    def sign_in(self,credentials):
        self.page.locator('#txtUser').fill(credentials.username)
        self.page.locator('#txtPass').fill(credentials.password)
        # One normal button click. Do not alter MFA flags, send API login requests,
        # select a fallback provider or repeat rejected credentials.
        self.page.locator('#btnLogin').click(no_wait_after=True)
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            if self.authenticated_page():self.authenticated=True;return
            if any(self.visible(selector) for selector in ('#oneTimePasswordText','#multiFactorMenu','#multiFactorTip','#retryAfterDeny')):
                raise ConnectorLoginError('vendor_mfa_required')
            if self.visible('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], #captcha, #challenge-form'):
                raise ConnectorLoginError('vendor_interactive_verification_required')
            message=self.page.locator('#lblMsg')
            if message.count() and message.is_visible():
                text=message.inner_text().strip()
                if text:raise ConnectorLoginError(error_code(text))
            self.page.wait_for_timeout(250)
        raise ConnectorLoginError('vendor_authenticated_session_not_confirmed')

    def sign_out(self):
        # Close only this browser's authenticated session using the documented
        # logout endpoint. Login always follows the normal browser/MFA flow.
        self.signing_out=True
        if close_authenticated_session(self.context.cookies()):
            self.context.clear_cookies()
            self.page.goto('https://'+HOST+'/Main',wait_until='domcontentloaded',timeout=20000)
            self.page.locator('#txtPass').wait_for(state='visible',timeout=20000)
            self.authenticated=False;return
        # Fall back to observed application controls if API logout is unavailable.
        self.page.wait_for_load_state('load',timeout=15000)
        if self.page.locator('#btnUserMenu').count() and self.page.locator('#btnSignOut').count():
            self.page.locator('#btnUserMenu').click()
            self.page.locator('#btnSignOut').click()
            self.page.locator('#txtPass').wait_for(state='visible',timeout=20000)
            self.authenticated=False;return
        links=self.page.locator('a[href*="logout" i]')
        for i in range(links.count()):
            href=links.nth(i).get_attribute('href')
            target=urljoin(self.page.url,href or '')
            if allowed_url(target) and not (href or '').lower().startswith('javascript:'):
                self.page.goto(target,wait_until='domcontentloaded',timeout=20000)
                if self.visible('#txtPass'):self.authenticated=False;return
        for i in range(links.count()):
            link=links.nth(i)
            identity=(link.get_attribute('href') or '')+' '+(link.get_attribute('id') or '')
            if re.search(r'other|all.sessions|revoke',identity,re.I):continue
            # WebForms often renders Logout as a javascript: postback link in a
            # collapsed menu. Click the observed control, not its script string.
            link.evaluate('(element)=>element.click()')
            try:self.page.locator('#txtPass').wait_for(state='visible',timeout=10000)
            except Exception:continue
            self.authenticated=False;return
        controls=self.page.get_by_text(re.compile(r'^(?:sign\s*out|log\s*out)$',re.I))
        if controls.count():
            # The application may keep the observed Sign Out menu item collapsed.
            # Invoke that exact control; never choose "sign out other/all sessions".
            controls.first.evaluate('(element)=>element.click()')
            self.page.locator('#txtPass').wait_for(state='visible',timeout=20000)
            self.authenticated=False;return
        controls=self.page.locator('[id*="logout" i], [id*="signout" i]')
        for i in range(controls.count()):
            control=controls.nth(i);identity=control.get_attribute('id') or ''
            if re.search(r'other|all|reason|message|revoke',identity,re.I):continue
            control.evaluate('(element)=>element.click()')
            try:self.page.locator('#txtPass').wait_for(state='visible',timeout=10000)
            except Exception:continue
            self.authenticated=False;return
        raise ConnectorLoginError('vendor_logout_not_confirmed')

    def close(self):
        # No storage_state, screenshots, traces, HARs, browser logs or persisted cookies.
        for resource in (self.context,self.browser):
            if resource:
                try:resource.close()
                except Exception:pass
        if self.playwright:
            try:self.playwright.stop()
            except Exception:pass
        if self.proxy_context:
            try:self.proxy_context.__exit__(None,None,None)
            except Exception:pass


def verify(url,credentials,session_factory=InventoryBrowser):
    target=approved_url(url)
    if not credentials.username or not credentials.password:raise ConnectorLoginError('connection_not_configured')
    session=session_factory()
    try:
        session.open(target);session.sign_in(credentials);session.sign_out()
    except ConnectorLoginError:raise
    except Exception as exc:
        # Classify only a missing executable; browser exception text can include
        # sensitive page data and must never reach worker logs or result JSON.
        code='vendor_logout_not_confirmed' if session.authenticated is True else ('inventory_browser_not_installed' if 'executable doesn\'t exist' in str(exc).lower() else 'vendor_service_unavailable')
        raise ConnectorLoginError(code) from None
    finally:session.close()
