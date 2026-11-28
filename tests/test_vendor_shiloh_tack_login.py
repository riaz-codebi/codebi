import unittest
import ssl
from unittest.mock import Mock, patch, call

from commerce_hub.connector_contracts import Credentials
from commerce_hub.connector_contracts import ConnectorLoginError
from commerce_hub.vendors.shiloh_tack.login import AccountPage, PinnedHTTPS, VendorSession, approved_host, login_token, verify

PAGE = '<script>var loginhash="' + 'a'*64 + '";</script><input type="password" id="lipass">'
AUTHENTICATED = '<button onclick="logoutaccount()">Log out</button>'


class LoginTests(unittest.TestCase):
    def session(self, reply='SUCCESS:0', after=AUTHENTICATED):
        session = Mock()
        session.request.side_effect = [PAGE,reply,after]
        return session

    def test_success_requires_authenticated_followup_and_closes_session(self):
        session = self.session()
        credentials = Credentials('dealer@example.com','p@ss &word')
        verify('https://www.shilohtack.com/search.php',credentials,lambda host:session)
        calls = session.request.call_args_list
        self.assertEqual(calls[1].args[0],'POST')
        self.assertIn('action=loginaccount',calls[1].args[1])
        self.assertEqual(calls[1].args[2],b'email=dealer%40example.com&pw=p%40ss+%26word')
        self.assertEqual(calls[2].args,('GET','/search.php'))
        session.close.assert_called_once()

    def test_http_ok_or_success_without_session_cannot_verify(self):
        for reply, after, expected in [('200 OK',AUTHENTICATED,'vendor_login_response_unrecognized'),
                                      ('SUCCESS:0',PAGE,'vendor_authenticated_session_not_confirmed'),
                                      ('ERROR:invalid','', 'vendor_login_rejected'),
                                      ('ERROR:captcha required','', 'vendor_interactive_verification_required')]:
            session = self.session(reply,after)
            with self.subTest(reply=reply),self.assertRaises(ConnectorLoginError) as caught:
                verify('https://www.shilohtack.com/search.php',Credentials('dealer','secret'),lambda host:session)
            self.assertEqual(caught.exception.code,expected)
            session.close.assert_called_once()
            self.assertEqual(sum(c.args[0]=='POST' for c in session.request.call_args_list),1)

    def test_sitewide_captcha_script_does_not_block_login(self):
        session = self.session()
        session.request.side_effect = [PAGE+'<script src="/recaptcha/api.js"></script>','SUCCESS:0',AUTHENTICATED]
        verify('https://www.shilohtack.com/search.php',Credentials('dealer','secret'),lambda host:session)

    def test_changed_structure_never_submits_credentials(self):
        for page in ('No login token', PAGE+AUTHENTICATED):
            session = self.session()
            session.request.side_effect = [page]
            with self.assertRaises(ConnectorLoginError):
                verify('https://www.shilohtack.com/search.php',Credentials('dealer','secret'),lambda host:session)
            self.assertEqual(session.request.call_count,1)

    def test_url_allowlist_excludes_credentials_queries_other_hosts_and_paths(self):
        for url in ('http://www.shilohtack.com/search.php','https://evil.example.com/search.php',
                    'https://www.shilohtack.com.evil.example/search.php','https://user:pw@www.shilohtack.com/',
                    'https://www.shilohtack.com:8443/','https://www.shilohtack.com/search.php?token=value',
                    'https://www.shilohtack.com/private.php','https://127.0.0.1/'):
            with self.subTest(url=url),self.assertRaises(ConnectorLoginError):
                approved_host(url)

    def test_apex_and_www_normalize_to_plain_canonical_hostname(self):
        for host in ('shilohtack.com','www.shilohtack.com'):
            self.assertEqual(approved_host('https://'+host+'/search.php'),'www.shilohtack.com')
            self.assertEqual(VendorSession(host).host,'www.shilohtack.com')
            factory = Mock(return_value=self.session())
            verify('https://'+host+'/search.php',Credentials('dealer','secret'),factory)
            factory.assert_called_once_with('www.shilohtack.com')
        with self.assertRaises(ConnectorLoginError):
            VendorSession('evil.example.com')

    def test_success_suffix_allows_bounded_paths_emails_and_spaces(self):
        for reply in ('SUCCESS','SUCCESS:','SUCCESS:/account/view.php','SUCCESS:dealer@example.com',
                      'SUCCESS:Dealer account ready', 'SUCCESS:'+'x'*256):
            with self.subTest(reply=reply):
                session = self.session(reply)
                verify('https://www.shilohtack.com/search.php',Credentials('dealer','secret'),lambda host:session)
                self.assertEqual(session.request.call_count,3)
        for reply in ('SUCCESS:'+'x'*257,'SUCCESS:dealer\nERROR:bad','SUCCESS:dealer\rhidden'):
            with self.subTest(reply=reply),self.assertRaises(ConnectorLoginError):
                verify('https://www.shilohtack.com/search.php',Credentials('dealer','secret'),lambda host:self.session(reply))
        # A permissive suffix never substitutes for authenticated follow-up evidence.
        with self.assertRaises(ConnectorLoginError):
            verify('https://www.shilohtack.com/search.php',Credentials('dealer','secret'),
                   lambda host:self.session('SUCCESS:dealer@example.com',PAGE))

    def test_hidden_tokens_handle_attribute_order_and_preserve_validation(self):
        for page in ('<input type="hidden" name="lc" value="abc-123">',
                     "<input value='abc-123' name='lc' type='hidden'>",
                     '<input VALUE="abc-123" TYPE="HIDDEN" NAME="lc"/>',
                     '<input type=hidden name=lc value=abc-123>',
                     '<input type="hidden" name="lc" value="abc&#45;123">'):
            with self.subTest(page=page):
                self.assertEqual(login_token(page),'abc-123')
                session = self.session()
                session.request.side_effect = [page,'SUCCESS:0',AUTHENTICATED]
                verify('https://www.shilohtack.com/search.php',Credentials('dealer','secret'),lambda host:session)
                self.assertIn('lc=abc-123',session.request.call_args_list[1].args[1])
        for value in ('','x'*257,'bad token','https://other.example','a&b'):
            self.assertIsNone(login_token('<input type="hidden" name="lc" value="'+value+'">'))
        self.assertIsNone(login_token('<input type="text" name="lc" value="abc-123">'))

    def test_conflicting_login_tokens_stop_before_post(self):
        page = PAGE+'<input type="hidden" name="lc" value="different">'
        self.assertIsNone(login_token(page))
        session = self.session()
        session.request.side_effect = [page]
        with self.assertRaises(ConnectorLoginError):
            verify('https://www.shilohtack.com/search.php',Credentials('dealer','secret'),lambda host:session)
        self.assertEqual(session.request.call_count,1)
        self.assertEqual(login_token(PAGE+'<input type="hidden" name="lc" value="'+'a'*64+'">'),'a'*64)

    def test_logout_detection_covers_control_attributes_and_nested_text(self):
        for markup in ('<button id="logout">Account</button>',
                       '<input type="submit" value="Sign out">',
                       '<input type="button" name="logout" value="Exit">',
                       '<a href="#">Log <span>out</span></a>',
                       '<button><span>Sign</span> out</button>',
                       '<a onclick="logoutaccount()">Exit</a>'):
            with self.subTest(markup=markup):
                page = AccountPage();page.feed(markup)
                self.assertTrue(page.logout)

    def test_non_controls_and_hidden_logout_markup_do_not_confirm_login(self):
        for markup in ('<p>Log out</p>', '<input type="hidden" name="logout" value="1">',
                       '<input type="text" value="signout">', '<button hidden>Log out</button>',
                       '<div aria-hidden="true"><button name="logout">Exit</button></div>',
                       '<div style="display: none"><button>Sign out</button></div>',
                       '<button><span hidden>Log out</span>Account</button>',
                       '<script>logoutaccount()</script>', '<template><button>Log out</button></template>'):
            with self.subTest(markup=markup):
                page = AccountPage();page.feed(markup)
                self.assertFalse(page.logout)

    @patch('commerce_hub.vendors.shiloh_tack.login.socket.getaddrinfo')
    @patch('commerce_hub.vendors.shiloh_tack.login.socket.create_connection')
    def test_private_or_mixed_dns_is_rejected_before_connect(self, connect, resolve):
        for addresses in (['127.0.0.1'],['::1'],['169.254.169.254'],['8.8.8.8','10.0.0.1']):
            resolve.return_value = [(2,1,6,'',(address,443)) for address in addresses]
            with self.assertRaises(ConnectorLoginError):
                PinnedHTTPS('www.shilohtack.com').connect()
        connect.assert_not_called()

    @patch('commerce_hub.vendors.shiloh_tack.login.ssl.create_default_context')
    @patch('commerce_hub.vendors.shiloh_tack.login.socket.create_connection')
    @patch('commerce_hub.vendors.shiloh_tack.login.public_addresses',return_value=['8.8.8.8'])
    def test_public_ip_is_pinned_and_tls_checks_vendor_hostname(self, resolve, connect, context):
        connection = PinnedHTTPS('www.shilohtack.com',timeout=15)
        connection.connect()
        connect.assert_called_once_with(('8.8.8.8',443),timeout=15)
        context.return_value.wrap_socket.assert_called_once_with(connect.return_value,server_hostname='www.shilohtack.com')
        resolve.assert_called_once_with('www.shilohtack.com')

    @patch('commerce_hub.vendors.shiloh_tack.login.ssl.create_default_context')
    @patch('commerce_hub.vendors.shiloh_tack.login.socket.create_connection')
    @patch('commerce_hub.vendors.shiloh_tack.login.public_addresses',return_value=['2606:4700:4700::1111','8.8.8.8'])
    def test_failed_ipv6_falls_back_without_resolving_again(self, resolve, connect, context):
        connect.side_effect = [OSError('IPv6 unreachable'),Mock()]
        connection = PinnedHTTPS('www.shilohtack.com',timeout=15)
        connection.connect()
        self.assertEqual(connect.call_args_list,[call(('2606:4700:4700::1111',443),timeout=15),call(('8.8.8.8',443),timeout=15)])
        resolve.assert_called_once()
        context.return_value.wrap_socket.assert_called_once()

    @patch('commerce_hub.vendors.shiloh_tack.login.ssl.create_default_context')
    @patch('commerce_hub.vendors.shiloh_tack.login.socket.create_connection')
    @patch('commerce_hub.vendors.shiloh_tack.login.public_addresses',return_value=['8.8.8.8','1.1.1.1'])
    def test_tls_failure_closes_socket_and_next_ip_still_checks_hostname(self, resolve, connect, context):
        first,second,wrapped = Mock(),Mock(),Mock()
        connect.side_effect = [first,second]
        context.return_value.wrap_socket.side_effect = [ssl.SSLError('bad TLS'),wrapped]
        connection = PinnedHTTPS('www.shilohtack.com',timeout=15)
        connection.connect()
        first.close.assert_called_once()
        second.close.assert_not_called()
        self.assertIs(connection.sock,wrapped)
        self.assertEqual(context.return_value.wrap_socket.call_args_list,
                         [call(first,server_hostname='www.shilohtack.com'),call(second,server_hostname='www.shilohtack.com')])

    @patch('commerce_hub.vendors.shiloh_tack.login.ssl.create_default_context')
    @patch('commerce_hub.vendors.shiloh_tack.login.socket.create_connection')
    @patch('commerce_hub.vendors.shiloh_tack.login.public_addresses',return_value=['8.8.8.8','1.1.1.1'])
    def test_all_ips_fail_raises_last_error_and_closes_tls_socket(self, resolve, connect, context):
        sock = Mock()
        last = OSError('last connection error')
        connect.side_effect = [sock,last]
        context.return_value.wrap_socket.side_effect = ssl.SSLError('first TLS error')
        with self.assertRaises(OSError) as caught:
            PinnedHTTPS('www.shilohtack.com',timeout=15).connect()
        self.assertIs(caught.exception,last)
        sock.close.assert_called_once()

    @patch('commerce_hub.vendors.shiloh_tack.login.ssl.create_default_context')
    @patch('commerce_hub.vendors.shiloh_tack.login.socket.create_connection')
    @patch('commerce_hub.vendors.shiloh_tack.login.public_addresses',return_value=['8.8.8.8','1.1.1.1'])
    def test_non_network_tls_error_also_closes_socket_without_retry(self, resolve, connect, context):
        context.return_value.wrap_socket.side_effect = RuntimeError('unexpected TLS failure')
        with self.assertRaises(RuntimeError):
            PinnedHTTPS('www.shilohtack.com',timeout=15).connect()
        connect.return_value.close.assert_called_once()
        connect.assert_called_once()

    def test_timeout_does_not_expose_values_or_retry_login(self):
        session = self.session()
        session.request.side_effect = [PAGE,TimeoutError('secret-password: session-cookie')]
        with self.assertRaises(ConnectorLoginError) as caught:
            verify('https://www.shilohtack.com/search.php',Credentials('dealer','secret-password'),lambda host:session)
        self.assertEqual(str(caught.exception),'vendor_service_unavailable')
        self.assertEqual(session.request.call_count,2)
        session.close.assert_called_once()

    @patch('commerce_hub.vendors.shiloh_tack.login.PinnedHTTPS')
    def test_memory_cookie_jar_is_reused_and_redirects_are_not_followed(self, transport):
        from email.message import Message
        response = Mock(status=200)
        response.read.return_value = b'ok'
        headers = Message(); headers['Set-Cookie']='PHPSESSID=fixture-session; Path=/; Secure; HttpOnly'
        response.info.return_value = headers
        transport.return_value.getresponse.return_value = response
        session = VendorSession('www.shilohtack.com')
        session.request('GET','/clientlogin.php')
        session.request('GET','/search.php')
        self.assertIn('PHPSESSID=fixture-session',transport.return_value.request.call_args.kwargs['headers']['Cookie'])
        response.status = 302
        with self.assertRaises(ConnectorLoginError):
            session.request('POST','/vsadmin/ajaxservice.php?action=loginaccount',b'private')
        self.assertEqual(transport.return_value.request.call_count,3)
        session.close()
        self.assertEqual(list(session.cookies),[])
