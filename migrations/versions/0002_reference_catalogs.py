"""Standardized, database-backed country, subdivision and timezone choices."""
import json
from pathlib import Path

from alembic import op
import sqlalchemy as sa

revision = '0002_reference_catalogs'
down_revision = '0001_existing_foundation'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('countries', sa.Column('code', sa.Text, primary_key=True),
        sa.Column('name', sa.Text, nullable=False), sa.Column('is_active', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.CheckConstraint("code ~ '^[A-Z]{2}$'", name='country_code_format'), schema='codebi')
    op.create_table('state_regions', sa.Column('code', sa.Text, primary_key=True),
        sa.Column('country_code', sa.Text, sa.ForeignKey('codebi.countries.code'), nullable=False),
        sa.Column('name', sa.Text, nullable=False), sa.Column('region_type', sa.Text, nullable=False),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.UniqueConstraint('code', 'country_code', name='state_region_country_unique'), schema='codebi')
    op.create_index('state_regions_country_name_idx', 'state_regions', ['country_code', 'name'], schema='codebi')
    op.create_table('timezones', sa.Column('name', sa.Text, primary_key=True),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default=sa.true()), schema='codebi')
    data = json.loads((Path(__file__).resolve().parents[2] / 'database/data/reference_catalogs.json').read_text(encoding='utf-8'))
    countries = sa.table('countries', sa.column('code'), sa.column('name'), schema='codebi')
    regions = sa.table('state_regions', *(sa.column(name) for name in ('code', 'country_code', 'name', 'region_type')), schema='codebi')
    zones = sa.table('timezones', sa.column('name'), schema='codebi')
    op.bulk_insert(countries, data['countries'])
    op.bulk_insert(regions, data['regions'])
    op.bulk_insert(zones, [{'name': name} for name in data['timezones']])
    op.add_column('clients', sa.Column('state_region_code', sa.Text), schema='codebi')
    bind = op.get_bind()
    # Backfill only unambiguous existing ISO codes/abbreviations/names. Keep original text.
    index = {}
    for region in data['regions']:
        for value in (region['code'], region['code'].split('-', 1)[1], region['name']):
            index.setdefault((region['country_code'], value.casefold()), set()).add(region['code'])
    for row in bind.execute(sa.text('SELECT id, country_code, state_region FROM codebi.clients WHERE state_region IS NOT NULL')):
        matches = index.get((row.country_code, row.state_region.strip().casefold()), set())
        if len(matches) == 1:
            bind.execute(sa.text('UPDATE codebi.clients SET state_region_code=:code WHERE id=:id'), {'code': next(iter(matches)), 'id': row.id})
    # NOT VALID preserves any preexisting nonstandard values; new changed keys are checked.
    op.create_foreign_key('clients_country_catalog_fk', 'clients', 'countries', ['country_code'], ['code'],
                         source_schema='codebi', referent_schema='codebi', postgresql_not_valid=True)
    op.create_foreign_key('clients_timezone_catalog_fk', 'clients', 'timezones', ['timezone'], ['name'],
                         source_schema='codebi', referent_schema='codebi', postgresql_not_valid=True)
    op.create_foreign_key('clients_region_country_fk', 'clients', 'state_regions', ['state_region_code', 'country_code'], ['code', 'country_code'],
                         source_schema='codebi', referent_schema='codebi', postgresql_not_valid=True)
    op.execute('ALTER TABLE codebi.clients ADD CONSTRAINT client_region_requires_country CHECK (state_region_code IS NULL OR country_code IS NOT NULL)')
    for table in ('countries', 'state_regions', 'timezones'):
        op.execute(f'ALTER TABLE codebi.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON codebi.{table} FROM PUBLIC, anon, authenticated')
        op.execute(f'GRANT SELECT ON codebi.{table} TO codebi_app')
        op.execute(f'CREATE POLICY flask_catalog_read ON codebi.{table} FOR SELECT TO codebi_app USING (true)')
    for name, invalid_query in (
        ('clients_country_catalog_fk', 'SELECT 1 FROM codebi.clients c WHERE c.country_code IS NOT NULL AND NOT EXISTS (SELECT 1 FROM codebi.countries r WHERE r.code=c.country_code)'),
        ('clients_timezone_catalog_fk', 'SELECT 1 FROM codebi.clients c WHERE NOT EXISTS (SELECT 1 FROM codebi.timezones r WHERE r.name=c.timezone)'),
    ):
        if bind.execute(sa.text(invalid_query)).first() is None:
            op.execute(f'ALTER TABLE codebi.clients VALIDATE CONSTRAINT {name}')
    op.execute('ALTER TABLE codebi.clients VALIDATE CONSTRAINT clients_region_country_fk')


def downgrade():
    # Explicit downgrade preserves historical state text but removes standardized keys/catalogs.
    for name in ('client_region_requires_country', 'clients_region_country_fk', 'clients_timezone_catalog_fk', 'clients_country_catalog_fk'):
        op.drop_constraint(name, 'clients', schema='codebi')
    op.drop_column('clients', 'state_region_code', schema='codebi')
    for table in ('state_regions', 'timezones', 'countries'):
        op.drop_table(table, schema='codebi')
