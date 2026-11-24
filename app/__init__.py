import os
import base64
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit
from dotenv import load_dotenv
from flask import Flask
from supabase import create_client
from supabase.lib.client_options import SyncClientOptions

load_dotenv(Path(__file__).resolve().parents[1] / '.env')

def create_app(test_config=None):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
    app.config["SUPABASE_URL"] = os.getenv("SUPABASE_URL")
    app.config["SUPABASE_KEY"] = os.getenv("SUPABASE_KEY")
    app.config["SUPABASE_BUCKET"] = os.getenv("SUPABASE_BUCKET", "Code-BI")
    app.config['APP_DATABASE_URL'] = os.getenv('APP_DATABASE_URL')
    app.config['GOOGLE_PLACES_API_KEY'] = os.getenv('GOOGLE_PLACES_API_KEY')
    app.config['GOOGLE_PLACES_MONTHLY_LIMIT'] = int(os.getenv('GOOGLE_PLACES_MONTHLY_LIMIT', '500'))
    app.config['GOOGLE_PLACES_PER_MINUTE_LIMIT'] = int(os.getenv('GOOGLE_PLACES_PER_MINUTE_LIMIT', '10'))
    app.config['APP_BASE_URL'] = os.getenv('APP_BASE_URL', 'http://localhost:5000').rstrip('/')
    app.config['RESEND_API_KEY'] = os.getenv('RESEND_API_KEY', '').strip()
    app.config['NOTIFICATION_FROM'] = 'CodeBI Portal <notifications@mail.codebisolutions.com>'
    app.config['NOTIFICATION_REPLY_TO'] = 'teams@codebisolutions.com'
    app.config['SCOPE_SUBMISSIONS_TO'] = 'team@codebisolutions.com'
    app.config['CLIENT_LINK_ORIGIN'] = 'https://codebisolutions.com'
    if test_config:
        app.config.update(test_config)
    origin = urlsplit(app.config['APP_BASE_URL'])
    if (origin.scheme not in ('http', 'https') or not origin.netloc or origin.username
            or origin.password or origin.path or origin.query or origin.fragment
            or (origin.scheme == 'http' and origin.hostname not in ('localhost', '127.0.0.1'))):
        raise ValueError('APP_BASE_URL must be an HTTPS origin, or HTTP localhost for development.')
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      SESSION_COOKIE_SECURE=origin.scheme == 'https', SESSION_COOKIE_NAME='codebi_session')
    instance = Path(app.instance_path)
    instance.mkdir(parents=True, exist_ok=True)
    if not app.config['SECRET_KEY']:
        key_path = instance / 'secret.key'
        try:
            with key_path.open('x', encoding='utf-8') as key_file:
                key_file.write(secrets.token_hex(32))
            key_path.chmod(0o600)
        except FileExistsError:
            pass
        app.config['SECRET_KEY'] = key_path.read_text(encoding='utf-8').strip()
    if len(app.config['SECRET_KEY']) < 32 or app.config['SECRET_KEY'] == 'change-me':
        raise ValueError('SECRET_KEY must be a stable random secret of at least 32 characters.')
    from app.session_store import SessionStore
    app.extensions['auth_store'] = SessionStore(
        app.config.get('AUTH_STORE_PATH', instance / 'auth_sessions.sqlite3'), app.config['SECRET_KEY'])
    from app.database import init_database
    init_database(app)

    supabase_url = app.config["SUPABASE_URL"]
    supabase_key = app.config["SUPABASE_KEY"]

    if supabase_url and supabase_key:
        legacy_role = None
        if supabase_key.count('.') == 2:
            try:
                encoded = supabase_key.split('.')[1]
                legacy_role = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))).get('role')
            except (ValueError, TypeError):
                raise ValueError('SUPABASE_KEY is not a valid publishable or legacy anon key.') from None
        if supabase_key.startswith('sb_secret_') or legacy_role == 'service_role':
            raise ValueError('SUPABASE_KEY must be a publishable or legacy anon key, not a secret key.')
        app.supabase = create_client(supabase_url, supabase_key,
                                    options=SyncClientOptions(auto_refresh_token=False, persist_session=False))
    else:
        app.supabase = None

    def _resolve_logo_url(bucket, key):
        try:
            if bucket.exists(key):
                public = bucket.get_public_url(key)
                return public.get("publicUrl") if isinstance(public, dict) else public
        except Exception as exc:
            app.logger.debug("Supabase logo resolution failed for %s: %s", key, exc)
        return None

    @app.context_processor
    def inject_logo_url():
        logo_url = None

        if app.supabase:
            bucket = app.supabase.storage.from_(app.config["SUPABASE_BUCKET"])
            for key in [
                "brand/logo/logo-codebi-v2.png",
                "code-bi/brand/logo/logo-codebi-v2.png",
                "logo/logo-codebi-v2.png",
                "code-bi/brand/logo/logo-codebi.png",
                "logo/logo-codebi.png",
                "brand/logo/logo-codebi.png",
            ]:
                logo_url = _resolve_logo_url(bucket, key)
                if logo_url:
                    break

        return {
            "logo_url": logo_url
        }

    from app.routes import main
    app.register_blueprint(main)

    from app.auth import auth, portal, csrf_token
    app.register_blueprint(auth)
    app.register_blueprint(portal)
    from app.administration import administration
    app.register_blueprint(administration)
    from app.scopes import scopes
    app.register_blueprint(scopes)

    @app.context_processor
    def inject_auth():
        return {'csrf_token': csrf_token,
                'auth_ready': bool(app.config['SUPABASE_URL'] and app.config['SUPABASE_KEY'])}

    @app.after_request
    def protect_auth_responses(response):
        if request_is_private():
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    return app


def request_is_private():
    from flask import request
    return request.path.startswith(('/auth/', '/portal'))
