"""Save the inquiry supplied during OAuth-verified client registration."""
from alembic import op
import sqlalchemy as sa

revision = '0011_client_registration_inquiry'
down_revision = '0010_scope_ai_solution_options'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('clients', sa.Column('registration_inquiry', sa.Text()), schema='codebi')
    op.create_check_constraint('client_inquiry_length', 'clients',
        'char_length(registration_inquiry) BETWEEN 1 AND 5000', schema='codebi')


def downgrade():
    if op.get_bind().scalar(sa.text(
            'SELECT EXISTS (SELECT 1 FROM codebi.clients WHERE registration_inquiry IS NOT NULL)')):
        raise RuntimeError('Registration inquiries exist. Downgrading would discard them.')
    op.drop_constraint('client_inquiry_length', 'clients', type_='check', schema='codebi')
    op.drop_column('clients', 'registration_inquiry', schema='codebi')
