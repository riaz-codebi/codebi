"""Inspect/install the additive catalog publication schema; never reset Alembic."""
import argparse
from pathlib import Path
import sys

from dotenv import dotenv_values
import psycopg

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.setup_commerce import connection_url

TABLES=('catalog_work_items','catalog_publications','catalog_publication_items','catalog_heads','vendor_catalog_leases')


def verify(conn):
    assert conn.execute("SELECT obj_description('commerce.catalog_heads'::regclass,'pg_class')").fetchone()[0]=='Catalog publication/resume v1'
    for table in TABLES:
        assert conn.execute('SELECT to_regclass(%s) IS NOT NULL',('commerce.'+table,)).fetchone()[0]
        for role in ('anon','authenticated'):
            assert not conn.execute('SELECT has_table_privilege(%s,%s,\'SELECT\')',(role,'commerce.'+table)).fetchone()[0]
        assert conn.execute("SELECT relrowsecurity FROM pg_class WHERE oid=%s::regclass",('commerce.'+table,)).fetchone()[0]
    assert not conn.execute("SELECT has_table_privilege('commerce_worker','commerce.products','UPDATE')").fetchone()[0]
    assert not conn.execute("SELECT has_table_privilege('commerce_worker','commerce.catalog_publication_items','UPDATE')").fetchone()[0]
    assert not conn.execute("SELECT has_table_privilege('codebi_app','commerce.catalog_heads','UPDATE')").fetchone()[0]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--rollback-check',action='store_true')
    args=parser.parse_args()
    settings=dotenv_values(ROOT/'.env')
    key='DATABASE_URL' if args.apply or args.rollback_check else 'APP_DATABASE_URL'
    try:
        with psycopg.connect(connection_url(settings[key]).render_as_string(hide_password=False),sslmode='require',connect_timeout=10) as conn:
            before=conn.execute('SELECT version_num FROM codebi.alembic_version ORDER BY version_num').fetchall() if key=='DATABASE_URL' else None
            present=[conn.execute('SELECT to_regclass(%s) IS NOT NULL',('commerce.'+name,)).fetchone()[0] for name in TABLES]
            if any(present) and not all(present):
                raise RuntimeError('Partial catalog installation; stop for review.')
            if args.apply or args.rollback_check:
                if not all(present):
                    source=(ROOT/'database/commerce/003_catalog_publication.sql').read_text(encoding='utf-8')
                    conn.execute('\n'.join(line for line in source.splitlines() if line.strip() not in ('BEGIN;','COMMIT;')),prepare=False)
                verify(conn)
                assert conn.execute('SELECT version_num FROM codebi.alembic_version ORDER BY version_num').fetchall()==before
                if args.rollback_check:
                    conn.rollback();print('Catalog migration/permissions verified and rolled back; Alembic unchanged.')
                else:
                    print('Catalog schema installed and verified; immutable snapshots and Alembic preserved.')
            else:
                conn.execute('SET TRANSACTION READ ONLY') if not conn.info.transaction_status else None
                print('Catalog tables installed:',all(present))
                if all(present):verify(conn)
                print('No changes made.')
        return 0
    except Exception as exc:
        print('Catalog setup unavailable:',type(exc).__name__)
        return 1


if __name__=='__main__':raise SystemExit(main())
