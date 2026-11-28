"""Read-only application-role check of the installed export editor and catalog snapshot."""
from pathlib import Path
import sys
import tempfile

from dotenv import dotenv_values
from sqlalchemy import create_engine,event,select

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app import create_app
from app.database import postgres_url,transaction
from app.models import User,UserRole
from app.commerce import export_services
from commerce_hub.models import ExportDestination


def main():
    engine=None
    try:
        settings=dotenv_values(ROOT/'.env')
        url=postgres_url(settings['APP_DATABASE_URL'])
        assert (url.username or '').split('.')[0]=='codebi_app'
        engine=create_engine(url,connect_args={'sslmode':'require','connect_timeout':10},hide_parameters=True)
        @event.listens_for(engine,'begin')
        def readonly(connection):connection.exec_driver_sql('SET TRANSACTION READ ONLY')
        with tempfile.TemporaryDirectory() as temp:
            app=create_app({'DATABASE_ENGINE':engine,'APP_DATABASE_URL':None,'SUPABASE_URL':None,'SUPABASE_KEY':None,
                            'AUTH_STORE_PATH':Path(temp)/'sessions.sqlite3','SECRET_KEY':'readonly-verification-'*4})
            with app.app_context():
                with transaction() as db:
                    auth=db.scalar(select(User.auth_user_id).join(UserRole,UserRole.user_id==User.id).where(User.is_active.is_(True),UserRole.role_code=='super_admin'))
                    assert auth
                    destinations=list(db.scalars(select(ExportDestination).where(ExportDestination.is_active.is_(True))))
                print('Active export subscriptions:',len(destinations))
                for destination in destinations:
                    data=export_services.editor(auth,destination.client_id,destination.id)
                    assert len(data['rows'])==61
                    with transaction() as db:
                        _publication,products=export_services.snapshot(db,destination.client_id,destination.vendor_id)
                    print('Editor verified: 61 fields; published products:',len(products),'; products needing variant verification:',sum(bool(p.attributes.get('dropdowns')) for p in products),'; sample product:',data['sample']['product_id'] if data['sample'] else 'none')
                    print('Saved mapping version:',data['version'] or 'none')
        print('Restricted-role editor/catalog checks passed. All database transactions were read-only; no mapping saved or file exported.')
        return 0
    except Exception as exc:
        print('Export verification unavailable:',type(exc).__name__)
        return 1
    finally:
        if engine is not None:engine.dispose()


if __name__=='__main__':raise SystemExit(main())
