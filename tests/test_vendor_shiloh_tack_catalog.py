import unittest
from unittest.mock import Mock, MagicMock, patch

from commerce_hub.connector_contracts import Credentials,SourceRecord
from commerce_hub.vendors.shiloh_tack.catalog_client import CatalogClient, CatalogError, CatalogRetryable, retry_seconds
from commerce_hub.vendors.shiloh_tack.details import detail_record


class CatalogClientTests(unittest.TestCase):
    def test_connection_sample_uses_one_session_first_listing_and_detail_fields(self):
        from commerce_hub.vendors.shiloh_tack.catalog import ShilohCatalogConnector
        context=MagicMock();client=context.__enter__.return_value
        client.listing.return_value={'paths':['/one','/two'],'next_page':2}
        client.product.side_effect=[SourceRecord(key,key,{'name':'Name '+key,'price_fields':[{'class':'detailprice','text':'Price: $12.50'}],'images':[]}) for key in ('0001','0002')]
        with patch('commerce_hub.vendors.shiloh_tack.catalog_client.CatalogClient',return_value=context):
            result=ShilohCatalogConnector().test_sample('https://www.shilohtack.com/search.php',Credentials('fixture','not-real'),Mock())
        context.__enter__.assert_called_once();context.__exit__.assert_called_once()
        client.listing.assert_called_once_with(1);self.assertEqual(client.product.call_count,2)
        self.assertEqual(result['row_count'],2)
        self.assertEqual(result['rows'][0]['source_values']['Product ID'],'0001')
        self.assertEqual(result['rows'][0]['source_values']['Price'],'$12.50')
        self.assertFalse(result['pagination_followed'])

    def client(self):
        client=CatalogClient('https://www.shilohtack.com/search.php',Credentials('fixture','not-real'),{'mode':'all'},lambda:None)
        client.gate=Mock()
        return client

    def test_unapproved_paths_and_query_fields_are_rejected_before_network(self):
        for path in ('https://127.0.0.1/','//other.example/item','/TEST/a','/%54ESTDB/a','/logout.php',
                     '/vsadmin/test','/search.php?password=x','/proddetail.php?prod=1&action=delete'):
            with self.subTest(path=path),patch('commerce_hub.vendors.shiloh_tack.login.PinnedHTTPS') as network:
                with self.assertRaises(CatalogError):self.client().get(path)
                network.assert_not_called()

    def test_rate_limit_honors_retry_after_without_logging_body(self):
        response=Mock(status=429)
        response.getheader.return_value='120'
        connection=Mock();connection.getresponse.return_value=response
        with patch('commerce_hub.vendors.shiloh_tack.login.PinnedHTTPS',return_value=connection):
            with self.assertRaises(CatalogRetryable) as raised:self.client().get('/Example_Product')
        self.assertEqual(raised.exception.retry_after,120)
        self.assertEqual(str(raised.exception),'vendor_rate_limited')
        connection.close.assert_called_once()

    def test_redirect_or_missing_login_marker_stops_catalog_reads(self):
        for status,body in [(302,b''),(200,b'<h1>Sign in</h1>')]:
            response=Mock(status=status);response.read.return_value=body
            connection=Mock();connection.getresponse.return_value=response
            with self.subTest(status=status),patch('commerce_hub.vendors.shiloh_tack.login.PinnedHTTPS',return_value=connection):
                with self.assertRaises(CatalogError):self.client().get('/Example_Product')
            connection.close.assert_called_once()

    def test_retry_after_is_bounded(self):
        self.assertEqual(retry_seconds('999999999'),86400)
        self.assertEqual(retry_seconds('invalid'),60)

    def test_details_preserve_bullets_original_gallery_and_visible_color(self):
        html='''<div class="proddetail"><div class="detailcustom2">Product ID: 001</div><div class="detailname">Coat</div>
        <div class="catnavigation">Home &raquo; Rider Attire &raquo; Outerwear</div>
        <div class="detailprice">$10.00</div><a role="tab" href="#features">Key Features</a>
        <div role="tabpanel" id="features"><ul><li>Water resistant</li><li>Warm lining</li></ul></div>
        <a href="/prodimages/001.jpg"><img src="/prodimages/001-650.jpg" alt="Coat"></a>
        <div class="optioncontainer"><label>Color</label><select name="optn0" id="optn0x0"><option value="">Please select</option><option value="123">Blue</option></select></div></div>'''
        record=detail_record(html,'/Coat')
        self.assertEqual(record['id'],'001')
        self.assertEqual(record['category_levels'],['Rider Attire','Outerwear'])
        self.assertEqual(record['dropdowns'][0]['label'],'Color')
        self.assertEqual(len(record['images']),1)
        self.assertEqual(record['images'][0]['url'],'https://www.shilohtack.com/prodimages/001.jpg')
        self.assertEqual([i['text'] for i in record['tabs'][0]['blocks'][0]['items']],['Water resistant','Warm lining'])
