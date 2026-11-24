# Flask / SQLAlchemy / Alembic transition

## What remains

- All 17 foundation tables, constraints, indexes, RLS settings and existing rows remain.
- Supabase Auth still handles Google/Microsoft OAuth; provider settings are unchanged.
- Existing `users.auth_user_id` links remain. Legacy server sessions are upgraded after
  a verified Supabase user lookup; successful new logins save the Auth UUID server-side.
- Timestamp/history-integrity triggers from 001 remain useful database safeguards.
- 002 was read-only and needs no cleanup.
- 003 RPC functions stay temporarily for any old application instances. The new code
  never calls them. Do not install the former `004_client_user_admin.sql`; it was removed.

## Connections

`DATABASE_URL` is the administrative connection, used only by setup, inspection and
Alembic. `APP_DATABASE_URL` uses the separate LOGIN role `codebi_app` for web requests.
The web app refuses an administrative username and checks the connected role against
superuser, role/database creation, RLS bypass, postgres membership and schema-creation
privileges. There is no fallback to DATABASE_URL.

The role has only schema USAGE, SELECT on reference tables, SELECT/INSERT/UPDATE on
clients/users/memberships, SELECT/INSERT on global assignments, INSERT on login/audit
history, and client-number sequence USAGE. It has no DELETE, DDL, Auth-table SELECT,
or SOW table privileges. SOW permissions will be introduced with that feature.

`database/004_flask_database_access.sql` contains permission statements, not business
functions. It adds backend-role RLS policies to eight tables. Database table privileges
still restrict operations. RLS remains enabled and anon/authenticated direct table
access remains closed. Client isolation and role rules for this trusted backend role
are enforced by Python services, not per-browser SQL policies. This role's credentials
must never be sent to browsers or used by the Supabase JavaScript client.

## Setup / migration sequence

Install requirements in the existing virtual environment. Then review the setup plan:

```powershell
.\app\.venv\Scripts\python.exe -B scripts/setup_app_database.py
```

Apply the prepared setup:

```powershell
.\app\.venv\Scripts\python.exe -B scripts/setup_app_database.py --apply
```

This creates/reuses codebi_app, grants only the listed privileges, generates a random
password locally, and writes APP_DATABASE_URL into the ignored .env without printing
credentials. It tests SSL connectivity and adopts revision `0001_existing_foundation`.
An existing role without local credentials requires an explicit `--rotate-password`;
do not rotate while other instances use those credentials. Existing portal data is not
rewritten. The baseline only adds `codebi.alembic_version` after checking the installed
tables and mapped columns. Do not stamp an incomplete schema to bypass these checks.

Restart Flask and test Google/Microsoft sign-in, client creation, user provisioning,
client-scoped access, and logout. Copy the restricted APP_DATABASE_URL to production's
protected environment when deploying the new code. Keep DATABASE_URL out of the web
service's environment in production; supply it only to migration/administrative jobs.

The searchable client selectors add revision `0002_reference_catalogs` before the
still-unapplied RPC cleanup. Install those lookups with `python -m alembic upgrade
0002_reference_catalogs`; this does not remove 003 functions. See CLIENT_LOOKUPS.md.

After **every** running instance has moved to the Python implementation and sign-in
works, retire the three old RPC functions with:

```powershell
.\app\.venv\Scripts\python.exe -B -m alembic -x confirm_rpc_retired=true upgrade 0002_retire_auth_rpc
```

The confirmation flag prevents accidental cleanup while an old deployment still uses
003. This migration drops only the two public auth RPCs and their private helper,
without CASCADE. It does not drop tables, users or triggers. Downgrade to 0002_reference_catalogs restores
the saved 003 definitions. The old foundation cannot be automatically downgraded to
empty; no migration deletes the adopted schema.

Check revision status with `python -m alembic current` using the virtual environment.

## Application structure

- `app/models.py`: explicit ORM mappings for the current feature and reference tables. No
  runtime create_all, reflection, or automatic schema changes.
- `app/database.py`: engine and short-lived SQLAlchemy transactions.
- `app/services.py`: identity linking, current roles, tenant checks, CRUD and audit.
- `app/auth.py`: Supabase PKCE and authoritative get_user verification; never trusts
  editable user_metadata for email verification, roles or client membership.
- `app/administration.py`: forms and HTTP handling; data operations call Python services.
- `migrations/`: versioned schema evolution, using the administrative connection.

Each service resolves the active caller using the server's verified Auth UUID. Client
IDs from URLs/forms are checked against current memberships. Writes and audit inserts
commit or roll back together. Client-row locks serialize membership changes; row locks
protect first-time identity linking. Emails are immutable for already provisioned users.

The remaining nine SOW/reference/invitation tables are retained but not yet mapped for
these features. Alembic intentionally uses **hand-authored revisions** (target_metadata
is None). Autogenerate is disabled rather than risking removal of existing unmapped
tables, foreign keys, indexes or Supabase objects. To evolve the schema:

```powershell
.\app\.venv\Scripts\python.exe -B -m alembic revision -m "describe schema change"
```

Edit/review upgrade/downgrade, add corresponding ORM mappings, test, then migrate.
For a brand-new Supabase project, install the saved 001 foundation before adopting
0001; this transition is designed for the existing database, not generic empty Postgres.

## Tests

Run `python -B -m unittest discover -s tests -v` in the virtual environment. Service tests
use real SQLAlchemy sessions and an isolated SQLite schema. They verify queries,
authorization, client isolation, creation, linking, deactivation, rollback, audit and
email immutability. SQLite does not prove PostgreSQL RLS, row locks, generated columns,
or database triggers. Those require the restricted PostgreSQL verification and live
browser test. No automatic user-data fixtures are created in the live database.
