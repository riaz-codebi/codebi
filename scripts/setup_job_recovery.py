"""Widen only the job retry integrity constraint; never queue or reset jobs."""
import argparse
from pathlib import Path
import sys
import psycopg
from dotenv import dotenv_values
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.setup_commerce import connection_url


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true');parser.add_argument('--rollback-check',action='store_true')
    args=parser.parse_args();settings=dotenv_values(ROOT/'.env')
    key='DATABASE_URL' if args.apply or args.rollback_check else 'APP_DATABASE_URL'
    try:
        with psycopg.connect(connection_url(settings[key]).render_as_string(hide_password=False),sslmode='require',connect_timeout=10) as conn:
            changing=args.apply or args.rollback_check
            before=conn.execute('SELECT version_num FROM codebi.alembic_version ORDER BY version_num').fetchall() if changing else None
            if args.apply or args.rollback_check:
                source=(ROOT/'database/commerce/005_job_recovery.sql').read_text(encoding='utf-8')
                conn.execute('\n'.join(line for line in source.splitlines() if line.strip() not in ('BEGIN;','COMMIT;')),prepare=False)
            marker=conn.execute("SELECT obj_description(oid,'pg_constraint') FROM pg_constraint WHERE conrelid='commerce.jobs'::regclass AND conname='jobs_foundation_check_3'").fetchone()
            assert marker and marker[0]=='Bounded job recovery v2: maximum 20 attempts'
            if changing:assert conn.execute('SELECT version_num FROM codebi.alembic_version ORDER BY version_num').fetchall()==before
            if args.rollback_check:conn.rollback();print('Retry constraint verified and rolled back; jobs and Alembic untouched.')
            elif args.apply:print('Retry integrity limit installed; jobs and Alembic untouched.')
            else:print('Retry integrity limit installed; no changes made.')
        return 0
    except Exception as exc:
        print('Job recovery setup unavailable:',type(exc).__name__);return 1


if __name__=='__main__':raise SystemExit(main())
