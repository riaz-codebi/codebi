"""Shiloh Tack Acumatica verification, preview and sequential inventory adapter."""
from commerce_hub.connector_contracts import ConnectorNotConfigured


class ShilohInventoryConnector:
    implemented = True
    extraction_implemented = True
    inventory_pipeline = True
    preview_implemented = True
    test_sample_implemented = True
    version = 'shiloh.inventory.pages.v1'

    def pages(self,url,credentials,heartbeat,page_limit=None):
        from .inventory_download import pages
        return pages(url,credentials,heartbeat,page_limit)

    def extract(self, url, credentials, heartbeat):
        raise ConnectorNotConfigured('connector_not_implemented')

    def verify(self, url, credentials):
        from commerce_hub.vendors.shiloh_tack.inventory_login import verify
        return verify(url,credentials)

    def preview_first_page(self,url,credentials,heartbeat):
        from commerce_hub.vendors.shiloh_tack.inventory_preview import preview_first_page
        return preview_first_page(url,credentials,heartbeat)

    def test_sample(self,url,credentials,heartbeat):
        return {**self.preview_first_page(url,credentials,heartbeat),'connection_type':'inventory'}
