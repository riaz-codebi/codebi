"""Exercise restricted PostgreSQL recovery in one rolled-back transaction. No vendor requests."""
import argparse
from datetime import timedelta
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4

from dotenv import dotenv_values
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.orm import sessionmaker

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.setup_commerce import connection_url
from commerce_hub.connector_contracts import SourceRecord
from commerce_hub.models import Job, CatalogHead, CatalogWorkItem, CatalogPublication, Product, VendorCatalogLease, now
from commerce_hub.worker import Worker
from commerce_hub.vendors.shiloh_tack.catalog_client import CatalogRetryable


class FixtureClient:
    fail=True
    fetched=[]
    def __enter__(self):return self
    def __exit__(self,*_args):pass
    def listing(self,_page):
        return {'page':1,'paths':['/fixture-'+str(i) for i in range(3)],'next_page':None,'last_page_hint':1}
    def product(self,path):
        self.fetched.append(path)
        if path=='/fixture-2' and self.fail:raise CatalogRetryable('rollback_fixture_interruption',1)
        return SourceRecord(path,None,{'name':'Rollback fixture','category_levels':[]},'https://www.shilohtack.com'+path)


class FixtureAdapter:
    implemented=extraction_implemented=catalog_pipeline=True
    version='rollback-fixture-v1'
    def __init__(self,client):self.client=client
    def open_catalog(self,*_args):return self.client


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rollback-check',action='store_true')
    args=parser.parse_args()
    if not args.rollback_check:
        print('No changes made. Use --rollback-check for temporary, rolled-back fixtures.')
        return 0
    engine=None
    stage='connect'
    try:
        settings=dotenv_values(ROOT/'.env')
        engine=create_engine(connection_url(settings['DATABASE_URL']).set(drivername='postgresql+psycopg'),
            hide_parameters=True,connect_args={'sslmode':'require','connect_timeout':10})
        with engine.connect() as connection:
            outer=connection.begin()
            try:
                stage='fixtures'
                source=connection.execute(text("SELECT c.client_id,c.vendor_id,c.id,c.credential_version FROM commerce.vendor_connections c JOIN codebi.clients org ON org.id=c.client_id JOIN commerce.vendors v ON v.id=c.vendor_id WHERE org.name='The Saddle Consignor' AND v.code='shiloh_tack' AND c.connection_type='catalog'")).one()
                client,vendor,conn_id,version=source
                original=connection.execute(text('SELECT publication_id FROM commerce.catalog_heads WHERE client_id=:client AND vendor_id=:vendor'),{'client':client,'vendor':vendor}).scalar()
                actor,job_id=str(uuid4()),str(uuid4())
                connection.execute(text("INSERT INTO codebi.users(id,email,display_name,user_type) VALUES (:id,:email,'Rollback catalog verification','client')"),{'id':actor,'email':actor+'@example.invalid'})
                connection.execute(text("INSERT INTO codebi.client_memberships(client_id,user_id,role_code) VALUES (:client,:actor,'client_admin')"),{'client':client,'actor':actor})
                sessions=sessionmaker(connection,expire_on_commit=False,join_transaction_mode='create_savepoint')
                with sessions.begin() as db:
                    db.add(Job(id=job_id,client_id=str(client),vendor_id=str(vendor),connection_id=str(conn_id),
                        credential_version=version,requested_by=actor,kind='catalog',payload={'catalog_selection':{'mode':'all'}}))
                owner=connection.execute(text('SELECT current_user')).scalar()
                quoted=connection.dialect.identifier_preparer.quote(owner)
                connection.exec_driver_sql('GRANT commerce_worker TO '+quoted)
                connection.execute(text('SET LOCAL ROLE commerce_worker'))
                assert connection.execute(text('SELECT current_user')).scalar()=='commerce_worker'
                worker=Worker(connection,secret_reader=lambda _db,_job:'rollback-fixture-only')
                worker.sessions=sessions
                worker.heartbeat_seconds=60  # Fixture operations finish before this; no shared-connection thread I/O.
                fixture=FixtureClient();fixture.fetched=[]
                stage='interrupted run'
                with patch('commerce_hub.worker.connector',return_value=FixtureAdapter(fixture)):
                    assert worker.run_once(job_id=job_id)
                with sessions.begin() as db:
                    assert db.get(Job,job_id).status=='queued'
                    assert db.get(CatalogHead,(str(client),str(vendor))).publication_id==str(original)
                    done=set(db.scalars(select(CatalogWorkItem.source_path).where(CatalogWorkItem.job_id==job_id,CatalogWorkItem.product_id.is_not(None))))
                    assert done
                    db.get(Job,job_id).available_at=now()-timedelta(seconds=1)
                    db.get(VendorCatalogLease,str(vendor)).throttle_until=None
                before=list(fixture.fetched);fixture.fail=False
                stage='resumed run'
                with patch('commerce_hub.worker.connector',return_value=FixtureAdapter(fixture)):
                    assert worker.run_once(job_id=job_id)
                assert not done.intersection(fixture.fetched[len(before):])
                with sessions.begin() as db:
                    assert db.get(Job,job_id).status=='succeeded'
                    assert db.get(CatalogHead,(str(client),str(vendor))).publication_id==job_id
                    assert db.get(CatalogPublication,job_id).summary['record_count']==3
                print('Restricted PostgreSQL recovery verified: committed products skipped; incomplete product retried; publication atomic.')
            finally:
                outer.rollback()
            stage='rollback verification'
            restored=connection.execute(text('SELECT publication_id FROM commerce.catalog_heads WHERE client_id=:client AND vendor_id=:vendor'),{'client':client,'vendor':vendor}).scalar()
            assert restored==original
            assert connection.execute(text('SELECT count(*) FROM commerce.jobs WHERE id=:id'),{'id':job_id}).scalar()==0
            assert connection.execute(text('SELECT count(*) FROM codebi.users WHERE id=:id'),{'id':actor}).scalar()==0
            print('All fixtures rolled back. Published live catalog restored unchanged; no Vault or vendor access occurred.')
        return 0
    except Exception as exc:
        print('Catalog recovery verification unavailable at',stage+':',type(exc).__name__)
        state=getattr(getattr(exc,'orig',None),'sqlstate',None)
        if state:print('SQLSTATE:',state)
        return 1
    finally:
        if engine is not None:engine.dispose()


if __name__=='__main__':raise SystemExit(main())
