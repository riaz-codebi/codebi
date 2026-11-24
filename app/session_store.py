"""Encrypted, expiring server-side auth state shared by local Gunicorn workers."""
import base64
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time

from cryptography.fernet import Fernet, InvalidToken


class SessionStore:
    def __init__(self, path, secret):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.cipher = Fernet(base64.urlsafe_b64encode(
            hashlib.sha256(('codebi-auth-state:' + secret).encode()).digest()
        ))
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS auth_sessions '
                       '(id_hash TEXT PRIMARY KEY, payload BLOB NOT NULL, expires_at REAL NOT NULL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def key(sid):
        return hashlib.sha256(sid.encode()).hexdigest()

    def create(self, payload, ttl):
        sid = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('DELETE FROM auth_sessions WHERE expires_at <= ?', (time.time(),))
            db.execute('INSERT INTO auth_sessions VALUES (?, ?, ?)', (
                self.key(sid), self.cipher.encrypt(json.dumps(payload).encode()), time.time() + ttl))
        return sid

    def load(self, sid, consume=False):
        if not sid:
            return None
        with self.connect() as db:
            # Serialize consumption, so callback replay cannot exchange a code twice.
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload, expires_at FROM auth_sessions WHERE id_hash = ?',
                             (self.key(sid),)).fetchone()
            if consume or (row and row[1] <= time.time()):
                db.execute('DELETE FROM auth_sessions WHERE id_hash = ?', (self.key(sid),))
        if not row or row[1] <= time.time():
            return None
        try:
            return json.loads(self.cipher.decrypt(row[0]))
        except (InvalidToken, ValueError):
            return None

    def save(self, sid, payload):
        with self.connect() as db:
            db.execute('UPDATE auth_sessions SET payload = ? WHERE id_hash = ? AND expires_at > ?', (
                self.cipher.encrypt(json.dumps(payload).encode()), self.key(sid), time.time()))

    def delete(self, sid):
        if sid:
            with self.connect() as db:
                db.execute('DELETE FROM auth_sessions WHERE id_hash = ?', (self.key(sid),))
