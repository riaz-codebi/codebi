"""Store the display background for client logos."""
from alembic import op
import sqlalchemy as sa

revision = '0014_client_logo_background'
down_revision = '0013_client_branding'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('clients', sa.Column('logo_background', sa.Text(), nullable=False, server_default='light'), schema='codebi')
    op.create_check_constraint('client_logo_background_choice', 'clients',
        "logo_background IN ('light', 'dark')", schema='codebi')


def downgrade():
    op.drop_constraint('client_logo_background_choice', 'clients', type_='check', schema='codebi')
    op.drop_column('clients', 'logo_background', schema='codebi')
