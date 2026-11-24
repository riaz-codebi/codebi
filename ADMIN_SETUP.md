# Clients and users

Administration now runs in **Flask Python services with SQLAlchemy**. Do not run the
former 004_client_user_admin.sql; it has been removed. See DATABASE_ACCESS.md for
the restricted PostgreSQL connection and Alembic baseline setup.

After setup and a Flask restart:

1. Open **Primary Administration > Clients > Create client**.
2. Save company, contact, address, industry and timezone details.
3. Saving returns to the client directory. Open that client's **Users** action separately
   and add the first user as **Client Administrator**.
4. Add other client users and select their role. Both Client User and Client
   Administrator roles include SOW creation; there is no separate permission checkbox.
5. Share the sign-in URL. Provisioning does not send an invitation email or store passwords.

Industry, country, state/region and timezone use searchable selectors. Countries,
subdivisions and IANA timezone values are stored in lookup tables introduced by the
reference-catalog Alembic revision; see CLIENT_LOOKUPS.md. Choosing a country clears
the previous state selection and loads only that country's subdivisions.

The global Users directory lists internal and client users with type filters, search,
pagination, role badges, login state and edit actions. Internal super administrators can
create additional internal administrators. Client admins see only their client membership
management screens. Membership deactivation retains history and does not deactivate the
person's account for unrelated clients.

Flask services recheck the active caller and client on every operation. Internal
administrators can correct a provisioned user's email before the first verified sign-in;
duplicates are rejected and the correction is audited. Once linked to an Auth identity
or previously signed in, email is locked in both the form and service, with an explanation
below the email field. Auth UUID cannot be changed by forms. Client admins cannot grant global roles,
attach existing cross-client accounts or modify shared profiles. Email addresses are
globally unique, normalized to lowercase with surrounding whitespace removed. New user
forms reject an already registered email with "This email is already in use," even
for global administrators, the same client, or an inactive account. Existing users
cannot be linked to additional clients through provisioning. Email corrections before
verification follow the same uniqueness rule. PostgreSQL's existing unique email
constraint also protects concurrent writes; no migration is needed. The last
active client administrator and an internal administrator's own account are protected.
Writes and audit events are committed together. Scope-of-work creation, client
approval and the internal review queue are described in SCOPE_OF_WORK_SETUP.md.

SOW creation permission is derived from the client role in Flask, including for
existing memberships with the legacy `can_create_sows` column set to false. That
column is retained for schema compatibility and synchronized to the role on save.
Inactive users, memberships and clients remain excluded from portal access. No
database migration or live backfill is required for this change.

The reference-style portal layout is retained: dedicated header, collapsible navigation,
Segoe UI/system typography, blue active states, compact forms, search filters and tables.

Run tests with `.\app\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`.
Service tests execute real SQLAlchemy queries in an isolated SQLite database; PostgreSQL
RLS, row locks, triggers and generated values require PostgreSQL checks. No test accounts
or clients are automatically retained in the live project.
# Client branding

Apply Alembic revision `0013_client_branding` before running the updated app against
another database. It creates the private `client-branding` Storage bucket.

CodeBI internal admins can upload or replace a PNG on the Create client and Edit
client forms. The app puts it in `<client UUID>/brand/logo.png`. Saving a client
without a logo creates the brand folder with a Storage placeholder. An existing
client gets its folder when its profile is saved.

The dashboard serves the logo through a signed-in portal route. Supabase also checks
the user's active client membership or internal administrator role before allowing
the download.
