"""Staff review and notification gating; no live email or database calls."""
from datetime import datetime,timedelta,timezone
import unittest
from unittest.mock import patch

from sqlalchemy import select
from app.database import transaction
from app.models import PublicInquiry,AuditEvent
import test_registration as registration
import test_administration as fixtures


class InquiryReviewTests(unittest.TestCase):
    setUp=registration.RegistrationTests.setUp
    browser=fixtures.AdministrationTests.browser
    csrf=registration.RegistrationTests.csrf

    def queue(self,**overrides):
        with self.app.app_context(),transaction() as db:
            row=PublicInquiry(name='Casey <Client>',email='casey@example.com',company='Example Co',
                details='We need help with our sales reports.',**overrides)
            db.add(row);db.flush();return row.id

    def action(self,browser,inquiry_id,action,csrf=True):
        browser.get('/portal/admin/inquiries')
        with browser.session_transaction() as state:token=state.get('csrf','')
        return browser.post(f'/portal/admin/inquiries/{inquiry_id}/review',
            data={'action':action,'csrf_token':token if csrf else ''})

    def saved(self,inquiry_id):
        with self.app.app_context(),transaction() as db:return db.get(PublicInquiry,inquiry_id)

    @patch('app.inquiry_review.send_notification')
    def test_staff_approval_sends_one_formatted_acknowledgment_and_repeated_clicks_do_not_resend(self,send):
        inquiry=self.queue();browser=self.browser(fixtures.ADMIN)
        page=browser.get('/portal/admin/inquiries')
        self.assertIn(b'Mark legitimate',page.data)
        self.assertEqual(self.action(browser,inquiry,'approve').status_code,303)
        self.assertEqual(self.action(browser,inquiry,'approve').status_code,303)
        self.assertEqual(send.call_count,1)
        args,kwargs=send.call_args
        self.assertEqual(args[0],'casey@example.com');self.assertIn('onboarding team',args[2])
        self.assertIn('Casey &lt;Client&gt;',kwargs['html'])
        self.assertEqual(kwargs['idempotency_key'],'inquiry-ack-'+inquiry)
        row=self.saved(inquiry)
        self.assertEqual(row.review_status,'legitimate');self.assertEqual(row.acknowledgement_status,'sent')
        self.assertIsNotNone(row.reviewed_by);self.assertIsNotNone(row.acknowledged_at)
        with self.app.app_context(),transaction() as db:
            actions=list(db.scalars(select(AuditEvent.action).where(AuditEvent.entity_id==inquiry)))
        self.assertEqual(sorted(actions),['inquiry.acknowledged','inquiry.approved'])

    @patch('app.inquiry_review.send_notification')
    def test_delete_hides_inquiry_preserves_review_history_and_sends_nothing(self,send):
        inquiry=self.queue();browser=self.browser(fixtures.ADMIN)
        self.assertEqual(self.action(browser,inquiry,'delete').status_code,303)
        self.assertEqual(self.saved(inquiry).review_status,'deleted')
        self.assertNotIn(b'casey@example.com',browser.get('/portal/admin/inquiries?status=all').data)
        self.action(browser,inquiry,'approve');send.assert_not_called()

    @patch('app.inquiry_review.send_notification')
    def test_client_and_missing_csrf_cannot_review_or_send(self,send):
        inquiry=self.queue()
        self.assertEqual(self.action(self.browser(fixtures.CLIENT),inquiry,'approve').status_code,403)
        self.assertEqual(self.action(self.browser(fixtures.ADMIN),inquiry,'approve',csrf=False).status_code,400)
        self.assertEqual(self.saved(inquiry).review_status,'pending');send.assert_not_called()

    @patch('app.inquiry_review.send_notification')
    def test_failed_acknowledgment_stays_legitimate_and_explicit_retry_uses_same_key(self,send):
        inquiry=self.queue();browser=self.browser(fixtures.ADMIN)
        send.side_effect=RuntimeError('private-provider-value')
        self.action(browser,inquiry,'approve')
        row=self.saved(inquiry)
        self.assertEqual(row.review_status,'legitimate');self.assertEqual(row.acknowledgement_status,'failed')
        page=browser.get('/portal/admin/inquiries?status=legitimate')
        self.assertIn(b'Retry acknowledgment',page.data);self.assertNotIn(b'private-provider-value',page.data)
        send.side_effect=None
        self.action(browser,inquiry,'retry')
        self.assertEqual(self.saved(inquiry).acknowledgement_status,'sent')
        self.assertEqual(send.call_args_list[0].kwargs['idempotency_key'],send.call_args_list[1].kwargs['idempotency_key'])

    @patch('app.inquiry_review.send_notification')
    def test_expired_ambiguous_delivery_and_historical_inquiries_are_not_resent(self,send):
        browser=self.browser(fixtures.ADMIN)
        old=self.queue(review_status='legitimate',acknowledgement_status='failed',
            acknowledgement_started_at=datetime.now(timezone.utc)-timedelta(hours=24))
        legacy=self.queue(acknowledgement_status='legacy')
        self.action(browser,old,'retry');self.action(browser,legacy,'approve')
        send.assert_not_called()
        self.assertEqual(self.saved(legacy).review_status,'legitimate')

    @patch('resend.Emails.send')
    def test_notification_transport_passes_html_and_idempotency_options(self,send):
        from app.notifications import send_notification
        self.app.config['RESEND_API_KEY']='test-key'
        with self.app.app_context():send_notification('casey@example.com','Subject','Text',html='<p>Text</p>',idempotency_key='inquiry-test')
        self.assertEqual(send.call_args.args[0]['html'],'<p>Text</p>')
        self.assertEqual(send.call_args.args[1],{'idempotency_key':'inquiry-test'})
