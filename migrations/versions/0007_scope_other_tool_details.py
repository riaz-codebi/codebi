"""Store the request author's description of an Other tool."""
from alembic import op
import sqlalchemy as sa

revision = '0007_scope_other_tool_details'
down_revision = '0006_hourly_completion_date'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('statements_of_work', sa.Column('other_tool_details', sa.Text()), schema='codebi')
    op.create_check_constraint('sow_other_tool_details_length', 'statements_of_work',
        'char_length(other_tool_details) BETWEEN 1 AND 1000', schema='codebi')


def downgrade():
    if op.get_bind().scalar(sa.text(
            'SELECT EXISTS (SELECT 1 FROM codebi.statements_of_work WHERE other_tool_details IS NOT NULL)')):
        raise RuntimeError('Other tool descriptions exist. Downgrading would discard them.')
    op.drop_constraint('sow_other_tool_details_length', 'statements_of_work', type_='check', schema='codebi')
    op.drop_column('statements_of_work', 'other_tool_details', schema='codebi')
