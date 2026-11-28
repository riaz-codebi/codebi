import ipaddress
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from flask import current_app
from sqlalchemy import String, cast, func, or_, select, text

from app.database import transaction
from app.models import Client, Membership, User
from app.services import Forbidden, InvalidDetails, NotFound, active_user, audit, identifier, is_global
from commerce_hub.models import ClientAppAccess, CommercialApp, DestinationTemplate, Export, Inventory, Job, MappingRule, Product, Vendor, VendorConnection, now
from commerce_hub.models import CatalogHead, CatalogPublication, CatalogPublicationItem, CatalogWorkItem
from app.commerce.presentation import product_row, product_details, run_statistics

FEATURES = ('connections', 'catalog', 'inventory', 'vendor_export', 'shopify_export')
FEATURE_LABELS = {'connections': 'Connections', 'catalog': 'Shiloh catalog',
                  'inventory': 'Shiloh inventory', 'vendor_export': 'Vendor export',
                  'shopify_export': 'Shopify export'}
JOB_FEATURE = {'test_connection': 'connections', 'catalog': 'catalog', 'inventory': 'inventory',
               'export_vendor': 'vendor_export', 'export_shopify': 'shopify_export'}


def authorize(db, auth_id, client_id, feature=None, admin=False):
    user = active_user(db, auth_id)
    if not user:
        raise Forbidden
    internal = is_global(db, user)
    client = db.get(Client, identifier(client_id))
    member = db.scalar(select(Membership).where(Membership.client_id == client_id,
        Membership.user_id == user.id, Membership.is_active.is_(True)))
    if not client or not client.is_active or (not internal and (not member or member.role_code not in ('client_user', 'client_admin'))):
        raise Forbidden
    if admin and not internal and member.role_code != 'client_admin':
        raise Forbidden
    app = db.get(CommercialApp, 'commerce_hub')
    access = db.get(ClientAppAccess, (client.id, 'commerce_hub'))
    if not app or not app.is_active or not access or not access.is_active or (feature and feature not in access.features):
        raise Forbidden
    return user, client, internal, bool(internal or member.role_code == 'client_admin'), access


def registry(auth_id):
    with transaction() as db:
        user = active_user(db, auth_id)
        if not user:
            raise Forbidden
        internal = is_global(db, user)
        statement=select(Client, ClientAppAccess).outerjoin(ClientAppAccess,
            (ClientAppAccess.client_id == Client.id) & (ClientAppAccess.app_code == 'commerce_hub'))
        if not internal:
            statement=statement.where(Client.id.in_(select(Membership.client_id).where(Membership.user_id==user.id,Membership.is_active.is_(True))))
        rows = db.execute(statement.order_by(Client.name)).all()
        allowed = set(db.scalars(select(Membership.client_id).where(Membership.user_id == user.id, Membership.is_active.is_(True))))
        return [{'id': c.id, 'name': c.name, 'client_active': c.is_active,
                 'enabled': bool(a and a.is_active), 'features': a.features if a else []}
                for c, a in rows if internal or (c.id in allowed and c.is_active and a and a.is_active)]


def set_access(auth_id, client_id, enabled, features):
    if any(f not in FEATURES for f in features):
        raise InvalidDetails('Select valid Commerce Hub features.')
    with transaction() as db:
        user = active_user(db, auth_id)
        if not is_global(db, user):
            raise Forbidden
        client = db.get(Client, identifier(client_id))
        if not client:
            raise NotFound
        if enabled and not client.is_active:
            raise InvalidDetails('Activate this client before granting tool access.')
        item = db.get(ClientAppAccess, (client.id, 'commerce_hub'))
        if not item:
            item = ClientAppAccess(client_id=client.id, app_code='commerce_hub')
            db.add(item)
        item.is_active, item.features = enabled, sorted(set(features))
        item.updated_by, item.updated_at = user.id, now()
        vendor = db.scalar(select(Vendor).where(Vendor.code == 'shiloh_tack'))
        if enabled and vendor:
            for kind in ('catalog', 'inventory'):
                if not db.scalar(select(VendorConnection.id).where(VendorConnection.client_id == client.id,
                    VendorConnection.vendor_id == vendor.id, VendorConnection.connection_type == kind)):
                    db.add(VendorConnection(client_id=client.id, vendor_id=vendor.id, connection_type=kind,
                        created_by=user.id, config={'sku_grain': 'unknown'}))
        audit(db, user, client.id, 'commerce.access_changed', 'client', client.id, {'enabled': enabled, 'features': item.features})


def connection(db, client_id, connection_id, lock=False):
    query = select(VendorConnection).where(VendorConnection.id == identifier(connection_id), VendorConnection.client_id == client_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if not row:
        raise NotFound
    return row


def public_connection(row, vendor):
    from commerce_hub.connector_registry import connector
    from commerce_hub.connector_contracts import ConnectorNotConfigured
    try:
        adapter=connector(vendor.code,row.connection_type)
        ready=bool(adapter.implemented)
        extraction=ready and bool(getattr(adapter,'extraction_implemented',True))
        if getattr(adapter,'catalog_pipeline',False) is True:
            extraction=extraction and row.config.get('catalog_pipeline_enabled') is True
    except ConnectorNotConfigured:
        ready,extraction=False,False
    category_support=vendor.code=='shiloh_tack' and row.connection_type=='catalog'
    from commerce_hub.vendors.shiloh_tack.search import categories
    return {'id': row.id, 'type': row.connection_type, 'vendor': vendor.name,
            'portal_url': row.portal_url or '', 'username': row.username, 'status': row.status,
            'has_password': bool(row.credential_ref), 'verified_at': row.last_verified_at,
            'error_code': row.last_error_code, 'connector_ready': ready, 'extraction_ready':extraction,
            'preview_ready':bool(ready and getattr(adapter,'preview_implemented',False)),
            'category_selection_supported':category_support,
            'categories':categories() if category_support else []}


def job_identity(view,row,name,display_timezone):
    try:
        zone=ZoneInfo(display_timezone)
    except (ZoneInfoNotFoundError,ValueError,TypeError):
        zone=ZoneInfo('UTC')
    stamp=row.started_at or row.created_at
    if stamp.tzinfo is None:
        stamp=stamp.replace(tzinfo=timezone.utc)
    local=stamp.astimezone(zone)
    view.update(requested_by_name=name or 'Unknown user',run_date_display=local.strftime('%Y-%m-%d %H:%M'),
                run_date_iso=local.isoformat(),run_timezone=str(zone),run_date_kind='Started' if row.started_at else 'Queued')
    return view


def job_filters(values=None):
    values=values or {}
    period=values.get('job_period','')
    if period not in ('','1','7','30'):period=''
    try:page=max(1,min(1000000,int(values.get('job_page',1))))
    except (ValueError,TypeError):page=1
    return {'search':str(values.get('job_search','')).strip()[:200],'period':period,'page':page}


def _job_history(db,user,client,admin,values=None):
    filters=job_filters(values)
    mode=Job.payload['catalog_selection']['mode'].as_string()
    label=Job.payload['catalog_selection']['category_label'].as_string()
    stamp=func.coalesce(Job.started_at,Job.created_at)
    query=select(Job,User.display_name,VendorConnection.connection_type).outerjoin(User,User.id==Job.requested_by).outerjoin(
        VendorConnection,(VendorConnection.id==Job.connection_id) & (VendorConnection.client_id==Job.client_id)
        & (VendorConnection.vendor_id==Job.vendor_id)).where(Job.client_id==client.id)
    if filters['period']:
        query=query.where(stamp>=now()-timedelta(days=int(filters['period'])))
    if filters['search']:
        def pattern(value):return '%'+value.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        term=pattern(filters['search'])
        query=query.where(or_(User.display_name.ilike(term,escape='\\'),label.ilike(term,escape='\\'),
            func.replace(cast(Job.id,String),'-','').ilike(pattern(filters['search'].replace('-','')),escape='\\'),
            (Job.kind=='catalog') & or_(mode=='all',mode.is_(None)) & func.lower('All products').like(term.lower(),escape='\\')))
    total=db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    page=min(filters['page'],max(1,(total+24)//25))
    filters['page']=page
    rows=db.execute(query.order_by(stamp.desc(),Job.id.desc()).limit(25).offset((page-1)*25)).all()
    catalog_ids=[row.id for row,_name,_connection_type in rows if row.result.get('catalog')]
    counts=dict(db.execute(select(CatalogWorkItem.job_id,func.count()).where(
        CatalogWorkItem.job_id.in_(catalog_ids),CatalogWorkItem.product_id.is_not(None)).group_by(CatalogWorkItem.job_id)).all()) if catalog_ids else {}
    jobs=[]
    for row,name,connection_type in rows:
        view=job_identity(job_view(row,connection_type),row,name,client.timezone)
        view['may_resume']=admin or row.requested_by==user.id
        view['can_resume']=view['can_resume'] and view['may_resume']
        if row.result.get('catalog'):view['completed_products']=counts.get(row.id,0)
        jobs.append(view)
    return {'jobs':jobs,'job_filters':filters,
            'job_pagination':{'page':page,'total':total,'pages':max(1,(total+24)//25),
                'start':(page-1)*25+1 if total else 0,'end':min(page*25,total)}}


def job_history(auth_id,client_id,values=None):
    with transaction() as db:
        user,client,_internal,admin,_access=authorize(db,auth_id,client_id)
        return {'client':{'id':client.id,'name':client.name,'timezone':client.timezone},'admin':admin,
                **_job_history(db,user,client,admin,values)}


def overview(auth_id, client_id,filters=None):
    with transaction() as db:
        _user, client, internal, admin, access = authorize(db, auth_id, client_id)
        rows = db.execute(select(VendorConnection, Vendor).join(Vendor).where(VendorConnection.client_id == client.id)).all()
        history=_job_history(db,_user,client,admin,filters)
        return {'client': {'id': client.id, 'name': client.name,'timezone':client.timezone}, 'internal': internal, 'admin': admin,
                'features': access.features, 'connections': [public_connection(c, v) for c, v in rows],
                **history,
                'exports': [export_view(e) for e in db.scalars(select(Export).where(Export.client_id == client.id).order_by(Export.created_at.desc()).limit(25))]}


def connection_form(auth_id, client_id, connection_id):
    with transaction() as db:
        _user, client, internal, _admin, _access = authorize(db, auth_id, client_id, 'connections', admin=True)
        row = connection(db, client.id, connection_id)
        return {'client': {'id': client.id, 'name': client.name}, 'connection': public_connection(row, db.get(Vendor, row.vendor_id)), 'internal': internal,'features':_access.features}


def portal_url(value):
    if not value:
        return None, None
    try:
        p = urlsplit(value)
        host = (p.hostname or '').encode('idna').decode('ascii').lower()
        if (p.scheme != 'https' or p.username or p.password or p.query or p.fragment or p.port not in (None, 443)
            or '.' not in host or host.endswith(('.local', '.internal', '.localhost')) or len(value)>1000
            or any(ord(c)<33 for c in value)):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError
        return urlunsplit(('https', host, p.path or '/', '', '')), host
    except (ValueError, UnicodeError):
        raise InvalidDetails('Use an approved HTTPS hostname without credentials, query parameters or a custom port.') from None


def save_connection(auth_id, client_id, connection_id, username, password=None, url=None):
    if len(username)>254 or (password is not None and (len(password)>4096 or not password)):
        raise InvalidDetails('Check the username and replacement password.')
    with transaction() as db:
        user, client, internal, _admin, _access = authorize(db, auth_id, client_id, 'connections', admin=True)
        row = connection(db, client.id, connection_id, lock=True)
        if url is not None:
            if not internal:
                raise Forbidden
            new_url, host = portal_url(url.strip())
        else:
            new_url, host = row.portal_url, row.approved_host
        changed = row.username != username.strip() or new_url != row.portal_url
        row.username, row.portal_url, row.approved_host = username.strip(), new_url, host
        row.updated_by, row.updated_at = user.id, now()
        if changed:
            row.credential_version += 1 if row.credential_ref else 0
            row.status, row.last_verified_at, row.last_error_code = 'unverified', None, None
        db.flush()
        if password is not None:
            writer = current_app.extensions.get('commerce_test_secret_writer') if current_app.testing else None
            if writer:
                writer(db, row, password)
            else:
                db.execute(text('SELECT commerce.store_connection_secret(:client,:connection,:password)'),
                    {'client': client.id, 'connection': row.id, 'password': password})
                db.refresh(row)
        if not (row.portal_url and row.username and row.credential_ref):
            row.status = 'not_configured'
        audit(db, user, client.id, 'commerce.connection_updated', 'vendor_connection', row.id,
              {'password_replaced': password is not None, 'configuration_changed': changed})


def enqueue_connection(auth_id, client_id, connection_id, kind, catalog_mode=None, category_id='',inventory_mode=None):
    with transaction() as db:
        user, client, _internal, _admin, _access = authorize(db, auth_id, client_id, JOB_FEATURE.get(kind), admin=kind=='test_connection')
        row = connection(db, client.id, connection_id)
        if kind not in ('test_connection', row.connection_type):
            raise InvalidDetails('Select a valid connection action.')
        if row.status == 'disabled' or not (row.portal_url and row.username and row.credential_ref):
            raise InvalidDetails('Configure the URL and credentials first.')
        payload={}
        if inventory_mode is not None:
            vendor=db.get(Vendor,row.vendor_id)
            if inventory_mode not in ('first_page','three_pages') or kind!='inventory' or row.connection_type!='inventory' or vendor.code!='shiloh_tack':
                raise InvalidDetails('Select a supported first-page inventory preview.')
            if inventory_mode=='first_page':payload['inventory_preview']=True
            else:
                authorize(db,auth_id,client.id,'inventory',admin=True)
                payload['inventory_page_limit']=3
        if catalog_mode is not None:
            vendor=db.get(Vendor,row.vendor_id)
            if kind!='catalog' or vendor.code!='shiloh_tack':
                raise InvalidDetails('Category selection is available for the Shiloh catalog only.')
            from commerce_hub.vendors.shiloh_tack.search import catalog_selection
            try:
                payload['catalog_selection']=catalog_selection(catalog_mode,category_id)
            except ValueError as exc:
                raise InvalidDetails(str(exc)) from None
        # Serialize enqueues per connection; SQL's partial unique index also closes races.
        row = connection(db, client.id, connection_id, lock=True)
        existing = db.scalar(select(Job.id).where(Job.client_id == client.id, Job.connection_id == row.id, Job.status.in_(('queued','running'))))
        if existing:
            raise InvalidDetails('This connection already has a queued or running job.')
        job = Job(client_id=client.id, vendor_id=row.vendor_id, connection_id=row.id,
                  credential_version=row.credential_version, requested_by=user.id, kind=kind,payload=payload)
        db.add(job)
        db.flush()
        audit(db, user, client.id, 'commerce.job_queued', 'commerce_job', job.id, {'kind': kind})
        return job.id


def job_view(row,connection_type=None):
    result={key: getattr(row, key) for key in ('id','kind','status','progress','attempts','error_code','created_at','finished_at')}
    result['job_label']=('Test '+connection_type+' connection' if row.kind=='test_connection' and connection_type in ('catalog','inventory')
                         else row.kind.replace('_',' ').capitalize())
    result['inventory_preview']=row.kind=='inventory' and row.payload.get('inventory_preview') is True
    result['preview_available']=result['inventory_preview'] and row.status=='succeeded' and bool(row.result.get('inventory_preview',{}).get('rows'))
    result['sample_rows']=row.result.get('inventory_preview',{}).get('row_count',0) if result['inventory_preview'] else 0
    result['connection_sample_available']=row.kind=='test_connection' and row.status=='succeeded' and bool(row.result.get('connection_sample'))
    if result['inventory_preview']:result['job_label']='Inventory sample (page 1)'
    result['error_message']={
        'vendor_mfa_required':'Acumatica requires a verification code or approval. Interactive sign-in is needed.',
        'vendor_tenant_not_available':'The Company tenant is not available on the configured inventory portal.',
        'vendor_password_change_required':'The vendor requires a password change before sign-in can continue.',
        'vendor_session_limit_reached':'The vendor session/license limit has been reached.',
        'vendor_logout_not_confirmed':'Sign-in succeeded, but sign-out could not be confirmed.',
        'inventory_browser_not_installed':'Install Playwright and Chromium on the worker to verify this connection.',
    }.get(row.error_code,'')
    selection=row.payload.get('catalog_selection',{}) if row.kind=='catalog' else {}
    result['catalog_label']=selection.get('category_label','All products') if row.kind=='catalog' else ''
    meta=row.result.get('inventory' if row.kind=='inventory' else 'catalog',{})
    result['completed_products']=meta.get('completed',meta.get('resumed_completed',0))
    result['total_products']=meta.get('total',0)
    result['stage']=meta.get('phase','')
    result['published']=meta.get('phase')=='published'
    result['summary']=row.result.get('summary',{})
    result['started_at']=row.started_at
    result['can_resume']=row.kind=='catalog' and bool(meta) and row.status in ('failed','blocked') and row.attempts<5 and row.error_code not in (
        'catalog_source_changed_start_new_run','catalog_resume_configuration_changed','catalog_listing_changed_or_empty')
    if row.kind=='inventory' and not result['inventory_preview']:
        result['can_resume']=bool(meta) and row.status in ('failed','blocked') and row.attempts<5 and row.error_code not in (
            'inventory_source_changed_start_new_run','inventory_resume_configuration_changed','inventory_duplicate_id_start_new_run','inventory_run_superseded_start_new_run')
        result['catalog_label']='Available items'
    if row.started_at:
        from datetime import timezone
        end=row.finished_at or now()
        start=row.started_at
        result['elapsed_seconds']=max(0,int((end.replace(tzinfo=timezone.utc)-start.replace(tzinfo=timezone.utc)).total_seconds()))
    else:
        result['elapsed_seconds']=0
    return result


def export_view(row):
    return {key: getattr(row, key) for key in ('id','format','row_count','created_at')}


def job_status(auth_id, client_id, job_id):
    with transaction() as db:
        user, client, _internal, admin, _access = authorize(db, auth_id, client_id)
        row = db.scalar(select(Job).where(Job.id == identifier(job_id), Job.client_id == client.id))
        if not row:
            raise NotFound
        authorize(db, auth_id, client.id, JOB_FEATURE[row.kind])
        name=db.scalar(select(User.display_name).where(User.id==row.requested_by))
        connection_type=db.scalar(select(VendorConnection.connection_type).where(VendorConnection.id==row.connection_id,
            VendorConnection.client_id==client.id,VendorConnection.vendor_id==row.vendor_id)) if row.connection_id else None
        view=job_identity(job_view(row,connection_type),row,name,client.timezone)
        view['can_resume']=view['can_resume'] and (admin or row.requested_by==user.id)
        if row.result.get('catalog'):
            view['completed_products']=db.scalar(select(func.count()).select_from(CatalogWorkItem).where(
                CatalogWorkItem.job_id==row.id,CatalogWorkItem.product_id.is_not(None)))
        return view


def resume_job(auth_id,client_id,job_id):
    with transaction() as db:
        user,client,internal,admin,_access=authorize(db,auth_id,client_id)
        row=db.scalar(select(Job).where(Job.id==identifier(job_id),Job.client_id==client.id).with_for_update())
        if not row:
            raise NotFound
        authorize(db,auth_id,client_id,JOB_FEATURE[row.kind])
        if not admin and row.requested_by!=user.id:
            raise Forbidden
        if not job_view(row)['can_resume']:
            raise InvalidDetails('This run cannot be resumed. Start a new run.')
        conn=connection(db,client.id,row.connection_id)
        if conn.credential_version!=row.credential_version:
            raise InvalidDetails('Connection credentials changed. Start a new run.')
        existing=db.scalar(select(Job.id).where(Job.client_id==client.id,Job.connection_id==row.connection_id,
            Job.status.in_(('queued','running')),Job.id!=row.id))
        if existing:
            raise InvalidDetails('Wait for the active connection job to finish.')
        row.max_attempts=max(row.max_attempts,min(5,row.attempts+1))
        row.status,row.error_code,row.available_at,row.finished_at='queued',None,now(),None
        audit(db,user,client.id,'commerce.'+row.kind+'_resumed','commerce_job',row.id,{})


def cancel_job(auth_id, client_id, job_id):
    with transaction() as db:
        _user, client, *_ = authorize(db, auth_id, client_id, admin=True)
        row = db.scalar(select(Job).where(Job.id == identifier(job_id), Job.client_id == client.id).with_for_update())
        if not row:
            raise NotFound
        if row.status not in ('queued','running'):
            raise InvalidDetails('Only queued/running jobs can be cancelled.')
        row.status, row.lease_token, row.lease_expires_at = 'cancelled', None, None
        row.finished_at, row.updated_at = now(), now()


def _catalog_page(db,query,vendor_code,client,page,values=None):
    values=values or {}
    filters={'search':str(values.get('q','')).strip()[:200],
             'category':str(values.get('category','')).strip()[:1000]}
    category=func.coalesce(Product.attributes['category_path'].as_string(),'')
    categories=list(db.scalars(query.with_only_columns(category,maintain_column_froms=True)
                              .where(category!='').distinct().order_by(category)))
    catalog_total=db.scalar(select(func.count()).select_from(query.subquery()))
    if filters['category']:
        query=query.where(category==filters['category'])
    if filters['search']:
        term='%'+filters['search'].replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        query=query.where(or_(Product.source_key.ilike(term,escape='\\'),
                             Product.attributes['name'].as_string().ilike(term,escape='\\')))
    total=db.scalar(select(func.count()).select_from(query.subquery()))
    page=min(page,max(1,(total+24)//25))
    try:zone=ZoneInfo(client.timezone)
    except (ZoneInfoNotFoundError,ValueError,TypeError):zone=ZoneInfo('UTC')
    rows=[]
    for product in db.scalars(query.order_by(Product.source_key).limit(25).offset((page-1)*25)):
        row=product_row(product,vendor_code)
        stamp=product.extracted_at
        if stamp and stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
        row['captured_display']=stamp.astimezone(zone).strftime('%Y-%m-%d %H:%M') if stamp else ''
        row['captured_timezone']=str(zone)
        rows.append(row)
    return {'rows':rows,'total':total,'catalog_total':catalog_total,'filters':filters,'categories':categories,'page':page}


def _inventory_page(db,source,client,page,values=None):
    search=str((values or {}).get('q','')).strip()[:200]
    query=select(Inventory).where(Inventory.client_id==client.id)
    query=query.where(Inventory.job_id==source.id) if source else query.where(False)
    inventory_total=db.scalar(select(func.count()).select_from(query.subquery()))
    if search:
        term='%'+search.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        query=query.where(or_(Inventory.source_key.ilike(term,escape='\\'),
            func.coalesce(Inventory.attributes['description'].as_string(),Inventory.attributes['name'].as_string(),'').ilike(term,escape='\\')))
    total=db.scalar(select(func.count()).select_from(query.subquery()))
    page=min(page,max(1,(total+24)//25))
    rows=db.scalars(query.order_by(Inventory.source_key).limit(25).offset((page-1)*25))
    return {'rows':[{'sku':r.vendor_sku,'key':r.source_key,'attributes':r.attributes} for r in rows],
        'total':total,'inventory_total':inventory_total,'page':page,'filters':{'search':search,'category':''}}


def results(auth_id, client_id, kind, page=1,run_id=None,filters=None):
    if kind not in ('catalog','inventory'):
        raise InvalidDetails('Select catalog or inventory.')
    with transaction() as db:
        _user, client, _internal, _admin, access = authorize(db, auth_id, client_id, kind)
        installed=db.bind.dialect.name!='postgresql' or db.scalar(text("SELECT to_regclass('commerce.catalog_heads') IS NOT NULL"))
        if kind=='catalog' and installed:
            if run_id:
                try:
                    selected_run=identifier(run_id)
                except InvalidDetails:
                    raise NotFound from None
                publication=db.scalar(select(CatalogPublication).where(CatalogPublication.job_id==selected_run,CatalogPublication.client_id==client.id))
                if not publication:
                    raise NotFound
            else:
                head=db.scalar(select(CatalogHead).join(Vendor).where(CatalogHead.client_id==client.id,Vendor.code=='shiloh_tack'))
                publication=db.get(CatalogPublication,head.publication_id) if head else None
            if publication:
                vendor=db.get(Vendor,publication.vendor_id)
                query=select(Product).join(CatalogPublicationItem,CatalogPublicationItem.product_id==Product.id).where(
                    CatalogPublicationItem.publication_id==publication.job_id,CatalogPublicationItem.client_id==client.id)
                return {'client':{'id':client.id,'name':client.name},'features':access.features,'kind':kind,'source_job_id':publication.job_id,
                    'catalog_label':'Full catalog' if publication.summary.get('coverage')=='all' else 'Selected categories',
                    'coverage_note':'Completed published refreshes only. A full catalog refresh has not been completed.' if publication.summary.get('coverage')!='all' else 'All products from the completed full refresh, including subsequent category updates.',
                    'summary':publication.summary,'run_id':run_id,
                    **_catalog_page(db,query,vendor.code,client,page,filters)}
        source_query=select(Job).where(Job.client_id==client.id,Job.kind==kind,Job.status=='succeeded')
        if kind=='inventory':source_query=source_query.where(Job.payload['inventory_preview'].as_boolean().is_not(True))
        if kind=='inventory' and run_id:source_query=source_query.where(Job.id==identifier(run_id))
        source=db.scalar(source_query.order_by(Job.finished_at.desc()).limit(1))
        if kind=='inventory':
            full=source and source.result.get('inventory',{}).get('coverage')=='all'
            return {'client':{'id':client.id,'name':client.name},'features':access.features,'kind':kind,
                'source_job_id':source.id if source else None,'run_id':run_id,
                'available_items_only':bool(source and source.result.get('inventory',{}).get('available_items_only')),
                'catalog_label':('Available inventory' if full else 'Limited inventory load') if source else '',
                'coverage_note':('All available items from the latest successful inventory run.' if full else
                    'Current inventory is a limited test load of '+str(source.result.get('inventory',{}).get('page_count',''))+' pages; a full download has not been published.') if source else '',
                **_inventory_page(db,source,client,page,filters)}
        model = Product if kind=='catalog' else Inventory
        rows = db.scalars(select(model).where(model.client_id==client.id, model.job_id==source.id).order_by(model.source_key).limit(25).offset((page-1)*25)).all() if source else []
        vendor=db.get(Vendor,source.vendor_id) if source else None
        if kind=='catalog':
            query=select(Product).where(Product.client_id==client.id,Product.job_id==source.id) if source else select(Product).where(Product.client_id==client.id).where(False)
            return {'client':{'id':client.id,'name':client.name},'features':access.features,'kind':kind,
                    'source_job_id':source.id if source else None,'catalog_label':'Completed run' if source else '',
                    'coverage_note':'Showing the latest completed extraction run.' if source else 'No completed catalog refresh has been published yet.',
                    'run_id':run_id,**_catalog_page(db,query,vendor.code if vendor else '',client,page,filters)}
        return {'client': {'id': client.id, 'name': client.name},'features':access.features, 'kind': kind, 'source_job_id': source.id if source else None,
                'catalog_label':('Available inventory' if source.result.get('inventory',{}).get('coverage')=='all' else 'Limited inventory load') if source and kind=='inventory' else '',
                'coverage_note':('All available items from the latest successful inventory run.' if source.result.get('inventory',{}).get('coverage')=='all' else 'Current inventory is a limited test load of '+str(source.result.get('inventory',{}).get('page_count',''))+' pages; a full download has not been published.') if source and kind=='inventory' else '',
                'rows': [product_row(r,vendor.code) if kind=='catalog' else {'sku':r.vendor_sku,'key':r.source_key,'attributes':r.attributes} for r in rows],
                'total': db.scalar(select(func.count()).select_from(model).where(model.client_id==client.id, model.job_id==source.id)) if source else 0}


def job_detail(auth_id,client_id,job_id,page=1):
    with transaction() as db:
        _user,client,_internal,_admin,access=authorize(db,auth_id,client_id,'catalog')
        job=db.scalar(select(Job).where(Job.id==identifier(job_id),Job.client_id==client.id,
            Job.kind=='catalog',Job.status=='succeeded'))
        if not job:raise NotFound
        name=db.scalar(select(User.display_name).where(User.id==job.requested_by))
        view=job_identity(job_view(job),job,name,client.timezone)
        total=db.scalar(select(func.count()).select_from(Product).where(Product.job_id==job.id))
        vendor=db.get(Vendor,job.vendor_id)
        rows=db.scalars(select(Product).where(Product.job_id==job.id,Product.client_id==client.id)
            .order_by(Product.source_key).limit(25).offset((page-1)*25))
        summary=job.result.get('summary') or {'record_count':total}
        return {'client':{'id':client.id,'name':client.name,'timezone':client.timezone},'features':access.features,'kind':'catalog',
            'job':view,'source_job_id':job.id,'catalog_label':view['catalog_label'],'rows':[product_row(p,vendor.code) for p in rows],
            'total':total,'summary':summary,'statistics':run_statistics(summary,job.payload.get('catalog_selection'))}


def product_detail(auth_id,client_id,product_id):
    with transaction() as db:
        _user,client,*_=authorize(db,auth_id,client_id,'catalog')
        row=db.execute(select(Product,Vendor.code).join(Vendor,Vendor.id==Product.vendor_id).join(Job,Job.id==Product.job_id)
            .where(Product.id==identifier(product_id),Product.client_id==client.id,Job.kind=='catalog',Job.status=='succeeded')).first()
        if not row:raise NotFound
        return product_details(row[0],row[1])


def connection_sample(auth_id,client_id,job_id):
    with transaction() as db:
        _user,client,_internal,admin,access=authorize(db,auth_id,client_id,'connections',admin=True)
        job=db.scalar(select(Job).where(Job.id==identifier(job_id),Job.client_id==client.id,
            Job.kind=='test_connection',Job.status=='succeeded'))
        sample=job.result.get('connection_sample') if job else None
        if not sample:raise NotFound
        return {'client':{'id':client.id,'name':client.name},'features':access.features,'admin':admin,
                'job_id':job.id,'sample':sample}


def inventory_preview(auth_id,client_id,job_id):
    with transaction() as db:
        _user,client,_internal,admin,access=authorize(db,auth_id,client_id,'inventory')
        job=db.scalar(select(Job).where(Job.id==identifier(job_id),Job.client_id==client.id,
            Job.kind=='inventory',Job.status=='succeeded'))
        if not job or job.payload.get('inventory_preview') is not True:raise NotFound
        sample=job.result.get('inventory_preview',{})
        if not sample.get('rows'):raise NotFound
        return {'client':{'id':client.id,'name':client.name},'features':access.features,'admin':admin,
                'job_id':job.id,'sample':sample}


def download(auth_id, client_id, export_id):
    with transaction() as db:
        _user, client, *_ = authorize(db, auth_id, client_id)
        row = db.scalar(select(Export).where(Export.id==identifier(export_id), Export.client_id==client.id))
        if not row:
            raise NotFound
        authorize(db, auth_id, client.id, 'shopify_export' if row.mapping_id else 'vendor_export')
        return row.artifact_path, row.format


def configuration(auth_id):
    with transaction() as db:
        user=active_user(db,auth_id)
        if not is_global(db,user):
            raise Forbidden
        return {'templates':[{'id':t.id,'platform':t.platform,'version':t.version,'columns':t.columns} for t in db.scalars(select(DestinationTemplate).order_by(DestinationTemplate.created_at.desc()))],
                'mappings':[{'id':m.id,'client_id':m.client_id,'version':m.version,'template_id':m.template_id} for m in db.scalars(select(MappingRule).order_by(MappingRule.created_at.desc()))]}


def register_template(auth_id, platform, version, columns, digest):
    from commerce_hub.export_mapping import validate
    validate(columns,[])
    if platform!='shopify' or not version.strip() or len(version)>100:
        raise InvalidDetails('Provide a Shopify template version.')
    with transaction() as db:
        user=active_user(db,auth_id)
        if not is_global(db,user):
            raise Forbidden
        if db.scalar(select(DestinationTemplate.id).where(DestinationTemplate.platform==platform, DestinationTemplate.version==version.strip())):
            raise InvalidDetails('This template version already exists. Create a new version.')
        row=DestinationTemplate(platform=platform,version=version.strip(),columns=columns,source_sha256=digest)
        db.add(row)
        db.flush()
        audit(db,user,None,'commerce.template_added','commerce_template',row.id,{'version':version.strip()})
        return row.id


def register_mapping(auth_id, client_id, vendor_id, template_id, version, rules):
    from commerce_hub.export_mapping import validate
    with transaction() as db:
        user=active_user(db,auth_id)
        if not is_global(db,user):
            raise Forbidden
        if not db.get(Client,identifier(client_id)) or not db.get(Vendor,identifier(vendor_id)):
            raise NotFound
        template=db.get(DestinationTemplate,identifier(template_id))
        if not template or not template.is_active:
            raise InvalidDetails('Select an active template.')
        if not rules or not isinstance(version,int) or not 1<=version<=100000:
            raise InvalidDetails('Provide mapping rules and a positive version.')
        validate(template.columns,rules)
        if db.scalar(select(MappingRule.id).where(MappingRule.client_id==client_id,MappingRule.vendor_id==vendor_id,MappingRule.template_id==template_id,MappingRule.version==version)):
            raise InvalidDetails('This mapping version already exists.')
        row=MappingRule(client_id=client_id,vendor_id=vendor_id,template_id=template_id,version=version,rules=rules,created_by=user.id)
        db.add(row)
        db.flush()
        audit(db,user,client_id,'commerce.mapping_added','commerce_mapping',row.id,{'version':version})
        return row.id


def enqueue_export(auth_id, client_id, kind, catalog_id, inventory_id=None, mapping_id=None, output_format='xlsx'):
    if kind not in ('export_vendor','export_shopify') or output_format not in ('csv','xlsx') or (kind=='export_shopify' and output_format!='csv'):
        raise InvalidDetails('Select a valid export format.')
    with transaction() as db:
        user,client,*_=authorize(db,auth_id,client_id,JOB_FEATURE[kind])
        def source(value,source_kind):
            row=db.scalar(select(Job).where(Job.id==identifier(value),Job.client_id==client.id,Job.kind==source_kind,Job.status=='succeeded'))
            if not row:
                raise InvalidDetails('Choose completed extraction runs belonging to this client.')
            return row
        catalog=source(catalog_id,'catalog')
        inventory=source(inventory_id,'inventory') if inventory_id else None
        if inventory and inventory.vendor_id!=catalog.vendor_id:
            raise InvalidDetails('Both runs must belong to the same vendor.')
        payload={'catalog_job_id':catalog.id,'format':output_format}
        if inventory:
            payload['inventory_job_id']=inventory.id
        if kind=='export_shopify':
            if not inventory:
                raise InvalidDetails('Select a completed inventory run.')
            mapping=db.scalar(select(MappingRule).where(MappingRule.id==identifier(mapping_id),MappingRule.client_id==client.id,MappingRule.vendor_id==catalog.vendor_id,MappingRule.is_active.is_(True)))
            if not mapping:
                raise InvalidDetails('Select a mapping for this client and vendor.')
            payload['mapping_id']=mapping.id
        job=Job(client_id=client.id,vendor_id=catalog.vendor_id,requested_by=user.id,kind=kind,payload=payload)
        db.add(job)
        db.flush()
        audit(db,user,client.id,'commerce.export_queued','commerce_job',job.id,{'kind':kind})
        return job.id


def export_options(auth_id,client_id):
    with transaction() as db:
        _user,client,*_=authorize(db,auth_id,client_id)
        return {'runs':[{'id':j.id,'kind':j.kind,'created_at':j.created_at,'catalog_label':job_view(j)['catalog_label']} for j in db.scalars(select(Job).where(Job.client_id==client.id,Job.kind.in_(('catalog','inventory')),Job.status=='succeeded').order_by(Job.created_at.desc()).limit(100))],
                'mappings':[{'id':m.id,'version':m.version} for m in db.scalars(select(MappingRule).where(MappingRule.client_id==client.id,MappingRule.is_active.is_(True)))]}
