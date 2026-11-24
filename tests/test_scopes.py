"""Scope workflows against real isolated SQLAlchemy transactions (no live records)."""
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from sqlalchemy import func, select
import test_administration as fixtures
from app import services, sow_services as scopes
from app.database import transaction
from app.models import AuditEvent, Client, Membership, ScopeOfWork, SowEstimate, SowEvent, SowMessage, SowTool, SowType, TimeEntry, Tool, User
from app.notifications import NotificationError, send_notification
from resend.exceptions import ResendError

TYPE = '12345678-1234-4234-8234-123456789001'
TOOL = '12345678-1234-4234-8234-123456789002'
INACTIVE = '12345678-1234-4234-8234-123456789003'
OTHER_TOOL = '12345678-1234-4234-8234-123456789004'
PEER = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'


class ScopeTests(unittest.TestCase):
    browser = fixtures.AdministrationTests.browser
    csrf = fixtures.AdministrationTests.csrf

    def setUp(self):
        fixtures.AdministrationTests.setUp(self)
        self.app.config['RESEND_API_KEY'] = ''
        with self.app.app_context(), transaction() as db:
            db.add(SowType(id=TYPE, name='New application'))
            db.add(SowType(name='Discovery'))
            db.add(SowType(id=INACTIVE, name='Retired type', is_active=False))
            db.add(Tool(id=TOOL, name='Python'))
            db.add(Tool(id=OTHER_TOOL, name='Other'))
            peer = User(email='peer@example.com', display_name='Peer', user_type='client', auth_user_id=PEER)
            db.add(peer)
            db.flush()
            db.add(Membership(client_id=fixtures.A, user_id=peer.id))

    def values(self, **changes):
        return {'title': 'Operations dashboard', 'description': 'Build an operations dashboard.',
                'sow_type_id': TYPE, 'priority': 'normal', 'tool_ids': [TOOL], **changes}

    def create(self, actor=fixtures.REGULAR, action='draft', **changes):
        with self.app.app_context():
            return scopes.save(actor, fixtures.A, self.values(**changes), action=action)

    def path(self, sow_id=None, suffix=''):
        return '/portal/clients/' + fixtures.A + '/scopes-of-work' + ('/' + sow_id if sow_id else '') + suffix

    def test_client_dashboard_navigation_and_form(self):
        self.client = self.browser(fixtures.REGULAR)
        page = self.client.get('/portal/')
        self.assertIn(b'id="portalAccount"', page.data)
        self.assertIn(b'action="/auth/logout"', page.data)
        self.assertNotIn(b'Back to website', page.data)
        self.assertIn(b'Create scope of work', page.data)
        self.assertNotIn(b'when that feature is available', page.data)
        self.assertEqual(self.client.get('/portal/scopes-of-work').location, self.path())
        page = self.client.get(self.path(suffix='/new'))
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Submit to CodeBI', page.data)
        self.assertLess(page.data.index(b'Submit to CodeBI'), page.data.index(b'id="title"'))
        self.assertLess(page.data.index(b'Solution interests'), page.data.index(b'id="description"'))
        self.assertNotIn(b'value="request_approval"', page.data)
        self.assertIn(b'New application', page.data)
        self.assertIn(b'Discovery', page.data)
        self.assertIn(b'get help defining the scope.', page.data)
        self.assertIn(b'value="normal" checked required', page.data)
        self.assertIn(b'value="low" disabled', page.data)
        self.assertIn(b'value="high" disabled', page.data)
        self.assertIn(b'value="urgent" disabled', page.data)
        self.assertNotIn(b'Retired type', page.data)
        self.assertIn(b'Python', page.data)

    def test_client_admin_sidebar_highlights_only_current_section(self):
        client = self.browser(fixtures.CLIENT)
        scope_page = client.get(self.path()).data
        users_page = client.get(f'/portal/clients/{fixtures.A}/users').data
        self.assertIn(b'class="portal-nav-item active"', scope_page)
        self.assertNotIn(b'class="portal-subnav portal-subnav-with-icon active"', scope_page)
        self.assertIn(b'class="portal-subnav portal-subnav-with-icon active"', users_page)
        self.assertNotIn(b'class="portal-nav-item active"', users_page)

    def test_web_create_draft_and_detail(self):
        self.client = self.browser(fixtures.REGULAR)
        response = self.client.post(self.path(suffix='/new'), data={**self.values(),
            'csrf_token': self.csrf(), 'action': 'draft', 'client_id': fixtures.B,
            'created_by': self.admin.id, 'status': 'approved'})
        self.assertEqual(response.status_code, 302)
        detail = self.client.get(response.location)
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b'Operations dashboard', detail.data)
        self.assertIn(b'Edit draft', detail.data)
        with self.app.app_context(), transaction() as db:
            row = db.scalar(select(ScopeOfWork))
            self.assertEqual((row.client_id, row.created_by, row.status), (fixtures.A, self.regular.id, 'draft'))
            self.assertEqual(db.scalar(select(func.count()).select_from(SowTool)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(SowEvent)), 1)
            self.assertEqual(db.scalar(select(AuditEvent)).action, 'scope.created')

    def test_other_tool_details_persist_and_clear_when_unchecked(self):
        self.client = self.browser(fixtures.REGULAR)
        response = self.client.post(self.path(suffix='/new'), data={**self.values(
            tool_ids=[TOOL, OTHER_TOOL], other_tool_details='Custom reporting tool'), 'csrf_token': self.csrf()})
        self.assertEqual(response.status_code, 302)
        scope_path = response.location
        for path in (scope_path, scope_path + '/edit'):
            self.assertIn(b'Custom reporting tool', self.client.get(path).data)
        response = self.client.post(scope_path + '/edit', data={**self.values(
            other_tool_details='Custom reporting tool'), 'csrf_token': self.csrf()})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(b'Custom reporting tool', self.client.get(scope_path).data)

    def test_user_submission_and_internal_review(self):
        row_id = self.create(action='submit')
        with self.app.app_context():
            with transaction() as db:
                row = db.get(ScopeOfWork, row_id)
                self.assertEqual(row.status, 'submitted')
                self.assertEqual(row.submitted_by, self.regular.id)
                self.assertIsNotNone(row.submitted_at)
                self.assertIsNone(row.accepted_estimate_id)  # Initial approval is not estimate acceptance.
            scopes.transition(fixtures.ADMIN, fixtures.A, row_id, 'start_review')
            self.assertEqual(scopes.detail(fixtures.REGULAR, fixtures.A, row_id)['scope']['status'], 'under_review')

    def test_client_admin_can_create_and_submit_directly(self):
        row_id = self.create(actor=fixtures.CLIENT, action='submit')
        with self.app.app_context(), transaction() as db:
            row = db.get(ScopeOfWork, row_id)
            self.assertEqual(row.status, 'submitted')
            self.assertEqual(row.submitted_by, self.member.id)

    @patch('app.scopes.send_notification')
    def test_submit_notification_and_removed_resend_action(self, send):
        self.client = self.browser(fixtures.REGULAR)
        response = self.client.post(self.path(suffix='/new'), data={
            **self.values(), 'csrf_token': self.csrf(), 'action': 'submit'})
        self.assertEqual(response.status_code, 302)
        detail_path = response.location
        recipient, _, body = send.call_args.args
        self.assertEqual(recipient, 'team@codebisolutions.com')
        self.assertIn('https://codebisolutions.com/portal/review/', body)
        link = urlsplit(body.splitlines()[-1])
        review_response = self.client.get(f'{link.path}?{link.query}')
        self.assertEqual(review_response.location, detail_path)
        self.assertEqual(self.app.test_client().get(review_response.location).location, '/auth/login')
        self.assertNotIn(b'Resend notification', self.client.get(detail_path).data)
        response = self.client.post(detail_path + '/transition', data={
            'csrf_token': self.csrf(), 'action': 'resend_notification'})
        self.assertEqual(response.status_code, 422)
        send.assert_called_once()
        with self.app.app_context(), transaction() as db:
            self.assertEqual(db.scalar(select(ScopeOfWork.status)), 'submitted')
            self.assertEqual(db.scalar(select(func.count()).select_from(SowEvent)), 2)

    @patch('resend.Emails.send', side_effect=ResendError(401, 'invalid_api_key', 'provider text', ''))
    def test_notification_reports_rejected_key_without_provider_text(self, send):
        self.app.config['RESEND_API_KEY'] = 're_test'
        with self.app.app_context(), self.assertRaisesRegex(NotificationError, '^Resend rejected the API key\\.$'):
            send_notification('test@example.com', 'Subject', 'Body')
        send.assert_called_once()

    def test_internal_user_requires_client_membership_to_submit(self):
        for actor in (fixtures.ADMIN, fixtures.OTHER):
            with self.subTest(actor=actor), self.app.app_context():
                with self.assertRaises(services.Forbidden):
                    scopes.save(actor, fixtures.A, self.values(), action='submit')
                with transaction() as db:
                    self.assertEqual(db.scalar(select(func.count()).select_from(ScopeOfWork)), 0)
                    self.assertEqual(db.scalar(select(func.count()).select_from(SowEvent)), 0)
                    self.assertEqual(db.scalar(select(func.count()).select_from(AuditEvent)), 0)

    def test_tenant_and_owner_isolation_for_reads_and_writes(self):
        row_id = self.create()
        for actor in (fixtures.OTHER, PEER):
            browser = self.browser(actor)
            for route in (self.path(row_id), self.path(row_id, '/edit')):
                self.assertIn(browser.get(route).status_code, (403, 404))
            with self.app.app_context():
                for operation in (lambda: scopes.save(actor, fixtures.A, self.values(title='Tampered'), sow_id=row_id),
                                  lambda: scopes.transition(actor, fixtures.A, row_id, 'submit')):
                    with self.assertRaises((services.Forbidden, services.NotFound)):
                        operation()
        with self.app.app_context():
            self.assertEqual(scopes.directory(PEER, fixtures.A)['total'], 0)
            self.assertEqual(scopes.directory(fixtures.CLIENT, fixtures.A)['total'], 1)
            self.assertEqual(scopes.directory(fixtures.ADMIN)['total'], 1)
            with self.assertRaises(services.NotFound):
                scopes.detail(fixtures.OTHER, fixtures.B, row_id)

    def test_inactive_membership_client_or_account_blocks_actions(self):
        row_id = self.create()
        with self.app.app_context(), transaction() as db:
            membership = db.scalar(select(Membership).where(Membership.user_id == self.regular.id))
            membership.is_active = False
        with self.app.app_context(), self.assertRaises(services.Forbidden):
            scopes.transition(fixtures.REGULAR, fixtures.A, row_id, 'submit')
        with self.app.app_context(), transaction() as db:
            db.get(Client, fixtures.A).is_active = False
        with self.app.app_context(), self.assertRaises(services.InvalidDetails):
            scopes.save(fixtures.ADMIN, fixtures.A, self.values(), sow_id=row_id)
        with self.app.app_context(), transaction() as db:
            db.get(User, self.admin.id).is_active = False
        with self.app.app_context(), self.assertRaises(services.Forbidden):
            scopes.directory(fixtures.ADMIN)

    def test_submitted_content_is_locked_and_transitions_are_not_replayed(self):
        row_id = self.create(action='submit')
        with self.app.app_context():
            with self.assertRaises(services.InvalidDetails):
                scopes.save(fixtures.REGULAR, fixtures.A, self.values(title='Changed'), sow_id=row_id)
            with self.assertRaises(services.InvalidDetails):
                scopes.transition(fixtures.CLIENT, fixtures.A, row_id, 'submit')
            with self.assertRaises(services.Forbidden):
                scopes.transition(fixtures.CLIENT, fixtures.A, row_id, 'start_review')
            with transaction() as db:
                self.assertEqual(db.get(ScopeOfWork, row_id).title, 'Operations dashboard')
                self.assertEqual(db.scalar(select(func.count()).select_from(SowEvent)), 2)

    def test_return_to_draft_and_edit_tools(self):
        row_id = self.create()
        with self.app.app_context(), transaction() as db:
            db.get(ScopeOfWork, row_id).status = 'pending_client_approval'
        with self.app.app_context():
            scopes.transition(fixtures.CLIENT, fixtures.A, row_id, 'return_to_draft')
            scopes.save(fixtures.REGULAR, fixtures.A, self.values(title='Revised title', tool_ids=[]), sow_id=row_id)
            with transaction() as db:
                self.assertEqual(db.get(ScopeOfWork, row_id).title, 'Revised title')
                self.assertEqual(db.scalar(select(func.count()).select_from(SowTool)), 0)

    def test_validation_rejects_bad_fields_without_writes(self):
        for changes, field in [({'title': ''}, 'title'), ({'description': ''}, 'description'),
            ({'sow_type_id': 'bad'}, 'sow_type_id'), ({'sow_type_id': INACTIVE}, 'sow_type_id'),
            ({'priority': 'invalid'}, 'priority'), ({'desired_completion_date': '2026-02-30'}, 'desired_completion_date'),
            ({'tool_ids': ['bad']}, 'tool_ids')]:
            with self.subTest(field=field), self.app.app_context():
                with self.assertRaises(services.InvalidDetails) as caught:
                    scopes.save(fixtures.REGULAR, fixtures.A, self.values(**changes))
                self.assertEqual(caught.exception.field, field)
                with transaction() as db:
                    self.assertEqual(db.scalar(select(func.count()).select_from(ScopeOfWork)), 0)

    def test_error_form_preserves_values_and_escapes_html(self):
        self.client = self.browser(fixtures.REGULAR)
        response = self.client.post(self.path(suffix='/new'), data={**self.values(title='<script>alert(1)</script>'),
            'description': '', 'csrf_token': self.csrf()})
        self.assertEqual(response.status_code, 422)
        self.assertIn(b'&lt;script&gt;', response.data)
        self.assertNotIn(b'<script>alert(1)</script>', response.data)

    def test_legacy_pending_scope_can_submit_and_appears_in_internal_queue(self):
        row_id = self.create()
        with self.app.app_context(), transaction() as db:
            db.get(ScopeOfWork, row_id).status = 'pending_client_approval'
        self.client = self.browser(fixtures.REGULAR)
        response = self.client.get(self.path(row_id))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Submit to CodeBI', response.data)
        result = self.client.post(self.path(row_id, '/transition'),
            data={'csrf_token': self.csrf(), 'action': 'submit'})
        self.assertEqual(result.status_code, 302)
        self.client = self.browser(fixtures.ADMIN)
        queue = self.client.get('/portal/scopes-of-work?status=submitted')
        self.assertEqual(queue.status_code, 200)
        self.assertIn(b'Operations dashboard', queue.data)
        result = self.client.post(self.path(row_id, '/transition'),
            data={'csrf_token': self.csrf(), 'action': 'start_review'})
        self.assertEqual(result.status_code, 302)
        self.assertIn(b'Under review', self.client.get(result.location).data)

    def test_csrf_and_anonymous_access(self):
        self.assertEqual(self.app.test_client().get(self.path()).status_code, 302)
        self.assertEqual(self.client.post(self.path(suffix='/new'), data=self.values()).status_code, 400)
        row_id = self.create()
        self.assertEqual(self.client.post(self.path(row_id, '/transition'), data={'action': 'submit'}).status_code, 400)

    @patch('app.scopes.send_notification')
    def test_conversation_and_rejected_hourly_estimate(self, send):
        row_id = self.create(action='submit')
        detail_path = self.path(row_id)

        def post(actor, action, **fields):
            self.client = self.browser(actor)
            return self.client.post(self.path(row_id, '/transition'), data={
                'action': action, 'csrf_token': self.csrf(), **fields}, follow_redirects=True)

        with self.app.app_context(), transaction() as db:
            peer = db.scalar(select(User).where(User.auth_user_id == PEER))
            member = db.scalar(select(Membership).where(Membership.user_id == peer.id))
            member.role_code = 'client_admin'
        self.assertEqual(post(fixtures.ADMIN, 'start_review').status_code, 200)
        self.assertEqual(post(fixtures.ADMIN, 'send_message', body='Which reports?').status_code, 200)
        self.assertEqual([c.args[0] for c in send.call_args_list], ['regular@example.com'])
        self.assertEqual(self.browser(fixtures.OTHER).get(detail_path).status_code, 403)
        response = post(PEER, 'send_message', body='Quarterly reports, please.')
        self.assertIn(b'Which reports?', response.data)
        self.assertIn(b'Quarterly reports, please.', response.data)
        self.assertIn(b'class="scope-history-message"', response.data)
        self.assertNotIn(b'Conversation</h2>', response.data)
        self.assertLess(response.data.index(b'id="scope-message-composer"'), response.data.index(b'id="scope-activity"'))
        self.assertIn(b'Message sent', response.data)
        self.assertIn(b'Under review', response.data)
        self.assertEqual([c.args[0] for c in send.call_args_list], ['regular@example.com', 'team@codebisolutions.com'])
        send.reset_mock()
        invalid = post(fixtures.ADMIN, 'issue_estimate', scope_snapshot='Quarterly reports',
                       pricing_type='hourly', min_hours='4', max_hours='8', hourly_rate='125.25')
        self.assertEqual(invalid.status_code, 422)
        self.assertIn(b'Enter an estimated completion date', invalid.data)
        send.assert_not_called()
        response = post(fixtures.ADMIN, 'issue_estimate', scope_snapshot='Quarterly reports',
                        pricing_type='hourly', min_hours='4', max_hours='8', hourly_rate='125',
                        assumptions='Data provided', estimated_completion='2026-12-15')
        self.assertIn(b'Awaiting acceptance', response.data)
        self.assertIn(b'Estimate sent', response.data)
        self.assertNotIn(b'Scope of work updated.', response.data)
        self.assertNotIn(b'Notification accepted for delivery.', response.data)
        self.assertIn(b'$500', response.data)
        self.assertIn(b'$1,000', response.data)
        self.assertIn(b'2026-12-15', response.data)
        self.assertEqual([c.args[0] for c in send.call_args_list], ['client@example.com', 'peer@example.com'])
        with self.app.app_context(), transaction() as db:
            estimate_id = db.scalar(select(SowEstimate.id))
            self.assertEqual(db.scalar(select(func.count()).select_from(SowMessage)), 2)
        self.assertEqual(post(fixtures.REGULAR, 'accept_estimate', estimate_id=estimate_id).status_code, 403)
        send.reset_mock()
        rejected = post(fixtures.CLIENT, 'reject_estimate', estimate_id=estimate_id, body='Outside our budget.')
        self.assertEqual(rejected.status_code, 200)
        self.assertIn(b'Outside our budget.', rejected.data)
        replay = post(fixtures.CLIENT, 'reject_estimate', estimate_id=estimate_id, body='Outside our budget.')
        self.assertEqual(replay.status_code, 200)
        self.assertIn(b'Estimate rejected', replay.data)
        self.assertNotIn(b'Notification accepted for delivery.', replay.data)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], 'team@codebisolutions.com')
        self.assertIn('rejected by client', send.call_args.args[1])
        self.assertIn(b'Operations dashboard', self.browser(fixtures.ADMIN).get('/portal/scopes-of-work?status=declined').data)
        with self.app.app_context(), transaction() as db:
            self.assertEqual(db.get(SowEstimate, estimate_id).status, 'rejected')
            self.assertIsNone(db.get(ScopeOfWork, row_id).accepted_estimate_id)
            self.assertEqual(db.scalar(select(func.count()).select_from(SowMessage)), 3)

    def test_admin_author_routing_and_work_completion(self):
        row_id = self.create(actor=fixtures.CLIENT, action='submit')
        with self.app.app_context():
            def move(actor, action, **fields):
                scopes.transition(actor, fixtures.A, row_id, action, fields)

            with transaction() as db:
                peer = db.scalar(select(User).where(User.auth_user_id == PEER))
                db.scalar(select(Membership).where(Membership.user_id == peer.id)).role_code = 'client_admin'
            move(fixtures.ADMIN, 'start_review')
            move(fixtures.REGULAR, 'send_message', body='Quarterly data.')
            with self.assertRaises(services.InvalidDetails):
                move(fixtures.ADMIN, 'issue_estimate', scope_snapshot='Dashboard',
                     pricing_type='project', project_amount='1500')
            move(fixtures.ADMIN, 'issue_estimate', scope_snapshot='Dashboard',
                 pricing_type='project', project_amount='1,500', estimated_completion='2026-12-15')
            self.assertEqual(scopes.notification(fixtures.ADMIN, fixtures.A, row_id, 'issue_estimate')[2], ['client@example.com'])
            with transaction() as db:
                estimate = db.scalar(select(SowEstimate))
                estimate_id = estimate.id
                self.assertEqual(estimate.min_amount, 1500)
                self.assertIsNone(estimate.min_hours)
                self.assertEqual(estimate.estimated_completion.isoformat(), '2026-12-15')
            detail = self.browser(fixtures.CLIENT).get(self.path(row_id))
            self.assertIn(b'$1,500', detail.data)
            self.assertIn(b'2026-12-15', detail.data)
            self.assertIn(b'Approve the scope of work', detail.data)
            self.client = self.browser(PEER)
            with patch('app.scopes.send_notification') as send:
                for _ in range(2):
                    approved = self.client.post(self.path(row_id, '/transition'), data={
                        'action': 'accept_estimate', 'estimate_id': estimate_id, 'body': 'Please proceed.',
                        'csrf_token': self.csrf()}, follow_redirects=True)
                    self.assertEqual(approved.status_code, 200)
                    self.assertIn(b'Estimate accepted', approved.data)
                    self.assertIn(b'Please proceed.', approved.data)
                    self.assertNotIn(b'Scope of work updated.', approved.data)
                    self.assertNotIn(b'Notification accepted for delivery.', approved.data)
                send.assert_called_once()
                self.assertEqual(send.call_args.args[0], 'team@codebisolutions.com')
                self.assertIn('approved by client', send.call_args.args[1])
            move(fixtures.ADMIN, 'start_work')
            move(fixtures.ADMIN, 'log_time', minutes='45', body='Built dashboard')
            move(fixtures.ADMIN, 'complete_work')
            with transaction() as db:
                row = db.get(ScopeOfWork, row_id)
                self.assertEqual((row.status, row.accepted_estimate_id), ('completed', estimate_id))
                self.assertEqual(db.scalar(select(TimeEntry.activity)), 'Built dashboard')

    def test_scope_and_events_roll_back_if_audit_fails(self):
        with self.app.app_context(), patch('app.sow_services.audit', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                scopes.save(fixtures.REGULAR, fixtures.A, self.values())
            with transaction() as db:
                self.assertEqual(db.scalar(select(func.count()).select_from(ScopeOfWork)), 0)
                self.assertEqual(db.scalar(select(func.count()).select_from(SowTool)), 0)
                self.assertEqual(db.scalar(select(func.count()).select_from(SowEvent)), 0)

    def test_filters_and_pagination(self):
        with self.app.app_context(), transaction() as db:
            for i in range(26):
                db.add(ScopeOfWork(client_id=fixtures.A, title='Page item '+str(i), description='Details',
                                   sow_type_id=TYPE, created_by=self.regular.id))
        with self.app.app_context():
            self.assertEqual(scopes.directory(fixtures.REGULAR, fixtures.A)['total'], 26)
            self.assertEqual(len(scopes.directory(fixtures.REGULAR, fixtures.A, page=2)['scopes']), 1)
            self.assertEqual(scopes.directory(fixtures.REGULAR, fixtures.A, status='submitted')['total'], 0)
            self.assertEqual(scopes.directory(fixtures.REGULAR, fixtures.A, query='Page item 25')['total'], 1)
        self.client = self.browser(fixtures.REGULAR)
        page = self.client.get(self.path() + '?page=2&q=Page&status=draft')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Previous', page.data)


if __name__ == '__main__':
    unittest.main()
