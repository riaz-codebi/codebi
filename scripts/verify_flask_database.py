"""Verify restricted PostgreSQL access; optional CRUD runs in a rolled-back transaction."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker
from app import create_app, services
from app.database import transaction
from app.models import Client, Membership, User, UserRole


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rollback-crud', action='store_true')
    parser.add_argument('--lookups', action='store_true')
    args = parser.parse_args()
    app = create_app()
    engine = app.extensions['portal_engine']
    if engine is None:
        raise RuntimeError('APP_DATABASE_URL is missing.')
    with app.app_context(), transaction() as db:
        user = db.scalar(select(User).join(UserRole, UserRole.user_id == User.id).where(
            User.user_type == 'internal', User.is_active.is_(True), User.auth_user_id.is_not(None), UserRole.role_code == 'super_admin'))
        if not user:
            raise RuntimeError('No linked active administrator is available for verification.')
        auth_id = user.auth_user_id
        counts_before = (db.scalar(select(func.count()).select_from(User)), db.scalar(select(func.count()).select_from(Client)))
        print('Runtime role:', db.execute(text('SELECT current_user')).scalar_one())
        for relation, privilege in [('users', 'DELETE'), ('roles', 'UPDATE'), ('statements_of_work', 'SELECT')]:
            permitted = db.execute(text('SELECT has_table_privilege(current_user, :relation, :privilege)'),
                                   {'relation': 'codebi.' + relation, 'privilege': privilege}).scalar_one()
            if permitted:
                raise RuntimeError('An unexpected runtime privilege was detected.')
        print('DELETE, role-reference UPDATE, and SOW table access are denied as intended.')
    with app.app_context():
        assert services.portal_context(auth_id)['is_super_admin']
        services.clients(auth_id)
        services.users(auth_id)
        print('Existing identity, client listing, and user listing verified through Python services.')
        if args.lookups:
            catalogs = services.client_catalogs(auth_id, country_code='US')
            assert len(catalogs['countries']) == 249
            assert len(catalogs['timezones']) == 598
            matches = sorted(r['name'] for r in catalogs['regions'] if r['name'].startswith('T'))
            assert matches == ['Tennessee', 'Texas']
            assert all(r['country_code'] == 'US' for r in catalogs['regions'])
            with transaction() as db:
                for table in ('countries', 'state_regions', 'timezones'):
                    assert not db.execute(text('SELECT has_table_privilege(current_user, :relation, :privilege)'),
                        {'relation': 'codebi.' + table, 'privilege': 'INSERT'}).scalar_one()
                total = db.execute(text('SELECT count(*) FROM codebi.state_regions')).scalar_one()
                assert total == 5046
            try:
                services.save_client(auth_id, {'name': 'Rejected lookup validation', 'country_code': 'CA', 'state_region_code': 'US-TX'})
            except services.InvalidDetails as exc:
                assert exc.field == 'state_region_code'
            else:
                raise RuntimeError('Cross-country validation did not reject the state.')
            print('Lookup catalogs verified: 249 countries, 5046 subdivisions, 598 timezones.')
            print('US prefix T matches Tennessee and Texas; catalog writes and cross-country state assignment are denied.')
        if args.rollback_crud:
            with engine.connect() as connection:
                outer = connection.begin()
                original = app.extensions['portal_sessionmaker']
                app.extensions['portal_sessionmaker'] = sessionmaker(connection, expire_on_commit=False,
                                                                      join_transaction_mode='create_savepoint')
                try:
                    client_id = services.save_client(auth_id, {'name': 'Migration verification (rolled back)', 'timezone': 'UTC'})
                    client = services.clients(auth_id, client_id=client_id)['clients'][0]
                    assert client['client_code'].startswith('CLI-')
                    uid = services.save_user(auth_id, {'email': 'migration-verification@example.invalid',
                        'display_name': 'Temporary verification user', 'role_code': 'client_admin'}, client_id=client_id)
                    services.save_user(auth_id, {'display_name': 'Temporary verification user', 'role_code': 'client_admin',
                        'can_create_sows': True, 'membership_active': True}, client_id=client_id, user_id=uid)
                    members = services.users(auth_id, client_id=client_id)['users']
                    assert len(members) == 1 and members[0]['id'] == uid
                    assert members[0]['memberships'][0]['can_create_sows']
                    print('Client creation/generated code, user provisioning, membership edit and reads verified.')
                finally:
                    app.extensions['portal_sessionmaker'] = original
                    outer.rollback()
                print('All verification records and audit inserts rolled back; identity sequence gaps are normal.')
        with transaction() as db:
            counts_after = (db.scalar(select(func.count()).select_from(User)), db.scalar(select(func.count()).select_from(Client)))
            assert counts_before == counts_after
            assert db.scalar(select(User.auth_user_id).where(User.auth_user_id == auth_id)) == auth_id
        print('Original user/client counts and administrator identity link are unchanged.')
    engine.dispose()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('Verification failed:', type(exc).__name__, '(connection details withheld).')
        original = getattr(exc, 'orig', exc)
        if getattr(original, 'sqlstate', None):
            print('SQLSTATE:', original.sqlstate)
        raise SystemExit(1)
