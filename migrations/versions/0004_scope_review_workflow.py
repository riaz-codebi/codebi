"""Enable the scope conversation, estimates, and work logging for Flask."""
from alembic import op

revision = '0004_scope_review_workflow'
down_revision = '0003_scope_of_work_access'
branch_labels = ('scope_review',)
depends_on = None

PRIVILEGES = {
    'sow_messages': 'SELECT, INSERT',
    'sow_estimates': 'SELECT, INSERT, UPDATE',
    'time_entries': 'SELECT, INSERT',
}


def upgrade():
    for table, privileges in PRIVILEGES.items():
        op.execute(f'GRANT {privileges} ON codebi.{table} TO codebi_app')
        op.execute(f'CREATE POLICY flask_scope_review ON codebi.{table} TO codebi_app USING (true) WITH CHECK (true)')


def downgrade():
    for table, privileges in PRIVILEGES.items():
        op.execute(f'DROP POLICY IF EXISTS flask_scope_review ON codebi.{table}')
        op.execute(f'REVOKE {privileges} ON codebi.{table} FROM codebi_app')
