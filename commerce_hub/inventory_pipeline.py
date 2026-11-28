"""Whole-page commits, fenced recovery and atomic inventory publication via job success."""
from contextlib import closing
from decimal import Decimal
import hashlib
import json

from sqlalchemy import select,func
from commerce_hub.connector_contracts import ConnectorLoginError
from commerce_hub.models import Inventory,Job


def run_inventory(worker,claimed,adapter,url,credentials):
    from commerce_hub.worker import close_job
    limit=claimed.payload.get('inventory_page_limit')
    if limit is not None and (type(limit) is not int or limit not in (2,3)):
        raise ConnectorLoginError('invalid_inventory_scope')
    with worker.live(claimed) as (_db,job):
        saved=job.result.get('inventory',{})
        if saved and saved.get('connector_version')!=adapter.version:
            raise ConnectorLoginError('inventory_resume_configuration_changed')
        checkpoint=list(saved.get('pages',[]))
        newer=_db.scalar(select(Job.id).where(Job.connection_id==job.connection_id,Job.kind=='inventory',
            Job.status=='succeeded',Job.created_at>job.created_at,
            Job.payload['inventory_preview'].as_boolean().is_not(True)).limit(1))
        if newer:raise ConnectorLoginError('inventory_run_superseded_start_new_run')
    seen=set();captured=0;final=None;observed_pages=0
    stream=adapter.pages(url,credentials,lambda _p:worker.heartbeat(claimed,0),page_limit=limit)
    with closing(stream):
        for page in stream:
            number=page['page_number'];rows=page['rows'];observed_pages+=1
            if number!=observed_pages or page.get('available_items_only') is not True or len(rows)>100:
                raise ConnectorLoginError('inventory_page_invalid')
            ids=[row['inventory_id'] for row in rows]
            if len(ids)!=len(set(ids)) or seen.intersection(ids):
                raise ConnectorLoginError('inventory_duplicate_id_start_new_run')
            if any(Decimal(row['warehouse_quantity'])<=0 for row in rows):
                raise ConnectorLoginError('inventory_unavailable_item_returned')
            if not rows and not (number==1 and page['last_page']):
                raise ConnectorLoginError('inventory_unexpected_empty_page')
            seen.update(ids);captured+=len(rows)
            if captured>50000:raise ConnectorLoginError('inventory_record_limit_exceeded')
            digest=hashlib.sha256(json.dumps(ids,separators=(',',':')).encode()).hexdigest()
            marker={'page':number,'count':len(rows),'ids_sha256':digest,
                'values_sha256':hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
            if number<=len(checkpoint):
                if checkpoint[number-1]!=marker:
                    raise ConnectorLoginError('inventory_source_changed_start_new_run')
            else:
                with worker.live(claimed) as (db,job):
                    for row in rows:
                        db.add(Inventory(client_id=job.client_id,vendor_id=job.vendor_id,job_id=job.id,
                            source_key=row['inventory_id'],vendor_sku=row['inventory_id'],
                            attributes={**row,'name':row['description'],'quantity':row['warehouse_quantity']}))
                    checkpoint.append(marker)
                    job.result={'inventory':{'phase':'capturing','pages':checkpoint,'completed':captured,
                        'connector_version':adapter.version,'available_items_only':True,'page_limit':limit}}
                    job.progress=min(95,int(number/limit*95)) if limit else 0
            final=page
    # The browser must finish logout successfully before this transaction publishes.
    if not final or not (final['last_page'] or (limit and observed_pages==limit)) or len(checkpoint)!=observed_pages:
        raise ConnectorLoginError('inventory_incomplete_run')
    with worker.live(claimed) as (db,job):
        actual=db.scalar(select(func.count()).select_from(Inventory).where(Inventory.job_id==job.id))
        if actual!=captured:raise ConnectorLoginError('inventory_record_count_mismatch')
        job.result={'record_count':captured,'connector_version':adapter.version,'inventory':{
            'phase':'published','pages':checkpoint,'completed':captured,'total':captured,
            'connector_version':adapter.version,'available_items_only':True,'page_limit':limit,
            'coverage':'all' if final['last_page'] else 'limited','page_count':observed_pages}}
        close_job(job,'succeeded');job.progress=100
