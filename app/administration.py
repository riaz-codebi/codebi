"""Client/user forms backed by Python services and SQLAlchemy transactions."""
from functools import wraps
import re

from flask import Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.auth import check_csrf, login_required
from app.database import transaction
from app.models import PublicInquiry
from app import services
from app.client_forms import CLIENT_FIELDS, EMAIL, catalog_choices, validate_client
from app.client_branding import BrandingUnavailable, MAX_LOGO_SIZE, download_logo, save_branding, validate_logo

administration = Blueprint('administration', __name__, url_prefix='/portal')


class AdminUnavailable(Exception):
    pass


InvalidDetails = services.InvalidDetails


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.portal_user['is_super_admin']:
            abort(403)
        return view(*args, **kwargs)
    return login_required(wrapped)


def require_client_admin(client_id):
    if not g.portal_user['is_super_admin'] and not any(
        str(c['id']) == str(client_id) and c['role'] == 'client_admin' for c in g.portal_user['clients']
    ):
        abort(403)


def call(operation, **params):
    try:
        return operation(g.portal_auth_user_id, **params)
    except services.Forbidden:
        abort(403)
    except services.NotFound:
        abort(404)
    except IntegrityError as exc:
        # PostgreSQL uniqueness closes the race between a lookup and a concurrent
        # create/edit. Keep the same field message as the normal duplicate check.
        constraint = getattr(getattr(exc.orig, 'diag', None), 'constraint_name', None)
        if constraint == 'users_email_key' or str(exc.orig) == 'UNIQUE constraint failed: users.email':
            raise InvalidDetails('This email is already in use.', 'email') from None
        raise InvalidDetails('A conflicting record exists or a referenced value is invalid. Check the details.') from None
    except (SQLAlchemyError, RuntimeError) as exc:
        current_app.logger.warning('Administration connection unavailable (%s).', type(exc).__name__)
        raise AdminUnavailable from None


@administration.errorhandler(AdminUnavailable)
def unavailable(_error):
    return render_template('portal/unavailable.html', portal_user=g.portal_user), 503


def page_args():
    query = request.args.get('q', '').strip()[:200]
    try:
        page = max(1, min(100000, int(request.args.get('page', '1'))))
    except ValueError:
        page = 1
    return query, page


@administration.get('/admin/inquiries')
@admin_required
def inquiries():
    query, page = page_args()
    statement = select(PublicInquiry)
    if query:
        statement = statement.where(or_(*(column.icontains(query, autoescape=True)
            for column in (PublicInquiry.name, PublicInquiry.email, PublicInquiry.company))))
    try:
        with transaction() as db:
            total = db.scalar(select(func.count()).select_from(statement.subquery()))
            rows = db.scalars(statement.order_by(PublicInquiry.created_at.desc(), PublicInquiry.id)
                              .offset((page - 1) * 25).limit(25)).all()
    except (SQLAlchemyError, RuntimeError):
        raise AdminUnavailable from None
    return render_template('portal/inquiries.html', portal_user=g.portal_user, rows=rows,
                           total=total, query=query, page=page)


@administration.get('/admin/clients')
@admin_required
def clients():
    query, page = page_args()
    data = call(services.clients, query=query, page=page)
    return render_template('portal/clients.html', portal_user=g.portal_user, data=data, query=query, page=page)




@administration.route('/admin/clients/new', methods=['GET', 'POST'])
@administration.route('/admin/clients/<uuid:client_id>/edit', methods=['GET', 'POST'])
@admin_required
def client_form(client_id=None):
    if request.method == 'POST':
        if request.content_length and request.content_length > MAX_LOGO_SIZE + 65536:
            abort(413, description='Upload a PNG no larger than 2 MB.')
        check_csrf()
    data = call(services.clients, client_id=str(client_id) if client_id else None)
    existing = data['clients'][0] if client_id else {}
    values = {field: existing.get(field) or '' for field in CLIENT_FIELDS}
    values.update(timezone=existing.get('timezone') or 'UTC', is_active=existing.get('is_active', True))
    values['logo_background'] = existing.get('logo_background') or 'light'
    legacy_state = existing.get('state_region') if not existing.get('state_region_code') else None
    if legacy_state:
        values['state_region_code'] = '__legacy__'
    errors = {}
    if request.method == 'POST':
        values = {field: request.form.get(field, '').strip() for field in CLIENT_FIELDS}
        values.update(is_active=request.form.get('is_active') == 'on')
        values['logo_background'] = request.form.get('logo_background', existing.get('logo_background') or 'light')
        values['timezone'] = values['timezone'] or 'UTC'
        errors = validate_client(values)
        if not errors:
            try:
                logo = validate_logo(request.files.get('logo'))
                saved_id = call(services.save_client, client_id=str(client_id) if client_id else None, data=values)
            except InvalidDetails as exc:
                errors[exc.field or '_form'] = str(exc)
            else:
                try:
                    if logo is not None or not existing.get('logo_path'):
                        save_branding(saved_id, logo)
                    if logo is not None:
                        call(services.set_client_logo, client_id=saved_id)
                except (BrandingUnavailable, AdminUnavailable) as exc:
                    current_app.logger.warning('Client branding update failed (%s).', type(exc).__name__)
                    flash('Client details were saved. The branding update failed. You can retry here.', 'error')
                    return redirect(url_for('administration.client_form', client_id=saved_id))
                flash('Client updated.' if client_id else 'Client created.', 'success')
                return redirect(url_for('administration.clients'))
    catalogs = call(services.client_catalogs, country_code=values['country_code'],
                    selected_region=values['state_region_code'], selected_timezone=values['timezone'])
    choices = catalog_choices(data['industries'], catalogs, values, existing)
    return render_template('portal/client_form.html', portal_user=g.portal_user, values=values, errors=errors,
        choices=choices, editing=bool(client_id), registration_inquiry=existing.get('registration_inquiry'),
        logo_client_id=client_id if existing.get('logo_path') else None), 422 if errors else 200


@administration.get('/clients/<uuid:client_id>/brand/logo.png')
@login_required
def client_brand_logo(client_id):
    path = call(services.client_logo, client_id=str(client_id))
    try:
        content = download_logo(path)
    except BrandingUnavailable:
        abort(503)
    if content is None:
        abort(404)
    return Response(content, mimetype='image/png',
                    headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


@administration.get('/admin/reference/regions')
@admin_required
def reference_regions():
    country = request.args.get('country', '').upper()
    if not re.fullmatch('[A-Z]{2}', country):
        abort(400)
    data = call(services.client_catalogs, country_code=country)
    return jsonify([{'value': r['code'], 'label': r['name']} for r in data['regions']])


@administration.post('/admin/address-suggestions')
@admin_required
def address_suggestions():
    from app.address_lookup import suggestions
    return suggestions()


@administration.get('/admin/users')
@administration.get('/clients/<uuid:client_id>/users')
@login_required
def users(client_id=None):
    if client_id:
        require_client_admin(client_id)
    elif not g.portal_user['is_super_admin']:
        abort(403)
    query, page = page_args()
    user_type = request.args.get('type', 'all')
    if user_type not in ('all', 'internal', 'client'):
        user_type = 'all'
    data = call(services.users, client_id=str(client_id) if client_id else None,
                query=query, user_type=user_type, page=page)
    return render_template('portal/users.html', portal_user=g.portal_user, data=data, client_id=client_id,
                           query=query, page=page, user_type=user_type)


@administration.route('/admin/users/new', methods=['GET', 'POST'])
@administration.route('/admin/users/<uuid:user_id>/edit', methods=['GET', 'POST'])
@administration.route('/clients/<uuid:client_id>/users/new', methods=['GET', 'POST'])
@administration.route('/clients/<uuid:client_id>/users/<uuid:user_id>/edit', methods=['GET', 'POST'])
@login_required
def user_form(client_id=None, user_id=None):
    if client_id:
        require_client_admin(client_id)
    elif not g.portal_user['is_super_admin']:
        abort(403)
    if request.method == 'POST':
        check_csrf()
    data = call(services.users, client_id=str(client_id) if client_id else None,
                user_id=str(user_id) if user_id else None)
    existing = data['users'][0] if user_id else {}
    if user_id and not client_id and existing['user_type'] != 'internal':
        abort(403)
    if not g.portal_user['is_super_admin'] and existing.get('user_type') == 'internal':
        abort(403)
    membership = (existing.get('memberships') or [{}])[0] if client_id else {}
    values = {'display_name': existing.get('display_name') or '', 'email': existing.get('email') or '',
              'phone': existing.get('phone') or '',
              'role_code': membership.get('role_code', 'client_admin' if client_id and data['total'] == 0 else 'client_user'),
              'membership_active': membership.get('is_active', True), 'user_active': existing.get('is_active', True)}
    errors = {}
    can_edit_profile = g.portal_user['is_super_admin'] or not user_id
    identity_linked = existing.get('identity_linked', False) or bool(existing.get('last_login_at'))
    can_edit_email = can_edit_profile and not identity_linked
    if request.method == 'POST':
        if can_edit_profile:
            values.update(display_name=request.form.get('display_name', '').strip(), phone=request.form.get('phone', '').strip())
        if can_edit_email:
            values['email'] = request.form.get('email', '').strip().lower()
        elif identity_linked and 'email' in request.form and request.form['email'].strip().lower() != values['email']:
            errors['email'] = 'This email has been verified and cannot be changed.'
        if client_id:
            values.update(role_code=request.form.get('role_code', ''),
                          membership_active=request.form.get('membership_active') == 'on')
        else:
            values['user_active'] = request.form.get('user_active') == 'on'
        if not values['display_name'] or len(values['display_name']) > 200:
            errors['display_name'] = 'Enter a name of up to 200 characters.'
        if not EMAIL.fullmatch(values['email']) or len(values['email']) > 254:
            errors['email'] = 'Enter a valid email address.'
        if len(values['phone']) > 100:
            errors['phone'] = 'Enter a phone number of up to 100 characters.'
        if client_id and values['role_code'] not in ('client_admin', 'client_user'):
            errors['role_code'] = 'Select a client role.'
        if not errors:
            payload = {key: values[key] for key in ('display_name', 'email', 'phone')}
            payload.update({key: values[key] for key in
                            (('role_code', 'membership_active') if client_id else ('user_active',))})
            try:
                call(services.save_user, client_id=str(client_id) if client_id else None,
                     user_id=str(user_id) if user_id else None, data=payload)
                flash('User updated.' if user_id else 'User provisioned. They can sign in with their registered email; no email notification was sent.', 'success')
                return redirect(url_for('administration.users', **({'client_id': client_id} if client_id else {})))
            except InvalidDetails as exc:
                errors[exc.field or '_form'] = str(exc)
    return render_template('portal/user_form.html', portal_user=g.portal_user, client_id=client_id,
        client=data.get('client'), editing=bool(user_id), values=values, errors=errors,
        can_edit_profile=can_edit_profile, can_edit_email=can_edit_email, identity_linked=identity_linked), 422 if errors else 200
