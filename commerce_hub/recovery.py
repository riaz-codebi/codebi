"""Bounded extraction recovery policy; no credentials or exception text in diagnostics."""
from pathlib import Path
import traceback

AUTO_ATTEMPTS=10
MAX_ATTEMPTS=20
NO_PROGRESS_LIMIT=3
NON_RESUMABLE={
    'catalog_source_changed_start_new_run','catalog_resume_configuration_changed','catalog_listing_changed_or_empty',
    'catalog_conflicting_product_identity','catalog_invalid_product','catalog_url_not_approved',
    'inventory_source_changed_start_new_run','inventory_resume_configuration_changed','inventory_duplicate_id_start_new_run',
    'inventory_run_superseded_start_new_run','invalid_inventory_scope','access_or_configuration_changed',
    'vendor_login_rejected','vendor_mfa_required','vendor_interactive_verification_required',
    'vendor_password_change_required','vendor_url_not_approved','connection_not_configured',
    'vendor_catalog_access_changed','vendor_response_too_large','catalog_page_limit_reached',
    'catalog_product_limit_reached','inventory_page_limit_exceeded','inventory_record_limit_exceeded',
}


def can_resume(kind,meta,status,attempts,error):
    return kind in ('catalog','inventory') and bool(meta) and status in ('failed','blocked') and attempts<MAX_ATTEMPTS and error not in NON_RESUMABLE


def retry_delay(attempt,retry_after=0):
    return max(retry_after,min(300,10*2**min(max(0,attempt-1),5)))


def diagnostic(exc):
    result={'exception_type':type(exc).__name__,
        'locations':[{'file':Path(item.filename).name,'function':item.name,'line':item.lineno}
                     for item in traceback.extract_tb(exc.__traceback__) if 'commerce_hub' in item.filename][-6:]}
    supplied=getattr(exc,'safe_diagnostic',None)
    if supplied:result['component']=supplied
    original=getattr(exc,'orig',None)
    state=getattr(original,'sqlstate',None)
    if isinstance(state,str) and len(state)==5 and state.isalnum():result['sqlstate']=state
    return result
