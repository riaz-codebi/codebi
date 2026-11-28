"""Capture/observe a bounded first-page inventory sample through the normal worker/Vault path."""
import argparse
import json
from pathlib import Path
import sys
import time

from dotenv import dotenv_values
from sqlalchemy import select

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app import create_app
from app.database import transaction
from app.models import Client,User,UserRole
from app.commerce import services
from commerce_hub.models import Job,Vendor,VendorConnection
from commerce_hub.worker import Worker,engine_from_url
from commerce_hub.vendors.shiloh_tack.inventory_login import approved_url


def report(job_id,status,code,sample):
    folder=ROOT/'instance/discovery';folder.mkdir(exist_ok=True)
    payload={'job_id':job_id,'status':status,'safe_error_code':code,'sample':sample}
    (folder/'inventory_first_page.json').write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    lines=['# Shiloh inventory: first-page sample','',f'- Job: {job_id}',f'- Status: {status}',
           '- Navigation: Orders > Catalog','- Page limit: 1; no pagination followed',
           '- Qty is order-entry quantity; This Warehouse is stock availability.','']
    if sample.get('available_items_only'):
        check=sample.get('availability_check',{})
        lines+=['- Show Available Items Only: checked and verified',
                '- Unfiltered first-page rows: '+str(check.get('unfiltered_first_page_count','')),
                '- Unfiltered zero-stock IDs: '+', '.join(check.get('unfiltered_zero_stock_ids',[])),
                '- Filtered first-page rows: '+str(sample.get('row_count',0)),'']
    if 'initial_row_count' in sample:
        viewport=sample.get('viewport',{})
        lines+=['- Initial browser page rows: '+str(sample['initial_row_count']),
                '- Rows after browser resize: '+str(sample.get('row_count',0)),
                '- Browser viewport: '+str(viewport.get('width'))+' x '+str(viewport.get('height')),'']
    def cell(value):return str(value).replace('|','\\|').replace('\n',' ')
    columns=sample.get('columns',[])
    if columns:
        lines+=['| '+' | '.join(cell(c['label']) for c in columns)+' |','| '+' | '.join('---' for _ in columns)+' |']
        for row in sample.get('rows',[]):lines.append('| '+' | '.join(cell(row['source_values'].get(c['label'],'')) for c in columns)+' |')
    lines+=['','## Pagination observed','']
    for control in sample.get('pagination_controls',[]):lines.append('- '+control['label']+' (`'+control.get('command','')+'`): '+('enabled' if control['enabled'] else 'disabled'))
    if not sample.get('pagination_controls'):lines.append('Pagination controls were not identified in this sample. No page navigation was attempted.')
    if code:lines+=['','Safe error: '+code]
    if sample.get('blocked_read_callbacks'):lines+=['','Blocked read callback paths: '+', '.join(sample['blocked_read_callbacks'])]
    (folder/'INVENTORY_FIRST_PAGE.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true');parser.add_argument('--observe-job')
    parser.add_argument('--client-name',default='The Saddle Consignor');args=parser.parse_args()
    if not args.run and not args.observe_job:print('No action taken. Use --run for one first-page sample or --observe-job.');return 0
    engine=None
    try:
        settings=dotenv_values(ROOT/'.env');app=create_app()
        with app.app_context():
            with transaction() as db:
                client=db.scalar(select(Client).where(Client.name==args.client_name,Client.is_active.is_(True)))
                actor=db.scalar(select(User.auth_user_id).join(UserRole,UserRole.user_id==User.id).where(
                    User.email=='riaz@codebisolutions.com',UserRole.role_code=='super_admin',User.is_active.is_(True)))
                if not client or not actor:raise RuntimeError('Authorized context unavailable')
                services.authorize(db,actor,client.id,'inventory')
                connection=db.scalar(select(VendorConnection).join(Vendor).where(VendorConnection.client_id==client.id,
                    Vendor.code=='shiloh_tack',VendorConnection.connection_type=='inventory'))
                if not connection or connection.status!='verified':raise RuntimeError('Verify the inventory connection first')
                approved_url(connection.portal_url)
                client_id,connection_id=client.id,connection.id
                active=db.scalar(select(Job).where(Job.connection_id==connection.id,Job.status.in_(('queued','running'))))
                if active and not (active.kind=='inventory' and active.payload.get('inventory_preview') is True):raise RuntimeError('Connection job already active')
                job_id=args.observe_job or (active.id if active else None)
            if not job_id:
                job_id=services.enqueue_connection(actor,client_id,connection_id,'inventory',inventory_mode='first_page')
                with transaction() as db:db.get(Job,job_id).max_attempts=1
            print('First-page inventory sample job:',job_id,flush=True)
            if not args.observe_job:engine=engine_from_url(settings['COMMERCE_WORKER_DATABASE_URL']);worker=Worker(engine,settings)
            deadline=time.monotonic()+160
            while time.monotonic()<deadline:
                with transaction() as db:
                    job=db.get(Job,job_id)
                    if not job or job.client_id!=client_id or job.connection_id!=connection_id or job.payload.get('inventory_preview') is not True:raise RuntimeError('Job context mismatch')
                    status,code,sample=job.status,job.error_code,job.result.get('inventory_preview',{})
                if status not in ('queued','running'):break
                if not args.observe_job:worker.run_once(job_id=job_id)
                time.sleep(.5)
            report(job_id,status,code,sample)
            print('Sample status:',status,'; safe code:',code or 'none','; captured rows:',sample.get('row_count',0))
            if sample.get('grid_snapshot'):
                for table in sample['grid_snapshot'].get('tables',[]):print('Observed header/data table:',table['id'],'; first row:',table['rows'][0] if table['rows'] else [],'; sibling tables:',[(t['id'],len(t['rows'])) for t in table.get('nearby_tables',[])])
            if sample.get('blocked_read_callbacks'):print('Blocked callback paths:',sample['blocked_read_callbacks'])
            print('Review: instance/discovery/INVENTORY_FIRST_PAGE.md and inventory_first_page.json')
            return 0 if status=='succeeded' else 1
    except Exception as exc:print('First-page sample unavailable:',type(exc).__name__);return 1
    finally:
        if engine is not None:engine.dispose()


if __name__=='__main__':raise SystemExit(main())
