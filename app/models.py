"""ORM mappings for the existing authentication, administration and scope tables.

These mappings never create or replace tables. The 001 SQL remains the complete
17-table baseline; future schema changes use reviewed Alembic revisions.
"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, Column, Date, DateTime, FetchedValue, ForeignKey, Integer, JSON, Numeric, Text, Uuid, BigInteger
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def uid():
    return Column(Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))


def timestamp():
    return Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Industry(Base):
    __tablename__ = 'industries'
    __table_args__ = {'schema': 'codebi'}
    id = uid()
    name = Column(Text, nullable=False)
    description = Column(Text)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = timestamp()
    updated_at = timestamp()


class Country(Base):
    __tablename__ = 'countries'
    __table_args__ = {'schema': 'codebi'}
    code = Column(Text, primary_key=True)
    name = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class StateRegion(Base):
    __tablename__ = 'state_regions'
    __table_args__ = {'schema': 'codebi'}
    code = Column(Text, primary_key=True)
    country_code = Column(Text, ForeignKey('codebi.countries.code'), nullable=False)
    name = Column(Text, nullable=False)
    region_type = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class Timezone(Base):
    __tablename__ = 'timezones'
    __table_args__ = {'schema': 'codebi'}
    name = Column(Text, primary_key=True)
    is_active = Column(Boolean, default=True, nullable=False)


class Client(Base):
    __tablename__ = 'clients'
    __table_args__ = {'schema': 'codebi'}
    id = uid()
    client_number = Column(BigInteger, server_default=FetchedValue())
    client_code = Column(Text, server_default=FetchedValue())
    name = Column(Text, nullable=False)
    legal_name = Column(Text)
    industry_id = Column(Uuid(as_uuid=False))
    website = Column(Text)
    email = Column(Text)
    phone = Column(Text)
    contact_name = Column(Text)
    contact_email = Column(Text)
    contact_phone = Column(Text)
    registration_inquiry = Column(Text)
    logo_path = Column(Text)
    logo_background = Column(Text, default='light', nullable=False)
    address_line_1 = Column(Text)
    address_line_2 = Column(Text)
    city = Column(Text)
    state_region = Column(Text)
    state_region_code = Column(Text)
    postal_code = Column(Text)
    country_code = Column(Text)
    timezone = Column(Text, default='UTC', nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(Uuid(as_uuid=False))
    updated_by = Column(Uuid(as_uuid=False))
    created_at = timestamp()
    updated_at = timestamp()


class PublicInquiry(Base):
    __tablename__ = 'public_inquiries'
    __table_args__ = {'schema': 'codebi'}
    id = uid()
    name = Column(Text, nullable=False)
    email = Column(Text, nullable=False)
    company = Column(Text, nullable=False)
    details = Column(Text, nullable=False)
    created_at = timestamp()


class User(Base):
    __tablename__ = 'users'
    __table_args__ = {'schema': 'codebi'}
    id = uid()
    auth_user_id = Column(Uuid(as_uuid=False), unique=True)
    email = Column(Text, nullable=False, unique=True)
    display_name = Column(Text, nullable=False)
    phone = Column(Text)
    user_type = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    last_login_at = Column(DateTime(timezone=True))
    created_by = Column(Uuid(as_uuid=False))
    updated_by = Column(Uuid(as_uuid=False))
    created_at = timestamp()
    updated_at = timestamp()


class Role(Base):
    __tablename__ = 'roles'
    __table_args__ = {'schema': 'codebi'}
    code = Column(Text, primary_key=True)
    name = Column(Text, nullable=False)
    description = Column(Text)
    scope = Column(Text, nullable=False)


class UserRole(Base):
    __tablename__ = 'user_roles'
    __table_args__ = {'schema': 'codebi'}
    user_id = Column(Uuid(as_uuid=False), primary_key=True)
    role_code = Column(Text, primary_key=True)
    role_scope = Column(Text, default='global', nullable=False)
    granted_by = Column(Uuid(as_uuid=False))
    granted_at = timestamp()


class Membership(Base):
    __tablename__ = 'client_memberships'
    __table_args__ = {'schema': 'codebi'}
    id = uid()
    client_id = Column(Uuid(as_uuid=False), nullable=False)
    user_id = Column(Uuid(as_uuid=False), nullable=False)
    role_code = Column(Text, default='client_user', nullable=False)
    role_scope = Column(Text, default='client', nullable=False)
    can_create_sows = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    granted_by = Column(Uuid(as_uuid=False))
    created_at = timestamp()
    updated_at = timestamp()


class LoginEvent(Base):
    __tablename__ = 'login_events'
    __table_args__ = {'schema': 'codebi'}
    id = uid()
    user_id = Column(Uuid(as_uuid=False))
    provider = Column(Text, nullable=False)
    outcome = Column(Text, nullable=False)
    reason_code = Column(Text)
    occurred_at = timestamp()


class AuditEvent(Base):
    __tablename__ = 'audit_events'
    __table_args__ = {'schema': 'codebi'}
    id = uid()
    client_id = Column(Uuid(as_uuid=False))
    actor_id = Column(Uuid(as_uuid=False), nullable=False)
    action = Column(Text, nullable=False)
    entity_type = Column(Text, nullable=False)
    entity_id = Column(Uuid(as_uuid=False))
    changes = Column(JSON().with_variant(JSONB, 'postgresql'), default=dict, nullable=False)
    occurred_at = timestamp()


class SowType(Base):
    __tablename__ = 'sow_types'
    __table_args__ = {'schema': 'codebi'}
    id = uid()
    name = Column(Text, nullable=False, unique=True)
    description = Column(Text)
    is_active = Column(Boolean, default=True, nullable=False)


class Tool(Base):
    __tablename__ = 'tools'
    __table_args__ = {'schema': 'codebi'}
    id = uid()
    name = Column(Text, nullable=False, unique=True)
    description = Column(Text)
    is_active = Column(Boolean, default=True, nullable=False)


class ScopeOfWork(Base):
    __tablename__ = 'statements_of_work'
    __table_args__ = {'schema': 'codebi'}
    id = uid()
    sow_number = Column(BigInteger, server_default=FetchedValue())
    sow_code = Column(Text, server_default=FetchedValue())
    client_id = Column(Uuid(as_uuid=False), nullable=False)
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    sow_type_id = Column(Uuid(as_uuid=False), nullable=False)
    priority = Column(Text, default='normal', nullable=False)
    desired_completion_date = Column(Date)
    other_tool_details = Column(Text)
    status = Column(Text, default='draft', nullable=False)
    created_by = Column(Uuid(as_uuid=False), nullable=False)
    updated_by = Column(Uuid(as_uuid=False))
    submitted_by = Column(Uuid(as_uuid=False))
    submitted_at = Column(DateTime(timezone=True))
    accepted_estimate_id = Column(Uuid(as_uuid=False))
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = timestamp()
    updated_at = timestamp()


class SowTool(Base):
    __tablename__ = 'sow_tools'
    __table_args__ = {'schema': 'codebi'}
    sow_id = Column(Uuid(as_uuid=False), primary_key=True)
    tool_id = Column(Uuid(as_uuid=False), primary_key=True)


class SowMessage(Base):
    __tablename__ = 'sow_messages'
    __table_args__ = {'schema': 'codebi'}
    id = uid()
    client_id = Column(Uuid(as_uuid=False), nullable=False)
    sow_id = Column(Uuid(as_uuid=False), nullable=False)
    author_id = Column(Uuid(as_uuid=False), nullable=False)
    message_type = Column(Text, nullable=False)
    body = Column(Text, nullable=False)
    created_at = timestamp()


class SowEstimate(Base):
    __tablename__ = 'sow_estimates'
    __table_args__ = {'schema': 'codebi'}
    id = uid()
    client_id = Column(Uuid(as_uuid=False), nullable=False)
    sow_id = Column(Uuid(as_uuid=False), nullable=False)
    revision = Column(Integer, nullable=False)
    scope_snapshot = Column(Text, nullable=False)
    feasibility = Column(Text, nullable=False)
    min_hours = Column(Numeric(10, 2))
    max_hours = Column(Numeric(10, 2))
    pricing_type = Column(Text)
    hourly_rate = Column(Numeric(10, 2))
    min_amount = Column(Numeric(18, 2))
    max_amount = Column(Numeric(18, 2))
    estimated_completion = Column(Date)
    assumptions = Column(Text)
    exclusions = Column(Text)
    status = Column(Text, nullable=False)
    created_by = Column(Uuid(as_uuid=False), nullable=False)
    issued_at = Column(DateTime(timezone=True))
    decided_by = Column(Uuid(as_uuid=False))
    decided_at = Column(DateTime(timezone=True))
    created_at = timestamp()
    updated_at = timestamp()


class TimeEntry(Base):
    __tablename__ = 'time_entries'
    __table_args__ = {'schema': 'codebi'}
    id = uid()
    client_id = Column(Uuid(as_uuid=False), nullable=False)
    sow_id = Column(Uuid(as_uuid=False), nullable=False)
    worker_id = Column(Uuid(as_uuid=False), nullable=False)
    activity = Column(Text, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(Text, nullable=False)


class SowEvent(Base):
    __tablename__ = 'sow_events'
    __table_args__ = {'schema': 'codebi'}
    id = uid()
    client_id = Column(Uuid(as_uuid=False), nullable=False)
    sow_id = Column(Uuid(as_uuid=False), nullable=False)
    actor_id = Column(Uuid(as_uuid=False), nullable=False)
    event_type = Column(Text, nullable=False)
    from_status = Column(Text)
    to_status = Column(Text)
    estimate_id = Column(Uuid(as_uuid=False))
    message_id = Column(Uuid(as_uuid=False))
    details = Column(JSON().with_variant(JSONB, 'postgresql'), default=dict, nullable=False)
    occurred_at = timestamp()
