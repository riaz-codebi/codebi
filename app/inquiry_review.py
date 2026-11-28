"""Staff moderation; public submissions never invoke the email provider."""
from datetime import datetime,timedelta,timezone

from flask import current_app,render_template
from sqlalchemy import select

from app import services
from app.database import transaction
from app.models import PublicInquiry
from app.notifications import send_notification


def locked_inquiry(db,auth_id,inquiry_id):
    user,_=services.actor(db,auth_id,global_only=True)
    row=db.scalar(select(PublicInquiry).where(PublicInquiry.id==services.identifier(inquiry_id)).with_for_update())
    if row is None:raise services.NotFound
    return user,row


def review(auth_id,inquiry_id,action):
    if action not in ('approve','delete','retry'):raise services.InvalidDetails('Choose a valid inquiry action.')
    with transaction() as db:
        user,row=locked_inquiry(db,auth_id,inquiry_id)
        if row.review_status=='deleted':raise services.InvalidDetails('This inquiry has been deleted.')
        if action=='delete':
            row.review_status='deleted';row.reviewed_by=user.id;row.reviewed_at=datetime.now(timezone.utc)
            services.audit(db,user,None,'inquiry.deleted','public_inquiry',row.id,{})
            return 'Inquiry deleted. No email was sent.'
        if action=='approve' and row.review_status=='pending':
            row.review_status='legitimate';row.reviewed_by=user.id;row.reviewed_at=datetime.now(timezone.utc)
            services.audit(db,user,None,'inquiry.approved','public_inquiry',row.id,{})
        elif row.review_status!='legitimate':
            raise services.InvalidDetails('Mark the inquiry legitimate before sending an acknowledgment.')
        if row.acknowledgement_status=='sent':return 'This inquiry was already acknowledged. No additional email was sent.'
        if row.acknowledgement_status=='legacy':return 'Inquiry marked legitimate. Historical acknowledgments are not resent.'
        # Commit the first-attempt time before the external call. Retries after
        # an ambiguous provider/DB failure use the same idempotency key.
        row.acknowledgement_started_at=row.acknowledgement_started_at or datetime.now(timezone.utc)
    with transaction() as db:
        user,row=locked_inquiry(db,auth_id,inquiry_id)
        if row.review_status!='legitimate':return 'Inquiry is no longer eligible for acknowledgment. No email was sent.'
        if row.acknowledgement_status=='sent':return 'This inquiry was already acknowledged. No additional email was sent.'
        started=row.acknowledgement_started_at
        if started.tzinfo is None:started=started.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc)-started>=timedelta(hours=23):
            return 'Acknowledgment delivery needs manual checking. No email was resent because its safe retry window expired.'
        text=(f"Hello {row.name},\n\nThank you for contacting CodeBI Solutions LLC. "
              'Our onboarding team has reviewed your inquiry and will assess your requirements. '
              'A member of our team will get back to you as soon as possible.\n\n'
              'We look forward to learning more about your goals.\n\nKind regards,\n'
              'The Onboarding Team\nCodeBI Solutions LLC')
        try:
            send_notification(row.email,'Your inquiry to CodeBI Solutions LLC',text,
                html=render_template('emails/inquiry_acknowledgment.html',name=row.name),
                idempotency_key='inquiry-ack-'+row.id)
        except Exception as exc:
            current_app.logger.warning('Inquiry acknowledgment unavailable (%s).',type(exc).__name__)
            row.acknowledgement_status='failed'
            return 'Inquiry marked legitimate, but acknowledgment delivery could not be confirmed. You can retry safely within 23 hours.'
        row.acknowledgement_status='sent';row.acknowledged_at=datetime.now(timezone.utc)
        services.audit(db,user,None,'inquiry.acknowledged','public_inquiry',row.id,{})
    return 'Inquiry marked legitimate and acknowledgment sent.'
