"""Install/verify inquiry review metadata without sending emails."""
import argparse
import importlib.util
from pathlib import Path
import sys

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from dotenv import load_dotenv,dotenv_values
from sqlalchemy import create_engine,text

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.database import postgres_url


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--rollback-check',action='store_true')
    mode.add_argument('--apply',action='store_true')
    args=parser.parse_args();engine=None;stage='connect'
    try:
        load_dotenv(ROOT/'.env')
        settings=dotenv_values(ROOT/'.env')
        if args.apply:
            config=Config(str(ROOT/'alembic.ini'))
            config.set_main_option('script_location',str(ROOT/'migrations'))
            command.upgrade(config,'0015_inquiry_review')
            print('Inquiry review migration applied. Existing records retained; no emails sent.')
            return 0
        key='DATABASE_URL' if args.rollback_check else 'APP_DATABASE_URL'
        engine=create_engine(postgres_url(settings[key]),hide_parameters=True,
            connect_args={'sslmode':'require','connect_timeout':10})
        with engine.connect() as conn:
            tx=conn.begin()
            try:
                if args.rollback_check:
                    stage='read migration version'
                    before=conn.execute(text('SELECT version_num FROM codebi.alembic_version')).all()
                    module=importlib.util.spec_from_file_location('inquiry_review_migration',ROOT/'migrations/versions/0015_inquiry_review.py')
                    revision=importlib.util.module_from_spec(module);module.loader.exec_module(revision)
                    conn.execute(text("SET LOCAL lock_timeout='5s'"))
                    stage='apply migration in rollback transaction'
                    with Operations.context(MigrationContext.configure(conn)):revision.upgrade()
                    assert conn.execute(text('SELECT version_num FROM codebi.alembic_version')).all()==before
                    stage='verify restricted column grants'
                    for column in ('review_status','reviewed_by','reviewed_at','acknowledgement_status','acknowledgement_started_at','acknowledged_at'):
                        assert conn.execute(text("SELECT has_column_privilege('codebi_app','codebi.public_inquiries',:column,'UPDATE')"),{'column':column}).scalar()
                    stage='restricted insert'
                    inquiry=conn.execute(text("INSERT INTO codebi.public_inquiries(name,email,company,details) VALUES ('Rollback check','check@example.invalid','Test','Temporary rollback verification') RETURNING id,review_status,acknowledgement_status")).one()
                    assert inquiry.review_status=='pending' and inquiry.acknowledgement_status=='none'
                    stage='restricted review update'
                    conn.execute(text("UPDATE codebi.public_inquiries SET review_status='deleted' WHERE id=:id"),{'id':inquiry.id})
                conn.execute(text('SELECT review_status,reviewed_by,reviewed_at,acknowledgement_status,acknowledgement_started_at,acknowledged_at FROM codebi.public_inquiries LIMIT 0'))
            finally:tx.rollback()
        print('Inquiry review schema and restricted permissions verified; all test changes rolled back.' if args.rollback_check else 'Inquiry review schema verified; no changes made.')
        return 0
    except Exception as exc:
        print('Inquiry review setup unavailable:',stage,type(exc).__name__,getattr(getattr(exc,'orig',None),'sqlstate',''))
        import re
        message=getattr(getattr(getattr(exc,'orig',None),'diag',None),'message_primary','') or ''
        if re.fullmatch(r'permission denied for (?:table|schema|sequence) [a-z_]+',message):print(message)
        return 1
    finally:
        if engine is not None:engine.dispose()


if __name__=='__main__':raise SystemExit(main())
