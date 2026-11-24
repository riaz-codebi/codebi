# Service Portal sign-in

Supabase Auth continues to handle Google and Microsoft OAuth using PKCE. Flask now uses
SQLAlchemy for portal identity linking, last-login history, roles and client membership.
See DATABASE_ACCESS.md for the runtime connection, baseline and eventual 003 cleanup.

## Environment and provider configuration

Keep the existing root .env values:

```dotenv
SUPABASE_URL="https://pnzleldwnpxudcupbgyb.supabase.co"
SUPABASE_KEY="YOUR_PUBLISHABLE_KEY"
APP_BASE_URL="http://localhost:5000"
DATABASE_URL="YOUR_ADMINISTRATIVE_CONNECTION"
APP_DATABASE_URL="GENERATED_RESTRICTED_CONNECTION"
```

The setup script generates APP_DATABASE_URL. Never use a service-role key or administrative
connection for web requests. No provider client secrets are stored by Flask; those remain
in Supabase. The app never falls back to DATABASE_URL.

Supabase URL Configuration must allow `http://localhost:5000/auth/callback` for local tests.
Google and Microsoft provider registrations use the hosted Supabase callback:
`https://pnzleldwnpxudcupbgyb.supabase.co/auth/v1/callback`.
Microsoft supports personal/work/school accounts with its common tenant URL and email
scope. Keep verified email claims enabled; see Supabase's Microsoft provider guide for
email/xms_edov optional claims. No files, mail or calendar scopes are requested.

Start the existing app:

```powershell
.\app\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\app\.venv\Scripts\python.exe -B -m flask --app run:app run
```

Open http://localhost:5000/auth/login and consistently use localhost throughout login.
Restart after environment changes. For production set APP_BASE_URL to the canonical HTTPS
origin and use Gunicorn run:app, never the Flask debug server.

## Identity and authorization

After exchanging the PKCE code, Flask calls Supabase's authoritative get_user endpoint.
It checks confirmed email and provider-maintained identity data, not editable user_metadata.
A matching active pre-provisioned user can be linked once to the verified Auth UUID.
Unrecognized verified identities are directed to Contact and receive
no portal session. The public inquiry form on Contact saves name, email,
company and details in `codebi.public_inquiries` and sends a receipt through Resend.
Apply Alembic revision `0012_public_inquiries` before deploying the form. Later access
for provisioned users uses the linked Auth UUID.
An unrelated Auth UUID cannot take over an already linked email. Both providers must map
to the same Supabase identity to access one existing portal account.

Python services load current active roles/memberships on every protected operation.
Inactive users/clients/memberships lose access. Existing encrypted sessions without an
Auth UUID are upgraded after verified user lookup, preserving the login where possible.
Success/denial history is stored through the normal SQLAlchemy transaction.

## Server sessions

Tokens and PKCE verifiers remain in encrypted server-side state under ignored instance/;
browser cookies carry opaque IDs and CSRF state. Pending state is single-use and expires
after ten minutes; portal sessions expire after eight hours. Access tokens refresh on the
server. POST login initiation and logout require CSRF. Logout deletes local state and
attempts Supabase local-session revocation; provider-wide sessions remain.

Supply a stable random SECRET_KEY of at least 32 characters in production. For local
development an ignored instance/secret.key is generated if none is configured. Keep the
instance directory private, persistent and shared by workers on the single server. Multiple
servers need a shared server-side session store before scaling out. Cookies are HttpOnly,
SameSite=Lax and Secure when APP_BASE_URL uses HTTPS. Avoid callback query strings in
access logs. Neither the database architecture change nor deployment changes Google
consent branding automatically.

## Checks

Run the unittest suite. PKCE/token endpoint calls are mocked; real Google/Microsoft browser
sign-in must be checked after deployment. Verify existing-user access, the public inquiry form,
sign-out, and rejection of unverified/reserved/inactive identities. Keep the old 003 functions until every instance uses the new code;
then apply the explicit Alembic cleanup as described in DATABASE_ACCESS.md.
