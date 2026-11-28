"""Resolve the stored vendor code and connection type to a supported adapter."""
from commerce_hub.connector_contracts import ConnectorNotConfigured
from commerce_hub.vendors.shiloh_tack.catalog import ShilohCatalogConnector
from commerce_hub.vendors.shiloh_tack.inventory import ShilohInventoryConnector

CONNECTORS = {
    ('shiloh_tack', 'catalog'): ShilohCatalogConnector,
    ('shiloh_tack', 'inventory'): ShilohInventoryConnector,
}


def connector(vendor_code, connection_type):
    adapter = CONNECTORS.get((vendor_code, connection_type))
    if adapter is None:
        raise ConnectorNotConfigured('connector_not_implemented')
    return adapter()
