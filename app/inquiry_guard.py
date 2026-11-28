"""Local inquiry abuse checks; no inquiry text or addresses leave the server."""
from contextlib import closing
import hashlib
import hmac
import ipaddress
from pathlib import Path
import re
import sqlite3
import time
import unicodedata

from flask import current_app, request, session
from itsdangerous import BadSignature, URLSafeTimedSerializer


def signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='public-inquiry-form-v1')


def form_token():
    from app.auth import csrf_token
    return signer().dumps({'csrf': hashlib.sha256(csrf_token().encode()).hexdigest(), 'issued': time.time()})


def check_form(token):
    try:
        payload = signer().loads(token, max_age=7200)
        expected = hashlib.sha256(session.get('csrf', '').encode()).hexdigest()
        if not hmac.compare_digest(payload['csrf'], expected):
            return 'invalid'
        age = time.time() - float(payload['issued'])
        if age < 0 or age > 7200:
            return 'invalid'
        return 'fast' if age < current_app.config['INQUIRY_MIN_SECONDS'] else 'ok'
    except (BadSignature, ValueError, TypeError, KeyError):
        return 'invalid'


def normalized_text(value):
    value=unicodedata.normalize('NFKD',value.casefold())
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in value if not unicodedata.combining(c)).split())


def content_issue(values):
    # Names and sender fields must be single-line text, never headers or markup.
    if any(any(ord(c) < 32 or ord(c) == 127 for c in values[k]) for k in ('name', 'email', 'company')):
        return 'Enter your name, email and company without line breaks or control characters.'
    body = values['details']
    # Observed rotating-address campaign. Match the combined profile, not a
    # language, email domain, or a legitimate company name by itself.
    if normalized_text(values['name'])=='robertsum' and normalized_text(values['company'])=='google':
        return 'Please provide your full name, company and a description of your project.'
    generic_prices={normalized_text(message) for message in (
        'Hola, volia saber el seu preu.',
        'Zdravo, htio sam znati vašu cijenu.',
        'Hæ, ég vildi vita verð þitt.',
        'Ndewo, achọrọ m ịmara ọnụahịa gị.',
        'Hello, I wanted to know your price.',
        'Hi, I wanted to know your price.',
    )}
    if normalized_text(body) in generic_prices:
        return 'Please tell us which service you need and briefly describe your project.'
    if any(ord(c) < 32 and c not in '\r\n\t' for c in body):
        return 'Describe your project using plain text.'
    # The screenshot's mixed-case random identifiers are not project descriptions.
    # Apply only to ASCII single-token messages, never to a language/script.
    if re.fullmatch(r'[A-Za-z0-9]{12,}', body):
        letters = [c for c in body if c.isalpha()]
        switches = sum(a.isupper() != b.isupper() for a, b in zip(letters, letters[1:]))
        if sum(c.isupper() for c in letters) >= 3 and sum(c.islower() for c in letters) >= 3 and switches >= 4:
            return 'Please describe what you need help with in a short sentence.'
    links = re.findall(r'(?:https?://|www\.)\S+', body, re.I)
    without_links = re.sub(r'(?:https?://|www\.)\S+', '', body, flags=re.I)
    if len(links) >= 4 or (links and sum(c.isalpha() for c in without_links) < 8):
        return 'Describe your project in words and include no more than three links.'
    if re.search(r'<\s*(?:script|iframe)\b|\[url[=\]]', body, re.I):
        return 'Describe your project using plain text and ordinary links.'
    # Count words containing letters; punctuation, emoji and number-only padding
    # do not satisfy the minimum. Hyphenated words and contractions count once.
    words=re.findall(r"[^\W\d_][^\W_]*(?:['’\-][^\W_]+)*",body,re.UNICODE)
    minimum=current_app.config['INQUIRY_MIN_WORDS']
    if len(words)<minimum:
        return f'Please provide at least {minimum} words describing the solution you need, your goals and the challenges you want to solve.'
    return None


def reserve_submission(email,values=None):
    """Atomic limits shared by Gunicorn workers on this host; persist only HMAC keys.

    Do not trust arbitrary forwarded headers. Private/loopback peers may be Nginx;
    skip their IP bucket instead of treating every visitor as the same person.
    Session/email limits and the global notification ceiling still apply.
    """
    app = current_app
    now = time.time()
    entries = [
        ('session:' + session['csrf'], 900, app.config['INQUIRY_SESSION_LIMIT']),
        ('email:' + email, 900, app.config['INQUIRY_EMAIL_LIMIT']),
        ('global', 3600, app.config['INQUIRY_HOURLY_LIMIT']),
    ]
    if values:
        # Rotating email addresses/sessions must not bypass campaign limits.
        # These identities are HMACed below; no raw names or messages are stored.
        entries.extend([
            ('profile:'+normalized_text(values['name'])+'|'+normalized_text(values['company']),3600,app.config['INQUIRY_PROFILE_LIMIT']),
            ('body:'+normalized_text(values['details']),3600,app.config['INQUIRY_BODY_LIMIT']),
        ])
    try:
        address = ipaddress.ip_address(request.remote_addr or '')
        if address.is_global:
            network = ipaddress.ip_network(f'{address}/{64 if address.version == 6 else 32}', strict=False)
            entries.append(('ip:' + str(network), 900, app.config['INQUIRY_IP_LIMIT']))
    except ValueError:
        pass
    path = app.config.get('INQUIRY_USAGE_PATH') or Path(app.extensions['auth_store'].path).with_name('inquiry_usage.sqlite3')
    with closing(sqlite3.connect(str(path), timeout=5)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS inquiry_limits (key TEXT PRIMARY KEY, started REAL NOT NULL, count INTEGER NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM inquiry_limits WHERE started <= ?', (now - 3600,))
        updates = []
        for identity, window, limit in entries:
            key = hmac.new(app.secret_key.encode(), ('inquiry:' + identity).encode(), hashlib.sha256).hexdigest()
            row = db.execute('SELECT started,count FROM inquiry_limits WHERE key=?', (key,)).fetchone()
            started, count = row if row and row[0] > now-window else (now, 0)
            if count >= limit:
                return False
            updates.append((key, started, count+1))
        db.executemany('INSERT INTO inquiry_limits VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET started=excluded.started,count=excluded.count', updates)
    return True
