import csv
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from sqlalchemy import select,func

import test_catalog_pipeline as fixtures
from app.commerce import export_builder as builder,export_services
from app.database import transaction
from app.services import Forbidden,InvalidDetails,NotFound
from commerce_hub.export_mapping import MappingError
from commerce_hub.models import ClientAppAccess,DestinationTemplate,ExportDestination,MappingRule

IDENTITY=fixtures.fixtures.fixtures


class ExportMappingTests(unittest.TestCase):
    csrf=fixtures.CatalogPipelineTests.csrf
    browser=fixtures.CatalogPipelineTests.browser
    configure=fixtures.CatalogPipelineTests.configure
    enqueue=fixtures.CatalogPipelineTests.enqueue
    url=fixtures.CatalogPipelineTests.url
    enqueue_scope=fixtures.CatalogPipelineTests.enqueue_scope
    execute=fixtures.CatalogPipelineTests.execute
    record=fixtures.CatalogPipelineTests.record

    def setUp(self):
        fixtures.CatalogPipelineTests.setUp(self)
        seed=json.loads((Path(__file__).resolve().parents[1]/'commerce_hub/templates/shopify_product_2026.json').read_text())
        self.columns=seed['columns']
        with self.app.app_context(),transaction() as db:
            template=DestinationTemplate(platform='shopify',version=seed['version'],columns=self.columns,source_sha256=seed['source_sha256'])
            db.add(template);db.flush();self.template=template.id
            dest=ExportDestination(client_id=IDENTITY.A,vendor_id=self.vendor_id,platform='shopify',template_id=template.id)
            db.add(dest);db.flush();self.destination=dest.id

    def form(self,rules=None,base=''):
        rules=rules if rules is not None else builder.defaults(self.columns)
        result={'base_mapping':base}
        for row in builder.editor_rows(self.columns,rules):
            i=str(row['index'])
            if row['included']:result['include_'+i]='on'
            for key in ('mode','field','automatic','constant'):result[key+'_'+i]=row[key]
        return result

    def save(self,rules=None,base=''):
        with self.app.app_context():
            data=export_services.editor(IDENTITY.CLIENT,IDENTITY.A,self.destination,self.form(rules,base),save=True)
        self.assertIsNone(data['error'])
        return data

    def priced(self,name='Coat',category='Outerwear'):
        record=self.record(name,category)
        record['price_fields']=[{'class':'detailprice','text':'Price: $21.50'}]
        return record

    def test_editor_renders_all_61_fields_and_export_icons(self):
        response=self.client.get(self.url('/export-destinations/'+self.destination+'/mapping'))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data.count(b'data-mapping-row'),61)
        self.assertIn(b'Packed product dimension unit',response.data)
        self.assertIn(b'Fixed value',response.data)
        self.assertIn(b'>Default value</option>',response.data)
        self.assertNotIn(b'>Automatic</option>',response.data)
        self.assertIn(b'aria-current="page">Exports',response.data)
        exports=self.client.get(self.url()+'?tab=exports')
        self.assertIn(b'Shiloh Tack to Shopify',exports.data)
        self.assertIn(b'Download Shiloh Tack to Shopify CSV',exports.data)
        self.assertNotIn(b'name="catalog_job_id"',exports.data)

    def test_sample_selects_most_populated_product_and_exposes_friendly_values(self):
        rich=self.priced('Detailed blanket','Bedding')
        rich['description_text']='Warm blanket'
        rich['images']=[{'url':'https://www.shilohtack.com/prodimages/one.jpg'}]
        rich['tabs']=[{'label':'Specifications','text':'Brand: Showman','blocks':[{'type':'table','label_value_pairs':[{'label':'Brand:','value':'Showman'}]}]}]
        self.execute({'001':self.priced('Basic'),'002':rich})
        with self.app.app_context():data=export_services.editor(IDENTITY.CLIENT,IDENTITY.A,self.destination)
        self.assertEqual(data['sample']['product_id'],'002')
        values={row['label']:row['value'] for row in data['sample']['rows']}
        self.assertEqual(values['Vendor name'],'Shiloh Tack')
        self.assertEqual(values['Leaf category'],'Bedding')
        self.assertEqual(values['Specification: Brand'],'Showman')
        self.assertEqual(values['Category path'],'Home > Bedding')
        response=self.client.get(self.url('/export-destinations/'+self.destination+'/mapping'))
        self.assertIn(b'data-mapping-sample',response.data)
        self.assertIn(b'Warm blanket',response.data)

    def test_sample_can_change_without_saving_a_mapping(self):
        self.execute({'001':self.priced('First'),'002':self.priced('Second')})
        route=self.url('/export-destinations/'+self.destination)
        response=self.client.get(route+'/sample?product_id=002')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'data-sample-product-id="002"',response.data)
        self.assertIn(b'Second',response.data)
        self.assertIn('no-store',response.headers['Cache-Control'])
        page=self.client.get(route+'/mapping?sample_product_id=002')
        self.assertIn(b'name="sample_product_id" value="002"',page.data)
        self.assertEqual(self.client.get(route+'/sample?product_id=missing').status_code,404)
        with self.app.app_context(),transaction() as db:self.assertEqual(db.scalar(select(func.count()).select_from(MappingRule)),0)

    def test_default_values_popup_explains_all_rules_with_selected_sample_values(self):
        record=self.priced('Warm Blanket','Bedding')
        record['description_text']='Soft lining. '*40
        self.execute({'001':record,'002':self.priced('Leather Tray','Kitchen')})
        route=self.url('/export-destinations/'+self.destination)
        response=self.client.get(route+'/mapping?sample_product_id=001')
        self.assertIn(b'View default values',response.data)
        self.assertIn(b'id="mapping-default-values-dialog"',response.data)
        self.assertIn(b'<th>Option</th><th>What it does</th><th>Sample value</th>',response.data)
        self.assertNotIn(b'<th>Usually mapped to</th>',response.data)
        self.assertEqual(response.data.count(b'class="commerce-default-example"'),8)
        self.assertIn(b'Home &gt; Gift Items &gt; Kitchen Accessories becomes Kitchen Accessories.',response.data)
        with self.app.app_context():
            sample=export_services.sample_product(IDENTITY.CLIENT,IDENTITY.A,self.destination,'001')
            other=export_services.sample_product(IDENTITY.CLIENT,IDENTITY.A,self.destination,'002')
        self.assertEqual(len(sample['default_values']),8)
        by_target={row['target']:row['value'] for row in sample['default_values']}
        self.assertEqual(by_target['URL handle'],'warm-blanket')
        self.assertEqual(by_target['Type'],'Bedding')
        self.assertEqual(by_target['Option1 name'],'Title')
        self.assertEqual(by_target['Option1 value'],'Default Title')
        self.assertEqual(len(by_target['SEO description']),320)
        self.assertEqual(next(row['value'] for row in other['default_values'] if row['target']=='URL handle'),'leather-tray')
        partial=self.client.get(route+'/sample?product_id=002')
        self.assertIn(b'data-default-values-content',partial.data)
        self.assertIn(b'leather-tray',partial.data)

    def test_sample_requires_editor_access_and_never_uses_other_clients_products(self):
        self.execute({'001':self.priced()})
        route=self.url('/export-destinations/'+self.destination+'/sample?product_id=001')
        self.assertEqual(self.browser(IDENTITY.REGULAR).get(route).status_code,403)
        self.assertEqual(self.browser(IDENTITY.OTHER).get(route).status_code,403)
        self.assertEqual(self.browser(IDENTITY.OTHER).get(self.url('/export-destinations/'+self.destination+'/sample?product_id=001',client=IDENTITY.B)).status_code,404)

    def test_empty_catalog_and_html_sample_values_are_safe(self):
        route=self.url('/export-destinations/'+self.destination+'/mapping')
        self.assertIn(b'No published products are available for a sample yet.',self.client.get(route).data)
        self.execute({'001':self.priced('<script>bad</script>')})
        sample=self.client.get(self.url('/export-destinations/'+self.destination+'/sample?product_id=001'))
        self.assertNotIn(b'<script>',sample.data)
        self.assertIn(b'&lt;script&gt;bad&lt;/script&gt;',sample.data)

    def test_save_versions_and_stale_editor_does_not_overwrite(self):
        first=self.save()
        rules=builder.defaults(self.columns)
        rules=[r for r in rules if r['target']!='SEO description']
        second=self.save(rules,base=first['mapping_id'])
        self.assertEqual(second['version'],2)
        with self.app.app_context():
            stale=export_services.editor(IDENTITY.CLIENT,IDENTITY.A,self.destination,self.form(base=first['mapping_id']),save=True)
        self.assertIn('another window',stale['error'])
        self.assertEqual(stale['mapping_id'],first['mapping_id'])
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(MappingRule)),2)
            self.assertEqual(db.get(MappingRule,first['mapping_id']).rules,builder.defaults(self.columns))

    def test_download_expands_images_preserves_sku_and_omits_unselected_columns(self):
        record=self.priced('Blanket')
        record['images']=[{'url':'https://www.shilohtack.com/prodimages/one.jpg'},
                          {'url':'https://www.shilohtack.com/prodimages/two.jpg'}]
        self.execute({'0237-1799':record,'9':self.priced('No image')})
        self.save()
        with self.app.app_context(),patch('commerce_hub.worker.connector',side_effect=AssertionError('No scraping')):
            content,filename=export_services.download(IDENTITY.REGULAR,IDENTITY.A,self.destination)
        reader=csv.DictReader(StringIO(content.decode('utf-8-sig')));rows=list(reader)
        self.assertNotIn(b'\r\n',content)
        self.assertEqual(len(rows),3)
        self.assertEqual(rows[0]['SKU'],'0237-1799')
        self.assertEqual(rows[0]['URL handle'],'blanket')
        self.assertEqual(rows[0]['Price'],'21.50')
        self.assertEqual(rows[0]['Status'],'draft')
        self.assertEqual(rows[0]['Published on online store'],'FALSE')
        self.assertEqual(rows[1]['URL handle'],rows[0]['URL handle'])
        self.assertEqual(rows[1]['Image position'],'2')
        self.assertEqual(rows[1]['Title'],'')
        self.assertEqual(rows[1]['SKU'],'')
        self.assertNotIn('Cost per item',reader.fieldnames)
        self.assertNotIn('Variant Barcodes',reader.fieldnames)
        self.assertTrue(filename.endswith('.csv'))

    def test_download_uses_combined_publication_not_just_latest_run(self):
        self.execute({'1':self.priced('Old'),'2':self.priced('Keep','Tack')})
        self.execute({'1':self.priced('New'),'3':self.priced('Added')},'category')
        self.save()
        with self.app.app_context():content,_=export_services.download(IDENTITY.CLIENT,IDENTITY.A,self.destination)
        rows=list(csv.DictReader(StringIO(content.decode('utf-8-sig'))))
        self.assertEqual({r['Title'] for r in rows},{'New','Keep','Added'})

    def test_client_admin_edits_regular_user_downloads_and_other_client_is_denied(self):
        route=self.url('/export-destinations/'+self.destination+'/mapping')
        self.assertEqual(self.browser(IDENTITY.CLIENT).get(route).status_code,200)
        self.assertEqual(self.browser(IDENTITY.REGULAR).get(route).status_code,403)
        self.assertEqual(self.browser(IDENTITY.OTHER).get(route).status_code,403)
        self.assertEqual(self.browser(IDENTITY.OTHER).get(self.url('/export-destinations/'+self.destination+'/mapping',client=IDENTITY.B)).status_code,404)
        self.assertEqual(self.client.post(route,data=self.form()).status_code,400)

    def test_required_fields_and_unknown_source_rejected_without_save(self):
        for rules in ([r for r in builder.defaults(self.columns) if r['target']!='Title'],
                      [{'target':r['target'],'source':['product','credentials']} if r['target']=='Title' else r for r in builder.defaults(self.columns)]):
            with self.app.app_context():data=export_services.editor(IDENTITY.CLIENT,IDENTITY.A,self.destination,self.form(rules),save=True)
            self.assertIsNotNone(data['error'])
        with self.app.app_context(),transaction() as db:self.assertEqual(db.scalar(select(func.count()).select_from(MappingRule)),0)

    def test_unresolved_variants_block_download_instead_of_inventing_skus(self):
        record=self.priced();record['dropdowns']=[{'label':'Color','options':[{'value':'16794','label':'Teal'}]}]
        self.execute({'8084':record});self.save()
        with self.app.app_context(),self.assertRaisesRegex(InvalidDetails,'Variant SKUs'):
            export_services.download(IDENTITY.CLIENT,IDENTITY.A,self.destination)

    def test_catalog_with_options_suggests_fixed_size_color_and_vendor_choice_fields(self):
        record=self.priced()
        record['dropdowns']=[{'label':'Size','options':[{'value':'1','label':'Medium'},{'value':'2','label':'Large'},{'value':'3','label':'Assorted'}]}]
        self.execute({'001':record})
        with self.app.app_context():data=export_services.editor(IDENTITY.CLIENT,IDENTITY.A,self.destination)
        rows={row['name']:row for row in data['rows']}
        self.assertEqual((rows['Option1 name']['mode'],rows['Option1 name']['constant']),('constant','Size'))
        self.assertEqual((rows['Option1 value']['mode'],rows['Option1 value']['field']),('field','option_1_values'))
        self.assertEqual((rows['Option2 name']['mode'],rows['Option2 name']['constant']),('constant','Color'))
        self.assertEqual((rows['Option2 value']['mode'],rows['Option2 value']['field']),('field','option_2_values'))
        self.assertTrue(all(rows[name]['included'] for name in ('Option1 name','Option1 value','Option2 name','Option2 value')))
        sample={row['key']:row['value'] for row in data['sample']['rows']}
        self.assertEqual(sample['option_1_values'],'Medium, Large, Assorted')
        self.assertEqual(sample['option_2_values'],'')
        rules=builder.defaults(self.columns,has_options=True)
        self.save(rules)
        with self.app.app_context(),self.assertRaisesRegex(InvalidDetails,'Variant SKUs'):
            export_services.download(IDENTITY.CLIENT,IDENTITY.A,self.destination)

    def test_second_option_mapping_saves_as_pair_and_preserves_existing_versions(self):
        self.execute({'001':self.priced()})
        first=self.save()
        rules=builder.defaults(self.columns,has_options=True)
        second=self.save(rules,base=first['mapping_id'])
        self.assertEqual(second['version'],2)
        with self.app.app_context(),transaction() as db:
            old_rules=db.get(MappingRule,first['mapping_id']).rules
        self.assertEqual(next(r['source'] for r in old_rules if r['target']=='Option1 name'),['product','auto.option_name'])
        broken=[r for r in rules if r['target']!='Option2 value']
        with self.assertRaisesRegex(MappingError,'both Option2'):
            builder.validate_rules(self.columns,broken,builder.FIELDS)

    def test_bad_price_and_duplicate_handles_are_blocked(self):
        self.execute({'1':self.priced(),'2':self.priced('Other')})
        rules=[{'target':'URL handle','value':'same-handle'} if r['target']=='URL handle' else r for r in builder.defaults(self.columns)]
        self.save(rules)
        with self.app.app_context(),self.assertRaisesRegex(InvalidDetails,'unique'):
            export_services.download(IDENTITY.CLIENT,IDENTITY.A,self.destination)
        self.assertRaises(MappingError,builder.cell_value,'Price','Price: $10 - $20')

    def test_title_handle_normalizes_spaces_and_uses_mapped_title(self):
        self.assertEqual(builder.title_handle('  Showman Leather Tray  '),'showman-leather-tray')
        self.assertEqual(builder.title_handle('Caf\u00e9  Saddle & Pad'),'cafe-saddle-pad')
        self.execute({'0237-1799':self.priced('Original name')})
        rules=[{'target':'Title','value':'My Product Title'} if r['target']=='Title' else r for r in builder.defaults(self.columns)]
        self.save(rules)
        with self.app.app_context():content,_=export_services.download(IDENTITY.CLIENT,IDENTITY.A,self.destination)
        row=next(csv.DictReader(StringIO(content.decode('utf-8-sig'))))
        self.assertEqual(row['URL handle'],'my-product-title')

    def test_duplicate_title_handles_require_review_instead_of_adding_ids(self):
        self.execute({'1':self.priced('Same title'),'2':self.priced('Same title')})
        self.save()
        with self.app.app_context(),self.assertRaisesRegex(InvalidDetails,'unique'):
            export_services.download(IDENTITY.CLIENT,IDENTITY.A,self.destination)

    def test_no_mapping_no_catalog_and_revoked_access_block_download(self):
        with self.app.app_context(),self.assertRaisesRegex(InvalidDetails,'Save a mapping'):
            export_services.download(IDENTITY.CLIENT,IDENTITY.A,self.destination)
        self.save()
        with self.app.app_context(),self.assertRaisesRegex(InvalidDetails,'catalog refresh'):
            export_services.download(IDENTITY.CLIENT,IDENTITY.A,self.destination)
        with self.app.app_context(),transaction() as db:
            db.get(ClientAppAccess,(IDENTITY.A,'commerce_hub')).features=['catalog']
        with self.app.app_context(),self.assertRaises(Forbidden):export_services.download(IDENTITY.CLIENT,IDENTITY.A,self.destination)

    def test_direct_download_route_is_private_and_csrf_protected(self):
        self.execute({'1':self.priced()});self.save()
        route=self.url('/export-destinations/'+self.destination+'/download')
        self.assertEqual(self.client.post(route).status_code,400)
        response=self.client.post(route,data={'csrf_token':self.csrf()})
        self.assertEqual(response.status_code,200)
        self.assertIn('no-store',response.headers['Cache-Control'])
        self.assertIn('attachment',response.headers['Content-Disposition'])

    def test_only_internal_admin_can_enable_or_disable_subscriptions(self):
        with self.app.app_context(),self.assertRaises(Forbidden):
            export_services.set_destination(IDENTITY.CLIENT,IDENTITY.A,self.vendor_id,self.template,False)
        with self.app.app_context():
            export_services.set_destination(IDENTITY.ADMIN,IDENTITY.A,self.vendor_id,self.template,False)
            self.assertEqual(export_services.overview(IDENTITY.CLIENT,IDENTITY.A)['rows'],[])
            with self.assertRaises(NotFound):export_services.download(IDENTITY.CLIENT,IDENTITY.A,self.destination)

    def test_mapping_post_saves_and_redirects_to_exports(self):
        route=self.url('/export-destinations/'+self.destination+'/mapping')
        form=self.form();form['csrf_token']=self.csrf()
        response=self.client.post(route,data=form)
        self.assertEqual(response.status_code,302)
        self.assertIn('tab=exports',response.location)
        with self.app.app_context(),transaction() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(MappingRule)),1)

    def test_included_dimension_fields_and_source_image_urls_are_validated(self):
        rules=builder.defaults(self.columns)+[{'target':'Packed product length','value':'20'}]
        with self.assertRaisesRegex(MappingError,'four packed'):
            builder.validate_rules(self.columns,rules,builder.FIELDS)
        self.assertRaises(MappingError,builder.cell_value,'Product image URL','javascript:bad')

    def test_html_description_escapes_source_and_preserves_lists(self):
        record=self.priced();record['tabs']=[{'label':'Key Features','blocks':[{'type':'list','items':[{'text':'<script>bad</script>','children':[]}]}]}]
        self.execute({'1':record});self.save()
        with self.app.app_context():content,_=export_services.download(IDENTITY.CLIENT,IDENTITY.A,self.destination)
        row=next(csv.DictReader(StringIO(content.decode('utf-8-sig'))))
        self.assertIn('<ul><li>&lt;script&gt;',row['Description'])
        self.assertNotIn('<script>',row['Description'])

    def test_spreadsheet_formula_source_is_rejected(self):
        self.execute({'1':self.priced('=HYPERLINK("bad")')});self.save()
        with self.app.app_context(),self.assertRaisesRegex(InvalidDetails,'formula'):
            export_services.download(IDENTITY.CLIENT,IDENTITY.A,self.destination)
