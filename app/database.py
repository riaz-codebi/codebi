"""SQLAlchemy sessions; the web application never falls back to DATABASE_URL."""
from contextlib import contextmanager

from flask import current_app
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker


def postgres_url(value):
    url = make_url(value)
    if url.drivername not in ('postgresql', 'postgres', 'postgresql+psycopg'):
        raise ValueError('A PostgreSQL connection URL is required.')
    return url.set(drivername='postgresql+psycopg')


def init_database(app):
    engine = app.config.get('DATABASE_ENGINE')  # Isolated test database only.
    if engine is None and app.config.get('APP_DATABASE_URL'):
        url = postgres_url(app.config['APP_DATABASE_URL'])
        if (url.username or '').split('.')[0] != 'codebi_app':
            raise ValueError('APP_DATABASE_URL must use the restricted codebi_app role.')
        engine = create_engine(url, connect_args={'sslmode': 'require', 'connect_timeout': 10},
                               pool_pre_ping=True, pool_size=3, max_overflow=2, hide_parameters=True)

        @event.listens_for(engine, 'connect')
        def check_runtime_role(connection, _record):
            with connection.cursor() as cursor:
                cursor.execute("SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, "
                               "pg_has_role(current_user, 'postgres', 'MEMBER'), "
                               "has_schema_privilege(current_user, 'codebi', 'CREATE'), "
                               "EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                               "WHERE n.nspname='codebi' AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)) "
                               "FROM pg_roles WHERE rolname = current_user")
                row = cursor.fetchone()
                if not row or row[0] != 'codebi_app' or any(row[1:]):
                    raise ValueError('Refusing a privileged web-application database connection.')
            connection.rollback()
    app.extensions['portal_engine'] = engine
    app.extensions['portal_sessionmaker'] = sessionmaker(engine, expire_on_commit=False) if engine else None


@contextmanager
def transaction():
    factory = current_app.extensions.get('portal_sessionmaker')
    if factory is None:
        raise RuntimeError('APP_DATABASE_URL is not configured.')
    with factory.begin() as db:
        yield db
