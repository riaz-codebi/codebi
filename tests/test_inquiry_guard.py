"""No live database, email provider or external spam-classification service."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import unittest
from unittest.mock import patch

from flask import session
from sqlalchemy import func, select

from app import inquiry_guard
from app.database import transaction
from app.models import PublicInquiry
import test_registration as registration


class InquiryGuardTests(unittest.TestCase):
    def setUp(self):
        registration.RegistrationTests.setUp(self)
        # Other abuse tests isolate their own checks using concise fixtures.
        self.app.config['INQUIRY_MIN_WORDS']=1
    csrf = registration.RegistrationTests.csrf
    browser = registration.RegistrationTests.browser

    def data(self, client=None, **changes):
        client = client or self.client
        page = client.get('/contact')
        token = re.search(rb'name="inquiry_token" value="([^"]+)"', page.data).group(1).decode()
        with client.session_transaction() as state:
            csrf = state['csrf']
        return {'name': 'Casey Smith', 'company': 'Example Co', 'email': 'casey@example.com',
                'details': 'We need help with our monthly Power BI reports.',
                'inquiry_token': token, 'csrf_token': csrf, **changes}

    def count(self):
        with self.app.app_context(), transaction() as db:
            return db.scalar(select(func.count()).select_from(PublicInquiry))

    def test_bot_field_accepts_silently_without_saving_or_sending(self):
        data = self.data(website='https://spam.example')
        response = self.client.post('/contact', data=data)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.count(), 0)
        self.send_inquiry_confirmation.assert_not_called()

    def test_twenty_word_minimum_blocks_direct_posts_preserves_fields_and_sends_no_email(self):
        self.app.config['INQUIRY_MIN_WORDS']=20
        words='Our team needs a reporting solution that combines sales and inventory data to improve planning and reduce manual spreadsheet work'.split()
        short=' '.join(words[:19])+' !!! 123 456'
        response=self.client.post('/contact',data=self.data(details=short))
        self.assertEqual(response.status_code,422)
        self.assertIn(b'Please provide at least 20 words',response.data)
        self.assertIn(short.encode(),response.data)
        self.assertEqual(self.count(),0)
        self.send_inquiry_confirmation.assert_not_called()
        self.assertEqual(self.client.post('/contact',data=self.data(details=' '.join(words[:20]))).status_code,303)
        self.assertEqual(self.count(),1)

    def test_word_count_accepts_unicode_and_counts_hyphenated_words_once(self):
        self.app.config['INQUIRY_MIN_WORDS']=20
        with self.app.test_request_context('/contact'):
            values={'name':'Casey','email':'casey@example.com','company':'Example',
                    'details':' '.join(['report-ready']*19)}
            self.assertIn('at least 20 words',inquiry_guard.content_issue(values))
            values['details']+=' análisis'
            self.assertIsNone(inquiry_guard.content_issue(values))

    def test_missing_forged_and_other_session_token_do_not_send(self):
        data = self.data()
        other = self.app.test_client()
        other_data = self.data(other)
        for token in ('', 'forged', other_data['inquiry_token']):
            response = self.client.post('/contact', data={**data, 'inquiry_token': token})
            self.assertEqual(response.status_code, 422)
            self.assertIn(b'This form expired', response.data)
        self.assertEqual(self.count(), 0)
        self.send_inquiry_confirmation.assert_not_called()

    def test_timing_rejects_fast_and_expired_but_allows_retry(self):
        self.app.config['INQUIRY_MIN_SECONDS'] = 2
        with patch('app.inquiry_guard.time.time', return_value=100000):
            data = self.data()
            response = self.client.post('/contact', data=data)
            self.assertEqual(response.status_code, 422)
            self.assertIn(b'take a moment', response.data)
        self.send_inquiry_confirmation.assert_not_called()
        with patch('app.inquiry_guard.time.time', return_value=100003):
            self.assertEqual(self.client.post('/contact', data=data).status_code, 303)
        with patch('app.inquiry_guard.time.time', return_value=107201):
            self.assertEqual(self.client.post('/contact', data=data).status_code, 422)
        self.assertEqual(self.count(), 1)

    def test_screenshot_random_bodies_and_link_spam_do_not_send(self):
        for body in ('VPnBqoMAsPIEYjZvBrp', 'rUZpGTFXKdfAJkAlE',
                     'https://example.com', 'Visit ' + 'https://example.com ' * 4,
                     '<script>alert(1)</script>'):
            response = self.client.post('/contact', data=self.data(details=body))
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.count(), 0)
        self.send_inquiry_confirmation.assert_not_called()

    def test_legitimate_non_english_names_free_email_and_technical_terms_pass(self):
        for name, body in [('李明', '我们需要帮助建立销售报表和数据分析系统。'),
                           ('José García', 'Necesitamos ayuda con informes de Power BI.'),
                           ('Casey', 'Please integrate API SKU123 and SSO with our ERP.')]:
            response = self.client.post('/contact', data=self.data(name=name, details=body, email='person@gmail.com'))
            self.assertEqual(response.status_code, 303)
        self.assertEqual(self.count(), 3)
        self.send_inquiry_confirmation.assert_not_called()

    def test_duplicate_sends_neither_team_nor_sender_email(self):
        data = self.data()
        self.assertEqual(self.client.post('/contact', data=data).status_code, 303)
        self.assertEqual(self.client.post('/contact', data={**data, 'email': 'CASEY@example.com',
            'details': data['details'].upper()}).status_code, 303)
        self.assertEqual(self.count(), 1)
        self.send_inquiry_confirmation.assert_not_called()

    def test_rotating_address_campaign_and_generic_price_templates_do_not_send(self):
        messages=('Hola, volia saber el seu preu.', 'Zdravo, htio sam znati vašu cijenu.',
                  'Hæ, ég vildi vita verð þitt.', 'Ndewo, achọrọ m ịmara ọnụahịa gị.')
        for index,body in enumerate(messages):
            for name,company in (('Robertsum','google'),('New Name','Example Co')):
                response=self.client.post('/contact',data=self.data(name=name,company=company,
                    email=f'rotating{index}@example.com',details=body))
                self.assertEqual(response.status_code,422)
        self.assertEqual(self.count(),0)
        self.send_inquiry_confirmation.assert_not_called()

    def test_profile_and_body_limits_survive_rotating_emails_and_sessions(self):
        self.app.config.update(INQUIRY_PROFILE_LIMIT=2,INQUIRY_BODY_LIMIT=2)
        for index in range(3):
            client=self.app.test_client()
            response=client.post('/contact',data=self.data(client,email=f'profile{index}@example.com',
                details=f'Help us build reporting for department {index}.'))
            self.assertEqual(response.status_code,303 if index<2 else 429)
        for index in range(3):
            client=self.app.test_client()
            response=client.post('/contact',data=self.data(client,name=f'Person {index}',company=f'Company {index}',
                email=f'body{index}@example.com',details='Please help us improve our warehouse reports.'))
            self.assertEqual(response.status_code,303 if index<2 else 429)
        self.assertEqual(self.count(),4)

    def test_real_google_contact_and_contextual_pricing_inquiries_pass(self):
        for name,company,body in (
            ('Robert Sum','Google','We need help building a Power BI dashboard for our team.'),
            ('José García','Example Co','Hola, necesitamos un presupuesto para informes de Power BI.'),
            ('Casey Smith','Example Co','What is your price for migrating our Flask application?')):
            self.assertEqual(self.client.post('/contact',data=self.data(name=name,company=company,details=body)).status_code,303)
        self.assertEqual(self.count(),3)

    def test_email_limit_survives_new_browser_sessions(self):
        for index in range(4):
            client = self.app.test_client()
            response = client.post('/contact', data=self.data(client, details=f'Help us build reporting for division {index}.'))
            self.assertEqual(response.status_code, 303 if index < 3 else 429)
        self.assertEqual(self.count(), 3)
        self.send_inquiry_confirmation.assert_not_called()

    def test_global_limit_bounds_random_senders_across_browser_sessions(self):
        self.app.config['INQUIRY_HOURLY_LIMIT'] = 2
        for index in range(3):
            client = self.app.test_client()
            response = client.post('/contact', data=self.data(client, email=f'person{index}@example.com'))
            self.assertEqual(response.status_code, 303 if index < 2 else 429)
        self.assertEqual(self.count(), 2)
        self.send_inquiry_confirmation.assert_not_called()

    def test_session_and_public_ip_limits_ignore_spoofed_forwarded_headers(self):
        self.app.config.update(INQUIRY_SESSION_LIMIT=2, INQUIRY_IP_LIMIT=2,
                               INQUIRY_BODY_LIMIT=10, INQUIRY_PROFILE_LIMIT=10)
        for index in range(3):
            response = self.client.post('/contact', data=self.data(email=f'session{index}@example.com'))
            self.assertEqual(response.status_code, 303 if index < 2 else 429)
        # A new session still shares the real public peer's bucket, not X-Forwarded-For.
        for index in range(3):
            client = self.app.test_client()
            response = client.post('/contact', data=self.data(client, email=f'ip{index}@example.com'),
                environ_overrides={'REMOTE_ADDR': '8.8.8.8'}, headers={'X-Forwarded-For': f'1.1.1.{index}'})
            self.assertEqual(response.status_code, 303 if index < 2 else 429)

    def test_limits_are_atomic_and_do_not_store_raw_addresses_or_content(self):
        def reserve(index):
            with self.app.test_request_context('/contact', environ_base={'REMOTE_ADDR': '127.0.0.1'}):
                session['csrf'] = str(index)
                return inquiry_guard.reserve_submission('concurrent@example.com')
        with ThreadPoolExecutor(max_workers=6) as pool:
            self.assertEqual(sum(pool.map(reserve, range(8))), 3)
        path = Path(self.store.path).with_name('inquiry_usage.sqlite3')
        with closing(sqlite3.connect(path)) as db:
            keys = [row[0] for row in db.execute('SELECT key FROM inquiry_limits')]
        self.assertTrue(all(re.fullmatch('[0-9a-f]{64}', key) for key in keys))

    def test_window_reset_releases_email_and_session_limits(self):
        with patch('app.inquiry_guard.time.time', return_value=100000):
            for index in range(3):
                self.assertEqual(self.client.post('/contact', data=self.data(details=f'Help with report number {index}.')).status_code, 303)
            self.assertEqual(self.client.post('/contact', data=self.data()).status_code, 429)
        with patch('app.inquiry_guard.time.time', return_value=100901):
            self.assertEqual(self.client.post('/contact', data=self.data()).status_code, 303)

    def test_guard_storage_failure_stops_email_and_inquiry_write(self):
        self.app.config['INQUIRY_USAGE_PATH'] = Path(self.temp.name) / 'missing' / 'usage.sqlite3'
        self.assertEqual(self.client.post('/contact', data=self.data()).status_code, 503)
        self.assertEqual(self.count(), 0)
        self.send_inquiry_confirmation.assert_not_called()

    def test_header_injection_stops_before_saving(self):
        self.assertEqual(self.client.post('/contact', data=self.data(name='Casey\nBcc: victim@example.com')).status_code, 422)
        self.assertEqual(self.count(), 0)
        self.send_inquiry_confirmation.assert_not_called()
