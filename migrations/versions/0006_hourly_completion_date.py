"""Allow completion dates on hourly estimates, including existing rows."""
from alembic import op

revision = '0006_hourly_completion_date'
down_revision = '0005_scope_estimate_pricing'
branch_labels = None
depends_on = None


def replace_pricing_check(hourly_date_check):
    table = 'sow_estimates'
    name = 'sow_estimates_pricing_values'
    op.drop_constraint(name, table, type_='check', schema='codebi')
    op.create_check_constraint(name, table, f"""
        (pricing_type IS NULL AND hourly_rate IS NULL AND min_amount IS NULL
         AND max_amount IS NULL AND estimated_completion IS NULL)
        OR
        (pricing_type = 'hourly' AND pricing_type IS NOT NULL
         AND min_hours IS NOT NULL AND max_hours IS NOT NULL
         AND hourly_rate IS NOT NULL AND hourly_rate > 0 AND hourly_rate < 'Infinity'::numeric
         AND min_amount IS NOT NULL AND max_amount IS NOT NULL
         AND min_amount = round(min_hours * hourly_rate, 2)
         AND max_amount = round(max_hours * hourly_rate, 2) {hourly_date_check})
        OR
        (pricing_type = 'project' AND pricing_type IS NOT NULL
         AND min_hours IS NULL AND max_hours IS NULL AND hourly_rate IS NULL
         AND min_amount IS NOT NULL AND min_amount > 0 AND min_amount < 'Infinity'::numeric
         AND max_amount IS NOT NULL AND min_amount = max_amount AND estimated_completion IS NOT NULL)
    """, schema='codebi')


def upgrade():
    replace_pricing_check('')


def downgrade():
    replace_pricing_check('AND estimated_completion IS NULL')
