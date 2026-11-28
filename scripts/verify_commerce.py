"""Read-only checks by default; --rollback-install exercises SQL in a rolled-back transaction."""
import argparse
from pathlib import Path
import sys
from uuid import uuid4

from dotenv import dotenv_values
import psycopg
from psycopg.types.json import Jsonb
from psycopg import sql

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.setup_commerce import connection_url,FILES


def check(conn):
    for name in ('vendors','vendor_connections','products','inventory','destination_templates','mapping_rules','jobs','exports'):
        table='commerce.'+name
        assert conn.execute('SELECT has_table_privilege(%s,%s,%s)',('codebi_app',table,'SELECT')).fetchone()[0]
        for role in ('anon','authenticated'):
            assert not conn.execute('SELECT has_table_privilege(%s,%s,%s)',(role,table,'SELECT')).fetchone()[0]
    for role in ('codebi_app','commerce_worker'):
        assert not conn.execute("SELECT has_schema_privilege(%s,'vault','USAGE')",(role,)).fetchone()[0]
        assert not conn.execute("SELECT has_schema_privilege(%s,'commerce','CREATE')",(role,)).fetchone()[0]
    assert not conn.execute("SELECT has_function_privilege('codebi_app','commerce.worker_connection_secret(uuid,uuid)','EXECUTE')").fetchone()[0]
    assert not conn.execute("SELECT has_table_privilege('commerce_worker','commerce.products','UPDATE')").fetchone()[0]
    assert not conn.execute("SELECT public FROM storage.buckets WHERE id='commerce-artifacts'").fetchone()[0]
    print('Table grants, immutable snapshot permissions, Vault boundary and private bucket verified.')


def rollback_exercise(conn):
    # Supabase's owner need not inherit application roles. Membership exists
    # only inside this rolled-back verification transaction.
    owner=conn.execute('SELECT current_user').fetchone()[0]
    conn.execute(sql.SQL('GRANT codebi_app,commerce_worker TO {}').format(sql.Identifier(owner)))
    client,other,user,connection,job,lease=[uuid4() for _ in range(6)]
    password='rollback-only-'+str(uuid4())
    conn.execute("INSERT INTO codebi.clients(id,name,timezone) VALUES (%s,'Commerce rollback verification','UTC'),(%s,'Commerce rollback isolation','UTC')",(client,other))
    conn.execute("INSERT INTO codebi.users(id,email,display_name,user_type) VALUES (%s,%s,'Rollback verification','client')",(user,str(user)+'@example.invalid'))
    conn.execute("INSERT INTO codebi.client_memberships(client_id,user_id,role_code) VALUES (%s,%s,'client_admin')",(client,user))
    conn.execute("INSERT INTO codebi.client_app_access(client_id,app_code,is_active,features) VALUES (%s,'commerce_hub',true,%s)",(client,Jsonb(['connections','catalog'])))
    vendor=conn.execute("SELECT id FROM commerce.vendors WHERE code='shiloh_tack'").fetchone()[0]
    conn.execute("INSERT INTO commerce.vendor_connections(id,client_id,vendor_id,connection_type,username) VALUES (%s,%s,%s,'catalog','rollback-only')",(connection,client,vendor))
    conn.execute('SET LOCAL ROLE codebi_app')
    conn.execute('SELECT commerce.store_connection_secret(%s,%s,%s)',(client,connection,password))
    version=conn.execute('SELECT credential_version FROM commerce.vendor_connections WHERE id=%s',(connection,)).fetchone()[0]
    assert version==1
    conn.execute('RESET ROLE')
    conn.execute("INSERT INTO commerce.jobs(id,client_id,vendor_id,connection_id,credential_version,requested_by,kind,status,lease_token,lease_expires_at) VALUES (%s,%s,%s,%s,%s,%s,'test_connection','running',%s,now()+interval '2 minutes')",(job,client,vendor,connection,version,user,lease))
    conn.execute('SET LOCAL ROLE commerce_worker')
    assert conn.execute('SELECT commerce.worker_connection_secret(%s,%s)',(job,lease)).fetchone()[0]==password
    try:
        with conn.transaction():
            conn.execute('SELECT commerce.worker_connection_secret(%s,%s)',(job,uuid4()))
    except psycopg.Error:
        pass
    else:
        raise AssertionError('Wrong lease must not retrieve a secret.')
    conn.execute('RESET ROLE')
    try:
        with conn.transaction():
            conn.execute('INSERT INTO commerce.products(client_id,vendor_id,job_id,source_key,attributes) VALUES (%s,%s,%s,%s,%s)',(other,vendor,job,'wrong-client',Jsonb({})))
    except psycopg.errors.ForeignKeyViolation:
        pass
    else:
        raise AssertionError('Cross-client source reference must fail.')
    print('Vault replacement/read, wrong-lease denial and cross-client FK tested with rollback-only fixtures.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rollback-install',action='store_true')
    parser.add_argument('--application',action='store_true',help='Also render Commerce views through the restricted Flask database connection.')
    args=parser.parse_args()
    values=dotenv_values(ROOT/'.env')
    try:
        with psycopg.connect(connection_url(values['DATABASE_URL']).render_as_string(hide_password=False),sslmode='require',connect_timeout=10) as conn:
            if args.rollback_install:
                exists=conn.execute("SELECT to_regclass('commerce.jobs') IS NOT NULL").fetchone()[0]
                if not exists:
                    for file in FILES:
                        source='\n'.join(line for line in file.read_text().splitlines() if line.strip() not in ('BEGIN;','COMMIT;'))
                        conn.execute(source,prepare=False)
                check(conn)
                rollback_exercise(conn)
                conn.rollback()
                print('All fixtures/DDL in this verification were rolled back. No vendor login was attempted.')
            else:
                conn.execute('SET TRANSACTION READ ONLY')
                check(conn)
                print('Recorded Alembic revisions:',conn.execute('SELECT version_num FROM codebi.alembic_version ORDER BY version_num').fetchall())
        if args.application:
            from app import create_app, services as portal_services
            from app.commerce import services
            from app.database import transaction
            from app.models import User, UserRole
            from flask import g, render_template
            from sqlalchemy import select
            app=create_app()
            # Avoid unrelated logo-storage lookups during this read-only smoke check.
            app.supabase=None
            with app.test_request_context('/portal/commerce'):
                with transaction() as db:
                    auth_id=db.scalar(select(User.auth_user_id).join(UserRole,UserRole.user_id==User.id).where(
                        User.is_active.is_(True),User.auth_user_id.is_not(None),UserRole.role_code=='super_admin').limit(1))
                if not auth_id:
                    raise RuntimeError('No verified active administrator exists for the application smoke check.')
                g.portal_auth_user_id=str(auth_id)
                g.portal_user=portal_services.portal_context(str(auth_id))
                clients=services.registry(str(auth_id))
                render_template('portal/commerce/index.html',portal_user=g.portal_user,
                    clients=[c for c in clients if c['enabled']],
                    all_clients=[c for c in clients if c['enabled']],
                    available_clients=[c for c in clients if c['client_active'] and not c['enabled']],
                    query='',selected_feature='',feature_labels=services.FEATURE_LABELS,features=services.FEATURES)
                enabled=[c for c in clients if c['enabled'] and c['client_active']]
                for client in enabled:
                    data=services.overview(str(auth_id),client['id'])
                    options=services.export_options(str(auth_id),client['id'])
                    render_template('portal/commerce/home.html',portal_user=g.portal_user,data=data,options=options,client_id=client['id'],features=services.FEATURES)
                    for item in data['connections']:
                        services.connection_form(str(auth_id),client['id'],item['id'])
                print('Restricted Flask registry, overview, connection reads and template rendering verified.')
        return 0
    except Exception as exc:
        print('Commerce verification failed:',type(exc).__name__)
        if isinstance(exc,psycopg.Error):
            print('SQLSTATE:',exc.sqlstate)
            # Constraint names are metadata, not query text or bound secrets.
            print('Constraint:',getattr(exc.diag,'constraint_name',None))
            if exc.sqlstate == '42501':
                print('Permission detail:', exc.diag.message_primary)
        return 1


if __name__=='__main__':
    sys.exit(main())
