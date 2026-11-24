"""Scope-of-work workflows with tenant checks on every operation."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re

from sqlalchemy import delete, func, or_, select

from app.database import transaction
from app.models import Client, Membership, ScopeOfWork, SowEstimate, SowEvent, SowMessage, SowTool, SowType, TimeEntry, Tool, User
from app.services import Forbidden, InvalidDetails, NotFound, active_user, audit, identifier, is_global, role_can_create_sows

STATUSES = {'draft': 'Draft', 'pending_client_approval': 'Ready to submit',
    'submitted': 'Submitted to CodeBI', 'under_review': 'Under review',
    'clarification_needed': 'Clarification needed', 'awaiting_acceptance': 'Awaiting acceptance',
    'approved': 'Approved', 'in_progress': 'In progress', 'completed': 'Completed',
    'declined': 'Rejected', 'cancelled': 'Cancelled'}
PRIORITIES = ('normal', 'low', 'high', 'urgent')
SCOPE_TYPE_ORDER = ('Discovery', 'New Development', 'Enhancement', 'Maintenance', 'Troubleshooting')
PRIVATE_STATUSES = ('draft', 'pending_client_approval')


def access(db, auth_id, client_id=None, writing=False):
    user = active_user(db, auth_id)
    if user is None:
        raise Forbidden
    internal = is_global(db, user)
    if client_id is None:
        if not internal:
            raise Forbidden
        return user, None, None, True
    client = db.get(Client, identifier(client_id))
    membership = db.scalar(select(Membership).where(Membership.client_id == client_id,
        Membership.user_id == user.id, Membership.is_active.is_(True)))
    if not internal and (not client or not client.is_active or not membership or not role_can_create_sows(membership.role_code)):
        raise Forbidden
    if not client:
        raise NotFound
    if writing and not client.is_active:
        raise InvalidDetails('This client is inactive.')
    return user, client, membership, internal


def client_admin(membership):
    return bool(membership and membership.role_code == 'client_admin')


def record(db, user, client, membership, internal, sow_id, lock=False):
    query = select(ScopeOfWork).where(ScopeOfWork.id == identifier(sow_id), ScopeOfWork.client_id == client.id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None or (row.status in PRIVATE_STATUSES and
                       not internal and not client_admin(membership) and row.created_by != user.id):
        raise NotFound
    return row


def serial(row):
    return {name: (value.isoformat() if isinstance(value, (date, datetime)) else value)
        for name in ('id', 'sow_code', 'client_id', 'title', 'description', 'sow_type_id', 'priority',
                     'desired_completion_date', 'other_tool_details', 'status', 'created_by', 'submitted_at', 'created_at', 'updated_at')
        for value in [getattr(row, name)]}


def directory(auth_id, client_id=None, query='', status='', page=1):
    client_id = identifier(client_id)
    with transaction() as db:
        user, client, membership, internal = access(db, auth_id, client_id)
        filters = []
        if client:
            filters.append(ScopeOfWork.client_id == client.id)
        if not internal and not client_admin(membership):
            filters.append(or_(ScopeOfWork.created_by == user.id, ScopeOfWork.status.not_in(PRIVATE_STATUSES)))
        if query:
            term = query[:200].casefold()
            filters.append(or_(func.strpos(func.lower(ScopeOfWork.title), term) > 0,
                func.strpos(func.lower(func.coalesce(ScopeOfWork.sow_code, '')), term) > 0))
        if status:
            if status not in STATUSES:
                raise InvalidDetails('Select a valid status.')
            filters.append(ScopeOfWork.status == status)
        total = db.scalar(select(func.count()).select_from(ScopeOfWork).where(*filters))
        rows = db.execute(select(ScopeOfWork, Client.name, User.display_name)
            .join(Client, Client.id == ScopeOfWork.client_id).join(User, User.id == ScopeOfWork.created_by)
            .where(*filters).order_by(ScopeOfWork.created_at.desc(), ScopeOfWork.id)
            .limit(25).offset((page - 1) * 25)).all()
        return {'scopes': [dict(serial(s), client_name=name, creator_name=creator) for s, name, creator in rows],
                'total': total, 'client': {'id': client.id, 'name': client.name, 'is_active': client.is_active} if client else None}


def form_data(auth_id, client_id, sow_id=None):
    client_id = identifier(client_id)
    with transaction() as db:
        user, client, membership, internal = access(db, auth_id, client_id, writing=True)
        row = record(db, user, client, membership, internal, sow_id) if sow_id else None
        if row and row.status != 'draft':
            raise InvalidDetails('Only drafts can be edited. Return this scope of work to draft first.')
        selected = list(db.scalars(select(SowTool.tool_id).where(SowTool.sow_id == row.id))) if row else []
        types = db.scalars(select(SowType).where(or_(SowType.is_active.is_(True), SowType.id == row.sow_type_id if row else False)).order_by(SowType.name)).all()
        tools = db.scalars(select(Tool).where(or_(Tool.is_active.is_(True), Tool.id.in_(selected))).order_by(Tool.name)).all()
        type_position = {name: position for position, name in enumerate(SCOPE_TYPE_ORDER)}
        types.sort(key=lambda item: type_position.get(item.name, len(type_position)))
        tools.sort(key=lambda item: item.name.casefold() == 'other')
        return {'client': {'id': client.id, 'name': client.name}, 'scope': serial(row) if row else None,
                'selected_tools': selected, 'types': [{'id': t.id, 'name': t.name, 'active': t.is_active} for t in types],
                'tools': [{'id': t.id, 'name': t.name, 'active': t.is_active, 'is_other': t.name.casefold() == 'other'} for t in tools],
                'can_submit': membership is not None}


def event(db, user, row, kind, before, details=None, message_id=None, estimate_id=None):
    db.add(SowEvent(client_id=row.client_id, sow_id=row.id, actor_id=user.id,
                    event_type=kind, from_status=before, to_status=row.status,
                    details=details or {}, message_id=message_id, estimate_id=estimate_id))
    audit(db, user, row.client_id, 'scope.' + kind, 'statement_of_work', row.id,
          {'from_status': before, 'to_status': row.status, **(details or {})})


def move(row, action, user, membership, internal):
    before = row.status
    if action == 'submit' and before in PRIVATE_STATUSES:
        if not membership:
            raise Forbidden
        row.status = 'submitted'
        row.submitted_by = user.id
        row.submitted_at = datetime.now(timezone.utc)
    elif action == 'return_to_draft' and before == 'pending_client_approval':
        if not client_admin(membership) and row.created_by != user.id:
            raise Forbidden
        row.status = 'draft'
    elif action == 'start_review' and before == 'submitted':
        if not internal:
            raise Forbidden
        row.status = 'under_review'
    elif action == 'start_work' and before == 'approved':
        if not internal:
            raise Forbidden
        row.status = 'in_progress'
        row.started_at = datetime.now(timezone.utc)
    elif action == 'complete_work' and before == 'in_progress':
        if not internal:
            raise Forbidden
        row.status = 'completed'
        row.completed_at = datetime.now(timezone.utc)
    else:
        raise InvalidDetails('This action is no longer available. Refresh the scope of work.')
    return before


def save(auth_id, client_id, values, sow_id=None, action='draft'):
    client_id = identifier(client_id)
    if action not in ('draft', 'submit'):
        raise InvalidDetails('Select a valid action.')
    with transaction() as db:
        user, client, membership, internal = access(db, auth_id, client_id, writing=True)
        row = record(db, user, client, membership, internal, sow_id, lock=True) if sow_id else None
        if row and row.status != 'draft':
            raise InvalidDetails('Only drafts can be edited.')
        title, description = str(values.get('title', '')).strip(), str(values.get('description', '')).strip()
        if not title or len(title) > 200:
            raise InvalidDetails('Enter a title of up to 200 characters.', 'title')
        if not description or len(description) > 20000:
            raise InvalidDetails('Enter a description of up to 20,000 characters.', 'description')
        try:
            type_id = identifier(values.get('sow_type_id') or '')
        except InvalidDetails:
            raise InvalidDetails('Select a scope type.', 'sow_type_id') from None
        kind = db.get(SowType, type_id)
        if not kind or (not kind.is_active and (not row or row.sow_type_id != type_id)):
            raise InvalidDetails('Select an active scope type.', 'sow_type_id')
        priority = values.get('priority', 'normal')
        if priority != 'normal':
            raise InvalidDetails('Normal is the available priority for client requests.', 'priority')
        try:
            deadline = date.fromisoformat(values['desired_completion_date']) if values.get('desired_completion_date') else None
        except (ValueError, TypeError):
            raise InvalidDetails('Enter a valid date.', 'desired_completion_date') from None
        try:
            if len(values.get('tool_ids', [])) > 30:
                raise InvalidDetails('Select up to 30 tools.', 'tool_ids')
            tool_ids = set(identifier(value) for value in values.get('tool_ids', []))
        except (InvalidDetails, TypeError):
            raise InvalidDetails('Select valid tools.', 'tool_ids') from None
        previous_tools = set(db.scalars(select(SowTool.tool_id).where(SowTool.sow_id == row.id))) if row else set()
        other_selected = False
        for tool_id in tool_ids:
            tool = db.get(Tool, tool_id)
            if not tool or (not tool.is_active and tool_id not in previous_tools):
                raise InvalidDetails('Select active tools.', 'tool_ids')
            other_selected |= tool.name.casefold() == 'other'
        other_details = str(values.get('other_tool_details') or '').strip() if other_selected else ''
        if len(other_details) > 1000:
            raise InvalidDetails('Keep the tool description to 1,000 characters.', 'other_tool_details')
        created = row is None
        if created:
            row = ScopeOfWork(client_id=client.id, created_by=user.id, status='draft')
            db.add(row)
        row.title, row.description, row.sow_type_id = title, description, type_id
        row.priority, row.desired_completion_date = priority, deadline
        row.other_tool_details = other_details or None
        row.updated_by, row.updated_at = user.id, datetime.now(timezone.utc)
        db.flush()
        removed = previous_tools - tool_ids
        if removed:
            db.execute(delete(SowTool).where(SowTool.sow_id == row.id, SowTool.tool_id.in_(removed)))
        for tool_id in tool_ids - previous_tools:
            db.add(SowTool(sow_id=row.id, tool_id=tool_id))
        event(db, user, row, 'created' if created else 'updated', None if created else 'draft',
              {'title': row.title, 'tool_count': len(tool_ids)})
        if action != 'draft':
            before = move(row, action, user, membership, internal)
            event(db, user, row, action, before)
        db.flush()
        return row.id


def latest_estimate(db, row):
    return db.scalar(select(SowEstimate).where(SowEstimate.client_id == row.client_id,
        SowEstimate.sow_id == row.id).order_by(SowEstimate.revision.desc()).limit(1))


def required_text(value, label, limit=10000):
    value = value.strip()
    if not value or len(value) > limit:
        raise InvalidDetails(f'Enter {label} of up to {limit:,} characters.')
    return value


def add_message(db, row, user, values):
    message = SowMessage(client_id=row.client_id, sow_id=row.id, author_id=user.id,
        message_type='general',
        body=required_text(values.get('body', ''), 'a message'))
    db.add(message)
    db.flush()
    return message.id


def positive_decimal(values, key, label, whole=False):
    try:
        value = values.get(key, '').strip()
        if key == 'project_amount' and re.fullmatch(r'\d{1,3}(?:,\d{3})+', value):
            value = value.replace(',', '')
        amount = Decimal(value)
        if not (amount.is_finite() and 0 < amount <= Decimal('99999999.99')
                and amount == amount.quantize(Decimal('1' if whole else '.01'))):
            raise InvalidOperation
    except InvalidOperation:
        precision = 'in whole dollars' if whole else 'with up to two decimal places'
        raise InvalidDetails(f'Enter a positive {label} {precision}.') from None
    return amount


def estimate_pricing(values):
    kind = values.get('pricing_type')
    if kind not in ('hourly', 'project'):
        raise InvalidDetails('Choose hourly or fixed project pricing.')
    try:
        completion = date.fromisoformat(values.get('estimated_completion', ''))
    except ValueError:
        raise InvalidDetails('Enter an estimated completion date.') from None
    if kind == 'hourly':
        low = positive_decimal(values, 'min_hours', 'minimum hours value')
        high = positive_decimal(values, 'max_hours', 'maximum hours value')
        rate = positive_decimal(values, 'hourly_rate', 'hourly rate', whole=True)
        if low > high:
            raise InvalidDetails('Maximum hours must be at least the minimum hours.')
        return {'pricing_type': kind, 'min_hours': low, 'max_hours': high, 'hourly_rate': rate,
                'min_amount': (low * rate).quantize(Decimal('.01'), rounding=ROUND_HALF_UP),
                'max_amount': (high * rate).quantize(Decimal('.01'), rounding=ROUND_HALF_UP),
                'estimated_completion': completion}
    amount = positive_decimal(values, 'project_amount', 'fixed project amount', whole=True)
    return {'pricing_type': kind, 'min_amount': amount, 'max_amount': amount,
            'estimated_completion': completion}


def issue_estimate(db, row, user, values):
    pricing = estimate_pricing(values)
    assumptions, exclusions = (values.get(key, '').strip() for key in ('assumptions', 'exclusions'))
    if max(len(assumptions), len(exclusions)) > 10000:
        raise InvalidDetails('Keep assumptions and exclusions to 10,000 characters each.')
    previous = latest_estimate(db, row)
    estimate = SowEstimate(client_id=row.client_id, sow_id=row.id, revision=previous.revision + 1 if previous else 1,
        created_by=user.id, scope_snapshot=required_text(values.get('scope_snapshot', ''), 'the estimated scope', 20000),
        feasibility='feasible', assumptions=assumptions or None, exclusions=exclusions or None,
        status='issued', issued_at=datetime.now(timezone.utc), **pricing)
    db.add(estimate)
    db.flush()
    row.status = 'awaiting_acceptance'
    return estimate.id


def transition(auth_id, client_id, sow_id, action, values=None):
    client_id = identifier(client_id)
    values = values or {}
    with transaction() as db:
        user, client, membership, internal = access(db, auth_id, client_id, writing=True)
        row = record(db, user, client, membership, internal, sow_id, lock=True)
        before = row.status
        message_id = estimate_id = None
        details = {}
        notification_action = None
        if action in ('issue_estimate', 'decline') and before == 'under_review':
            if not internal:
                raise Forbidden
            if action == 'issue_estimate':
                estimate_id = issue_estimate(db, row, user, values)
                notification_action = action
            else:
                message_id = add_message(db, row, user, values)
                row.status = 'declined'
        elif action == 'send_message' and before not in PRIVATE_STATUSES and before != 'cancelled':
            if not internal and not membership:
                raise Forbidden
            message_id = add_message(db, row, user, values)
            if not internal and before == 'clarification_needed':
                row.status = 'under_review'
            notification_action = 'message_to_author' if internal else 'message_to_codebi'
        elif action in ('accept_estimate', 'reject_estimate'):
            if not client_admin(membership):
                raise Forbidden
            estimate = latest_estimate(db, row)
            if not estimate or values.get('estimate_id') != estimate.id:
                raise InvalidDetails('This estimate has changed. Review the latest estimate before deciding.')
            accepted = action == 'accept_estimate'
            decision = 'accepted' if accepted else 'rejected'
            if estimate.status == decision:
                return None
            if before != 'awaiting_acceptance' or estimate.status != 'issued':
                raise InvalidDetails('This estimate has already been decided or is no longer available.')
            estimate.status = decision
            estimate.decided_by, estimate.decided_at = user.id, datetime.now(timezone.utc)
            estimate_id = estimate.id
            row.status = 'approved' if accepted else 'declined'
            row.accepted_estimate_id = estimate.id if accepted else None
            if values.get('body', '').strip():
                message_id = add_message(db, row, user, values)
            notification_action = action
        elif action == 'log_time' and before == 'in_progress':
            if not internal:
                raise Forbidden
            try:
                minutes = int(values.get('minutes', ''))
                if not 1 <= minutes <= 1440:
                    raise ValueError
            except ValueError:
                raise InvalidDetails('Enter between 1 and 1,440 minutes.') from None
            ended = datetime.now(timezone.utc)
            db.add(TimeEntry(client_id=row.client_id, sow_id=row.id, worker_id=user.id,
                activity=required_text(values.get('body', ''), 'a work description'),
                started_at=ended - timedelta(minutes=minutes), ended_at=ended, status='submitted'))
            details = {'minutes': minutes}
        else:
            move(row, action, user, membership, internal)
            if action == 'submit':
                notification_action = action
        row.updated_by, row.updated_at = user.id, datetime.now(timezone.utc)
        event(db, user, row, action, before, details, message_id, estimate_id)
        return notification_action


def notification(auth_id, client_id, sow_id, action):
    with transaction() as db:
        user, client, membership, internal = access(db, auth_id, identifier(client_id), writing=True)
        row = record(db, user, client, membership, internal, sow_id)
        recipients = []
        if action == 'message_to_author':
            author = db.get(User, row.created_by)
            author_membership = db.scalar(select(Membership.id).where(Membership.client_id == client.id,
                Membership.user_id == author.id, Membership.is_active.is_(True)))
            if not author.is_active or not (author_membership or is_global(db, author)):
                raise InvalidDetails('The request author no longer has access to this scope.')
            recipients = [author.email]
        elif action == 'issue_estimate':
            approvers = db.execute(select(User.id, User.email)
                .join(Membership, Membership.user_id == User.id)
                .where(Membership.client_id == client.id, Membership.role_code == 'client_admin',
                       Membership.is_active.is_(True), User.is_active.is_(True))
                .distinct().order_by(User.email)).all()
            recipients = [email for user_id, email in approvers if user_id == row.created_by]
            if not recipients:
                recipients = [email for _, email in approvers]
            if not recipients:
                raise InvalidDetails('No active client admin can receive the approval email.')
        return action, row.title, recipients


def detail(auth_id, client_id, sow_id, activity_page=1):
    client_id = identifier(client_id)
    with transaction() as db:
        user, client, membership, internal = access(db, auth_id, client_id)
        row = record(db, user, client, membership, internal, sow_id)
        kind = db.get(SowType, row.sow_type_id)
        creator = db.get(User, row.created_by)
        tools = db.scalars(select(Tool.name).join(SowTool, SowTool.tool_id == Tool.id).where(SowTool.sow_id == row.id).order_by(Tool.name)).all()
        history = db.execute(select(SowEvent, User.display_name, SowMessage.body)
            .join(User, User.id == SowEvent.actor_id)
            .outerjoin(SowMessage, (SowMessage.id == SowEvent.message_id)
                       & (SowMessage.client_id == SowEvent.client_id)
                       & (SowMessage.sow_id == SowEvent.sow_id))
            .where(SowEvent.sow_id == row.id, SowEvent.client_id == client.id)
            .order_by(SowEvent.occurred_at.desc(), SowEvent.id)
            .offset((activity_page - 1) * 50).limit(51)).all()
        estimate = latest_estimate(db, row)
        can_approve = client.is_active and client_admin(membership)
        can_manage = client.is_active and internal
        return {'client': {'id': client.id, 'name': client.name},
            'scope': dict(serial(row), type_name=kind.name if kind else '', creator_name=creator.display_name if creator else '', tools=list(tools)),
            'can_edit': client.is_active and row.status == 'draft',
            'can_submit': client.is_active and membership is not None and row.status in PRIVATE_STATUSES,
            'can_return': client.is_active and row.status == 'pending_client_approval' and (can_approve or row.created_by == user.id),
            'can_review': can_manage and row.status == 'submitted',
            'can_assess': can_manage and row.status == 'under_review',
            'can_message': client.is_active and row.status not in PRIVATE_STATUSES and row.status != 'cancelled',
            'can_decide': can_approve and row.status == 'awaiting_acceptance' and estimate and estimate.status == 'issued',
            'can_start': can_manage and row.status == 'approved',
            'can_complete': can_manage and row.status == 'in_progress',
            'estimate': estimate,
            'activity_page': activity_page,
            'has_older_activity': len(history) > 50,
            'history': [{'kind': e.event_type, 'actor': name, 'status': e.to_status,
                         'minutes': e.details.get('minutes'), 'message': body,
                         'at': e.occurred_at.isoformat()} for e, name, body in history[:50]]}
