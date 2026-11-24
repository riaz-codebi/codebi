"""Add AI solution interests to the scope request catalog."""
from alembic import op

revision = '0010_scope_ai_solution_options'
down_revision = '0009_scope_solution_catalog'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO codebi.tools (name) VALUES
            ('AI document reader'),
            ('AI transcription'),
            ('AI assistant or agent'),
            ('AI search and Q&A')
        ON CONFLICT (name) DO UPDATE SET is_active = true
    """)


def downgrade():
    op.execute("""
        UPDATE codebi.tools SET is_active = false
        WHERE name IN ('AI document reader', 'AI transcription', 'AI assistant or agent', 'AI search and Q&A')
    """)
