"""Client-scoped export subscriptions, immutable mapping versions and catalog downloads."""
import hashlib

from sqlalchemy import String,cast,func,select

from app.database import transaction
from app.commerce.services import authorize
from app.commerce import export_builder as builder
from app.services import Forbidden, InvalidDetails, NotFound, audit, identifier
from commerce_hub.models import CatalogHead, CatalogPublication, CatalogPublicationItem, DestinationTemplate, ExportDestination, MappingRule, Product, Vendor, VendorConnection, now
from commerce_hub.export_mapping import MappingError


def latest(db,destination):
    return db.scalar(select(MappingRule).where(MappingRule.client_id==destination.client_id,
        MappingRule.vendor_id==destination.vendor_id,MappingRule.template_id==destination.template_id,
        MappingRule.is_active.is_(True)).order_by(MappingRule.version.desc()).limit(1))


def destination_row(db,client_id,destination_id,lock=False):
    query=select(ExportDestination).where(ExportDestination.id==identifier(destination_id),ExportDestination.client_id==client_id,
        ExportDestination.is_active.is_(True))
    destination=db.scalar(query.with_for_update() if lock else query)
    if not destination:raise NotFound
    vendor=db.get(Vendor,destination.vendor_id)
    template=db.get(DestinationTemplate,destination.template_id)
    if not vendor or not vendor.is_active or not template or not template.is_active or template.platform!=destination.platform or destination.platform!='shopify':raise NotFound
    if not db.scalar(select(VendorConnection.id).where(VendorConnection.client_id==client_id,
        VendorConnection.vendor_id==vendor.id,VendorConnection.connection_type=='catalog')):raise NotFound
    return destination,vendor,template


def snapshot(db,client_id,vendor_id):
    head=db.get(CatalogHead,(client_id,vendor_id))
    if not head:return None,[]
    publication=db.get(CatalogPublication,head.publication_id)
    if not publication or publication.client_id!=client_id or publication.vendor_id!=vendor_id:raise NotFound
    query=select(Product).join(CatalogPublicationItem,CatalogPublicationItem.product_id==Product.id).where(
        CatalogPublicationItem.publication_id==publication.job_id,CatalogPublicationItem.client_id==client_id,
        Product.client_id==client_id,Product.vendor_id==vendor_id)
    count,size=db.execute(query.with_only_columns(func.count(),func.coalesce(func.sum(func.length(cast(Product.attributes,String))),0),maintain_column_froms=True)).one()
    if count>10000 or size>20*1024*1024:raise InvalidDetails('Catalog export limit is 10,000 products or 20 MB of source data.')
    products=list(db.scalars(query.order_by(Product.source_key)))
    return publication,products


def overview(auth_id,client_id):
    with transaction() as db:
        _user,client,internal,admin,access=authorize(db,auth_id,client_id)
        rows=[];available=[]
        if 'shopify_export' in access.features and 'catalog' in access.features:
            for dest in db.scalars(select(ExportDestination).where(ExportDestination.client_id==client.id,ExportDestination.is_active.is_(True))):
                try:destination,vendor,template=destination_row(db,client.id,dest.id)
                except NotFound:continue
                mapping=latest(db,destination)
                has_catalog=db.get(CatalogHead,(client.id,vendor.id)) is not None
                rows.append({'id':dest.id,'label':vendor.name+' to Shopify','version':mapping.version if mapping else None,
                    'configured':bool(mapping),'has_catalog':has_catalog})
            if internal:
                for vendor in db.scalars(select(Vendor).join(VendorConnection).where(VendorConnection.client_id==client.id,
                    VendorConnection.connection_type=='catalog',Vendor.is_active.is_(True)).order_by(Vendor.name)):
                    for template in db.scalars(select(DestinationTemplate).where(DestinationTemplate.platform=='shopify',DestinationTemplate.is_active.is_(True)).order_by(DestinationTemplate.created_at.desc())):
                        if not db.scalar(select(ExportDestination.id).where(ExportDestination.client_id==client.id,
                            ExportDestination.vendor_id==vendor.id,ExportDestination.platform==template.platform,ExportDestination.is_active.is_(True))):
                            available.append({'vendor_id':vendor.id,'template_id':template.id,'label':vendor.name+' to Shopify ('+template.version+')'})
        return {'rows':rows,'available':available,'internal':internal,'admin':admin}


def set_destination(auth_id,client_id,vendor_id,template_id,enabled=True):
    with transaction() as db:
        user,client,internal,*_=authorize(db,auth_id,client_id,'shopify_export',admin=True)
        if not internal:raise Forbidden
        authorize(db,auth_id,client_id,'catalog')
        vendor=db.get(Vendor,identifier(vendor_id));template=db.get(DestinationTemplate,identifier(template_id))
        if not vendor or not vendor.is_active or not template or not template.is_active or template.platform!='shopify':raise NotFound
        if not db.scalar(select(VendorConnection.id).where(VendorConnection.client_id==client.id,VendorConnection.vendor_id==vendor.id,VendorConnection.connection_type=='catalog')):raise NotFound
        row=db.scalar(select(ExportDestination).where(ExportDestination.client_id==client.id,ExportDestination.vendor_id==vendor.id,ExportDestination.platform==template.platform).with_for_update())
        if not row:
            row=ExportDestination(client_id=client.id,vendor_id=vendor.id,platform=template.platform,template_id=template.id)
            db.add(row)
        row.template_id,row.is_active,row.updated_by,row.updated_at=template.id,bool(enabled),user.id,now()
        db.flush()
        audit(db,user,client.id,'commerce.export_destination_changed','export_destination',row.id,{'enabled':bool(enabled),'template_id':template.id})


def editor(auth_id,client_id,destination_id,form=None,save=False,sample_product_id=None):
    with transaction() as db:
        user,client,_internal,admin,access=authorize(db,auth_id,client_id,'shopify_export',admin=True)
        authorize(db,auth_id,client_id,'catalog')
        destination,vendor,template=destination_row(db,client.id,destination_id,lock=save)
        mapping=latest(db,destination)
        _publication,products=snapshot(db,client.id,vendor.id)
        fields=builder.FIELDS|builder.specification_fields(products)
        sample=builder.mapping_sample(products,vendor,fields,product_id=sample_product_id)
        rules=mapping.rules if mapping else builder.defaults(template.columns,has_options=any(product.attributes.get('dropdowns') for product in products))
        error=None
        if form is not None:
            try:
                rules=builder.form_rules(template.columns,form)
                builder.validate_rules(template.columns,rules,fields)
                if save:
                    if form.get('base_mapping','')!=(mapping.id if mapping else ''):
                        raise MappingError('This mapping changed in another window. Reload before saving.')
                    version=(db.scalar(select(func.max(MappingRule.version)).where(MappingRule.client_id==client.id,
                        MappingRule.vendor_id==vendor.id,MappingRule.template_id==template.id)) or 0)+1
                    new=MappingRule(client_id=client.id,vendor_id=vendor.id,template_id=template.id,version=version,rules=rules,created_by=user.id)
                    db.add(new);db.flush()
                    audit(db,user,client.id,'commerce.mapping_saved','commerce_mapping',new.id,{'version':version,'destination_id':destination.id})
                    mapping=new
            except MappingError as exc:error=str(exc)
        return {'client':{'id':client.id,'name':client.name},'features':access.features,'admin':admin,
            'destination':{'id':destination.id,'label':vendor.name+' to Shopify','template':template.version},
            'mapping_id':form.get('base_mapping','') if error and form is not None else mapping.id if mapping else '', 'version':mapping.version if mapping else None,
            'rows':builder.editor_rows(template.columns,rules),'fields':fields,'automatic':builder.AUTOMATIC,'error':error,'sample':sample}


def sample_product(auth_id,client_id,destination_id,product_id):
    with transaction() as db:
        _user,client,*_=authorize(db,auth_id,client_id,'shopify_export',admin=True)
        authorize(db,auth_id,client_id,'catalog')
        _destination,vendor,_template=destination_row(db,client.id,destination_id)
        _publication,products=snapshot(db,client.id,vendor.id)
        fields=builder.FIELDS|builder.specification_fields(products)
        try:return builder.mapping_sample(products,vendor,fields,product_id=str(product_id or '').strip()[:200],strict=True)
        except MappingError:raise NotFound from None


def download(auth_id,client_id,destination_id):
    with transaction() as db:
        user,client,*_=authorize(db,auth_id,client_id,'shopify_export')
        authorize(db,auth_id,client_id,'catalog')
        destination,vendor,template=destination_row(db,client.id,destination_id)
        mapping=latest(db,destination)
        if not mapping:raise InvalidDetails('Save a mapping using the gear icon before downloading.')
        publication,products=snapshot(db,client.id,vendor.id)
        if not publication:raise InvalidDetails('Complete a catalog refresh before downloading.')
        try:content,counts=builder.build_csv(products,vendor,template.columns,mapping.rules)
        except MappingError as exc:raise InvalidDetails(str(exc)) from None
        audit(db,user,client.id,'commerce.catalog_exported','commerce_mapping',mapping.id,
            {'destination_id':destination.id,'publication_id':publication.job_id,'template_id':template.id,
             'mapping_version':mapping.version,'sha256':hashlib.sha256(content).hexdigest(),**counts})
        return content,vendor.code+'-shopify-v'+str(mapping.version)+'.csv'
