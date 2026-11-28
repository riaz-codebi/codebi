"""Explicit commerce mappings. Production objects are installed by reviewed SQL only."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, ForeignKeyConstraint, Integer, JSON, MetaData, Table, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase


class CommerceBase(DeclarativeBase):
    pass


# FK targets only. Existing identity tables remain owned/mapped by the portal.
Table('clients', CommerceBase.metadata, Column('id', Uuid(as_uuid=False), primary_key=True), schema='codebi')
Table('users', CommerceBase.metadata, Column('id', Uuid(as_uuid=False), primary_key=True), schema='codebi')


def uuid():
    return Column(Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))


def now():
    return datetime.now(timezone.utc)


def stamp():
    return Column(DateTime(timezone=True), default=now, nullable=False)


def json_value(default=dict):
    return Column(JSON().with_variant(JSONB, 'postgresql'), default=default, nullable=False)


class CommercialApp(CommerceBase):
    __tablename__ = 'commercial_apps'
    __table_args__ = {'schema': 'codebi'}
    code = Column(Text, primary_key=True)
    name = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class ClientAppAccess(CommerceBase):
    __tablename__ = 'client_app_access'
    __table_args__ = {'schema': 'codebi'}
    client_id = Column(Uuid(as_uuid=False), ForeignKey('codebi.clients.id'), primary_key=True)
    app_code = Column(Text, ForeignKey('codebi.commercial_apps.code'), primary_key=True)
    is_active = Column(Boolean, default=False, nullable=False)
    features = json_value(list)
    updated_by = Column(Uuid(as_uuid=False), ForeignKey('codebi.users.id'))
    updated_at = stamp()


class Vendor(CommerceBase):
    __tablename__ = 'vendors'
    __table_args__ = {'schema': 'commerce'}
    id = uuid()
    code = Column(Text, unique=True, nullable=False)
    name = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class VendorConnection(CommerceBase):
    __tablename__ = 'vendor_connections'
    __table_args__ = (UniqueConstraint('client_id', 'id', 'vendor_id'),
        UniqueConstraint('client_id', 'vendor_id', 'connection_type'), {'schema': 'commerce'})
    id = uuid()
    client_id = Column(Uuid(as_uuid=False), ForeignKey('codebi.clients.id'), nullable=False)
    vendor_id = Column(Uuid(as_uuid=False), ForeignKey('commerce.vendors.id'), nullable=False)
    connection_type = Column(Text, nullable=False)
    portal_url = Column(Text)
    approved_host = Column(Text)
    username = Column(Text, default='', nullable=False)
    credential_ref = Column(Uuid(as_uuid=False))
    credential_version = Column(Integer, default=0, nullable=False)
    status = Column(Text, default='not_configured', nullable=False)
    last_verified_at = Column(DateTime(timezone=True))
    last_error_code = Column(Text)
    config = json_value()
    created_by = Column(Uuid(as_uuid=False), ForeignKey('codebi.users.id'))
    updated_by = Column(Uuid(as_uuid=False), ForeignKey('codebi.users.id'))
    created_at = stamp()
    updated_at = stamp()


class DestinationTemplate(CommerceBase):
    __tablename__ = 'destination_templates'
    __table_args__ = (UniqueConstraint('platform', 'version'), {'schema': 'commerce'})
    id = uuid()
    platform = Column(Text, nullable=False)
    version = Column(Text, nullable=False)
    columns = json_value(list)
    source_sha256 = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = stamp()


class MappingRule(CommerceBase):
    __tablename__ = 'mapping_rules'
    __table_args__ = (UniqueConstraint('client_id', 'id', 'vendor_id', 'template_id'),
        UniqueConstraint('client_id', 'vendor_id', 'template_id', 'version'), {'schema': 'commerce'})
    id = uuid()
    client_id = Column(Uuid(as_uuid=False), ForeignKey('codebi.clients.id'), nullable=False)
    vendor_id = Column(Uuid(as_uuid=False), ForeignKey('commerce.vendors.id'), nullable=False)
    template_id = Column(Uuid(as_uuid=False), ForeignKey('commerce.destination_templates.id'), nullable=False)
    version = Column(Integer, nullable=False)
    rules = json_value(list)
    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(Uuid(as_uuid=False), ForeignKey('codebi.users.id'), nullable=False)
    created_at = stamp()


class ExportDestination(CommerceBase):
    __tablename__ = 'client_export_destinations'
    __table_args__ = (UniqueConstraint('client_id','vendor_id','platform'), {'schema':'commerce'})
    id = uuid()
    client_id = Column(Uuid(as_uuid=False), ForeignKey('codebi.clients.id'), nullable=False)
    vendor_id = Column(Uuid(as_uuid=False), ForeignKey('commerce.vendors.id'), nullable=False)
    platform = Column(Text, nullable=False)
    template_id = Column(Uuid(as_uuid=False), ForeignKey('commerce.destination_templates.id'), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    updated_by = Column(Uuid(as_uuid=False), ForeignKey('codebi.users.id'))
    updated_at = stamp()


class Job(CommerceBase):
    __tablename__ = 'jobs'
    __table_args__ = (UniqueConstraint('client_id', 'id', 'vendor_id'),
        ForeignKeyConstraint(['client_id', 'connection_id', 'vendor_id'],
            ['commerce.vendor_connections.client_id', 'commerce.vendor_connections.id', 'commerce.vendor_connections.vendor_id']),
        {'schema': 'commerce'})
    id = uuid()
    client_id = Column(Uuid(as_uuid=False), ForeignKey('codebi.clients.id'), nullable=False)
    vendor_id = Column(Uuid(as_uuid=False), ForeignKey('commerce.vendors.id'), nullable=False)
    connection_id = Column(Uuid(as_uuid=False))
    credential_version = Column(Integer)
    requested_by = Column(Uuid(as_uuid=False), ForeignKey('codebi.users.id'), nullable=False)
    kind = Column(Text, nullable=False)
    status = Column(Text, default='queued', nullable=False)
    payload = json_value()
    result = json_value()
    attempts = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=3, nullable=False)
    progress = Column(Integer, default=0, nullable=False)
    lease_token = Column(Uuid(as_uuid=False))
    lease_expires_at = Column(DateTime(timezone=True))
    available_at = stamp()
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    error_code = Column(Text)
    created_at = stamp()
    updated_at = stamp()


class Product(CommerceBase):
    __tablename__ = 'products'
    __table_args__ = (UniqueConstraint('client_id', 'job_id', 'source_key'),
        UniqueConstraint('client_id', 'vendor_id', 'id', name='products_catalog_scoped_id'),
        ForeignKeyConstraint(['client_id', 'job_id', 'vendor_id'], ['commerce.jobs.client_id', 'commerce.jobs.id', 'commerce.jobs.vendor_id']), {'schema': 'commerce'})
    id = uuid()
    client_id = Column(Uuid(as_uuid=False), nullable=False)
    vendor_id = Column(Uuid(as_uuid=False), nullable=False)
    job_id = Column(Uuid(as_uuid=False), nullable=False)
    source_key = Column(Text, nullable=False)
    vendor_sku = Column(Text)
    source_url = Column(Text)
    attributes = json_value()
    artifact_path = Column(Text)
    extracted_at = stamp()


class CatalogWorkItem(CommerceBase):
    __tablename__ = 'catalog_work_items'
    __table_args__ = (UniqueConstraint('job_id','source_path'),
        ForeignKeyConstraint(['client_id','job_id','vendor_id'],['commerce.jobs.client_id','commerce.jobs.id','commerce.jobs.vendor_id']),
        ForeignKeyConstraint(['client_id','vendor_id','product_id'],['commerce.products.client_id','commerce.products.vendor_id','commerce.products.id']),
        {'schema':'commerce'})
    job_id = Column(Uuid(as_uuid=False), primary_key=True)
    ordinal = Column(Integer, primary_key=True)
    client_id = Column(Uuid(as_uuid=False), nullable=False)
    vendor_id = Column(Uuid(as_uuid=False), nullable=False)
    source_path = Column(Text, nullable=False)
    product_id = Column(Uuid(as_uuid=False))
    completed_at = Column(DateTime(timezone=True))


class CatalogPublication(CommerceBase):
    __tablename__ = 'catalog_publications'
    __table_args__ = (UniqueConstraint('client_id','vendor_id','job_id'),
        ForeignKeyConstraint(['client_id','job_id','vendor_id'],['commerce.jobs.client_id','commerce.jobs.id','commerce.jobs.vendor_id']),
        {'schema':'commerce'})
    job_id = Column(Uuid(as_uuid=False), primary_key=True)
    client_id = Column(Uuid(as_uuid=False), nullable=False)
    vendor_id = Column(Uuid(as_uuid=False), nullable=False)
    selection = json_value()
    summary = json_value()
    published_at = stamp()


class CatalogPublicationItem(CommerceBase):
    __tablename__ = 'catalog_publication_items'
    __table_args__ = (
        ForeignKeyConstraint(['client_id','vendor_id','publication_id'],['commerce.catalog_publications.client_id','commerce.catalog_publications.vendor_id','commerce.catalog_publications.job_id']),
        ForeignKeyConstraint(['client_id','vendor_id','product_id'],['commerce.products.client_id','commerce.products.vendor_id','commerce.products.id']),
        {'schema':'commerce'})
    publication_id = Column(Uuid(as_uuid=False), primary_key=True)
    source_key = Column(Text, primary_key=True)
    client_id = Column(Uuid(as_uuid=False), nullable=False)
    vendor_id = Column(Uuid(as_uuid=False), nullable=False)
    product_id = Column(Uuid(as_uuid=False), nullable=False)
    category_ids = json_value(list)


class CatalogHead(CommerceBase):
    __tablename__ = 'catalog_heads'
    __table_args__ = (
        ForeignKeyConstraint(['client_id','vendor_id','publication_id'],['commerce.catalog_publications.client_id','commerce.catalog_publications.vendor_id','commerce.catalog_publications.job_id']),
        {'schema':'commerce'})
    client_id = Column(Uuid(as_uuid=False), ForeignKey('codebi.clients.id'), primary_key=True)
    vendor_id = Column(Uuid(as_uuid=False), ForeignKey('commerce.vendors.id'), primary_key=True)
    publication_id = Column(Uuid(as_uuid=False), nullable=False)


class VendorCatalogLease(CommerceBase):
    __tablename__ = 'vendor_catalog_leases'
    __table_args__ = {'schema':'commerce'}
    vendor_id = Column(Uuid(as_uuid=False), ForeignKey('commerce.vendors.id'), primary_key=True)
    job_id = Column(Uuid(as_uuid=False), ForeignKey('commerce.jobs.id'))
    lease_token = Column(Uuid(as_uuid=False))
    lease_expires_at = Column(DateTime(timezone=True))
    throttle_until = Column(DateTime(timezone=True))


class Inventory(CommerceBase):
    __tablename__ = 'inventory'
    __table_args__ = (UniqueConstraint('client_id', 'job_id', 'source_key'),
        ForeignKeyConstraint(['client_id', 'job_id', 'vendor_id'], ['commerce.jobs.client_id', 'commerce.jobs.id', 'commerce.jobs.vendor_id']), {'schema': 'commerce'})
    id = uuid()
    client_id = Column(Uuid(as_uuid=False), nullable=False)
    vendor_id = Column(Uuid(as_uuid=False), nullable=False)
    job_id = Column(Uuid(as_uuid=False), nullable=False)
    source_key = Column(Text, nullable=False)
    vendor_sku = Column(Text)
    attributes = json_value()
    extracted_at = stamp()


class Export(CommerceBase):
    __tablename__ = 'exports'
    __table_args__ = (UniqueConstraint('client_id', 'id'), UniqueConstraint('job_id'),
        ForeignKeyConstraint(['client_id', 'job_id', 'vendor_id'], ['commerce.jobs.client_id', 'commerce.jobs.id', 'commerce.jobs.vendor_id']),
        ForeignKeyConstraint(['client_id', 'catalog_job_id', 'vendor_id'], ['commerce.jobs.client_id', 'commerce.jobs.id', 'commerce.jobs.vendor_id']),
        ForeignKeyConstraint(['client_id', 'inventory_job_id', 'vendor_id'], ['commerce.jobs.client_id', 'commerce.jobs.id', 'commerce.jobs.vendor_id']),
        ForeignKeyConstraint(['client_id', 'mapping_id', 'vendor_id', 'template_id'], ['commerce.mapping_rules.client_id', 'commerce.mapping_rules.id', 'commerce.mapping_rules.vendor_id', 'commerce.mapping_rules.template_id']), {'schema': 'commerce'})
    id = uuid()
    client_id = Column(Uuid(as_uuid=False), nullable=False)
    vendor_id = Column(Uuid(as_uuid=False), nullable=False)
    job_id = Column(Uuid(as_uuid=False), nullable=False)
    catalog_job_id = Column(Uuid(as_uuid=False))
    inventory_job_id = Column(Uuid(as_uuid=False))
    mapping_id = Column(Uuid(as_uuid=False))
    template_id = Column(Uuid(as_uuid=False), ForeignKey('commerce.destination_templates.id'))
    format = Column(Text, nullable=False)
    artifact_path = Column(Text, nullable=False)
    sha256 = Column(Text, nullable=False)
    row_count = Column(Integer, nullable=False)
    manifest = json_value()
    created_at = stamp()
