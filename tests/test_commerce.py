import copy
from datetime import timedelta
from io import BytesIO
import json
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import test_administration as fixtures
from app import services as portal
from app.commerce import services
from app.database import transaction
from commerce_hub.connector_contracts import SourceRecord
from commerce_hub.export_files import csv_file, headers, xlsx_file
from commerce_hub.export_mapping import MappingError, map_row, match_skus
from commerce_hub.models import CommerceBase, CommercialApp, Export, Inventory, Job, Product, Vendor, VendorConnection, now
from commerce_hub.worker import LeaseLost, Worker
from commerce_hub.connector_contracts import ConnectorLoginError


class CommerceTests(unittest.TestCase):
    csrf=fixtures.AdministrationTests.csrf
    browser=fixtures.AdministrationTests.browser

    def setUp(self):
        fixtures.AdministrationTests.setUp(self)
        self.engine=self.engine.execution_options(schema_translate_map={'codebi':None,'commerce':None})
        self.app.extensions['portal_engine']=self.engine
        self.app.extensions['portal_sessionmaker']=sessionmaker(self.engine,expire_on_commit=False)
        with self.engine.connect() as conn:
            conn.exec_driver_sql('PRAGMA foreign_keys=ON')
        CommerceBase.metadata.create_all(self.engine)
        self.app.config['COMMERCE_HUB_ENABLED']=True
        self.secret_calls=[]
        def writer(db,row,password):
            self.secret_calls.append(password)
            row.credential_ref=str(uuid4())
            row.credential_version+=1
            row.status='unverified'
        self.app.extensions['commerce_test_secret_writer']=writer
        with self.app.app_context(),transaction() as db:
            db.add(CommercialApp(code='commerce_hub',name='Commerce Hub'))
            vendor=Vendor(code='shiloh_tack',name='Shiloh Tack')
            db.add(vendor)
            db.flush()
            self.vendor_id=vendor.id
        with self.app.app_context():
            services.set_access(fixtures.ADMIN,fixtures.A,True,list(services.FEATURES))
            services.set_access(fixtures.ADMIN,fixtures.B,True,list(services.FEATURES))
            with transaction() as db:
                self.catalog=db.scalar(select(VendorConnection.id).where(VendorConnection.client_id==fixtures.A,VendorConnection.connection_type=='catalog'))
                self.inventory=db.scalar(select(VendorConnection.id).where(VendorConnection.client_id==fixtures.A,VendorConnection.connection_type=='inventory'))
        self.worker=Worker(self.engine,secret_reader=Mock(return_value='fixture-password'),uploader=Mock())

    def url(self,suffix='',client=fixtures.A):
        return f'/portal/clients/{client}/commerce'+suffix

    def configure(self,connection_id=None):
        with self.app.app_context():
            services.save_connection(fixtures.ADMIN,fixtures.A,connection_id or self.catalog,'account',password='fixture-password',url='https://vendor.example.com/login')

    def enqueue(self,kind='catalog'):
        with self.app.app_context():
            return services.enqueue_connection(fixtures.CLIENT,fixtures.A,self.inventory if kind=='inventory' else self.catalog,kind)

    def test_feature_flag_keeps_existing_portal_independent(self):
        self.app.config['COMMERCE_HUB_ENABLED']=False
        self.assertEqual(self.client.get('/portal/commerce').status_code,404)
        page=self.client.get('/portal/')
        self.assertEqual(page.status_code,200)
        self.assertNotIn(b'>Commerce Hub</span>',page.data)

    def test_pages_render_for_authorized_roles(self):
        for path in ('/portal/commerce','/portal/commerce/configuration',self.url(),self.url('/results/catalog'),self.url('/results/inventory'),self.url('/connections/'+self.catalog)):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code,200)
        self.client=self.browser(fixtures.REGULAR)
        self.assertEqual(self.client.get(self.url()).status_code,200)
        self.assertEqual(self.client.get(self.url('/connections/'+self.catalog)).status_code,403)
        self.assertEqual(self.client.get('/portal/commerce/configuration').status_code,403)

    def test_inventory_login_verification_marks_only_connection_verified(self):
        self.configure(self.inventory)
        with self.app.app_context():job_id=services.enqueue_connection(fixtures.CLIENT,fixtures.A,self.inventory,'test_connection')
        adapter=Mock(implemented=True,extraction_implemented=False)
        with patch('commerce_hub.worker.connector',return_value=adapter):self.worker.run_once(job_id=job_id)
        adapter.verify.assert_called_once();adapter.extract.assert_not_called()
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.get(VendorConnection,self.inventory).status,'verified')
            self.assertEqual(db.get(Job,job_id).status,'succeeded')
            self.assertEqual(db.scalar(select(func.count()).select_from(Inventory)),0)
        from commerce_hub.connector_registry import connector
        self.assertTrue(connector('shiloh_tack','inventory').implemented)
        self.assertTrue(connector('shiloh_tack','inventory').extraction_implemented)

    def test_first_page_inventory_sample_is_saved_without_publishing_inventory(self):
        self.configure(self.inventory)
        sample={'page':1,'page_limit':1,'complete_inventory':False,'pagination_followed':False,'row_count':1,
                'columns':[{'label':'Inventory ID','key':'inventory_id'}],
                'rows':[{'inventory_id':'002300','source_values':{'Inventory ID':'002300'}}],
                'pagination_controls':[],'quantity_meaning':'Qty is order entry; This Warehouse is stock.'}
        with self.app.app_context():
            job_id=services.enqueue_connection(fixtures.CLIENT,fixtures.A,self.inventory,'inventory',inventory_mode='first_page')
        adapter=Mock(implemented=True,preview_implemented=True,extraction_implemented=False,version='fixture')
        adapter.preview_first_page.return_value=sample
        with patch('commerce_hub.worker.connector',return_value=adapter):self.worker.run_once(job_id=job_id)
        adapter.extract.assert_not_called();adapter.preview_first_page.assert_called_once()
        with self.app.app_context(),transaction() as db:
            job=db.get(Job,job_id)
            self.assertEqual(job.status,'succeeded')
            self.assertEqual(job.result['inventory_preview']['rows'][0]['inventory_id'],'002300')
            self.assertEqual(db.scalar(select(func.count()).select_from(Inventory)),0)
        with self.app.app_context():
            self.assertEqual(services.results(fixtures.CLIENT,fixtures.A,'inventory')['total'],0)
            status=services.job_status(fixtures.CLIENT,fixtures.A,job_id)
        self.assertEqual(status['job_label'],'Inventory sample (page 1)')
        self.assertTrue(status['preview_available'])
        response=self.client.get(self.url('/jobs/'+job_id+'/inventory-preview'))
        self.assertEqual(response.status_code,200)
        self.assertIn(b'002300',response.data)
        self.assertIn(b'First page only',response.data)
        self.assertIn(b'View sample',self.client.get(self.url()+'?tab=jobs').data)
        self.assertEqual(self.browser(fixtures.OTHER).get(self.url('/jobs/'+job_id+'/inventory-preview')).status_code,403)

    def test_inventory_preview_mode_is_explicit_and_not_allowed_on_catalog(self):
        self.configure(self.inventory);self.configure()
        with self.app.app_context(),self.assertRaises(services.InvalidDetails):
            services.enqueue_connection(fixtures.CLIENT,fixtures.A,self.inventory,'inventory',inventory_mode='all')
        with self.app.app_context(),self.assertRaises(services.InvalidDetails):
            services.enqueue_connection(fixtures.CLIENT,fixtures.A,self.catalog,'catalog',inventory_mode='first_page')

    def test_registry_table_lists_only_enabled_clients_and_has_access_dialog(self):
        with self.app.app_context():
            services.set_access(fixtures.ADMIN,fixtures.B,False,[])
        page=self.client.get('/portal/commerce')
        self.assertEqual(page.status_code,200)
        table=page.data.split(b'<tbody>',1)[1].split(b'</tbody>',1)[0]
        self.assertIn(b'Example Health',table)
        self.assertNotIn(b'Other Company',table)
        self.assertNotIn(b'type="checkbox"',table)
        self.assertIn(b'data-commerce-settings',table)
        self.assertIn(b'data-commerce-client-row',table)
        self.assertIn(b'aria-label="Open Commerce Hub for Example Health"',table)
        self.assertIn(b'Shiloh inventory',table)
        for label in (b'>Connections<',b'>Vendor export<',b'>Shopify export<'):
            self.assertNotIn(label,table)
        self.assertIn(b'<th class="commerce-row-actions">Actions</th>',page.data)
        self.assertNotIn(b'type="submit">Search</button>',page.data)
        self.assertIn(b'<span>Commercial tools</span>',page.data)
        self.assertIn(b'class="portal-subnav portal-subnav-with-icon active"',page.data)
        self.assertIn(b'id="commerce-access-dialog"',page.data)
        self.assertIn(b'>Other Company</option>',page.data)
        self.assertIn(b'data-commerce-add',page.data)

    def test_registry_combines_client_search_and_access_filter(self):
        with self.app.app_context():
            services.set_access(fixtures.ADMIN,fixtures.A,True,['catalog'])
            services.set_access(fixtures.ADMIN,fixtures.B,True,['inventory'])
        for query, present, absent in [
            ('?feature=inventory',b'Other Company',b'Example Health'),
            ('?q=EXAMPLE',b'Example Health',b'Other Company'),
            ('?feature=catalog&q=example',b'Example Health',b'Other Company')]:
            with self.subTest(query=query):
                page=self.client.get('/portal/commerce'+query)
                self.assertEqual(page.status_code,200)
                table=page.data.split(b'<tbody>',1)[1].split(b'</tbody>',1)[0]
                self.assertIn(present,table)
                self.assertNotIn(absent,table)
        page=self.client.get('/portal/commerce?feature=inventory&q=example')
        self.assertIn(b'No clients match your search.',page.data)
        self.assertEqual(self.client.get('/portal/commerce?feature=unknown').status_code,200)

    def test_add_update_and_disable_access_through_admin_form(self):
        with self.app.app_context():
            services.set_access(fixtures.ADMIN,fixtures.B,False,[])
        url='/portal/commerce/access/'+fixtures.B
        token=self.csrf()
        response=self.client.post(url,data={'csrf_token':token,'enabled':'on','features':['inventory']})
        self.assertEqual(response.status_code,302)
        table=self.client.get('/portal/commerce?feature=inventory').data.split(b'<tbody>',1)[1].split(b'</tbody>',1)[0]
        self.assertIn(b'Other Company',table)
        response=self.client.post(url,data={'csrf_token':token,'enabled':'on','features':['catalog']})
        self.assertEqual(response.status_code,302)
        table=self.client.get('/portal/commerce?feature=inventory').data.split(b'<tbody>',1)[1].split(b'</tbody>',1)[0]
        self.assertNotIn(b'Other Company',table)
        self.assertEqual(self.client.post(url,data={'csrf_token':token}).status_code,302)
        table=self.client.get('/portal/commerce').data.split(b'<tbody>',1)[1].split(b'</tbody>',1)[0]
        self.assertNotIn(b'Other Company',table)
        browser=self.browser(fixtures.CLIENT)
        browser.get('/auth/login')
        with browser.session_transaction() as state:
            token=state['csrf']
        self.assertEqual(browser.post('/portal/commerce/access/'+fixtures.A,
            data={'csrf_token':token,'enabled':'on','features':['catalog']}).status_code,403)

    def test_client_scope_and_revoked_entitlement(self):
        browser=self.browser(fixtures.CLIENT)
        self.assertEqual(browser.get(self.url(client=fixtures.B)).status_code,403)
        with self.app.app_context():
            services.set_access(fixtures.ADMIN,fixtures.A,False,[])
        self.assertEqual(browser.get(self.url()).status_code,403)
        self.assertEqual(browser.get('/portal/').status_code,200)

    def test_passwords_and_references_never_returned(self):
        self.configure()
        page=self.client.get(self.url('/connections/'+self.catalog))
        self.assertNotIn(b'fixture-password',page.data)
        self.assertIn(b'value=""',page.data)
        with self.app.app_context(),transaction() as db:
            row=db.get(VendorConnection,self.catalog)
            self.assertNotIn(row.credential_ref.encode(),page.data)
            self.assertNotIn('password',row.config)
        self.assertEqual(self.secret_calls,['fixture-password'])

    def test_connection_view_mask_and_blank_password_preserve_existing_secret(self):
        self.configure()
        with self.app.app_context(),transaction() as db:
            row=db.get(VendorConnection,self.catalog)
            reference,version=row.credential_ref,row.credential_version
        page=self.client.get(self.url('/connections/'+self.catalog))
        self.assertIn(b'data-editing="false"',page.data)
        self.assertIn(b'placeholder="*****"',page.data)
        response=self.client.post(self.url('/connections/'+self.catalog),data={
            'csrf_token':self.csrf(),'portal_url':'https://vendor.example.com/login',
            'username':'account','password':''})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.secret_calls,['fixture-password'])
        with self.app.app_context(),transaction() as db:
            row=db.get(VendorConnection,self.catalog)
            self.assertEqual((row.credential_ref,row.credential_version),(reference,version))

    def test_connection_validation_retains_edit_mode_without_echoing_password(self):
        self.configure()
        response=self.client.post(self.url('/connections/'+self.catalog),data={
            'csrf_token':self.csrf(),'portal_url':'http://invalid.example.com/',
            'username':'account','password':'replacement-not-to-be-echoed'})
        self.assertEqual(response.status_code,422)
        self.assertIn(b'data-editing="true"',response.data)
        self.assertNotIn(b'replacement-not-to-be-echoed',response.data)
        self.assertEqual(self.secret_calls,['fixture-password'])

    def test_client_admin_cannot_change_url_or_other_clients_credentials(self):
        with self.app.app_context():
            with self.assertRaises(portal.Forbidden):
                services.save_connection(fixtures.CLIENT,fixtures.A,self.catalog,'account',url='https://another.example.com/')
            with self.assertRaises(portal.Forbidden):
                services.save_connection(fixtures.OTHER,fixtures.A,self.catalog,'account',password='not-saved')
        self.assertEqual(self.secret_calls,[])

    def test_url_validation_and_missing_configuration(self):
        for url in ('http://vendor.example.com','https://user:password@vendor.example.com/','https://127.0.0.1','https://localhost/','https://vendor.example.com/?token=secret'):
            with self.subTest(url=url),self.assertRaises(portal.InvalidDetails):
                services.portal_url(url)
        with self.app.app_context(),self.assertRaises(portal.InvalidDetails):
            services.enqueue_connection(fixtures.CLIENT,fixtures.A,self.catalog,'catalog')

    def test_placeholder_worker_does_not_get_secrets_or_access_vendor(self):
        self.configure()
        job_id=self.enqueue()
        self.assertTrue(self.worker.run_once())  # No Flask app/request context.
        self.worker.secret_reader.assert_not_called()
        self.worker.uploader.assert_not_called()
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.get(Job,job_id).status,'blocked')
            self.assertEqual(db.get(Job,job_id).error_code,'connector_not_implemented')
            self.assertNotEqual(db.get(VendorConnection,self.catalog).status,'verified')
            self.assertEqual(db.scalar(select(func.count()).select_from(Product)),0)

    def test_targeted_login_job_confirms_connection_without_extracting(self):
        self.configure()
        extraction_id=self.enqueue()
        with self.app.app_context(),transaction() as db:
            job=db.get(Job,extraction_id)
            job.status='blocked'
        with self.app.app_context():
            test_id=services.enqueue_connection(fixtures.ADMIN,fixtures.A,self.catalog,'test_connection')
        sample={'connection_type':'catalog','page_limit':1,'pagination_followed':False,'row_count':1,'rows':[{'product_id':'A'}]}
        with patch('commerce_hub.vendors.shiloh_tack.catalog.ShilohCatalogConnector.test_sample',return_value=sample) as verify:
            self.assertTrue(self.worker.run_once(job_id=test_id))
        verify.assert_called_once()
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.get(Job,test_id).status,'succeeded')
            self.assertEqual(db.get(VendorConnection,self.catalog).status,'verified')
            self.assertEqual(db.get(Job,extraction_id).status,'blocked')
            self.assertEqual(db.scalar(select(func.count()).select_from(Product)),0)

    def test_rejected_login_clears_prior_verification_without_password_retries(self):
        self.configure()
        with self.app.app_context(),transaction() as db:
            row=db.get(VendorConnection,self.catalog)
            row.status,row.last_verified_at='verified',now()
        with self.app.app_context():
            test_id=services.enqueue_connection(fixtures.ADMIN,fixtures.A,self.catalog,'test_connection')
        with patch('commerce_hub.vendors.shiloh_tack.catalog.ShilohCatalogConnector.test_sample',side_effect=ConnectorLoginError('vendor_login_rejected')) as verify:
            self.worker.run_once(job_id=test_id)
        verify.assert_called_once()
        with self.app.app_context(),transaction() as db:
            job,row=db.get(Job,test_id),db.get(VendorConnection,self.catalog)
            self.assertEqual((job.status,job.error_code,job.attempts),('blocked','vendor_login_rejected',1))
            self.assertEqual(row.status,'error')
            self.assertIsNone(row.last_verified_at)
        self.assertFalse(self.worker.run_once(job_id=test_id))

    def test_job_payload_and_status_never_contain_credentials(self):
        self.configure()
        job_id=self.enqueue()
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.get(Job,job_id).payload,{})
        page=self.client.get(self.url('/jobs/'+job_id))
        self.assertEqual(page.status_code,200)
        self.assertNotIn(b'fixture-password',page.data)
        self.assertNotIn(b'credential_ref',page.data)
        self.assertEqual(self.browser(fixtures.OTHER).get(self.url('/jobs/'+job_id)).status_code,403)

    def test_catalog_scope_controls_and_real_vendor_categories_render(self):
        page=self.client.get(self.url())
        self.assertEqual(page.status_code,200)
        self.assertIn(b'data-catalog-selection',page.data)
        self.assertIn(b'All products',page.data)
        self.assertIn(b'Selected category',page.data)
        self.assertIn(b'value="440"',page.data)
        self.assertIn(b'Outerwear',page.data)
        self.assertIn(b'commerce-catalog-selection.js',page.data)

    def test_category_selection_is_saved_and_forwarded_to_connector(self):
        self.configure()
        with self.app.app_context():
            job_id=services.enqueue_connection(fixtures.CLIENT,fixtures.A,self.catalog,'catalog',
                catalog_mode='category',category_id='440')
        adapter=Mock(implemented=True,version='fixture-v1')
        adapter.extract.return_value=[SourceRecord('one','001',{'name':'Outerwear example'})]
        with patch('commerce_hub.worker.connector',return_value=adapter):
            self.worker.run_once(job_id=job_id)
        expected={'mode':'category','category_id':'440','category_label':'\u00bb Outerwear'}
        self.assertEqual(adapter.extract.call_args.kwargs['selection'],expected)
        with self.app.app_context(),transaction() as db:
            job=db.get(Job,job_id)
            self.assertEqual(job.payload['catalog_selection'],expected)
            self.assertEqual(job.result['catalog_selection'],expected)
            self.assertEqual(job.status,'succeeded')
        self.assertIn(b'Outerwear',self.client.get(self.url()+'?tab=jobs').data)

    def test_invalid_category_is_rejected_without_queuing_a_job(self):
        self.configure()
        with self.app.app_context(),self.assertRaises(portal.InvalidDetails):
            services.enqueue_connection(fixtures.CLIENT,fixtures.A,self.catalog,'catalog',
                catalog_mode='category',category_id='not-a-category')
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Job)),0)

    def test_worker_rejects_corrupt_category_before_reading_secret(self):
        self.configure()
        job_id=self.enqueue()
        with self.app.app_context(),transaction() as db:
            db.get(Job,job_id).payload={'catalog_selection':{'mode':'category','category_id':'unknown'}}
        self.worker.run_once(job_id=job_id)
        self.worker.secret_reader.assert_not_called()
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.get(Job,job_id).error_code,'invalid_catalog_selection')

    def test_job_deduplication_and_csrf(self):
        self.configure()
        self.enqueue()
        with self.app.app_context(),self.assertRaises(portal.InvalidDetails):
            services.enqueue_connection(fixtures.CLIENT,fixtures.A,self.catalog,'catalog')
        self.assertEqual(self.client.post(self.url('/connections/'+self.catalog),data={'username':'changed'}).status_code,400)
        self.assertEqual(self.client.post('/portal/commerce/access/'+fixtures.A,data={'enabled':'on'}).status_code,400)

    def test_lease_recovery_fences_old_worker(self):
        self.configure()
        job_id=self.enqueue()
        old=self.worker.claim()
        with self.app.app_context(),transaction() as db:
            db.get(Job,job_id).lease_expires_at=now()-timedelta(seconds=1)
        self.assertIsNone(self.worker.claim())
        with self.app.app_context(),transaction() as db:
            db.get(Job,job_id).available_at=now()-timedelta(seconds=1)
        new=self.worker.claim()
        self.assertNotEqual(old.lease_token,new.lease_token)
        with self.assertRaises(LeaseLost):
            self.worker.heartbeat(old)
        self.worker.finish_error(old,'stale-worker')
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.get(Job,job_id).lease_token,new.lease_token)

    def test_revoked_access_or_replaced_password_cancels_work(self):
        self.configure()
        job_id=self.enqueue()
        claimed=self.worker.claim()
        self.configure()  # Credential version changes, invalidating the old job.
        with self.assertRaises(LeaseLost):
            self.worker.heartbeat(claimed)
        self.worker.finish_error(claimed,'changed')
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.get(Job,job_id).status,'cancelled')

    def test_chatty_progress_does_not_renew_lease_per_product(self):
        self.configure()
        job_id=self.enqueue()
        adapter=Mock(implemented=True,version='fixture-v1')
        def extract(_url,_credentials,progress):
            for value in range(100):
                progress(value)
            return [SourceRecord('one','001',{'name':'Saddle'})]
        adapter.extract.side_effect=extract
        with patch('commerce_hub.worker.connector',return_value=adapter), patch.object(self.worker,'_renew_lease') as renew:
            self.worker.run_once(job_id=job_id)
        renew.assert_not_called()
        self.assertIsNone(self.worker._job_heartbeat)
        with self.app.app_context(),transaction() as db:
            self.assertEqual((db.get(Job,job_id).status,db.get(Job,job_id).progress),('succeeded',100))

    def test_delayed_renewal_cannot_move_progress_backwards(self):
        self.configure()
        job_id=self.enqueue()
        claimed=self.worker.claim(job_id)
        self.worker.heartbeat(claimed,80)
        self.worker.heartbeat(claimed,20)
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.get(Job,job_id).progress,80)

    def test_cancellation_during_login_cannot_mark_connection_verified(self):
        self.configure()
        with self.app.app_context():
            job_id=services.enqueue_connection(fixtures.ADMIN,fixtures.A,self.catalog,'test_connection')
        adapter=Mock(implemented=True)
        def cancel(_url,_credentials):
            with self.app.app_context(),transaction() as db:
                job=db.get(Job,job_id)
                job.status='cancelled'
                job.lease_token=job.lease_expires_at=None
        adapter.verify.side_effect=cancel
        with patch('commerce_hub.worker.connector',return_value=adapter):
            self.worker.run_once(job_id=job_id)
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.get(Job,job_id).status,'cancelled')
            self.assertNotEqual(db.get(VendorConnection,self.catalog).status,'verified')

    def snapshots(self):
        self.configure(self.catalog)
        self.configure(self.inventory)
        adapter=Mock(implemented=True,version='fixture-v1')
        adapter.extract.return_value=[SourceRecord('one','001',{'name':'Saddle','cost':'12.50','quantity':'3'})]
        with patch('commerce_hub.worker.connector',return_value=adapter):
            catalog=self.enqueue('catalog')
            self.worker.run_once()
            inventory=self.enqueue('inventory')
            self.worker.run_once()
        with self.app.app_context(),transaction() as db:
            for key in (self.catalog,self.inventory):
                db.get(VendorConnection,key).config={'sku_grain':'product'}
        return catalog,inventory

    def test_snapshot_export_preserves_original_data(self):
        catalog,inventory=self.snapshots()
        with self.app.app_context():
            source=services.results(fixtures.CLIENT,fixtures.A,'catalog')['rows']
            before=copy.deepcopy(source)
            job_id=services.enqueue_export(fixtures.CLIENT,fixtures.A,'export_vendor',catalog,inventory)
        self.worker.run_once()
        self.worker.uploader.assert_called_once()
        content=self.worker.uploader.call_args.args[2]
        from openpyxl import load_workbook
        workbook=load_workbook(BytesIO(content),read_only=True)
        self.assertEqual(workbook.sheetnames,['Products','Inventory'])
        workbook.close()
        with self.app.app_context():
            self.assertEqual(services.results(fixtures.CLIENT,fixtures.A,'catalog')['rows'],before)
            with transaction() as db:
                artifact=db.scalar(select(Export).where(Export.job_id==job_id))
                self.assertEqual(artifact.catalog_job_id,catalog)
                self.assertEqual(artifact.inventory_job_id,inventory)
                self.assertEqual(artifact.row_count,1)

    def test_template_mapping_and_shopify_export_are_versioned(self):
        catalog,inventory=self.snapshots()
        columns,digest=headers(b'Fixture title,Fixture price\n','fixture.csv')
        columns[0]['required']=True
        rules=[{'target':'Fixture title','source':['product','attributes','name']},
               {'target':'Fixture price','source':['inventory','attributes','cost'],'transform':'multiply','factor':'1.5'}]
        with self.app.app_context():
            template=services.register_template(fixtures.ADMIN,'shopify','fixture-1',columns,digest)
            mapping=services.register_mapping(fixtures.ADMIN,fixtures.A,self.vendor_id,template,1,rules)
            with self.assertRaises(portal.InvalidDetails):
                services.register_mapping(fixtures.ADMIN,fixtures.A,self.vendor_id,template,1,rules)
            job_id=services.enqueue_export(fixtures.CLIENT,fixtures.A,'export_shopify',catalog,inventory,mapping,'csv')
        self.worker.run_once()
        content=self.worker.uploader.call_args.args[2].decode('utf-8-sig')
        self.assertIn('Saddle,18.750',content)
        with self.app.app_context(),transaction() as db:
            artifact=db.scalar(select(Export).where(Export.job_id==job_id))
            self.assertEqual(artifact.mapping_id,mapping)
            self.assertEqual(artifact.template_id,template)

    def test_cross_client_input_and_foreign_key_are_rejected(self):
        catalog,inventory=self.snapshots()
        with self.app.app_context():
            with self.assertRaises(portal.InvalidDetails):
                services.enqueue_export(fixtures.OTHER,fixtures.B,'export_vendor',catalog,inventory)
            with self.assertRaises(IntegrityError),transaction() as db:
                db.add(Product(client_id=fixtures.B,vendor_id=self.vendor_id,job_id=catalog,source_key='cross-client',attributes={}))

    def test_revocation_during_upload_never_publishes_export(self):
        catalog,inventory=self.snapshots()
        def revoke(*args):
            with self.app.app_context():
                services.set_access(fixtures.ADMIN,fixtures.A,False,[])
        self.worker.uploader=revoke
        with self.app.app_context():
            job_id=services.enqueue_export(fixtures.CLIENT,fixtures.A,'export_vendor',catalog,inventory)
        self.worker.run_once()
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.get(Job,job_id).status,'cancelled')
            self.assertEqual(db.scalar(select(func.count()).select_from(Export)),0)

    def test_download_checks_client_and_feature_before_storage(self):
        catalog,inventory=self.snapshots()
        with self.app.app_context():
            services.enqueue_export(fixtures.CLIENT,fixtures.A,'export_vendor',catalog,inventory)
        self.worker.run_once()
        with self.app.app_context(),transaction() as db:
            artifact=db.scalar(select(Export))
            artifact_id=artifact.id
        self.client=self.browser(fixtures.CLIENT)
        with patch('app.commerce.routes.download',return_value=b'file') as transport:
            self.assertEqual(self.client.get(self.url('/exports/'+artifact_id)).status_code,200)
            self.assertEqual(self.browser(fixtures.OTHER).get(self.url('/exports/'+artifact_id)).status_code,403)
            transport.assert_called_once()


class MappingTests(unittest.TestCase):
    def test_only_declared_non_executable_transforms(self):
        columns=[{'name':'Title','required':True}]
        with self.assertRaises(MappingError):
            map_row({}, {},columns,[{'target':'Title','transform':'eval','value':'__import__("os")'}])
        with self.assertRaises(MappingError):
            map_row({}, {},columns,[{'target':'Title','source':['product','missing']}])
        product={'name':' Saddle '}
        self.assertEqual(map_row(product,{},columns,[{'target':'Title','source':['product','name'],'transform':'strip'}]),{'Title':'Saddle'})
        self.assertEqual(product,{'name':' Saddle '})

    def test_exact_sku_matching_reports_ambiguity(self):
        products=[{'source_key':'one','vendor_sku':'001'},{'source_key':'two','vendor_sku':'ABC'}]
        inventory=[{'vendor_sku':'1'},{'vendor_sku':'ABC'},{'vendor_sku':'ABC'}]
        matches,errors=match_skus(products,inventory)
        self.assertEqual(matches,[])
        self.assertEqual([e['code'] for e in errors],['unmatched_sku','ambiguous_sku'])

    def test_formula_safety_and_template_headers(self):
        with self.assertRaises(MappingError):
            csv_file(['name'],[{'name':' =HYPERLINK("bad")'}])
        content=xlsx_file([('Source',['name'],[{'name':'=SUM(1,2)'}])])
        from openpyxl import load_workbook
        book=load_workbook(BytesIO(content))
        self.assertEqual(book.active['A2'].data_type,'s')
        book.close()
        with self.assertRaises(MappingError):
            headers(b'Name,Name\n','template.csv')


if __name__=='__main__':
    unittest.main()
