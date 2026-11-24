"""Remove old RPC endpoints only after every app instance has switched to Python."""
from pathlib import Path
from alembic import context, op

revision = '0002_retire_auth_rpc'
down_revision = '0003_scope_of_work_access'
branch_labels = None
depends_on = None


def upgrade():
    if context.get_x_argument(as_dictionary=True).get('confirm_rpc_retired') != 'true':
        raise RuntimeError('First verify Flask SQLAlchemy sign-in on every instance; then use -x confirm_rpc_retired=true.')
    # No CASCADE: unexpected dependencies stop the migration instead of being removed.
    op.execute('DROP FUNCTION IF EXISTS public.codebi_portal_login(text)')
    op.execute('DROP FUNCTION IF EXISTS public.codebi_portal_context()')
    op.execute('DROP FUNCTION IF EXISTS codebi.portal_identity_context(uuid)')
    op.execute("NOTIFY pgrst, 'reload schema'")


def downgrade():
    text = (Path(__file__).resolve().parents[2] / 'database' / '003_portal_auth.sql').read_text(encoding='utf-8')
    # Run the saved prior definition within Alembic's transaction, not nested BEGIN/COMMIT.
    text = '\n'.join(line for line in text.splitlines() if line.strip() not in ('BEGIN;', 'COMMIT;'))
    op.get_bind().exec_driver_sql(text)
