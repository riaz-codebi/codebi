"""Explicit bounded live catalog acceptance: publish or observe a complete category."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

from dotenv import dotenv_values
from sqlalchemy import select, func

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app import create_app
from app.database import transaction
from app.models import Client, User, UserRole
from app.commerce import services
from commerce_hub.models import VendorConnection, Vendor, Job, CatalogHead, CatalogWorkItem, CatalogPublication, Product
from commerce_hub.worker import Worker, engine_from_url


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--client-name',default='The Saddle Consignor')
    parser.add_argument('--category-id',default='440')
    parser.add_argument('--observe-job',help='Verify an existing catalog job without enqueueing another.')
    args=parser.parse_args()
    if not args.run and not args.observe_job:
        print('No changes made. --run explicitly enables this client catalog and captures the selected category.')
        return 0
    started=datetime.now(timezone.utc)
    settings=dotenv_values(ROOT/'.env')
    engine=None
    try:
        app=create_app()
        with app.app_context():
            with transaction() as db:
                client=db.scalar(select(Client).where(Client.name==args.client_name,Client.is_active.is_(True)))
                actor=db.scalar(select(User.auth_user_id).join(UserRole,UserRole.user_id==User.id).where(
                    User.email=='riaz@codebisolutions.com',UserRole.role_code=='super_admin',User.is_active.is_(True)))
                if not client or not actor:raise RuntimeError('Authorized context missing.')
                services.authorize(db,str(actor),client.id,'catalog',admin=True)
                conn=db.scalar(select(VendorConnection).join(Vendor).where(VendorConnection.client_id==client.id,
                    Vendor.code=='shiloh_tack',VendorConnection.connection_type=='catalog').with_for_update(of=VendorConnection))
                if not conn:raise RuntimeError('Catalog connection missing.')
                existing=db.scalar(select(Job.id).where(Job.connection_id==conn.id,Job.status.in_(('queued','running'))))
                if existing and not args.observe_job:raise RuntimeError('A connection job is already active.')
                if not args.observe_job:conn.config={**conn.config,'catalog_pipeline_enabled':True}
                client_id,vendor_id,connection_id=client.id,conn.vendor_id,conn.id
                head=db.get(CatalogHead,(client_id,vendor_id))
                previous=head.publication_id if head else None
            job_id=args.observe_job or services.enqueue_connection(str(actor),client_id,connection_id,'catalog',catalog_mode='category',category_id=args.category_id)
            engine=engine_from_url(settings['COMMERCE_WORKER_DATABASE_URL'])
            worker=Worker(engine,settings)
            if not args.observe_job:
                deadline=time.monotonic()+180
                while time.monotonic()<deadline:
                    with transaction() as db:
                        current=db.get(Job,job_id)
                        if current.status in ('succeeded','blocked','failed','cancelled'):break
                    worker.run_once(job_id=job_id)
                    time.sleep(.5)
            with transaction() as db:
                row=db.get(Job,job_id)
                if row.status!='succeeded':
                    print('Catalog acceptance status:',row.status,'safe code:',row.error_code)
                    return 1
                assert row.client_id==client_id and row.vendor_id==vendor_id and row.kind=='catalog'
                assert db.get(CatalogHead,(client_id,vendor_id)).publication_id==job_id
                publication=db.get(CatalogPublication,job_id)
                assert db.scalar(select(func.count()).select_from(Product).where(Product.job_id==job_id))==publication.summary['record_count']
                report={'job_id':job_id,'start_utc':started.isoformat(),'end_utc':datetime.now(timezone.utc).isoformat(),
                    'selection':publication.selection,'summary':publication.summary,
                    'interruption_verification':'Covered separately by rollback-only fixtures and automated tests',
                    'published':True}
            output=ROOT/'instance/discovery/catalog_database_acceptance.json'
            output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            page=services.results(str(actor),client_id,'catalog')
            assert page['source_job_id']==job_id
            print('Published category:',report['selection']['category_label'],'products:',report['summary']['record_count'],flush=True)
            print('Current published catalog and historical run are readable through the restricted application role.',flush=True)
            print('Acceptance report saved locally; no credentials logged.',flush=True)
        return 0
    except Exception as exc:
        print('Catalog acceptance stopped:',type(exc).__name__)
        return 1
    finally:
        if engine is not None:engine.dispose()


if __name__=='__main__':raise SystemExit(main())
