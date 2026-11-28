"""Shared public inquiry form for contact and prospective clients."""
from datetime import datetime, timedelta, timezone
import sqlite3

from flask import current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.client_forms import EMAIL
from app.database import transaction
from app.models import PublicInquiry
from app.notifications import send_notification
from app import inquiry_guard

FIELDS = {'name': 200, 'email': 254, 'company': 200, 'details': 5000}


def received():
    flash('Thanks for reaching out. We received your inquiry and will get back to you.', 'inquiry_received')
    return redirect(url_for(request.endpoint, _anchor='inquiry'), code=303)


def inquiry_page(template, **context):
    values = {field: '' for field in FIELDS}
    errors = {}
    status = 200
    token = None
    if request.method == 'POST':
        from app.auth import check_csrf
        check_csrf()
        if request.form.get('website', '').strip():
            current_app.logger.info('Inquiry rejected (honeypot).')
            return received()
        token = request.form.get('inquiry_token', '')
        form_state = inquiry_guard.check_form(token)
        values = {field: request.form.get(field, '').strip() for field in FIELDS}
        for field, limit in FIELDS.items():
            if not values[field] or len(values[field]) > limit:
                errors[field] = f'Enter a value of up to {limit:,} characters.'
        if not EMAIL.fullmatch(values['email']):
            errors['email'] = 'Enter a valid email address.'
        if form_state == 'invalid':
            errors['_form'] = 'This form expired. Please review your inquiry and submit again.'
            token = None
        elif form_state == 'fast':
            errors['_form'] = 'Please take a moment to review your inquiry, then submit again.'
        if not errors and (issue := inquiry_guard.content_issue(values)):
            errors['details'] = issue
            current_app.logger.info('Inquiry rejected (content).')
        if errors:
            status = 422
        else:
            try:
                recipient = values['email'].lower()
                if not inquiry_guard.reserve_submission(recipient):
                    current_app.logger.info('Inquiry rejected (submission limit).')
                    errors['_form'] = 'Too many inquiries have been submitted. Please try again later.'
                    return render_template(template, inquiry_values=values, inquiry_errors=errors,
                        inquiry_token=token, **context), 429, {'Retry-After': '900'}
                with transaction() as db:
                    if db.bind.dialect.name == 'postgresql':
                        db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                                   {'key': 'public-inquiry:' + recipient})
                    recent = db.scalar(select(PublicInquiry.id).where(PublicInquiry.email == recipient,
                        PublicInquiry.created_at >= datetime.now(timezone.utc) - timedelta(minutes=5)).limit(1))
                    duplicate = db.scalar(select(PublicInquiry.id).where(PublicInquiry.email == recipient,
                        func.lower(PublicInquiry.details) == values['details'].lower(),
                        PublicInquiry.created_at >= datetime.now(timezone.utc) - timedelta(minutes=15)).limit(1))
                    if duplicate is None:
                        db.add(PublicInquiry(**{**values, 'email': recipient}))
            except (SQLAlchemyError, RuntimeError, sqlite3.Error, OSError) as exc:
                current_app.logger.warning('Public inquiry could not be saved (%s).', type(exc).__name__)
                errors['_form'] = 'We couldn’t save your inquiry. Please try again.'
                status = 503
            else:
                if duplicate is not None:
                    return received()
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
                return received()
    return render_template(template, inquiry_values=values, inquiry_errors=errors,
                           inquiry_token=token or inquiry_guard.form_token(), **context), status
