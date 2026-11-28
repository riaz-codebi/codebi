"""Durable whole-product capture and atomic catalog publication."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone, timedelta
import hashlib
import json
import time

from sqlalchemy import select, func

from .models import CatalogWorkItem, CatalogPublication, CatalogPublicationItem, CatalogHead, Product, now
from .vendors.shiloh_tack.catalog_client import CatalogError
from .vendors.shiloh_tack.search import categories


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def category_ids(attributes):
    # Breadcrumb names identify IDs only when the vendor list is unambiguous.
    names=defaultdict(list)
    for option in categories():
        names[option['label'].lstrip('\u00bb >').casefold()].append(option['value'])
    return {names[name.casefold()][0] for name in attributes.get('category_levels',[])
            if len(names[name.casefold()])==1}


def statistics(products):
    records=[p.attributes for p in products]
    tabs=Counter(label for r in records for label in {t.get('label','Unresolved') for t in r.get('tabs',[])})
    images=Counter(len(r.get('images',[])) for r in records)
    paths=Counter(r.get('category_path') or 'Unclassified' for r in records)
    return {'record_count':len(records),'image_count':sum(len(r.get('images',[])) for r in records),
            'image_distribution':dict(images),'tab_labels':dict(tabs),'category_paths':dict(paths),
            'tab_count_distribution':dict(Counter(len({t.get('label') for t in r.get('tabs',[])}) for r in records)),
            'block_types':dict(Counter(b.get('type','unresolved') for r in records for t in r.get('tabs',[]) for b in t.get('blocks',[]))),
            'products_with_quantity_pricing':sum(bool(r.get('quantity_pricing')) for r in records),
            'stock_notices':sum(bool(r.get('stock_notice')) for r in records),
            'category_coverage':sum(bool(r.get('category_path')) for r in records),
            'products_with_dropdowns':sum(bool(r.get('dropdowns')) for r in records),
            'unresolved_option_labels':sum(not d.get('label') for r in records for d in r.get('dropdowns',[])),
            'unresolved_tabs':sum(t.get('label','').startswith('Unmapped') for r in records for t in r.get('tabs',[]))}


class CatalogRunner:
    pause_seconds=120
    batch_size=1000

    def __init__(self, worker, claimed, adapter, url, credentials, selection):
        self.worker,self.claimed,self.adapter=worker,claimed,adapter
        self.url,self.credentials=url,credentials
        self.selection=selection or {'mode':'all'}
        self.meta={}

    def check(self):
        self.worker.heartbeat(self.claimed)

    def save_meta(self):
        with self.worker.live(self.claimed) as (_db,job):
            job.result={**job.result,'catalog':dict(self.meta)}

    def run(self):
        with self.worker.live(self.claimed) as (_db,job):
            self.meta=dict(job.result.get('catalog',{}))
            if self.meta and (self.meta.get('connector_version')!=self.adapter.version or self.meta.get('selection')!=self.selection):
                raise CatalogError('catalog_resume_configuration_changed')
            if not self.meta:
                self.meta={'connector_version':self.adapter.version,'selection':self.selection,'phase':'discovering',
                           'next_page':1,'pages':[],'discovery_complete':False,'next_pause_after':self.batch_size}
        self.save_meta()
        try:
            self.pause()  # Honor a persisted batch break before any new vendor login/read.
            with self.adapter.open_catalog(self.url,self.credentials,self.selection,self.check) as client:
                self.discover(client)
                self.validate_edges(client)
                self.capture(client)
                self.validate_edges(client)
            self.publish()
        finally:
            self.credentials=None

    def discover(self, client):
        if self.meta['discovery_complete']:
            return
        with self.worker.live(self.claimed) as (db,_job):
            seen=set(db.scalars(select(CatalogWorkItem.source_path).where(CatalogWorkItem.job_id==self.claimed.id)))
        if self.meta['pages']:
            self.validate_edges(client,first_only=True)
        while self.meta['next_page'] is not None:
            self.check()
            number=self.meta['next_page']
            if number>500:
                raise CatalogError('catalog_page_limit_reached')
            page=client.listing(number)
            paths=page['paths']
            if not paths or len(paths)!=len(set(paths)) or seen.intersection(paths):
                raise CatalogError('catalog_listing_changed_or_empty')
            if len(seen)+len(paths)>10000:
                raise CatalogError('catalog_product_limit_reached')
            info={'page':number,'count':len(paths),'signature':digest(paths)}
            if number==1:
                self.meta['initial_last_page']=page['last_page_hint']
                last=client.listing(page['last_page_hint']) if page['last_page_hint']!=1 else page
                self.meta['initial_last_signature']=digest(last['paths'])
            self.meta['pages'].append(info)
            self.meta['next_page']=page['next_page']
            self.meta['discovery_complete']=page['next_page'] is None
            self.meta['total']=len(seen)+len(paths)
            with self.worker.live(self.claimed) as (db,job):
                for offset,path in enumerate(paths,1):
                    db.add(CatalogWorkItem(job_id=job.id,ordinal=len(seen)+offset,client_id=job.client_id,
                        vendor_id=job.vendor_id,source_path=path))
                job.result={**job.result,'catalog':dict(self.meta)}
            seen.update(paths)
        self.meta['total']=len(seen)
        self.meta['phase']='capturing'
        self.save_meta()

    def validate_edges(self, client,first_only=False):
        if not self.meta.get('pages'):
            return
        first=client.listing(1)
        if digest(first['paths'])!=self.meta['pages'][0]['signature'] or first['last_page_hint']!=self.meta['initial_last_page']:
            raise CatalogError('catalog_source_changed_start_new_run')
        if not first_only:
            last=client.listing(self.meta['initial_last_page']) if self.meta['initial_last_page']!=1 else first
            if digest(last['paths'])!=self.meta['initial_last_signature']:
                raise CatalogError('catalog_source_changed_start_new_run')

    def pause(self):
        until=self.meta.get('pause_until')
        if until:
            remaining=(datetime.fromisoformat(until)-now()).total_seconds()
            while remaining>0:
                self.check()
                time.sleep(min(.2,remaining))
                remaining=(datetime.fromisoformat(until)-now()).total_seconds()
            self.meta.pop('pause_until',None)
            self.meta['phase']='capturing'
            self.save_meta()

    def capture(self, client):
        self.pause()
        with self.worker.live(self.claimed) as (db,_job):
            pending=list(db.scalars(select(CatalogWorkItem).where(CatalogWorkItem.job_id==self.claimed.id,
                CatalogWorkItem.product_id.is_(None)).order_by(CatalogWorkItem.ordinal)))
            completed=db.scalar(select(func.count()).select_from(CatalogWorkItem).where(
                CatalogWorkItem.job_id==self.claimed.id,CatalogWorkItem.product_id.is_not(None)))
        self.meta['resumed_completed']=completed
        if not pending:
            return
        index=0
        with ThreadPoolExecutor(max_workers=2) as pool:
            active={}
            while index<len(pending) or active:
                self.check()
                if completed>=self.meta['next_pause_after'] and not active:
                    self.meta['next_pause_after']+=self.batch_size
                    self.meta['pause_until']=(now()+timedelta(seconds=self.pause_seconds)).isoformat()
                    self.meta['phase']='batch_break'
                    self.save_meta()
                    self.pause()
                while len(active)<2 and index<len(pending) and completed+len(active)<self.meta['next_pause_after']:
                    item=pending[index];index+=1
                    active[pool.submit(client.product,item.source_path)]=item
                if not active:
                    continue
                done,_=wait(active,timeout=.2,return_when=FIRST_COMPLETED)
                for future in done:
                    item=active.pop(future)
                    record=future.result()
                    self.commit_product(item,record,completed+1)
                    completed+=1
        self.meta['completed']=completed
        self.meta['phase']='validating'
        self.save_meta()

    def commit_product(self, item, record, completed):
        if not record.source_key or not isinstance(record.attributes,dict) or len(json.dumps(record.attributes,allow_nan=False).encode())>1024*1024:
            raise CatalogError('catalog_invalid_product')
        with self.worker.live(self.claimed) as (db,job):
            work=db.get(CatalogWorkItem,(job.id,item.ordinal))
            if work.product_id:
                return
            product=db.scalar(select(Product).where(Product.job_id==job.id,Product.source_key==record.source_key))
            if product is None:
                product=Product(client_id=job.client_id,vendor_id=job.vendor_id,job_id=job.id,
                    source_key=record.source_key,vendor_sku=record.vendor_sku,source_url=record.source_url,attributes=record.attributes)
                db.add(product);db.flush()
            elif digest(product.attributes)!=digest(record.attributes):
                raise CatalogError('catalog_conflicting_product_identity')
            work.product_id,work.completed_at=product.id,now()
            job.progress=min(95,10+int(85*completed/max(1,self.meta['total'])))

    def publish(self):
        from .worker import close_job
        with self.worker.live(self.claimed) as (db,job):
            total=db.scalar(select(func.count()).select_from(CatalogWorkItem).where(CatalogWorkItem.job_id==job.id))
            unfinished=db.scalar(select(func.count()).select_from(CatalogWorkItem).where(
                CatalogWorkItem.job_id==job.id,CatalogWorkItem.product_id.is_(None)))
            products=list(db.scalars(select(Product).where(Product.job_id==job.id)))
            if unfinished or total!=self.meta.get('total') or not products or not self.meta['discovery_complete']:
                raise CatalogError('catalog_incomplete_cannot_publish')
            head=db.get(CatalogHead,(job.client_id,job.vendor_id))
            previous={}
            previous_coverage='partial'
            if head:
                previous={i.source_key:i for i in db.scalars(select(CatalogPublicationItem).where(
                    CatalogPublicationItem.publication_id==head.publication_id))}
                previous_coverage=db.get(CatalogPublication,head.publication_id).summary.get('coverage','partial')
            selected={p.source_key:p for p in products}
            old_ids=[previous[key].product_id for key in selected if key in previous]
            old_products={p.id:p for p in db.scalars(select(Product).where(Product.id.in_(old_ids)))} if old_ids else {}
            merged={key:(i.product_id,set(i.category_ids)) for key,i in previous.items()} if self.selection['mode']=='category' else {}
            membership_removed=0
            if self.selection['mode']=='category':
                cat=self.selection['category_id']
                for key,(_pid,memberships) in merged.items():
                    if key not in selected and cat in memberships:
                        memberships.discard(cat);membership_removed+=1
            for key,product in selected.items():
                memberships=category_ids(product.attributes)
                if self.selection['mode']=='category' and key in previous:
                    memberships.update(previous[key].category_ids)
                if self.selection['mode']=='category':
                    memberships.add(self.selection['category_id'])
                merged[key]=(product.id,memberships)
            summary=statistics(products)
            summary.update({'current_product_count':len(merged),'new_products':sum(k not in previous for k in selected),
                'changed_products':sum(k in previous and digest(p.attributes)!=digest(old_products[previous[k].product_id].attributes) for k,p in selected.items()),
                'removed_products':len(set(previous)-set(selected)) if self.selection['mode']=='all' else 0,
                'category_memberships_removed':membership_removed,
                'coverage':'all' if self.selection['mode']=='all' or previous_coverage=='all' else 'partial'})
            elapsed=max(0,(now()-job.started_at.replace(tzinfo=timezone.utc) if job.started_at.tzinfo is None else now()-job.started_at).total_seconds())
            summary['elapsed_seconds']=round(elapsed,3)
            db.add(CatalogPublication(job_id=job.id,client_id=job.client_id,vendor_id=job.vendor_id,
                selection=self.selection,summary=summary));db.flush()
            for key,(product_id,memberships) in merged.items():
                db.add(CatalogPublicationItem(publication_id=job.id,source_key=key,client_id=job.client_id,
                    vendor_id=job.vendor_id,product_id=product_id,category_ids=sorted(memberships)))
            db.flush()
            if head:
                head.publication_id=job.id
            else:
                db.add(CatalogHead(client_id=job.client_id,vendor_id=job.vendor_id,publication_id=job.id))
            self.meta['phase']='published'
            job.result={**job.result,'catalog':dict(self.meta),'record_count':len(products),
                'connector_version':self.adapter.version,'catalog_selection':self.selection,'summary':summary}
            close_job(job,'succeeded');job.progress=100
