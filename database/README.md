# CodeBI Service Portal database foundation

Portal heading: **Service Portal for [Client Name]**.
Work item: **Statement of Work (SOW)**.

Current architecture: Flask + SQLAlchemy, with Alembic for future schema changes.
See `DATABASE_ACCESS.md` in the repository root for the transition. The previously
installed `003_portal_auth.sql` is retained as history/rollback material; new code does
not call it. The former unexecuted `004_client_user_admin.sql` has been removed.
`004_flask_database_access.sql` grants a restricted backend role through the setup
script. The closed-access discussion below describes the original 001 baseline.

## Run in Supabase

1. Open the intended project's SQL Editor as the database administrator.
2. Paste the complete contents of `001_service_portal.sql` and run once.
3. Run `002_verify_service_portal.sql` and review the result sets.

The creation script is one transaction: an error rolls back its changes. It intentionally
does not use `CREATE TABLE IF NOT EXISTS`; this avoids silently accepting incompatible
preexisting tables. Do not rerun after success. If the editor reports an aborted transaction,
issue `ROLLBACK;` before retrying a corrected script. This is a fresh-schema baseline,
not an upgrade script for an existing portal.

No existing table is dropped or changed, including `codebi.test`. No changes are made
to the `auth`, `storage`, `history`, or other schemas. The `auth.users` primary key is
referenced, not altered. The `codebi` schema description is updated.

## Tables and relationships

- `industries` -> `clients`: one industry, many clients.
- `users`: provisioned accounts with an optional link to Supabase `auth.users`.
- `roles` + `user_roles`: global administrator assignments.
- `client_memberships`: users, client roles, and `can_create_sows` for each client.
- `user_invitations`: proposed memberships, expiry and acceptance details.
- `login_events`: portal login history; never store credentials or tokens.
- `sow_types` + `tools`: configurable reference values.
- `statements_of_work` + `sow_tools`: client requirements and selected technologies.
- `sow_estimates`: numbered scope/effort revisions for each SOW.
- `sow_messages`: client-visible correspondence (append-only).
- `sow_events`: request lifecycle history (append-only).
- `time_entries`: activity and time tracking; internal notes are separate fields.
- `audit_events`: administrative history (append-only).

Foreign keys include client/SOW pairs where tenant IDs are repeated. Estimates and
message references in SOW events must match the same SOW and client. Roles are
restricted to their intended global or client scope. Identity sequence gaps are normal.
Timestamps use `timestamptz`; render in the client's IANA timezone. Country codes are
two uppercase letters. Client users are deactivated rather than deleted.

## Initial data

- Roles: `super_admin`, `client_admin`, `client_user`.
- Five industries and five SOW types.
- Eleven tool choices, including Other.
- `riaz@codebisolutions.com` provisioned as an internal super administrator.

This seed does not create a Supabase Auth account or grant access to an unauthenticated
visitor. A controlled server flow must link the verified OAuth identity to the provisioned
user. No password, service key, OAuth client secret or connection string is embedded.

## Security boundary and remaining application work

All 17 new tables have RLS enabled and direct table privileges revoked from PUBLIC,
anon and authenticated. No permissive policies or new API exposure are installed.
This is deliberately a **closed database foundation**, not a working portal yet.
The project administrator and privileged roles can still bypass ordinary RLS.
Do not use the current administrative DATABASE_URL as the eventual web application's
unrestricted query identity. Review any existing custom-role grants separately.

Before enabling portal access, implement and test restricted-role access or user-scoped
Supabase policies/functions, including:

- Active user/client checks; client-wide SOW visibility; global admin scope.
- Client admins managing only their own client users; no global-role self-promotion.
- Creation permission, draft ownership, client-admin submission/acceptance/replies.
- Verified identity linking, single-use invitation acceptance and duplicate-login handling.
- Atomic, authorized status transitions with event logging and concurrency control.
- Immutable issued estimate revisions; acceptance must target the current issued feasible
  revision and set the SOW's accepted estimate in the same transaction. The foundation
  foreign key enforces SOW ownership but does not enforce this complete acceptance protocol.
- Freeze accepted scope; amendments require another revision and explicit acceptance.
- Internal-only estimates/time recording; work starts only after acceptance.
- Submitted non-void time totals versus the accepted estimate; show an overrun indicator.
- Field filtering: never return internal time notes or private administrative data to clients.
- Valid IANA timezone input, sanitized audit changes, and login-event retention limits.

The core status sequence is draft -> pending_client_approval -> submitted -> under_review
-> awaiting_acceptance -> approved -> in_progress -> completed. Clarification can interrupt
review; requested changes can return a draft; declined and cancelled are terminal outcomes.
The schema checks allowed status values and basic required fields, not every transition.

Email delivery, attachments, invoicing, contracts and other FANSMO modules are out of scope.
Schema changes after this baseline should be versioned migrations; this project does not
yet have Alembic installed or configured.

## Validation scope

The scripts are checked locally with a PostgreSQL SQL parser. This validates SQL syntax,
not execution semantics, database permissions, PL/pgSQL runtime, or application workflows.
They have not been executed against Supabase. Run the verification SQL after creation.
