"""Shiloh Tack authenticated catalog adapter."""
from commerce_hub.connector_contracts import ConnectorNotConfigured
from commerce_hub.vendors.shiloh_tack import login
import re


class ShilohCatalogConnector:
    implemented = True
    extraction_implemented = True
    catalog_pipeline = True
    test_sample_implemented = True
    version = 'shiloh.catalog.v2'

    def open_catalog(self, url, credentials, selection, check):
        from .catalog_client import CatalogClient
        return CatalogClient(url,credentials,selection,check)

    def extract(self, url, credentials, heartbeat, selection=None):
        raise ConnectorNotConfigured('connector_not_implemented')

    def verify(self, url, credentials):
        return login.verify(url, credentials)

    def test_sample(self,url,credentials,heartbeat):
        from .catalog_client import CatalogClient
        from .search import catalog_selection
        rows=[]
        with CatalogClient(url,credentials,catalog_selection('all'),lambda:heartbeat(0)) as client:
            listing=client.listing(1)
            if not 1<=len(listing['paths'])<=100:
                raise login.ConnectorLoginError('catalog_sample_page_unrecognized')
            for index,path in enumerate(listing['paths']):
                record=client.product(path)
                values=record.attributes
                price=next((item.get('text','') for item in values.get('price_fields',[]) if item.get('class')=='detailprice'),'')
                price=re.sub(r'^\s*price\s*:\s*','',price,flags=re.I).strip()
                rows.append({'product_id':record.source_key,'attributes':values,'source_values':{
                    'Product ID':record.source_key,'Name':values.get('name',''),
                    'Price':price,'Images':str(len(values.get('images',[]))),
                    'Category':values.get('category_path','')}})
                heartbeat(int((index+1)/len(listing['paths'])*95))
        return {'connection_type':'catalog','page':1,'page_limit':1,'pagination_followed':False,
            'row_count':len(rows),'rows':rows,'columns':[{'label':label} for label in ('Product ID','Name','Price','Images','Category')]}
