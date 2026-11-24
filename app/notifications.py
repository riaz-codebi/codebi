"""Send portal notifications and create signed scope review links."""
from flask import current_app, url_for
from itsdangerous import URLSafeTimedSerializer


class NotificationError(RuntimeError):
    """A send failure with a message safe to show in the portal."""


def send_notification(to, subject, text):
    api_key = current_app.config['RESEND_API_KEY']
    if not api_key:
        raise NotificationError('The Flask app is missing its Resend API key.')

    try:
        import resend
        from resend.exceptions import ResendError
    except ImportError as exc:
        raise NotificationError('The Resend SDK could not load in the Flask Python environment.') from exc

    resend.api_key = api_key
    try:
        return resend.Emails.send({
            'from': current_app.config['NOTIFICATION_FROM'],
            'reply_to': current_app.config['NOTIFICATION_REPLY_TO'],
            'to': to,
            'subject': subject,
            'text': text,
        })
    except ResendError as exc:
        code = str(exc.code)
        if exc.error_type == 'HttpClientError':
            message = 'Flask could not connect to Resend.'
        elif code == '401':
            message = 'Resend rejected the API key.'
        elif code == '403':
            message = 'Resend denied the send (403). Check the sending key permissions and sender domain.'
        elif code == '429':
            message = 'Resend rate-limited the send (429).'
        elif code in ('400', '422'):
            message = f'Resend rejected the email details ({code}).'
        else:
            message = 'Resend could not process the send.'
        raise NotificationError(message) from exc


def scope_link_signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='codebi-scope-review')


def scope_review_link(client_id, sow_id):
    token = scope_link_signer().dumps([str(client_id), str(sow_id)])
    path = url_for('scopes.review_link', sow_id=sow_id, t=token, _external=False)
    return current_app.config['CLIENT_LINK_ORIGIN'] + path
