"""Sequential, available-only Acumatica inventory pages in one authenticated session."""
import json
import time
import traceback
from pathlib import Path
from decimal import Decimal

from commerce_hub.connector_contracts import ConnectorLoginError
from .catalog_client import CatalogRetryable
from .inventory_login import approved_url
from .inventory_preview import AVAILABLE_FILTER,GRID_SNAPSHOT,InventoryPreviewBrowser,parse_snapshot


class InventoryCrawlBrowser(InventoryPreviewBrowser):
    session_seconds=14400
    request_limit=20000
    crawl_mode=True

    def next_page(self,previous,heartbeat):
        self.preview_phase='next_page_locate'
        frame=next((frame for frame in self.page.frames if frame.locator('#ctl00_phG_gridInventoryItem').count()),None)
        if frame is None:raise ConnectorLoginError('inventory_grid_structure_changed')
        control=frame.locator('#ctl00_phG_gridInventoryItem [data-cmd="PageNext"]')
        if control.count()!=1:raise ConnectorLoginError('inventory_pagination_unrecognized')
        self.preview_phase='next_page_click'
        control.click(timeout=10000)
        self.preview_phase='next_page_read'
        deadline=time.monotonic()+40;last=None;stable=0
        while time.monotonic()<deadline:
            heartbeat(0)
            try:
                status=frame.evaluate(AVAILABLE_FILTER)
                if status.get('unrecognized'):
                    self.page.wait_for_timeout(250);continue
                if status.get('checked') is not True:
                    raise ConnectorLoginError('inventory_availability_filter_not_applied')
                result=parse_snapshot(frame.evaluate(GRID_SNAPSHOT),allow_empty=True)
            except ConnectorLoginError as exc:
                if exc.code in ('inventory_grid_empty_or_unrecognized','inventory_grid_structure_changed'):
                    self.page.wait_for_timeout(250);continue
                raise
            if not result['rows']:
                self.page.wait_for_timeout(250);continue
            signature=json.dumps(result['rows'],sort_keys=True)
            if [row['inventory_id'] for row in result['rows']]==previous:
                self.page.wait_for_timeout(250);continue
            if any(Decimal(row['warehouse_quantity'])<=0 for row in result['rows']):
                raise ConnectorLoginError('inventory_unavailable_item_returned')
            stable=stable+1 if signature==last else 0;last=signature
            if stable>=2:return result
            self.page.wait_for_timeout(250)
        raise CatalogRetryable('inventory_page_timeout')


def page_state(result,page_number):
    controls={control.get('command'):control for control in result['pagination_controls']}
    if not all(key in controls for key in ('PageFirst','PagePrev','PageNext','PageLast')):
        raise ConnectorLoginError('inventory_pagination_unrecognized')
    if page_number==1 and controls['PagePrev']['enabled']:
        raise ConnectorLoginError('inventory_first_page_not_confirmed')
    if controls['PageNext']['enabled']!=controls['PageLast']['enabled']:
        raise ConnectorLoginError('inventory_pagination_inconsistent')
    return not controls['PageNext']['enabled']


def pages(url,credentials,heartbeat,page_limit=None,session_factory=InventoryCrawlBrowser):
    session=session_factory();failure=None
    try:
        session.open(approved_url(url))
        session.page.set_viewport_size({'width':1920,'height':1170})
        session.sign_in(credentials)
        result=session.first_page(heartbeat)
        for page_number in range(1,1001):
            heartbeat(0)
            last_page=page_state(result,page_number)
            result.update(page_number=page_number,last_page=last_page,available_items_only=True,
                          limited=bool(page_limit and page_number>=page_limit and not last_page))
            yield result
            if last_page or (page_limit and page_number>=page_limit):return
            previous=[row['inventory_id'] for row in result['rows']]
            result=session.next_page(previous,heartbeat)
        raise ConnectorLoginError('inventory_page_limit_exceeded')
    except (ConnectorLoginError,CatalogRetryable) as exc:failure=exc;raise
    except GeneratorExit:
        failure=True;raise
    except Exception as exc:
        failure=True
        error=CatalogRetryable('inventory_page_unavailable')
        error.safe_diagnostic={'exception_type':type(exc).__name__,'phase':getattr(session,'preview_phase','open'),
            'locations':[{'file':Path(frame.filename).name,'function':frame.name,'line':frame.lineno} for frame in traceback.extract_tb(exc.__traceback__) if 'commerce_hub' in frame.filename][-5:]}
        raise error from None
    finally:
        try:
            if session.authenticated is True:session.sign_out()
        except Exception:
            if failure is None:raise ConnectorLoginError('vendor_logout_not_confirmed') from None
        finally:session.close()
