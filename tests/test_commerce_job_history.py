from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select
import test_commerce as fixtures
from app.commerce import services
from app.database import transaction
from commerce_hub.models import Job, VendorConnection


class JobHistoryTests(unittest.TestCase):
    browser=fixtures.CommerceTests.browser
    csrf=fixtures.CommerceTests.csrf
    url=fixtures.CommerceTests.url

    def setUp(self):
        fixtures.CommerceTests.setUp(self)
        self.clock=datetime(2026,11,26,18,tzinfo=timezone.utc)

    def seed(self,kind='catalog',mode=None,label='',category='440',days=0,actor=None,client=None):
        client=client or fixtures.fixtures.A
        with self.app.app_context(),transaction() as db:
            connection=db.scalar(select(VendorConnection).where(VendorConnection.client_id==client,VendorConnection.connection_type=='catalog'))
            payload={'catalog_selection':{'mode':mode}} if mode else {}
            if mode=='category':payload['catalog_selection'].update(category_id=category,category_label=label)
            row=Job(client_id=client,vendor_id=self.vendor_id,connection_id=connection.id,credential_version=0,
                requested_by=actor or self.member.id,kind=kind,status='succeeded',payload=payload,
                created_at=self.clock-timedelta(days=days),started_at=self.clock-timedelta(days=days),finished_at=self.clock)
            db.add(row);db.flush()
            return row.id

    def history(self,filters=None):
        with self.app.app_context(),patch('app.commerce.services.now',return_value=self.clock):
            return services.job_history(fixtures.fixtures.CLIENT,fixtures.fixtures.A,filters)

    def test_search_separates_all_product_runs_and_removed_filters_do_not_hide_jobs(self):
        legacy=self.seed()
        all_products=self.seed(mode='all')
        selected=self.seed(mode='category',label='Outerwear')
        self.seed(kind='test_connection')
        self.assertEqual({j['id'] for j in self.history({'job_search':'All products'})['jobs']},{legacy,all_products})
        self.assertEqual([j['id'] for j in self.history({'job_search':'Outerwear'})['jobs']],[selected])
        self.assertEqual(self.history({'job_scope':'category','job_category':'440'})['job_pagination']['total'],4)

    def test_relative_date_filters_use_run_start(self):
        newest=self.seed(days=.25)
        week=self.seed(days=3)
        month=self.seed(days=15)
        self.seed(days=40)
        self.assertEqual({j['id'] for j in self.history({'job_period':'1'})['jobs']},{newest})
        self.assertEqual({j['id'] for j in self.history({'job_period':'7'})['jobs']},{newest,week})
        self.assertEqual({j['id'] for j in self.history({'job_period':'30'})['jobs']},{newest,week,month})

    def test_connection_tests_identify_catalog_or_inventory_in_history_and_polling(self):
        catalog=self.seed(kind='test_connection')
        inventory=self.seed(kind='test_connection')
        legacy=self.seed(kind='test_connection')
        with self.app.app_context(),transaction() as db:
            db.get(Job,inventory).connection_id=self.inventory
            db.get(Job,legacy).connection_id=None
        labels={row['id']:row['job_label'] for row in self.history()['jobs']}
        self.assertEqual(labels[catalog],'Test catalog connection')
        self.assertEqual(labels[inventory],'Test inventory connection')
        self.assertEqual(labels[legacy],'Test connection')
        page=self.client.get(self.url('/jobs'))
        self.assertIn(b'Test catalog connection',page.data)
        self.assertIn(b'Test inventory connection',page.data)
        response=self.client.get(self.url('/jobs/'+inventory))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.get_json()['job_label'],'Test inventory connection')
        self.assertEqual(response.get_json()['kind'],'test_connection')

    def test_search_and_pagination_cover_history_beyond_first_25_rows(self):
        target=self.seed(mode='category',label='Older Office & Home Decor',days=40,actor=self.regular.id)
        for _ in range(30):self.seed(kind='test_connection')
        first=self.history()
        self.assertEqual(first['job_pagination']['total'],31)
        self.assertEqual(len(first['jobs']),25)
        self.assertNotIn(target,{j['id'] for j in first['jobs']})
        self.assertEqual(len(self.history({'job_page':'2'})['jobs']),6)
        self.assertEqual([j['id'] for j in self.history({'job_search':'Office & Home'})['jobs']],[target])
        self.assertEqual([j['id'] for j in self.history({'job_search':self.regular.display_name})['jobs']],[target])
        self.assertEqual([j['id'] for j in self.history({'job_search':target})['jobs']],[target])

    def test_search_treats_wildcard_characters_literally(self):
        target=self.seed(mode='category',label='Office_Home')
        self.seed(mode='all')
        self.assertEqual([j['id'] for j in self.history({'job_search':'_'})['jobs']],[target])
        self.assertEqual(self.history({'job_search':'%'})['job_pagination']['total'],0)

    def test_requester_and_run_date_use_existing_identity_and_client_timezone(self):
        job=self.seed()
        from app.models import Client
        with self.app.app_context(),transaction() as db:db.get(Client,fixtures.fixtures.A).timezone='America/Chicago'
        row=self.history()['jobs'][0]
        self.assertEqual(row['requested_by_name'],'Client Admin')
        self.assertEqual(row['run_date_display'],'2026-11-26 12:00')
        with self.app.app_context():status=services.job_status(fixtures.fixtures.CLIENT,fixtures.fixtures.A,job)
        self.assertEqual(status['run_date_display'],row['run_date_display'])

    def test_fragment_and_category_choices_are_client_scoped(self):
        self.seed(mode='category',label='Outerwear')
        self.seed(mode='category',label='Private category',category='999',client=fixtures.fixtures.B,actor=self.other.id)
        data=self.history()
        self.assertNotIn('Private category',{j['catalog_label'] for j in data['jobs']})
        url=self.url('/jobs')
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertIn(b'data-job-history-content',response.data)
        self.assertNotIn(b'Private category',response.data)
        self.assertEqual(self.browser(fixtures.fixtures.OTHER).get(url).status_code,403)
