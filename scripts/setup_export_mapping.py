"""Install additive export access and register the reviewed Shopify header template."""
import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4

from dotenv import dotenv_values
import psycopg
from psycopg.types.json import Jsonb

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.setup_commerce import connection_url


def verify(conn):
    assert conn.execute("SELECT obj_description('commerce.client_export_destinations'::regclass,'pg_class')").fetchone()[0]=='Client export destinations v1'
    assert conn.execute("SELECT relrowsecurity FROM pg_class WHERE oid='commerce.client_export_destinations'::regclass").fetchone()[0]
    for role in ('anon','authenticated'):
        assert not conn.execute("SELECT has_table_privilege(%s,'commerce.client_export_destinations','SELECT')",(role,)).fetchone()[0]
    for permission in ('SELECT','INSERT'):
        assert conn.execute("SELECT has_table_privilege('codebi_app','commerce.client_export_destinations',%s)",(permission,)).fetchone()[0]
    assert not conn.execute("SELECT has_table_privilege('codebi_app','commerce.client_export_destinations','DELETE')").fetchone()[0]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--apply',action='store_true');group.add_argument('--rollback-check',action='store_true')
    args=parser.parse_args()
    settings=dotenv_values(ROOT/'.env')
    seed=json.loads((ROOT/'commerce_hub/templates/shopify_product_2026.json').read_text(encoding='utf-8'))
    try:
        with psycopg.connect(connection_url(settings['DATABASE_URL' if args.apply or args.rollback_check else 'APP_DATABASE_URL']).render_as_string(hide_password=False),sslmode='require',connect_timeout=10) as conn:
            if args.apply or args.rollback_check:
                before=conn.execute('SELECT version_num FROM codebi.alembic_version ORDER BY version_num').fetchall()
                if not conn.execute("SELECT to_regclass('commerce.client_export_destinations') IS NOT NULL").fetchone()[0]:
                    sql=(ROOT/'database/commerce/004_export_destinations.sql').read_text(encoding='utf-8')
                    conn.execute('\n'.join(line for line in sql.splitlines() if line.strip() not in ('BEGIN;','COMMIT;')),prepare=False)
                verify(conn)
                conn.execute('INSERT INTO commerce.destination_templates(id,platform,version,columns,source_sha256,is_active) VALUES (%s,%s,%s,%s,%s,true) ON CONFLICT(platform,version) DO NOTHING',
                             (uuid4(),seed['platform'],seed['version'],Jsonb(seed['columns']),seed['source_sha256']))
                row=conn.execute('SELECT id,source_sha256,columns FROM commerce.destination_templates WHERE platform=%s AND version=%s',(seed['platform'],seed['version'])).fetchone()
                assert row[1]==seed['source_sha256'] and row[2]==seed['columns'],'Template version differs; stop rather than overwrite.'
                # Preserve explicit opt-outs. Existing catalog+Shopify feature grants seed the initial subscription.
                conn.execute('''INSERT INTO commerce.client_export_destinations(client_id,vendor_id,platform,template_id)
                    SELECT DISTINCT a.client_id,v.vendor_id,'shopify',%s FROM codebi.client_app_access a
                    JOIN codebi.clients c ON c.id=a.client_id AND c.is_active
                    JOIN commerce.vendor_connections v ON v.client_id=a.client_id AND v.connection_type='catalog'
                    JOIN commerce.vendors vendor ON vendor.id=v.vendor_id AND vendor.is_active AND vendor.code='shiloh_tack'
                    WHERE a.app_code='commerce_hub' AND a.is_active AND a.features ? 'catalog' AND a.features ? 'shopify_export'
                    ON CONFLICT(client_id,vendor_id,platform) DO NOTHING''',(row[0],))
                assert conn.execute('SELECT version_num FROM codebi.alembic_version ORDER BY version_num').fetchall()==before
                if args.rollback_check:conn.rollback();print('Export schema, template and restricted permissions checked; transaction rolled back.')
                else:print('Export schema/template installed. Existing eligible subscriptions enabled; prior mappings and catalog preserved.')
            else:
                verify(conn)
                print('Export schema and restricted permissions verified; no changes made.')
        return 0
    except Exception as exc:
        print('Export setup unavailable:',type(exc).__name__)
        return 1


if __name__=='__main__':raise SystemExit(main())
