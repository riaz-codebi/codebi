import unittest
from sqlalchemy import select

import test_commerce as fixtures
from app.database import transaction
from commerce_hub.models import Job


class CommerceTabTests(unittest.TestCase):
    browser=fixtures.CommerceTests.browser
    csrf=fixtures.CommerceTests.csrf
    configure=fixtures.CommerceTests.configure
    url=fixtures.CommerceTests.url
    def setUp(self):fixtures.CommerceTests.setUp(self)

    def test_home_tabs_render_only_the_selected_panel(self):
        for tab,label in [('connections','Connections'),('jobs','Jobs'),('exports','Exports')]:
            with self.subTest(tab=tab):
                response=self.client.get(self.url()+'?tab='+tab)
                self.assertEqual(response.status_code,200)
                self.assertIn(('aria-current="page">'+label+'</a>').encode(),response.data)
                self.assertIn(('data-commerce-tab-panel="'+tab+'"').encode(),response.data)
                for other in {'connections','jobs','exports'}-{tab}:
                    self.assertNotIn(('data-commerce-tab-panel="'+other+'"').encode(),response.data)

    def test_default_and_invalid_tab_select_connections(self):
        for suffix in ('','?tab=invalid'):
            response=self.client.get(self.url()+suffix)
            self.assertIn(b'data-commerce-tab-panel="connections"',response.data)
            self.assertNotIn(b'data-commerce-jobs-panel',response.data)

    def test_product_inventory_and_connection_pages_keep_selected_navigation(self):
        for suffix,label in [('/results/catalog','Products'),('/results/inventory','Inventory'),('/connections/'+self.catalog,'Connections')]:
            response=self.client.get(self.url(suffix))
            self.assertEqual(response.status_code,200)
            self.assertIn(('aria-current="page">'+label+'</a>').encode(),response.data)

    def test_filters_and_refresh_keep_jobs_selected(self):
        response=self.client.get(self.url()+'?tab=jobs&job_search=Office&job_period=7')
        self.assertIn(b'name="tab" value="jobs"',response.data)
        self.assertIn(b'aria-current="page">Jobs</a>',response.data)
        self.assertIn(b'value="Office"',response.data)
        self.assertNotIn(b'data-commerce-tab-panel="connections"',response.data)
        legacy=self.client.get(self.url()+'?job_period=7')
        self.assertIn(b'aria-current="page">Jobs</a>',legacy.data)

    def test_queued_connection_job_and_cancel_return_to_jobs(self):
        self.configure()
        response=self.client.post(self.url('/connections/'+self.catalog+'/run'),
            data={'csrf_token':self.csrf(),'kind':'test_connection'})
        self.assertEqual(response.status_code,302)
        self.assertIn('tab=jobs',response.location)
        with self.app.app_context(),transaction() as db:job=db.scalar(select(Job.id))
        response=self.client.post(self.url('/jobs/'+job+'/cancel'),data={'csrf_token':self.csrf()})
        self.assertIn('tab=jobs',response.location)
