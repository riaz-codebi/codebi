import socket
import threading
import unittest
from unittest.mock import Mock,patch

from commerce_hub.connector_contracts import Credentials,ConnectorLoginError
from commerce_hub.secure_browser import pinned_browser_proxy
from commerce_hub.vendors.shiloh_tack.inventory_login import HOST,LOGIN_PATH,TENANT,InventoryBrowser,allowed_url,approved_url,close_authenticated_session,error_code,main_page,verify


class InventoryLoginTests(unittest.TestCase):
    def test_only_inventory_https_origin_and_login_paths_are_accepted(self):
        for path in ('/','/Frames/Login.aspx','/(W(1))/Frames/Login.aspx'):
            self.assertEqual(approved_url('https://'+HOST+path),'https://'+HOST+LOGIN_PATH)
        for url in ('http://'+HOST+'/', 'https://evil.example/','https://'+HOST+'.evil.example/',
                    'https://user:password@'+HOST+'/', 'https://'+HOST+':8443/',
                    'https://'+HOST+'/Main','https://'+HOST+'/Frames/Login.aspx?token=secret',
                    'https://127.0.0.1/','https://'+HOST+'/%2e%2e/Frames/Login.aspx'):
            with self.subTest(url=url),self.assertRaises(ConnectorLoginError):approved_url(url)

    def test_normal_mfa_hub_allowed_but_api_login_and_other_posts_blocked(self):
        self.assertTrue(allowed_url('https://'+HOST+'/(W(1))/signalr/hubs/MultifactorHub/negotiate','POST'))
        self.assertTrue(allowed_url('https://'+HOST+LOGIN_PATH,'POST'))
        self.assertFalse(allowed_url('https://'+HOST+'/entity/auth/login','POST'))
        self.assertFalse(allowed_url('https://'+HOST+'/Main','POST'))
        self.assertFalse(allowed_url('https://'+HOST+'/Files/../Main'))
        self.assertFalse(allowed_url('https://'+HOST+'/Frames/Login.aspx','DELETE'))
        self.assertTrue(main_page('https://'+HOST+'/(W(1))/Main'))

    def test_normal_login_and_logout_use_one_browser_attempt_and_always_close(self):
        session=Mock()
        credentials=Credentials('fixture-user','fixture-password')
        verify('https://'+HOST+LOGIN_PATH,credentials,lambda:session)
        session.open.assert_called_once_with('https://'+HOST+LOGIN_PATH)
        session.sign_in.assert_called_once_with(credentials)
        session.sign_out.assert_called_once_with()
        session.close.assert_called_once_with()
        self.assertEqual(TENANT,'Company')

    def test_mfa_rejection_and_structure_errors_are_not_retried(self):
        for code in ('vendor_mfa_required','vendor_login_rejected','vendor_login_structure_changed'):
            session=Mock();session.sign_in.side_effect=ConnectorLoginError(code)
            with self.subTest(code=code),self.assertRaises(ConnectorLoginError) as caught:
                verify('https://'+HOST+LOGIN_PATH,Credentials('fixture','secret'),lambda:session)
            self.assertEqual(caught.exception.code,code)
            session.sign_in.assert_called_once();session.sign_out.assert_not_called();session.close.assert_called_once()

    def test_exceptions_and_logout_failures_do_not_expose_sensitive_text(self):
        session=Mock();session.sign_in.side_effect=RuntimeError('password=secret;cookie=private')
        with self.assertRaises(ConnectorLoginError) as caught:
            verify('https://'+HOST+LOGIN_PATH,Credentials('fixture','secret'),lambda:session)
        self.assertEqual(str(caught.exception),'vendor_service_unavailable')
        session.close.assert_called_once()
        session=Mock();session.sign_out.side_effect=ConnectorLoginError('vendor_logout_not_confirmed')
        with self.assertRaisesRegex(ConnectorLoginError,'vendor_logout_not_confirmed'):
            verify('https://'+HOST+LOGIN_PATH,Credentials('fixture','secret'),lambda:session)
        session.close.assert_called_once()

    def test_vendor_messages_are_classified_without_returning_the_message(self):
        self.assertEqual(error_code('Incorrect password for fixture'),'vendor_login_rejected')
        self.assertEqual(error_code('Enter verification code'),'vendor_mfa_required')
        self.assertEqual(error_code('Password expired'),'vendor_password_change_required')
        self.assertEqual(error_code('Concurrent session license limit'),'vendor_session_limit_reached')

    def test_documented_logout_uses_only_this_hosts_cookie_session_and_no_password(self):
        connection=Mock();connection.getresponse.return_value.status=204;connection.getresponse.return_value.read.return_value=b''
        factory=Mock(return_value=connection)
        cookies=[{'domain':HOST,'path':'/','name':'.ASPXAUTH','value':'fixture-cookie'},
                 {'domain':'evil.example','path':'/','name':'private','value':'other-cookie'}]
        self.assertTrue(close_authenticated_session(cookies,factory))
        args=connection.request.call_args
        self.assertEqual(args.args,('POST','/entity/auth/logout'))
        self.assertEqual(args.kwargs['body'],b'')
        self.assertEqual(args.kwargs['headers']['Cookie'],'.ASPXAUTH=fixture-cookie')
        connection.close.assert_called_once()

    def test_logout_redirect_or_failure_never_counts_as_confirmed(self):
        for status in (200,302,401,403,500):
            connection=Mock();connection.getresponse.return_value.status=status;connection.getresponse.return_value.read.return_value=b''
            self.assertFalse(close_authenticated_session([{'domain':HOST,'name':'.ASPXAUTH','value':'fixture','path':'/'}],lambda *_a,**_k:connection))

    def test_successful_cleanup_uses_logout_then_returns_to_login(self):
        session=InventoryBrowser();session.page=Mock();session.context=Mock();session.authenticated=True
        with patch('commerce_hub.vendors.shiloh_tack.inventory_login.close_authenticated_session',return_value=True):session.sign_out()
        session.context.clear_cookies.assert_called_once()
        session.page.goto.assert_called_once_with('https://'+HOST+'/Main',wait_until='domcontentloaded',timeout=20000)
        session.page.locator.assert_called_with('#txtPass')
        self.assertFalse(session.authenticated)

    def test_http_ok_without_authentication_evidence_is_not_success(self):
        session=InventoryBrowser();session.page=Mock();session.context=Mock()
        session.page.url='https://'+HOST+'/Main';session.visible=Mock(return_value=False)
        session.context.cookies.return_value=[{'name':'ASP.NET_SessionId'}]
        self.assertFalse(session.authenticated_page())
        session.context.cookies.return_value=[{'name':'.ASPXAUTH'}]
        session.page.locator.return_value.count.return_value=1
        self.assertTrue(session.authenticated_page())
        session.page.url='https://'+HOST+LOGIN_PATH
        self.assertFalse(session.authenticated_page())

    def test_visible_mfa_prompt_stops_after_one_normal_login_click(self):
        session=InventoryBrowser();session.page=Mock();session.authenticated_page=Mock(return_value=False)
        session.visible=Mock(side_effect=lambda selector:selector=='#oneTimePasswordText')
        with self.assertRaisesRegex(ConnectorLoginError,'vendor_mfa_required'):
            session.sign_in(Credentials('fixture','secret'))
        session.page.locator.return_value.click.assert_called_once()


class BrowserTunnelTests(unittest.TestCase):
    def client(self,proxy):
        connection=socket.socket();connection.settimeout(3)
        connection.connect(('127.0.0.1',int(proxy['server'].rsplit(':',1)[1])))
        self.addCleanup(connection.close)
        return connection

    def test_redirect_authorities_private_addresses_ports_and_plain_http_are_denied(self):
        with patch('commerce_hub.secure_browser.public_addresses',return_value=['8.8.8.8']),patch('commerce_hub.secure_browser.socket.create_connection') as dial:
            with pinned_browser_proxy(HOST) as (proxy,_server):
                for request in ('CONNECT 127.0.0.1:443 HTTP/1.1','CONNECT '+HOST+':8443 HTTP/1.1',
                                'CONNECT evil.example:443 HTTP/1.1','CONNECT user@'+HOST+':443 HTTP/1.1',
                                'GET http://'+HOST+'/ HTTP/1.1'):
                    connection=self.client(proxy);connection.sendall((request+'\r\n\r\n').encode())
                    self.assertIn(b'403 Forbidden',connection.recv(1024));connection.close()
            dial.assert_not_called()

    def test_tunnel_connects_to_the_validated_ip_and_keeps_tls_payload_opaque(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(4)
        self.addCleanup(listener.close)
        port=listener.getsockname()[1]
        def echo():
            peer,_=listener.accept()
            try:peer.sendall(peer.recv(1024))
            finally:peer.close()
        thread=threading.Thread(target=echo,daemon=True);thread.start()
        def dial(address,timeout):
            self.assertEqual(address,('8.8.8.8',443))
            upstream=socket.socket();upstream.settimeout(timeout);upstream.connect(('127.0.0.1',port));return upstream
        with patch('commerce_hub.secure_browser.public_addresses',return_value=['8.8.8.8']) as dns,patch('commerce_hub.secure_browser.socket.create_connection',side_effect=dial):
            with pinned_browser_proxy(HOST) as (proxy,_server):
                connection=self.client(proxy)
                connection.sendall(('CONNECT '+HOST+':443 HTTP/1.1\r\n\r\n').encode())
                self.assertIn(b'200 Connection Established',connection.recv(1024))
                connection.sendall(b'opaque-fixture-tls-bytes')
                self.assertEqual(connection.recv(1024),b'opaque-fixture-tls-bytes')
                connection.close()
            dns.assert_called_once_with(HOST)
        thread.join(timeout=2)
