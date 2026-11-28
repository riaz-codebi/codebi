import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from datetime import timedelta
from sqlalchemy import select,func
import test_commerce as setup
import test_administration as identities
from app.database import transaction
from app.commerce import services
from commerce_hub.models import Inventory,Job,now
from commerce_hub.vendors.shiloh_tack.catalog_client import CatalogRetryable
from commerce_hub.connector_contracts import ConnectorLoginError
from commerce_hub.vendors.shiloh_tack.inventory_download import pages,InventoryCrawlBrowser
from commerce_hub.vendors.shiloh_tack.inventory_preview import AVAILABLE_FILTER
import test_vendor_shiloh_tack_inventory_preview as grid_fixtures


def page(number,ids,last=False,limited=False):
    return {'page_number':number,'rows':[{'inventory_id':key,'description':'Item '+key,'price':'12.5000',
        'unit':'EACH','warehouse_quantity':'2.00'} for key in ids], 'last_page':last,'limited':limited,'available_items_only':True}


class InventoryPipelineTests(unittest.TestCase):
    setUp=setup.CommerceTests.setUp
    configure=setup.CommerceTests.configure
    enqueue=setup.CommerceTests.enqueue
    browser=setup.CommerceTests.browser
    csrf=setup.CommerceTests.csrf
    url=setup.CommerceTests.url

    def run_job(self,sequence,job_id=None):
        if not job_id:
            self.configure(self.inventory);job_id=self.enqueue('inventory')
        adapter=Mock(implemented=True,extraction_implemented=True,inventory_pipeline=True,version='inventory-fixture')
        adapter.pages.side_effect=lambda *args,**kwargs:(value for value in sequence)
        with patch('commerce_hub.worker.connector',return_value=adapter):self.worker.run_once(job_id=job_id)
        return job_id

    def current(self):
        with self.app.app_context():return services.results(identities.CLIENT,identities.A,'inventory')

    def test_complete_run_supersedes_previous_and_retains_history(self):
        old=self.run_job([page(1,['0001','0002'],True)])
        new=self.run_job([page(1,['0002'],True)])
        self.assertEqual(self.current()['total'],1)
        self.assertEqual(self.current()['source_job_id'],new)
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Inventory).where(Inventory.job_id==old)),2)
        response=self.client.get(self.url('/results/inventory'))
        self.assertIn(b'12.5000',response.data);self.assertIn(b'This Warehouse',response.data)

    def test_inventory_search_covers_all_pages_description_and_literal_sku(self):
        first=page(1,[str(i).zfill(4) for i in range(26)])
        second=page(2,['LATE_SKU','LATE0SKU'],True)
        second['rows'][0]['description']='Canyon Blue Bucket'
        self.run_job([first,second])
        with self.app.app_context():
            matched=services.results(identities.CLIENT,identities.A,'inventory',page=99,filters={'q':'cAnYoN'})
            self.assertEqual(matched['total'],1);self.assertEqual(matched['page'],1)
            self.assertEqual(matched['rows'][0]['key'],'LATE_SKU')
            literal=services.results(identities.CLIENT,identities.A,'inventory',filters={'q':'LATE_'})
            self.assertEqual(literal['total'],1)
            self.assertEqual(services.results(identities.CLIENT,identities.A,'inventory',filters={'q':'missing'})['total'],0)
        response=self.client.get(self.url('/results/inventory')+'?q=Canyon')
        self.assertIn(b'LATE_SKU',response.data)
        self.assertNotIn(b'LATE0SKU',response.data)
        self.assertIn(b'SKU or description',response.data)
        self.assertNotIn(b'>Search</button>',response.data)
        self.assertIn(b'Only available items from Shiloh inventory were downloaded.',response.data)
        self.assertIn(b'$12.5000',response.data)
        self.assertNotIn(b'id="product-category"',response.data)

    def test_combined_connection_samples_do_not_replace_published_inventory(self):
        current=self.run_job([page(1,['CURRENT'],True)])
        for kind,connection_id in [('catalog',self.catalog),('inventory',self.inventory)]:
            self.configure(connection_id)
            sample={'connection_type':kind,'page_limit':1,'pagination_followed':False,'row_count':1,
                'rows':[{'source_values':{'Item':'SAMPLE'}}],'columns':[{'label':'Item'}]}
            with self.app.app_context():job_id=services.enqueue_connection(identities.ADMIN,identities.A,connection_id,'test_connection')
            adapter=Mock(implemented=True,test_sample_implemented=True,version='sample-fixture')
            adapter.test_sample.return_value=sample
            with patch('commerce_hub.worker.connector',return_value=adapter):self.worker.run_once(job_id=job_id)
            adapter.test_sample.assert_called_once();adapter.verify.assert_not_called();adapter.extract.assert_not_called()
            self.assertEqual(self.current()['source_job_id'],current)
            with self.app.app_context(),transaction() as db:
                self.assertEqual(db.get(Job,job_id).status,'succeeded')
                self.assertEqual(db.get(Job,job_id).result['connection_sample'],sample)
                self.assertEqual(db.scalar(select(func.count()).select_from(Inventory)),1)
            response=self.client.get(self.url('/jobs/'+job_id+'/sample'))
            self.assertEqual(response.status_code,200);self.assertIn(b'SAMPLE',response.data)
            self.assertIn(b'current catalog and inventory were not changed',response.data)
            self.assertEqual(self.browser(identities.OTHER).get(self.url('/jobs/'+job_id+'/sample')).status_code,403)
            self.client=self.browser(identities.ADMIN)

    def test_sample_read_failure_does_not_confirm_connection_or_change_inventory(self):
        current=self.run_job([page(1,['CURRENT'],True)])
        with self.app.app_context():job_id=services.enqueue_connection(identities.ADMIN,identities.A,self.inventory,'test_connection')
        adapter=Mock(implemented=True,test_sample_implemented=True)
        adapter.test_sample.side_effect=ConnectorLoginError('inventory_grid_structure_changed')
        with patch('commerce_hub.worker.connector',return_value=adapter):self.worker.run_once(job_id=job_id)
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.get(Job,job_id).status,'blocked')
            from commerce_hub.models import VendorConnection
            self.assertEqual(db.get(VendorConnection,self.inventory).status,'error')
        self.assertEqual(self.current()['source_job_id'],current)

    @unittest.skipUnless(Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe').is_file(),'Local Edge required for dialog fixture')
    def test_combined_test_dialog_cancel_and_single_confirm_for_both_connections(self):
        from playwright.sync_api import sync_playwright
        self.configure(self.inventory);self.configure(self.catalog)
        html=self.client.get(self.url()+'?tab=connections').data.decode('utf-8')
        self.assertNotIn('Preview first page',html)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                page=browser.new_page();page.route('**/*',lambda route:route.abort());page.set_content(html)
                page.add_script_tag(content=Path('app/static/js/commerce-connection-test.js').read_text(encoding='utf-8'))
                page.evaluate('''() => { window.approvedTests=0; document.querySelectorAll('form[data-test-connection]').forEach(form=>form.addEventListener('submit',event=>{if(!event.defaultPrevented) {window.approvedTests++;event.preventDefault();}})); }''')
                for index,kind in enumerate(('catalog','inventory')):
                    page.locator('form[data-test-connection="'+kind+'"] button').click()
                    self.assertTrue(page.locator('#commerce-connection-test-dialog').is_visible())
                    self.assertEqual(page.locator('[data-test-connection-type]').inner_text(),kind)
                    page.locator('[data-test-cancel]').click();self.assertEqual(page.evaluate('approvedTests'),index)
                    page.locator('form[data-test-connection="'+kind+'"] button').click()
                    page.locator('[data-test-confirm]').click();self.assertEqual(page.evaluate('approvedTests'),index+1)
            finally:browser.close()

    @unittest.skipUnless(Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe').is_file(),'Local Edge required for dialog fixture')
    def test_inventory_confirmation_cancel_and_available_only_note(self):
        from playwright.sync_api import sync_playwright
        self.configure(self.inventory)
        html=self.client.get(self.url()+'?tab=connections').data.decode('utf-8')
        script=Path('app/static/js/commerce-extraction-confirmation.js').read_text(encoding='utf-8')
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                page=browser.new_page()
                page.route('**/*',lambda route:route.abort())
                page.set_content(html)
                page.add_script_tag(content=script)
                page.evaluate('''() => { window.approvedSubmissions=0; document.querySelectorAll('form[data-extraction-kind]').forEach(form=>form.addEventListener('submit',event=>{if(!event.defaultPrevented) {window.approvedSubmissions++;event.preventDefault();}})); }''')
                page.locator('form[data-extraction-kind="inventory"] button').click()
                self.assertTrue(page.locator('#commerce-extraction-dialog').is_visible())
                note=page.locator('[data-extraction-inventory-note]')
                self.assertTrue(note.is_visible());self.assertIn('Only available items',note.inner_text())
                page.locator('[data-extraction-cancel]').click()
                self.assertEqual(page.evaluate('approvedSubmissions'),0)
                page.locator('form[data-extraction-kind="inventory"] button').click()
                page.locator('[data-extraction-confirm]').click()
                self.assertEqual(page.evaluate('approvedSubmissions'),1)
            finally:browser.close()

    def test_failed_page_retains_previous_current_and_resume_skips_saved_rows(self):
        old=self.run_job([page(1,['OLD'],True)])
        self.configure(self.inventory);job_id=self.enqueue('inventory')
        def interrupted(*args,**kwargs):
            yield page(1,['NEW1'])
            raise CatalogRetryable('inventory_page_timeout',1)
        adapter=Mock(implemented=True,extraction_implemented=True,inventory_pipeline=True,version='inventory-fixture')
        adapter.pages.side_effect=interrupted
        with patch('commerce_hub.worker.connector',return_value=adapter):self.worker.run_once(job_id=job_id)
        self.assertEqual(self.current()['source_job_id'],old)
        with self.app.app_context(),transaction() as db:
            job=db.get(Job,job_id);self.assertEqual(job.status,'queued');job.available_at=now()-timedelta(seconds=1)
            self.assertEqual(db.scalar(select(func.count()).select_from(Inventory).where(Inventory.job_id==job_id)),1)
        self.run_job([page(1,['NEW1']),page(2,['NEW2'],True)],job_id)
        self.assertEqual(self.current()['total'],2)
        self.assertEqual(self.current()['source_job_id'],job_id)

    def test_changed_page_boundary_blocks_resume_without_duplicate_rows(self):
        self.configure(self.inventory);job_id=self.enqueue('inventory')
        # A truncated generator persists page 1 but cannot publish it.
        self.run_job([page(1,['A'])],job_id)
        with self.app.app_context():services.resume_job(identities.ADMIN,identities.A,job_id)
        self.run_job([page(1,['B']),page(2,['C'],True)],job_id)
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.get(Job,job_id).error_code,'inventory_source_changed_start_new_run')
            self.assertEqual(db.scalar(select(func.count()).select_from(Inventory)),1)
        self.assertEqual(self.current()['total'],0)

    def test_duplicate_across_pages_never_publishes(self):
        job_id=self.run_job([page(1,['A']),page(2,['A'],True)])
        self.assertEqual(self.current()['total'],0)
        with self.app.app_context(),transaction() as db:self.assertEqual(db.get(Job,job_id).status,'blocked')

    def test_price_change_in_completed_page_requires_fresh_run(self):
        self.configure(self.inventory);job_id=self.enqueue('inventory')
        self.run_job([page(1,['A'])],job_id)
        with self.app.app_context():services.resume_job(identities.ADMIN,identities.A,job_id)
        changed=page(1,['A']);changed['rows'][0]['price']='99.0000'
        self.run_job([changed,page(2,['B'],True)],job_id)
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.get(Job,job_id).error_code,'inventory_source_changed_start_new_run')
        self.assertIsNone(self.current()['source_job_id'])

    def test_old_failed_run_cannot_supersede_newer_successful_inventory(self):
        self.configure(self.inventory);old=self.enqueue('inventory')
        self.run_job([page(1,['OLD'])],old)
        new=self.enqueue('inventory')
        self.run_job([page(1,['NEW'],True)],new)
        with self.app.app_context():services.resume_job(identities.ADMIN,identities.A,old)
        self.run_job([page(1,['OLD'],True)],old)
        self.assertEqual(self.current()['source_job_id'],new)
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.get(Job,old).error_code,'inventory_run_superseded_start_new_run')

    def test_confirmed_empty_run_clears_current_but_preserves_history(self):
        self.run_job([page(1,['A'],True)])
        new=self.run_job([page(1,[],True)])
        self.assertEqual(self.current()['source_job_id'],new);self.assertEqual(self.current()['total'],0)

    def test_logout_failure_does_not_publish_captured_pages(self):
        self.configure(self.inventory);job_id=self.enqueue('inventory')
        def interrupted(*args,**kwargs):
            yield page(1,['A'],True)
            raise ConnectorLoginError('vendor_logout_not_confirmed')
        adapter=Mock(implemented=True,extraction_implemented=True,inventory_pipeline=True,version='inventory-fixture')
        adapter.pages.side_effect=interrupted
        with patch('commerce_hub.worker.connector',return_value=adapter):self.worker.run_once(job_id=job_id)
        self.assertIsNone(self.current()['source_job_id'])

    def test_limited_three_pages_are_explicitly_labeled_and_not_full(self):
        self.configure(self.inventory)
        with self.app.app_context():job_id=services.enqueue_connection(identities.ADMIN,identities.A,self.inventory,'inventory',inventory_mode='three_pages')
        self.run_job([page(1,['A']),page(2,['B']),page(3,['C'],limited=True)],job_id)
        self.assertEqual(self.current()['total'],3)
        self.assertEqual(self.current()['catalog_label'],'Limited inventory load')

    def test_browser_iterator_stops_at_limit_and_logs_out_once(self):
        browser=Mock(authenticated=True)
        controls=[{'command':key,'enabled':key in ('PageNext','PageLast')} for key in ('PageFirst','PagePrev','PageNext','PageLast')]
        browser.first_page.return_value={**page(1,['A']),'pagination_controls':controls}
        controls2=[{**c,'enabled':True} for c in controls]
        browser.next_page.side_effect=[{**page(2,['B']),'pagination_controls':controls2},{**page(3,['C']),'pagination_controls':controls2}]
        result=list(pages('https://shilohtack-portal.acumatica.com/',Mock(),Mock(),page_limit=3,session_factory=lambda:browser))
        self.assertEqual(len(result),3);self.assertTrue(result[-1]['limited'])
        self.assertEqual(browser.next_page.call_count,2)
        browser.sign_in.assert_called_once();browser.sign_out.assert_called_once();browser.close.assert_called_once()

    def test_next_page_waits_through_temporary_empty_grid_without_clicking_twice(self):
        browser=InventoryCrawlBrowser();frame=Mock();frame.locator.return_value.count.return_value=1
        browser.page=Mock(frames=[frame])
        blank=grid_fixtures.fixture();blank['tables'][0]['rows']=blank['tables'][0]['rows'][:1]
        filled=grid_fixtures.fixture();filled['tables'][0]['rows'][2][7]='1.00'
        snapshots=iter([blank]+[filled]*3)
        frame.evaluate.side_effect=lambda script:{'checked':True} if script==AVAILABLE_FILTER else next(snapshots)
        result=browser.next_page(['OLD'],Mock())
        self.assertEqual(result['row_count'],2)
        frame.locator.return_value.click.assert_called_once()
        self.assertGreaterEqual(browser.page.wait_for_timeout.call_count,3)
