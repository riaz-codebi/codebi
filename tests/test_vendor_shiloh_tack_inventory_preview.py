import unittest
from pathlib import Path
from html import escape
from unittest.mock import Mock

from commerce_hub.connector_contracts import ConnectorLoginError
from commerce_hub.vendors.shiloh_tack.inventory_preview import AVAILABLE_FILTER,GRID_SNAPSHOT,InventoryPreviewBrowser,parse_snapshot
from commerce_hub.vendors.shiloh_tack.inventory_login import HOST


def fixture():
    return {'tables':[{'id':'grid','rows':[['','Add To Cart','Qty','Inventory ID','Description','Price','* Unit','This Warehouse'],
        ['','FALSE','0.00','002300','Assorted Chicago Screw Pack','20.5000','EACH','38.00'],
        ['','FALSE','0.00','00835_BLK','Heavy Duty Bucket','13.5000','EACH','0.00']]}],
        'pager':[{'label':'Next Page','enabled':True},{'label':'Last Page','enabled':True}]}


class InventoryPreviewTests(unittest.TestCase):
    @unittest.skipUnless(Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe').is_file(),'Local Edge required for DOM fixture')
    def test_acumatica_div_pager_tooltips_and_nested_disabled_controls(self):
        from playwright.sync_api import sync_playwright
        rows=fixture()['tables'][0]['rows']
        table='<table>'+''.join('<tr>'+''.join('<td>'+escape(cell)+'</td>' for cell in row)+'</tr>' for row in rows)+'</table>'
        controls=''.join('<div class="toolsBtn" data-cmd="'+command+'" tooltip="Go to '+label+' Page"><div class="'+state+'" data-type="button"><div icon="'+command+'"></div></div></div>'
                         for command,label,state in [('PageFirst','First','toolBtnDisabled'),('PagePrev','Previous','toolBtnDisabled'),('PageNext','Next','toolBtnNormal'),('PageLast','Last','toolBtnNormal')])
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                page=browser.new_page()
                page.set_content('<label for="available">Show Available Items Only</label><input id="available" type="checkbox"><div id="ctl00_phG_gridInventoryItem"><input id="cart" type="checkbox">'+table+controls+'<div data-cmd="AddToCart">Add to cart</div></div>')
                state=page.evaluate(AVAILABLE_FILTER)
                self.assertEqual(state['control_id'],'available')
                self.assertFalse(state['checked'])
                page.locator('#available').check()
                self.assertTrue(page.evaluate(AVAILABLE_FILTER)['checked'])
                self.assertFalse(page.locator('#cart').is_checked())
                snapshot=page.evaluate(GRID_SNAPSHOT)
            finally:browser.close()
        self.assertEqual([control['command'] for control in snapshot['pager']],['PageFirst','PagePrev','PageNext','PageLast'])
        self.assertEqual([control['enabled'] for control in snapshot['pager']],[False,False,True,True])
        self.assertEqual(snapshot['pager'][2]['tooltip'],'Go to Next Page')
        self.assertEqual(parse_snapshot(snapshot)['row_count'],2)

    def test_all_columns_ids_and_numeric_precision_are_preserved(self):
        result=parse_snapshot(fixture())
        self.assertEqual(result['row_count'],2)
        self.assertEqual(result['rows'][0]['inventory_id'],'002300')
        self.assertEqual(result['rows'][0]['price'],'20.5000')
        self.assertEqual(result['rows'][0]['order_quantity'],'0.00')
        self.assertEqual(result['rows'][0]['warehouse_quantity'],'38.00')
        self.assertEqual(result['rows'][1]['warehouse_quantity'],'0.00')
        self.assertIn('add_to_cart',[column['key'] for column in result['columns']])
        self.assertIs(result['pagination_followed'],False)
        self.assertIs(result['complete_inventory'],False)
        self.assertEqual(result['pagination_controls'][0]['label'],'Next Page')

    def test_separate_header_and_body_tables_are_supported(self):
        sample=fixture();table=sample['tables'][0]
        table['nearby_tables']=[{'id':'grid-data','rows':table['rows'][1:]}];table['rows']=table['rows'][:1]
        self.assertEqual(parse_snapshot(sample)['row_count'],2)

    def test_hidden_erp_id_does_not_replace_inventory_id_or_display_columns(self):
        sample=fixture();table=sample['tables'][0]
        table['visible_columns']=list(range(8))
        for row,value in zip(table['rows'],['InventoryID','22405','37121']):row.append(value)
        result=parse_snapshot(sample)
        self.assertEqual(result['rows'][0]['inventory_id'],'002300')
        self.assertEqual(result['rows'][0]['internal_inventory_id'],'22405')
        self.assertNotIn('InventoryID',[column['label'] for column in result['columns']])

    def test_unknown_nonempty_inventory_id_cannot_be_silently_dropped(self):
        sample=fixture();sample['tables'][0]['rows'][2][3]='unexpected id!'
        self.assertRaisesRegex(ConnectorLoginError,'id_unrecognized',parse_snapshot,sample)

    def test_empty_inventory_requires_explicit_empty_grid_evidence(self):
        sample=fixture();sample['tables'][0]['rows']=sample['tables'][0]['rows'][:1]
        with self.assertRaises(ConnectorLoginError):parse_snapshot(sample,allow_empty=True)
        sample['empty']=True
        self.assertEqual(parse_snapshot(sample,allow_empty=True)['row_count'],0)
        with self.assertRaises(ConnectorLoginError):parse_snapshot(sample)

    def test_missing_required_columns_or_duplicate_ids_block_partial_capture(self):
        sample=fixture();sample['tables'][0]['rows'][0][7]='Unknown'
        self.assertRaises(ConnectorLoginError,parse_snapshot,sample)
        sample=fixture();sample['tables'][0]['rows'][2][3]='002300'
        self.assertRaisesRegex(ConnectorLoginError,'duplicate',parse_snapshot,sample)

    def test_cart_callbacks_are_blocked_and_only_observed_read_pages_allowed(self):
        browser=InventoryPreviewBrowser();browser.reading_inventory=True
        request=Mock(url='https://'+HOST+'/Pages/SP/SP504000.aspx',method='GET')
        request.is_navigation_request.return_value=True
        self.assertFalse(browser.additional_request_allowed(request))
        request.method='POST';request.post_data='__CALLBACKID=grid&__CALLBACKPARAM=Refresh'
        self.assertTrue(browser.additional_request_allowed(request))
        request.post_data='__CALLBACKID=grid&__CALLBACKPARAM=AddToCart'
        self.assertFalse(browser.additional_request_allowed(request))
        request.url='https://evil.example/Pages/SP/SP504000.aspx'
        self.assertFalse(browser.additional_request_allowed(request))
        request.url='https://'+HOST+'/Pages/SP/NotObserved.aspx';request.post_data='__CALLBACKID=grid&__CALLBACKPARAM=Refresh'
        self.assertFalse(browser.additional_request_allowed(request))
        request.url='https://'+HOST+'/frameset/workspace/029983b7-541e-47dc-9942-0cfe6292ec82/tiles/4ad314ac-7b88-4eb6-a234-35adbe8ae433/execute'
        self.assertTrue(browser.additional_request_allowed(request))
        request.url=request.url.replace('4ad314ac','9ad314ac')
        self.assertFalse(browser.additional_request_allowed(request))

    def test_known_inventory_callback_does_not_require_prior_navigation_get(self):
        for path in ('/pages/sp/sp700000.aspx','/(W(1))/Pages/SP/SP700000.aspx'):
            with self.subTest(path=path):
                browser=InventoryPreviewBrowser()
                request=Mock(url='https://'+HOST+path,method='POST',post_data='__CALLBACKID=grid&__CALLBACKPARAM=Refresh')
                self.assertFalse(browser.additional_request_allowed(request))
                browser.reading_inventory=True
                self.assertTrue(browser.additional_request_allowed(request))
                for command in ('AddToCart','Checkout','Save','Delete','Insert'):
                    request.post_data='__CALLBACKID=grid&__CALLBACKPARAM='+command
                    self.assertFalse(browser.additional_request_allowed(request))
                request.post_data='unrecognized=form'
                self.assertFalse(browser.additional_request_allowed(request))
                request.post_data='__CALLBACKID=grid&__CALLBACKPARAM=Refresh'
                request.url='https://evil.example'+path
                self.assertFalse(browser.additional_request_allowed(request))
