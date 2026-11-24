"""Create least-privilege Flask credentials and adopt the installed Alembic baseline.

Without --apply, only print the plan. Passwords are generated locally, written to
the ignored .env, and never printed or passed as command-line arguments.
"""
import argparse
import os
from pathlib import Path
import secrets
import sys

from dotenv import dotenv_values, set_key
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--rotate-password', action='store_true')
    args = parser.parse_args()
    if not args.apply:
        print('Plan: validate installed tables; create/reuse codebi_app; grant only current feature access;')
        print('save APP_DATABASE_URL in ignored .env; verify restricted connectivity; adopt Alembic revision 0001.')
        print('Existing records, Auth identities, provider configuration, and 003 functions remain intact.')
        return 0
    values = dotenv_values(ROOT / '.env')
    admin_uri = values.get('DATABASE_URL') or os.getenv('DATABASE_URL')
    if not admin_uri:
        raise RuntimeError('DATABASE_URL is missing.')
    admin = make_url(admin_uri)
    app_uri = values.get('APP_DATABASE_URL')
    app_url = make_url(app_uri) if app_uri else None
    if app_url and (app_url.host, app_url.port, app_url.database) != (admin.host, admin.port, admin.database):
        raise RuntimeError('Existing application connection targets another database. Review it before proceeding.')
    expected_user = 'codebi_app' + ('.' + admin.username.split('.', 1)[1] if '.' in admin.username else '')
    if app_url and app_url.username != expected_user:
        raise RuntimeError('APP_DATABASE_URL has an unexpected role or project suffix.')
    password = app_url.password if app_url and not args.rotate_password else secrets.token_urlsafe(40)
    new_url = admin.set(drivername='postgresql', username=expected_user, password=password)
    with psycopg.connect(admin_uri, sslmode='require', connect_timeout=10) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname='codebi_app'")
            role = cursor.fetchone()
            if role and any(role[1:]):
                raise RuntimeError('Existing codebi_app is privileged; refusing to reuse it.')
            if role and not app_url and not args.rotate_password:
                raise RuntimeError('Role exists but its local credentials are absent. Explicit password rotation is required.')
            if not role:
                cursor.execute(sql.SQL('CREATE ROLE codebi_app LOGIN PASSWORD {} NOSUPERUSER NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 20').format(sql.Literal(password)))
            elif args.rotate_password:
                cursor.execute(sql.SQL('ALTER ROLE codebi_app PASSWORD {}').format(sql.Literal(password)))
            cursor.execute((ROOT / 'database' / '004_flask_database_access.sql').read_text(encoding='utf-8'))
    # Save immediately after role creation so later verification errors do not lose credentials.
    set_key(str(ROOT / '.env'), 'APP_DATABASE_URL', new_url.render_as_string(hide_password=False))
    from app.database import postgres_url
    from sqlalchemy import create_engine, text
    engine = create_engine(postgres_url(new_url.render_as_string(hide_password=False)),
                           connect_args={'sslmode': 'require', 'connect_timeout': 10}, hide_parameters=True)
    try:
        with engine.connect() as conn:
            role = conn.execute(text("SELECT current_user, has_schema_privilege(current_user, 'codebi', 'CREATE'), "
                "has_table_privilege(current_user, (SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='auth' AND c.relname='users'), 'SELECT')")).one()
            if role[0] != 'codebi_app' or role[1] or role[2]:
                raise RuntimeError('Restricted-role verification failed.')
            conn.execute(text('SELECT count(*) FROM codebi.roles')).scalar_one()
            print('Restricted PostgreSQL connection verified; no schema creation or Auth table access.')
    finally:
        engine.dispose()
    from alembic import command
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    os.environ['DATABASE_URL'] = admin_uri
    migration_engine = create_engine(postgres_url(admin_uri), connect_args={'sslmode': 'require', 'connect_timeout': 10}, hide_parameters=True)
    try:
        with migration_engine.connect() as connection:
            current = MigrationContext.configure(connection, opts={'version_table_schema': 'codebi'}).get_current_revision()
        if current is None:
            command.upgrade(Config(str(ROOT / 'alembic.ini')), '0001_existing_foundation')
    finally:
        migration_engine.dispose()
    print('APP_DATABASE_URL saved without displaying credentials. Alembic baseline adopted.')
    print('Restart Flask. Migration 0002 (old RPC cleanup) remains unapplied until sign-in is verified.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # SQLAlchemy/driver errors may embed connection details; never print their text.
        print('Database setup failed:', str(exc) if type(exc) is RuntimeError else type(exc).__name__ + ' (connection details withheld).')
        original = getattr(exc, 'orig', exc)
        if getattr(original, 'sqlstate', None):
            print('SQLSTATE:', original.sqlstate)
        raise SystemExit(1)
