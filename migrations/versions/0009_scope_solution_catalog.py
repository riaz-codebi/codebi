"""Offer client-facing solution categories while retaining legacy selections."""
from alembic import op

revision = '0009_scope_solution_catalog'
down_revision = '0008_scope_type_discovery'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        UPDATE codebi.tools SET is_active = false
        WHERE name IN ('Excel', 'FastAPI', 'Flask', 'PostgreSQL', 'Power Apps',
                       'Power Automate', 'Power BI', 'Python', 'SharePoint', 'Supabase')
    """)
    op.execute("""
        INSERT INTO codebi.tools (name) VALUES
            ('Excel automation'),
            ('Workflow automation'),
            ('Web scraping'),
            ('SaaS platform'),
            ('Power BI reporting'),
            ('SharePoint administration'),
            ('Custom application'),
            ('Data integration')
        ON CONFLICT (name) DO UPDATE SET is_active = true
    """)


def downgrade():
    raise RuntimeError('The live solution catalog must be changed explicitly to preserve current selections.')
