"""Explicitly run three inventory pages and publish them as a labeled limited current load."""
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
from commerce_hub.models import Job,Inventory,Vendor,VendorConnection
from commerce_hub.worker import Worker,engine_from_url


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-limited',action='store_true');parser.add_argument('--observe-job');parser.add_argument('--resume-job')
    args=parser.parse_args()
    if not args.run_limited and not args.observe_job and not args.resume_job:print('Use --run-limited for three pages, or --observe-job UUID.');return 0
    engine=None
    try:
        settings=dotenv_values(ROOT/'.env');app=create_app()
        with app.app_context():
            with transaction() as db:
                client=db.scalar(select(Client).where(Client.name=='The Saddle Consignor',Client.is_active.is_(True)))
                actor=db.scalar(select(User.auth_user_id).join(UserRole,UserRole.user_id==User.id).where(
                    User.email=='riaz@codebisolutions.com',UserRole.role_code=='super_admin',User.is_active.is_(True)))
                if not client or not actor:raise RuntimeError('Authorized context unavailable')
                connection=db.scalar(select(VendorConnection).join(Vendor).where(VendorConnection.client_id==client.id,
                    Vendor.code=='shiloh_tack',VendorConnection.connection_type=='inventory'))
                if not connection or connection.status!='verified':raise RuntimeError('Verified connection required')
                client_id,connection_id=client.id,connection.id
                services.authorize(db,actor,client_id,'inventory',admin=True)
            if args.resume_job:
                with transaction() as db:
                    previous=db.get(Job,args.resume_job)
                    if not previous or previous.client_id!=client_id or previous.connection_id!=connection_id or previous.payload.get('inventory_page_limit')!=3:
                        raise RuntimeError('Job scope mismatch')
                services.resume_job(actor,client_id,args.resume_job)
            job_id=args.observe_job or args.resume_job or services.enqueue_connection(actor,client_id,connection_id,'inventory',inventory_mode='three_pages')
            print('Limited inventory job:',job_id,flush=True)
            engine=engine_from_url(settings['COMMERCE_WORKER_DATABASE_URL']);worker=Worker(engine,settings)
            deadline=time.monotonic()+300
            while time.monotonic()<deadline:
                with transaction() as db:
                    job=db.get(Job,job_id)
                    if not job or job.client_id!=client_id or job.connection_id!=connection_id or job.payload.get('inventory_page_limit')!=3:
                        raise RuntimeError('Job scope mismatch')
                    status,code=job.status,job.error_code
                if status not in ('queued','running'):break
                if not args.observe_job:worker.run_once(job_id=job_id)
                time.sleep(1)
            with transaction() as db:
                job=db.get(Job,job_id)
                rows=db.scalars(select(Inventory).where(Inventory.client_id==client_id,Inventory.job_id==job_id).order_by(Inventory.source_key)).all()
                data={'job_id':job_id,'status':job.status,'safe_error_code':job.error_code,'summary':job.result,
                    'started_at':job.started_at.isoformat() if job.started_at else None,
                    'finished_at':job.finished_at.isoformat() if job.finished_at else None,
                    'rows':[row.attributes for row in rows]}
            current=services.results(actor,client_id,'inventory')
            data['is_current']=current['source_job_id']==job_id
            folder=ROOT/'instance/discovery';folder.mkdir(exist_ok=True)
            (folder/'inventory_current_load.json').write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
            meta=data['summary'].get('inventory',{})
            lines=['# Shiloh current inventory: limited test load','',
                '- Job: '+job_id,'- Status: '+status,'- Current inventory: '+str(data['is_current']),
                '- Coverage: '+meta.get('coverage','unpublished'),'- Pages: '+str(meta.get('page_count',len(meta.get('pages',[])))),
                '- Rows: '+str(len(rows)),'- Available items only: enabled',
                '- Started: '+str(data['started_at']),'- Finished: '+str(data['finished_at']),'',
                '| Inventory ID | Description | Price | Unit | This Warehouse |',
                '| --- | --- | ---: | --- | ---: |']
            def cell(value):return str(value).replace('|','\\|').replace('\n',' ')
            for row in data['rows']:lines.append('| '+' | '.join(cell(row.get(key,'')) for key in ('inventory_id','description','price','unit','warehouse_quantity'))+' |')
            (folder/'INVENTORY_CURRENT_LOAD.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
            print('Status:',status,'safe code:',code or 'none','pages:',meta.get('page_count'),
                'rows:',len(rows),'current:',data['is_current'])
            return 0 if status=='succeeded' and data['is_current'] else 1
    except Exception as exc:
        print('Limited inventory check unavailable:',type(exc).__name__);return 1
    finally:
        if engine is not None:engine.dispose()


if __name__=='__main__':raise SystemExit(main())
