"""Store public inquiries without provisioning portal accounts."""
from alembic import op

revision = '0012_public_inquiries'
down_revision = '0011_client_registration_inquiry'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE codebi.public_inquiries (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
            email text NOT NULL CHECK (char_length(email) BETWEEN 3 AND 254),
            company text NOT NULL CHECK (char_length(company) BETWEEN 1 AND 200),
            details text NOT NULL CHECK (char_length(details) BETWEEN 1 AND 5000),
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute('CREATE INDEX public_inquiries_email_created ON codebi.public_inquiries (email, created_at)')
    op.execute('ALTER TABLE codebi.public_inquiries ENABLE ROW LEVEL SECURITY')
    op.execute('REVOKE ALL ON codebi.public_inquiries FROM PUBLIC, anon, authenticated')
    op.execute('GRANT SELECT, INSERT ON codebi.public_inquiries TO codebi_app')
    op.execute('CREATE POLICY flask_public_inquiry ON codebi.public_inquiries TO codebi_app USING (true) WITH CHECK (true)')


def downgrade():
    if op.get_bind().exec_driver_sql('SELECT EXISTS (SELECT 1 FROM codebi.public_inquiries)').scalar():
        raise RuntimeError('Public inquiries exist. Downgrading would discard them.')
    op.drop_table('public_inquiries', schema='codebi')
