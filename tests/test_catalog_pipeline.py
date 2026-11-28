from collections import Counter
from datetime import timedelta
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select, func, event
import test_commerce as fixtures
from app.commerce import services
from app.database import transaction
from commerce_hub.connector_contracts import SourceRecord
from commerce_hub.models import Job, Product, VendorConnection, CatalogHead, CatalogWorkItem, CatalogPublication, CatalogPublicationItem, VendorCatalogLease, now
from commerce_hub.catalog_pipeline import CatalogRunner
from commerce_hub.vendors.shiloh_tack.catalog_client import CatalogError, CatalogRetryable
from commerce_hub.worker import LeaseLost


class FakeClient:
    def __init__(self, products):
        self.products=products
        self.fetched=[]
        self.fail=None

    def __enter__(self):return self
    def __exit__(self,*_args):pass

    def listing(self,page):
        return {'page':1,'paths':['/item-'+key for key in self.products], 'next_page':None,'last_page_hint':1}

    def product(self,path):
        key=path.removeprefix('/item-')
        self.fetched.append(key)
        if key==self.fail:raise CatalogRetryable('fixture_transient_failure',1)
        return SourceRecord(key,None,self.products[key],'https://www.shilohtack.com'+path)


class FakeAdapter:
    implemented=extraction_implemented=catalog_pipeline=True
    version='fixture-catalog-v1'
    def __init__(self,client):self.client=client
    def open_catalog(self,*_args):return self.client


class CatalogPipelineTests(unittest.TestCase):
    csrf=fixtures.CommerceTests.csrf
    browser=fixtures.CommerceTests.browser
    configure=fixtures.CommerceTests.configure
    enqueue=fixtures.CommerceTests.enqueue
    url=fixtures.CommerceTests.url

    def setUp(self):
        fixtures.CommerceTests.setUp(self)
        self.configure()
        with self.app.app_context(),transaction() as db:
            db.get(VendorConnection,self.catalog).config={'catalog_pipeline_enabled':True}

    def record(self,name,category='Outerwear'):
        return {'name':name,'category_path':'Home > '+category,'category_levels':[category],
                'images':[],'tabs':[],'dropdowns':[]}

    def enqueue_scope(self,mode='all'):
        with self.app.app_context():
            return services.enqueue_connection(fixtures.fixtures.CLIENT,fixtures.fixtures.A,self.catalog,'catalog',
                catalog_mode=mode,category_id='440' if mode=='category' else '')

    def execute(self,products,mode='all'):
        job=self.enqueue_scope(mode)
        client=FakeClient(products)
        with patch('commerce_hub.worker.connector',return_value=FakeAdapter(client)):
            self.worker.run_once(job_id=job)
        return job,client

    def current(self):
        with self.app.app_context():
            return services.results(fixtures.fixtures.CLIENT,fixtures.fixtures.A,'catalog')

    def test_full_refresh_publishes_atomically_and_retains_history(self):
        first,_=self.execute({'1':self.record('First'),'2':self.record('Second')})
        second,_=self.execute({'2':self.record('Second changed'),'3':self.record('Third')})
        self.assertEqual({r['key'] for r in self.current()['rows']},{'2','3'})
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Product)),4)
            summary=db.get(CatalogPublication,second).summary
            self.assertEqual((summary['new_products'],summary['changed_products'],summary['removed_products']),(1,1,1))
        with self.app.app_context():
            old=services.results(fixtures.fixtures.CLIENT,fixtures.fixtures.A,'catalog',run_id=first)
        self.assertEqual({r['key'] for r in old['rows']},{'1','2'})
        page=self.client.get(self.url()+'?tab=jobs')
        self.assertEqual(page.status_code,200)
        self.assertIn(b'>View</a>',page.data)
        history=self.client.get(self.url('/results/catalog')+'?run_id='+first)
        self.assertEqual(history.status_code,200)
        self.assertIn(b'Product ID',history.data)
        detail=self.client.get(self.url('/jobs/'+first+'/view'))
        self.assertEqual(detail.status_code,200)
        self.assertIn(b'Products captured',detail.data)
        self.assertEqual(self.client.get(self.url('/results/catalog')+'?run_id=invalid').status_code,404)

    def test_category_refresh_preserves_other_products_and_removes_only_membership(self):
        self.execute({'1':self.record('Coat'),'2':self.record('Tack','Tack')})
        self.execute({'1':self.record('Updated coat'),'3':self.record('New coat')},'category')
        final,_=self.execute({'3':self.record('New coat')},'category')
        self.assertEqual({r['key'] for r in self.current()['rows']},{'1','2','3'})
        with self.app.app_context(),transaction() as db:
            old=db.get(CatalogPublicationItem,(final,'1'))
            self.assertNotIn('440',old.category_ids)
            self.assertEqual(db.get(CatalogPublication,final).summary['removed_products'],0)
        self.execute({'2':self.record('Tack','Tack'),'3':self.record('New coat')})
        self.assertEqual({r['key'] for r in self.current()['rows']},{'2','3'})

    def test_failed_run_keeps_previous_head_and_resume_skips_committed_products(self):
        original,_=self.execute({'9':self.record('Existing')})
        job=self.enqueue_scope()
        client=FakeClient({str(i):self.record(str(i)) for i in (1,2,3)})
        client.fail='3'
        with patch('commerce_hub.worker.connector',return_value=FakeAdapter(client)):
            self.worker.run_once(job_id=job)
        self.assertEqual(self.current()['source_job_id'],original)
        with self.app.app_context(),transaction() as db:
            done=set(db.scalars(select(Product.source_key).where(Product.job_id==job)))
            self.assertTrue(done)
            row=db.get(Job,job);row.available_at=now()-timedelta(seconds=1)
            db.get(VendorCatalogLease,self.vendor_id).throttle_until=None
        before=Counter(client.fetched);client.fail=None
        with patch('commerce_hub.worker.connector',return_value=FakeAdapter(client)):
            self.worker.run_once(job_id=job)
        for key in done:self.assertEqual(Counter(client.fetched)[key],before[key])
        self.assertEqual(self.current()['source_job_id'],job)
        self.assertEqual(self.current()['total'],3)

    def test_empty_search_never_replaces_published_data(self):
        original,_=self.execute({'1':self.record('Existing')})
        failed,_=self.execute({})
        self.assertEqual(self.current()['source_job_id'],original)
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.get(Job,failed).status,'blocked')
            self.assertIsNone(db.get(CatalogPublication,failed))

    def test_cancel_before_commit_cannot_publish_or_save_product(self):
        job=self.enqueue_scope()
        commit=CatalogRunner.commit_product
        def cancel(runner,item,record,count):
            with self.app.app_context(),transaction() as db:
                row=db.get(Job,job);row.status='cancelled';row.lease_token=row.lease_expires_at=None
            commit(runner,item,record,count)
        with patch('commerce_hub.worker.connector',return_value=FakeAdapter(FakeClient({'1':self.record('Coat')}))),patch.object(CatalogRunner,'commit_product',cancel):
            self.worker.run_once(job_id=job)
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.get(Job,job).status,'cancelled')
            self.assertEqual(db.scalar(select(func.count()).select_from(Product)),0)
            self.assertEqual(db.scalar(select(func.count()).select_from(CatalogPublication)),0)

    def test_vendor_job_lock_is_shared_across_clients(self):
        first=self.enqueue_scope()
        self.configure()  # Invalidates queued credentials; fix first job to current version below.
        with self.app.app_context(),transaction() as db:
            conn=db.scalar(select(VendorConnection).where(VendorConnection.client_id==fixtures.fixtures.B,VendorConnection.connection_type=='catalog'))
            other=conn.id
        with self.app.app_context():
            services.save_connection(fixtures.fixtures.ADMIN,fixtures.fixtures.B,other,'account',password='fixture-password',url='https://vendor.example.com/login')
        with self.app.app_context(),transaction() as db:
            conn=db.get(VendorConnection,other);conn.config={'catalog_pipeline_enabled':True}
            db.get(Job,first).credential_version=db.get(VendorConnection,self.catalog).credential_version
        with self.app.app_context():
            second=services.enqueue_connection(fixtures.fixtures.ADMIN,fixtures.fixtures.B,other,'catalog',catalog_mode='all')
        claimed=self.worker.claim(first)
        self.assertIsNotNone(claimed)
        self.assertIsNone(self.worker.claim(second))

    def test_manifest_and_completion_marker_commit_together(self):
        job,_=self.execute({'001':self.record('Leading zero')})
        with self.app.app_context(),transaction() as db:
            item=db.get(CatalogWorkItem,(job,1))
            self.assertIsNotNone(item.completed_at)
            self.assertEqual(db.get(Product,item.product_id).source_key,'001')
            self.assertEqual(db.get(CatalogHead,(fixtures.fixtures.A,self.vendor_id)).publication_id,job)

    def test_publication_failure_rolls_back_head_and_history_then_resumes_without_refetch(self):
        original,_=self.execute({'9':self.record('Existing')})
        job=self.enqueue_scope()
        client=FakeClient({'1':self.record('New')})
        def fail(_mapper,_connection,_target):raise RuntimeError('fixture publication failure')
        event.listen(CatalogHead,'before_update',fail)
        try:
            with patch('commerce_hub.worker.connector',return_value=FakeAdapter(client)):
                self.worker.run_once(job_id=job)
        finally:
            event.remove(CatalogHead,'before_update',fail)
        self.assertEqual(self.current()['source_job_id'],original)
        with self.app.app_context(),transaction() as db:
            self.assertIsNone(db.get(CatalogPublication,job))
            db.get(Job,job).status='failed'
        with self.app.app_context():
            services.resume_job(fixtures.fixtures.CLIENT,fixtures.fixtures.A,job)
        before=list(client.fetched)
        with patch('commerce_hub.worker.connector',return_value=FakeAdapter(client)):
            self.worker.run_once(job_id=job)
        self.assertEqual(client.fetched,before)
        self.assertEqual(self.current()['source_job_id'],job)

    def test_changed_listing_blocks_publication_and_requires_new_run(self):
        original,_=self.execute({'9':self.record('Existing')})
        job=self.enqueue_scope()
        client=FakeClient({'1':self.record('One'),'2':self.record('Two')})
        listing=client.listing
        calls=[]
        def changed(page):
            calls.append(page)
            result=listing(page)
            if len(calls)>1:result['paths'].reverse()
            return result
        client.listing=changed
        with patch('commerce_hub.worker.connector',return_value=FakeAdapter(client)):
            self.worker.run_once(job_id=job)
        self.assertEqual(self.current()['source_job_id'],original)
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.get(Job,job).error_code,'catalog_source_changed_start_new_run')
            self.assertFalse(services.job_view(db.get(Job,job))['can_resume'])

    def test_batch_break_preserves_progress_and_finishes_remaining_product(self):
        with patch.object(CatalogRunner,'batch_size',2),patch.object(CatalogRunner,'pause_seconds',.001):
            job,_=self.execute({str(i):self.record(str(i)) for i in range(3)})
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.get(Job,job).status,'succeeded')
            self.assertEqual(db.get(Job,job).result['catalog']['next_pause_after'],4)
            self.assertNotIn('pause_until',db.get(Job,job).result['catalog'])

    def test_history_and_resume_enforce_client_boundaries(self):
        job,_=self.execute({'1':self.record('One')})
        with self.app.app_context(),self.assertRaises(services.Forbidden):
            services.results(fixtures.fixtures.OTHER,fixtures.fixtures.A,'catalog',run_id=job)
        with self.app.app_context(),self.assertRaises(services.NotFound):
            services.results(fixtures.fixtures.OTHER,fixtures.fixtures.B,'catalog',run_id=job)
        response=self.client.post(self.url('/jobs/'+job+'/resume'),data={})
        self.assertEqual(response.status_code,400)

    def test_polling_advertises_resume_only_to_owner_or_admin(self):
        job=self.enqueue_scope()
        with self.app.app_context(),transaction() as db:
            row=db.get(Job,job)
            row.status='blocked'
            row.result={'catalog':{'phase':'capturing','total':1}}
        with self.app.app_context():
            self.assertFalse(services.job_status(fixtures.fixtures.REGULAR,fixtures.fixtures.A,job)['can_resume'])
            self.assertTrue(services.job_status(fixtures.fixtures.CLIENT,fixtures.fixtures.A,job)['can_resume'])
        with self.app.app_context(),transaction() as db:
            db.get(Job,job).requested_by=self.regular.id
        with self.app.app_context():
            self.assertTrue(services.job_status(fixtures.fixtures.REGULAR,fixtures.fixtures.A,job)['can_resume'])
