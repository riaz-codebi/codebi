"""Scope-of-work forms and actions. All authorization is rechecked by services."""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from itsdangerous import BadData
from sqlalchemy.exc import SQLAlchemyError

from app.auth import check_csrf, login_required
from app import sow_services as service
from app.notifications import NotificationError, scope_link_signer, scope_review_link, send_notification
from app.services import Forbidden, InvalidDetails, NotFound

ESTIMATE_ACTIONS = ('issue_estimate', 'accept_estimate', 'reject_estimate')

scopes = Blueprint('scopes', __name__, url_prefix='/portal')


@scopes.app_template_filter('central_time')
def central_time(iso_timestamp):
    timestamp = datetime.fromisoformat(iso_timestamp)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(ZoneInfo('America/Chicago')).strftime('%Y-%m-%d %H:%M %Z')


@scopes.app_template_filter('usd')
def usd(amount):
    return f'${amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP):,}'


def call(operation, **kwargs):
    try:
        return operation(g.portal_auth_user_id, **kwargs)
    except Forbidden:
        abort(403)
    except NotFound:
        abort(404)


@scopes.errorhandler(SQLAlchemyError)
@scopes.errorhandler(RuntimeError)
def unavailable(error):
    current_app.logger.warning('Scope-of-work storage unavailable (%s).', type(error).__name__)
    return render_template('portal/unavailable.html', portal_user=g.portal_user), 503


def render(name, **kwargs):
    return render_template('portal/scopes/' + name, portal_user=g.portal_user,
        statuses=service.STATUSES, priorities=service.PRIORITIES, **kwargs)


def notify_scope(client_id, sow_id, action):
    try:
        action, title, recipients = call(service.notification, client_id=str(client_id),
            sow_id=str(sow_id), action=action)
    except InvalidDetails as exc:
        flash(str(exc), 'error')
        return
    except (SQLAlchemyError, RuntimeError) as exc:
        current_app.logger.warning('Scope %s notification lookup failed (%s).', sow_id, type(exc).__name__)
        flash('The change was saved, but notification recipients could not be loaded.', 'error')
        return

    link = scope_review_link(client_id, sow_id)
    if action in ('message_to_author', 'message_to_codebi'):
        if action == 'message_to_codebi':
            recipients = [current_app.config['SCOPE_SUBMISSIONS_TO']]
        subject = 'CodeBI Portal: New message'
        text = f'There is a new message on this scope of work: {title}\n\n{link}'
    elif action == 'issue_estimate':
        subject = 'CodeBI Portal: Estimate awaiting approval'
        text = f'Please review the estimate for: {title}\n\n{link}'
    elif action in ('accept_estimate', 'reject_estimate'):
        recipients = [current_app.config['SCOPE_SUBMISSIONS_TO']]
        decision = 'approved' if action == 'accept_estimate' else 'rejected'
        subject = f'CodeBI Portal: Estimate {decision} by client'
        text = f'The client {decision} the estimate for: {title}\n\n{link}'
    else:
        recipients = [current_app.config['SCOPE_SUBMISSIONS_TO']]
        subject = 'CodeBI Portal: Scope of work submitted'
        text = f'A scope of work was submitted to CodeBI: {title}\n\n{link}'

    accepted = 0
    failure_reasons = set()
    for recipient in recipients:
        try:
            send_notification(recipient, subject, text)
        except NotificationError as exc:
            current_app.logger.warning('Scope %s notification failed: %s', sow_id, exc)
            failure_reasons.add(str(exc))
        except Exception as exc:
            current_app.logger.warning('Scope %s notification send failed (%s).', sow_id, type(exc).__name__)
        else:
            accepted += 1
    if accepted == len(recipients) and action not in ESTIMATE_ACTIONS:
        flash('Notification accepted for delivery.', 'success')
    elif accepted != len(recipients):
        outcome = f'Email accepted for {accepted} of {len(recipients)} recipients.'
        flash(' '.join([outcome, *sorted(failure_reasons)]), 'error')


@scopes.get('/scopes-of-work')
@scopes.get('/clients/<uuid:client_id>/scopes-of-work')
@login_required
def index(client_id=None):
    if client_id is None and not g.portal_user['is_super_admin']:
        clients = g.portal_user['clients']
        if len(clients) == 1:
            return redirect(url_for('scopes.index', client_id=clients[0]['id']))
        abort(403)
    query = request.args.get('q', '').strip()[:200]
    status = request.args.get('status', '')
    try:
        page = max(1, min(100000, int(request.args.get('page', 1))))
    except ValueError:
        page = 1
    if status not in service.STATUSES:
        status = ''
    data = call(service.directory, client_id=str(client_id) if client_id else None,
                query=query, status=status, page=page)
    return render('index.html', data=data, client_id=client_id, query=query, status=status, page=page)


@scopes.route('/clients/<uuid:client_id>/scopes-of-work/new', methods=['GET', 'POST'])
@scopes.route('/clients/<uuid:client_id>/scopes-of-work/<uuid:sow_id>/edit', methods=['GET', 'POST'])
@login_required
def form(client_id, sow_id=None):
    if request.method == 'POST':
        check_csrf()
    try:
        data = call(service.form_data, client_id=str(client_id), sow_id=str(sow_id) if sow_id else None)
    except InvalidDetails as exc:
        flash(str(exc), 'error')
        return redirect(url_for('scopes.detail', client_id=client_id, sow_id=sow_id) if sow_id
                        else url_for('scopes.index', client_id=client_id))
    values = {name: (data['scope'] or {}).get(name) or '' for name in
              ('title', 'description', 'sow_type_id', 'desired_completion_date', 'other_tool_details')}
    values.update(tool_ids=data['selected_tools'])
    errors = {}
    if request.method == 'POST':
        values = {name: request.form.get(name, '').strip() for name in
                  ('title', 'description', 'sow_type_id', 'priority', 'desired_completion_date', 'other_tool_details')}
        values['tool_ids'] = request.form.getlist('tool_ids')
        action = request.form.get('action', 'draft')
        try:
            saved = call(service.save, client_id=str(client_id), sow_id=str(sow_id) if sow_id else None,
                         values=values, action=action)
        except InvalidDetails as exc:
            errors[exc.field or '_form'] = str(exc)
        else:
            flash('Scope of work saved.', 'success')
            if action == 'submit':
                notify_scope(client_id, saved, action)
            return redirect(url_for('scopes.detail', client_id=client_id, sow_id=saved))
    return render('form.html', data=data, client_id=client_id, editing=bool(sow_id),
                  values=values, errors=errors), 422 if errors else 200


@scopes.get('/clients/<uuid:client_id>/scopes-of-work/<uuid:sow_id>')
@login_required
def detail(client_id, sow_id):
    activity_page = max(1, min(request.args.get('activity_page', 1, type=int), 100000))
    data = call(service.detail, client_id=str(client_id), sow_id=str(sow_id), activity_page=activity_page)
    return render('detail.html', data=data, client_id=client_id)


@scopes.get('/review/<uuid:sow_id>')
def review_link(sow_id):
    try:
        client_id, signed_sow_id = scope_link_signer().loads(request.args.get('t', ''), max_age=86400)
    except BadData:
        abort(404)
    if signed_sow_id != str(sow_id):
        abort(404)
    response = redirect(url_for('scopes.detail', client_id=client_id, sow_id=sow_id))
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    return response


@scopes.post('/clients/<uuid:client_id>/scopes-of-work/<uuid:sow_id>/transition')
@login_required
def transition(client_id, sow_id):
    check_csrf()
    action = request.form.get('action')
    try:
        notification_action = call(service.transition, client_id=str(client_id), sow_id=str(sow_id),
                                   action=action, values=request.form)
    except InvalidDetails as exc:
        flash(str(exc), 'error')
        data = call(service.detail, client_id=str(client_id), sow_id=str(sow_id))
        return render('detail.html', data=data, client_id=client_id, review_values=request.form), 422
    else:
        if action not in ESTIMATE_ACTIONS:
            flash('Message posted.' if action == 'send_message' else 'Scope of work updated.', 'success')
        if notification_action:
            notify_scope(client_id, sow_id, notification_action)
    anchor = 'scope-message-composer' if action == 'send_message' else None
    return redirect(url_for('scopes.detail', client_id=client_id, sow_id=sow_id, _anchor=anchor))
