"""Self-registration uses the same real, isolated SQLAlchemy database as admin tests."""
import time
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import func, select
import test_administration as fixtures
from app import services
from app.database import transaction
from app.models import AuditEvent, Client, Membership, PublicInquiry, User, UserRole

NEW_AUTH = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        fixtures.AdministrationTests.setUp(self)
        self.app.config.update(SUPABASE_URL='https://example.supabase.co', SUPABASE_KEY='sb_publishable_test')
        self.app.config['INQUIRY_MIN_SECONDS'] = 0
        sender = patch('app.auth.send_notification')
        self.send_confirmation = sender.start()
        self.addCleanup(sender.stop)
        inquiry_sender = patch('resend.Emails.send')
        self.send_inquiry_confirmation = inquiry_sender.start()
        self.addCleanup(inquiry_sender.stop)
        self.identity = services.Identity(NEW_AUTH, 'signup@example.com', True, 'New Person')
        self.client = self.app.test_client()
        self.store = self.app.extensions['auth_store']

    browser = fixtures.AdministrationTests.browser

    def pending(self, ttl=1800):
        sid = self.store.create({'kind': 'onboarding', 'auth_user_id': NEW_AUTH, 'email': self.identity.email,
            'display_name': self.identity.display_name, 'verified': True, 'provider': 'google',
            'access_token': 'signup-access', 'refresh_token': 'signup-refresh', 'expires_at': time.time()+3600}, ttl)
        with self.client.session_transaction() as state:
            state['onboarding'] = sid
        return sid

    def csrf(self):
        self.client.get('/auth/login')
        with self.client.session_transaction() as state:
            return state['csrf']

    def data(self, **overrides):
        return {'csrf_token': self.csrf(), 'name': 'New Client Company', 'owner_name': 'Company Owner',
            'timezone': 'UTC', 'country_code': 'US', 'state_region_code': 'US-TX',
            'inquiry': 'Help with quarterly reporting', **overrides}

    def counts(self):
        with self.app.app_context(), transaction() as db:
            return tuple(db.scalar(select(func.count()).select_from(table)) for table in (User, Client, Membership))

    def test_sign_in_only_and_public_contact_form(self):
        home = self.client.get('/')
        self.assertIn(b'href="/auth/login">Service portal</a>', home.data)
        self.assertIn(b'href="/contact">Discuss a Project', home.data)
        self.assertEqual(home.data.count(b'class="ecosystem-visual '), 1)
        self.assertIn(b'ecosystem-visual ecosystem-light', home.data)
        self.assertNotIn(b'Send inquiry', home.data)
        self.assertNotIn(b'<dialog', home.data)
        login = self.client.get('/auth/login')
        self.assertIn(b'Existing user sign-in', login.data)
        self.assertIn(b'class="login-back" href="/">&larr; back', login.data)
        self.assertNotIn(b'id="navLinks"', login.data)
        self.assertNotIn(b'Welcome to CodeBI', login.data)
        self.assertIn(b'Email sign-in', login.data)
        self.assertNotIn(b'Coming soon', login.data)
        self.assertLess(login.data.index(b'Continue with Microsoft'), login.data.index(b'Email sign-in'))
        self.assertNotIn(b'New to CodeBI?', login.data)
        self.assertNotIn(b'Send inquiry', login.data)
        self.assertNotIn(b'<dialog', login.data)
        self.assertEqual(self.client.get('/auth/signup').status_code, 404)
        contact = self.client.get('/contact')
        self.assertEqual(contact.data.count(b'Send inquiry'), 1)
        self.assertNotIn(b'Good fit for', contact.data)

    def test_public_inquiry_is_queued_without_email_or_creating_an_account(self):
        before = self.counts()
        values = {'name': 'Casey', 'email': 'casey@example.com', 'company': 'Example Co',
                  'details': 'We need help with reporting across sales and inventory so our team can plan purchases and reduce manual spreadsheet work', 'csrf_token': self.csrf()}
        page = self.client.get('/contact')
        values['inquiry_token'] = re.search(rb'name="inquiry_token" value="([^"]+)"', page.data).group(1).decode()
        self.assertEqual(self.client.post('/contact', data={**values, 'csrf_token': ''}).status_code, 400)
        self.assertEqual(self.client.post('/contact', data={**values, 'email': 'invalid'}).status_code, 422)
        self.send_inquiry_confirmation.assert_not_called()
        response = self.client.post('/contact', data=values, follow_redirects=True)
        self.assertIn(b'Our onboarding team will review your inquiry', response.data)
        self.send_inquiry_confirmation.assert_not_called()
        response = self.client.post('/contact', data={**values, 'details': 'Our current reports require manual updates every week and we need a reliable dashboard with accurate totals for each department'}, follow_redirects=True)
        self.assertIn(b'Our onboarding team will review your inquiry', response.data)
        self.send_inquiry_confirmation.assert_not_called()
        admin_view = self.browser(fixtures.ADMIN).get('/portal/admin/inquiries?q=casey')
        self.assertEqual(admin_view.status_code, 200)
        self.assertIn(b'We need help with reporting across sales and inventory so our team can plan purchases and reduce manual spreadsheet work', admin_view.data)
        self.assertIn(b'Our current reports require manual updates every week and we need a reliable dashboard with accurate totals for each department', admin_view.data)
        self.assertEqual(self.browser(fixtures.CLIENT).get('/portal/admin/inquiries').status_code, 403)
        with self.app.app_context(), transaction() as db:
            self.assertEqual(set(db.scalars(select(PublicInquiry.details))),
                             {'We need help with reporting across sales and inventory so our team can plan purchases and reduce manual spreadsheet work', 'Our current reports require manual updates every week and we need a reliable dashboard with accurate totals for each department'})
        self.assertEqual(self.counts(), before)

    def test_only_unknown_verified_identity_is_eligible(self):
        before = self.counts()
        with self.app.app_context():
            result = services.portal_login(self.identity, 'google')
            self.assertTrue(result['onboarding'])
            self.assertFalse(result['allowed'])
            self.assertNotIn('onboarding', services.portal_login(services.Identity(NEW_AUTH, self.identity.email, False), 'google'))
            self.assertNotIn('onboarding', services.portal_login(services.Identity(NEW_AUTH, 'admin@example.com', True), 'google'))
        self.assertEqual(self.counts(), before)

    def test_reserved_or_inactive_identity_cannot_self_register(self):
        with self.app.app_context(), transaction() as db:
            db.get(User, self.regular.id).is_active = False
        for identity in [services.Identity(fixtures.REGULAR, self.regular.email, True),
                         services.Identity(NEW_AUTH, self.unbound.email, True)]:
            with self.app.app_context():
                self.assertNotIn('onboarding', services.portal_login(identity, 'google'))
                with self.assertRaises(services.Forbidden):
                    services.register_client(identity, 'google', {'name': 'Bypass'}, 'Bad Actor')

    @patch('app.auth.verify_identity')
    @patch('app.auth.new_client')
    @patch('app.auth.revoke')
    def test_unknown_identity_is_redirected_to_public_inquiry(self, revoke, sdk, verify):
        verify.return_value = self.identity
        sdk.return_value.auth.exchange_code_for_session.return_value = SimpleNamespace(session=SimpleNamespace(
            access_token='signup-access', refresh_token='signup-refresh', expires_at=time.time()+3600))
        pending = self.store.create({'kind': 'pending', 'provider': 'google', 'verifier': 'test-verifier'}, 600)
        with self.client.session_transaction() as state:
            state['pending'] = pending
        response = self.client.get('/auth/callback?code=test-code')
        self.assertEqual(response.location, '/contact#inquiry')
        with self.client.session_transaction() as state:
            self.assertNotIn('sid', state)
            self.assertNotIn('onboarding', state)
            self.assertNotIn('signup-access', str(dict(state)))
        revoke.assert_called_once()
        self.assertEqual(self.client.get('/portal/admin/clients').location, '/auth/login')

    def test_registration_requires_bound_unexpired_state_and_csrf(self):
        self.assertEqual(self.client.get('/auth/register/client').location, '/auth/login')
        self.assertEqual(self.client.post('/auth/register/client').location, '/auth/login')
        self.pending()
        self.assertEqual(self.client.post('/auth/register/client', data={'name': 'No CSRF'}).status_code, 400)
        self.pending(ttl=-1)
        self.assertEqual(self.client.get('/auth/register/client').location, '/auth/login')

    def test_registration_form_and_lookup_access(self):
        self.pending()
        page = self.client.get('/auth/register/client')
        self.assertEqual(page.status_code, 200)
        self.assertIn(self.identity.email.encode(), page.data)
        self.assertIn(b'name="owner_name"', page.data)
        self.assertNotIn(b'name="is_active"', page.data)
        self.assertNotIn(b'Primary Administration', page.data)
        self.assertIn(b'data-searchable', page.data)
        lookup = self.client.get('/auth/register/regions?country=US')
        self.assertEqual([r['label'] for r in lookup.json], ['Tennessee', 'Texas'])
        self.assertEqual(self.client.get('/auth/register/regions?country=invalid').status_code, 400)
        self.assertEqual(self.app.test_client().get('/auth/register/regions?country=US').location, '/auth/login')

    @patch('app.auth.verify_identity')
    def test_success_creates_new_client_admin_without_accepting_privilege_fields(self, verify):
        verify.return_value = self.identity
        sid = self.pending()
        response = self.client.post('/auth/register/client', data=self.data(email='billing@example.com',
            auth_user_id=fixtures.ADMIN, user_type='internal', role_code='super_admin', client_id=fixtures.A, is_active=''))
        self.assertEqual(response.location, '/portal/')
        self.send_confirmation.assert_called_once()
        self.assertEqual(self.send_confirmation.call_args.args[0], self.identity.email)
        self.assertIsNone(self.store.load(sid))
        with self.client.session_transaction() as state:
            self.assertNotIn('onboarding', state)
            self.assertEqual(self.store.load(state['sid'])['auth_user_id'], NEW_AUTH)
        with self.app.app_context(), transaction() as db:
            user = db.scalar(select(User).where(User.auth_user_id == NEW_AUTH))
            self.assertEqual(user.email, self.identity.email)
            self.assertEqual(user.user_type, 'client')
            self.assertIsNone(db.get(UserRole, (user.id, 'super_admin')))
            membership = db.scalar(select(Membership).where(Membership.user_id == user.id))
            self.assertNotIn(membership.client_id, (fixtures.A, fixtures.B))
            self.assertEqual(membership.role_code, 'client_admin')
            self.assertTrue(membership.can_create_sows)
            client = db.get(Client, membership.client_id)
            self.assertTrue(client.is_active)
            self.assertEqual(client.email, 'billing@example.com')
            self.assertEqual(client.registration_inquiry, 'Help with quarterly reporting')
            self.assertEqual(client.created_by, user.id)
            self.assertIsNotNone(db.scalar(select(AuditEvent).where(AuditEvent.action == 'client.self_registered')))
        self.assertEqual(self.client.get('/portal/').status_code, 200)
        self.assertEqual(self.client.get('/portal/admin/clients').status_code, 403)
        self.assertEqual(self.client.post('/auth/register/client', data=self.data()).location, '/portal/')
        self.send_confirmation.assert_called_once()

    @patch('app.auth.verify_identity')
    def test_invalid_client_rolls_back_provisional_user_and_allows_retry(self, verify):
        verify.return_value = self.identity
        self.pending()
        before = self.counts()
        response = self.client.post('/auth/register/client', data=self.data(country_code='CA', state_region_code='US-TX'))
        self.assertEqual(response.status_code, 422)
        self.assertIn(b'New Client Company', response.data)
        self.assertEqual(self.counts(), before)
        response = self.client.post('/auth/register/client', data=self.data(country_code='CA', state_region_code='CA-ON'))
        self.assertEqual(response.location, '/portal/')

    @patch('app.auth.verify_identity')
    def test_changed_or_unverified_identity_cannot_complete_registration(self, verify):
        self.pending()
        before = self.counts()
        verify.return_value = services.Identity(fixtures.ADMIN, 'admin@example.com', True)
        self.assertEqual(self.client.post('/auth/register/client', data=self.data()).status_code, 403)
        self.assertEqual(self.counts(), before)
        self.pending()
        verify.return_value = services.Identity(NEW_AUTH, self.identity.email, False)
        self.assertEqual(self.client.post('/auth/register/client', data=self.data()).status_code, 403)
        self.assertEqual(self.counts(), before)

    def test_repeat_registration_does_not_create_an_orphan_client(self):
        with self.app.app_context():
            services.register_client(self.identity, 'google', {'name': 'First Client'}, 'New Owner')
            before = self.counts()
            with self.assertRaises(services.Forbidden):
                services.register_client(self.identity, 'google', {'name': 'Duplicate'}, 'New Owner')
            self.assertEqual(self.counts(), before)

    def test_matching_company_name_never_claims_an_existing_client(self):
        with self.app.app_context():
            context = services.register_client(self.identity, 'azure', {'name': 'Example Health'}, 'New Owner')
            self.assertFalse(context['is_super_admin'])
            self.assertEqual(len(context['clients']), 1)
            self.assertNotIn(context['clients'][0]['id'], (fixtures.A, fixtures.B))
            self.assertEqual(context['clients'][0]['role'], 'client_admin')

    def test_owner_name_validation_does_not_create_records(self):
        self.pending()
        before = self.counts()
        response = self.client.post('/auth/register/client', data=self.data(owner_name=''))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.counts(), before)


if __name__ == '__main__':
    unittest.main()
