"""Moderate inquiries before sending any acknowledgment."""
from alembic import op

revision='0015_inquiry_review'
down_revision='0014_client_logo_background'
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
        ALTER TABLE codebi.public_inquiries
          ADD COLUMN review_status text NOT NULL DEFAULT 'pending'
            CHECK (review_status IN ('pending','legitimate','deleted')),
          ADD COLUMN reviewed_by uuid REFERENCES codebi.users(id),
          ADD COLUMN reviewed_at timestamptz,
          ADD COLUMN acknowledgement_status text NOT NULL DEFAULT 'none'
            CHECK (acknowledgement_status IN ('none','sent','failed','legacy')),
          ADD COLUMN acknowledgement_started_at timestamptz,
          ADD COLUMN acknowledged_at timestamptz;
        -- Earlier submissions already triggered an acknowledgment attempt.
        -- Do not resend automatically when historical records are reviewed.
        UPDATE codebi.public_inquiries SET acknowledgement_status='legacy';
        CREATE INDEX public_inquiries_review_created ON codebi.public_inquiries(review_status,created_at);
        GRANT UPDATE (review_status,reviewed_by,reviewed_at,acknowledgement_status,
          acknowledgement_started_at,acknowledged_at) ON codebi.public_inquiries TO codebi_app;
    """)


def downgrade():
    raise RuntimeError('Inquiry review history must be preserved; use a reviewed forward migration.')
