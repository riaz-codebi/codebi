"""Read only the first Acumatica Orders/Catalog page; never cart actions or pagination."""
from decimal import Decimal,InvalidOperation
import re
import time
import traceback
from pathlib import Path
from urllib.parse import parse_qs,urlsplit,urljoin

from commerce_hub.connector_contracts import ConnectorLoginError
from commerce_hub.vendors.shiloh_tack.inventory_login import InventoryBrowser,allowed_url,approved_url,clean_path

AVAILABLE_FILTER=r'''() => {
    const labels=[...document.querySelectorAll('label,span,div')].filter(el=>/^Show Available Items Only$/i.test((el.textContent||'').trim()) && el.getClientRects().length);
    for(const label of labels.reverse()) {
        let control=label.control||document.getElementById(label.getAttribute('for')||'');
        for(let parent=label,depth=0;!control && parent && depth<4;parent=parent.parentElement,depth++) {
            const candidates=[...parent.querySelectorAll('input[type=checkbox],[role=checkbox]')].filter(el=>!el.closest('#ctl00_phG_gridInventoryItem'));
            if(candidates.length===1) control=candidates[0];
        }
        if(control && control.id && !control.closest('#ctl00_phG_gridInventoryItem')) return {
            control_id:control.id,tag:control.tagName,type:control.type||'',checked:control.type==='checkbox'?control.checked:control.getAttribute('aria-checked')==='true',
            disabled:!!control.disabled,label_id:label.id
        };
    }
    return {unrecognized:true,labels:labels.slice(0,5).map(el=>({tag:el.tagName,id:el.id,parent_id:el.parentElement?.id,parent_class:el.parentElement?.className}))};
}'''

GRID_SNAPSHOT=r'''() => {
    const text=element=>{
        const checkbox=element.querySelector('input[type=checkbox]');
        if(checkbox) return checkbox.checked?'TRUE':'FALSE';
        const input=element.querySelector('input[type=text],input[type=number],select,textarea');
        if(input) return input.value;
        const raw=(element.innerText||element.textContent||'').replace(/\s+/g,' ').trim();
        const title=element.getAttribute('title')||element.querySelector('[title]')?.getAttribute('title');
        return title && (raw.endsWith('...')||raw.endsWith('\u2026')) ? title : raw;
    };
    const tables=[];
    for(const table of document.querySelectorAll('table')) {
        const rows=[...table.rows].filter(row=>row.closest('table')===table);
        const cells=rows.map(row=>[...row.cells].map(text));
        if(!cells.some(row=>row.some(value=>/^Inventory\s*ID$/i.test(value)))) continue;
        let parent=table.parentElement;
        for(let level=0;level<7 && parent.parentElement && parent.querySelectorAll('table').length<2;level++) parent=parent.parentElement;
        const header=rows.find(row=>[...row.cells].some(cell=>/^Inventory\s*ID$/i.test(text(cell))));
        tables.push({id:table.id,rows:cells.slice(0,105),visible_columns:header?[...header.cells].flatMap((cell,index)=>cell.getBoundingClientRect().width>0 && getComputedStyle(cell).display!=='none'?[index]:[]):[],
            nearby_tables:[...parent.querySelectorAll('table')].filter(t=>t!==table).slice(0,15).map(t=>({id:t.id,rows:[...t.rows].filter(row=>row.closest('table')===t).slice(0,105).map(row=>[...row.cells].map(text))}))});
    }
    const grid=document.getElementById('ctl00_phG_gridInventoryItem');
    const commands={PageFirst:'First page',PagePrev:'Previous page',PageNext:'Next page',PageLast:'Last page'};
    const pager=grid?[...grid.querySelectorAll('[data-cmd]')].filter(el=>commands[el.getAttribute('data-cmd')]).map(el=>({
        command:el.getAttribute('data-cmd'),label:commands[el.getAttribute('data-cmd')],
        tooltip:el.getAttribute('tooltip')||el.title||'',
        enabled:![el,...el.querySelectorAll('*')].some(node=>node.disabled || node.getAttribute('aria-disabled')==='true' || /disabled/i.test(node.className||'')),
        visible:el.getClientRects().length>0
    })):[];
    if(!pager.length) pager.push(...[...document.querySelectorAll('[title],[aria-label],img[alt]')]
        .filter(el=>/next|previous|first page|last page|page \d/i.test((el.title||'')+' '+(el.getAttribute('aria-label')||'')+' '+(el.getAttribute('alt')||'')))
        .filter(el=>el.getClientRects().length).slice(0,30).map(el=>({label:el.title||el.getAttribute('aria-label')||el.getAttribute('alt'),
            enabled:!el.disabled && el.getAttribute('aria-disabled')!=='true' && !/disabled/i.test(el.className||'')})));
    const structure=[...document.querySelectorAll('th,td,div,span')].filter(el=>el.getClientRects().length && /^Inventory\s*ID$/i.test((el.textContent||'').trim())).slice(-10).map(el=>{
        const ancestors=[];for(let p=el,depth=0;p && depth<6;p=p.parentElement,depth++) ancestors.push({tag:p.tagName,id:p.id,class:p.className});
        return ancestors;
    });
    return {tables,pager,structure,empty:!!grid && /no (?:records|data|items)(?: (?:to display|found|available))?/i.test(grid.innerText||'')};
}'''


def number(value):
    try:
        result=Decimal(str(value).replace(',','').strip())
        if not result.is_finite():raise ValueError
        return format(result,'f')
    except (InvalidOperation,ValueError):raise ConnectorLoginError('inventory_grid_number_unrecognized') from None


def parse_snapshot(snapshot,allow_empty=False):
    required={'qty':'order_quantity','inventoryid':'inventory_id','description':'description',
              'price':'price','unit':'unit','thiswarehouse':'warehouse_quantity'}
    def key(value):
        normalized=re.sub(r'[^a-z]','',value.lower())
        return 'qty' if normalized=='quantity' else normalized
    for table in snapshot.get('tables',[]):
        for index,cells in enumerate(table.get('rows',[])):
            keys=[key(value) for value in cells]
            if not set(required)<=set(keys):continue
            visible=table.get('visible_columns',list(range(len(cells))))
            columns=[{'label':label,'key':required.get(k,'add_to_cart' if k in ('addtocart','addtocar') else k)} for i,(label,k) in enumerate(zip(cells,keys)) if k and i in visible]
            data=table['rows'][index+1:]
            if not any(len(row)==len(cells) and row[keys.index('inventoryid')] for row in data):
                candidates=table.get('nearby_tables',[])
                data=next((candidate['rows'] for candidate in candidates if any(len(row)==len(cells) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}',row[keys.index('inventoryid')]) for row in candidate['rows'])),[])
            rows=[];seen=set()
            for row in data:
                if len(row)!=len(cells):continue
                inventory_id=row[keys.index('inventoryid')].strip()
                if not inventory_id or inventory_id.lower()=='inventory id':continue
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}',inventory_id):raise ConnectorLoginError('inventory_grid_id_unrecognized')
                if inventory_id in seen:raise ConnectorLoginError('inventory_grid_duplicate_id')
                item={column['key']:row[i] for i,column in enumerate([{'key':'internal_inventory_id' if cells[i]=='InventoryID' else required.get(k,'add_to_cart' if k in ('addtocart','addtocar') else k)} for i,k in enumerate(keys)]) if column['key']}
                for name in ('order_quantity','price','warehouse_quantity'):item[name]=number(item[name])
                item['inventory_id']=inventory_id
                item['source_values']={label:row[i] for i,label in enumerate(cells) if keys[i]}
                rows.append(item);seen.add(inventory_id)
            if not rows and not (allow_empty and snapshot.get('empty') is True):raise ConnectorLoginError('inventory_grid_empty_or_unrecognized')
            if len(rows)>100:raise ConnectorLoginError('inventory_preview_limit_exceeded')
            return {'page':1,'page_limit':1,'complete_inventory':False,'columns':columns,'rows':rows,
                    'row_count':len(rows),'pagination_controls':snapshot.get('pager',[]),'pagination_followed':False,
                    'quantity_meaning':'Qty is order-entry quantity; This Warehouse is stock availability.'}
    raise ConnectorLoginError('inventory_grid_structure_changed')


class InventoryPreviewBrowser(InventoryBrowser):
    def __init__(self):
        super().__init__();self.reading_inventory=False;self.read_pages={'/main','/main.aspx'};self.blocked_callbacks=set()

    def additional_request_allowed(self,request):
        if not self.reading_inventory or not allowed_url(request.url,'GET'):return False
        path=clean_path(urlsplit(request.url).path)
        # The observed Orders > Catalog tile uses a POST to open its screen.
        # This exact tile is navigation, not a cart or order operation.
        if request.method=='POST' and path=='/frameset/workspace/029983b7-541e-47dc-9942-0cfe6292ec82/tiles/4ad314ac-7b88-4eb6-a234-35adbe8ae433/execute':
            return True
        if request.method=='GET' and request.is_navigation_request() and re.fullmatch(r'/pages/[a-z0-9_/]+\.aspx',path):
            self.read_pages.add(path)
        if request.method!='POST':return False
        if path not in self.read_pages:
            if re.fullmatch(r'/[a-z0-9_/.-]{1,200}',path):self.blocked_callbacks.add(path)
            return False
        body=request.post_data or ''
        if len(body)>2*1024*1024:return False
        values=parse_qs(body,keep_blank_values=True)
        commands=' '.join(value for name,items in values.items() if name.lower() in ('__callbackparam','__eventtarget','__eventargument','command','action') for value in items)
        if re.search(r'add.?to.?cart|addcart|checkout|place.?order|submit.?order|(?:^|[|:$,;&])(?:save|delete|insert)(?:$|[|:$,;&])',commands,re.I):return False
        return '__CALLBACKID' in values or bool(values.get('__EVENTTARGET'))

    def click_label(self,label):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            for frame in self.page.frames:
                matches=frame.get_by_text(re.compile(r'^'+re.escape(label)+r'$',re.I))
                for i in range(min(matches.count(),15)):
                    if matches.nth(i).is_visible():matches.nth(i).click(timeout=10000);return
            self.page.wait_for_timeout(250)
        raise ConnectorLoginError('inventory_catalog_navigation_unavailable')

    def first_page(self,heartbeat=lambda _p:None):
        self.preview_phase='catalog_navigation'
        self.reading_inventory=True
        navigation_responses=[]
        def observe(response):
            path=clean_path(urlsplit(response.url).path)
            if '/tiles/' in path and path.endswith('/execute'):
                item={'path':path,'status':response.status}
                try:
                    value=response.json()
                    if isinstance(value,dict) and isinstance(value.get('url'),str):
                        destination=urljoin(self.page.url,value['url'])
                        if allowed_url(destination,'GET'):item['target_path']=clean_path(urlsplit(destination).path)
                except Exception:pass
                navigation_responses.append(item)
        self.context.on('response',observe)
        self.page.wait_for_load_state('load',timeout=20000)
        self.click_label('Orders');heartbeat(35)
        self.click_label('Catalog');heartbeat(55)
        # The modern workspace resolves Catalog to a WebForms screen URL. Follow
        # that observed, same-host read-only URL if its shell does not navigate.
        self.page.wait_for_timeout(500)
        for response in reversed(navigation_responses):
            target=response.get('target_path','')
            destination=urljoin(self.page.url,target)
            if response['status']==200 and allowed_url(destination,'GET') and clean_path(urlsplit(destination).path)=='/pages/sp/sp700000.aspx':
                self.read_pages.add('/pages/sp/sp700000.aspx')
                existing=next((frame for frame in self.page.frames if clean_path(urlsplit(frame.url).path)=='/pages/sp/sp700000.aspx'),None)
                if existing is None:
                    screen=next((frame for frame in self.page.frames if clean_path(urlsplit(frame.url).path)=='/frames/default.aspx'),None)
                    if screen is not None:
                        try:screen.goto(destination,wait_until='commit',timeout=20000)
                        except Exception:
                            # Acumatica's shell can start the same navigation while
                            # this GET is committing. Let the bounded grid reader
                            # verify the resulting page; never retry credentials.
                            self.preview_phase='catalog_navigation_settling'
                break
        deadline=time.monotonic()+30
        self.preview_phase='grid_read'
        last=None;stable=0;last_snapshot=None;frames_seen=[];initial_count=None;availability_before=None
        while time.monotonic()<deadline:
            frames=[frame for page in self.context.pages for frame in page.frames]
            for frame in frames:
                try:
                    snapshot=frame.evaluate(GRID_SNAPSHOT)
                    info={'path':urlsplit(frame.url).path,'structure':snapshot.get('structure',[]),'table_count':frame.locator('table').count()}
                except Exception:continue  # An iframe can be replaced by a normal read-only navigation.
                if info not in frames_seen:frames_seen.append(info)
                if snapshot.get('tables') or snapshot.get('structure'):last_snapshot=snapshot
                if not snapshot.get('tables'):continue
                try:result=parse_snapshot(snapshot,allow_empty=getattr(self,'crawl_mode',False))
                except ConnectorLoginError:continue
                signature=tuple(row['inventory_id'] for row in result['rows'])
                if signature==last:stable+=1
                else:last=signature;stable=0
                if stable>=2:
                    if initial_count is None:
                        initial_count=result['row_count']
                        self.preview_phase='viewport_resize'
                        self.page.set_viewport_size({'width':1920,'height':1170})
                        self.page.wait_for_timeout(1500)
                        self.preview_phase='resized_grid_read'
                        last=None;stable=0
                        continue
                    if availability_before is None:
                        self.preview_phase='available_items_filter'
                        status=frame.evaluate(AVAILABLE_FILTER)
                        if not status.get('control_id') or status.get('disabled'):
                            error=ConnectorLoginError('inventory_availability_filter_unrecognized');error.grid_snapshot={'availability_filter':status};raise error
                        availability_before=result['rows']
                        control=frame.locator('[id="'+status['control_id']+'"]')
                        if not status['checked']:
                            if status['type']=='checkbox':control.check(timeout=10000)
                            else:control.click(timeout=10000)
                        frame.wait_for_function('('+AVAILABLE_FILTER+')().checked === true',timeout=10000)
                        self.page.wait_for_timeout(2000)
                        self.preview_phase='available_items_read'
                        last=None;stable=0
                        continue
                    status=frame.evaluate(AVAILABLE_FILTER)
                    if status.get('checked') is not True:raise ConnectorLoginError('inventory_availability_filter_not_applied')
                    # Do not return a stale, unfiltered grid while its callback is pending.
                    if any(Decimal(row['warehouse_quantity'])<=0 for row in result['rows']):continue
                    result['navigation']='Orders > Catalog'
                    result['available_items_only']=True
                    result['availability_check']={'unfiltered_first_page_count':len(availability_before),
                        'unfiltered_zero_stock_ids':[row['inventory_id'] for row in availability_before if Decimal(row['warehouse_quantity'])<=0],
                        'filtered_first_page_count':result['row_count']}
                    result['viewport']=self.page.viewport_size
                    result['initial_row_count']=initial_count
                    result['blocked_read_callbacks']=sorted(self.blocked_callbacks)
                    return result
            self.page.wait_for_timeout(500)
        if last_snapshot:
            # Safe grid-only diagnostics; never page HTML, hidden form state or cookies.
            last_snapshot['blocked_read_callbacks']=sorted(self.blocked_callbacks)
            last_snapshot['requests']=self.requests
            error=ConnectorLoginError('inventory_grid_structure_changed');error.grid_snapshot=last_snapshot;raise error
        error=ConnectorLoginError('inventory_catalog_navigation_unavailable');error.grid_snapshot={'frames':frames_seen,'requests':self.requests,'navigation_responses':navigation_responses};error.blocked_callbacks=sorted(self.blocked_callbacks);raise error


def preview_first_page(url,credentials,heartbeat=lambda _p:None,session_factory=InventoryPreviewBrowser):
    target=approved_url(url);session=session_factory();failure=None
    try:
        session.open(target);session.sign_in(credentials);heartbeat(20)
        return session.first_page(heartbeat)
    except ConnectorLoginError as exc:
        failure=exc
        if not hasattr(exc,'grid_snapshot'):exc.grid_snapshot={'phase':getattr(session,'preview_phase','sign_in')}
        raise
    except Exception as exc:
        failure=ConnectorLoginError('inventory_preview_unavailable')
        failure.grid_snapshot={'phase':getattr(session,'preview_phase','sign_in'),'exception_type':type(exc).__name__,
            'locations':[{'file':Path(frame.filename).name,'function':frame.name,'line':frame.lineno} for frame in traceback.extract_tb(exc.__traceback__) if 'commerce_hub' in frame.filename][-5:]}
        raise failure from None
    finally:
        try:
            if session.authenticated is True:session.sign_out()
        except Exception:
            if failure is None:raise ConnectorLoginError('vendor_logout_not_confirmed') from None
        finally:session.close()
