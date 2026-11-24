"""Store hourly and fixed-project pricing while preserving legacy estimates."""
from alembic import op
import sqlalchemy as sa

revision = '0005_scope_estimate_pricing'
down_revision = '0004_scope_review_workflow'
branch_labels = None
depends_on = None

TABLE = 'sow_estimates'
SCHEMA = 'codebi'
COLUMNS = (
    ('pricing_type', sa.Text()), ('hourly_rate', sa.Numeric(10, 2)),
    ('min_amount', sa.Numeric(18, 2)), ('max_amount', sa.Numeric(18, 2)),
    ('estimated_completion', sa.Date()),
)
HOURS_CHECK = "status NOT IN ('issued', 'accepted') OR feasibility <> 'feasible' OR min_hours IS NOT NULL"


def upgrade():
    for name, kind in COLUMNS:
        op.add_column(TABLE, sa.Column(name, kind), schema=SCHEMA)
    legacy_check = op.get_bind().scalar(sa.text("""
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'codebi.sow_estimates'::regclass AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%issued%accepted%feasibility%min_hours%'
    """))
    if not legacy_check:
        raise RuntimeError('The legacy estimate hours constraint could not be identified.')
    op.drop_constraint(legacy_check, TABLE, type_='check', schema=SCHEMA)
    op.create_check_constraint('sow_estimates_feasible_pricing', TABLE,
        f"pricing_type IS NOT DISTINCT FROM 'project' OR ({HOURS_CHECK})", schema=SCHEMA)
    op.create_check_constraint('sow_estimates_pricing_values', TABLE, """
        (pricing_type IS NULL AND hourly_rate IS NULL AND min_amount IS NULL
         AND max_amount IS NULL AND estimated_completion IS NULL)
        OR
        (pricing_type = 'hourly' AND pricing_type IS NOT NULL
         AND min_hours IS NOT NULL AND max_hours IS NOT NULL
         AND hourly_rate IS NOT NULL AND hourly_rate > 0 AND hourly_rate < 'Infinity'::numeric
         AND min_amount IS NOT NULL AND max_amount IS NOT NULL
         AND min_amount = round(min_hours * hourly_rate, 2)
         AND max_amount = round(max_hours * hourly_rate, 2) AND estimated_completion IS NULL)
        OR
        (pricing_type = 'project' AND pricing_type IS NOT NULL
         AND min_hours IS NULL AND max_hours IS NULL AND hourly_rate IS NULL
         AND min_amount IS NOT NULL AND min_amount > 0 AND min_amount < 'Infinity'::numeric
         AND max_amount IS NOT NULL AND min_amount = max_amount AND estimated_completion IS NOT NULL)
    """, schema=SCHEMA)


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM codebi.sow_estimates WHERE pricing_type IS NOT NULL)")):
        raise RuntimeError('Priced estimates exist. Downgrading would discard their pricing.')
    for name in ('sow_estimates_pricing_values', 'sow_estimates_feasible_pricing'):
        op.drop_constraint(name, TABLE, type_='check', schema=SCHEMA)
    op.create_check_constraint('sow_estimates_feasible_hours', TABLE, HOURS_CHECK, schema=SCHEMA)
    for name, _ in reversed(COLUMNS):
        op.drop_column(TABLE, name, schema=SCHEMA)
