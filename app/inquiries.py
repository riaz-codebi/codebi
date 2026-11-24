"""Shared public inquiry form for contact and prospective clients."""
from datetime import datetime, timedelta, timezone

from flask import current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.client_forms import EMAIL
from app.database import transaction
from app.models import PublicInquiry
from app.notifications import send_notification

FIELDS = {'name': 200, 'email': 254, 'company': 200, 'details': 5000}


def inquiry_page(template, **context):
    values = {field: '' for field in FIELDS}
    errors = {}
    status = 200
    if request.method == 'POST':
        from app.auth import check_csrf
        check_csrf()
        values = {field: request.form.get(field, '').strip() for field in FIELDS}
        for field, limit in FIELDS.items():
            if not values[field] or len(values[field]) > limit:
                errors[field] = f'Enter a value of up to {limit:,} characters.'
        if not EMAIL.fullmatch(values['email']):
            errors['email'] = 'Enter a valid email address.'
        if errors:
            status = 422
        else:
            try:
                recipient = values['email'].lower()
                with transaction() as db:
                    if db.bind.dialect.name == 'postgresql':
                        db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                                   {'key': 'public-inquiry:' + recipient})
                    recent = db.scalar(select(PublicInquiry.id).where(PublicInquiry.email == recipient,
                        PublicInquiry.created_at >= datetime.now(timezone.utc) - timedelta(minutes=5)).limit(1))
                    db.add(PublicInquiry(**{**values, 'email': recipient}))
            except (SQLAlchemyError, RuntimeError) as exc:
                current_app.logger.warning('Public inquiry could not be saved (%s).', type(exc).__name__)
                errors['_form'] = 'We couldn’t save your inquiry. Please try again.'
                status = 503
            else:
                try:
                    send_notification(current_app.config['SCOPE_SUBMISSIONS_TO'], 'CodeBI: New website inquiry',
                        f"Name: {values['name']}\nEmail: {recipient}\nCompany: {values['company']}\n\n{values['details']}")
                except Exception as exc:
                    current_app.logger.warning('CodeBI inquiry notification failed (%s).', type(exc).__name__)
                if recent is None:
                    try:
                        send_notification(recipient, 'Thanks for contacting CodeBI Solutions',
                            'Thank you for your interest in CodeBI Solutions. We received your inquiry '
                            'and will get back to you.')
                    except Exception as exc:
                        current_app.logger.warning('Public inquiry confirmation failed (%s).', type(exc).__name__)
                flash('Thanks for reaching out. We received your inquiry and will get back to you.', 'inquiry_received')
                return redirect(url_for(request.endpoint, _anchor='inquiry'), code=303)
    return render_template(template, inquiry_values=values, inquiry_errors=errors, **context), status
