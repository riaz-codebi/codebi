"""Run one catalog and one inventory Test connection sample, without publishing either."""
import json
from pathlib import Path
import sys
import time
import argparse
from dotenv import dotenv_values
from sqlalchemy import select

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app import create_app
from app.database import transaction
from app.models import Client,User,UserRole
from app.commerce import services
from commerce_hub.models import Job,Vendor,VendorConnection
from commerce_hub.worker import Worker,engine_from_url


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true');parser.add_argument('--kind',choices=('catalog','inventory'))
    args=parser.parse_args()
    if not args.run:
        print('Use --run to test both connections and read one sample page each.');return 0
    engine=None
    try:
        settings=dotenv_values(ROOT/'.env');app=create_app()
        with app.app_context():
            with transaction() as db:
                client=db.scalar(select(Client).where(Client.name=='The Saddle Consignor',Client.is_active.is_(True)))
                actor=db.scalar(select(User.auth_user_id).join(UserRole,UserRole.user_id==User.id).where(
                    User.email=='riaz@codebisolutions.com',UserRole.role_code=='super_admin',User.is_active.is_(True)))
                if not client or not actor:raise RuntimeError('Authorized context unavailable')
                services.authorize(db,actor,client.id,'connections',admin=True)
                client_id=client.id
            before={kind:services.results(actor,client_id,kind)['source_job_id'] for kind in ('catalog','inventory')}
            engine=engine_from_url(settings['COMMERCE_WORKER_DATABASE_URL']);worker=Worker(engine,settings)
            results=[]
            for kind in ((args.kind,) if args.kind else ('catalog','inventory')):
                with transaction() as db:
                    connection=db.scalar(select(VendorConnection).join(Vendor).where(VendorConnection.client_id==client_id,
                        Vendor.code=='shiloh_tack',VendorConnection.connection_type==kind))
                    if not connection:raise RuntimeError('Connection unavailable')
                    connection_id=connection.id
                job_id=services.enqueue_connection(actor,client_id,connection_id,'test_connection')
                # A diagnostic sample should not repeat rejected credentials automatically.
                with transaction() as db:db.get(Job,job_id).max_attempts=1
                print(kind,'sample job:',job_id,flush=True)
                deadline=time.monotonic()+180
                while time.monotonic()<deadline:
                    with transaction() as db:
                        job=db.get(Job,job_id);status=job.status;code=job.error_code;sample=job.result.get('connection_sample',{});diagnostic=job.result.get('sample_diagnostic')
                    if status not in ('queued','running'):break
                    worker.run_once(job_id=job_id);time.sleep(.5)
                results.append({'kind':kind,'job_id':job_id,'status':status,'safe_error_code':code,'sample':sample,'diagnostic':diagnostic})
                print(kind,'status:',status,'rows:',sample.get('row_count',0),'safe code:',code or 'none',flush=True)
            after={kind:services.results(actor,client_id,kind)['source_job_id'] for kind in ('catalog','inventory')}
            unchanged=before==after
            folder=ROOT/'instance/discovery';folder.mkdir(exist_ok=True)
            if args.kind and (folder/'connection_samples.json').exists():
                previous=json.loads((folder/'connection_samples.json').read_text(encoding='utf-8'))
                results=[row for row in previous.get('results',[]) if row['kind']!=args.kind]+results
            (folder/'connection_samples.json').write_text(json.dumps({'published_data_unchanged':unchanged,'results':results},indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
            lines=['# Connection tests with first-page samples','',f'- Published catalog and inventory unchanged: {unchanged}','']
            for result in results:
                sample=result['sample'];columns=sample.get('columns',[])
                lines+=['## '+result['kind'].capitalize(),'', '- Job: '+result['job_id'],'- Status: '+result['status'],
                    '- Rows: '+str(sample.get('row_count',0)),'']
                def cell(value):return str(value).replace('|','\\|').replace('\n',' ')
                if columns:
                    lines+=['| '+' | '.join(cell(c['label']) for c in columns)+' |','| '+' | '.join('---' for c in columns)+' |']
                    for row in sample['rows']:lines.append('| '+' | '.join(cell(row['source_values'].get(c['label'],'')) for c in columns)+' |')
                lines+=['']
            (folder/'CONNECTION_SAMPLES.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
            print('Published catalog and inventory unchanged:',unchanged)
            return 0 if unchanged and all(result['status']=='succeeded' for result in results) else 1
    except Exception as exc:print('Connection sample check unavailable:',type(exc).__name__);return 1
    finally:
        if engine is not None:engine.dispose()


if __name__=='__main__':raise SystemExit(main())
