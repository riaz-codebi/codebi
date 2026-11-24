import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import create_app
from app.auth import verify_identity as check_provider_identity
from app.services import Identity


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'test-secret-' * 6,
                               'SUPABASE_URL': None, 'SUPABASE_KEY': None, 'APP_DATABASE_URL': None,
                               'APP_BASE_URL': 'http://localhost:5000',
                               'AUTH_STORE_PATH': Path(self.temp.name) / 'sessions.sqlite3'})
        self.app.config.update(SUPABASE_URL='https://example.supabase.co', SUPABASE_KEY='sb_publishable_test')
        self.client = self.app.test_client()
        self.store = self.app.extensions['auth_store']
        self.identity = Identity('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'admin@example.com', True)
        identity_patch = patch('app.auth.verify_identity', return_value=self.identity)
        identity_patch.start()
        self.addCleanup(identity_patch.stop)
        self.context = {'allowed': True, 'display_name': 'Test Admin', 'is_super_admin': True, 'clients': []}

    def csrf(self, client=None):
        client = client or self.client
        client.get('/auth/login')
        with client.session_transaction() as state:
            return state['csrf']

    def login_state(self, client=None, expiry=None):
        client = client or self.client
        sid = self.store.create({'kind': 'login', 'access_token': 'private-access-token',
                                 'refresh_token': 'private-refresh-token', 'auth_user_id': self.identity.auth_id,
                                 'expires_at': expiry or time.time() + 3600}, 3600)
        with client.session_transaction() as state:
            state['sid'] = sid
        return sid

    def pending(self, client=None):
        client = client or self.client
        sid = self.store.create({'kind': 'pending', 'provider': 'azure', 'verifier': 'private-verifier'}, 600)
        with client.session_transaction() as state:
            state['pending'] = sid
        return sid

    @staticmethod
    def result():
        return SimpleNamespace(session=SimpleNamespace(access_token='private-access-token',
            refresh_token='private-refresh-token', expires_at=time.time() + 3600))

    def test_public_pages_and_login_render(self):
        for path in ['/', '/services', '/products', '/assets', '/contact', '/auth/login']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        page = self.client.get('/auth/login')
        self.assertIn(b'Continue with Microsoft', page.data)
        self.assertIn(b'Continue with Google', page.data)
        self.assertEqual(page.headers['Cache-Control'], 'no-store')

    def test_missing_configuration_disables_login(self):
        self.app.config['SUPABASE_KEY'] = None
        token = self.csrf()
        response = self.client.post('/auth/login/google', data={'csrf_token': token})
        self.assertEqual(response.status_code, 503)

    def test_start_requires_post_and_csrf(self):
        self.assertEqual(self.client.get('/auth/login/google').status_code, 405)
        self.assertEqual(self.client.post('/auth/login/google').status_code, 400)
        self.assertEqual(self.client.post('/auth/login/not-a-provider').status_code, 404)

    def test_real_sdk_generates_pkce_for_each_provider(self):
        for provider in ('google', 'azure'):
            token = self.csrf()
            response = self.client.post('/auth/login/' + provider, data={'csrf_token': token})
            self.assertEqual(response.status_code, 302)
            self.assertIn('code_challenge=', response.location)
            self.assertIn('provider=' + provider, response.location)
            self.assertIn('scopes=email' if provider == 'azure' else 'scopes=openid', response.location)
            with self.client.session_transaction() as state:
                payload = self.store.load(state['pending'])
                self.assertEqual(payload['provider'], provider)
                self.assertNotIn(payload['verifier'], str(dict(state)))
            self.assertEqual(len(payload['verifier']) > 40, True)

    def test_start_ignores_attacker_host_for_callback(self):
        token = self.csrf()
        response = self.client.post('/auth/login/google', data={'csrf_token': token}, headers={'Host': 'evil.example'})
        # Wrong host cannot reuse the browser's CSRF cookie.
        self.assertEqual(response.status_code, 400)

    def test_callback_requires_browser_bound_pending_state(self):
        self.assertEqual(self.client.get('/auth/callback?code=foreign').status_code, 400)
        self.pending()
        other = self.app.test_client()
        self.assertEqual(other.get('/auth/callback?code=foreign').status_code, 400)

    def test_cancelled_callback_consumes_pending_state(self):
        sid = self.pending()
        self.assertEqual(self.client.get('/auth/callback?error=access_denied').status_code, 400)
        self.assertIsNone(self.store.load(sid))
        self.assertEqual(self.client.get('/auth/callback?code=replay').status_code, 400)

    @patch('app.auth.services.portal_login')
    @patch('app.auth.new_client')
    def test_callback_rotates_session_and_keeps_tokens_out_of_cookie(self, sdk, rpc):
        pending = self.pending()
        sdk.return_value.auth.exchange_code_for_session.return_value = self.result()
        rpc.return_value = self.context
        response = self.client.get('/auth/callback?code=one-time')
        self.assertEqual(response.location, '/portal/')
        sdk.return_value.auth.exchange_code_for_session.assert_called_once_with(
            {'auth_code': 'one-time', 'code_verifier': 'private-verifier'})
        rpc.assert_called_once_with(self.identity, 'azure')
        self.assertIsNone(self.store.load(pending))
        with self.client.session_transaction() as state:
            self.assertNotEqual(state['sid'], pending)
            self.assertNotIn('private-', str(dict(state)))
        self.assertNotIn('private-', response.headers.get('Set-Cookie', ''))
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])
        self.assertIn('SameSite=Lax', response.headers['Set-Cookie'])
        self.assertEqual(self.client.get('/auth/callback?code=one-time').status_code, 400)

    @patch('app.auth.revoke')
    @patch('app.auth.services.portal_login', return_value={'allowed': False})
    @patch('app.auth.new_client')
    def test_unprovisioned_identity_is_denied(self, sdk, rpc, revoke):
        self.pending()
        sdk.return_value.auth.exchange_code_for_session.return_value = self.result()
        self.assertEqual(self.client.get('/auth/callback?code=valid').status_code, 403)
        revoke.assert_called_once()
        with self.client.session_transaction() as state:
            self.assertNotIn('sid', state)

    @patch('app.auth.revoke')
    @patch('app.auth.new_client', side_effect=ValueError('sensitive-exception'))
    def test_oauth_failure_is_safe(self, sdk, revoke):
        self.pending()
        response = self.client.get('/auth/callback?code=valid')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b'sensitive-exception', response.data)

    def test_anonymous_portal_redirects_to_login(self):
        self.assertEqual(self.client.get('/portal/').location, '/auth/login')

    @patch('app.auth.services.portal_context')
    def test_role_checked_on_each_request_and_client_cannot_access_admin(self, rpc):
        self.login_state()
        rpc.return_value = {'allowed': True, 'display_name': 'Client Person', 'is_super_admin': False,
                            'clients': [{'id': 'abc', 'name': 'Client ABC', 'role': 'client_user'}]}
        response = self.client.get('/portal/')
        self.assertIn(b'<span class="portal-title-prefix">Service Portal for</span> Client ABC', response.data)
        self.assertNotIn(b'Primary Administration', response.data)
        self.assertEqual(self.client.get('/portal/admin').status_code, 403)
        rpc.return_value = self.context
        self.assertEqual(self.client.get('/portal/admin').status_code, 200)
        self.assertEqual(rpc.call_count, 3)

    @patch('app.auth.revoke')
    @patch('app.auth.services.portal_context', return_value={'allowed': False})
    def test_deactivated_user_loses_access(self, rpc, revoke):
        sid = self.login_state()
        self.assertEqual(self.client.get('/portal/').status_code, 403)
        self.assertIsNone(self.store.load(sid))

    @patch('app.auth.services.portal_context')
    def test_browser_sessions_are_isolated(self, rpc):
        self.login_state()
        rpc.return_value = self.context
        self.assertEqual(self.client.get('/portal/').status_code, 200)
        self.assertEqual(self.app.test_client().get('/portal/').location, '/auth/login')

    @patch('app.auth.services.portal_context')
    @patch('app.auth.new_client')
    def test_expired_access_token_refreshes_server_side(self, sdk, rpc):
        sid = self.login_state(expiry=time.time() - 1)
        sdk.return_value.auth.refresh_session.return_value = self.result()
        rpc.return_value = self.context
        self.assertEqual(self.client.get('/portal/').status_code, 200)
        sdk.return_value.auth.refresh_session.assert_called_once_with('private-refresh-token')
        self.assertGreater(self.store.load(sid)['expires_at'], time.time())

    @patch('app.auth.revoke')
    def test_logout_is_csrf_protected_and_invalidates_server_state(self, revoke):
        token = self.csrf()
        sid = self.login_state()
        self.assertEqual(self.client.get('/auth/logout').status_code, 405)
        self.assertEqual(self.client.post('/auth/logout').status_code, 400)
        response = self.client.post('/auth/logout', data={'csrf_token': token})
        self.assertEqual(response.location, '/')
        self.assertIsNone(self.store.load(sid))

    def test_server_store_encrypts_and_expires_state(self):
        sid = self.store.create({'refresh': 'do-not-store-plaintext'}, 600)
        self.assertNotIn(b'do-not-store-plaintext', Path(self.store.path).read_bytes())
        self.assertNotIn(sid.encode(), Path(self.store.path).read_bytes())
        self.assertEqual(self.store.load(sid, consume=True)['refresh'], 'do-not-store-plaintext')
        self.assertIsNone(self.store.load(sid))
        expired = self.store.create({'secret': 'expired'}, -1)
        self.assertIsNone(self.store.load(expired))

    def test_untrusted_or_insecure_production_origin_is_rejected(self):
        for origin in ('http://example.com', 'https://example.com/path', 'https://user:pass@example.com'):
            with self.assertRaises(ValueError):
                create_app({'APP_BASE_URL': origin})

    @patch('app.auth.new_client')
    def test_verification_uses_provider_identity_not_editable_metadata(self, sdk):
        provider_identity = SimpleNamespace(provider='azure', identity_data={'email': 'admin@example.com', 'email_verified': False})
        user = SimpleNamespace(id=self.identity.auth_id, email='admin@example.com', email_confirmed_at='confirmed',
                               identities=[provider_identity], user_metadata={'email_verified': True, 'role': 'super_admin'})
        sdk.return_value.auth.get_user.return_value = SimpleNamespace(user=user)
        result = check_provider_identity('token', 'azure')
        self.assertFalse(result.verified)
        provider_identity.identity_data['email_verified'] = True
        self.assertTrue(check_provider_identity('token', 'azure').verified)
        self.assertFalse(check_provider_identity('token', 'google').verified)
        provider_identity.identity_data['email'] = 'different@example.com'
        self.assertFalse(check_provider_identity('token', 'azure').verified)

    @patch('app.auth.services.portal_context')
    def test_existing_server_session_upgrades_without_relinking(self, context):
        sid = self.login_state()
        payload = self.store.load(sid)
        payload.pop('auth_user_id')
        self.store.save(sid, payload)
        context.return_value = self.context
        self.assertEqual(self.client.get('/portal/').status_code, 200)
        self.assertEqual(self.store.load(sid)['auth_user_id'], self.identity.auth_id)
        context.assert_called_once_with(self.identity.auth_id)


if __name__ == '__main__':
    unittest.main()
