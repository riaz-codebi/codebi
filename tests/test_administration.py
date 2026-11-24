"""Real services/transactions against isolated SQLite, rather than mocked RPCs."""
import tempfile
import time
from pathlib import Path
from io import BytesIO
import unittest
from unittest.mock import patch
from PIL import Image
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.pool import StaticPool
from app import create_app, services
from app.database import transaction
from app.models import AuditEvent, Base, Client, Country, Industry, LoginEvent, Membership, Role, StateRegion, Timezone, User, UserRole

A = '11111111-1111-4111-8111-111111111111'
B = '22222222-2222-4222-8222-222222222222'
ADMIN = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
CLIENT = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
REGULAR = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
OTHER = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'


class AdministrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False},
                                    execution_options={'schema_translate_map': {'codebi': None}})
        self.addCleanup(self.engine.dispose)
        @event.listens_for(self.engine, 'connect')
        def sqlite_functions(connection, _record):
            connection.create_function('strpos', 2, lambda text, part: (text or '').find(part) + 1)
        Base.metadata.create_all(self.engine)  # TEST database only.
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'test-secret-' * 6,
            'SUPABASE_URL': None, 'SUPABASE_KEY': None, 'APP_DATABASE_URL': None, 'DATABASE_ENGINE': self.engine,
            'APP_BASE_URL': 'http://localhost:5000', 'AUTH_STORE_PATH': Path(self.temp.name) / 'sessions.sqlite3'})
        with self.app.app_context(), transaction() as db:
            db.add_all([Country(code='US', name='United States'), Country(code='CA', name='Canada'),
                Timezone(name='UTC'), Timezone(name='America/Chicago')])
            db.flush()
            db.add_all([StateRegion(code='US-TX', country_code='US', name='Texas', region_type='State'),
                StateRegion(code='US-TN', country_code='US', name='Tennessee', region_type='State'),
                StateRegion(code='CA-ON', country_code='CA', name='Ontario', region_type='Province')])
            db.add_all([Role(code='super_admin', name='Super Admin', scope='global'),
                        Role(code='client_admin', name='Client Admin', scope='client'), Role(code='client_user', name='User', scope='client')])
            self.admin = User(email='admin@example.com', display_name='Test Admin', user_type='internal', auth_user_id=ADMIN)
            self.member = User(email='client@example.com', display_name='Client Admin', user_type='client', auth_user_id=CLIENT)
            self.regular = User(email='regular@example.com', display_name='Regular User', user_type='client', auth_user_id=REGULAR)
            self.other = User(email='other@example.com', display_name='Other Admin', user_type='client', auth_user_id=OTHER)
            self.unbound = User(email='reserved@example.com', display_name='Reserved User', user_type='client')
            db.add_all([self.admin, self.member, self.regular, self.other, self.unbound])
            db.add_all([Client(id=A, name='Example Health', client_code='CLI-0001'), Client(id=B, name='Other Company', client_code='CLI-0002')])
            db.add(Industry(name='Healthcare'))
            db.flush()
            db.add(UserRole(user_id=self.admin.id, role_code='super_admin'))
            db.add_all([Membership(client_id=A, user_id=self.member.id, role_code='client_admin'),
                Membership(client_id=A, user_id=self.regular.id), Membership(client_id=A, user_id=self.unbound.id),
                Membership(client_id=B, user_id=self.other.id, role_code='client_admin')])
        self.client = self.browser(ADMIN)

    def browser(self, auth_id):
        browser = self.app.test_client()
        sid = self.app.extensions['auth_store'].create({'kind': 'login', 'auth_user_id': auth_id,
            'access_token': 'test-token', 'refresh_token': 'test-refresh', 'expires_at': time.time()+3600}, 3600)
        with browser.session_transaction() as state:
            state['sid'] = sid
        return browser

    def csrf(self):
        self.client.get('/auth/login')
        with self.client.session_transaction() as state:
            return state['csrf']

    def test_directories_and_forms_render_with_real_queries(self):
        for path in ['/portal/', '/portal/admin/clients', '/portal/admin/clients/new',
            f'/portal/admin/clients/{A}/edit', '/portal/admin/users', '/portal/admin/users/new',
            f'/portal/clients/{A}/users', f'/portal/clients/{A}/users/new', f'/portal/clients/{A}/users/{self.regular.id}/edit']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    @patch('app.administration.save_branding')
    def test_create_client_and_audit_commit_together(self, save_branding):
        response = self.client.post('/portal/admin/clients/new', data={'csrf_token': self.csrf(),
            'name': 'New Company', 'timezone': 'America/Chicago', 'is_active': 'on'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/portal/admin/clients')
        with self.app.app_context(), transaction() as db:
            saved = db.scalar(select(Client).where(Client.name == 'New Company'))
            self.assertEqual(saved.created_by, self.admin.id)
            self.assertEqual(db.scalar(select(AuditEvent).where(AuditEvent.entity_id == saved.id)).action, 'client.created')
            save_branding.assert_called_once_with(saved.id, None)

    @patch('app.administration.download_logo')
    @patch('app.client_branding.storage_request')
    def test_client_logo_upload_and_tenant_access(self, storage_request, download_logo):
        data = {'csrf_token': self.csrf(), 'name': 'Example Health', 'is_active': 'on', 'logo_background': 'dark'}
        edit_path = f'/portal/admin/clients/{A}/edit'
        invalid = self.client.post(edit_path, data={**data, 'logo': (BytesIO(b'not png'), 'invalid.png')})
        self.assertEqual(invalid.status_code, 422)
        storage_request.assert_not_called()

        png = BytesIO()
        Image.new('RGBA', (8, 8), 'blue').save(png, format='PNG')
        png.seek(0)
        result = self.client.post(edit_path, data={**data, 'logo': (png, 'company.png')})
        self.assertEqual(result.location, '/portal/admin/clients')
        expected_path = f'{A}/brand/logo.png'
        self.assertEqual(storage_request.call_args.args, ('POST', 'object/client-branding/' + expected_path))
        with self.app.app_context(), transaction() as db:
            self.assertEqual(db.get(Client, A).logo_path, expected_path)
            self.assertEqual(db.get(Client, A).logo_background, 'dark')

        logo_path = f'/portal/clients/{A}/brand/logo.png'
        download_logo.return_value = b'png bytes'
        client = self.browser(REGULAR)
        dashboard = client.get('/portal/').data
        self.assertIn(logo_path.encode(), dashboard)
        self.assertIn(b'data-background="dark"', dashboard)
        self.assertIn(b'client-brand-frame portal-client-brand', dashboard)
        self.assertNotIn(b'Welcome back', dashboard)
        self.assertEqual(dashboard.count(logo_path.encode()), 1)
        self.assertEqual(client.get(logo_path).mimetype, 'image/png')
        download_logo.assert_called_once_with(expected_path)
        self.assertEqual(self.browser(OTHER).get(logo_path).status_code, 404)
        download_logo.assert_called_once()

    def test_invalid_client_preserves_form_without_writes(self):
        response = self.client.post('/portal/admin/clients/new', data={'csrf_token': self.csrf(),
            'name': 'Keep Name', 'email': 'bad', 'timezone': 'Not/A_Zone'})
        self.assertEqual(response.status_code, 422)
        self.assertIn(b'Keep Name', response.data)
        with self.app.app_context(), transaction() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Client)), 2)

    def test_search_and_filter(self):
        page = self.client.get('/portal/admin/users?type=client&q=regular')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'regular@example.com', page.data)
        self.assertNotIn(b'other@example.com', page.data)
        page = self.client.get('/portal/admin/clients?q=Health')
        self.assertIn(b'Example Health', page.data)
        self.assertNotIn(b'Other Company', page.data)

    def test_client_admin_cannot_access_global_or_other_client(self):
        browser = self.browser(CLIENT)
        self.assertEqual(browser.get(f'/portal/clients/{A}/users').status_code, 200)
        for path in ['/portal/admin/clients', '/portal/admin/users', f'/portal/clients/{B}/users']:
            self.assertEqual(browser.get(path).status_code, 403)
        with self.app.app_context():
            with self.assertRaises(services.Forbidden):
                services.clients(CLIENT)
            with self.assertRaises(services.Forbidden):
                services.users(CLIENT, client_id=B)
            with self.assertRaises(services.Forbidden):
                services.save_user(CLIENT, {'email': 'x@example.com'}, client_id=B)

    def test_regular_user_cannot_administer(self):
        self.assertEqual(self.browser(REGULAR).get(f'/portal/clients/{A}/users').status_code, 403)
        with self.app.app_context(), self.assertRaises(services.Forbidden):
            services.save_client(REGULAR, {'name': 'Denied'})

    def test_client_admin_provisions_without_global_privileges(self):
        with self.app.app_context():
            uid = services.save_user(CLIENT, {'email': ' NEW@Example.com ', 'display_name': 'New User',
                'can_create_sows': True, 'user_type': 'internal'}, client_id=A)
            with transaction() as db:
                self.assertEqual(db.get(User, uid).email, 'new@example.com')
                self.assertEqual(db.get(User, uid).user_type, 'client')
                self.assertIsNone(db.get(UserRole, (uid, 'super_admin')))
                self.assertTrue(db.scalar(select(Membership).where(Membership.user_id == uid)).can_create_sows)

    def test_client_roles_include_sow_creation_without_separate_permission(self):
        for role in ('client_user', 'client_admin'):
            with self.subTest(role=role):
                response = self.client.post(f'/portal/clients/{A}/users/new', data={
                    'csrf_token': self.csrf(), 'display_name': 'Role User',
                    'email': role + '@example.com', 'role_code': role, 'membership_active': 'on',
                    'can_create_sows': ''})  # Obsolete/malicious override is ignored.
                self.assertEqual(response.status_code, 302)
                with self.app.app_context(), transaction() as db:
                    user = db.scalar(select(User).where(User.email == role + '@example.com'))
                    membership = db.scalar(select(Membership).where(Membership.user_id == user.id))
                    self.assertTrue(membership.can_create_sows)
                    self.assertEqual(membership.role_code, role)
        for url in (f'/portal/clients/{A}/users/new', f'/portal/clients/{A}/users/{self.regular.id}/edit'):
            page = self.client.get(url)
            self.assertEqual(page.status_code, 200)
            self.assertIn(b'name="role_code"', page.data)
            self.assertIn(b'name="membership_active"', page.data)
            self.assertNotIn(b'name="can_create_sows"', page.data)
            self.assertNotIn(b'Allow a regular client user to draft', page.data)
            self.assertNotIn(b'Client administrators manage users and approve', page.data)

    def test_existing_memberships_use_role_policy_and_still_require_active_access(self):
        with self.app.app_context():
            for auth_id in (CLIENT, REGULAR):
                context = services.portal_context(auth_id)
                self.assertTrue(context['allowed'])
                self.assertTrue(context['clients'][0]['can_create_sows'])
            with transaction() as db:
                membership = db.scalar(select(Membership).where(Membership.user_id == self.regular.id))
                self.assertFalse(membership.can_create_sows)  # Legacy stored flag isn't authoritative.
                membership.is_active = False
            context = services.portal_context(REGULAR)
            self.assertFalse(context['allowed'])
            self.assertEqual(context['clients'], [])
            services.save_user(ADMIN, {'role_code': 'client_user', 'membership_active': True,
                'display_name': 'Regular User', 'can_create_sows': False}, client_id=A, user_id=self.regular.id)
            self.assertTrue(services.portal_context(REGULAR)['clients'][0]['can_create_sows'])
        self.assertFalse(services.role_can_create_sows('unknown-role'))

    def test_client_admin_cannot_escalate_or_attach_existing_user(self):
        with self.app.app_context():
            with self.assertRaises(services.InvalidDetails):
                services.save_user(CLIENT, {'email': 'new@example.com', 'display_name': 'New', 'role_code': 'super_admin'}, client_id=A)
            with self.assertRaises(services.InvalidDetails):
                services.save_user(CLIENT, {'email': 'other@example.com', 'display_name': 'Other'}, client_id=A)
            with self.assertRaises(services.Forbidden):
                services.save_user(CLIENT, {'email': 'new@example.com', 'display_name': 'New', 'user_active': False}, client_id=A)
            with transaction() as db:
                self.assertIsNone(db.scalar(select(User).where(User.email == 'new@example.com')))

    def test_membership_edit_preserves_global_profile(self):
        with self.app.app_context():
            services.save_user(CLIENT, {'display_name': 'Override', 'email': 'different@example.com',
                'phone': 'Override', 'membership_active': False}, client_id=A, user_id=self.regular.id)
            with transaction() as db:
                user = db.get(User, self.regular.id)
                self.assertEqual(user.display_name, 'Regular User')
                self.assertEqual(user.email, 'regular@example.com')
                self.assertTrue(user.is_active)
            self.assertFalse(services.portal_context(REGULAR)['allowed'])

    def test_unverified_email_can_be_corrected_with_name_and_phone(self):
        import re
        url = f'/portal/clients/{A}/users/{self.unbound.id}/edit'
        page = self.client.get(url)
        self.assertIn(b'<h1>Edit user</h1>', page.data)
        self.assertNotIn(b'Manage membership in', page.data)
        self.assertNotIn(b'This user has not signed in yet', page.data)
        email_input = re.search(rb'<input[^>]*id="email"[^>]*>', page.data).group()
        self.assertNotIn(b'readonly', email_input)
        self.assertNotIn(b'This email has been verified', page.data)
        response = self.client.post(url, data={'csrf_token': self.csrf(),
            'display_name': 'Corrected Name', 'email': ' Corrected@Example.com ', 'phone': '2025550123',
            'role_code': 'client_user', 'membership_active': 'on'})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context(), transaction() as db:
            user = db.get(User, self.unbound.id)
            self.assertEqual(user.email, 'corrected@example.com')
            self.assertEqual(user.display_name, 'Corrected Name')
            self.assertEqual(user.phone, '2025550123')
            self.assertIsNone(user.auth_user_id)
            self.assertEqual(db.scalar(select(Membership).where(Membership.user_id == user.id)).client_id, A)
            audit = db.scalar(select(AuditEvent).where(AuditEvent.entity_id == user.id))
            self.assertEqual(audit.changes['before']['email'], 'reserved@example.com')
            self.assertEqual(audit.changes['after']['email'], 'corrected@example.com')

    def test_first_verified_login_locks_email_in_form_and_service(self):
        import re
        auth_id = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'
        with self.app.app_context():
            result = services.portal_login(services.Identity(auth_id, 'reserved@example.com', True), 'google')
            self.assertTrue(result['allowed'])
            with self.assertRaises(services.InvalidDetails):
                services.save_user(ADMIN, {'email': 'changed@example.com', 'display_name': 'Changed'},
                                   client_id=A, user_id=self.unbound.id)
        url = f'/portal/clients/{A}/users/{self.unbound.id}/edit'
        page = self.client.get(url)
        email_input = re.search(rb'<input[^>]*id="email"[^>]*>', page.data).group()
        self.assertIn(b'readonly', email_input)
        self.assertIn(b'This email has been verified and cannot be changed.', page.data)
        response = self.client.post(url, data={'csrf_token': self.csrf(), 'email': 'changed@example.com',
            'display_name': 'Changed', 'phone': '', 'role_code': 'client_user', 'membership_active': 'on'})
        self.assertEqual(response.status_code, 422)
        with self.app.app_context(), transaction() as db:
            self.assertEqual(db.get(User, self.unbound.id).email, 'reserved@example.com')
            self.assertEqual(db.get(User, self.unbound.id).auth_user_id, auth_id)
        response = self.client.post(url, data={'csrf_token': self.csrf(), 'email': 'reserved@example.com',
            'display_name': 'Updated Verified Name', 'phone': '2025550199',
            'role_code': 'client_user', 'membership_active': 'on'})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context(), transaction() as db:
            user = db.get(User, self.unbound.id)
            self.assertEqual(user.display_name, 'Updated Verified Name')
            self.assertEqual(user.phone, '2025550199')
            self.assertEqual(user.auth_user_id, auth_id)

    def test_email_correction_does_not_merge_existing_accounts(self):
        url = f'/portal/clients/{A}/users/{self.unbound.id}/edit'
        response = self.client.post(url, data={'csrf_token': self.csrf(), 'email': 'regular@example.com',
            'display_name': 'Changed', 'phone': '', 'role_code': 'client_user', 'membership_active': 'on'})
        self.assertEqual(response.status_code, 422)
        self.assertIn(b'This email is already in use.', response.data)
        with self.app.app_context(), transaction() as db:
            self.assertEqual(db.get(User, self.unbound.id).email, 'reserved@example.com')
            self.assertEqual(db.get(User, self.unbound.id).display_name, 'Reserved User')

    def test_last_admin_and_own_global_account_protected(self):
        with self.app.app_context():
            with self.assertRaises(services.InvalidDetails):
                services.save_user(ADMIN, {'display_name': 'Admin', 'role_code': 'client_user'}, client_id=A, user_id=self.member.id)
            with self.assertRaises(services.InvalidDetails):
                services.save_user(ADMIN, {'display_name': 'Test Admin', 'user_active': False}, user_id=self.admin.id)
            self.assertTrue(services.portal_context(ADMIN)['allowed'])
            self.assertTrue(services.portal_context(CLIENT)['allowed'])

    def test_global_admin_cannot_link_an_existing_email_to_another_client(self):
        with self.app.app_context():
            with self.assertRaises(services.InvalidDetails) as error:
                services.save_user(ADMIN, {'email': ' REGULAR@EXAMPLE.COM ', 'display_name': 'Do Not Replace'}, client_id=B)
            self.assertEqual(str(error.exception), 'This email is already in use.')
            self.assertEqual(error.exception.field, 'email')
            with transaction() as db:
                self.assertEqual(db.get(User, self.regular.id).display_name, 'Regular User')
                self.assertEqual(db.scalar(select(func.count()).select_from(Membership).where(Membership.user_id == self.regular.id)), 1)
            self.assertEqual(len(services.portal_context(REGULAR)['clients']), 1)

    def test_duplicate_email_is_rejected_for_same_client_and_inactive_accounts(self):
        with self.app.app_context(), transaction() as db:
            db.get(User, self.other.id).is_active = False
            db.scalar(select(Membership).where(Membership.user_id == self.other.id)).is_active = False
        for client_id, email in ((A, ' REGULAR@EXAMPLE.COM '), (A, 'OTHER@EXAMPLE.COM'), (B, 'admin@example.com')):
            with self.subTest(client_id=client_id, email=email):
                response = self.client.post(f'/portal/clients/{client_id}/users/new', data={
                    'csrf_token': self.csrf(), 'email': email, 'display_name': 'Duplicate',
                    'role_code': 'client_user', 'membership_active': 'on'})
                self.assertEqual(response.status_code, 422)
                self.assertIn(b'This email is already in use.', response.data)
                self.assertIn(b'id="email-error"', response.data)
        with self.app.app_context(), transaction() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(User)), 5)
            self.assertEqual(db.scalar(select(func.count()).select_from(Membership)), 4)

    def test_legacy_shared_memberships_remain_scoped_in_user_directory(self):
        # Legacy/direct-SQL links are still hidden from unrelated client admins.
        with self.app.app_context():
            with transaction() as db:
                db.add(Membership(client_id=B, user_id=self.regular.id))
            scoped = services.users(CLIENT, client_id=A, user_id=self.regular.id)['users'][0]
            self.assertEqual(len(scoped['memberships']), 1)
            self.assertNotIn('auth_user_id', scoped)

    def test_internal_admin_provisioning(self):
        with self.app.app_context():
            uid = services.save_user(ADMIN, {'email': 'second@example.com', 'display_name': 'Second', 'user_active': True})
            with transaction() as db:
                self.assertEqual(db.get(User, uid).user_type, 'internal')
                self.assertIsNotNone(db.get(UserRole, (uid, 'super_admin')))

    def test_verified_login_links_and_records_history(self):
        identity = services.Identity('eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', self.unbound.email, True)
        with self.app.app_context():
            self.assertTrue(services.portal_login(identity, 'google')['allowed'])
            with transaction() as db:
                self.assertEqual(db.get(User, self.unbound.id).auth_user_id, identity.auth_id)
                self.assertEqual(db.scalar(select(LoginEvent).where(LoginEvent.user_id == self.unbound.id)).outcome, 'success')

    def test_unverified_unknown_and_duplicate_identity_denied(self):
        with self.app.app_context():
            for identity in [services.Identity(ADMIN, 'unknown@example.com', False),
                services.Identity('eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', 'unknown@example.com', True),
                services.Identity('eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', 'admin@example.com', True)]:
                self.assertFalse(services.portal_login(identity, 'azure')['allowed'])
            with transaction() as db:
                self.assertEqual(db.scalar(select(func.count()).select_from(User)), 5)
                self.assertEqual(db.get(User, self.admin.id).auth_user_id, ADMIN)

    def test_csrf_and_html_escaping(self):
        self.assertEqual(self.client.post('/portal/admin/clients/new', data={'name': 'x'}).status_code, 400)
        with self.app.app_context(), transaction() as db:
            db.get(Client, A).name = '<script>alert(1)</script>'
        page = self.client.get('/portal/admin/clients')
        self.assertNotIn(b'<script>alert(1)</script>', page.data)
        self.assertIn(b'&lt;script&gt;', page.data)

    def test_admin_connection_cannot_be_used_as_runtime(self):
        with self.assertRaises(ValueError):
            create_app({'SECRET_KEY': 'x'*64, 'APP_DATABASE_URL': 'postgresql://postgres:dummy@example.com/postgres'})

    def test_searchable_lookups_render_and_states_are_country_scoped(self):
        page = self.client.get('/portal/admin/clients/new')
        self.assertIn(b'data-searchable', page.data)
        self.assertIn(b'United States', page.data)
        self.assertIn(b'America/Chicago', page.data)
        self.assertIn(b'name="state_region_code"', page.data)
        states = self.client.get('/portal/admin/reference/regions?country=US')
        self.assertEqual(states.status_code, 200)
        self.assertEqual([r['label'] for r in states.json], ['Tennessee', 'Texas'])
        self.assertNotIn('Ontario', str(states.json))
        self.assertEqual(self.client.get('/portal/admin/reference/regions?country=invalid').status_code, 400)
        self.assertEqual(self.browser(CLIENT).get('/portal/admin/reference/regions?country=US').status_code, 403)

    def test_country_region_timezone_validation_and_storage(self):
        with self.app.app_context():
            uid = services.save_client(ADMIN, {'name': 'Texas Company', 'country_code': 'US',
                'state_region_code': 'US-TX', 'timezone': 'America/Chicago'})
            with transaction() as db:
                client = db.get(Client, uid)
                self.assertEqual((client.state_region, client.state_region_code), ('Texas', 'US-TX'))
            for data, field in [({'country_code': 'CA', 'state_region_code': 'US-TX'}, 'state_region_code'),
                                ({'country_code': 'ZZ'}, 'country_code'), ({'timezone': 'Made/Up'}, 'timezone')]:
                with self.assertRaises(services.InvalidDetails) as caught:
                    services.save_client(ADMIN, {'name': 'Invalid', **data})
                self.assertEqual(caught.exception.field, field)

    def test_legacy_region_is_preserved_without_inventing_a_catalog_entry(self):
        with self.app.app_context(), transaction() as db:
            client = db.get(Client, A)
            client.country_code = 'US'
            client.state_region = 'Historical region'
        page = self.client.get(f'/portal/admin/clients/{A}/edit')
        self.assertIn(b'Historical region (saved value)', page.data)
        with self.app.app_context():
            services.save_client(ADMIN, {'name': 'Example Health', 'country_code': 'US',
                'state_region_code': '__legacy__', 'timezone': 'UTC'}, client_id=A)
            with transaction() as db:
                self.assertEqual(db.get(Client, A).state_region, 'Historical region')
            with self.assertRaises(services.InvalidDetails):
                services.save_client(ADMIN, {'name': 'Invalid', 'state_region_code': '__legacy__'})

    def test_inactive_catalog_values_cannot_be_new_selections(self):
        with self.app.app_context(), transaction() as db:
            db.get(StateRegion, 'US-TX').is_active = False
            db.get(Country, 'CA').is_active = False
            industry = db.scalar(select(Industry))
            industry.is_active = False
            industry_id = industry.id
        with self.app.app_context():
            for data in ({'country_code': 'US', 'state_region_code': 'US-TX'}, {'country_code': 'CA'}, {'industry_id': industry_id}):
                with self.assertRaises(services.InvalidDetails):
                    services.save_client(ADMIN, {'name': 'Invalid', **data})


if __name__ == '__main__':
    unittest.main()
