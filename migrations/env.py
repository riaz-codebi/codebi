"""Migrations use DATABASE_URL, never the runtime role. No credential logging."""
import os
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import create_engine, pool
from app.database import postgres_url

load_dotenv(Path(__file__).resolve().parents[1] / '.env')
# Mappings cover implemented features, not every foundation table.
# Explicit migrations avoid autogenerate dropping unmapped SOW/auth objects.
target_metadata = None

if context.is_offline_mode():
    raise RuntimeError('Use an online migration connection for schema validation.')
engine = create_engine(postgres_url(os.environ['DATABASE_URL']), poolclass=pool.NullPool,
                       connect_args={'sslmode': 'require', 'connect_timeout': 10}, hide_parameters=True)
try:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          version_table_schema='codebi', transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()
finally:
    engine.dispose()
