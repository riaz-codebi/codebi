"""Resolve confirmed catalog SKU semantics without changing historical snapshots."""


def catalog_sku(source_key,stored_sku,vendor_code):
    return stored_sku or (source_key if vendor_code=='shiloh_tack' else None)
