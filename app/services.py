"""Portal authorization and business operations in Python, using SQLAlchemy."""
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select

from app.database import transaction
from app.models import AuditEvent, Client, Country, Industry, LoginEvent, Membership, Role, StateRegion, Timezone, User, UserRole


def role_can_create_sows(role_code):
    """Client role policy; SOW creation is not an independent user permission."""
    return role_code in ('client_user', 'client_admin')


class Forbidden(Exception):
    pass


class NotFound(Exception):
    pass


class InvalidDetails(Exception):
    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class Identity:
    auth_id: str
    email: str
    verified: bool
    display_name: str = ''


def identifier(value):
    try:
        return str(UUID(str(value))) if value is not None else None
    except (ValueError, TypeError):
        raise InvalidDetails('Invalid record identifier.') from None


def active_user(db, auth_id):
    return db.scalar(select(User).where(User.auth_user_id == identifier(auth_id), User.is_active.is_(True)))


def is_global(db, user):
    return bool(user and user.user_type == 'internal' and db.get(UserRole, (user.id, 'super_admin')))


def actor(db, auth_id, client_id=None, global_only=False):
    user = active_user(db, auth_id)
    global_admin = is_global(db, user)
    if global_admin:
        return user, True
    if user and client_id and not global_only:
        allowed = db.scalar(select(Membership.id).join(Client, Client.id == Membership.client_id).where(
            Membership.client_id == client_id, Membership.user_id == user.id,
            Membership.is_active.is_(True), Membership.role_code == 'client_admin', Client.is_active.is_(True)))
        if allowed:
            return user, False
    raise Forbidden


def context_for(db, user):
    if not user or not user.is_active:
        return {'allowed': False}
    clients = [{'id': c.id, 'name': c.name, 'role': m.role_code,
                'has_logo': bool(c.logo_path),
                'logo_background': c.logo_background,
                'can_create_sows': role_can_create_sows(m.role_code)}
               for m, c in db.execute(select(Membership, Client).join(Client, Client.id == Membership.client_id)
                   .where(Membership.user_id == user.id, Membership.is_active.is_(True), Client.is_active.is_(True))
                   .order_by(Client.name, Client.id))]
    global_admin = is_global(db, user)
    return {'allowed': global_admin or bool(clients), 'user_id': user.id,
            'display_name': user.display_name, 'is_super_admin': global_admin, 'clients': clients}


def portal_context(auth_id):
    with transaction() as db:
        return context_for(db, active_user(db, auth_id))


def portal_login(identity, provider):
    if provider not in ('google', 'azure'):
        raise Forbidden
    with transaction() as db:
        if not identity.verified:
            db.add(LoginEvent(provider=provider, outcome='denied', reason_code='unverified_identity'))
            return {'allowed': False}
        auth_id = identifier(identity.auth_id)
        email = identity.email.strip().lower()
        user = db.scalar(select(User).where(or_(User.auth_user_id == auth_id,
            (User.auth_user_id.is_(None) & (User.email == email))))
            .order_by(User.auth_user_id.is_not(None).desc()).limit(1).with_for_update())
        if user is None and _registration_available(db, identity):
            db.add(LoginEvent(provider=provider, outcome='success', reason_code='registration_required'))
            return {'allowed': False, 'onboarding': True}
        context = context_for(db, user)
        if not context['allowed']:
            db.add(LoginEvent(user_id=user.id if user else None, provider=provider,
                              outcome='denied', reason_code='not_provisioned_or_inactive'))
            return {'allowed': False}
        user.auth_user_id = auth_id
        user.last_login_at = datetime.now(timezone.utc)
        db.add(LoginEvent(user_id=user.id, provider=provider, outcome='success'))
        return context


def serial(row):
    result = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        result[column.name] = value.isoformat() if isinstance(value, datetime) else value
    return result


def clients(auth_id, query='', page=1, client_id=None):
    client_id = identifier(client_id)
    with transaction() as db:
        actor(db, auth_id, global_only=True)
        if client_id and not db.get(Client, client_id):
            raise NotFound
        filters = []
        if client_id:
            filters.append(Client.id == client_id)
        if query:
            filters.append(func.strpos(func.lower(Client.name + ' ' + func.coalesce(Client.client_code, '') + ' ' + func.coalesce(Client.contact_email, '')), query.lower()) > 0)
        rows = db.scalars(select(Client).where(*filters).order_by(Client.name, Client.id).limit(25).offset((page - 1) * 25)).all()
        industries = {i.id: i for i in db.scalars(select(Industry).order_by(Industry.name))}
        counts = dict(db.execute(select(Membership.client_id, func.count()).where(Membership.is_active.is_(True))
                                 .group_by(Membership.client_id)).all())
        return {'clients': [dict(serial(c), industry_name=industries[c.industry_id].name if c.industry_id in industries else None,
                                  user_count=counts.get(c.id, 0)) for c in rows],
                'total': db.scalar(select(func.count()).select_from(Client).where(*filters)),
                'industries': [serial(i) for i in industries.values()]}


CLIENT_FIELDS = ('name', 'legal_name', 'industry_id', 'website', 'email', 'phone', 'contact_name',
                 'contact_email', 'contact_phone', 'address_line_1', 'address_line_2', 'city',
                 'state_region_code', 'postal_code', 'country_code', 'timezone')


def client_catalogs(auth_id, country_code=None, selected_region=None, selected_timezone=None):
    with transaction() as db:
        actor(db, auth_id, global_only=True)
        return _catalog_rows(db, country_code, selected_region, selected_timezone)


def _catalog_rows(db, country_code=None, selected_region=None, selected_timezone=None):
    country_code = (country_code or '').upper()
    countries = db.scalars(select(Country).where(or_(Country.is_active.is_(True), Country.code == country_code)).order_by(Country.name))
    zones = db.scalars(select(Timezone).where(or_(Timezone.is_active.is_(True), Timezone.name == selected_timezone)).order_by(Timezone.name))
    regions = db.scalars(select(StateRegion).where(StateRegion.country_code == country_code,
        or_(StateRegion.is_active.is_(True), StateRegion.code == selected_region)).order_by(StateRegion.name, StateRegion.code))
    return {'countries': [serial(c) for c in countries], 'timezones': [serial(t) for t in zones],
            'regions': [serial(r) for r in regions]}


def _registration_available(db, identity):
    if not identity.verified:
        return False
    auth_id = identifier(identity.auth_id)
    email = identity.email.strip().lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email) > 254:
        return False
    return db.scalar(select(User.id).where(or_(User.auth_user_id == auth_id, User.email == email)).limit(1)) is None


def registration_catalogs(identity, country_code=None):
    with transaction() as db:
        if not _registration_available(db, identity):
            raise Forbidden
        result = _catalog_rows(db, country_code)
        result['industries'] = [serial(i) for i in db.scalars(select(Industry).where(Industry.is_active.is_(True)).order_by(Industry.name))]
        return result


def register_client(identity, provider, data, owner_name, inquiry=None):
    """One verified identity -> one NEW client tenant, never a global role."""
    if provider not in ('google', 'azure') or not identity.verified:
        raise Forbidden
    owner_name = owner_name.strip()
    if not owner_name or len(owner_name) > 200:
        raise InvalidDetails('Enter your name, up to 200 characters.', 'owner_name')
    if inquiry is not None:
        inquiry = inquiry.strip()
        if not 1 <= len(inquiry) <= 5000:
            raise InvalidDetails('Tell us what you’re looking for in up to 5,000 characters.', 'inquiry')
    with transaction() as db:
        if not _registration_available(db, identity):
            raise Forbidden
        uid = str(uuid4())
        user = User(id=uid, auth_user_id=identifier(identity.auth_id), email=identity.email.strip().lower(),
                    display_name=owner_name, user_type='client', is_active=True, created_by=uid,
                    last_login_at=datetime.now(timezone.utc))
        db.add(user)
        # Unique email/Auth UUID constraints serialize racing registrations. All
        # subsequent work shares this transaction, so a losing request leaves no tenant.
        db.flush()
        client_id = _save_client_record(db, user, {**data, 'is_active': True})
        if inquiry is not None:
            db.get(Client, client_id).registration_inquiry = inquiry
        db.add(Membership(client_id=client_id, user_id=uid, role_code='client_admin', role_scope='client',
                          can_create_sows=True, is_active=True, granted_by=uid))
        db.add(LoginEvent(user_id=uid, provider=provider, outcome='success', reason_code='registration_completed'))
        audit(db, user, client_id, 'client.self_registered', 'user', uid,
              {'role': 'client_admin', 'provider': provider})
        db.flush()
        return context_for(db, user)


def audit(db, acting, client_id, action, entity, entity_id, changes):
    db.add(AuditEvent(actor_id=acting.id, client_id=client_id, action=action,
                      entity_type=entity, entity_id=entity_id, changes=changes))


def save_client(auth_id, data, client_id=None):
    client_id = identifier(client_id)
    with transaction() as db:
        acting, _ = actor(db, auth_id, global_only=True)
        client = db.scalar(select(Client).where(Client.id == client_id).with_for_update()) if client_id else None
        if client_id and not client:
            raise NotFound
        return _save_client_record(db, acting, data, client)


def set_client_logo(auth_id, client_id):
    with transaction() as db:
        acting, _ = actor(db, auth_id, global_only=True)
        client = db.get(Client, identifier(client_id))
        if not client:
            raise NotFound
        client.logo_path = f'{client.id}/brand/logo.png'
        audit(db, acting, client.id, 'client.logo_uploaded', 'client', client.id, {})


def client_logo(auth_id, client_id):
    with transaction() as db:
        client = db.get(Client, identifier(client_id))
        user = active_user(db, auth_id)
        if not client or not client.logo_path or user is None:
            raise NotFound
        if not is_global(db, user) and (not client.is_active or not db.scalar(select(Membership.id).where(
                Membership.client_id == client.id, Membership.user_id == user.id, Membership.is_active.is_(True)))):
            raise NotFound
        return client.logo_path


def _save_client_record(db, acting, data, client=None):
    """Shared persistence after the caller has authorized admin or self-registration."""
    if not data.get('name', '').strip():
        raise InvalidDetails('Client name is required.', 'name')
    if any(len(str(data.get(key) or '')) > 254 for key in CLIENT_FIELDS):
        raise InvalidDetails('Client details are too long.')
    client_id = client.id if client else None
    before = serial(client) if client else None
    cleaned = {}
    logo_background = data.get('logo_background', client.logo_background if client else 'light')
    if logo_background not in ('light', 'dark'):
        raise InvalidDetails('Choose Light or Dark.', 'logo_background')
    cleaned['logo_background'] = logo_background
    for key in CLIENT_FIELDS:
        value = str(data.get(key) or '').strip() or None
        if key == 'industry_id':
            value = identifier(value)
            industry = db.get(Industry, value) if value else None
            if value and (not industry or (not industry.is_active and (not client or value != client.industry_id))):
                raise InvalidDetails('Select an active industry.', 'industry_id')
        if key in ('email', 'contact_email') and value:
            value = value.lower()
        if key == 'country_code' and value:
            value = value.upper()
            if not re.fullmatch('[A-Z]{2}', value):
                raise InvalidDetails('Use a two-letter country code.')
        cleaned[key] = value or ('UTC' if key == 'timezone' else None)
    country = db.get(Country, cleaned['country_code']) if cleaned['country_code'] else None
    if cleaned['country_code'] and (not country or not country.is_active) and (not client or cleaned['country_code'] != client.country_code):
        raise InvalidDetails('Select an active country.', 'country_code')
    zone = db.get(Timezone, cleaned['timezone'])
    if (not zone or not zone.is_active) and (not client or cleaned['timezone'] != client.timezone):
        raise InvalidDetails('Select an active timezone.', 'timezone')
    region_code = cleaned['state_region_code']
    state_name = None
    if region_code == '__legacy__':
        if not client or client.state_region_code or not client.state_region or cleaned['country_code'] != client.country_code:
            raise InvalidDetails('Select a state or region for this country.', 'state_region_code')
        cleaned['state_region_code'] = None
        state_name = client.state_region
    elif region_code:
        region = db.get(StateRegion, region_code)
        if not region or region.country_code != cleaned['country_code'] or (not region.is_active and (not client or region_code != client.state_region_code)):
            raise InvalidDetails('Select a state or region belonging to this country.', 'state_region_code')
        state_name = region.name
    if not client:
        client = Client(created_by=acting.id)
        db.add(client)
    for key, value in cleaned.items():
        setattr(client, key, value)
    client.state_region = state_name
    client.is_active = bool(data.get('is_active', True))
    client.updated_by = acting.id
    db.flush()
    audit(db, acting, client.id, 'client.updated' if client_id else 'client.created', 'client', client.id,
          {'before': before, 'after': serial(client)})
    return client.id


def users(auth_id, client_id=None, query='', user_type='all', page=1, user_id=None):
    client_id, user_id = identifier(client_id), identifier(user_id)
    with transaction() as db:
        actor(db, auth_id, client_id, global_only=not client_id)
        client = db.get(Client, client_id) if client_id else None
        if client_id and not client:
            raise NotFound
        filters = []
        if user_id:
            filters.append(User.id == user_id)
        if client_id:
            filters.append(User.id.in_(select(Membership.user_id).where(Membership.client_id == client_id)))
        if user_type != 'all':
            filters.append(User.user_type == user_type)
        if query:
            filters.append(func.strpos(func.lower(User.display_name + ' ' + User.email + ' ' + User.user_type), query.lower()) > 0)
        total = db.scalar(select(func.count()).select_from(User).where(*filters))
        if user_id and not total:
            raise NotFound
        rows = db.scalars(select(User).where(*filters).order_by(User.display_name, User.id).limit(25).offset((page - 1) * 25)).all()
        memberships = {}
        if rows:
            stmt = select(Membership, Client).join(Client, Client.id == Membership.client_id).where(Membership.user_id.in_([u.id for u in rows]))
            if client_id:
                stmt = stmt.where(Membership.client_id == client_id)
            for m, c in db.execute(stmt.order_by(Client.name, Client.id)):
                memberships.setdefault(m.user_id, []).append({'client_id': c.id, 'client_name': c.name,
                    'role_code': m.role_code, 'can_create_sows': role_can_create_sows(m.role_code), 'is_active': m.is_active})
        result = []
        for u in rows:
            # Explicit fields: Auth UUID and unrelated memberships never leave the service.
            result.append({'id': u.id, 'email': u.email, 'display_name': u.display_name, 'phone': u.phone,
                'user_type': u.user_type, 'is_active': u.is_active, 'identity_linked': u.auth_user_id is not None,
                'last_login_at': (u.last_login_at.replace(tzinfo=timezone.utc) if u.last_login_at.tzinfo is None else u.last_login_at).astimezone(timezone.utc).isoformat() if u.last_login_at else None,
                'is_super_admin': is_global(db, u), 'memberships': memberships.get(u.id, [])})
        return {'users': result, 'total': total, 'client': serial(client) if client else None}


def save_user(auth_id, data, client_id=None, user_id=None):
    client_id, user_id = identifier(client_id), identifier(user_id)
    role = data.get('role_code', 'client_user')
    if client_id and role not in ('client_user', 'client_admin'):
        raise InvalidDetails('Select a client role.')
    with transaction() as db:
        acting, global_admin = actor(db, auth_id, client_id, global_only=not client_id)
        if client_id:
            if not db.scalar(select(Client).where(Client.id == client_id).with_for_update()):
                raise NotFound
            actor(db, auth_id, client_id)
        else:
            db.scalar(select(Role).where(Role.code == 'super_admin').with_for_update())
        user = db.scalar(select(User).where(User.id == user_id).with_for_update()) if user_id else None
        if user_id and not user:
            raise NotFound
        membership = db.scalar(select(Membership).where(Membership.client_id == client_id, Membership.user_id == user.id)) if user and client_id else None
        if user_id and client_id and not membership:
            raise NotFound
        created = False
        if not user_id:
            email = data.get('email', '').strip().lower()
            if len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
                raise InvalidDetails('Enter a valid email address.')
            user = db.scalar(select(User).where(User.email == email).with_for_update())
            if user:
                # Never attach a provisioned identity to another client, including
                # when the caller is a global administrator or access is inactive.
                raise InvalidDetails('This email is already in use.', 'email')
            if not user:
                user = User(email=email, display_name=data.get('display_name', '').strip(),
                            phone=data.get('phone') or None, user_type='client' if client_id else 'internal', created_by=acting.id)
                db.add(user)
                db.flush()
                created = True
        if not global_admin and (user.user_type != 'client' or 'user_active' in data):
            raise Forbidden
        if not client_id and user.user_type != 'internal':
            raise InvalidDetails('Manage this user through their client membership.')
        before = {'name': user.display_name, 'email': user.email, 'phone': user.phone, 'active': user.is_active}
        if user_id and global_admin and 'email' in data:
            email = data['email'].strip().lower()
            if email != user.email:
                # Lock and recheck here: sign-in may have verified this user after
                # the edit form was opened. Never modify the linked Auth identity.
                if user.auth_user_id is not None or user.last_login_at is not None:
                    raise InvalidDetails('This email has been verified and cannot be changed.', 'email')
                if len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
                    raise InvalidDetails('Enter a valid email address.', 'email')
                if db.scalar(select(User.id).where(User.email == email, User.id != user.id)):
                    raise InvalidDetails('This email is already in use.', 'email')
                user.email = email
        if created or (global_admin and user_id):
            name = data.get('display_name', '').strip()
            if not name or len(name) > 200:
                raise InvalidDetails('Enter a name of up to 200 characters.')
            user.display_name = name
            user.phone = data.get('phone') or None
            user.updated_by = acting.id
        if client_id and 'user_active' in data:
            raise InvalidDetails('Use membership status for client access.')
        if not client_id:
            active = bool(data.get('user_active', user.is_active))
            if user.id == acting.id and not active:
                raise InvalidDetails('You cannot deactivate your own administrator account.')
            user.is_active = active
            if not db.get(UserRole, (user.id, 'super_admin')):
                db.add(UserRole(user_id=user.id, role_code='super_admin', granted_by=acting.id))
        else:
            if membership is None:
                membership = db.scalar(select(Membership).where(Membership.client_id == client_id, Membership.user_id == user.id))
            active = bool(data.get('membership_active', True))
            if membership and membership.is_active and membership.role_code == 'client_admin' and (not active or role != 'client_admin'):
                other = db.scalar(select(Membership.id).join(User, User.id == Membership.user_id).where(
                    Membership.client_id == client_id, Membership.user_id != user.id, Membership.is_active.is_(True),
                    Membership.role_code == 'client_admin', User.is_active.is_(True)))
                if not other:
                    raise InvalidDetails('Assign another active client administrator before removing this access.')
            if not membership:
                membership = Membership(client_id=client_id, user_id=user.id)
                db.add(membership)
            membership.role_code = role
            membership.is_active = active
            # Keep the legacy column in sync on save; authorization derives from role.
            membership.can_create_sows = role_can_create_sows(role)
            membership.granted_by = acting.id
        db.flush()
        audit(db, acting, client_id, 'user.provisioned' if created else 'user.access_updated', 'user', user.id,
              {'before': None if created else before, 'after': {'name': user.display_name, 'email': user.email, 'phone': user.phone, 'active': user.is_active},
               'role': role if client_id else 'super_admin', 'membership_active': active,
               'can_create_sows': membership.can_create_sows if membership else False})
        return user.id
