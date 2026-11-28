from datetime import timedelta
import json
import unittest
from unittest.mock import patch
import test_catalog_pipeline as catalog
from app.commerce import services
from app.database import transaction
from commerce_hub.models import Job,Product,CatalogWorkItem,VendorCatalogLease,now
from commerce_hub.recovery import retry_delay,AUTO_ATTEMPTS
from sqlalchemy import select,func


class RecoveryTests(unittest.TestCase):
    setUp=catalog.CatalogPipelineTests.setUp
    csrf=catalog.CatalogPipelineTests.csrf
    browser=catalog.CatalogPipelineTests.browser
    configure=catalog.CatalogPipelineTests.configure
    url=catalog.CatalogPipelineTests.url
    enqueue_scope=catalog.CatalogPipelineTests.enqueue_scope
    record=catalog.CatalogPipelineTests.record
    current=catalog.CatalogPipelineTests.current

    def ready(self,job_id):
        with self.app.app_context(),transaction() as db:
            db.get(Job,job_id).available_at=now()-timedelta(seconds=1)
            db.get(VendorCatalogLease,self.vendor_id).throttle_until=None

    def test_transient_failure_auto_resumes_saved_products_and_sanitizes_details(self):
        job_id=self.enqueue_scope();client=catalog.FakeClient({'1':self.record('One'),'2':self.record('Two'),'3':self.record('Three')})
        original=client.product
        def product(path):
            if path.endswith('-3'):raise RuntimeError('private-database-password')
            return original(path)
        client.product=product
        with patch('commerce_hub.worker.connector',return_value=catalog.FakeAdapter(client)):self.worker.run_once(job_id=job_id)
        with self.app.app_context(),transaction() as db:
            job=db.get(Job,job_id)
            self.assertEqual(job.status,'queued');self.assertEqual(job.max_attempts,AUTO_ATTEMPTS)
            self.assertEqual(job.result['recovery']['state'],'scheduled')
            self.assertNotIn('private-database-password',json.dumps(job.result))
            completed=set(db.scalars(select(Product.source_key).where(Product.job_id==job_id)))
            self.assertTrue(completed)
        with self.app.app_context():
            view=services.job_status(catalog.fixtures.fixtures.CLIENT,catalog.fixtures.fixtures.A,job_id)
            self.assertEqual(view['status_label'],'Retry scheduled');self.assertIn('Automatic retry queued',view['error_message'])
        self.assertIsNone(self.current()['source_job_id'])
        fetched=list(client.fetched);client.product=original;self.ready(job_id)
        with patch('commerce_hub.worker.connector',return_value=catalog.FakeAdapter(client)):self.worker.run_once(job_id=job_id)
        for key in completed:self.assertEqual(client.fetched.count(key),fetched.count(key))
        self.assertEqual(self.current()['source_job_id'],job_id)

    def test_repeated_failure_without_progress_stops_with_explanation(self):
        job_id=self.enqueue_scope();client=catalog.FakeClient({'1':self.record('One')})
        client.product=lambda _path:(_ for _ in ()).throw(RuntimeError('private-value'))
        for _attempt in range(3):
            with patch('commerce_hub.worker.connector',return_value=catalog.FakeAdapter(client)):self.worker.run_once(job_id=job_id)
            self.ready(job_id)
        with self.app.app_context(),transaction() as db:
            job=db.get(Job,job_id)
            self.assertEqual(job.status,'failed');self.assertEqual(job.result['recovery']['state'],'no_progress')
            self.assertEqual(job.attempts,3);self.assertTrue(services.job_view(job)['can_resume'])
            self.assertIn('without progress',services.job_view(job)['error_message'])

    def test_legacy_five_attempt_run_can_receive_new_manual_resume_budget(self):
        job_id=self.enqueue_scope();client=catalog.FakeClient({'1':self.record('One')})
        with patch('commerce_hub.worker.connector',return_value=catalog.FakeAdapter(client)),patch.object(catalog.CatalogRunner,'publish',side_effect=RuntimeError('fixture')):
            self.worker.run_once(job_id=job_id)
        with self.app.app_context(),transaction() as db:
            job=db.get(Job,job_id);job.status='failed';job.attempts=job.max_attempts=5
            job.lease_token=job.lease_expires_at=None
            self.assertTrue(services.job_view(job)['can_resume'])
        with self.app.app_context():services.resume_job(catalog.fixtures.fixtures.CLIENT,catalog.fixtures.fixtures.A,job_id)
        with self.app.app_context(),transaction() as db:
            job=db.get(Job,job_id);self.assertEqual(job.attempts,5);self.assertEqual(job.max_attempts,15)
            self.assertEqual(db.scalar(select(func.count()).select_from(Product).where(Product.job_id==job_id)),1)

    def test_backoff_is_bounded_and_respects_vendor_retry_after(self):
        self.assertEqual(retry_delay(1),10);self.assertEqual(retry_delay(20),300)
        self.assertEqual(retry_delay(1,3600),3600)
