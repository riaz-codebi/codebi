"""Rename Consulting to Discovery while keeping the existing scope type ID."""
from alembic import op

revision = '0008_scope_type_discovery'
down_revision = '0007_scope_other_tool_details'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE codebi.sow_types SET name = 'Discovery' WHERE name = 'Consulting'")


def downgrade():
    op.execute("UPDATE codebi.sow_types SET name = 'Consulting' WHERE name = 'Discovery'")
