"""Adopt the already installed 001 schema without recreating tables or data."""
from alembic import op
import sqlalchemy as sa

revision = '0001_existing_foundation'
down_revision = None
branch_labels = None
depends_on = None

TABLES = ('industries', 'clients', 'users', 'roles', 'user_roles', 'client_memberships',
          'user_invitations', 'login_events', 'sow_types', 'tools', 'statements_of_work',
          'sow_tools', 'sow_estimates', 'sow_messages', 'sow_events', 'time_entries', 'audit_events')


def upgrade():
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names(schema='codebi'))
    if not set(TABLES) <= existing:
        raise RuntimeError('The existing 001 foundation is incomplete. Do not stamp an incomplete database.')
    # Frozen original fields: later ORM additions must not change adoption requirements.
    original_columns = {
        'users': {'id', 'auth_user_id', 'email', 'display_name', 'user_type', 'is_active', 'last_login_at'},
        'clients': {'id', 'client_number', 'client_code', 'name', 'industry_id', 'state_region', 'country_code', 'timezone'},
        'industries': {'id', 'name', 'is_active'}, 'roles': {'code', 'name', 'scope'},
        'user_roles': {'user_id', 'role_code', 'role_scope'},
        'client_memberships': {'id', 'client_id', 'user_id', 'role_code', 'can_create_sows', 'is_active'},
        'login_events': {'id', 'user_id', 'provider', 'outcome', 'occurred_at'},
        'audit_events': {'id', 'client_id', 'actor_id', 'action', 'entity_type', 'entity_id', 'changes'},
    }
    for table, required in original_columns.items():
        columns = {c['name'] for c in inspector.get_columns(table, schema='codebi')}
        if not required <= columns:
            raise RuntimeError('Existing foundation columns do not match the application mappings.')
    # Alembic writes only its version marker; no business tables are modified.


def downgrade():
    raise RuntimeError('The adopted foundation cannot be dropped by an automatic downgrade.')
