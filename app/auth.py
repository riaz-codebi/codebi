"""Google/Microsoft PKCE login. Tokens and verifiers never enter browser cookies."""
from functools import wraps
import secrets
import time
import re

from flask import Blueprint, abort, current_app, g, jsonify, redirect, render_template, request, session, url_for
import httpx
from supabase import create_client
from supabase.lib.client_options import SyncClientOptions
from supabase_auth import SyncMemoryStorage
from app import services
from app.client_forms import CLIENT_FIELDS, catalog_choices, validate_client
from app.notifications import send_notification
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

auth = Blueprint('auth', __name__, url_prefix='/auth')
portal = Blueprint('portal', __name__, url_prefix='/portal')


def configured():
    return bool(current_app.config['SUPABASE_URL'] and current_app.config['SUPABASE_KEY'])


def csrf_token():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_urlsafe(32)
    return session['csrf']


def check_csrf():
    expected = session.get('csrf', '')
    supplied = request.form.get('csrf_token', '')
    if not expected or not secrets.compare_digest(expected, supplied):
        abort(400, description='The form expired. Reload the page and try again.')


def store():
    return current_app.extensions['auth_store']


def new_client(storage=None):
    return create_client(current_app.config['SUPABASE_URL'], current_app.config['SUPABASE_KEY'],
                         options=SyncClientOptions(auto_refresh_token=False, persist_session=False,
                                                   flow_type='pkce', storage=storage or SyncMemoryStorage()))


def verify_identity(access_token, provider=None):
    """Use Supabase's user endpoint, not browser claims or editable metadata."""
    result = new_client().auth.get_user(access_token)
    if not result or not result.user:
        raise ValueError('No verified Supabase identity')
    user = result.user
    email = (user.email or '').strip().lower()
    verified = bool(user.email_confirmed_at) and any(
        (identity.provider == provider if provider else identity.provider in ('google', 'azure'))
        and (identity.identity_data or {}).get('email', '').strip().lower() == email
        and (identity.identity_data or {}).get('email_verified') is True
        for identity in user.identities or []
    )
    metadata = getattr(user, 'user_metadata', None) or {}
    name = metadata.get('full_name') or metadata.get('name') or ''
    return services.Identity(str(user.id), email, verified, name.strip()[:200] if isinstance(name, str) else '')


def revoke(payload):
    if payload and payload.get('access_token'):
        try:
            with httpx.Client(timeout=10) as client:
                client.post(current_app.config['SUPABASE_URL'] + '/auth/v1/logout?scope=local',
                            headers={'apikey': current_app.config['SUPABASE_KEY'],
                                     'Authorization': 'Bearer ' + payload['access_token']})
        except httpx.HTTPError:
            current_app.logger.warning('Remote logout unavailable; local session removed.')


def clear_auth():
    store().delete(session.get('sid'))
    store().delete(session.get('pending'))
    store().delete(session.get('onboarding'))
    session.clear()


def failure(message, status):
    return render_template('auth/login.html', auth_message=message, auth_ready=configured()), status


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        sid = session.get('sid')
        payload = store().load(sid)
        if not payload or payload.get('kind') != 'login':
            registration = store().load(session.get('onboarding'))
            if registration and registration.get('kind') == 'onboarding':
                return redirect(url_for('auth.register'))
            clear_auth()
            if request.endpoint == 'scopes.detail':
                session['scope_after_login'] = [str(kwargs['client_id']), str(kwargs['sow_id'])]
            return redirect(url_for('auth.login'))
        try:
            if payload['expires_at'] <= time.time() + 60:
                refreshed = new_client().auth.refresh_session(payload['refresh_token']).session
                if not refreshed:
                    raise ValueError('No refreshed session')
                payload.update(access_token=refreshed.access_token, refresh_token=refreshed.refresh_token,
                               expires_at=refreshed.expires_at)
                store().save(sid, payload)
            if not payload.get('auth_user_id'):
                # Upgrade sessions created before the SQLAlchemy transition.
                identity = verify_identity(payload['access_token'])
                if not identity.verified:
                    raise ValueError('Unverified legacy session')
                payload['auth_user_id'] = identity.auth_id
                store().save(sid, payload)
            context = services.portal_context(payload['auth_user_id'])
        except Exception as exc:
            # Never log SDK exception text, which can contain tokens or request URLs.
            current_app.logger.warning('Portal session validation failed (%s).', type(exc).__name__)
            return failure('Your session could not be verified. Please try signing in again.', 503)
        if not context or not context.get('allowed'):
            revoke(payload)
            clear_auth()
            return failure('Your account does not currently have access to this portal.', 403)
        g.portal_user = context
        g.portal_auth_user_id = payload['auth_user_id']
        g.portal_access_token = payload['access_token']
        return view(*args, **kwargs)
    return wrapped


@auth.get('/login')
def login():
    return render_template('auth/login.html', auth_ready=configured())


@auth.post('/login/<provider>')
def start(provider):
    if provider not in ('google', 'azure'):
        abort(404)
    check_csrf()
    if not configured():
        return failure('Sign-in is not configured yet. Please contact CodeBI.', 503)
    memory = SyncMemoryStorage()
    try:
        result = new_client(memory).auth.sign_in_with_oauth({
            'provider': provider,
            'options': {'redirect_to': current_app.config['APP_BASE_URL'] + '/auth/callback',
                        'scopes': 'email' if provider == 'azure' else 'openid email profile'},
        })
        verifier = next(value for key, value in memory.storage.items() if key.endswith('-code-verifier'))
    except Exception as exc:
        current_app.logger.warning('OAuth start failed (%s).', type(exc).__name__)
        return failure('Sign-in could not be started. Please try again.', 503)
    # Starting another login invalidates old pending state and rotates the browser cookie.
    old_payload = store().load(session.get('sid')) or store().load(session.get('onboarding'))
    scope_after_login = session.get('scope_after_login')
    revoke(old_payload)
    clear_auth()
    session['pending'] = store().create({'kind': 'pending', 'provider': provider, 'verifier': verifier,
                                         'scope_after_login': scope_after_login}, 600)
    return redirect(result.url)


@auth.get('/callback')
def callback():
    pending = store().load(session.pop('pending', None), consume=True)
    if not pending or pending.get('kind') != 'pending':
        return failure('This sign-in attempt expired or was already used. Please start again.', 400)
    if request.args.get('error') or not request.args.get('code'):
        return failure('Sign-in was cancelled or could not be completed. Please try again.', 400)
    payload = None
    try:
        result = new_client().auth.exchange_code_for_session({
            'auth_code': request.args['code'], 'code_verifier': pending['verifier']})
        if not result.session:
            raise ValueError('No OAuth session')
        payload = {'kind': 'login', 'access_token': result.session.access_token,
                   'refresh_token': result.session.refresh_token, 'expires_at': result.session.expires_at}
        identity = verify_identity(payload['access_token'], pending['provider'])
        context = services.portal_login(identity, pending['provider'])
        payload['auth_user_id'] = identity.auth_id
    except Exception as exc:
        current_app.logger.warning('OAuth callback failed (%s).', type(exc).__name__)
        revoke(payload)
        clear_auth()
        return failure('Sign-in could not be completed. Please contact CodeBI if it continues.', 503)
    if context and context.get('onboarding') and identity.verified:
        revoke(payload)
        clear_auth()
        return redirect(url_for('main.contact', _anchor='inquiry'))
    if not context or not context.get('allowed'):
        revoke(payload)
        clear_auth()
        return failure('Your account is not provisioned for portal access, or its email could not be verified. Contact CodeBI.', 403)
    clear_auth()
    session['sid'] = store().create(payload, 8 * 60 * 60)
    if pending.get('scope_after_login'):
        client_id, sow_id = pending['scope_after_login']
        return redirect(url_for('scopes.detail', client_id=client_id, sow_id=sow_id))
    return redirect(url_for('portal.home'))


@auth.post('/logout')
def logout():
    check_csrf()
    revoke(store().load(session.get('sid')) or store().load(session.get('onboarding')))
    clear_auth()
    return redirect(url_for('main.index'))


def onboarding_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        payload = store().load(session.get('onboarding'))
        if not payload or payload.get('kind') != 'onboarding' or payload.get('verified') is not True:
            session.pop('onboarding', None)
            existing = store().load(session.get('sid'))
            if existing and existing.get('kind') == 'login':
                return redirect(url_for('portal.home'))
            return redirect(url_for('auth.login'))
        try:
            if payload['expires_at'] <= time.time() + 60:
                refreshed = new_client().auth.refresh_session(payload['refresh_token']).session
                if not refreshed:
                    raise ValueError('No refreshed onboarding session')
                payload.update(access_token=refreshed.access_token, refresh_token=refreshed.refresh_token,
                               expires_at=refreshed.expires_at)
                store().save(session['onboarding'], payload)
        except Exception as exc:
            current_app.logger.warning('Onboarding session refresh failed (%s).', type(exc).__name__)
            return failure('Please sign in again to continue account setup.', 503)
        g.registration_payload = payload
        g.registration_identity = services.Identity(payload['auth_user_id'], payload['email'], True, payload.get('display_name', ''))
        return view(*args, **kwargs)
    return wrapped


@auth.route('/register/client', methods=['GET', 'POST'])
@onboarding_required
def register():
    if request.method == 'POST':
        check_csrf()
    payload = g.registration_payload
    identity = g.registration_identity
    owner_name = identity.display_name or identity.email.split('@')[0]
    inquiry = ''
    values = {key: '' for key in CLIENT_FIELDS}
    values.update(email=identity.email, contact_email=identity.email, contact_name=identity.display_name,
                  timezone='UTC', is_active=True)
    errors = {}
    try:
        if request.method == 'POST':
            owner_name = request.form.get('owner_name', '').strip()
            inquiry = request.form.get('inquiry', '').strip()
            values = {key: request.form.get(key, '').strip() for key in CLIENT_FIELDS}
            values.update(timezone=values['timezone'] or 'UTC', is_active=True)
            errors = validate_client(values)
            if not owner_name or len(owner_name) > 200:
                errors['owner_name'] = 'Enter your name, up to 200 characters.'
            if not errors:
                fresh = verify_identity(payload['access_token'], payload['provider'])
                if not fresh.verified or fresh.auth_id != identity.auth_id or fresh.email != identity.email:
                    raise services.Forbidden
                try:
                    context = services.register_client(fresh, payload['provider'], values, owner_name, inquiry=inquiry)
                except services.InvalidDetails as exc:
                    errors[exc.field or '_form'] = str(exc)
                except IntegrityError:
                    errors['_form'] = 'This account may already be registered. Please sign in again to continue.'
                else:
                    if not context.get('allowed'):
                        raise services.Forbidden
                    clear_auth()
                    payload['kind'] = 'login'
                    session['sid'] = store().create(payload, 8 * 60 * 60)
                    try:
                        send_notification(fresh.email, 'Thanks for contacting CodeBI Solutions',
                            'Thank you for your interest in CodeBI Solutions. We received your inquiry '
                            'and will get back to you about what you’re looking for.')
                    except Exception as exc:
                        current_app.logger.warning('Registration confirmation email failed (%s).', type(exc).__name__)
                    return redirect(url_for('portal.home'))
        catalogs = services.registration_catalogs(identity, values['country_code'])
        choices = catalog_choices(catalogs['industries'], catalogs, values)
    except services.Forbidden:
        clear_auth()
        return failure('Account setup is not available for this identity. Please sign in or contact CodeBI.', 403)
    except (SQLAlchemyError, RuntimeError) as exc:
        current_app.logger.warning('Client registration database unavailable (%s).', type(exc).__name__)
        return failure('Account setup is temporarily unavailable. Please try again.', 503)
    except Exception as exc:
        current_app.logger.warning('Client registration verification failed (%s).', type(exc).__name__)
        return failure('Please sign in again to verify your account before continuing.', 503)
    return render_template('auth/register.html', values=values, choices=choices, errors=errors,
                           identity=identity, owner_name=owner_name, inquiry=inquiry, onboarding=True), 422 if errors else 200


@auth.get('/register/regions')
@onboarding_required
def register_regions():
    country = request.args.get('country', '').upper()
    if not re.fullmatch('[A-Z]{2}', country):
        abort(400)
    try:
        catalogs = services.registration_catalogs(g.registration_identity, country)
    except services.Forbidden:
        abort(403)
    except (SQLAlchemyError, RuntimeError):
        return jsonify({'error': 'Reference data is temporarily unavailable.'}), 503
    return jsonify([{'value': r['code'], 'label': r['name']} for r in catalogs['regions']])


@auth.post('/register/address-suggestions')
@onboarding_required
def registration_address_suggestions():
    check_csrf()
    try:
        services.registration_catalogs(g.registration_identity)
    except services.Forbidden:
        abort(403)
    from app.address_lookup import suggestions
    return suggestions()


@portal.get('/')
@login_required
def home():
    return render_template('portal/dashboard.html', portal_user=g.portal_user)


@portal.get('/admin')
@login_required
def admin():
    if not g.portal_user['is_super_admin']:
        abort(403)
    return render_template('portal/dashboard.html', portal_user=g.portal_user, admin_view=True)
