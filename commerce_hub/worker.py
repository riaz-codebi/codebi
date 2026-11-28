"""PostgreSQL-backed, leased jobs. Run: python -m commerce_hub.worker --once."""
import argparse
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
import time
from uuid import uuid4

from dotenv import dotenv_values
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from app.models import Client, Membership, User, UserRole

from commerce_hub.connector_contracts import ConnectorNotConfigured, Credentials, ConnectorLoginError
from commerce_hub.connector_registry import connector
from commerce_hub.export_files import csv_file, xlsx_file
from commerce_hub.export_mapping import MappingError, map_row, match_skus
from commerce_hub.models import ClientAppAccess, CommercialApp, DestinationTemplate, Export, Inventory, Job, MappingRule, Product, Vendor, VendorConnection, now
from commerce_hub.artifact_storage import StorageUnavailable, upload
from commerce_hub.job_heartbeat import JobHeartbeat
from commerce_hub.product_identity import catalog_sku
from commerce_hub.models import VendorCatalogLease
from commerce_hub.vendors.shiloh_tack.catalog_client import CatalogError, CatalogRetryable

FEATURE={'test_connection':'connections','catalog':'catalog','inventory':'inventory','export_vendor':'vendor_export','export_shopify':'shopify_export'}
log=logging.getLogger('commerce_worker')


class LeaseLost(Exception):
    pass


class Blocked(Exception):
    pass


def authorized(db,job):
    app=db.get(CommercialApp,'commerce_hub')
    access=db.get(ClientAppAccess,(job.client_id,'commerce_hub'))
    if not app or not app.is_active or not access or not access.is_active or FEATURE[job.kind] not in access.features:
        return False
    active=db.scalar(select(Client.is_active).where(Client.id==job.client_id))
    user=db.execute(select(User.is_active,User.user_type).where(User.id==job.requested_by)).first()
    if not active or not user or not user[0]:
        return False
    internal=user[1]=='internal' and db.scalar(select(UserRole.role_code).where(UserRole.user_id==job.requested_by,UserRole.role_code=='super_admin'))
    role=db.scalar(select(Membership.role_code).where(Membership.client_id==job.client_id,Membership.user_id==job.requested_by,Membership.is_active.is_(True)))
    if not internal and (role not in ('client_user','client_admin') or (job.kind=='test_connection' and role!='client_admin')):
        return False
    if job.connection_id:
        conn=db.get(VendorConnection,job.connection_id)
        if not conn or conn.client_id!=job.client_id or conn.vendor_id!=job.vendor_id or conn.status=='disabled' or conn.credential_version!=job.credential_version:
            return False
    return True


def close_job(job,status,error=None):
    job.status,job.error_code=status,error
    job.lease_token,job.lease_expires_at=None,None
    job.finished_at,job.updated_at=now(),now()


class Worker:
    def __init__(self,engine,settings=None,secret_reader=None,uploader=None):
        self.sessions=sessionmaker(engine,expire_on_commit=False)
        self.settings=settings or {}
        self.secret_reader=secret_reader
        self.uploader=uploader or upload
        self.lease_seconds=120
        self.heartbeat_seconds=15
        self._job_heartbeat=None

    def claim(self,job_id=None):
        with self.sessions.begin() as db:
            expired_query=select(Job).where(Job.status=='running',Job.lease_expires_at<now())
            if job_id is not None:
                expired_query=expired_query.where(Job.id==job_id)
            expired=db.scalars(expired_query.with_for_update(skip_locked=True).limit(20)).all()
            for job in expired:
                if job.attempts>=job.max_attempts:
                    close_job(job,'failed','lease_expired')
                else:
                    job.status,job.lease_token,job.lease_expires_at='queued',None,None
                    job.available_at=now()+timedelta(seconds=2**job.attempts)
                    job.error_code='lease_expired'
            db.flush()
            query=select(Job).where(Job.status=='queued',Job.available_at<=now())
            if job_id is not None:
                query=query.where(Job.id==job_id)
            job=db.scalar(query.order_by(Job.created_at).with_for_update(skip_locked=True).limit(1))
            if not job:
                return None
            if not authorized(db,job):
                close_job(job,'cancelled','access_or_configuration_changed')
                return None
            configured=db.get(VendorConnection,job.connection_id) if job.connection_id else None
            catalog_lock=job.kind=='catalog' and configured.config.get('catalog_pipeline_enabled') is True
            lease=None
            if catalog_lock:
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert
                insert=pg_insert if db.bind.dialect.name=='postgresql' else sqlite_insert
                db.execute(insert(VendorCatalogLease).values(vendor_id=job.vendor_id).on_conflict_do_nothing())
                lease=db.scalar(select(VendorCatalogLease).where(VendorCatalogLease.vendor_id==job.vendor_id).with_for_update())
                def future(value):
                    if value is None:return False
                    from datetime import timezone
                    return value.replace(tzinfo=timezone.utc)>now() if value.tzinfo is None else value>now()
                if future(lease.throttle_until):
                    return None
                owner=db.get(Job,lease.job_id) if lease.job_id else None
                if owner and owner.status=='running' and future(owner.lease_expires_at) and owner.lease_token==lease.lease_token:
                    return None
            job.status,job.lease_token='running',str(uuid4())
            job.lease_expires_at=now()+timedelta(seconds=self.lease_seconds)
            job.attempts+=1
            job.started_at,job.updated_at=job.started_at or now(),now()
            job.finished_at=None
            if lease is not None:
                lease.job_id,lease.lease_token,lease.lease_expires_at=job.id,job.lease_token,job.lease_expires_at
                job._vendor_catalog_lock=True
            db.flush()
            return job

    @contextmanager
    def live(self,claimed,check_access=True):
        if check_access and self._job_heartbeat is not None:
            self._job_heartbeat.check()
        with self.sessions.begin() as db:
            job=db.scalar(select(Job).where(Job.id==claimed.id,Job.lease_token==claimed.lease_token,
                Job.status=='running',Job.lease_expires_at>now()).with_for_update())
            if not job or (check_access and not authorized(db,job)):
                raise LeaseLost
            yield db,job

    def heartbeat(self,claimed,progress=0):
        if self._job_heartbeat is not None:
            self._job_heartbeat.update(progress)
        else:
            self._renew_lease(claimed,progress)

    def _renew_lease(self,claimed,progress=0):
        with self.live(claimed) as (_db,job):
            if getattr(claimed,'_vendor_catalog_lock',False):
                lease=_db.scalar(select(VendorCatalogLease).where(VendorCatalogLease.vendor_id==job.vendor_id,
                    VendorCatalogLease.job_id==job.id,VendorCatalogLease.lease_token==claimed.lease_token).with_for_update())
                if lease is None:
                    raise LeaseLost
                lease.lease_expires_at=now()+timedelta(seconds=self.lease_seconds)
            job.lease_expires_at=now()+timedelta(seconds=self.lease_seconds)
            job.progress=max(job.progress,max(0,min(99,int(progress))))
            job.updated_at=now()

    def finish_error(self,claimed,code,status='blocked',retry=False,retry_after=0):
        try:
            with self.live(claimed,check_access=False) as (db,job):
                if not authorized(db,job):
                    close_job(job,'cancelled','access_or_configuration_changed')
                elif retry and job.attempts<job.max_attempts:
                    job.status,job.lease_token,job.lease_expires_at='queued',None,None
                    job.available_at=now()+timedelta(seconds=max(retry_after,min(60,2**job.attempts)))
                    job.error_code=code
                else:
                    close_job(job,status,code)
                    if job.kind == 'test_connection' and job.connection_id:
                        connection = db.get(VendorConnection,job.connection_id)
                        connection.status,connection.last_error_code = 'error',code
                        connection.last_verified_at,connection.updated_at = None,now()
        except LeaseLost:
            pass

    def source_job(self,db,job,key,kind):
        value=job.payload.get(key)
        try:
            from uuid import UUID
            value=str(UUID(str(value)))
        except ValueError:
            raise Blocked('invalid_export_inputs') from None
        source=db.scalar(select(Job).where(Job.id==value,Job.client_id==job.client_id,
            Job.vendor_id==job.vendor_id,Job.kind==kind,Job.status=='succeeded'))
        if not source:
            raise Blocked('invalid_export_inputs')
        return source

    def extraction(self,claimed):
        with self.live(claimed) as (db,job):
            conn=db.get(VendorConnection,job.connection_id)
            vendor=db.get(Vendor,job.vendor_id)
            adapter=connector(vendor.code,conn.connection_type)
            preview=job.kind=='inventory' and job.payload.get('inventory_preview') is True
            selection=None
            if job.kind=='catalog' and 'catalog_selection' in job.payload:
                from commerce_hub.vendors.shiloh_tack.search import catalog_selection
                chosen=job.payload['catalog_selection']
                try:
                    if vendor.code!='shiloh_tack' or not isinstance(chosen,dict):
                        raise ValueError
                    selection=catalog_selection(chosen.get('mode'),chosen.get('category_id',''))
                except ValueError:
                    raise Blocked('invalid_catalog_selection') from None
            if not adapter.implemented or (preview and not getattr(adapter,'preview_implemented',False)) or (not preview and job.kind != 'test_connection' and not getattr(adapter,'extraction_implemented',True)):
                raise ConnectorNotConfigured
            if job.kind=='catalog' and getattr(adapter,'catalog_pipeline',False) is True and conn.config.get('catalog_pipeline_enabled') is not True:
                raise ConnectorNotConfigured
            if not conn.portal_url or not conn.username or not conn.credential_ref:
                raise Blocked('connection_not_configured')
            password=self.secret_reader(db,job) if self.secret_reader else db.execute(text('SELECT commerce.worker_connection_secret(:job,:lease)'),{'job':job.id,'lease':job.lease_token}).scalar()
            credentials=Credentials(conn.username,password)
            url=conn.portal_url
        # Never keep a database transaction open across vendor/browser operations.
        if claimed.kind=='test_connection':
            sample=None
            if getattr(adapter,'test_sample_implemented',False) is True:
                try:
                    sample=adapter.test_sample(url,credentials,lambda p:self.heartbeat(claimed,p))
                except ConnectorLoginError as exc:
                    diagnostic=getattr(exc,'grid_snapshot',None)
                    if diagnostic and len(json.dumps(diagnostic,allow_nan=False).encode())<=512*1024:
                        with self.live(claimed) as (_db,job):job.result={'sample_diagnostic':diagnostic}
                    raise
                if (not isinstance(sample,dict) or sample.get('page_limit')!=1 or sample.get('pagination_followed') is not False
                    or sample.get('connection_type')!=conn.connection_type or not isinstance(sample.get('rows'),list)
                    or not 1<=len(sample['rows'])<=100 or sample.get('row_count')!=len(sample['rows'])
                    or len(json.dumps(sample,allow_nan=False).encode())>2*1024*1024):
                    raise Blocked('connection_sample_invalid')
            else:
                adapter.verify(url,credentials)
            with self.live(claimed) as (db,job):
                # Legacy/future adapters can retain a login-only verification.
                # Shiloh adapters always perform the combined read above.
                if sample is not None:job.result={'connection_sample':sample,'connector_version':adapter.version}
                conn=db.get(VendorConnection,job.connection_id)
                conn.status,conn.last_verified_at,conn.last_error_code='verified',now(),None
                close_job(job,'succeeded')
                job.progress=100
            return
        if preview:
            try:
                result=adapter.preview_first_page(url,credentials,lambda p:self.heartbeat(claimed,p))
            except ConnectorLoginError as exc:
                diagnostic=getattr(exc,'grid_snapshot',None)
                blocked=getattr(exc,'blocked_callbacks',None)
                if diagnostic or blocked:
                    safe={'page_limit':1,'complete_inventory':False,'grid_snapshot':diagnostic,'blocked_read_callbacks':blocked or []}
                    if len(json.dumps(safe,allow_nan=False).encode())<=512*1024:
                        with self.live(claimed) as (_db,job):job.result={'inventory_preview':safe}
                raise
            if (not isinstance(result,dict) or result.get('page_limit')!=1 or result.get('pagination_followed') is not False
                or result.get('complete_inventory') is not False or not isinstance(result.get('rows'),list)
                or not 1<=len(result['rows'])<=100 or len(json.dumps(result,allow_nan=False).encode())>512*1024):
                raise Blocked('inventory_preview_limit_exceeded')
            with self.live(claimed) as (_db,job):
                job.result={'inventory_preview':result,'record_count':len(result['rows']),'connector_version':adapter.version}
                close_job(job,'succeeded');job.progress=100
            return
        if claimed.kind=='inventory' and getattr(adapter,'inventory_pipeline',False) is True:
            from commerce_hub.inventory_pipeline import run_inventory
            run_inventory(self,claimed,adapter,url,credentials)
            return
        if claimed.kind=='catalog' and getattr(adapter,'catalog_pipeline',False) is True:
            from commerce_hub.catalog_pipeline import CatalogRunner
            CatalogRunner(self,claimed,adapter,url,credentials,selection).run()
            return
        records=[]
        keys=set()
        byte_count=0
        extract_options={'selection':selection} if selection is not None else {}
        for record in adapter.extract(url,credentials,lambda p=0:self.heartbeat(claimed,p),**extract_options):
            if self._job_heartbeat is not None:
                self._job_heartbeat.check()
            if not record.source_key or record.source_key in keys or not isinstance(record.attributes,dict):
                raise Blocked('invalid_or_duplicate_source_record')
            keys.add(record.source_key)
            byte_count+=len(json.dumps(record.attributes,allow_nan=False).encode())
            if len(records)>=10000 or byte_count>32*1024*1024:
                raise Blocked('extraction_limit_reached')
            records.append(record)
        with self.live(claimed) as (db,job):
            model=Product if job.kind=='catalog' else Inventory
            for record in records:
                values={'client_id':job.client_id,'vendor_id':job.vendor_id,'job_id':job.id,
                    'source_key':record.source_key,'vendor_sku':record.vendor_sku,'attributes':record.attributes}
                if model is Product:
                    values['source_url']=record.source_url
                db.add(model(**values))
            job.result={'record_count':len(records),'connector_version':adapter.version}
            if selection is not None:
                job.result['catalog_selection']=selection
            close_job(job,'succeeded')
            job.progress=100

    def export(self,claimed):
        with self.live(claimed) as (db,job):
            catalog=self.source_job(db,job,'catalog_job_id','catalog')
            inventory=self.source_job(db,job,'inventory_job_id','inventory') if job.payload.get('inventory_job_id') else None
            vendor=db.get(Vendor,catalog.vendor_id)
            products=[{'source_key':p.source_key,'vendor_sku':catalog_sku(p.source_key,p.vendor_sku,vendor.code),'source_url':p.source_url,'attributes':p.attributes} for p in db.scalars(select(Product).where(Product.client_id==job.client_id,Product.job_id==catalog.id).order_by(Product.source_key)).all()]
            inventory_rows=[{'source_key':p.source_key,'vendor_sku':p.vendor_sku,'attributes':p.attributes} for p in db.scalars(select(Inventory).where(Inventory.client_id==job.client_id,Inventory.job_id==inventory.id).order_by(Inventory.source_key)).all()] if inventory else []
            if not products or len(products)>10000 or len(inventory_rows)>10000:
                raise Blocked('missing_or_excessive_source_records')
            mapping=template=None
            if job.kind=='export_shopify':
                mapping=db.scalar(select(MappingRule).where(MappingRule.id==job.payload.get('mapping_id'),MappingRule.client_id==job.client_id,MappingRule.vendor_id==job.vendor_id,MappingRule.is_active.is_(True)))
                template=db.get(DestinationTemplate,mapping.template_id) if mapping else None
                if not mapping or not template or not template.is_active or not inventory:
                    raise Blocked('mapping_not_configured')
                conns=[db.get(VendorConnection,r.connection_id) for r in (catalog,inventory)]
                if any(not c or c.config.get('sku_grain') not in ('product','variant') for c in conns) or conns[0].config['sku_grain']!=conns[1].config['sku_grain']:
                    raise Blocked('sku_semantics_unverified')
                columns,rules=template.columns,mapping.rules
            fmt=job.payload.get('format','xlsx')
            if fmt not in ('csv','xlsx') or (job.kind=='export_shopify' and fmt!='csv'):
                raise Blocked('invalid_export_format')
            manifest={'catalog_job_id':catalog.id,'inventory_job_id':inventory.id if inventory else None,
                'mapping_id':mapping.id if mapping else None,'template_id':template.id if template else None,
                'template_sha256':template.source_sha256 if template else None,'exporter_version':1}
            if 'catalog_selection' in catalog.payload:
                manifest['catalog_selection']=catalog.payload['catalog_selection']
        self._renew_lease(claimed,20)
        if mapping:
            pairs,errors=match_skus(products,inventory_rows)
            if errors:
                raise Blocked('sku_matching_requires_review')
            rows=[map_row(p,i,columns,rules) for p,i in pairs]
            content=csv_file([c['name'] for c in columns],rows)
            count=len(rows)
        else:
            # Raw source JSON remains intact; no Shopify columns are assumed.
            pcols=['source_key','vendor_sku','source_url','original_attributes_json']
            prows=[{**p,'original_attributes_json':json.dumps(p['attributes'],ensure_ascii=False)} for p in products]
            icols=['source_key','vendor_sku','original_attributes_json']
            irows=[{**r,'original_attributes_json':json.dumps(r['attributes'],ensure_ascii=False)} for r in inventory_rows]
            content=xlsx_file([('Products',pcols,prows),('Inventory',icols,irows)]) if fmt=='xlsx' else csv_file(pcols,prows)
            count=len(prows)
        # Preserve the explicit authorization/ownership check before uploading.
        self._renew_lease(claimed,80)
        path=f'{claimed.client_id}/exports/{claimed.id}/{claimed.lease_token}.{fmt}'
        self.uploader(self.settings,path,content,'text/csv' if fmt=='csv' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        with self.live(claimed) as (db,job):
            db.add(Export(client_id=job.client_id,vendor_id=job.vendor_id,job_id=job.id,
                catalog_job_id=manifest['catalog_job_id'],inventory_job_id=manifest['inventory_job_id'],
                mapping_id=manifest['mapping_id'],template_id=manifest['template_id'],format=fmt,
                artifact_path=path,sha256=hashlib.sha256(content).hexdigest(),row_count=count,manifest=manifest))
            job.result={'row_count':count,'format':fmt}
            close_job(job,'succeeded')
            job.progress=100

    def run_once(self,job_id=None):
        claimed=self.claim(job_id)
        if not claimed:
            return False
        try:
            if not 0 < self.heartbeat_seconds < self.lease_seconds:
                raise ValueError('Heartbeat interval must be shorter than the lease.')
            heartbeat=JobHeartbeat(lambda p:self._renew_lease(claimed,p),LeaseLost,self.heartbeat_seconds)
            self._job_heartbeat=heartbeat
            try:
                heartbeat.start()
                if claimed.kind in ('export_vendor','export_shopify'):
                    self.export(claimed)
                else:
                    self.extraction(claimed)
            finally:
                heartbeat.close()
                self._job_heartbeat=None
        except ConnectorNotConfigured:
            self.finish_error(claimed,'connector_not_implemented')
        except ConnectorLoginError as exc:
            self.finish_error(claimed,exc.code)
        except CatalogRetryable as exc:
            if claimed.kind=='inventory' and hasattr(exc,'safe_diagnostic'):
                try:
                    with self.live(claimed,check_access=False) as (_db,job):
                        job.result={**job.result,'diagnostic':exc.safe_diagnostic}
                except LeaseLost:pass
            if getattr(claimed,'_vendor_catalog_lock',False):
                with self.sessions.begin() as db:
                    lease=db.scalar(select(VendorCatalogLease).where(VendorCatalogLease.vendor_id==claimed.vendor_id,
                        VendorCatalogLease.job_id==claimed.id,VendorCatalogLease.lease_token==claimed.lease_token).with_for_update())
                    if lease:
                        lease.throttle_until=now()+timedelta(seconds=exc.retry_after)
            self.finish_error(claimed,exc.code,status='failed',retry=True,retry_after=exc.retry_after)
        except CatalogError as exc:
            self.finish_error(claimed,exc.code)
        except Blocked as exc:
            self.finish_error(claimed,str(exc))
        except MappingError:
            self.finish_error(claimed,'mapping_validation_failed')
        except StorageUnavailable:
            self.finish_error(claimed,'storage_not_configured_or_unavailable')
        except LeaseLost:
            self.finish_error(claimed,'access_or_configuration_changed',status='cancelled')
        except Exception as exc:
            # Exception text, payloads, credentials and URLs must never be logged.
            log.warning('Commerce job %s failed (%s)',claimed.id,type(exc).__name__)
            self.finish_error(claimed,'worker_error',status='failed',retry=True)
        finally:
            if getattr(claimed,'_vendor_catalog_lock',False):
                try:
                    with self.sessions.begin() as db:
                        lease=db.scalar(select(VendorCatalogLease).where(VendorCatalogLease.vendor_id==claimed.vendor_id,
                            VendorCatalogLease.job_id==claimed.id,VendorCatalogLease.lease_token==claimed.lease_token).with_for_update())
                        if lease:
                            lease.job_id=lease.lease_token=lease.lease_expires_at=None
                except Exception as exc:
                    log.warning('Catalog lease release unavailable (%s)',type(exc).__name__)
        return True


def engine_from_url(value):
    url=make_url(value)
    if (url.username or '').split('.')[0]!='commerce_worker' or url.drivername not in ('postgres','postgresql','postgresql+psycopg'):
        raise ValueError('Use the restricted commerce_worker PostgreSQL connection.')
    engine=create_engine(url.set(drivername='postgresql+psycopg'),pool_size=1,max_overflow=0,
        pool_pre_ping=True,hide_parameters=True,connect_args={'sslmode':'require','connect_timeout':10})
    @event.listens_for(engine,'connect')
    def guard(connection,_record):
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,pg_has_role(current_user,'postgres','MEMBER'),has_schema_privilege(current_user,'commerce','CREATE') FROM pg_roles WHERE rolname=current_user")
            row=cursor.fetchone()
            if not row or row[0]!='commerce_worker' or any(row[1:]):
                raise ValueError('Refusing a privileged worker database role.')
        connection.rollback()
    return engine


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    settings={**dotenv_values(Path(__file__).resolve().parents[1]/'.env'),**os.environ}
    if not settings.get('COMMERCE_WORKER_DATABASE_URL'):
        parser.error('COMMERCE_WORKER_DATABASE_URL is required; run the setup helper.')
    engine=engine_from_url(settings['COMMERCE_WORKER_DATABASE_URL'])
    worker=Worker(engine,settings)
    try:
        while True:
            try:
                processed=worker.run_once()
            except Exception as exc:
                log.warning('Commerce worker unavailable (%s)',type(exc).__name__)
                if args.once:
                    return 1
                processed=False
            if args.once:
                break
            if not processed:
                time.sleep(3)
    except KeyboardInterrupt:
        pass
    finally:
        engine.dispose()


if __name__=='__main__':
    raise SystemExit(main())
