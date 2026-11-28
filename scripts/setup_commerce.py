"""Inspect/install approved Commerce SQL. Never print secrets or alter Alembic history."""
import argparse
from pathlib import Path
import secrets
import subprocess
import sys

from dotenv import dotenv_values, set_key
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

ROOT=Path(__file__).resolve().parents[1]
FILES=[ROOT/'database/commerce/001_foundation.sql',ROOT/'database/commerce/002_private_storage.sql']
FEATURES=['connections','catalog','inventory','vendor_export','shopify_export']


def connection_url(value):
    url=make_url(value)
    if url.drivername not in ('postgres','postgresql','postgresql+psycopg'):
        raise ValueError('Use a PostgreSQL connection.')
    return url.set(drivername='postgresql')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true',help='Apply the version-controlled additive SQL.')
    parser.add_argument('--configure-worker',action='store_true',help='Generate a restricted worker password in ignored .env.')
    parser.add_argument('--rotate-worker-password',action='store_true')
    parser.add_argument('--enable-local',action='store_true',help='Set the local feature flag after successful installation.')
    parser.add_argument('--enable-client-name',help='Grant this one existing client all initial Commerce features.')
    args=parser.parse_args()
    values=dotenv_values(ROOT/'.env')
    key='DATABASE_URL' if args.apply else 'APP_DATABASE_URL'
    if not values.get(key):
        parser.error(key+' is missing in the root .env.')
    if not args.apply and (args.configure_worker or args.rotate_worker_password or args.enable_local or args.enable_client_name):
        parser.error('Configuration changes require --apply.')
    if args.apply:
        ignored=subprocess.run(['git','check-ignore','--quiet','.env'],cwd=ROOT).returncode==0
        if not ignored:
            parser.error('Refusing to write credentials: .env must be ignored by Git.')
    try:
        worker_url=None
        with psycopg.connect(connection_url(values[key]).render_as_string(hide_password=False),sslmode='require',connect_timeout=10) as conn:
            if not args.apply:
                conn.execute('SET TRANSACTION READ ONLY')
                installed=conn.execute("SELECT to_regclass('commerce.jobs') IS NOT NULL").fetchone()[0]
                print('Commerce foundation:', 'present' if installed else 'not installed')
                print('Vault installed:', conn.execute("SELECT to_regclass('vault.secrets') IS NOT NULL").fetchone()[0])
                print('SQL to review:',*[str(f.relative_to(ROOT)) for f in FILES],sep='\n  ')
                print('No changes made. Use --apply only after reviewing these scripts.')
                return 0
            versions=conn.execute('SELECT version_num FROM codebi.alembic_version ORDER BY version_num').fetchall()
            if not conn.execute("SELECT to_regclass('commerce.jobs') IS NOT NULL").fetchone()[0]:
                if conn.execute("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='commerce')").fetchone()[0]:
                    raise RuntimeError('Existing commerce schema requires manual review; no reset performed.')
                if not conn.execute("SELECT to_regclass('vault.secrets') IS NOT NULL").fetchone()[0]:
                    raise RuntimeError('Enable Supabase Vault before applying the foundation.')
                source=FILES[0].read_text(encoding='utf-8')
                conn.execute('\n'.join(line for line in source.splitlines() if line.strip() not in ('BEGIN;','COMMIT;')),prepare=False)
            expected=('vendors','vendor_connections','products','inventory','destination_templates','mapping_rules','jobs','exports')
            for table in expected:
                if not conn.execute('SELECT to_regclass(%s) IS NOT NULL',('commerce.'+table,)).fetchone()[0]:
                    raise RuntimeError('Existing commerce schema is incomplete; no repair attempted.')
            if not conn.execute("SELECT to_regprocedure('commerce.can_download_artifact(text)') IS NOT NULL").fetchone()[0]:
                source=FILES[1].read_text(encoding='utf-8')
                conn.execute('\n'.join(line for line in source.splitlines() if line.strip() not in ('BEGIN;','COMMIT;')),prepare=False)
            role=conn.execute("SELECT rolcanlogin,rolsuper,rolbypassrls,rolcreaterole,rolcreatedb,pg_has_role('commerce_worker','postgres','MEMBER'),has_schema_privilege('commerce_worker','commerce','CREATE') FROM pg_roles WHERE rolname='commerce_worker'").fetchone()
            if not role or any(role[1:]):
                raise RuntimeError('Worker role has unexpected privileges.')
            if conn.execute("SELECT has_function_privilege('codebi_app','commerce.worker_connection_secret(uuid,uuid)','EXECUTE') OR has_schema_privilege('codebi_app','vault','USAGE') OR has_schema_privilege('commerce_worker','vault','USAGE')").fetchone()[0]:
                raise RuntimeError('Unexpected broad Vault access.')
            if not conn.execute("SELECT has_function_privilege('codebi_app','commerce.store_connection_secret(uuid,uuid,text)','EXECUTE'),has_function_privilege('commerce_worker','commerce.worker_connection_secret(uuid,uuid)','EXECUTE')").fetchone()==(True,True):
                raise RuntimeError('Credential helper grants are missing.')
            if args.configure_worker:
                if values.get('COMMERCE_WORKER_DATABASE_URL') and role[0] and not args.rotate_worker_password:
                    print('Existing worker password preserved.')
                elif role[0] and not args.rotate_worker_password:
                    raise RuntimeError('Worker already has a password. Use --rotate-worker-password explicitly if it is lost.')
                else:
                    password=secrets.token_urlsafe(36)
                    conn.execute(sql.SQL('ALTER ROLE commerce_worker LOGIN PASSWORD {}').format(sql.Literal(password)))
                    admin=connection_url(values['DATABASE_URL'])
                    suffix='.'+admin.username.split('.',1)[1] if '.' in (admin.username or '') else ''
                    worker_url=admin.set(username='commerce_worker'+suffix,password=password).render_as_string(hide_password=False)
            if args.enable_client_name:
                rows=conn.execute('SELECT id FROM codebi.clients WHERE lower(btrim(name))=lower(btrim(%s)) AND is_active',(args.enable_client_name,)).fetchall()
                if len(rows)!=1:
                    raise RuntimeError('Client name must identify exactly one active client.')
                from psycopg.types.json import Jsonb
                client_id=rows[0][0]
                conn.execute("INSERT INTO codebi.client_app_access(client_id,app_code,is_active,features,updated_at) VALUES (%s,'commerce_hub',true,%s,now()) ON CONFLICT(client_id,app_code) DO UPDATE SET is_active=true,features=excluded.features,updated_at=now()",(client_id,Jsonb(FEATURES)))
                conn.execute("INSERT INTO commerce.vendor_connections(id,client_id,vendor_id,connection_type,username,credential_version,status,config,created_at,updated_at) SELECT gen_random_uuid(),%s,v.id,k.kind,'',0,'not_configured','{\"sku_grain\":\"unknown\"}'::jsonb,now(),now() FROM commerce.vendors v CROSS JOIN (VALUES ('catalog'),('inventory')) k(kind) WHERE v.code='shiloh_tack' ON CONFLICT(client_id,vendor_id,connection_type) DO NOTHING",(client_id,))
            if conn.execute('SELECT version_num FROM codebi.alembic_version ORDER BY version_num').fetchall()!=versions:
                raise RuntimeError('Migration history unexpectedly changed.')
        # Database committed. Secrets are written only to the existing ignored file.
        if worker_url:
            set_key(str(ROOT/'.env'),'COMMERCE_WORKER_DATABASE_URL',worker_url)
        if args.enable_local:
            set_key(str(ROOT/'.env'),'COMMERCE_HUB_ENABLED','true')
        print('Commerce schema, scoped Vault helpers and private Storage policies installed/verified.')
        print('Alembic history preserved. No vendor passwords, URLs, product data or Shopify columns were seeded.')
        if worker_url:
            print('Worker connection saved to ignored .env; value not displayed.')
        return 0
    except Exception as exc:
        # Driver exception strings may include SQL parameters. Keep output sanitized.
        print('Commerce setup failed:',type(exc).__name__)
        if isinstance(exc,RuntimeError):
            print(str(exc))
        print('Review configuration and database privileges. Do not reset Alembic or delete existing schemas.')
        return 1


if __name__=='__main__':
    sys.exit(main())
