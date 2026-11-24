"""Allow the restricted Flask role to use existing scope-of-work foundation tables."""
from pathlib import Path
from alembic import op

revision = '0003_scope_of_work_access'
down_revision = '0002_reference_catalogs'
branch_labels = None
depends_on = None


def upgrade():
    text = (Path(__file__).resolve().parents[2] / 'database' / '005_scope_of_work_access.sql').read_text(encoding='utf-8')
    text = '\n'.join(line for line in text.splitlines() if line.strip() not in ('BEGIN;', 'COMMIT;'))
    # The script contains PostgreSQL format('%I') identifiers, not DBAPI parameters.
    op.get_bind().exec_driver_sql(text, execution_options={'no_parameters': True})


def downgrade():
    # Retain tables, data and RLS. Only remove this feature's backend privileges.
    for table in ('sow_types', 'tools', 'statements_of_work', 'sow_tools', 'sow_events'):
        op.execute(f'DROP POLICY IF EXISTS flask_sow_access ON codebi.{table}')
        op.execute(f'REVOKE ALL ON codebi.{table} FROM codebi_app')
    op.execute('REVOKE USAGE ON SEQUENCE codebi.statements_of_work_sow_number_seq FROM codebi_app')
