"""Run/observe one Vault-backed inventory connection/sample test; never publish inventory."""
import argparse
from pathlib import Path
import sys
import time
from unittest.mock import patch

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


def diagnostic_adapter(vendor_code,connection_type):
    from commerce_hub.connector_registry import connector
    from commerce_hub.vendors.shiloh_tack.inventory_preview import InventoryPreviewBrowser,preview_first_page
    adapter=connector(vendor_code,connection_type)
    if (vendor_code,connection_type)!=('shiloh_tack','inventory'):return adapter
    class DiagnosticBrowser(InventoryPreviewBrowser):
        def sign_out(self):
            self.page.wait_for_load_state('load',timeout=15000)
            for index,frame in enumerate(self.page.frames):
                controls=frame.locator('[id*="logout" i], [id*="signout" i], [id*="user" i], [id*="caption" i], a[href*="logout" i]')
                metadata=controls.evaluate_all('''elements=>elements.slice(0,35).map(element=>({
                    tag:element.tagName,id:/^[A-Za-z0-9_$-]{1,120}$/.test(element.id)?element.id:'',
                    role:element.getAttribute('role'),clickHandler:typeof element.onclick==='function',
                    linkType:(element.getAttribute('href')||'').startsWith('javascript:')?'javascript':element.hasAttribute('href')?'link':'none',
                    visible:!!(element.offsetWidth||element.offsetHeight||element.getClientRects().length)
                }))''')
                print('Logout control metadata; frame',index,':',metadata,flush=True)
            return super().sign_out()
    adapter.test_sample=lambda url,credentials,heartbeat:{**preview_first_page(url,credentials,heartbeat,session_factory=DiagnosticBrowser),'connection_type':'inventory'}
    return adapter


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true');parser.add_argument('--observe-job')
    parser.add_argument('--inspect-logout',action='store_true',help='Print control IDs/types only; never page text, values, cookies or tokens.')
    parser.add_argument('--client-name',default='The Saddle Consignor')
    args=parser.parse_args()
    if not args.run and not args.observe_job:
        print('No action taken. Use --run for one inventory login verification or --observe-job for an existing job.');return 0
    engine=None
    try:
        settings=dotenv_values(ROOT/'.env');app=create_app()
        with app.app_context():
            with transaction() as db:
                client=db.scalar(select(Client).where(Client.name==args.client_name,Client.is_active.is_(True)))
                actor=db.scalar(select(User.auth_user_id).join(UserRole,UserRole.user_id==User.id).where(
                    User.email=='riaz@codebisolutions.com',UserRole.role_code=='super_admin',User.is_active.is_(True)))
                if not client or not actor:raise RuntimeError('Authorized context unavailable')
                services.authorize(db,actor,client.id,'connections',admin=True)
                connection=db.scalar(select(VendorConnection).join(Vendor).where(VendorConnection.client_id==client.id,
                    Vendor.code=='shiloh_tack',VendorConnection.connection_type=='inventory'))
                if not connection or not connection.username or not connection.credential_ref or not connection.portal_url:
                    raise RuntimeError('Inventory configuration missing')
                approved_url(connection.portal_url)
                active=db.scalar(select(Job).where(Job.connection_id==connection.id,Job.status.in_(('queued','running'))))
                if active and active.kind!='test_connection':raise RuntimeError('Inventory job already active')
                job_id=args.observe_job or (active.id if active else None)
                client_id,connection_id=client.id,connection.id
            if not job_id:
                job_id=services.enqueue_connection(actor,client_id,connection_id,'test_connection')
                with transaction() as db:db.get(Job,job_id).max_attempts=1
            print('Inventory verification job:',job_id,flush=True)
            if not args.observe_job:
                engine=engine_from_url(settings['COMMERCE_WORKER_DATABASE_URL']);worker=Worker(engine,settings)
            deadline=time.monotonic()+150
            while time.monotonic()<deadline:
                with transaction() as db:
                    row=db.get(Job,job_id)
                    if not row or row.client_id!=client_id or row.connection_id!=connection_id or row.kind!='test_connection':raise RuntimeError('Job context mismatch')
                    status,code=row.status,row.error_code
                if status not in ('queued','running'):break
                if not args.observe_job:
                    if args.inspect_logout:
                        with patch('commerce_hub.worker.connector',side_effect=diagnostic_adapter):worker.run_once(job_id=job_id)
                    else:worker.run_once(job_id=job_id)
                time.sleep(.5)
            print('Inventory verification status:',status,'; safe code:',code or 'none')
            print('Only a connection sample was requested; published inventory is unchanged. Credentials and raw vendor pages were not printed or saved.')
            return 0 if status=='succeeded' else 1
    except Exception as exc:
        print('Inventory verification unavailable:',type(exc).__name__);return 1
    finally:
        if engine is not None:engine.dispose()


if __name__=='__main__':raise SystemExit(main())
