import unittest
from unittest.mock import patch

from sqlalchemy import select
import test_catalog_pipeline as fixtures
from app.commerce import services
from app.commerce.presentation import image_url
from app.database import transaction
from commerce_hub.models import Product
from commerce_hub.connector_contracts import Credentials
from commerce_hub.vendors.shiloh_tack.catalog_client import CatalogClient


class CatalogPresentationTests(unittest.TestCase):
    browser=fixtures.CatalogPipelineTests.browser
    csrf=fixtures.CatalogPipelineTests.csrf
    url=fixtures.CatalogPipelineTests.url
    configure=fixtures.CatalogPipelineTests.configure
    enqueue=fixtures.CatalogPipelineTests.enqueue
    enqueue_scope=fixtures.CatalogPipelineTests.enqueue_scope
    execute=fixtures.CatalogPipelineTests.execute
    record=fixtures.CatalogPipelineTests.record

    def setUp(self):fixtures.CatalogPipelineTests.setUp(self)

    def product(self,job):
        with self.app.app_context(),transaction() as db:
            return db.scalar(select(Product).where(Product.job_id==job))

    def test_job_view_has_readable_statistics_and_only_products_in_that_run(self):
        self.execute({'1':self.record('Existing coat'),'2':self.record('Unrelated','Tack')})
        job,_=self.execute({'1':self.record('Updated coat'),'3':self.record('New coat')},'category')
        with self.app.app_context():
            data=services.job_detail(fixtures.fixtures.fixtures.CLIENT,fixtures.fixtures.fixtures.A,job)
            current=services.results(fixtures.fixtures.fixtures.CLIENT,fixtures.fixtures.fixtures.A,'catalog')
        self.assertEqual(data['total'],2)
        self.assertEqual(current['total'],3)
        page=self.client.get(self.url('/jobs/'+job+'/view'))
        self.assertEqual(page.status_code,200)
        self.assertIn(b'Products captured',page.data)
        self.assertIn(b'Updated coat',page.data)
        self.assertNotIn(b'original_attributes_json',page.data)
        self.assertNotIn(b'Vendor SKU',page.data)
        self.assertNotIn(b'<pre',page.data)
        overview=page.data.split(b'class="commerce-run-overview"',1)[1].split(b'</dl>',1)[0]
        self.assertIn(b'Products captured',overview)
        self.assertIn(b'Images',overview)
        self.assertNotIn(b'<h3>Product images</h3>',page.data)
        self.assertNotIn(b'<h3>Run statistics</h3>',page.data)
        self.assertNotIn(b'commerce-statistics-metrics',page.data)
        self.assertIn(b'<div class="commerce-statistics-grid">\n<dl class="commerce-run-overview">',page.data)
        self.assertIn(b'<h3>Categories captured</h3>',page.data)
        self.assertIn(b'<th>Images</th>',page.data)

    def test_product_rows_count_unique_gallery_images_including_zero(self):
        record=self.record('Two images')
        record['images']=[{'url':'https://www.shilohtack.com/prodimages/one.jpg'},
                          {'url':'https://www.shilohtack.com/prodimages/two.jpg'},
                          {'url':'https://www.shilohtack.com/prodimages/one.jpg'}]
        empty=self.record('No images');empty['images']=[]
        job,_=self.execute({'1':record,'2':empty})
        with self.app.app_context():
            data=services.job_detail(fixtures.fixtures.fixtures.CLIENT,fixtures.fixtures.fixtures.A,job)
        self.assertEqual({row['name']:row['image_count'] for row in data['rows']},
                         {'Two images':2,'No images':0})
        page=self.client.get(self.url('/jobs/'+job+'/view'))
        self.assertIn(b'Two images</td><td>2</td>',page.data)
        self.assertIn(b'No images</td><td>0</td>',page.data)

    def test_scope_filters_and_inline_job_statistics_are_removed(self):
        self.execute({'1':self.record('Coat')})
        page=self.client.get(self.url()+'?tab=jobs')
        self.assertNotIn(b'id="job-scope"',page.data)
        self.assertNotIn(b'id="job-category"',page.data)
        self.assertNotIn(b'data-job-statistics',page.data)
        self.assertIn(b'>View</a>',page.data)

    def test_catalog_filters_search_all_pages_and_keep_category_options(self):
        records={str(1000+i):self.record('Coat '+str(i)) for i in range(30)}
        records['9000']=self.record('Needle blanket','Tack')
        self.execute(records)
        with self.app.app_context():
            data=services.results(fixtures.fixtures.fixtures.CLIENT,fixtures.fixtures.fixtures.A,'catalog',
                                  page=99,filters={'q':'needle'})
        self.assertEqual(data['total'],1)
        self.assertEqual(data['catalog_total'],31)
        self.assertEqual(data['page'],1)
        self.assertEqual([row['key'] for row in data['rows']],['9000'])
        self.assertEqual(len(data['categories']),2)
        page=self.client.get(self.url('/results/catalog')+'?q=needle&category=Home+%3E+Tack')
        self.assertEqual(page.status_code,200)
        self.assertIn(b'Needle blanket',page.data)
        self.assertNotIn(b'Coat 0</td>',page.data)
        self.assertIn(b'<th>Category</th><th class="commerce-price">Price</th><th>Last captured</th>',page.data)
        self.assertIn(b'data-product-results',page.data)
        missing=self.client.get(self.url('/results/catalog')+'?q=needle&category=Home+%3E+Outerwear')
        self.assertIn(b'No products match these filters.',missing.data)

    def test_catalog_search_treats_wildcards_literally_and_retains_pagination(self):
        records={str(1000+i):self.record('Coat '+str(i)) for i in range(30)}
        records['9000']=self.record('100% cotton_hoodie')
        self.execute(records)
        with self.app.app_context():
            data=services.results(fixtures.fixtures.fixtures.CLIENT,fixtures.fixtures.fixtures.A,'catalog',filters={'q':'%'})
        self.assertEqual([row['key'] for row in data['rows']],['9000'])
        page=self.client.get(self.url('/results/catalog')+'?q=Coat&category=Home+%3E+Outerwear')
        self.assertIn(b'q=Coat',page.data)
        self.assertIn(b'category=Home+%3E+Outerwear',page.data)
        second=self.client.get(self.url('/results/catalog')+'?q=Coat&category=Home+%3E+Outerwear&page=2')
        self.assertNotIn(b'Coat 0</td>',second.data)
        self.assertIn(b'Coat 29</td>',second.data)

    def test_selected_categories_are_complete_published_records_and_filters_use_current_versions(self):
        old,_=self.execute({'1':self.record('Old coat')},'category')
        self.execute({'1':self.record('New coat')},'category')
        page=self.client.get(self.url('/results/catalog'))
        self.assertIn(b'Selected categories',page.data)
        self.assertNotIn(b'Partial catalog',page.data)
        self.assertNotIn(b'Old coat',page.data)
        with self.app.app_context():
            current=services.results(fixtures.fixtures.fixtures.CLIENT,fixtures.fixtures.fixtures.A,'catalog',filters={'q':'Old coat'})
            history=services.results(fixtures.fixtures.fixtures.CLIENT,fixtures.fixtures.fixtures.A,'catalog',run_id=old,filters={'q':'Old coat'})
        self.assertEqual(current['total'],0)
        self.assertEqual(history['total'],1)

    def test_existing_snapshot_resolves_sku_without_mutating_history(self):
        job,_=self.execute({'0237-1799':self.record('Comforter')})
        product=self.product(job)
        self.assertIsNone(product.vendor_sku)
        with self.app.app_context():
            detail=services.product_detail(fixtures.fixtures.fixtures.CLIENT,fixtures.fixtures.fixtures.A,product.id)
        self.assertEqual(detail['key'],'0237-1799')
        self.assertEqual(detail['sku'],'0237-1799')
        self.assertIsNone(self.product(job).vendor_sku)

    def test_detail_renders_bullets_options_and_gallery_without_raw_vendor_html(self):
        record=self.record('Comforter')
        record.update(images=[{'url':'https://www.shilohtack.com/prodimages/one.jpg','alt':'Front'},
                              {'url':'https://www.shilohtack.com/prodimages/two.jpg','alt':'Back'},
                              {'url':'https://evil.example/steal','alt':'Bad'}],
            tabs=[{'label':'Key Features','text':'Warm','blocks':[{'type':'list','ordered':False,'items':[{'text':'Warm lining','children':[]}]}]},
                  {'label':'Description','text':'<script>alert(1)</script>','blocks':[]}],
            dropdowns=[{'label':'Color','options':[{'label':'Please select','value':'','is_placeholder':True},{'label':'Blue','value':'16794'}]}],
            price_fields=[{'class':'detailprice','text':'$14.50'}])
        job,_=self.execute({'0237-1799':record})
        product=self.product(job)
        response=self.client.get(self.url('/products/'+product.id))
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Warm lining',response.data)
        self.assertIn(b'<li>',response.data)
        self.assertIn(b'data-gallery-index="1"',response.data)
        self.assertIn(b'Blue',response.data)
        self.assertNotIn(b'evil.example',response.data)
        self.assertNotIn(b'<script>',response.data)
        self.assertNotIn(b'16794',response.data)
        self.assertEqual(response.headers['Cache-Control'],'no-store')

    def test_product_and_job_details_enforce_client_boundary(self):
        job,_=self.execute({'1':self.record('Coat')})
        product=self.product(job)
        self.assertEqual(self.browser(fixtures.fixtures.fixtures.OTHER).get(self.url('/products/'+product.id)).status_code,403)
        self.assertEqual(self.browser(fixtures.fixtures.fixtures.OTHER).get(self.url('/products/'+product.id,client=fixtures.fixtures.fixtures.B)).status_code,404)
        self.assertEqual(self.browser(fixtures.fixtures.fixtures.OTHER).get(self.url('/jobs/'+job+'/view',client=fixtures.fixtures.fixtures.B)).status_code,404)

    def test_unapproved_images_are_rejected(self):
        for url in ('http://www.shilohtack.com/prodimages/a.jpg','https://www.shilohtack.com.evil/prodimages/a.jpg',
                    'https://www.shilohtack.com/prodimages/%2e%2e/admin','https://user:pass@www.shilohtack.com/prodimages/a.jpg',
                    'https://www.shilohtack.com:8443/prodimages/a.jpg','javascript:alert(1)'):
            with self.subTest(url=url):self.assertIsNone(image_url(url,'shiloh_tack'))

    def test_new_shiloh_capture_stores_product_id_as_sku(self):
        client=CatalogClient('https://www.shilohtack.com/search.php',Credentials('fixture','not-real'),{'mode':'all'},lambda:None)
        html='<div class="proddetail"><div class="detailcustom2">Product ID: 0237-1799</div><div class="detailname">Comforter</div></div>'
        with patch.object(client,'get',return_value=html):record=client.product('/Comforter')
        self.assertEqual((record.source_key,record.vendor_sku),('0237-1799','0237-1799'))
