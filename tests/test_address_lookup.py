import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx
import test_administration as fixtures
from app.address_lookup import reserve_request


class AddressLookupTests(unittest.TestCase):
    browser = fixtures.AdministrationTests.browser
    csrf = fixtures.AdministrationTests.csrf

    def setUp(self):
        fixtures.AdministrationTests.setUp(self)
        self.app.config.update(GOOGLE_PLACES_API_KEY='private-test-key',
                               GOOGLE_PLACES_MONTHLY_LIMIT=500, GOOGLE_PLACES_PER_MINUTE_LIMIT=10)
        self.url = '/portal/admin/address-suggestions'

    def search(self, **data):
        return self.client.post(self.url, data={'csrf_token': self.csrf(), 'text': '123 Main Austin', **data})

    def choice(self, actor=fixtures.ADMIN, country='US', ttl=600):
        return self.app.extensions['auth_store'].create({'kind': 'address-choice', 'actor': actor,
                                                       'place_id': 'test-place', 'country': country}, ttl)

    def details(self, token):
        return self.client.post(self.url, data={'csrf_token': self.csrf(), 'action': 'details', 'selection': token})

    @staticmethod
    def provider(transport):
        client = transport.return_value.__enter__.return_value
        client.post.return_value = httpx.Response(200, request=httpx.Request('POST', 'https://places.googleapis.com'),
            json={'suggestions': [{'placePrediction': {'placeId': 'test-place', 'text': {'text': '123 Main St, Austin TX'}}}]})
        parts = [('street_number', '123', '123'), ('route', 'Main St', 'Main St'),
                 ('locality', 'Austin', 'Austin'), ('administrative_area_level_1', 'Texas', 'TX'),
                 ('country', 'United States', 'US'), ('postal_code', '78701', '78701')]
        client.get.return_value = httpx.Response(200, request=httpx.Request('GET', 'https://places.googleapis.com'),
            json={'addressComponents': [{'types': [t], 'longText': long, 'shortText': short} for t, long, short in parts],
                  'timeZone': {'id': 'America/Chicago'}})
        return client

    @patch('app.address_lookup.httpx.Client')
    def test_search_then_selection_make_exactly_two_calls(self, transport):
        upstream = self.provider(transport)
        result = self.search(country='US')
        self.assertEqual(result.status_code, 200)
        upstream.post.assert_called_once()
        upstream.get.assert_not_called()
        self.assertEqual(upstream.post.call_args.kwargs['json']['includedRegionCodes'], ['us'])
        self.assertNotIn(b'private-test-key', result.data)
        selected = self.details(result.json[0]['selection'])
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.json['state_region_code'], 'US-TX')
        self.assertEqual(selected.json['address_line_1'], '123 Main St')
        self.assertEqual(selected.json['postal_code'], '78701')
        self.assertNotIn('address_line_2', selected.json)
        self.assertEqual(selected.json['timezone'], 'America/Chicago')
        upstream.get.assert_called_once()
        mask = upstream.get.call_args.kwargs['headers']['X-Goog-FieldMask']
        self.assertEqual(mask, 'addressComponents,formattedAddress,attributions,timeZone')
        self.assertEqual(self.details(result.json[0]['selection']).status_code, 400)
        upstream.get.assert_called_once()  # Selection replay incurs no additional cost.

    @patch('app.address_lookup.httpx.Client')
    def test_access_csrf_invalid_inputs_and_missing_key_do_not_call_google(self, transport):
        self.assertEqual(self.client.post(self.url).status_code, 400)
        self.assertEqual(self.browser(fixtures.CLIENT).post(self.url).status_code, 403)
        self.assertEqual(self.app.test_client().post(self.url).status_code, 302)
        for data in ({'text': '12'}, {'text': 'a' * 251}, {'country': 'GB'}, {'action': 'unknown'},
                     {'action': 'details', 'selection': 'forged'}):
            self.assertEqual(self.search(**data).status_code, 400)
        self.app.config['GOOGLE_PLACES_API_KEY'] = None
        self.assertEqual(self.search().status_code, 503)
        transport.assert_not_called()

    @patch('app.address_lookup.httpx.Client')
    def test_failed_calls_count_towards_cap_without_leaking_key(self, transport):
        self.app.config['GOOGLE_PLACES_MONTHLY_LIMIT'] = 1
        self.provider(transport).post.side_effect = httpx.ConnectError('private-test-key')
        failed = self.search()
        self.assertEqual(failed.status_code, 503)
        self.assertNotIn(b'private-test-key', failed.data)
        self.assertEqual(self.search().status_code, 429)
        transport.return_value.__enter__.return_value.post.assert_called_once()

    @patch('app.address_lookup.httpx.Client')
    def test_monthly_cap_is_shared_across_search_and_details(self, transport):
        self.app.config['GOOGLE_PLACES_MONTHLY_LIMIT'] = 1
        upstream = self.provider(transport)
        result = self.search()
        self.assertEqual(self.details(result.json[0]['selection']).status_code, 429)
        upstream.get.assert_not_called()

    @patch('app.address_lookup.httpx.Client')
    def test_per_user_rate_limit(self, transport):
        self.app.config['GOOGLE_PLACES_PER_MINUTE_LIMIT'] = 1
        self.provider(transport)
        self.assertEqual(self.search().status_code, 200)
        self.assertEqual(self.search().status_code, 429)

    @patch('app.address_lookup.httpx.Client')
    def test_expired_and_other_user_selections_never_call_google(self, transport):
        self.assertEqual(self.details(self.choice(ttl=-1)).status_code, 400)
        self.assertEqual(self.details(self.choice(actor=fixtures.OTHER)).status_code, 400)
        transport.assert_not_called()

    @patch('app.address_lookup.httpx.Client')
    def test_canadian_address_maps_to_province(self, transport):
        upstream = self.provider(transport)
        upstream.get.return_value = httpx.Response(200, request=httpx.Request('GET', 'https://places.googleapis.com'),
            json={'addressComponents': [
                {'types': ['country'], 'shortText': 'CA'},
                {'types': ['administrative_area_level_1'], 'shortText': 'ON'},
                {'types': ['postal_code'], 'longText': 'M5V 3L9'}]})
        response = self.details(self.choice(country='CA'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['state_region_code'], 'CA-ON')
        self.assertEqual(response.json['timezone'], '')  # Missing timezone does not reject the address.

    @patch('app.address_lookup.httpx.Client')
    def test_timezone_must_be_an_active_catalog_value(self, transport):
        from app.database import transaction
        from app.models import Timezone
        with self.app.app_context(), transaction() as db:
            db.add(Timezone(name='America/New_York'))
            db.add(Timezone(name='America/Phoenix', is_active=False))
        upstream = self.provider(transport)
        for zone, expected in [('America/New_York', 'America/New_York'),
                               ('America/Phoenix', ''), ('Unknown/Timezone', '')]:
            with self.subTest(zone=zone):
                body = upstream.get.return_value.json()
                body['timeZone'] = {'id': zone}
                upstream.get.return_value = httpx.Response(200,
                    request=httpx.Request('GET', 'https://places.googleapis.com'), json=body)
                response = self.details(self.choice())
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json['timezone'], expected)
        self.assertEqual(upstream.get.call_count, 3)  # No separate timezone API calls.

    def test_atomic_cap_across_concurrent_workers(self):
        self.app.config.update(GOOGLE_PLACES_MONTHLY_LIMIT=3, GOOGLE_PLACES_PER_MINUTE_LIMIT=100)
        def reserve(_):
            with self.app.app_context():
                return reserve_request(fixtures.ADMIN)
        with ThreadPoolExecutor(max_workers=5) as workers:
            self.assertEqual(sum(workers.map(reserve, range(10))), 3)

    @patch('app.address_lookup.httpx.Client')
    def test_counter_storage_failure_blocks_google(self, transport):
        self.app.config['ADDRESS_USAGE_PATH'] = Path(self.temp.name) / 'missing-folder' / 'counters.sqlite3'
        self.assertEqual(self.search().status_code, 503)
        transport.assert_not_called()

    @patch('app.address_lookup.httpx.Client')
    def test_form_renders_find_button_without_network_request(self, transport):
        page = self.client.get('/portal/admin/clients/new')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Find address', page.data)
        self.assertIn(b'aria-label="Search address"', page.data)
        self.assertIn(b'id="address-search-dialog"', page.data)
        self.assertNotIn(b'Address &amp; preferences', page.data)
        self.assertNotIn(b'>Find address</button>', page.data)
        self.assertIn(b'address-autocomplete.js', page.data)
        self.assertNotIn(b'private-test-key', page.data)
        transport.assert_not_called()

    @patch('app.address_lookup.httpx.Client')
    def test_verified_registration_uses_same_protected_adapter(self, transport):
        upstream = self.provider(transport)
        browser = self.app.test_client()
        actor = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'
        import time
        pending = self.app.extensions['auth_store'].create({'kind': 'onboarding', 'verified': True,
            'auth_user_id': actor, 'email': 'new@example.com', 'provider': 'google',
            'access_token': 'test', 'refresh_token': 'test', 'expires_at': time.time() + 3600}, 1800)
        with browser.session_transaction() as state:
            state.update(onboarding=pending, csrf='test-csrf')
        response = browser.post('/auth/register/address-suggestions',
            data={'csrf_token': 'test-csrf', 'text': '123 Main Austin', 'country': 'US'})
        self.assertEqual(response.status_code, 200)
        upstream.post.assert_called_once()
        self.assertEqual(browser.post('/auth/register/address-suggestions', data={'text': '123 Main'}).status_code, 400)
        self.assertEqual(self.app.test_client().post('/auth/register/address-suggestions').status_code, 302)
