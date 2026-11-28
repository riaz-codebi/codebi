# Code BI Solutions current architecture audit

## Executive summary

The live Supabase PostgreSQL database already has an organization model. `codebi.clients` represents organizations, and `codebi.client_memberships` links users to clients and client roles. The existing Flask portal and Supabase Auth can serve as the shared login and account layer for commercial tools. Today, Flask enforces client isolation for portal database access. The live policies allow the runtime SQL role to operate across client rows within its table grants.

PostgreSQL 17.6 has 12 non-system schemas, with 65 tables and three views across them. Five directly support the application or its retained database interfaces: `codebi`, `codebi_branding`, `auth`, `storage`, and `public`. The application-owned `codebi` schema has 23 tables: 20 mapped by current code, plus `user_invitations`, `test`, and `alembic_version`. The other four application-relevant schemas serve OAuth, Storage, branding authorization, or legacy RPCs. The `history` schema contains a separate table with no identified repository consumer.

Use a hybrid design. Keep identity, client membership, and commercial entitlements in `codebi`. Give Vendor Catalog Extraction & Mapping a private `catalog` schema with tenant-aware constraints, scoped database permissions, and its own worker entry point. Future tools can use their own schemas when their datasets and release lifecycles justify a boundary.

## Verification summary

| Area | Status | Evidence and limits |
| --- | --- | --- |
| Repository inspection | Complete | Static review of application entry points, routes, mappings, migrations, SQL, configuration and test sources. Runtime package versions verified through installed package metadata. Production host and OAuth settings were outside the repository. |
| Live PostgreSQL connection | Successful | `APP_DATABASE_URL` authenticated as `codebi_app` in a read-only transaction after network approval. |
| Schema inventory | Complete | All non-system schema and relation metadata visible in the PostgreSQL catalogs was inventoried. Counts are estimates and may be unavailable. Migration tracker contents require a privileged reader. |
| RLS and permissions inspection | Complete | Catalog inspection covered stored policies, effective CRUD grants for application-facing roles, schema ACLs, routine ACLs, and role membership. Hosted API exposure and end-to-end login were unverified. |
| SQLAlchemy reconciliation | Complete | All 20 mappings compared with the live catalog for table/column presence, SQL types, nullability, primary keys, and foreign/unique constraint coverage. |

## Current application and deployment architecture

- **Flask 3.0.3**, SQLAlchemy **2.1.9**, Alembic **1.20.1**, psycopg **3.3.9**, and Supabase Python **2.31.0** match both `requirements.txt` and the installed workspace environment. `run.py` exposes `run:app`. The application factory in `app/__init__.py:create_app` loads root dotenv configuration, validates the canonical origin, initializes database and session storage, wires Supabase Storage, registers five blueprints, and applies response headers. Templates and static JavaScript implement the browser UI.
- **Public site.** `app/routes.py:main` serves home, services, products, asset listings, contact, privacy, and terms. `list_supabase_files` reads configured Storage prefixes for public assets. `app/inquiries.py:inquiry_page` validates contact submissions, writes a `PublicInquiry`, then sends notifications through Resend. Its five-minute check limits repeated acknowledgments to an email address. Each valid submission is still saved and attempted for staff notification.
- **Portal.** `app/auth.py:auth` defines OAuth initiation, callback, logout, and registration endpoints. `portal` serves the dashboard and admin entry. `app/administration.py:administration` handles inquiries, clients, users, reference lookups, addresses, and branding. `app/scopes.py:scopes` handles directory, draft/edit, detail, signed review links, and workflow transitions.
- **SQLAlchemy.** `app/database.py:init_database` checks the role on each physical connection, sets TLS and connection timeouts, and pools three connections with two overflow slots per process. `transaction` uses short-lived ORM transactions. Mappings in `app/models.py` explicitly target `codebi`. The runtime requires `APP_DATABASE_URL`. Alembic uses its separate `DATABASE_URL` and `NullPool`. Secret values are omitted.
- **Hosting.** `AUTH_SETUP.md` documents Gunicorn with a canonical HTTPS origin and local persistent session state. `run.py` starts the development server when executed directly. No tracked systemd service, Dockerfile, reverse proxy configuration, production WSGI worker settings, task deployment manifest, or CI/CD workflow was identified. The production host, scaling topology, backup configuration and deployment settings are unverified. Supabase provides the live database, and repository integrations use Supabase Auth/Storage, Resend and Google Places.

## Authentication, authorization, and organization capabilities

**Identity and sessions.** `app/auth.py:start` accepts Google and Microsoft (Supabase's `azure` provider). The callback consumes a ten-minute PKCE state once, exchanges the code, then verifies the Supabase user, matching provider email and verification status through `verify_identity`. `app/services.py:portal_login` links the first verified matching email to a provisioned portal user, or resolves a returning user by Auth UUID. The live `codebi.users.auth_user_id` is unique, nullable, and references `auth.users.id` with `ON DELETE SET NULL`. Supabase stores login identities; the portal user stores business identity and access. Provider enablement, email-claim settings, and redirect allowlists require hosted configuration or a browser test.

`app/session_store.py:SessionStore` stores encrypted tokens and transient OAuth state in local SQLite. Flask's signed cookie carries opaque identifiers and CSRF state. Portal sessions expire after eight hours. `login_required` refreshes tokens near expiry and reloads the current authorization context. Login initiation, logout, and state-changing portal forms check CSRF. Cookie flags are HttpOnly and SameSite=Lax, with Secure enabled for HTTPS. Logout clears local state and attempts Supabase session revocation. One host can share the instance directory across web processes, while multi-host deployment needs shared session state and a common stable signing key.

**Registration.** `register_client` can create a client and first `client_admin` transactionally, and `/auth/register/client` still has its form and onboarding guard. The current OAuth callback redirects unprovisioned verified identities to the contact inquiry and clears auth state, so the normal flow cannot enter self-registration. Administrator provisioning is active. The live `user_invitations` table has neither an ORM mapping nor runtime grants, and the current user provisioning flow sends no invitation email.

**Authorization and administration.** `services.actor`, `context_for`, and `is_global` resolve active users, current memberships and global privilege. Internal `super_admin` users can manage clients and portal users, view inquiries, and operate across clients. An active `client_admin` can manage users in an authorized client context. Memberships support many-to-many user/organization assignments with a live unique `(client_id, user_id)` constraint. `sow_services.access` and `record` resolve the client and enforce membership, client status, role and draft visibility. Current SOW creation rules derive from `client_user` or `client_admin` role via `role_can_create_sows`. The persisted `can_create_sows` field remains in the model and database.

**Consulting and products.** The public inquiry queue and the scoped consulting workflow are implemented in repository code, with corresponding live relations. `sow_services.save` and `transition` implement drafting, submission, internal review, clarification, estimates, client acceptance, progress, completion and history. The related SQL uses client-scoped keys for SOW child records and accepted estimate references. `Tool` and `SowTool` are solution choices attached to consulting scopes. No commercial entitlement, billing integration, tool launch permission, dedicated tool assignment, or separate workspace workflow was identified in the inspected application or relevant schemas. The existing client is the natural organization boundary.

## Current database structure

The diagram summarizes selected live foreign-key relationships. The catalog appendix contains the full columns, types, defaults, constraints, indexes, sequences, grants and policies.

~~~mermaid
erDiagram
    AUTH_USERS o|--o| PORTAL_USERS : "optional portal link"
    PORTAL_USERS ||--o{ CLIENT_MEMBERSHIPS : joins
    CLIENTS ||--o{ CLIENT_MEMBERSHIPS : has
    ROLES ||--o{ CLIENT_MEMBERSHIPS : scopes
    PORTAL_USERS ||--o{ USER_ROLES : holds
    ROLES ||--o{ USER_ROLES : grants
    CLIENTS ||--o{ STATEMENTS_OF_WORK : owns
    STATEMENTS_OF_WORK ||--o{ SOW_ESTIMATES : has
    STATEMENTS_OF_WORK ||--o{ SOW_MESSAGES : has
    STATEMENTS_OF_WORK ||--o{ SOW_EVENTS : records
    STATEMENTS_OF_WORK ||--o{ TIME_ENTRIES : records
    STATEMENTS_OF_WORK ||--o{ SOW_TOOLS : selects
    TOOLS ||--o{ SOW_TOOLS : categorizes
    STORAGE_BUCKETS ||--o{ STORAGE_OBJECTS : contains
~~~

`AUTH_USERS` represents `auth.users`. `PORTAL_USERS` represents `codebi.users`. The Storage nodes represent `storage.buckets` and `storage.objects`. Public inquiries are standalone records. Branding authorization reaches `codebi.users`, `clients` and `client_memberships` through the helper described below.

## Database security and RLS

- **Live runtime role.** `codebi_app` can log in but has no superuser, RLS bypass, role creation, database creation or schema CREATE privilege. `anon` and `authenticated` have no direct grants to `codebi` tables. RLS is enabled on 22 of the 23 `codebi` tables and forced on none. The exception, `alembic_version`, is inaccessible to the runtime role. ACLs limit operations independently of policy. For example, `sow_tools` supports runtime deletes, while append-only history tables accept inserts under the applicable grants.
- **Isolation boundary.** The live `flask_backend_access`, `flask_sow_access`, `flask_scope_review`, and inquiry policies allow the service role broadly through true expressions. Flask's client checks and query predicates provide tenant isolation, with database foreign keys preserving same-client integrity in selected consulting relationships. A new database consumer using `codebi_app` would inherit cross-client data access within those grants.
- **Storage.** Live `storage.objects` policies call the `SECURITY DEFINER` helper `codebi_branding.can_access(text, boolean)` for the `client-branding` bucket. Repository migration `0013_client_branding` defines active-client membership checks for reads and internal super-admin checks for writes. `app/client_branding.py` uses the signed-in user's bearer token. Catalog definitions establish that the policies and helper exist. Bucket visibility settings, file contents, and proof of end-to-end Storage access were outside this inspection.
- **Legacy RPCs.** The catalog still lists `public.codebi_portal_context()`, `public.codebi_portal_login(text)`, and `codebi.portal_identity_context(uuid)`. Both public functions are `SECURITY DEFINER` and grant EXECUTE to `authenticated` and `service_role`. Current Flask sign-in uses Python services. Hosted PostgREST exposure, consumers and the installed routine bodies were unverified.
- **Metadata scope.** ACLs, policy expressions, routine signatures, sequences and triggers were inspected. No customer records, Auth records, object contents, vault contents, live routine bodies, or secret-bearing role settings were read.

## SQLAlchemy and migration assessment

`database/001_service_portal.sql` supplies the original 17-table foundation. `migrations/versions/0001_existing_foundation.py` adopts an installed foundation after verifying structure. Follow-on hand-authored revisions add catalogs, SOW access and pricing, inquiries, and branding. `migrations/env.py` disables autogenerate and tracks revisions in `codebi.alembic_version`. The repository has two heads, `0014_client_logo_background` and `0002_retire_auth_rpc`. The retirement branch requires explicit confirmation and should be evaluated for old consumers. The live branding column and surviving RPCs establish object presence. The actual Alembic version rows were unavailable through the restricted role.

All 20 mapped tables exist. The database carries additional nullability, defaults, checks, indexes and foreign keys that ORM mappings often omit. Examples include the Auth-to-portal FK, membership uniqueness, and same-client SOW links. The live schema also contains unmapped fields in login events, estimates, and time entries. Compare the appendix when adding models or validating drift. Reproducing the development schema from ORM metadata alone would omit PostgreSQL enforcement.

`user_invitations` is a foundation table unused by the current portal runtime. `codebi.test` and `history.knowledge_entries` have no identified repository owner or runtime dependency. Their usage and retention remain unverified. The recent catalog and branding migrations make some counts in older setup documentation historical.

## Multi-tenant architecture options

| Option | Fit and operating tradeoffs |
| --- | --- |
| A. Shared schemas and tables | Adding commercial tables to `codebi` follows the current deployment and keeps one migration stream. Shared tenant data stays efficient with indexed client keys and explicit tenant constraints. As unrelated tools grow, grants, model discovery, naming and releases become more coupled. |
| B. Schema per commercial tool | Private tool schemas separate SQLAlchemy mappings, grants, and migration ownership. Each tool still needs a client discriminator, scoped keys and tenant-aware RLS when clients share it. Cross-schema identity and membership references require explicit dependencies. Keep tool schemas out of direct Supabase API exposure unless a tool specifically needs that interface. |
| C. Schema per client | A per-client namespace can support exceptional contractual separation, but routine migrations, grants, pooled connections, schema routing and incident recovery repeat across clients. Shared tooling and reporting become more expensive. A schema alone provides neither authorization nor a separate backup or compute boundary. |
| D. Hybrid | Reuse `codebi` for identity, organizations and entitlement checks. Give catalog its own schema and keep client rows shared within it. Dedicated access is an entitlement restriction. The arrangement fits the current explicit schema mappings and keeps unrelated tool data and worker permissions separate. |

## Recommended architecture and proposed database changes

The following expansion is **proposed**. Preserve the current portal identities, client IDs, memberships and consulting data.

1. Add a commercial product registry and organization entitlements to `codebi`. Use a stable tool key, entitlement status and validity window, with an external subscription reference if billing is introduced. Gate each tool on active user, active membership, active organization, active entitlement and the relevant tool action. Add member-specific grants only when client-level roles cannot express a product's permissions. Shared and dedicated products can use the same model with different entitlement assignment rules.
2. Add `catalog` with organization-owned vendors, source imports, normalized products and preserved vendor attributes, destination mapping versions, extraction jobs and export metadata. Use structured columns for common search and join keys and JSONB for original vendor-specific attributes. Use client-qualified unique and foreign keys throughout. Version mapping and export inputs so a result can be reproduced. Index organization, vendor, job status and recent activity based on actual access paths.
3. Use restricted tool-specific web and worker roles or narrowly scoped grants as the tool matures. Keep tenant context transaction-local where required by RLS and fail closed when scope is missing. Apply membership and entitlement checks in Flask even when RLS is present. Test overlapping identifiers, tenant mismatch, revoked memberships, pooled transaction reuse, worker retries and unauthorized artifact downloads. Keep output CSV/XLSX objects private and link downloads to authenticated, authorized export records.
4. Use explicit SQLAlchemy schema names and hand-authored revisions. Centralize ownership of shared data definitions and dependency ordering. Begin with the current Alembic infrastructure and document the catalog revision dependencies before allowing independent releases. Separate deployment units or migration branches can follow when teams need them.

~~~mermaid
flowchart LR
    U[Portal user] --> F[Flask portal]
    A[Existing Supabase Auth] --> F
    F --> C[Existing clients, users and memberships]
    F --> E[Proposed commercial entitlements]
    E --> T[Proposed catalog tool]
    T --> J[Catalog job and export metadata]
    W[Independent Python catalog worker] --> J
    W --> D[Catalog sources, products and mappings]
    W --> S[Private export storage]
    F --> S
    E --> X[Future tools and optional private schemas]
~~~

## Background-worker integration

No Redis, RQ, Celery, existing worker process, durable job tracking, queue consumer, or scheduler was identified. HTTP request handlers currently do external work synchronously, including notifications and Google Places calls. Consulting status/history tracks business activity, and the SQLite database stores session state. Both have different lifecycles from extraction jobs.

For the first tool, use durable PostgreSQL job records in `catalog` and one separately supervised Python worker. Flask authorizes the request, creates the job in a short transaction, and returns a job ID for status polling. The worker uses its own restricted connection and short claim transactions with row locking and skip-locked semantics. Store lease expiry, attempt count, timestamps, sanitized error codes, and result references. Make writes and export publication idempotent, recover expired leases, and apply bounded retries and per-client concurrency limits. Hold no database transaction open during extraction or upload. Log job IDs and status transitions without vendor payloads, and keep web and worker lifecycle management separate. A broker can be added if workload or scheduling requirements justify it. No infrastructure changes were made during this audit.

## Identified gaps and implementation sequence

1. Verify hosted OAuth settings, production process/session topology, private Storage configuration and the exact Alembic revision using an authorized metadata-only administrative review. Check dependencies before retiring the live definer RPCs. Credentials included in the IDE context were omitted from the report.
2. Define organization entitlement and tool action rules. Reuse existing client membership and keep public inquiry, admin provisioning, and any future self-service signup as distinct business flows.
3. Establish a PostgreSQL integration environment and validate role grants, tenant RLS, cross-client FK protections, and pool behavior. Existing tests in `tests/test_auth.py`, `test_registration.py`, `test_administration.py` and `test_scopes.py` exercise application paths. Mocked provider and SQLite tests cannot establish live OAuth, PostgreSQL policy or lock behavior.
4. Add commercial metadata and the private catalog schema, then deliver one tenant-scoped job and private export end to end. Extend to vendor mappings, repeatable export generation, subscription lifecycle and tool assignment once isolation and retry behavior are demonstrated.
5. Exercise concurrent workers and web processes, recovery, queue limits, backup/restore, audit events and output retention before onboarding additional clients. Decide whether PostgreSQL job polling remains adequate using observed throughput and queue delay.


## Live PostgreSQL inventory

Verified through the restricted application login. Counts use catalog statistics and can lag writes. A dash means an unavailable estimate or an empty metadata field. Relation kinds are ordinary table (r), partitioned table (p), view (v), materialized view (m), and foreign table (f).

| current_database | current_user | server_version | read_only | pgrst_schemas |
| --- | --- | --- | --- | --- |
| postgres | codebi_app | 17.6 | on | — |

| Schema | Tables | Views or materialized views | Owner | ACL | Runtime USAGE | Runtime CREATE |
| --- | --- | --- | --- | --- | --- | --- |
| auth | 27 | 0 | supabase_admin | ['supabase_admin=UC/supabase_admin', 'anon=U/supabase_admin', 'authenticated=U/supabase_admin', 'service_role=U/supabase_admin', 'supabase_auth_admin=UC/supabase_admin', 'dashboard_user=UC/supabase_admin', 'postgres=U/supabase_admin'] | no | no |
| codebi | 23 | 0 | postgres | ['postgres=UC/postgres', 'codebi_app=U/postgres'] | yes | no |
| codebi_branding | 0 | 0 | postgres | ['postgres=UC/postgres', 'authenticated=U/postgres'] | no | no |
| extensions | 0 | 2 | postgres | ['postgres=UC/postgres', 'anon=U/postgres', 'authenticated=U/postgres', 'service_role=U/postgres', 'dashboard_user=UC/postgres'] | no | no |
| graphql | 0 | 0 | supabase_admin | ['supabase_admin=UC/supabase_admin', 'postgres=U*/supabase_admin', 'anon=U/supabase_admin', 'authenticated=U/supabase_admin', 'service_role=U/supabase_admin'] | no | no |
| graphql_public | 0 | 0 | supabase_admin | ['supabase_admin=UC/supabase_admin', 'postgres=U*/supabase_admin', 'anon=U/supabase_admin', 'authenticated=U/supabase_admin', 'service_role=U/supabase_admin'] | no | no |
| history | 1 | 0 | postgres | — | no | no |
| pgbouncer | 0 | 0 | pgbouncer | — | no | no |
| public | 0 | 0 | pg_database_owner | ['pg_database_owner=UC/pg_database_owner', '=U/pg_database_owner', 'postgres=U/pg_database_owner', 'anon=U/pg_database_owner', 'authenticated=U/pg_database_owner', 'service_role=U/pg_database_owner'] | yes | no |
| realtime | 3 | 0 | supabase_admin | ['supabase_admin=UC/supabase_admin', 'postgres=U*/supabase_admin', 'anon=U/supabase_admin', 'authenticated=U/supabase_admin', 'service_role=U/supabase_admin', 'supabase_realtime_admin=UC/supabase_admin'] | no | no |
| storage | 10 | 0 | supabase_admin | ['supabase_admin=UC/supabase_admin', 'postgres=U*/supabase_admin', 'anon=U/supabase_admin', 'authenticated=U/supabase_admin', 'service_role=U/supabase_admin', 'supabase_storage_admin=U*C*/supabase_admin', 'dashboard_user=UC/supabase_admin'] | no | no |
| vault | 1 | 1 | supabase_admin | ['supabase_admin=UC/supabase_admin', 'postgres=U*/supabase_admin', 'service_role=U/supabase_admin'] | no | no |

The catalog contains **23 CodeBI schema tables**, including migration tracking. Migration-related relations: auth.schema_migrations, codebi.alembic_version, realtime.schema_migrations, storage.migrations.
The Alembic revision is unverified because the runtime role lacks SELECT on the migration tracker.

### SQLAlchemy reconciliation against live PostgreSQL

Live CodeBI tables without ORM mappings: alembic_version, test, user_invitations. ORM mappings without live tables: none.
| Mapped table | Structural differences (ORM / PostgreSQL) |
| --- | --- |
| audit_events | occurred_at nullable ORM=True, DB=False<br>foreign keys ORM=0, DB=2 |
| client_memberships | created_at nullable ORM=True, DB=False<br>updated_at nullable ORM=True, DB=False<br>foreign keys ORM=0, DB=4<br>unique constraints ORM=0, DB=1 |
| clients | client_number nullable ORM=True, DB=False<br>created_at nullable ORM=True, DB=False<br>updated_at nullable ORM=True, DB=False<br>foreign keys ORM=0, DB=6<br>unique constraints ORM=0, DB=2 |
| countries | Mapped columns, types, nullability and key counts align |
| industries | created_at nullable ORM=True, DB=False<br>updated_at nullable ORM=True, DB=False<br>unique constraints ORM=0, DB=1 |
| login_events | database-only columns ip_address, user_agent<br>occurred_at nullable ORM=True, DB=False<br>foreign keys ORM=0, DB=1 |
| public_inquiries | created_at nullable ORM=True, DB=False |
| roles | unique constraints ORM=0, DB=1 |
| sow_estimates | database-only columns notes<br>created_at nullable ORM=True, DB=False<br>updated_at nullable ORM=True, DB=False<br>foreign keys ORM=0, DB=3<br>unique constraints ORM=0, DB=2 |
| sow_events | occurred_at nullable ORM=True, DB=False<br>foreign keys ORM=0, DB=4 |
| sow_messages | created_at nullable ORM=True, DB=False<br>foreign keys ORM=0, DB=2<br>unique constraints ORM=0, DB=1 |
| sow_tools | foreign keys ORM=0, DB=2 |
| sow_types | Mapped columns, types, nullability and key counts align |
| state_regions | unique constraints ORM=0, DB=1 |
| statements_of_work | sow_number nullable ORM=True, DB=False<br>created_at nullable ORM=True, DB=False<br>updated_at nullable ORM=True, DB=False<br>foreign keys ORM=0, DB=6<br>unique constraints ORM=0, DB=3 |
| time_entries | database-only columns created_at, duration_minutes, internal_notes, updated_at, void_reason<br>foreign keys ORM=0, DB=2 |
| timezones | Mapped columns, types, nullability and key counts align |
| tools | Mapped columns, types, nullability and key counts align |
| user_roles | granted_at nullable ORM=True, DB=False<br>foreign keys ORM=0, DB=3 |
| users | created_at nullable ORM=True, DB=False<br>updated_at nullable ORM=True, DB=False<br>foreign keys ORM=0, DB=3 |

Python-side defaults and generated-value markers support inserts and refreshes. The live column inventory lists server defaults, identity sequences and generated expressions. Check constraints, triggers and indexes are maintained outside the ORM. Unique constraint counts exclude standalone unique indexes.

### Table statistics estimates

These estimates come from PostgreSQL statistics without scanning table rows. A zero can reflect empty or unpopulated statistics. The analyze flags only reflect statistics available to this session and can reset independently of table contents.

| Schema | Table | Estimated live rows | Manual analyze recorded | Autoanalyze recorded |
| --- | --- | --- | --- | --- |
| auth | audit_log_entries | 0 | no | no |
| auth | custom_oauth_providers | 0 | no | no |
| auth | flow_state | 6 | no | yes |
| auth | identities | 5 | no | no |
| auth | instances | 0 | no | no |
| auth | mfa_amr_claims | 16 | no | no |
| auth | mfa_challenges | 0 | no | no |
| auth | mfa_factors | 0 | no | no |
| auth | mfa_recovery_code_sets | 0 | no | no |
| auth | mfa_recovery_codes | 0 | no | no |
| auth | oauth_authorizations | 0 | no | no |
| auth | oauth_client_states | 0 | no | no |
| auth | oauth_clients | 0 | no | no |
| auth | oauth_consents | 0 | no | no |
| auth | one_time_tokens | 0 | no | no |
| auth | refresh_tokens | 25 | no | no |
| auth | saml_providers | 0 | no | no |
| auth | saml_relay_states | 0 | no | no |
| auth | schema_migrations | 84 | no | yes |
| auth | scim_tokens | 0 | no | no |
| auth | scim_users | 0 | no | no |
| auth | sessions | 16 | no | no |
| auth | sso_domains | 0 | no | no |
| auth | sso_providers | 0 | no | no |
| auth | users | 4 | no | yes |
| auth | webauthn_challenges | 0 | no | no |
| auth | webauthn_credentials | 0 | no | no |
| codebi | alembic_version | 1 | no | no |
| codebi | audit_events | 24 | no | no |
| codebi | client_memberships | 3 | no | no |
| codebi | clients | 2 | no | no |
| codebi | countries | 249 | no | yes |
| codebi | industries | 7 | no | no |
| codebi | login_events | 21 | no | no |
| codebi | public_inquiries | 1 | no | no |
| codebi | roles | 3 | no | no |
| codebi | sow_estimates | 1 | no | no |
| codebi | sow_events | 12 | no | no |
| codebi | sow_messages | 3 | no | no |
| codebi | sow_tools | 2 | no | no |
| codebi | sow_types | 5 | no | no |
| codebi | state_regions | 5046 | no | yes |
| codebi | statements_of_work | 3 | no | no |
| codebi | test | 0 | no | no |
| codebi | time_entries | 0 | no | no |
| codebi | timezones | 598 | no | yes |
| codebi | tools | 23 | no | no |
| codebi | user_invitations | 0 | no | no |
| codebi | user_roles | 1 | no | no |
| codebi | users | 4 | no | no |
| history | knowledge_entries | 8 | no | no |
| storage | bucket_lifecycle_states | 0 | no | no |
| storage | buckets | 2 | no | no |
| storage | buckets_analytics | 0 | no | no |
| storage | buckets_vectors | 0 | no | no |
| storage | lifecycle_abandoned_artifacts | 0 | no | no |
| storage | migrations | 82 | no | yes |
| storage | objects | 13 | no | no |
| storage | s3_multipart_uploads | 0 | no | no |
| storage | s3_multipart_uploads_parts | 0 | no | no |
| storage | vector_indexes | 0 | no | no |

~~~sql
SELECT schemaname, relname, n_live_tup, last_analyze IS NOT NULL AS analyzed,
       last_autoanalyze IS NOT NULL AS autoanalyzed
FROM pg_stat_all_tables
WHERE schemaname IN ('codebi','auth','storage','history')
ORDER BY 1,2;
~~~

## Database catalog evidence

ACL dashes indicate default privileges. Effective privilege checks include role inheritance. RLS can further restrict operations. Triggers below exclude internally generated constraint triggers.

### Relations

#### auth

| schema | relation | kind | owner | rls | force_rls | estimated_rows | acl | runtime_select | runtime_insert | runtime_update | runtime_delete | options |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auth | audit_log_entries | r | supabase_auth_admin | yes | no | — | ['supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin', 'postgres=ar*wdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | custom_oauth_providers | r | supabase_auth_admin | no | no | — | ['postgres=arwdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | flow_state | r | supabase_auth_admin | yes | no | 5 | ['postgres=ar*wdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | identities | r | supabase_auth_admin | yes | no | — | ['postgres=ar*wdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | instances | r | supabase_auth_admin | yes | no | — | ['supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin', 'postgres=ar*wdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | mfa_amr_claims | r | supabase_auth_admin | yes | no | — | ['postgres=ar*wdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | mfa_challenges | r | supabase_auth_admin | yes | no | — | ['postgres=ar*wdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | mfa_factors | r | supabase_auth_admin | yes | no | — | ['postgres=ar*wdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | mfa_recovery_code_sets | r | supabase_auth_admin | no | no | — | ['postgres=arwdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | mfa_recovery_codes | r | supabase_auth_admin | no | no | — | ['postgres=arwdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | oauth_authorizations | r | supabase_auth_admin | no | no | — | ['postgres=arwdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | oauth_client_states | r | supabase_auth_admin | no | no | — | ['postgres=arwdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | oauth_clients | r | supabase_auth_admin | no | no | — | ['postgres=arwdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | oauth_consents | r | supabase_auth_admin | no | no | — | ['postgres=arwdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | one_time_tokens | r | supabase_auth_admin | yes | no | — | ['postgres=ar*wdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | refresh_tokens | r | supabase_auth_admin | yes | no | — | ['supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin', 'postgres=ar*wdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | saml_providers | r | supabase_auth_admin | yes | no | — | ['postgres=ar*wdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | saml_relay_states | r | supabase_auth_admin | yes | no | — | ['postgres=ar*wdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | schema_migrations | r | supabase_auth_admin | yes | no | 76 | ['supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'postgres=r*/supabase_auth_admin'] | no | no | no | no | — |
| auth | scim_tokens | r | supabase_auth_admin | no | no | — | ['postgres=arwdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | scim_users | r | supabase_auth_admin | no | no | — | ['postgres=arwdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | sessions | r | supabase_auth_admin | yes | no | — | ['postgres=ar*wdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | sso_domains | r | supabase_auth_admin | yes | no | — | ['postgres=ar*wdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | sso_providers | r | supabase_auth_admin | yes | no | — | ['postgres=ar*wdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | users | r | supabase_auth_admin | yes | no | 2 | ['supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin', 'postgres=ar*wdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | webauthn_challenges | r | supabase_auth_admin | no | no | — | ['postgres=arwdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |
| auth | webauthn_credentials | r | supabase_auth_admin | no | no | — | ['postgres=arwdDxtm/supabase_auth_admin', 'supabase_auth_admin=arwdDxtm/supabase_auth_admin', 'dashboard_user=arwdDxtm/supabase_auth_admin'] | no | no | no | no | — |

#### codebi

| schema | relation | kind | owner | rls | force_rls | estimated_rows | acl | runtime_select | runtime_insert | runtime_update | runtime_delete | options |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| codebi | alembic_version | r | postgres | no | no | — | — | no | no | no | no | — |
| codebi | audit_events | r | postgres | yes | no | — | ['postgres=arwdDxtm/postgres', 'codebi_app=a/postgres'] | no | yes | no | no | — |
| codebi | client_memberships | r | postgres | yes | no | — | ['postgres=arwdDxtm/postgres', 'codebi_app=arw/postgres'] | yes | yes | yes | no | — |
| codebi | clients | r | postgres | yes | no | — | ['postgres=arwdDxtm/postgres', 'codebi_app=arw/postgres'] | yes | yes | yes | no | — |
| codebi | countries | r | postgres | yes | no | 249 | ['postgres=arwdDxtm/postgres', 'codebi_app=r/postgres'] | yes | no | no | no | — |
| codebi | industries | r | postgres | yes | no | — | ['postgres=arwdDxtm/postgres', 'codebi_app=r/postgres'] | yes | no | no | no | — |
| codebi | login_events | r | postgres | yes | no | — | ['postgres=arwdDxtm/postgres', 'codebi_app=a/postgres'] | no | yes | no | no | — |
| codebi | public_inquiries | r | postgres | yes | no | — | ['postgres=arwdDxtm/postgres', 'codebi_app=ar/postgres'] | yes | yes | no | no | — |
| codebi | roles | r | postgres | yes | no | — | ['postgres=arwdDxtm/postgres', 'codebi_app=r/postgres'] | yes | no | no | no | — |
| codebi | sow_estimates | r | postgres | yes | no | — | ['postgres=arwdDxtm/postgres', 'codebi_app=arw/postgres'] | yes | yes | yes | no | — |
| codebi | sow_events | r | postgres | yes | no | — | ['postgres=arwdDxtm/postgres', 'codebi_app=ar/postgres'] | yes | yes | no | no | — |
| codebi | sow_messages | r | postgres | yes | no | — | ['postgres=arwdDxtm/postgres', 'codebi_app=ar/postgres'] | yes | yes | no | no | — |
| codebi | sow_tools | r | postgres | yes | no | — | ['postgres=arwdDxtm/postgres', 'codebi_app=ard/postgres'] | yes | yes | no | yes | — |
| codebi | sow_types | r | postgres | yes | no | — | ['postgres=arwdDxtm/postgres', 'codebi_app=r/postgres'] | yes | no | no | no | — |
| codebi | state_regions | r | postgres | yes | no | 5046 | ['postgres=arwdDxtm/postgres', 'codebi_app=r/postgres'] | yes | no | no | no | — |
| codebi | statements_of_work | r | postgres | yes | no | — | ['postgres=arwdDxtm/postgres', 'codebi_app=arw/postgres'] | yes | yes | yes | no | — |
| codebi | test | r | postgres | yes | no | — | — | no | no | no | no | — |
| codebi | time_entries | r | postgres | yes | no | — | ['postgres=arwdDxtm/postgres', 'codebi_app=ar/postgres'] | yes | yes | no | no | — |
| codebi | timezones | r | postgres | yes | no | 598 | ['postgres=arwdDxtm/postgres', 'codebi_app=r/postgres'] | yes | no | no | no | — |
| codebi | tools | r | postgres | yes | no | — | ['postgres=arwdDxtm/postgres', 'codebi_app=r/postgres'] | yes | no | no | no | — |
| codebi | user_invitations | r | postgres | yes | no | — | ['postgres=arwdDxtm/postgres'] | no | no | no | no | — |
| codebi | user_roles | r | postgres | yes | no | — | ['postgres=arwdDxtm/postgres', 'codebi_app=ar/postgres'] | yes | yes | no | no | — |
| codebi | users | r | postgres | yes | no | — | ['postgres=arwdDxtm/postgres', 'codebi_app=arw/postgres'] | yes | yes | yes | no | — |

#### extensions

| schema | relation | kind | owner | rls | force_rls | estimated_rows | acl | runtime_select | runtime_insert | runtime_update | runtime_delete | options |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| extensions | pg_stat_statements | v | postgres | no | no | — | ['postgres=a*r*w*d*D*x*t*m*/postgres', '=r/postgres', 'dashboard_user=arwdDxtm/postgres'] | yes | no | no | no | — |
| extensions | pg_stat_statements_info | v | postgres | no | no | — | ['postgres=a*r*w*d*D*x*t*m*/postgres', '=r/postgres', 'dashboard_user=arwdDxtm/postgres'] | yes | no | no | no | — |

#### history

| schema | relation | kind | owner | rls | force_rls | estimated_rows | acl | runtime_select | runtime_insert | runtime_update | runtime_delete | options |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| history | knowledge_entries | r | postgres | yes | no | — | — | no | no | no | no | — |

#### realtime

| schema | relation | kind | owner | rls | force_rls | estimated_rows | acl | runtime_select | runtime_insert | runtime_update | runtime_delete | options |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| realtime | messages | p | supabase_realtime_admin | yes | no | — | ['supabase_realtime_admin=arwdDxtm/supabase_realtime_admin', 'postgres=a*r*wdDxtm/supabase_realtime_admin', 'dashboard_user=arwdDxtm/supabase_realtime_admin', 'anon=arw/supabase_realtime_admin', 'authenticated=arw/supabase_realtime_admin', 'service_role=arw/supabase_realtime_admin'] | no | no | no | no | — |
| realtime | schema_migrations | r | supabase_admin | no | no | 73 | ['supabase_admin=arwdDxtm/supabase_admin'] | no | no | no | no | — |
| realtime | subscription | r | supabase_realtime_admin | no | no | — | ['supabase_realtime_admin=arwdDxtm/supabase_realtime_admin', 'postgres=arwdDxtm/supabase_realtime_admin', 'dashboard_user=arwdDxtm/supabase_realtime_admin', 'anon=r/supabase_realtime_admin', 'authenticated=r/supabase_realtime_admin', 'service_role=r/supabase_realtime_admin'] | no | no | no | no | — |

#### storage

| schema | relation | kind | owner | rls | force_rls | estimated_rows | acl | runtime_select | runtime_insert | runtime_update | runtime_delete | options |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| storage | bucket_lifecycle_states | r | supabase_storage_admin | yes | no | — | ['supabase_storage_admin=arwdDxtm/supabase_storage_admin', 'service_role=arwdDxtm/supabase_storage_admin', 'postgres=r/supabase_storage_admin'] | no | no | no | no | — |
| storage | buckets | r | supabase_storage_admin | yes | no | — | ['supabase_storage_admin=a*r*w*d*D*x*t*m*/supabase_storage_admin', 'service_role=arwdDxtm/supabase_storage_admin', 'authenticated=arwd/supabase_storage_admin', 'anon=arwd/supabase_storage_admin', 'postgres=a*r*w*d*D*x*t*m*/supabase_storage_admin'] | no | no | no | no | — |
| storage | buckets_analytics | r | supabase_storage_admin | yes | no | — | ['supabase_storage_admin=arwdDxtm/supabase_storage_admin', 'service_role=arwdDxtm/supabase_storage_admin', 'authenticated=arwd/supabase_storage_admin', 'anon=arwd/supabase_storage_admin'] | no | no | no | no | — |
| storage | buckets_vectors | r | supabase_storage_admin | yes | no | — | ['supabase_storage_admin=arwdDxtm/supabase_storage_admin', 'service_role=r/supabase_storage_admin', 'anon=r/supabase_storage_admin', 'authenticated=r/supabase_storage_admin'] | no | no | no | no | — |
| storage | lifecycle_abandoned_artifacts | r | supabase_storage_admin | yes | no | — | ['supabase_storage_admin=arwdDxtm/supabase_storage_admin', 'service_role=arwdDxtm/supabase_storage_admin', 'postgres=r/supabase_storage_admin'] | no | no | no | no | — |
| storage | migrations | r | supabase_storage_admin | yes | no | 61 | — | no | no | no | no | — |
| storage | objects | r | supabase_storage_admin | yes | no | 11 | ['supabase_storage_admin=a*r*w*d*D*x*t*m*/supabase_storage_admin', 'service_role=arwdDxtm/supabase_storage_admin', 'authenticated=arwd/supabase_storage_admin', 'anon=arwd/supabase_storage_admin', 'postgres=a*r*w*d*D*x*t*m*/supabase_storage_admin'] | no | no | no | no | — |
| storage | s3_multipart_uploads | r | supabase_storage_admin | yes | no | — | ['supabase_storage_admin=arwdDxtm/supabase_storage_admin', 'service_role=arwdDxtm/supabase_storage_admin', 'authenticated=r/supabase_storage_admin', 'anon=r/supabase_storage_admin'] | no | no | no | no | — |
| storage | s3_multipart_uploads_parts | r | supabase_storage_admin | yes | no | — | ['supabase_storage_admin=arwdDxtm/supabase_storage_admin', 'service_role=arwdDxtm/supabase_storage_admin', 'authenticated=r/supabase_storage_admin', 'anon=r/supabase_storage_admin'] | no | no | no | no | — |
| storage | vector_indexes | r | supabase_storage_admin | yes | no | — | ['supabase_storage_admin=arwdDxtm/supabase_storage_admin', 'service_role=r/supabase_storage_admin', 'anon=r/supabase_storage_admin', 'authenticated=r/supabase_storage_admin'] | no | no | no | no | — |

#### vault

| schema | relation | kind | owner | rls | force_rls | estimated_rows | acl | runtime_select | runtime_insert | runtime_update | runtime_delete | options |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| vault | decrypted_secrets | v | supabase_admin | no | no | — | ['supabase_admin=arwdDxtm/supabase_admin', 'postgres=r*d*D*x*/supabase_admin', 'service_role=rd/supabase_admin'] | no | no | no | no | — |
| vault | secrets | r | supabase_admin | no | no | — | ['supabase_admin=arwdDxtm/supabase_admin', 'postgres=r*d*D*x*/supabase_admin', 'service_role=rd/supabase_admin'] | no | no | no | no | — |


### Columns

#### auth

| schema | relation | column_name | sql_type | not_null | default_or_generated_expression | identity | generated | acl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auth | audit_log_entries | instance_id | uuid | no | — | — | — | — |
| auth | audit_log_entries | id | uuid | yes | — | — | — | — |
| auth | audit_log_entries | payload | json | no | — | — | — | — |
| auth | audit_log_entries | created_at | timestamp with time zone | no | — | — | — | — |
| auth | audit_log_entries | ip_address | character varying(64) | yes | ''::character varying | — | — | — |
| auth | custom_oauth_providers | id | uuid | yes | gen_random_uuid() | — | — | — |
| auth | custom_oauth_providers | provider_type | text | yes | — | — | — | — |
| auth | custom_oauth_providers | identifier | text | yes | — | — | — | — |
| auth | custom_oauth_providers | name | text | yes | — | — | — | — |
| auth | custom_oauth_providers | client_id | text | yes | — | — | — | — |
| auth | custom_oauth_providers | client_secret | text | yes | — | — | — | — |
| auth | custom_oauth_providers | acceptable_client_ids | text[] | yes | '{}'::text[] | — | — | — |
| auth | custom_oauth_providers | scopes | text[] | yes | '{}'::text[] | — | — | — |
| auth | custom_oauth_providers | pkce_enabled | boolean | yes | true | — | — | — |
| auth | custom_oauth_providers | attribute_mapping | jsonb | yes | '{}'::jsonb | — | — | — |
| auth | custom_oauth_providers | authorization_params | jsonb | yes | '{}'::jsonb | — | — | — |
| auth | custom_oauth_providers | enabled | boolean | yes | true | — | — | — |
| auth | custom_oauth_providers | email_optional | boolean | yes | false | — | — | — |
| auth | custom_oauth_providers | issuer | text | no | — | — | — | — |
| auth | custom_oauth_providers | discovery_url | text | no | — | — | — | — |
| auth | custom_oauth_providers | skip_nonce_check | boolean | yes | false | — | — | — |
| auth | custom_oauth_providers | cached_discovery | jsonb | no | — | — | — | — |
| auth | custom_oauth_providers | discovery_cached_at | timestamp with time zone | no | — | — | — | — |
| auth | custom_oauth_providers | authorization_url | text | no | — | — | — | — |
| auth | custom_oauth_providers | token_url | text | no | — | — | — | — |
| auth | custom_oauth_providers | userinfo_url | text | no | — | — | — | — |
| auth | custom_oauth_providers | jwks_uri | text | no | — | — | — | — |
| auth | custom_oauth_providers | created_at | timestamp with time zone | yes | now() | — | — | — |
| auth | custom_oauth_providers | updated_at | timestamp with time zone | yes | now() | — | — | — |
| auth | custom_oauth_providers | custom_claims_allowlist | text[] | yes | '{}'::text[] | — | — | — |
| auth | flow_state | id | uuid | yes | — | — | — | — |
| auth | flow_state | user_id | uuid | no | — | — | — | — |
| auth | flow_state | auth_code | text | no | — | — | — | — |
| auth | flow_state | code_challenge_method | auth.code_challenge_method | no | — | — | — | — |
| auth | flow_state | code_challenge | text | no | — | — | — | — |
| auth | flow_state | provider_type | text | yes | — | — | — | — |
| auth | flow_state | provider_access_token | text | no | — | — | — | — |
| auth | flow_state | provider_refresh_token | text | no | — | — | — | — |
| auth | flow_state | created_at | timestamp with time zone | no | — | — | — | — |
| auth | flow_state | updated_at | timestamp with time zone | no | — | — | — | — |
| auth | flow_state | authentication_method | text | yes | — | — | — | — |
| auth | flow_state | auth_code_issued_at | timestamp with time zone | no | — | — | — | — |
| auth | flow_state | invite_token | text | no | — | — | — | — |
| auth | flow_state | referrer | text | no | — | — | — | — |
| auth | flow_state | oauth_client_state_id | uuid | no | — | — | — | — |
| auth | flow_state | linking_target_id | uuid | no | — | — | — | — |
| auth | flow_state | email_optional | boolean | yes | false | — | — | — |
| auth | identities | provider_id | text | yes | — | — | — | — |
| auth | identities | user_id | uuid | yes | — | — | — | — |
| auth | identities | identity_data | jsonb | yes | — | — | — | — |
| auth | identities | provider | text | yes | — | — | — | — |
| auth | identities | last_sign_in_at | timestamp with time zone | no | — | — | — | — |
| auth | identities | created_at | timestamp with time zone | no | — | — | — | — |
| auth | identities | updated_at | timestamp with time zone | no | — | — | — | — |
| auth | identities | email | text | no | lower((identity_data ->> 'email'::text)) | — | s | — |
| auth | identities | id | uuid | yes | gen_random_uuid() | — | — | — |
| auth | instances | id | uuid | yes | — | — | — | — |
| auth | instances | uuid | uuid | no | — | — | — | — |
| auth | instances | raw_base_config | text | no | — | — | — | — |
| auth | instances | created_at | timestamp with time zone | no | — | — | — | — |
| auth | instances | updated_at | timestamp with time zone | no | — | — | — | — |
| auth | mfa_amr_claims | session_id | uuid | yes | — | — | — | — |
| auth | mfa_amr_claims | created_at | timestamp with time zone | yes | — | — | — | — |
| auth | mfa_amr_claims | updated_at | timestamp with time zone | yes | — | — | — | — |
| auth | mfa_amr_claims | authentication_method | text | yes | — | — | — | — |
| auth | mfa_amr_claims | id | uuid | yes | — | — | — | — |
| auth | mfa_challenges | id | uuid | yes | — | — | — | — |
| auth | mfa_challenges | factor_id | uuid | yes | — | — | — | — |
| auth | mfa_challenges | created_at | timestamp with time zone | yes | — | — | — | — |
| auth | mfa_challenges | verified_at | timestamp with time zone | no | — | — | — | — |
| auth | mfa_challenges | ip_address | inet | yes | — | — | — | — |
| auth | mfa_challenges | otp_code | text | no | — | — | — | — |
| auth | mfa_challenges | web_authn_session_data | jsonb | no | — | — | — | — |
| auth | mfa_factors | id | uuid | yes | — | — | — | — |
| auth | mfa_factors | user_id | uuid | yes | — | — | — | — |
| auth | mfa_factors | friendly_name | text | no | — | — | — | — |
| auth | mfa_factors | factor_type | auth.factor_type | yes | — | — | — | — |
| auth | mfa_factors | status | auth.factor_status | yes | — | — | — | — |
| auth | mfa_factors | created_at | timestamp with time zone | yes | — | — | — | — |
| auth | mfa_factors | updated_at | timestamp with time zone | yes | — | — | — | — |
| auth | mfa_factors | secret | text | no | — | — | — | — |
| auth | mfa_factors | phone | text | no | — | — | — | — |
| auth | mfa_factors | last_challenged_at | timestamp with time zone | no | — | — | — | — |
| auth | mfa_factors | web_authn_credential | jsonb | no | — | — | — | — |
| auth | mfa_factors | web_authn_aaguid | uuid | no | — | — | — | — |
| auth | mfa_factors | last_webauthn_challenge_data | jsonb | no | — | — | — | — |
| auth | mfa_recovery_code_sets | id | uuid | yes | — | — | — | — |
| auth | mfa_recovery_code_sets | user_id | uuid | yes | — | — | — | — |
| auth | mfa_recovery_code_sets | mfa_factor_id | uuid | yes | — | — | — | — |
| auth | mfa_recovery_code_sets | failed_verification_count | integer | yes | 0 | — | — | — |
| auth | mfa_recovery_code_sets | verification_locked_until | timestamp with time zone | no | — | — | — | — |
| auth | mfa_recovery_code_sets | created_at | timestamp with time zone | yes | now() | — | — | — |
| auth | mfa_recovery_code_sets | updated_at | timestamp with time zone | yes | now() | — | — | — |
| auth | mfa_recovery_codes | id | uuid | yes | — | — | — | — |
| auth | mfa_recovery_codes | mfa_recovery_code_set_id | uuid | yes | — | — | — | — |
| auth | mfa_recovery_codes | code_hash | text | yes | — | — | — | — |
| auth | mfa_recovery_codes | consumed_at | timestamp with time zone | no | — | — | — | — |
| auth | mfa_recovery_codes | created_at | timestamp with time zone | yes | now() | — | — | — |
| auth | oauth_authorizations | id | uuid | yes | — | — | — | — |
| auth | oauth_authorizations | authorization_id | text | yes | — | — | — | — |
| auth | oauth_authorizations | client_id | uuid | yes | — | — | — | — |
| auth | oauth_authorizations | user_id | uuid | no | — | — | — | — |
| auth | oauth_authorizations | redirect_uri | text | yes | — | — | — | — |
| auth | oauth_authorizations | scope | text | yes | — | — | — | — |
| auth | oauth_authorizations | state | text | no | — | — | — | — |
| auth | oauth_authorizations | resource | text | no | — | — | — | — |
| auth | oauth_authorizations | code_challenge | text | no | — | — | — | — |
| auth | oauth_authorizations | code_challenge_method | auth.code_challenge_method | no | — | — | — | — |
| auth | oauth_authorizations | response_type | auth.oauth_response_type | yes | 'code'::auth.oauth_response_type | — | — | — |
| auth | oauth_authorizations | status | auth.oauth_authorization_status | yes | 'pending'::auth.oauth_authorization_status | — | — | — |
| auth | oauth_authorizations | authorization_code | text | no | — | — | — | — |
| auth | oauth_authorizations | created_at | timestamp with time zone | yes | now() | — | — | — |
| auth | oauth_authorizations | expires_at | timestamp with time zone | yes | (now() + '00:03:00'::interval) | — | — | — |
| auth | oauth_authorizations | approved_at | timestamp with time zone | no | — | — | — | — |
| auth | oauth_authorizations | nonce | text | no | — | — | — | — |
| auth | oauth_client_states | id | uuid | yes | — | — | — | — |
| auth | oauth_client_states | provider_type | text | yes | — | — | — | — |
| auth | oauth_client_states | code_verifier | text | no | — | — | — | — |
| auth | oauth_client_states | created_at | timestamp with time zone | yes | — | — | — | — |
| auth | oauth_clients | id | uuid | yes | — | — | — | — |
| auth | oauth_clients | client_secret_hash | text | no | — | — | — | — |
| auth | oauth_clients | registration_type | auth.oauth_registration_type | yes | — | — | — | — |
| auth | oauth_clients | redirect_uris | text | yes | — | — | — | — |
| auth | oauth_clients | grant_types | text | yes | — | — | — | — |
| auth | oauth_clients | client_name | text | no | — | — | — | — |
| auth | oauth_clients | client_uri | text | no | — | — | — | — |
| auth | oauth_clients | logo_uri | text | no | — | — | — | — |
| auth | oauth_clients | created_at | timestamp with time zone | yes | now() | — | — | — |
| auth | oauth_clients | updated_at | timestamp with time zone | yes | now() | — | — | — |
| auth | oauth_clients | deleted_at | timestamp with time zone | no | — | — | — | — |
| auth | oauth_clients | client_type | auth.oauth_client_type | yes | 'confidential'::auth.oauth_client_type | — | — | — |
| auth | oauth_clients | token_endpoint_auth_method | text | yes | — | — | — | — |
| auth | oauth_consents | id | uuid | yes | — | — | — | — |
| auth | oauth_consents | user_id | uuid | yes | — | — | — | — |
| auth | oauth_consents | client_id | uuid | yes | — | — | — | — |
| auth | oauth_consents | scopes | text | yes | — | — | — | — |
| auth | oauth_consents | granted_at | timestamp with time zone | yes | now() | — | — | — |
| auth | oauth_consents | revoked_at | timestamp with time zone | no | — | — | — | — |
| auth | one_time_tokens | id | uuid | yes | — | — | — | — |
| auth | one_time_tokens | user_id | uuid | yes | — | — | — | — |
| auth | one_time_tokens | token_type | auth.one_time_token_type | yes | — | — | — | — |
| auth | one_time_tokens | token_hash | text | yes | — | — | — | — |
| auth | one_time_tokens | relates_to | text | yes | — | — | — | — |
| auth | one_time_tokens | created_at | timestamp without time zone | yes | now() | — | — | — |
| auth | one_time_tokens | updated_at | timestamp without time zone | yes | now() | — | — | — |
| auth | one_time_tokens | expires_at | timestamp with time zone | no | — | — | — | — |
| auth | one_time_tokens | link_token_hash | text | no | — | — | — | — |
| auth | refresh_tokens | instance_id | uuid | no | — | — | — | — |
| auth | refresh_tokens | id | bigint | yes | nextval('auth.refresh_tokens_id_seq'::regclass) | — | — | — |
| auth | refresh_tokens | token | character varying(255) | no | — | — | — | — |
| auth | refresh_tokens | user_id | character varying(255) | no | — | — | — | — |
| auth | refresh_tokens | revoked | boolean | no | — | — | — | — |
| auth | refresh_tokens | created_at | timestamp with time zone | no | — | — | — | — |
| auth | refresh_tokens | updated_at | timestamp with time zone | no | — | — | — | — |
| auth | refresh_tokens | parent | character varying(255) | no | — | — | — | — |
| auth | refresh_tokens | session_id | uuid | no | — | — | — | — |
| auth | saml_providers | id | uuid | yes | — | — | — | — |
| auth | saml_providers | sso_provider_id | uuid | yes | — | — | — | — |
| auth | saml_providers | entity_id | text | yes | — | — | — | — |
| auth | saml_providers | metadata_xml | text | yes | — | — | — | — |
| auth | saml_providers | metadata_url | text | no | — | — | — | — |
| auth | saml_providers | attribute_mapping | jsonb | no | — | — | — | — |
| auth | saml_providers | created_at | timestamp with time zone | no | — | — | — | — |
| auth | saml_providers | updated_at | timestamp with time zone | no | — | — | — | — |
| auth | saml_providers | name_id_format | text | no | — | — | — | — |
| auth | saml_providers | dedicated_scope | boolean | no | — | — | — | — |
| auth | saml_relay_states | id | uuid | yes | — | — | — | — |
| auth | saml_relay_states | sso_provider_id | uuid | yes | — | — | — | — |
| auth | saml_relay_states | request_id | text | yes | — | — | — | — |
| auth | saml_relay_states | for_email | text | no | — | — | — | — |
| auth | saml_relay_states | redirect_to | text | no | — | — | — | — |
| auth | saml_relay_states | created_at | timestamp with time zone | no | — | — | — | — |
| auth | saml_relay_states | updated_at | timestamp with time zone | no | — | — | — | — |
| auth | saml_relay_states | flow_state_id | uuid | no | — | — | — | — |
| auth | schema_migrations | version | character varying(255) | yes | — | — | — | — |
| auth | scim_tokens | id | uuid | yes | — | — | — | — |
| auth | scim_tokens | sso_provider_id | uuid | yes | — | — | — | — |
| auth | scim_tokens | token_hash | text | yes | — | — | — | — |
| auth | scim_tokens | prefix | text | yes | — | — | — | — |
| auth | scim_tokens | created_at | timestamp with time zone | yes | now() | — | — | — |
| auth | scim_tokens | expires_at | timestamp with time zone | no | — | — | — | — |
| auth | scim_tokens | revoked_at | timestamp with time zone | no | — | — | — | — |
| auth | scim_tokens | last_used_at | timestamp with time zone | no | — | — | — | — |
| auth | scim_users | id | uuid | yes | — | — | — | — |
| auth | scim_users | sso_provider_id | uuid | yes | — | — | — | — |
| auth | scim_users | user_id | uuid | no | — | — | — | — |
| auth | scim_users | resource | jsonb | yes | — | — | — | — |
| auth | scim_users | user_name | text | yes | lower((resource ->> 'userName'::text)) | — | s | — |
| auth | scim_users | external_id | text | no | (resource ->> 'externalId'::text) | — | s | — |
| auth | scim_users | active | boolean | yes | COALESCE(((resource ->> 'active'::text))::boolean, true) | — | s | — |
| auth | scim_users | created_at | timestamp with time zone | yes | now() | — | — | — |
| auth | scim_users | updated_at | timestamp with time zone | yes | now() | — | — | — |
| auth | scim_users | deleted_at | timestamp with time zone | no | — | — | — | — |
| auth | sessions | id | uuid | yes | — | — | — | — |
| auth | sessions | user_id | uuid | yes | — | — | — | — |
| auth | sessions | created_at | timestamp with time zone | no | — | — | — | — |
| auth | sessions | updated_at | timestamp with time zone | no | — | — | — | — |
| auth | sessions | factor_id | uuid | no | — | — | — | — |
| auth | sessions | aal | auth.aal_level | no | — | — | — | — |
| auth | sessions | not_after | timestamp with time zone | no | — | — | — | — |
| auth | sessions | refreshed_at | timestamp without time zone | no | — | — | — | — |
| auth | sessions | user_agent | text | no | — | — | — | — |
| auth | sessions | ip | inet | no | — | — | — | — |
| auth | sessions | tag | text | no | — | — | — | — |
| auth | sessions | oauth_client_id | uuid | no | — | — | — | — |
| auth | sessions | refresh_token_hmac_key | text | no | — | — | — | — |
| auth | sessions | refresh_token_counter | bigint | no | — | — | — | — |
| auth | sessions | scopes | text | no | — | — | — | — |
| auth | sso_domains | id | uuid | yes | — | — | — | — |
| auth | sso_domains | sso_provider_id | uuid | yes | — | — | — | — |
| auth | sso_domains | domain | text | yes | — | — | — | — |
| auth | sso_domains | created_at | timestamp with time zone | no | — | — | — | — |
| auth | sso_domains | updated_at | timestamp with time zone | no | — | — | — | — |
| auth | sso_providers | id | uuid | yes | — | — | — | — |
| auth | sso_providers | resource_id | text | no | — | — | — | — |
| auth | sso_providers | created_at | timestamp with time zone | no | — | — | — | — |
| auth | sso_providers | updated_at | timestamp with time zone | no | — | — | — | — |
| auth | sso_providers | disabled | boolean | no | — | — | — | — |
| auth | users | instance_id | uuid | no | — | — | — | — |
| auth | users | id | uuid | yes | — | — | — | — |
| auth | users | aud | character varying(255) | no | — | — | — | — |
| auth | users | role | character varying(255) | no | — | — | — | — |
| auth | users | email | character varying(255) | no | — | — | — | — |
| auth | users | encrypted_password | character varying(255) | no | — | — | — | — |
| auth | users | email_confirmed_at | timestamp with time zone | no | — | — | — | — |
| auth | users | invited_at | timestamp with time zone | no | — | — | — | — |
| auth | users | confirmation_token | character varying(255) | no | — | — | — | — |
| auth | users | confirmation_sent_at | timestamp with time zone | no | — | — | — | — |
| auth | users | recovery_token | character varying(255) | no | — | — | — | — |
| auth | users | recovery_sent_at | timestamp with time zone | no | — | — | — | — |
| auth | users | email_change_token_new | character varying(255) | no | — | — | — | — |
| auth | users | email_change | character varying(255) | no | — | — | — | — |
| auth | users | email_change_sent_at | timestamp with time zone | no | — | — | — | — |
| auth | users | last_sign_in_at | timestamp with time zone | no | — | — | — | — |
| auth | users | raw_app_meta_data | jsonb | no | — | — | — | — |
| auth | users | raw_user_meta_data | jsonb | no | — | — | — | — |
| auth | users | is_super_admin | boolean | no | — | — | — | — |
| auth | users | created_at | timestamp with time zone | no | — | — | — | — |
| auth | users | updated_at | timestamp with time zone | no | — | — | — | — |
| auth | users | phone | text | no | NULL::character varying | — | — | — |
| auth | users | phone_confirmed_at | timestamp with time zone | no | — | — | — | — |
| auth | users | phone_change | text | no | ''::character varying | — | — | — |
| auth | users | phone_change_token | character varying(255) | no | ''::character varying | — | — | — |
| auth | users | phone_change_sent_at | timestamp with time zone | no | — | — | — | — |
| auth | users | confirmed_at | timestamp with time zone | no | LEAST(email_confirmed_at, phone_confirmed_at) | — | s | — |
| auth | users | email_change_token_current | character varying(255) | no | ''::character varying | — | — | — |
| auth | users | email_change_confirm_status | smallint | no | 0 | — | — | — |
| auth | users | banned_until | timestamp with time zone | no | — | — | — | — |
| auth | users | reauthentication_token | character varying(255) | no | ''::character varying | — | — | — |
| auth | users | reauthentication_sent_at | timestamp with time zone | no | — | — | — | — |
| auth | users | is_sso_user | boolean | yes | false | — | — | — |
| auth | users | deleted_at | timestamp with time zone | no | — | — | — | — |
| auth | users | is_anonymous | boolean | yes | false | — | — | — |
| auth | webauthn_challenges | id | uuid | yes | gen_random_uuid() | — | — | — |
| auth | webauthn_challenges | user_id | uuid | no | — | — | — | — |
| auth | webauthn_challenges | challenge_type | text | yes | — | — | — | — |
| auth | webauthn_challenges | session_data | jsonb | yes | — | — | — | — |
| auth | webauthn_challenges | created_at | timestamp with time zone | yes | now() | — | — | — |
| auth | webauthn_challenges | expires_at | timestamp with time zone | yes | — | — | — | — |
| auth | webauthn_credentials | id | uuid | yes | gen_random_uuid() | — | — | — |
| auth | webauthn_credentials | user_id | uuid | yes | — | — | — | — |
| auth | webauthn_credentials | credential_id | bytea | yes | — | — | — | — |
| auth | webauthn_credentials | public_key | bytea | yes | — | — | — | — |
| auth | webauthn_credentials | attestation_type | text | yes | ''::text | — | — | — |
| auth | webauthn_credentials | aaguid | uuid | no | — | — | — | — |
| auth | webauthn_credentials | sign_count | bigint | yes | 0 | — | — | — |
| auth | webauthn_credentials | transports | jsonb | yes | '[]'::jsonb | — | — | — |
| auth | webauthn_credentials | backup_eligible | boolean | yes | false | — | — | — |
| auth | webauthn_credentials | backed_up | boolean | yes | false | — | — | — |
| auth | webauthn_credentials | friendly_name | text | yes | ''::text | — | — | — |
| auth | webauthn_credentials | created_at | timestamp with time zone | yes | now() | — | — | — |
| auth | webauthn_credentials | updated_at | timestamp with time zone | yes | now() | — | — | — |
| auth | webauthn_credentials | last_used_at | timestamp with time zone | no | — | — | — | — |

#### codebi

| schema | relation | column_name | sql_type | not_null | default_or_generated_expression | identity | generated | acl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| codebi | alembic_version | version_num | character varying(32) | yes | — | — | — | — |
| codebi | audit_events | id | uuid | yes | gen_random_uuid() | — | — | — |
| codebi | audit_events | client_id | uuid | no | — | — | — | — |
| codebi | audit_events | actor_id | uuid | yes | — | — | — | — |
| codebi | audit_events | action | text | yes | — | — | — | — |
| codebi | audit_events | entity_type | text | yes | — | — | — | — |
| codebi | audit_events | entity_id | uuid | no | — | — | — | — |
| codebi | audit_events | changes | jsonb | yes | '{}'::jsonb | — | — | — |
| codebi | audit_events | occurred_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | client_memberships | id | uuid | yes | gen_random_uuid() | — | — | — |
| codebi | client_memberships | client_id | uuid | yes | — | — | — | — |
| codebi | client_memberships | user_id | uuid | yes | — | — | — | — |
| codebi | client_memberships | role_code | text | yes | 'client_user'::text | — | — | — |
| codebi | client_memberships | role_scope | text | yes | 'client'::text | — | — | — |
| codebi | client_memberships | can_create_sows | boolean | yes | false | — | — | — |
| codebi | client_memberships | is_active | boolean | yes | true | — | — | — |
| codebi | client_memberships | granted_by | uuid | no | — | — | — | — |
| codebi | client_memberships | created_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | client_memberships | updated_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | clients | id | uuid | yes | gen_random_uuid() | — | — | — |
| codebi | clients | client_number | bigint | yes | — | a | — | — |
| codebi | clients | client_code | text | no | ('CLI-'::text \|\| lpad((client_number)::text, GREATEST(4, length((client_number)::text)), '0'::text)) | — | s | — |
| codebi | clients | name | text | yes | — | — | — | — |
| codebi | clients | legal_name | text | no | — | — | — | — |
| codebi | clients | industry_id | uuid | no | — | — | — | — |
| codebi | clients | website | text | no | — | — | — | — |
| codebi | clients | email | text | no | — | — | — | — |
| codebi | clients | phone | text | no | — | — | — | — |
| codebi | clients | contact_name | text | no | — | — | — | — |
| codebi | clients | contact_email | text | no | — | — | — | — |
| codebi | clients | contact_phone | text | no | — | — | — | — |
| codebi | clients | address_line_1 | text | no | — | — | — | — |
| codebi | clients | address_line_2 | text | no | — | — | — | — |
| codebi | clients | city | text | no | — | — | — | — |
| codebi | clients | state_region | text | no | — | — | — | — |
| codebi | clients | postal_code | text | no | — | — | — | — |
| codebi | clients | country_code | text | no | — | — | — | — |
| codebi | clients | timezone | text | yes | 'UTC'::text | — | — | — |
| codebi | clients | is_active | boolean | yes | true | — | — | — |
| codebi | clients | created_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | clients | updated_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | clients | created_by | uuid | no | — | — | — | — |
| codebi | clients | updated_by | uuid | no | — | — | — | — |
| codebi | clients | state_region_code | text | no | — | — | — | — |
| codebi | clients | registration_inquiry | text | no | — | — | — | — |
| codebi | clients | logo_path | text | no | — | — | — | — |
| codebi | clients | logo_background | text | yes | 'light'::text | — | — | — |
| codebi | countries | code | text | yes | — | — | — | — |
| codebi | countries | name | text | yes | — | — | — | — |
| codebi | countries | is_active | boolean | yes | true | — | — | — |
| codebi | industries | id | uuid | yes | gen_random_uuid() | — | — | — |
| codebi | industries | name | text | yes | — | — | — | — |
| codebi | industries | description | text | no | — | — | — | — |
| codebi | industries | is_active | boolean | yes | true | — | — | — |
| codebi | industries | created_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | industries | updated_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | login_events | id | uuid | yes | gen_random_uuid() | — | — | — |
| codebi | login_events | user_id | uuid | no | — | — | — | — |
| codebi | login_events | provider | text | yes | — | — | — | — |
| codebi | login_events | outcome | text | yes | — | — | — | — |
| codebi | login_events | reason_code | text | no | — | — | — | — |
| codebi | login_events | ip_address | inet | no | — | — | — | — |
| codebi | login_events | user_agent | text | no | — | — | — | — |
| codebi | login_events | occurred_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | public_inquiries | id | uuid | yes | gen_random_uuid() | — | — | — |
| codebi | public_inquiries | name | text | yes | — | — | — | — |
| codebi | public_inquiries | email | text | yes | — | — | — | — |
| codebi | public_inquiries | company | text | yes | — | — | — | — |
| codebi | public_inquiries | details | text | yes | — | — | — | — |
| codebi | public_inquiries | created_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | roles | code | text | yes | — | — | — | — |
| codebi | roles | name | text | yes | — | — | — | — |
| codebi | roles | description | text | no | — | — | — | — |
| codebi | roles | scope | text | yes | — | — | — | — |
| codebi | sow_estimates | id | uuid | yes | gen_random_uuid() | — | — | — |
| codebi | sow_estimates | client_id | uuid | yes | — | — | — | — |
| codebi | sow_estimates | sow_id | uuid | yes | — | — | — | — |
| codebi | sow_estimates | revision | integer | yes | — | — | — | — |
| codebi | sow_estimates | scope_snapshot | text | yes | — | — | — | — |
| codebi | sow_estimates | feasibility | text | yes | — | — | — | — |
| codebi | sow_estimates | min_hours | numeric(10,2) | no | — | — | — | — |
| codebi | sow_estimates | max_hours | numeric(10,2) | no | — | — | — | — |
| codebi | sow_estimates | assumptions | text | no | — | — | — | — |
| codebi | sow_estimates | exclusions | text | no | — | — | — | — |
| codebi | sow_estimates | notes | text | no | — | — | — | — |
| codebi | sow_estimates | status | text | yes | 'draft'::text | — | — | — |
| codebi | sow_estimates | created_by | uuid | yes | — | — | — | — |
| codebi | sow_estimates | issued_at | timestamp with time zone | no | — | — | — | — |
| codebi | sow_estimates | decided_by | uuid | no | — | — | — | — |
| codebi | sow_estimates | decided_at | timestamp with time zone | no | — | — | — | — |
| codebi | sow_estimates | created_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | sow_estimates | updated_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | sow_estimates | pricing_type | text | no | — | — | — | — |
| codebi | sow_estimates | hourly_rate | numeric(10,2) | no | — | — | — | — |
| codebi | sow_estimates | min_amount | numeric(18,2) | no | — | — | — | — |
| codebi | sow_estimates | max_amount | numeric(18,2) | no | — | — | — | — |
| codebi | sow_estimates | estimated_completion | date | no | — | — | — | — |
| codebi | sow_events | id | uuid | yes | gen_random_uuid() | — | — | — |
| codebi | sow_events | client_id | uuid | yes | — | — | — | — |
| codebi | sow_events | sow_id | uuid | yes | — | — | — | — |
| codebi | sow_events | actor_id | uuid | yes | — | — | — | — |
| codebi | sow_events | event_type | text | yes | — | — | — | — |
| codebi | sow_events | from_status | text | no | — | — | — | — |
| codebi | sow_events | to_status | text | no | — | — | — | — |
| codebi | sow_events | estimate_id | uuid | no | — | — | — | — |
| codebi | sow_events | message_id | uuid | no | — | — | — | — |
| codebi | sow_events | details | jsonb | yes | '{}'::jsonb | — | — | — |
| codebi | sow_events | occurred_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | sow_messages | id | uuid | yes | gen_random_uuid() | — | — | — |
| codebi | sow_messages | client_id | uuid | yes | — | — | — | — |
| codebi | sow_messages | sow_id | uuid | yes | — | — | — | — |
| codebi | sow_messages | author_id | uuid | yes | — | — | — | — |
| codebi | sow_messages | message_type | text | yes | 'general'::text | — | — | — |
| codebi | sow_messages | body | text | yes | — | — | — | — |
| codebi | sow_messages | created_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | sow_tools | sow_id | uuid | yes | — | — | — | — |
| codebi | sow_tools | tool_id | uuid | yes | — | — | — | — |
| codebi | sow_types | id | uuid | yes | gen_random_uuid() | — | — | — |
| codebi | sow_types | name | text | yes | — | — | — | — |
| codebi | sow_types | description | text | no | — | — | — | — |
| codebi | sow_types | is_active | boolean | yes | true | — | — | — |
| codebi | state_regions | code | text | yes | — | — | — | — |
| codebi | state_regions | country_code | text | yes | — | — | — | — |
| codebi | state_regions | name | text | yes | — | — | — | — |
| codebi | state_regions | region_type | text | yes | — | — | — | — |
| codebi | state_regions | is_active | boolean | yes | true | — | — | — |
| codebi | statements_of_work | id | uuid | yes | gen_random_uuid() | — | — | — |
| codebi | statements_of_work | sow_number | bigint | yes | — | a | — | — |
| codebi | statements_of_work | sow_code | text | no | ('SOW-'::text \|\| lpad((sow_number)::text, GREATEST(6, length((sow_number)::text)), '0'::text)) | — | s | — |
| codebi | statements_of_work | client_id | uuid | yes | — | — | — | — |
| codebi | statements_of_work | title | text | yes | — | — | — | — |
| codebi | statements_of_work | description | text | yes | — | — | — | — |
| codebi | statements_of_work | sow_type_id | uuid | yes | — | — | — | — |
| codebi | statements_of_work | priority | text | yes | 'normal'::text | — | — | — |
| codebi | statements_of_work | desired_completion_date | date | no | — | — | — | — |
| codebi | statements_of_work | status | text | yes | 'draft'::text | — | — | — |
| codebi | statements_of_work | created_by | uuid | yes | — | — | — | — |
| codebi | statements_of_work | updated_by | uuid | no | — | — | — | — |
| codebi | statements_of_work | submitted_by | uuid | no | — | — | — | — |
| codebi | statements_of_work | submitted_at | timestamp with time zone | no | — | — | — | — |
| codebi | statements_of_work | accepted_estimate_id | uuid | no | — | — | — | — |
| codebi | statements_of_work | started_at | timestamp with time zone | no | — | — | — | — |
| codebi | statements_of_work | completed_at | timestamp with time zone | no | — | — | — | — |
| codebi | statements_of_work | created_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | statements_of_work | updated_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | statements_of_work | other_tool_details | text | no | — | — | — | — |
| codebi | test | id | bigint | yes | — | d | — | — |
| codebi | test | created_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | time_entries | id | uuid | yes | gen_random_uuid() | — | — | — |
| codebi | time_entries | client_id | uuid | yes | — | — | — | — |
| codebi | time_entries | sow_id | uuid | yes | — | — | — | — |
| codebi | time_entries | worker_id | uuid | yes | — | — | — | — |
| codebi | time_entries | activity | text | yes | — | — | — | — |
| codebi | time_entries | started_at | timestamp with time zone | yes | — | — | — | — |
| codebi | time_entries | ended_at | timestamp with time zone | yes | — | — | — | — |
| codebi | time_entries | duration_minutes | numeric | no | (EXTRACT(epoch FROM (ended_at - started_at)) / 60.0) | — | s | — |
| codebi | time_entries | status | text | yes | 'draft'::text | — | — | — |
| codebi | time_entries | internal_notes | text | no | — | — | — | — |
| codebi | time_entries | void_reason | text | no | — | — | — | — |
| codebi | time_entries | created_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | time_entries | updated_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | timezones | name | text | yes | — | — | — | — |
| codebi | timezones | is_active | boolean | yes | true | — | — | — |
| codebi | tools | id | uuid | yes | gen_random_uuid() | — | — | — |
| codebi | tools | name | text | yes | — | — | — | — |
| codebi | tools | description | text | no | — | — | — | — |
| codebi | tools | is_active | boolean | yes | true | — | — | — |
| codebi | user_invitations | id | uuid | yes | gen_random_uuid() | — | — | — |
| codebi | user_invitations | client_id | uuid | yes | — | — | — | — |
| codebi | user_invitations | email | text | yes | — | — | — | — |
| codebi | user_invitations | role_code | text | yes | 'client_user'::text | — | — | — |
| codebi | user_invitations | role_scope | text | yes | 'client'::text | — | — | — |
| codebi | user_invitations | can_create_sows | boolean | yes | false | — | — | — |
| codebi | user_invitations | invited_by | uuid | yes | — | — | — | — |
| codebi | user_invitations | token_hash | text | no | — | — | — | — |
| codebi | user_invitations | expires_at | timestamp with time zone | yes | — | — | — | — |
| codebi | user_invitations | accepted_by | uuid | no | — | — | — | — |
| codebi | user_invitations | accepted_at | timestamp with time zone | no | — | — | — | — |
| codebi | user_invitations | revoked_at | timestamp with time zone | no | — | — | — | — |
| codebi | user_invitations | created_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | user_roles | user_id | uuid | yes | — | — | — | — |
| codebi | user_roles | role_code | text | yes | — | — | — | — |
| codebi | user_roles | role_scope | text | yes | 'global'::text | — | — | — |
| codebi | user_roles | granted_by | uuid | no | — | — | — | — |
| codebi | user_roles | granted_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | users | id | uuid | yes | gen_random_uuid() | — | — | — |
| codebi | users | auth_user_id | uuid | no | — | — | — | — |
| codebi | users | email | text | yes | — | — | — | — |
| codebi | users | display_name | text | yes | — | — | — | — |
| codebi | users | phone | text | no | — | — | — | — |
| codebi | users | user_type | text | yes | — | — | — | — |
| codebi | users | is_active | boolean | yes | true | — | — | — |
| codebi | users | last_login_at | timestamp with time zone | no | — | — | — | — |
| codebi | users | created_by | uuid | no | — | — | — | — |
| codebi | users | updated_by | uuid | no | — | — | — | — |
| codebi | users | created_at | timestamp with time zone | yes | now() | — | — | — |
| codebi | users | updated_at | timestamp with time zone | yes | now() | — | — | — |

#### extensions

| schema | relation | column_name | sql_type | not_null | default_or_generated_expression | identity | generated | acl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| extensions | pg_stat_statements | userid | oid | no | — | — | — | — |
| extensions | pg_stat_statements | dbid | oid | no | — | — | — | — |
| extensions | pg_stat_statements | toplevel | boolean | no | — | — | — | — |
| extensions | pg_stat_statements | queryid | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | query | text | no | — | — | — | — |
| extensions | pg_stat_statements | plans | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | total_plan_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | min_plan_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | max_plan_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | mean_plan_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | stddev_plan_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | calls | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | total_exec_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | min_exec_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | max_exec_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | mean_exec_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | stddev_exec_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | rows | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | shared_blks_hit | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | shared_blks_read | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | shared_blks_dirtied | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | shared_blks_written | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | local_blks_hit | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | local_blks_read | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | local_blks_dirtied | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | local_blks_written | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | temp_blks_read | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | temp_blks_written | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | shared_blk_read_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | shared_blk_write_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | local_blk_read_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | local_blk_write_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | temp_blk_read_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | temp_blk_write_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | wal_records | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | wal_fpi | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | wal_bytes | numeric | no | — | — | — | — |
| extensions | pg_stat_statements | jit_functions | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | jit_generation_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | jit_inlining_count | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | jit_inlining_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | jit_optimization_count | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | jit_optimization_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | jit_emission_count | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | jit_emission_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | jit_deform_count | bigint | no | — | — | — | — |
| extensions | pg_stat_statements | jit_deform_time | double precision | no | — | — | — | — |
| extensions | pg_stat_statements | stats_since | timestamp with time zone | no | — | — | — | — |
| extensions | pg_stat_statements | minmax_stats_since | timestamp with time zone | no | — | — | — | — |
| extensions | pg_stat_statements_info | dealloc | bigint | no | — | — | — | — |
| extensions | pg_stat_statements_info | stats_reset | timestamp with time zone | no | — | — | — | — |

#### history

| schema | relation | column_name | sql_type | not_null | default_or_generated_expression | identity | generated | acl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| history | knowledge_entries | id | bigint | yes | — | a | — | — |
| history | knowledge_entries | topic | text | yes | — | — | — | — |
| history | knowledge_entries | reference | text | no | — | — | — | — |
| history | knowledge_entries | source_url | text | no | — | — | — | — |
| history | knowledge_entries | language | text | no | — | — | — | — |

#### realtime

| schema | relation | column_name | sql_type | not_null | default_or_generated_expression | identity | generated | acl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| realtime | messages | topic | text | yes | — | — | — | — |
| realtime | messages | extension | text | yes | — | — | — | — |
| realtime | messages | payload | jsonb | no | — | — | — | — |
| realtime | messages | event | text | no | — | — | — | — |
| realtime | messages | private | boolean | no | false | — | — | — |
| realtime | messages | updated_at | timestamp without time zone | yes | timezone('utc'::text, clock_timestamp()) | — | — | — |
| realtime | messages | inserted_at | timestamp without time zone | yes | timezone('utc'::text, clock_timestamp()) | — | — | — |
| realtime | messages | id | uuid | yes | gen_random_uuid() | — | — | — |
| realtime | messages | binary_payload | bytea | no | — | — | — | — |
| realtime | messages | skip_broadcast | boolean | yes | false | — | — | — |
| realtime | messages | expires_at | timestamp without time zone | no | — | — | — | — |
| realtime | schema_migrations | version | bigint | yes | — | — | — | — |
| realtime | schema_migrations | inserted_at | timestamp(0) without time zone | no | — | — | — | — |
| realtime | subscription | id | bigint | yes | — | a | — | — |
| realtime | subscription | subscription_id | uuid | yes | — | — | — | — |
| realtime | subscription | entity | regclass | yes | — | — | — | — |
| realtime | subscription | filters | realtime.user_defined_filter[] | yes | '{}'::realtime.user_defined_filter[] | — | — | — |
| realtime | subscription | claims | jsonb | yes | — | — | — | — |
| realtime | subscription | claims_role | regrole | yes | realtime.to_regrole((claims ->> 'role'::text)) | — | s | — |
| realtime | subscription | created_at | timestamp without time zone | yes | timezone('utc'::text, now()) | — | — | — |
| realtime | subscription | action_filter | text | no | '*'::text | — | — | — |
| realtime | subscription | selected_columns | text[] | no | — | — | — | — |

#### storage

| schema | relation | column_name | sql_type | not_null | default_or_generated_expression | identity | generated | acl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| storage | bucket_lifecycle_states | bucket_id | text | yes | — | — | — | — |
| storage | bucket_lifecycle_states | generation | uuid | no | — | — | — | — |
| storage | bucket_lifecycle_states | next_run_at | timestamp with time zone | no | — | — | — | — |
| storage | bucket_lifecycle_states | claim_id | uuid | no | — | — | — | — |
| storage | bucket_lifecycle_states | claim_until | timestamp with time zone | no | — | — | — | — |
| storage | bucket_lifecycle_states | continuation | jsonb | no | — | — | — | — |
| storage | bucket_lifecycle_states | last_started_at | timestamp with time zone | no | — | — | — | — |
| storage | bucket_lifecycle_states | last_completed_at | timestamp with time zone | no | — | — | — | — |
| storage | bucket_lifecycle_states | last_success_at | timestamp with time zone | no | — | — | — | — |
| storage | bucket_lifecycle_states | last_result | jsonb | no | — | — | — | — |
| storage | bucket_lifecycle_states | failure_count | integer | yes | 0 | — | — | — |
| storage | bucket_lifecycle_states | last_error | jsonb | no | — | — | — | — |
| storage | bucket_lifecycle_states | updated_at | timestamp with time zone | yes | now() | — | — | — |
| storage | buckets | id | text | yes | — | — | — | — |
| storage | buckets | name | text | yes | — | — | — | — |
| storage | buckets | owner | uuid | no | — | — | — | — |
| storage | buckets | created_at | timestamp with time zone | no | now() | — | — | — |
| storage | buckets | updated_at | timestamp with time zone | no | now() | — | — | — |
| storage | buckets | public | boolean | no | false | — | — | — |
| storage | buckets | avif_autodetection | boolean | no | false | — | — | — |
| storage | buckets | file_size_limit | bigint | no | — | — | — | — |
| storage | buckets | allowed_mime_types | text[] | no | — | — | — | — |
| storage | buckets | owner_id | text | no | — | — | — | — |
| storage | buckets | type | storage.buckettype | yes | 'STANDARD'::storage.buckettype | — | — | — |
| storage | buckets | versioning_status | text | yes | 'DISABLED'::text | — | — | — |
| storage | buckets | lifecycle_configuration | jsonb | no | — | — | — | — |
| storage | buckets | lifecycle_configuration_generation | uuid | no | — | — | — | — |
| storage | buckets_analytics | name | text | yes | — | — | — | — |
| storage | buckets_analytics | type | storage.buckettype | yes | 'ANALYTICS'::storage.buckettype | — | — | — |
| storage | buckets_analytics | format | text | yes | 'ICEBERG'::text | — | — | — |
| storage | buckets_analytics | created_at | timestamp with time zone | yes | now() | — | — | — |
| storage | buckets_analytics | updated_at | timestamp with time zone | yes | now() | — | — | — |
| storage | buckets_analytics | id | uuid | yes | gen_random_uuid() | — | — | — |
| storage | buckets_analytics | deleted_at | timestamp with time zone | no | — | — | — | — |
| storage | buckets_vectors | id | text | yes | — | — | — | — |
| storage | buckets_vectors | type | storage.buckettype | yes | 'VECTOR'::storage.buckettype | — | — | — |
| storage | buckets_vectors | created_at | timestamp with time zone | yes | now() | — | — | — |
| storage | buckets_vectors | updated_at | timestamp with time zone | yes | now() | — | — | — |
| storage | lifecycle_abandoned_artifacts | bucket_id | text | yes | — | — | — | — |
| storage | lifecycle_abandoned_artifacts | key | text | yes | — | — | — | — |
| storage | lifecycle_abandoned_artifacts | name | text | yes | — | — | — | — |
| storage | lifecycle_abandoned_artifacts | version | text | no | — | — | — | — |
| storage | lifecycle_abandoned_artifacts | attempted_at | timestamp with time zone | yes | now() | — | — | — |
| storage | migrations | id | integer | yes | — | — | — | — |
| storage | migrations | name | character varying(100) | yes | — | — | — | — |
| storage | migrations | hash | character varying(40) | yes | — | — | — | — |
| storage | migrations | executed_at | timestamp without time zone | no | CURRENT_TIMESTAMP | — | — | — |
| storage | objects | id | uuid | yes | gen_random_uuid() | — | — | — |
| storage | objects | bucket_id | text | no | — | — | — | — |
| storage | objects | name | text | no | — | — | — | — |
| storage | objects | owner | uuid | no | — | — | — | — |
| storage | objects | created_at | timestamp with time zone | no | now() | — | — | — |
| storage | objects | updated_at | timestamp with time zone | no | now() | — | — | — |
| storage | objects | last_accessed_at | timestamp with time zone | no | now() | — | — | — |
| storage | objects | metadata | jsonb | no | — | — | — | — |
| storage | objects | path_tokens | text[] | no | string_to_array(name, '/'::text) | — | s | — |
| storage | objects | version | text | no | — | — | — | — |
| storage | objects | owner_id | text | no | — | — | — | — |
| storage | objects | user_metadata | jsonb | no | — | — | — | — |
| storage | objects | archived_at | timestamp with time zone | no | — | — | — | — |
| storage | objects | is_delete_marker | boolean | yes | false | — | — | — |
| storage | objects | is_versioned | boolean | yes | false | — | — | — |
| storage | s3_multipart_uploads | id | text | yes | — | — | — | — |
| storage | s3_multipart_uploads | in_progress_size | bigint | yes | 0 | — | — | — |
| storage | s3_multipart_uploads | upload_signature | text | yes | — | — | — | — |
| storage | s3_multipart_uploads | bucket_id | text | yes | — | — | — | — |
| storage | s3_multipart_uploads | key | text | yes | — | — | — | — |
| storage | s3_multipart_uploads | version | text | yes | — | — | — | — |
| storage | s3_multipart_uploads | owner_id | text | no | — | — | — | — |
| storage | s3_multipart_uploads | created_at | timestamp with time zone | yes | now() | — | — | — |
| storage | s3_multipart_uploads | user_metadata | jsonb | no | — | — | — | — |
| storage | s3_multipart_uploads | metadata | jsonb | no | — | — | — | — |
| storage | s3_multipart_uploads_parts | id | uuid | yes | gen_random_uuid() | — | — | — |
| storage | s3_multipart_uploads_parts | upload_id | text | yes | — | — | — | — |
| storage | s3_multipart_uploads_parts | size | bigint | yes | 0 | — | — | — |
| storage | s3_multipart_uploads_parts | part_number | integer | yes | — | — | — | — |
| storage | s3_multipart_uploads_parts | bucket_id | text | yes | — | — | — | — |
| storage | s3_multipart_uploads_parts | key | text | yes | — | — | — | — |
| storage | s3_multipart_uploads_parts | etag | text | yes | — | — | — | — |
| storage | s3_multipart_uploads_parts | owner_id | text | no | — | — | — | — |
| storage | s3_multipart_uploads_parts | version | text | yes | — | — | — | — |
| storage | s3_multipart_uploads_parts | created_at | timestamp with time zone | yes | now() | — | — | — |
| storage | vector_indexes | id | text | yes | gen_random_uuid() | — | — | — |
| storage | vector_indexes | name | text | yes | — | — | — | — |
| storage | vector_indexes | bucket_id | text | yes | — | — | — | — |
| storage | vector_indexes | data_type | text | yes | — | — | — | — |
| storage | vector_indexes | dimension | integer | yes | — | — | — | — |
| storage | vector_indexes | distance_metric | text | yes | — | — | — | — |
| storage | vector_indexes | metadata_configuration | jsonb | no | — | — | — | — |
| storage | vector_indexes | created_at | timestamp with time zone | yes | now() | — | — | — |
| storage | vector_indexes | updated_at | timestamp with time zone | yes | now() | — | — | — |

#### vault

| schema | relation | column_name | sql_type | not_null | default_or_generated_expression | identity | generated | acl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| vault | decrypted_secrets | id | uuid | no | — | — | — | — |
| vault | decrypted_secrets | name | text | no | — | — | — | — |
| vault | decrypted_secrets | description | text | no | — | — | — | — |
| vault | decrypted_secrets | secret | text | no | — | — | — | — |
| vault | decrypted_secrets | decrypted_secret | text | no | — | — | — | — |
| vault | decrypted_secrets | key_id | uuid | no | — | — | — | — |
| vault | decrypted_secrets | nonce | bytea | no | — | — | — | — |
| vault | decrypted_secrets | created_at | timestamp with time zone | no | — | — | — | — |
| vault | decrypted_secrets | updated_at | timestamp with time zone | no | — | — | — | — |
| vault | secrets | id | uuid | yes | gen_random_uuid() | — | — | — |
| vault | secrets | name | text | no | — | — | — | — |
| vault | secrets | description | text | yes | ''::text | — | — | — |
| vault | secrets | secret | text | yes | — | — | — | — |
| vault | secrets | key_id | uuid | no | — | — | — | — |
| vault | secrets | nonce | bytea | no | vault._crypto_aead_det_noncegen() | — | — | — |
| vault | secrets | created_at | timestamp with time zone | yes | CURRENT_TIMESTAMP | — | — | — |
| vault | secrets | updated_at | timestamp with time zone | yes | CURRENT_TIMESTAMP | — | — | — |


### Constraints

#### auth

| schema | relation | constraint_name | kind | definition | validated |
| --- | --- | --- | --- | --- | --- |
| auth | audit_log_entries | audit_log_entries_pkey | p | PRIMARY KEY (id) | yes |
| auth | custom_oauth_providers | custom_oauth_providers_authorization_url_https | c | CHECK (((authorization_url IS NULL) OR (authorization_url ~~ 'https://%'::text))) | yes |
| auth | custom_oauth_providers | custom_oauth_providers_authorization_url_length | c | CHECK (((authorization_url IS NULL) OR (char_length(authorization_url) <= 2048))) | yes |
| auth | custom_oauth_providers | custom_oauth_providers_client_id_length | c | CHECK (((char_length(client_id) >= 1) AND (char_length(client_id) <= 512))) | yes |
| auth | custom_oauth_providers | custom_oauth_providers_discovery_url_length | c | CHECK (((discovery_url IS NULL) OR (char_length(discovery_url) <= 2048))) | yes |
| auth | custom_oauth_providers | custom_oauth_providers_identifier_format | c | CHECK ((identifier ~ '^[a-z0-9][a-z0-9:-]{0,48}[a-z0-9]$'::text)) | yes |
| auth | custom_oauth_providers | custom_oauth_providers_identifier_key | u | UNIQUE (identifier) | yes |
| auth | custom_oauth_providers | custom_oauth_providers_issuer_length | c | CHECK (((issuer IS NULL) OR ((char_length(issuer) >= 1) AND (char_length(issuer) <= 2048)))) | yes |
| auth | custom_oauth_providers | custom_oauth_providers_jwks_uri_https | c | CHECK (((jwks_uri IS NULL) OR (jwks_uri ~~ 'https://%'::text))) | yes |
| auth | custom_oauth_providers | custom_oauth_providers_jwks_uri_length | c | CHECK (((jwks_uri IS NULL) OR (char_length(jwks_uri) <= 2048))) | yes |
| auth | custom_oauth_providers | custom_oauth_providers_name_length | c | CHECK (((char_length(name) >= 1) AND (char_length(name) <= 100))) | yes |
| auth | custom_oauth_providers | custom_oauth_providers_oauth2_requires_endpoints | c | CHECK (((provider_type <> 'oauth2'::text) OR ((authorization_url IS NOT NULL) AND (token_url IS NOT NULL) AND (userinfo_url IS NOT NULL)))) | yes |
| auth | custom_oauth_providers | custom_oauth_providers_oidc_discovery_url_https | c | CHECK (((provider_type <> 'oidc'::text) OR (discovery_url IS NULL) OR (discovery_url ~~ 'https://%'::text))) | yes |
| auth | custom_oauth_providers | custom_oauth_providers_oidc_issuer_https | c | CHECK (((provider_type <> 'oidc'::text) OR (issuer IS NULL) OR (issuer ~~ 'https://%'::text))) | yes |
| auth | custom_oauth_providers | custom_oauth_providers_oidc_requires_issuer | c | CHECK (((provider_type <> 'oidc'::text) OR (issuer IS NOT NULL))) | yes |
| auth | custom_oauth_providers | custom_oauth_providers_pkey | p | PRIMARY KEY (id) | yes |
| auth | custom_oauth_providers | custom_oauth_providers_provider_type_check | c | CHECK ((provider_type = ANY (ARRAY['oauth2'::text, 'oidc'::text]))) | yes |
| auth | custom_oauth_providers | custom_oauth_providers_token_url_https | c | CHECK (((token_url IS NULL) OR (token_url ~~ 'https://%'::text))) | yes |
| auth | custom_oauth_providers | custom_oauth_providers_token_url_length | c | CHECK (((token_url IS NULL) OR (char_length(token_url) <= 2048))) | yes |
| auth | custom_oauth_providers | custom_oauth_providers_userinfo_url_https | c | CHECK (((userinfo_url IS NULL) OR (userinfo_url ~~ 'https://%'::text))) | yes |
| auth | custom_oauth_providers | custom_oauth_providers_userinfo_url_length | c | CHECK (((userinfo_url IS NULL) OR (char_length(userinfo_url) <= 2048))) | yes |
| auth | flow_state | flow_state_pkey | p | PRIMARY KEY (id) | yes |
| auth | identities | identities_pkey | p | PRIMARY KEY (id) | yes |
| auth | identities | identities_provider_id_provider_unique | u | UNIQUE (provider_id, provider) | yes |
| auth | identities | identities_user_id_fkey | f | FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE | yes |
| auth | instances | instances_pkey | p | PRIMARY KEY (id) | yes |
| auth | mfa_amr_claims | amr_id_pk | p | PRIMARY KEY (id) | yes |
| auth | mfa_amr_claims | mfa_amr_claims_session_id_authentication_method_pkey | u | UNIQUE (session_id, authentication_method) | yes |
| auth | mfa_amr_claims | mfa_amr_claims_session_id_fkey | f | FOREIGN KEY (session_id) REFERENCES auth.sessions(id) ON DELETE CASCADE | yes |
| auth | mfa_challenges | mfa_challenges_auth_factor_id_fkey | f | FOREIGN KEY (factor_id) REFERENCES auth.mfa_factors(id) ON DELETE CASCADE | yes |
| auth | mfa_challenges | mfa_challenges_pkey | p | PRIMARY KEY (id) | yes |
| auth | mfa_factors | mfa_factors_last_challenged_at_key | u | UNIQUE (last_challenged_at) | yes |
| auth | mfa_factors | mfa_factors_pkey | p | PRIMARY KEY (id) | yes |
| auth | mfa_factors | mfa_factors_user_id_fkey | f | FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE | yes |
| auth | mfa_recovery_code_sets | mfa_recovery_code_sets_failed_verification_count_check | c | CHECK ((failed_verification_count >= 0)) | yes |
| auth | mfa_recovery_code_sets | mfa_recovery_code_sets_mfa_factor_id_fkey | f | FOREIGN KEY (mfa_factor_id) REFERENCES auth.mfa_factors(id) ON DELETE CASCADE | yes |
| auth | mfa_recovery_code_sets | mfa_recovery_code_sets_mfa_factor_id_key | u | UNIQUE (mfa_factor_id) | yes |
| auth | mfa_recovery_code_sets | mfa_recovery_code_sets_pkey | p | PRIMARY KEY (id) | yes |
| auth | mfa_recovery_code_sets | mfa_recovery_code_sets_user_id_fkey | f | FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE | yes |
| auth | mfa_recovery_code_sets | mfa_recovery_code_sets_user_id_key | u | UNIQUE (user_id) | yes |
| auth | mfa_recovery_codes | mfa_recovery_codes_mfa_recovery_code_set_id_fkey | f | FOREIGN KEY (mfa_recovery_code_set_id) REFERENCES auth.mfa_recovery_code_sets(id) ON DELETE CASCADE | yes |
| auth | mfa_recovery_codes | mfa_recovery_codes_pkey | p | PRIMARY KEY (id) | yes |
| auth | oauth_authorizations | oauth_authorizations_authorization_code_key | u | UNIQUE (authorization_code) | yes |
| auth | oauth_authorizations | oauth_authorizations_authorization_code_length | c | CHECK ((char_length(authorization_code) <= 255)) | yes |
| auth | oauth_authorizations | oauth_authorizations_authorization_id_key | u | UNIQUE (authorization_id) | yes |
| auth | oauth_authorizations | oauth_authorizations_client_id_fkey | f | FOREIGN KEY (client_id) REFERENCES auth.oauth_clients(id) ON DELETE CASCADE | yes |
| auth | oauth_authorizations | oauth_authorizations_code_challenge_length | c | CHECK ((char_length(code_challenge) <= 128)) | yes |
| auth | oauth_authorizations | oauth_authorizations_expires_at_future | c | CHECK ((expires_at > created_at)) | yes |
| auth | oauth_authorizations | oauth_authorizations_nonce_length | c | CHECK ((char_length(nonce) <= 255)) | yes |
| auth | oauth_authorizations | oauth_authorizations_pkey | p | PRIMARY KEY (id) | yes |
| auth | oauth_authorizations | oauth_authorizations_redirect_uri_length | c | CHECK ((char_length(redirect_uri) <= 2048)) | yes |
| auth | oauth_authorizations | oauth_authorizations_resource_length | c | CHECK ((char_length(resource) <= 2048)) | yes |
| auth | oauth_authorizations | oauth_authorizations_scope_length | c | CHECK ((char_length(scope) <= 4096)) | yes |
| auth | oauth_authorizations | oauth_authorizations_state_length | c | CHECK ((char_length(state) <= 4096)) | yes |
| auth | oauth_authorizations | oauth_authorizations_user_id_fkey | f | FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE | yes |
| auth | oauth_client_states | oauth_client_states_pkey | p | PRIMARY KEY (id) | yes |
| auth | oauth_clients | oauth_clients_client_name_length | c | CHECK ((char_length(client_name) <= 1024)) | yes |
| auth | oauth_clients | oauth_clients_client_uri_length | c | CHECK ((char_length(client_uri) <= 2048)) | yes |
| auth | oauth_clients | oauth_clients_logo_uri_length | c | CHECK ((char_length(logo_uri) <= 2048)) | yes |
| auth | oauth_clients | oauth_clients_pkey | p | PRIMARY KEY (id) | yes |
| auth | oauth_clients | oauth_clients_token_endpoint_auth_method_check | c | CHECK ((token_endpoint_auth_method = ANY (ARRAY['client_secret_basic'::text, 'client_secret_post'::text, 'none'::text]))) | yes |
| auth | oauth_consents | oauth_consents_client_id_fkey | f | FOREIGN KEY (client_id) REFERENCES auth.oauth_clients(id) ON DELETE CASCADE | yes |
| auth | oauth_consents | oauth_consents_pkey | p | PRIMARY KEY (id) | yes |
| auth | oauth_consents | oauth_consents_revoked_after_granted | c | CHECK (((revoked_at IS NULL) OR (revoked_at >= granted_at))) | yes |
| auth | oauth_consents | oauth_consents_scopes_length | c | CHECK ((char_length(scopes) <= 2048)) | yes |
| auth | oauth_consents | oauth_consents_scopes_not_empty | c | CHECK ((char_length(TRIM(BOTH FROM scopes)) > 0)) | yes |
| auth | oauth_consents | oauth_consents_user_client_unique | u | UNIQUE (user_id, client_id) | yes |
| auth | oauth_consents | oauth_consents_user_id_fkey | f | FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE | yes |
| auth | one_time_tokens | one_time_tokens_pkey | p | PRIMARY KEY (id) | yes |
| auth | one_time_tokens | one_time_tokens_token_hash_check | c | CHECK ((char_length(token_hash) > 0)) | yes |
| auth | one_time_tokens | one_time_tokens_user_id_fkey | f | FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE | yes |
| auth | refresh_tokens | refresh_tokens_pkey | p | PRIMARY KEY (id) | yes |
| auth | refresh_tokens | refresh_tokens_session_id_fkey | f | FOREIGN KEY (session_id) REFERENCES auth.sessions(id) ON DELETE CASCADE | yes |
| auth | refresh_tokens | refresh_tokens_token_unique | u | UNIQUE (token) | yes |
| auth | saml_providers | entity_id not empty | c | CHECK ((char_length(entity_id) > 0)) | yes |
| auth | saml_providers | metadata_url not empty | c | CHECK (((metadata_url = NULL::text) OR (char_length(metadata_url) > 0))) | yes |
| auth | saml_providers | metadata_xml not empty | c | CHECK ((char_length(metadata_xml) > 0)) | yes |
| auth | saml_providers | saml_providers_entity_id_key | u | UNIQUE (entity_id) | yes |
| auth | saml_providers | saml_providers_pkey | p | PRIMARY KEY (id) | yes |
| auth | saml_providers | saml_providers_sso_provider_id_fkey | f | FOREIGN KEY (sso_provider_id) REFERENCES auth.sso_providers(id) ON DELETE CASCADE | yes |
| auth | saml_relay_states | request_id not empty | c | CHECK ((char_length(request_id) > 0)) | yes |
| auth | saml_relay_states | saml_relay_states_flow_state_id_fkey | f | FOREIGN KEY (flow_state_id) REFERENCES auth.flow_state(id) ON DELETE CASCADE | yes |
| auth | saml_relay_states | saml_relay_states_pkey | p | PRIMARY KEY (id) | yes |
| auth | saml_relay_states | saml_relay_states_sso_provider_id_fkey | f | FOREIGN KEY (sso_provider_id) REFERENCES auth.sso_providers(id) ON DELETE CASCADE | yes |
| auth | schema_migrations | schema_migrations_pkey | p | PRIMARY KEY (version) | yes |
| auth | scim_tokens | scim_tokens_expires_at_future | c | CHECK (((expires_at IS NULL) OR (expires_at > created_at))) | yes |
| auth | scim_tokens | scim_tokens_pkey | p | PRIMARY KEY (id) | yes |
| auth | scim_tokens | scim_tokens_revoked_after_created | c | CHECK (((revoked_at IS NULL) OR (revoked_at >= created_at))) | yes |
| auth | scim_tokens | scim_tokens_sso_provider_id_fkey | f | FOREIGN KEY (sso_provider_id) REFERENCES auth.sso_providers(id) ON DELETE CASCADE | yes |
| auth | scim_tokens | scim_tokens_token_hash_check | c | CHECK ((token_hash ~ '^[0-9a-f]{64}$'::text)) | yes |
| auth | scim_users | scim_users_pkey | p | PRIMARY KEY (id) | yes |
| auth | scim_users | scim_users_sso_provider_id_fkey | f | FOREIGN KEY (sso_provider_id) REFERENCES auth.sso_providers(id) ON DELETE CASCADE | yes |
| auth | scim_users | scim_users_user_id_fkey | f | FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE SET NULL | yes |
| auth | sessions | sessions_oauth_client_id_fkey | f | FOREIGN KEY (oauth_client_id) REFERENCES auth.oauth_clients(id) ON DELETE CASCADE | yes |
| auth | sessions | sessions_pkey | p | PRIMARY KEY (id) | yes |
| auth | sessions | sessions_scopes_length | c | CHECK ((char_length(scopes) <= 4096)) | yes |
| auth | sessions | sessions_user_id_fkey | f | FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE | yes |
| auth | sso_domains | domain not empty | c | CHECK ((char_length(domain) > 0)) | yes |
| auth | sso_domains | sso_domains_pkey | p | PRIMARY KEY (id) | yes |
| auth | sso_domains | sso_domains_sso_provider_id_fkey | f | FOREIGN KEY (sso_provider_id) REFERENCES auth.sso_providers(id) ON DELETE CASCADE | yes |
| auth | sso_providers | resource_id not empty | c | CHECK (((resource_id = NULL::text) OR (char_length(resource_id) > 0))) | yes |
| auth | sso_providers | sso_providers_pkey | p | PRIMARY KEY (id) | yes |
| auth | users | users_email_change_confirm_status_check | c | CHECK (((email_change_confirm_status >= 0) AND (email_change_confirm_status <= 2))) | yes |
| auth | users | users_phone_key | u | UNIQUE (phone) | yes |
| auth | users | users_pkey | p | PRIMARY KEY (id) | yes |
| auth | webauthn_challenges | webauthn_challenges_challenge_type_check | c | CHECK ((challenge_type = ANY (ARRAY['signup'::text, 'registration'::text, 'authentication'::text]))) | yes |
| auth | webauthn_challenges | webauthn_challenges_pkey | p | PRIMARY KEY (id) | yes |
| auth | webauthn_challenges | webauthn_challenges_user_id_fkey | f | FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE | yes |
| auth | webauthn_credentials | webauthn_credentials_pkey | p | PRIMARY KEY (id) | yes |
| auth | webauthn_credentials | webauthn_credentials_user_id_fkey | f | FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE | yes |

#### codebi

| schema | relation | constraint_name | kind | definition | validated |
| --- | --- | --- | --- | --- | --- |
| codebi | alembic_version | alembic_version_pkc | p | PRIMARY KEY (version_num) | yes |
| codebi | audit_events | audit_events_action_check | c | CHECK ((btrim(action) <> ''::text)) | yes |
| codebi | audit_events | audit_events_actor_id_fkey | f | FOREIGN KEY (actor_id) REFERENCES codebi.users(id) | yes |
| codebi | audit_events | audit_events_changes_check | c | CHECK ((jsonb_typeof(changes) = 'object'::text)) | yes |
| codebi | audit_events | audit_events_client_id_fkey | f | FOREIGN KEY (client_id) REFERENCES codebi.clients(id) | yes |
| codebi | audit_events | audit_events_pkey | p | PRIMARY KEY (id) | yes |
| codebi | client_memberships | client_memberships_client_id_fkey | f | FOREIGN KEY (client_id) REFERENCES codebi.clients(id) | yes |
| codebi | client_memberships | client_memberships_client_id_user_id_key | u | UNIQUE (client_id, user_id) | yes |
| codebi | client_memberships | client_memberships_granted_by_fkey | f | FOREIGN KEY (granted_by) REFERENCES codebi.users(id) | yes |
| codebi | client_memberships | client_memberships_pkey | p | PRIMARY KEY (id) | yes |
| codebi | client_memberships | client_memberships_role_code_role_scope_fkey | f | FOREIGN KEY (role_code, role_scope) REFERENCES codebi.roles(code, scope) | yes |
| codebi | client_memberships | client_memberships_role_scope_check | c | CHECK ((role_scope = 'client'::text)) | yes |
| codebi | client_memberships | client_memberships_user_id_fkey | f | FOREIGN KEY (user_id) REFERENCES codebi.users(id) | yes |
| codebi | clients | client_inquiry_length | c | CHECK (((char_length(registration_inquiry) >= 1) AND (char_length(registration_inquiry) <= 5000))) | yes |
| codebi | clients | client_logo_background_choice | c | CHECK ((logo_background = ANY (ARRAY['light'::text, 'dark'::text]))) | yes |
| codebi | clients | client_logo_own_path | c | CHECK ((logo_path = ((id)::text \|\| '/brand/logo.png'::text))) | yes |
| codebi | clients | client_region_requires_country | c | CHECK (((state_region_code IS NULL) OR (country_code IS NOT NULL))) | yes |
| codebi | clients | clients_client_code_key | u | UNIQUE (client_code) | yes |
| codebi | clients | clients_client_number_key | u | UNIQUE (client_number) | yes |
| codebi | clients | clients_country_catalog_fk | f | FOREIGN KEY (country_code) REFERENCES codebi.countries(code) | yes |
| codebi | clients | clients_country_code_check | c | CHECK ((country_code ~ '^[A-Z]{2}$'::text)) | yes |
| codebi | clients | clients_created_by_fkey | f | FOREIGN KEY (created_by) REFERENCES codebi.users(id) | yes |
| codebi | clients | clients_industry_id_fkey | f | FOREIGN KEY (industry_id) REFERENCES codebi.industries(id) | yes |
| codebi | clients | clients_name_check | c | CHECK ((btrim(name) <> ''::text)) | yes |
| codebi | clients | clients_pkey | p | PRIMARY KEY (id) | yes |
| codebi | clients | clients_region_country_fk | f | FOREIGN KEY (state_region_code, country_code) REFERENCES codebi.state_regions(code, country_code) | yes |
| codebi | clients | clients_timezone_catalog_fk | f | FOREIGN KEY (timezone) REFERENCES codebi.timezones(name) | yes |
| codebi | clients | clients_updated_by_fkey | f | FOREIGN KEY (updated_by) REFERENCES codebi.users(id) | yes |
| codebi | countries | countries_pkey | p | PRIMARY KEY (code) | yes |
| codebi | countries | country_code_format | c | CHECK ((code ~ '^[A-Z]{2}$'::text)) | yes |
| codebi | industries | industries_name_check | c | CHECK ((btrim(name) <> ''::text)) | yes |
| codebi | industries | industries_name_key | u | UNIQUE (name) | yes |
| codebi | industries | industries_pkey | p | PRIMARY KEY (id) | yes |
| codebi | login_events | login_events_outcome_check | c | CHECK ((outcome = ANY (ARRAY['success'::text, 'denied'::text, 'failure'::text]))) | yes |
| codebi | login_events | login_events_pkey | p | PRIMARY KEY (id) | yes |
| codebi | login_events | login_events_provider_check | c | CHECK ((provider = ANY (ARRAY['google'::text, 'azure'::text]))) | yes |
| codebi | login_events | login_events_user_id_fkey | f | FOREIGN KEY (user_id) REFERENCES codebi.users(id) | yes |
| codebi | public_inquiries | public_inquiries_company_check | c | CHECK (((char_length(company) >= 1) AND (char_length(company) <= 200))) | yes |
| codebi | public_inquiries | public_inquiries_details_check | c | CHECK (((char_length(details) >= 1) AND (char_length(details) <= 5000))) | yes |
| codebi | public_inquiries | public_inquiries_email_check | c | CHECK (((char_length(email) >= 3) AND (char_length(email) <= 254))) | yes |
| codebi | public_inquiries | public_inquiries_name_check | c | CHECK (((char_length(name) >= 1) AND (char_length(name) <= 200))) | yes |
| codebi | public_inquiries | public_inquiries_pkey | p | PRIMARY KEY (id) | yes |
| codebi | roles | roles_code_scope_key | u | UNIQUE (code, scope) | yes |
| codebi | roles | roles_pkey | p | PRIMARY KEY (code) | yes |
| codebi | roles | roles_scope_check | c | CHECK ((scope = ANY (ARRAY['global'::text, 'client'::text]))) | yes |
| codebi | sow_estimates | sow_estimates_check | c | CHECK ((((min_hours IS NULL) AND (max_hours IS NULL)) OR ((min_hours IS NOT NULL) AND (max_hours IS NOT NULL) AND (min_hours > (0)::numeric) AND (max_hours >= min_hours) AND (max_hours < 'Infinity'::numeric)))) | yes |
| codebi | sow_estimates | sow_estimates_check2 | c | CHECK (((status = 'draft'::text) OR (issued_at IS NOT NULL))) | yes |
| codebi | sow_estimates | sow_estimates_check3 | c | CHECK (((decided_by IS NULL) = (decided_at IS NULL))) | yes |
| codebi | sow_estimates | sow_estimates_check4 | c | CHECK (((status <> ALL (ARRAY['accepted'::text, 'rejected'::text])) OR (decided_at IS NOT NULL))) | yes |
| codebi | sow_estimates | sow_estimates_check5 | c | CHECK (((status <> 'accepted'::text) OR (feasibility = 'feasible'::text))) | yes |
| codebi | sow_estimates | sow_estimates_check6 | c | CHECK (((decided_at IS NULL) OR ((issued_at IS NOT NULL) AND (decided_at >= issued_at)))) | yes |
| codebi | sow_estimates | sow_estimates_client_id_sow_id_fkey | f | FOREIGN KEY (client_id, sow_id) REFERENCES codebi.statements_of_work(client_id, id) | yes |
| codebi | sow_estimates | sow_estimates_client_id_sow_id_id_key | u | UNIQUE (client_id, sow_id, id) | yes |
| codebi | sow_estimates | sow_estimates_created_by_fkey | f | FOREIGN KEY (created_by) REFERENCES codebi.users(id) | yes |
| codebi | sow_estimates | sow_estimates_decided_by_fkey | f | FOREIGN KEY (decided_by) REFERENCES codebi.users(id) | yes |
| codebi | sow_estimates | sow_estimates_feasibility_check | c | CHECK ((feasibility = ANY (ARRAY['feasible'::text, 'not_feasible'::text, 'needs_clarification'::text]))) | yes |
| codebi | sow_estimates | sow_estimates_feasible_pricing | c | CHECK (((NOT (pricing_type IS DISTINCT FROM 'project'::text)) OR ((status <> ALL (ARRAY['issued'::text, 'accepted'::text])) OR (feasibility <> 'feasible'::text) OR (min_hours IS NOT NULL)))) | yes |
| codebi | sow_estimates | sow_estimates_pkey | p | PRIMARY KEY (id) | yes |
| codebi | sow_estimates | sow_estimates_pricing_values | c | CHECK ((((pricing_type IS NULL) AND (hourly_rate IS NULL) AND (min_amount IS NULL) AND (max_amount IS NULL) AND (estimated_completion IS NULL)) OR ((pricing_type = 'hourly'::text) AND (pricing_type IS NOT NULL) AND (min_hours IS NOT NULL) AND (max_hours IS NOT NULL) AND (hourly_rate IS NOT NULL) AND (hourly_rate > (0)::numeric) AND (hourly_rate < 'Infinity'::numeric) AND (min_amount IS NOT NULL) AND (max_amount IS NOT NULL) AND (min_amount = round((min_hours * hourly_rate), 2)) AND (max_amount = round((max_hours * hourly_rate), 2))) OR ((pricing_type = 'project'::text) AND (pricing_type IS NOT NULL) AND (min_hours IS NULL) AND (max_hours IS NULL) AND (hourly_rate IS NULL) AND (min_amount IS NOT NULL) AND (min_amount > (0)::numeric) AND (min_amount < 'Infinity'::numeric) AND (max_amount IS NOT NULL) AND (min_amount = max_amount) AND (estimated_completion IS NOT NULL)))) | yes |
| codebi | sow_estimates | sow_estimates_revision_check | c | CHECK ((revision > 0)) | yes |
| codebi | sow_estimates | sow_estimates_scope_snapshot_check | c | CHECK ((btrim(scope_snapshot) <> ''::text)) | yes |
| codebi | sow_estimates | sow_estimates_sow_id_revision_key | u | UNIQUE (sow_id, revision) | yes |
| codebi | sow_estimates | sow_estimates_status_check | c | CHECK ((status = ANY (ARRAY['draft'::text, 'issued'::text, 'accepted'::text, 'rejected'::text, 'superseded'::text]))) | yes |
| codebi | sow_events | sow_events_actor_id_fkey | f | FOREIGN KEY (actor_id) REFERENCES codebi.users(id) | yes |
| codebi | sow_events | sow_events_client_id_sow_id_estimate_id_fkey | f | FOREIGN KEY (client_id, sow_id, estimate_id) REFERENCES codebi.sow_estimates(client_id, sow_id, id) | yes |
| codebi | sow_events | sow_events_client_id_sow_id_fkey | f | FOREIGN KEY (client_id, sow_id) REFERENCES codebi.statements_of_work(client_id, id) | yes |
| codebi | sow_events | sow_events_client_id_sow_id_message_id_fkey | f | FOREIGN KEY (client_id, sow_id, message_id) REFERENCES codebi.sow_messages(client_id, sow_id, id) | yes |
| codebi | sow_events | sow_events_details_check | c | CHECK ((jsonb_typeof(details) = 'object'::text)) | yes |
| codebi | sow_events | sow_events_event_type_check | c | CHECK ((btrim(event_type) <> ''::text)) | yes |
| codebi | sow_events | sow_events_pkey | p | PRIMARY KEY (id) | yes |
| codebi | sow_messages | sow_messages_author_id_fkey | f | FOREIGN KEY (author_id) REFERENCES codebi.users(id) | yes |
| codebi | sow_messages | sow_messages_body_check | c | CHECK ((btrim(body) <> ''::text)) | yes |
| codebi | sow_messages | sow_messages_client_id_sow_id_fkey | f | FOREIGN KEY (client_id, sow_id) REFERENCES codebi.statements_of_work(client_id, id) | yes |
| codebi | sow_messages | sow_messages_client_id_sow_id_id_key | u | UNIQUE (client_id, sow_id, id) | yes |
| codebi | sow_messages | sow_messages_message_type_check | c | CHECK ((message_type = ANY (ARRAY['general'::text, 'clarification'::text, 'reply'::text]))) | yes |
| codebi | sow_messages | sow_messages_pkey | p | PRIMARY KEY (id) | yes |
| codebi | sow_tools | sow_tools_pkey | p | PRIMARY KEY (sow_id, tool_id) | yes |
| codebi | sow_tools | sow_tools_sow_id_fkey | f | FOREIGN KEY (sow_id) REFERENCES codebi.statements_of_work(id) | yes |
| codebi | sow_tools | sow_tools_tool_id_fkey | f | FOREIGN KEY (tool_id) REFERENCES codebi.tools(id) | yes |
| codebi | sow_types | sow_types_name_key | u | UNIQUE (name) | yes |
| codebi | sow_types | sow_types_pkey | p | PRIMARY KEY (id) | yes |
| codebi | state_regions | state_region_country_unique | u | UNIQUE (code, country_code) | yes |
| codebi | state_regions | state_regions_country_code_fkey | f | FOREIGN KEY (country_code) REFERENCES codebi.countries(code) | yes |
| codebi | state_regions | state_regions_pkey | p | PRIMARY KEY (code) | yes |
| codebi | statements_of_work | sow_accepted_estimate_fk | f | FOREIGN KEY (client_id, id, accepted_estimate_id) REFERENCES codebi.sow_estimates(client_id, sow_id, id) | yes |
| codebi | statements_of_work | sow_other_tool_details_length | c | CHECK (((char_length(other_tool_details) >= 1) AND (char_length(other_tool_details) <= 1000))) | yes |
| codebi | statements_of_work | statements_of_work_check | c | CHECK (((submitted_by IS NULL) = (submitted_at IS NULL))) | yes |
| codebi | statements_of_work | statements_of_work_check1 | c | CHECK (((status <> ALL (ARRAY['submitted'::text, 'under_review'::text, 'clarification_needed'::text, 'awaiting_acceptance'::text, 'approved'::text, 'in_progress'::text, 'completed'::text])) OR (submitted_at IS NOT NULL))) | yes |
| codebi | statements_of_work | statements_of_work_check2 | c | CHECK (((status <> ALL (ARRAY['approved'::text, 'in_progress'::text, 'completed'::text])) OR (accepted_estimate_id IS NOT NULL))) | yes |
| codebi | statements_of_work | statements_of_work_check3 | c | CHECK (((status <> ALL (ARRAY['in_progress'::text, 'completed'::text])) OR (started_at IS NOT NULL))) | yes |
| codebi | statements_of_work | statements_of_work_check4 | c | CHECK (((status <> 'completed'::text) OR (completed_at IS NOT NULL))) | yes |
| codebi | statements_of_work | statements_of_work_check5 | c | CHECK (((completed_at IS NULL) OR ((started_at IS NOT NULL) AND (completed_at >= started_at)))) | yes |
| codebi | statements_of_work | statements_of_work_client_id_fkey | f | FOREIGN KEY (client_id) REFERENCES codebi.clients(id) | yes |
| codebi | statements_of_work | statements_of_work_client_id_id_key | u | UNIQUE (client_id, id) | yes |
| codebi | statements_of_work | statements_of_work_created_by_fkey | f | FOREIGN KEY (created_by) REFERENCES codebi.users(id) | yes |
| codebi | statements_of_work | statements_of_work_description_check | c | CHECK ((btrim(description) <> ''::text)) | yes |
| codebi | statements_of_work | statements_of_work_pkey | p | PRIMARY KEY (id) | yes |
| codebi | statements_of_work | statements_of_work_priority_check | c | CHECK ((priority = ANY (ARRAY['low'::text, 'normal'::text, 'high'::text, 'urgent'::text]))) | yes |
| codebi | statements_of_work | statements_of_work_sow_code_key | u | UNIQUE (sow_code) | yes |
| codebi | statements_of_work | statements_of_work_sow_number_key | u | UNIQUE (sow_number) | yes |
| codebi | statements_of_work | statements_of_work_sow_type_id_fkey | f | FOREIGN KEY (sow_type_id) REFERENCES codebi.sow_types(id) | yes |
| codebi | statements_of_work | statements_of_work_status_check | c | CHECK ((status = ANY (ARRAY['draft'::text, 'pending_client_approval'::text, 'submitted'::text, 'under_review'::text, 'clarification_needed'::text, 'awaiting_acceptance'::text, 'approved'::text, 'in_progress'::text, 'completed'::text, 'declined'::text, 'cancelled'::text]))) | yes |
| codebi | statements_of_work | statements_of_work_submitted_by_fkey | f | FOREIGN KEY (submitted_by) REFERENCES codebi.users(id) | yes |
| codebi | statements_of_work | statements_of_work_title_check | c | CHECK ((btrim(title) <> ''::text)) | yes |
| codebi | statements_of_work | statements_of_work_updated_by_fkey | f | FOREIGN KEY (updated_by) REFERENCES codebi.users(id) | yes |
| codebi | test | test_pkey | p | PRIMARY KEY (id) | yes |
| codebi | time_entries | time_entries_activity_check | c | CHECK ((btrim(activity) <> ''::text)) | yes |
| codebi | time_entries | time_entries_check | c | CHECK ((ended_at > started_at)) | yes |
| codebi | time_entries | time_entries_check1 | c | CHECK (((status <> 'void'::text) OR (NULLIF(btrim(void_reason), ''::text) IS NOT NULL))) | yes |
| codebi | time_entries | time_entries_client_id_sow_id_fkey | f | FOREIGN KEY (client_id, sow_id) REFERENCES codebi.statements_of_work(client_id, id) | yes |
| codebi | time_entries | time_entries_pkey | p | PRIMARY KEY (id) | yes |
| codebi | time_entries | time_entries_status_check | c | CHECK ((status = ANY (ARRAY['draft'::text, 'submitted'::text, 'void'::text]))) | yes |
| codebi | time_entries | time_entries_worker_id_fkey | f | FOREIGN KEY (worker_id) REFERENCES codebi.users(id) | yes |
| codebi | timezones | timezones_pkey | p | PRIMARY KEY (name) | yes |
| codebi | tools | tools_name_key | u | UNIQUE (name) | yes |
| codebi | tools | tools_pkey | p | PRIMARY KEY (id) | yes |
| codebi | user_invitations | user_invitations_accepted_by_fkey | f | FOREIGN KEY (accepted_by) REFERENCES codebi.users(id) | yes |
| codebi | user_invitations | user_invitations_check | c | CHECK ((expires_at > created_at)) | yes |
| codebi | user_invitations | user_invitations_check1 | c | CHECK (((accepted_by IS NULL) = (accepted_at IS NULL))) | yes |
| codebi | user_invitations | user_invitations_check2 | c | CHECK (((accepted_at IS NULL) OR ((revoked_at IS NULL) AND (accepted_at <= expires_at)))) | yes |
| codebi | user_invitations | user_invitations_client_id_fkey | f | FOREIGN KEY (client_id) REFERENCES codebi.clients(id) | yes |
| codebi | user_invitations | user_invitations_email_check | c | CHECK (((email = lower(btrim(email))) AND (POSITION(('@'::text) IN (email)) > 1))) | yes |
| codebi | user_invitations | user_invitations_invited_by_fkey | f | FOREIGN KEY (invited_by) REFERENCES codebi.users(id) | yes |
| codebi | user_invitations | user_invitations_pkey | p | PRIMARY KEY (id) | yes |
| codebi | user_invitations | user_invitations_role_code_role_scope_fkey | f | FOREIGN KEY (role_code, role_scope) REFERENCES codebi.roles(code, scope) | yes |
| codebi | user_invitations | user_invitations_role_scope_check | c | CHECK ((role_scope = 'client'::text)) | yes |
| codebi | user_invitations | user_invitations_token_hash_key | u | UNIQUE (token_hash) | yes |
| codebi | user_roles | user_roles_granted_by_fkey | f | FOREIGN KEY (granted_by) REFERENCES codebi.users(id) | yes |
| codebi | user_roles | user_roles_pkey | p | PRIMARY KEY (user_id, role_code) | yes |
| codebi | user_roles | user_roles_role_code_role_scope_fkey | f | FOREIGN KEY (role_code, role_scope) REFERENCES codebi.roles(code, scope) | yes |
| codebi | user_roles | user_roles_role_scope_check | c | CHECK ((role_scope = 'global'::text)) | yes |
| codebi | user_roles | user_roles_user_id_fkey | f | FOREIGN KEY (user_id) REFERENCES codebi.users(id) | yes |
| codebi | users | users_auth_user_id_fkey | f | FOREIGN KEY (auth_user_id) REFERENCES auth.users(id) ON DELETE SET NULL | yes |
| codebi | users | users_auth_user_id_key | u | UNIQUE (auth_user_id) | yes |
| codebi | users | users_created_by_fkey | f | FOREIGN KEY (created_by) REFERENCES codebi.users(id) | yes |
| codebi | users | users_display_name_check | c | CHECK ((btrim(display_name) <> ''::text)) | yes |
| codebi | users | users_email_check | c | CHECK (((email = lower(btrim(email))) AND (email ~ '^[^[:space:]@]+@[^[:space:]@]+[.][^[:space:]@]+$'::text))) | yes |
| codebi | users | users_email_key | u | UNIQUE (email) | yes |
| codebi | users | users_pkey | p | PRIMARY KEY (id) | yes |
| codebi | users | users_updated_by_fkey | f | FOREIGN KEY (updated_by) REFERENCES codebi.users(id) | yes |
| codebi | users | users_user_type_check | c | CHECK ((user_type = ANY (ARRAY['internal'::text, 'client'::text]))) | yes |

#### history

| schema | relation | constraint_name | kind | definition | validated |
| --- | --- | --- | --- | --- | --- |
| history | knowledge_entries | knowledge_entries_pkey | p | PRIMARY KEY (id) | yes |

#### realtime

| schema | relation | constraint_name | kind | definition | validated |
| --- | --- | --- | --- | --- | --- |
| realtime | messages | messages_payload_exclusive | c | CHECK (((payload IS NULL) OR (binary_payload IS NULL))) NOT VALID | no |
| realtime | messages | messages_pkey | p | PRIMARY KEY (id, inserted_at) | yes |
| realtime | schema_migrations | schema_migrations_pkey | p | PRIMARY KEY (version) | yes |
| realtime | subscription | pk_subscription | p | PRIMARY KEY (id) | yes |
| realtime | subscription | subscription_action_filter_check | c | CHECK ((action_filter = ANY (ARRAY['*'::text, 'INSERT'::text, 'UPDATE'::text, 'DELETE'::text]))) | yes |

#### storage

| schema | relation | constraint_name | kind | definition | validated |
| --- | --- | --- | --- | --- | --- |
| storage | bucket_lifecycle_states | bucket_lifecycle_continuation_object_check | c | CHECK (((continuation IS NULL) OR (jsonb_typeof(continuation) = 'object'::text))) | yes |
| storage | bucket_lifecycle_states | bucket_lifecycle_failure_count_check | c | CHECK ((failure_count >= 0)) | yes |
| storage | bucket_lifecycle_states | bucket_lifecycle_last_result_object_check | c | CHECK (((last_result IS NULL) OR (jsonb_typeof(last_result) = 'object'::text))) | yes |
| storage | bucket_lifecycle_states | bucket_lifecycle_recovery_due_check | c | CHECK (((next_run_at IS NOT NULL) OR (NOT jsonb_path_exists(COALESCE(continuation, '{}'::jsonb), '$."batch"."inFlight"'::jsonpath)))) | yes |
| storage | bucket_lifecycle_states | bucket_lifecycle_states_bucket_id_fkey | f | FOREIGN KEY (bucket_id) REFERENCES storage.buckets(id) ON DELETE CASCADE | yes |
| storage | bucket_lifecycle_states | bucket_lifecycle_states_pkey | p | PRIMARY KEY (bucket_id) | yes |
| storage | buckets | buckets_lifecycle_configuration_pair_check | c | CHECK (((lifecycle_configuration IS NULL) = (lifecycle_configuration_generation IS NULL))) | yes |
| storage | buckets | buckets_lifecycle_configuration_shape_check | c | CHECK (((lifecycle_configuration IS NULL) OR ((jsonb_typeof(lifecycle_configuration) = 'object'::text) AND (lifecycle_configuration ? 'rules'::text) AND CASE     WHEN (jsonb_typeof((lifecycle_configuration -> 'rules'::text)) = 'array'::text) THEN ((jsonb_array_length((lifecycle_configuration -> 'rules'::text)) >= 1) AND (jsonb_array_length((lifecycle_configuration -> 'rules'::text)) <= 1000))     ELSE false END))) | yes |
| storage | buckets | buckets_lifecycle_configuration_standard_only_check | c | CHECK (((type = 'STANDARD'::storage.buckettype) OR ((lifecycle_configuration IS NULL) AND (lifecycle_configuration_generation IS NULL)))) | yes |
| storage | buckets | buckets_pkey | p | PRIMARY KEY (id) | yes |
| storage | buckets | buckets_versioning_standard_only_check | c | CHECK (((type = 'STANDARD'::storage.buckettype) OR (versioning_status = 'DISABLED'::text))) | yes |
| storage | buckets | buckets_versioning_status_check | c | CHECK ((versioning_status = ANY (ARRAY['DISABLED'::text, 'ENABLED'::text, 'SUSPENDED'::text]))) | yes |
| storage | buckets_analytics | buckets_analytics_pkey | p | PRIMARY KEY (id) | yes |
| storage | buckets_vectors | buckets_vectors_pkey | p | PRIMARY KEY (id) | yes |
| storage | lifecycle_abandoned_artifacts | lifecycle_abandoned_artifacts_pkey | p | PRIMARY KEY (bucket_id, key) | yes |
| storage | migrations | migrations_name_key | u | UNIQUE (name) | yes |
| storage | migrations | migrations_pkey | p | PRIMARY KEY (id) | yes |
| storage | objects | objects_bucketId_fkey | f | FOREIGN KEY (bucket_id) REFERENCES storage.buckets(id) | yes |
| storage | objects | objects_pkey | p | PRIMARY KEY (id) | yes |
| storage | s3_multipart_uploads | s3_multipart_uploads_bucket_id_fkey | f | FOREIGN KEY (bucket_id) REFERENCES storage.buckets(id) | yes |
| storage | s3_multipart_uploads | s3_multipart_uploads_pkey | p | PRIMARY KEY (id) | yes |
| storage | s3_multipart_uploads_parts | s3_multipart_uploads_parts_bucket_id_fkey | f | FOREIGN KEY (bucket_id) REFERENCES storage.buckets(id) | yes |
| storage | s3_multipart_uploads_parts | s3_multipart_uploads_parts_pkey | p | PRIMARY KEY (id) | yes |
| storage | s3_multipart_uploads_parts | s3_multipart_uploads_parts_upload_id_fkey | f | FOREIGN KEY (upload_id) REFERENCES storage.s3_multipart_uploads(id) ON DELETE CASCADE | yes |
| storage | vector_indexes | vector_indexes_bucket_id_fkey | f | FOREIGN KEY (bucket_id) REFERENCES storage.buckets_vectors(id) | yes |
| storage | vector_indexes | vector_indexes_pkey | p | PRIMARY KEY (id) | yes |

#### vault

| schema | relation | constraint_name | kind | definition | validated |
| --- | --- | --- | --- | --- | --- |
| vault | secrets | secrets_pkey | p | PRIMARY KEY (id) | yes |


### Indexes

#### auth

| schemaname | tablename | indexname | indexdef |
| --- | --- | --- | --- |
| auth | audit_log_entries | audit_log_entries_pkey | CREATE UNIQUE INDEX audit_log_entries_pkey ON auth.audit_log_entries USING btree (id) |
| auth | audit_log_entries | audit_logs_instance_id_idx | CREATE INDEX audit_logs_instance_id_idx ON auth.audit_log_entries USING btree (instance_id) |
| auth | custom_oauth_providers | custom_oauth_providers_created_at_idx | CREATE INDEX custom_oauth_providers_created_at_idx ON auth.custom_oauth_providers USING btree (created_at) |
| auth | custom_oauth_providers | custom_oauth_providers_enabled_idx | CREATE INDEX custom_oauth_providers_enabled_idx ON auth.custom_oauth_providers USING btree (enabled) |
| auth | custom_oauth_providers | custom_oauth_providers_identifier_idx | CREATE INDEX custom_oauth_providers_identifier_idx ON auth.custom_oauth_providers USING btree (identifier) |
| auth | custom_oauth_providers | custom_oauth_providers_identifier_key | CREATE UNIQUE INDEX custom_oauth_providers_identifier_key ON auth.custom_oauth_providers USING btree (identifier) |
| auth | custom_oauth_providers | custom_oauth_providers_pkey | CREATE UNIQUE INDEX custom_oauth_providers_pkey ON auth.custom_oauth_providers USING btree (id) |
| auth | custom_oauth_providers | custom_oauth_providers_provider_type_idx | CREATE INDEX custom_oauth_providers_provider_type_idx ON auth.custom_oauth_providers USING btree (provider_type) |
| auth | flow_state | flow_state_created_at_idx | CREATE INDEX flow_state_created_at_idx ON auth.flow_state USING btree (created_at DESC) |
| auth | flow_state | flow_state_pkey | CREATE UNIQUE INDEX flow_state_pkey ON auth.flow_state USING btree (id) |
| auth | flow_state | idx_auth_code | CREATE INDEX idx_auth_code ON auth.flow_state USING btree (auth_code) |
| auth | flow_state | idx_user_id_auth_method | CREATE INDEX idx_user_id_auth_method ON auth.flow_state USING btree (user_id, authentication_method) |
| auth | identities | identities_email_idx | CREATE INDEX identities_email_idx ON auth.identities USING btree (email text_pattern_ops) |
| auth | identities | identities_pkey | CREATE UNIQUE INDEX identities_pkey ON auth.identities USING btree (id) |
| auth | identities | identities_provider_id_provider_unique | CREATE UNIQUE INDEX identities_provider_id_provider_unique ON auth.identities USING btree (provider_id, provider) |
| auth | identities | identities_user_id_idx | CREATE INDEX identities_user_id_idx ON auth.identities USING btree (user_id) |
| auth | instances | instances_pkey | CREATE UNIQUE INDEX instances_pkey ON auth.instances USING btree (id) |
| auth | mfa_amr_claims | amr_id_pk | CREATE UNIQUE INDEX amr_id_pk ON auth.mfa_amr_claims USING btree (id) |
| auth | mfa_amr_claims | mfa_amr_claims_session_id_authentication_method_pkey | CREATE UNIQUE INDEX mfa_amr_claims_session_id_authentication_method_pkey ON auth.mfa_amr_claims USING btree (session_id, authentication_method) |
| auth | mfa_challenges | mfa_challenge_created_at_idx | CREATE INDEX mfa_challenge_created_at_idx ON auth.mfa_challenges USING btree (created_at DESC) |
| auth | mfa_challenges | mfa_challenges_pkey | CREATE UNIQUE INDEX mfa_challenges_pkey ON auth.mfa_challenges USING btree (id) |
| auth | mfa_factors | factor_id_created_at_idx | CREATE INDEX factor_id_created_at_idx ON auth.mfa_factors USING btree (user_id, created_at) |
| auth | mfa_factors | mfa_factors_last_challenged_at_key | CREATE UNIQUE INDEX mfa_factors_last_challenged_at_key ON auth.mfa_factors USING btree (last_challenged_at) |
| auth | mfa_factors | mfa_factors_pkey | CREATE UNIQUE INDEX mfa_factors_pkey ON auth.mfa_factors USING btree (id) |
| auth | mfa_factors | mfa_factors_user_friendly_name_unique | CREATE UNIQUE INDEX mfa_factors_user_friendly_name_unique ON auth.mfa_factors USING btree (friendly_name, user_id) WHERE (TRIM(BOTH FROM friendly_name) <> ''::text) |
| auth | mfa_factors | mfa_factors_user_id_idx | CREATE INDEX mfa_factors_user_id_idx ON auth.mfa_factors USING btree (user_id) |
| auth | mfa_factors | unique_phone_factor_per_user | CREATE UNIQUE INDEX unique_phone_factor_per_user ON auth.mfa_factors USING btree (user_id, phone) |
| auth | mfa_recovery_code_sets | mfa_recovery_code_sets_mfa_factor_id_key | CREATE UNIQUE INDEX mfa_recovery_code_sets_mfa_factor_id_key ON auth.mfa_recovery_code_sets USING btree (mfa_factor_id) |
| auth | mfa_recovery_code_sets | mfa_recovery_code_sets_pkey | CREATE UNIQUE INDEX mfa_recovery_code_sets_pkey ON auth.mfa_recovery_code_sets USING btree (id) |
| auth | mfa_recovery_code_sets | mfa_recovery_code_sets_user_id_key | CREATE UNIQUE INDEX mfa_recovery_code_sets_user_id_key ON auth.mfa_recovery_code_sets USING btree (user_id) |
| auth | mfa_recovery_codes | mfa_recovery_codes_pkey | CREATE UNIQUE INDEX mfa_recovery_codes_pkey ON auth.mfa_recovery_codes USING btree (id) |
| auth | mfa_recovery_codes | mfa_recovery_codes_set_id_idx | CREATE INDEX mfa_recovery_codes_set_id_idx ON auth.mfa_recovery_codes USING btree (mfa_recovery_code_set_id) |
| auth | oauth_authorizations | oauth_auth_pending_exp_idx | CREATE INDEX oauth_auth_pending_exp_idx ON auth.oauth_authorizations USING btree (expires_at) WHERE (status = 'pending'::auth.oauth_authorization_status) |
| auth | oauth_authorizations | oauth_authorizations_authorization_code_key | CREATE UNIQUE INDEX oauth_authorizations_authorization_code_key ON auth.oauth_authorizations USING btree (authorization_code) |
| auth | oauth_authorizations | oauth_authorizations_authorization_id_key | CREATE UNIQUE INDEX oauth_authorizations_authorization_id_key ON auth.oauth_authorizations USING btree (authorization_id) |
| auth | oauth_authorizations | oauth_authorizations_pkey | CREATE UNIQUE INDEX oauth_authorizations_pkey ON auth.oauth_authorizations USING btree (id) |
| auth | oauth_client_states | idx_oauth_client_states_created_at | CREATE INDEX idx_oauth_client_states_created_at ON auth.oauth_client_states USING btree (created_at) |
| auth | oauth_client_states | oauth_client_states_pkey | CREATE UNIQUE INDEX oauth_client_states_pkey ON auth.oauth_client_states USING btree (id) |
| auth | oauth_clients | oauth_clients_deleted_at_idx | CREATE INDEX oauth_clients_deleted_at_idx ON auth.oauth_clients USING btree (deleted_at) |
| auth | oauth_clients | oauth_clients_pkey | CREATE UNIQUE INDEX oauth_clients_pkey ON auth.oauth_clients USING btree (id) |
| auth | oauth_consents | oauth_consents_active_client_idx | CREATE INDEX oauth_consents_active_client_idx ON auth.oauth_consents USING btree (client_id) WHERE (revoked_at IS NULL) |
| auth | oauth_consents | oauth_consents_active_user_client_idx | CREATE INDEX oauth_consents_active_user_client_idx ON auth.oauth_consents USING btree (user_id, client_id) WHERE (revoked_at IS NULL) |
| auth | oauth_consents | oauth_consents_pkey | CREATE UNIQUE INDEX oauth_consents_pkey ON auth.oauth_consents USING btree (id) |
| auth | oauth_consents | oauth_consents_user_client_unique | CREATE UNIQUE INDEX oauth_consents_user_client_unique ON auth.oauth_consents USING btree (user_id, client_id) |
| auth | oauth_consents | oauth_consents_user_order_idx | CREATE INDEX oauth_consents_user_order_idx ON auth.oauth_consents USING btree (user_id, granted_at DESC) |
| auth | one_time_tokens | one_time_tokens_link_token_hash_hash_idx | CREATE INDEX one_time_tokens_link_token_hash_hash_idx ON auth.one_time_tokens USING hash (link_token_hash) |
| auth | one_time_tokens | one_time_tokens_pkey | CREATE UNIQUE INDEX one_time_tokens_pkey ON auth.one_time_tokens USING btree (id) |
| auth | one_time_tokens | one_time_tokens_relates_to_hash_idx | CREATE INDEX one_time_tokens_relates_to_hash_idx ON auth.one_time_tokens USING hash (relates_to) |
| auth | one_time_tokens | one_time_tokens_token_hash_hash_idx | CREATE INDEX one_time_tokens_token_hash_hash_idx ON auth.one_time_tokens USING hash (token_hash) |
| auth | one_time_tokens | one_time_tokens_user_id_token_type_key | CREATE UNIQUE INDEX one_time_tokens_user_id_token_type_key ON auth.one_time_tokens USING btree (user_id, token_type) |
| auth | refresh_tokens | refresh_tokens_instance_id_idx | CREATE INDEX refresh_tokens_instance_id_idx ON auth.refresh_tokens USING btree (instance_id) |
| auth | refresh_tokens | refresh_tokens_instance_id_user_id_idx | CREATE INDEX refresh_tokens_instance_id_user_id_idx ON auth.refresh_tokens USING btree (instance_id, user_id) |
| auth | refresh_tokens | refresh_tokens_parent_idx | CREATE INDEX refresh_tokens_parent_idx ON auth.refresh_tokens USING btree (parent) |
| auth | refresh_tokens | refresh_tokens_pkey | CREATE UNIQUE INDEX refresh_tokens_pkey ON auth.refresh_tokens USING btree (id) |
| auth | refresh_tokens | refresh_tokens_session_id_revoked_idx | CREATE INDEX refresh_tokens_session_id_revoked_idx ON auth.refresh_tokens USING btree (session_id, revoked) |
| auth | refresh_tokens | refresh_tokens_token_unique | CREATE UNIQUE INDEX refresh_tokens_token_unique ON auth.refresh_tokens USING btree (token) |
| auth | refresh_tokens | refresh_tokens_updated_at_idx | CREATE INDEX refresh_tokens_updated_at_idx ON auth.refresh_tokens USING btree (updated_at DESC) |
| auth | saml_providers | saml_providers_entity_id_key | CREATE UNIQUE INDEX saml_providers_entity_id_key ON auth.saml_providers USING btree (entity_id) |
| auth | saml_providers | saml_providers_pkey | CREATE UNIQUE INDEX saml_providers_pkey ON auth.saml_providers USING btree (id) |
| auth | saml_providers | saml_providers_sso_provider_id_idx | CREATE INDEX saml_providers_sso_provider_id_idx ON auth.saml_providers USING btree (sso_provider_id) |
| auth | saml_relay_states | saml_relay_states_created_at_idx | CREATE INDEX saml_relay_states_created_at_idx ON auth.saml_relay_states USING btree (created_at DESC) |
| auth | saml_relay_states | saml_relay_states_for_email_idx | CREATE INDEX saml_relay_states_for_email_idx ON auth.saml_relay_states USING btree (for_email) |
| auth | saml_relay_states | saml_relay_states_pkey | CREATE UNIQUE INDEX saml_relay_states_pkey ON auth.saml_relay_states USING btree (id) |
| auth | saml_relay_states | saml_relay_states_sso_provider_id_idx | CREATE INDEX saml_relay_states_sso_provider_id_idx ON auth.saml_relay_states USING btree (sso_provider_id) |
| auth | schema_migrations | schema_migrations_pkey | CREATE UNIQUE INDEX schema_migrations_pkey ON auth.schema_migrations USING btree (version) |
| auth | scim_tokens | scim_tokens_expires_at_idx | CREATE INDEX scim_tokens_expires_at_idx ON auth.scim_tokens USING btree (expires_at) |
| auth | scim_tokens | scim_tokens_pkey | CREATE UNIQUE INDEX scim_tokens_pkey ON auth.scim_tokens USING btree (id) |
| auth | scim_tokens | scim_tokens_revoked_at_idx | CREATE INDEX scim_tokens_revoked_at_idx ON auth.scim_tokens USING btree (revoked_at) |
| auth | scim_tokens | scim_tokens_sso_provider_id_idx | CREATE INDEX scim_tokens_sso_provider_id_idx ON auth.scim_tokens USING btree (sso_provider_id) |
| auth | scim_tokens | scim_tokens_token_hash_key | CREATE UNIQUE INDEX scim_tokens_token_hash_key ON auth.scim_tokens USING btree (token_hash) |
| auth | scim_users | scim_users_created_at_idx | CREATE INDEX scim_users_created_at_idx ON auth.scim_users USING btree (sso_provider_id, created_at, id) WHERE (deleted_at IS NULL) |
| auth | scim_users | scim_users_deleted_at_idx | CREATE INDEX scim_users_deleted_at_idx ON auth.scim_users USING btree (deleted_at) |
| auth | scim_users | scim_users_external_id_key | CREATE UNIQUE INDEX scim_users_external_id_key ON auth.scim_users USING btree (sso_provider_id, external_id) WHERE ((external_id IS NOT NULL) AND (deleted_at IS NULL)) |
| auth | scim_users | scim_users_id_idx | CREATE INDEX scim_users_id_idx ON auth.scim_users USING btree (sso_provider_id, id) WHERE (deleted_at IS NULL) |
| auth | scim_users | scim_users_pkey | CREATE UNIQUE INDEX scim_users_pkey ON auth.scim_users USING btree (id) |
| auth | scim_users | scim_users_sso_provider_id_idx | CREATE INDEX scim_users_sso_provider_id_idx ON auth.scim_users USING btree (sso_provider_id) |
| auth | scim_users | scim_users_updated_at_idx | CREATE INDEX scim_users_updated_at_idx ON auth.scim_users USING btree (sso_provider_id, updated_at, id) WHERE (deleted_at IS NULL) |
| auth | scim_users | scim_users_user_id_idx | CREATE INDEX scim_users_user_id_idx ON auth.scim_users USING btree (user_id) |
| auth | scim_users | scim_users_user_name_idx | CREATE INDEX scim_users_user_name_idx ON auth.scim_users USING btree (sso_provider_id, user_name COLLATE "C", id) WHERE (deleted_at IS NULL) |
| auth | scim_users | scim_users_user_name_key | CREATE UNIQUE INDEX scim_users_user_name_key ON auth.scim_users USING btree (sso_provider_id, user_name) WHERE (deleted_at IS NULL) |
| auth | sessions | sessions_not_after_idx | CREATE INDEX sessions_not_after_idx ON auth.sessions USING btree (not_after DESC) |
| auth | sessions | sessions_oauth_client_id_idx | CREATE INDEX sessions_oauth_client_id_idx ON auth.sessions USING btree (oauth_client_id) |
| auth | sessions | sessions_pkey | CREATE UNIQUE INDEX sessions_pkey ON auth.sessions USING btree (id) |
| auth | sessions | sessions_user_id_idx | CREATE INDEX sessions_user_id_idx ON auth.sessions USING btree (user_id) |
| auth | sessions | user_id_created_at_idx | CREATE INDEX user_id_created_at_idx ON auth.sessions USING btree (user_id, created_at) |
| auth | sso_domains | sso_domains_domain_idx | CREATE UNIQUE INDEX sso_domains_domain_idx ON auth.sso_domains USING btree (lower(domain)) |
| auth | sso_domains | sso_domains_pkey | CREATE UNIQUE INDEX sso_domains_pkey ON auth.sso_domains USING btree (id) |
| auth | sso_domains | sso_domains_sso_provider_id_idx | CREATE INDEX sso_domains_sso_provider_id_idx ON auth.sso_domains USING btree (sso_provider_id) |
| auth | sso_providers | sso_providers_pkey | CREATE UNIQUE INDEX sso_providers_pkey ON auth.sso_providers USING btree (id) |
| auth | sso_providers | sso_providers_resource_id_idx | CREATE UNIQUE INDEX sso_providers_resource_id_idx ON auth.sso_providers USING btree (lower(resource_id)) |
| auth | sso_providers | sso_providers_resource_id_pattern_idx | CREATE INDEX sso_providers_resource_id_pattern_idx ON auth.sso_providers USING btree (resource_id text_pattern_ops) |
| auth | users | confirmation_token_idx | CREATE UNIQUE INDEX confirmation_token_idx ON auth.users USING btree (confirmation_token) WHERE ((confirmation_token)::text !~ '^[0-9 ]*$'::text) |
| auth | users | email_change_token_current_idx | CREATE UNIQUE INDEX email_change_token_current_idx ON auth.users USING btree (email_change_token_current) WHERE ((email_change_token_current)::text !~ '^[0-9 ]*$'::text) |
| auth | users | email_change_token_new_idx | CREATE UNIQUE INDEX email_change_token_new_idx ON auth.users USING btree (email_change_token_new) WHERE ((email_change_token_new)::text !~ '^[0-9 ]*$'::text) |
| auth | users | idx_users_created_at_desc | CREATE INDEX idx_users_created_at_desc ON auth.users USING btree (created_at DESC) |
| auth | users | idx_users_email | CREATE INDEX idx_users_email ON auth.users USING btree (email) |
| auth | users | idx_users_last_sign_in_at_desc | CREATE INDEX idx_users_last_sign_in_at_desc ON auth.users USING btree (last_sign_in_at DESC) |
| auth | users | idx_users_name | CREATE INDEX idx_users_name ON auth.users USING btree (((raw_user_meta_data ->> 'name'::text))) WHERE ((raw_user_meta_data ->> 'name'::text) IS NOT NULL) |
| auth | users | reauthentication_token_idx | CREATE UNIQUE INDEX reauthentication_token_idx ON auth.users USING btree (reauthentication_token) WHERE ((reauthentication_token)::text !~ '^[0-9 ]*$'::text) |
| auth | users | recovery_token_idx | CREATE UNIQUE INDEX recovery_token_idx ON auth.users USING btree (recovery_token) WHERE ((recovery_token)::text !~ '^[0-9 ]*$'::text) |
| auth | users | users_email_partial_key | CREATE UNIQUE INDEX users_email_partial_key ON auth.users USING btree (email) WHERE (is_sso_user = false) |
| auth | users | users_instance_id_email_idx | CREATE INDEX users_instance_id_email_idx ON auth.users USING btree (instance_id, lower((email)::text)) |
| auth | users | users_instance_id_idx | CREATE INDEX users_instance_id_idx ON auth.users USING btree (instance_id) |
| auth | users | users_is_anonymous_idx | CREATE INDEX users_is_anonymous_idx ON auth.users USING btree (is_anonymous) |
| auth | users | users_phone_key | CREATE UNIQUE INDEX users_phone_key ON auth.users USING btree (phone) |
| auth | users | users_pkey | CREATE UNIQUE INDEX users_pkey ON auth.users USING btree (id) |
| auth | webauthn_challenges | webauthn_challenges_expires_at_idx | CREATE INDEX webauthn_challenges_expires_at_idx ON auth.webauthn_challenges USING btree (expires_at) |
| auth | webauthn_challenges | webauthn_challenges_pkey | CREATE UNIQUE INDEX webauthn_challenges_pkey ON auth.webauthn_challenges USING btree (id) |
| auth | webauthn_challenges | webauthn_challenges_user_id_idx | CREATE INDEX webauthn_challenges_user_id_idx ON auth.webauthn_challenges USING btree (user_id) |
| auth | webauthn_credentials | webauthn_credentials_credential_id_key | CREATE UNIQUE INDEX webauthn_credentials_credential_id_key ON auth.webauthn_credentials USING btree (credential_id) |
| auth | webauthn_credentials | webauthn_credentials_pkey | CREATE UNIQUE INDEX webauthn_credentials_pkey ON auth.webauthn_credentials USING btree (id) |
| auth | webauthn_credentials | webauthn_credentials_user_id_idx | CREATE INDEX webauthn_credentials_user_id_idx ON auth.webauthn_credentials USING btree (user_id) |

#### codebi

| schemaname | tablename | indexname | indexdef |
| --- | --- | --- | --- |
| codebi | alembic_version | alembic_version_pkc | CREATE UNIQUE INDEX alembic_version_pkc ON codebi.alembic_version USING btree (version_num) |
| codebi | audit_events | audit_client_time_idx | CREATE INDEX audit_client_time_idx ON codebi.audit_events USING btree (client_id, occurred_at DESC) |
| codebi | audit_events | audit_events_pkey | CREATE UNIQUE INDEX audit_events_pkey ON codebi.audit_events USING btree (id) |
| codebi | client_memberships | client_memberships_client_id_user_id_key | CREATE UNIQUE INDEX client_memberships_client_id_user_id_key ON codebi.client_memberships USING btree (client_id, user_id) |
| codebi | client_memberships | client_memberships_pkey | CREATE UNIQUE INDEX client_memberships_pkey ON codebi.client_memberships USING btree (id) |
| codebi | client_memberships | memberships_user_idx | CREATE INDEX memberships_user_idx ON codebi.client_memberships USING btree (user_id, client_id) |
| codebi | clients | clients_client_code_key | CREATE UNIQUE INDEX clients_client_code_key ON codebi.clients USING btree (client_code) |
| codebi | clients | clients_client_number_key | CREATE UNIQUE INDEX clients_client_number_key ON codebi.clients USING btree (client_number) |
| codebi | clients | clients_industry_idx | CREATE INDEX clients_industry_idx ON codebi.clients USING btree (industry_id) |
| codebi | clients | clients_pkey | CREATE UNIQUE INDEX clients_pkey ON codebi.clients USING btree (id) |
| codebi | countries | countries_pkey | CREATE UNIQUE INDEX countries_pkey ON codebi.countries USING btree (code) |
| codebi | industries | industries_name_key | CREATE UNIQUE INDEX industries_name_key ON codebi.industries USING btree (name) |
| codebi | industries | industries_pkey | CREATE UNIQUE INDEX industries_pkey ON codebi.industries USING btree (id) |
| codebi | login_events | login_events_pkey | CREATE UNIQUE INDEX login_events_pkey ON codebi.login_events USING btree (id) |
| codebi | login_events | login_events_user_time_idx | CREATE INDEX login_events_user_time_idx ON codebi.login_events USING btree (user_id, occurred_at DESC) |
| codebi | public_inquiries | public_inquiries_email_created | CREATE INDEX public_inquiries_email_created ON codebi.public_inquiries USING btree (email, created_at) |
| codebi | public_inquiries | public_inquiries_pkey | CREATE UNIQUE INDEX public_inquiries_pkey ON codebi.public_inquiries USING btree (id) |
| codebi | roles | roles_code_scope_key | CREATE UNIQUE INDEX roles_code_scope_key ON codebi.roles USING btree (code, scope) |
| codebi | roles | roles_pkey | CREATE UNIQUE INDEX roles_pkey ON codebi.roles USING btree (code) |
| codebi | sow_estimates | estimates_sow_idx | CREATE INDEX estimates_sow_idx ON codebi.sow_estimates USING btree (client_id, sow_id, created_at DESC) |
| codebi | sow_estimates | sow_estimates_client_id_sow_id_id_key | CREATE UNIQUE INDEX sow_estimates_client_id_sow_id_id_key ON codebi.sow_estimates USING btree (client_id, sow_id, id) |
| codebi | sow_estimates | sow_estimates_pkey | CREATE UNIQUE INDEX sow_estimates_pkey ON codebi.sow_estimates USING btree (id) |
| codebi | sow_estimates | sow_estimates_sow_id_revision_key | CREATE UNIQUE INDEX sow_estimates_sow_id_revision_key ON codebi.sow_estimates USING btree (sow_id, revision) |
| codebi | sow_estimates | sow_one_accepted_estimate | CREATE UNIQUE INDEX sow_one_accepted_estimate ON codebi.sow_estimates USING btree (sow_id) WHERE (status = 'accepted'::text) |
| codebi | sow_events | events_sow_idx | CREATE INDEX events_sow_idx ON codebi.sow_events USING btree (client_id, sow_id, occurred_at) |
| codebi | sow_events | sow_events_pkey | CREATE UNIQUE INDEX sow_events_pkey ON codebi.sow_events USING btree (id) |
| codebi | sow_messages | messages_sow_idx | CREATE INDEX messages_sow_idx ON codebi.sow_messages USING btree (client_id, sow_id, created_at) |
| codebi | sow_messages | sow_messages_client_id_sow_id_id_key | CREATE UNIQUE INDEX sow_messages_client_id_sow_id_id_key ON codebi.sow_messages USING btree (client_id, sow_id, id) |
| codebi | sow_messages | sow_messages_pkey | CREATE UNIQUE INDEX sow_messages_pkey ON codebi.sow_messages USING btree (id) |
| codebi | sow_tools | sow_tools_pkey | CREATE UNIQUE INDEX sow_tools_pkey ON codebi.sow_tools USING btree (sow_id, tool_id) |
| codebi | sow_tools | sow_tools_tool_idx | CREATE INDEX sow_tools_tool_idx ON codebi.sow_tools USING btree (tool_id) |
| codebi | sow_types | sow_types_name_key | CREATE UNIQUE INDEX sow_types_name_key ON codebi.sow_types USING btree (name) |
| codebi | sow_types | sow_types_pkey | CREATE UNIQUE INDEX sow_types_pkey ON codebi.sow_types USING btree (id) |
| codebi | state_regions | state_region_country_unique | CREATE UNIQUE INDEX state_region_country_unique ON codebi.state_regions USING btree (code, country_code) |
| codebi | state_regions | state_regions_country_name_idx | CREATE INDEX state_regions_country_name_idx ON codebi.state_regions USING btree (country_code, name) |
| codebi | state_regions | state_regions_pkey | CREATE UNIQUE INDEX state_regions_pkey ON codebi.state_regions USING btree (code) |
| codebi | statements_of_work | sows_client_status_idx | CREATE INDEX sows_client_status_idx ON codebi.statements_of_work USING btree (client_id, status, created_at DESC) |
| codebi | statements_of_work | sows_type_idx | CREATE INDEX sows_type_idx ON codebi.statements_of_work USING btree (sow_type_id) |
| codebi | statements_of_work | statements_of_work_client_id_id_key | CREATE UNIQUE INDEX statements_of_work_client_id_id_key ON codebi.statements_of_work USING btree (client_id, id) |
| codebi | statements_of_work | statements_of_work_pkey | CREATE UNIQUE INDEX statements_of_work_pkey ON codebi.statements_of_work USING btree (id) |
| codebi | statements_of_work | statements_of_work_sow_code_key | CREATE UNIQUE INDEX statements_of_work_sow_code_key ON codebi.statements_of_work USING btree (sow_code) |
| codebi | statements_of_work | statements_of_work_sow_number_key | CREATE UNIQUE INDEX statements_of_work_sow_number_key ON codebi.statements_of_work USING btree (sow_number) |
| codebi | test | test_pkey | CREATE UNIQUE INDEX test_pkey ON codebi.test USING btree (id) |
| codebi | time_entries | time_entries_pkey | CREATE UNIQUE INDEX time_entries_pkey ON codebi.time_entries USING btree (id) |
| codebi | time_entries | time_sow_idx | CREATE INDEX time_sow_idx ON codebi.time_entries USING btree (client_id, sow_id, status) |
| codebi | time_entries | time_worker_idx | CREATE INDEX time_worker_idx ON codebi.time_entries USING btree (worker_id, started_at) |
| codebi | timezones | timezones_pkey | CREATE UNIQUE INDEX timezones_pkey ON codebi.timezones USING btree (name) |
| codebi | tools | tools_name_key | CREATE UNIQUE INDEX tools_name_key ON codebi.tools USING btree (name) |
| codebi | tools | tools_pkey | CREATE UNIQUE INDEX tools_pkey ON codebi.tools USING btree (id) |
| codebi | user_invitations | invitations_email_idx | CREATE INDEX invitations_email_idx ON codebi.user_invitations USING btree (email, client_id) |
| codebi | user_invitations | user_invitations_pkey | CREATE UNIQUE INDEX user_invitations_pkey ON codebi.user_invitations USING btree (id) |
| codebi | user_invitations | user_invitations_token_hash_key | CREATE UNIQUE INDEX user_invitations_token_hash_key ON codebi.user_invitations USING btree (token_hash) |
| codebi | user_roles | user_roles_pkey | CREATE UNIQUE INDEX user_roles_pkey ON codebi.user_roles USING btree (user_id, role_code) |
| codebi | users | users_auth_user_id_key | CREATE UNIQUE INDEX users_auth_user_id_key ON codebi.users USING btree (auth_user_id) |
| codebi | users | users_email_key | CREATE UNIQUE INDEX users_email_key ON codebi.users USING btree (email) |
| codebi | users | users_pkey | CREATE UNIQUE INDEX users_pkey ON codebi.users USING btree (id) |

#### history

| schemaname | tablename | indexname | indexdef |
| --- | --- | --- | --- |
| history | knowledge_entries | knowledge_entries_pkey | CREATE UNIQUE INDEX knowledge_entries_pkey ON history.knowledge_entries USING btree (id) |

#### realtime

| schemaname | tablename | indexname | indexdef |
| --- | --- | --- | --- |
| realtime | messages | messages_pkey | CREATE UNIQUE INDEX messages_pkey ON ONLY realtime.messages USING btree (id, inserted_at) |
| realtime | messages | messages_replay_index | CREATE INDEX messages_replay_index ON ONLY realtime.messages USING btree (topic, inserted_at DESC, COALESCE(expires_at, 'infinity'::timestamp without time zone)) WHERE ((extension = 'broadcast'::text) AND (private = true)) |
| realtime | schema_migrations | schema_migrations_pkey | CREATE UNIQUE INDEX schema_migrations_pkey ON realtime.schema_migrations USING btree (version) |
| realtime | subscription | ix_realtime_subscription_entity | CREATE INDEX ix_realtime_subscription_entity ON realtime.subscription USING btree (entity) |
| realtime | subscription | pk_subscription | CREATE UNIQUE INDEX pk_subscription ON realtime.subscription USING btree (id) |
| realtime | subscription | subscription_subscription_id_entity_filters_hash_key | CREATE UNIQUE INDEX subscription_subscription_id_entity_filters_hash_key ON realtime.subscription USING btree (subscription_id, entity, realtime.filters_hash(filters), action_filter, COALESCE(selected_columns, '{}'::text[])) |

#### storage

| schemaname | tablename | indexname | indexdef |
| --- | --- | --- | --- |
| storage | bucket_lifecycle_states | bucket_lifecycle_states_due_idx | CREATE INDEX bucket_lifecycle_states_due_idx ON storage.bucket_lifecycle_states USING btree (next_run_at, bucket_id) WHERE (next_run_at IS NOT NULL) |
| storage | bucket_lifecycle_states | bucket_lifecycle_states_pkey | CREATE UNIQUE INDEX bucket_lifecycle_states_pkey ON storage.bucket_lifecycle_states USING btree (bucket_id) |
| storage | buckets | bname | CREATE UNIQUE INDEX bname ON storage.buckets USING btree (name) |
| storage | buckets | buckets_pkey | CREATE UNIQUE INDEX buckets_pkey ON storage.buckets USING btree (id) |
| storage | buckets_analytics | buckets_analytics_pkey | CREATE UNIQUE INDEX buckets_analytics_pkey ON storage.buckets_analytics USING btree (id) |
| storage | buckets_analytics | buckets_analytics_unique_name_idx | CREATE UNIQUE INDEX buckets_analytics_unique_name_idx ON storage.buckets_analytics USING btree (name) WHERE (deleted_at IS NULL) |
| storage | buckets_vectors | buckets_vectors_pkey | CREATE UNIQUE INDEX buckets_vectors_pkey ON storage.buckets_vectors USING btree (id) |
| storage | lifecycle_abandoned_artifacts | lifecycle_abandoned_artifacts_attempted_at_idx | CREATE INDEX lifecycle_abandoned_artifacts_attempted_at_idx ON storage.lifecycle_abandoned_artifacts USING btree (attempted_at) |
| storage | lifecycle_abandoned_artifacts | lifecycle_abandoned_artifacts_pkey | CREATE UNIQUE INDEX lifecycle_abandoned_artifacts_pkey ON storage.lifecycle_abandoned_artifacts USING btree (bucket_id, key) |
| storage | migrations | migrations_name_key | CREATE UNIQUE INDEX migrations_name_key ON storage.migrations USING btree (name) |
| storage | migrations | migrations_pkey | CREATE UNIQUE INDEX migrations_pkey ON storage.migrations USING btree (id) |
| storage | objects | idx_objects_bucket_id_name | CREATE INDEX idx_objects_bucket_id_name ON storage.objects USING btree (bucket_id, name COLLATE "C") |
| storage | objects | idx_objects_bucket_id_name_lower | CREATE INDEX idx_objects_bucket_id_name_lower ON storage.objects USING btree (bucket_id, lower(name) COLLATE "C") |
| storage | objects | idx_objects_current_version | CREATE UNIQUE INDEX idx_objects_current_version ON storage.objects USING btree (bucket_id, name COLLATE "C") WHERE (archived_at IS NULL) |
| storage | objects | idx_objects_delete_markers | CREATE INDEX idx_objects_delete_markers ON storage.objects USING btree (bucket_id, name COLLATE "C") WHERE is_delete_marker |
| storage | objects | idx_objects_null_version | CREATE UNIQUE INDEX idx_objects_null_version ON storage.objects USING btree (bucket_id, name COLLATE "C") WHERE (NOT is_versioned) |
| storage | objects | name_prefix_search | CREATE INDEX name_prefix_search ON storage.objects USING btree (name text_pattern_ops) |
| storage | objects | objects_archived_due_idx | CREATE INDEX objects_archived_due_idx ON storage.objects USING btree (bucket_id, archived_at, name COLLATE "C") INCLUDE (id, version, is_delete_marker) WHERE (archived_at IS NOT NULL) |
| storage | objects | objects_archived_order_uq | CREATE UNIQUE INDEX objects_archived_order_uq ON storage.objects USING btree (bucket_id, name COLLATE "C", archived_at) WHERE (archived_at IS NOT NULL) |
| storage | objects | objects_bucket_id_name_version_key | CREATE UNIQUE INDEX objects_bucket_id_name_version_key ON storage.objects USING btree (bucket_id, name COLLATE "C", version) NULLS NOT DISTINCT |
| storage | objects | objects_pkey | CREATE UNIQUE INDEX objects_pkey ON storage.objects USING btree (id) |
| storage | s3_multipart_uploads | idx_multipart_uploads_list | CREATE INDEX idx_multipart_uploads_list ON storage.s3_multipart_uploads USING btree (bucket_id, key, created_at) |
| storage | s3_multipart_uploads | s3_multipart_uploads_pkey | CREATE UNIQUE INDEX s3_multipart_uploads_pkey ON storage.s3_multipart_uploads USING btree (id) |
| storage | s3_multipart_uploads_parts | idx_multipart_uploads_parts_upload_part | CREATE INDEX idx_multipart_uploads_parts_upload_part ON storage.s3_multipart_uploads_parts USING btree (upload_id, part_number) |
| storage | s3_multipart_uploads_parts | s3_multipart_uploads_parts_pkey | CREATE UNIQUE INDEX s3_multipart_uploads_parts_pkey ON storage.s3_multipart_uploads_parts USING btree (id) |
| storage | vector_indexes | vector_indexes_name_bucket_id_idx | CREATE UNIQUE INDEX vector_indexes_name_bucket_id_idx ON storage.vector_indexes USING btree (name, bucket_id) |
| storage | vector_indexes | vector_indexes_pkey | CREATE UNIQUE INDEX vector_indexes_pkey ON storage.vector_indexes USING btree (id) |

#### vault

| schemaname | tablename | indexname | indexdef |
| --- | --- | --- | --- |
| vault | secrets | secrets_name_idx | CREATE UNIQUE INDEX secrets_name_idx ON vault.secrets USING btree (name) WHERE (name IS NOT NULL) |
| vault | secrets | secrets_pkey | CREATE UNIQUE INDEX secrets_pkey ON vault.secrets USING btree (id) |


### Policies

#### codebi

| schemaname | tablename | policyname | permissive | roles | cmd | using_expression | with_check |
| --- | --- | --- | --- | --- | --- | --- | --- |
| codebi | audit_events | flask_backend_access | PERMISSIVE | ['codebi_app'] | ALL | true | true |
| codebi | client_memberships | flask_backend_access | PERMISSIVE | ['codebi_app'] | ALL | true | true |
| codebi | clients | flask_backend_access | PERMISSIVE | ['codebi_app'] | ALL | true | true |
| codebi | countries | flask_catalog_read | PERMISSIVE | ['codebi_app'] | SELECT | true | — |
| codebi | industries | flask_backend_access | PERMISSIVE | ['codebi_app'] | ALL | true | true |
| codebi | login_events | flask_backend_access | PERMISSIVE | ['codebi_app'] | ALL | true | true |
| codebi | public_inquiries | flask_public_inquiry | PERMISSIVE | ['codebi_app'] | ALL | true | true |
| codebi | roles | flask_backend_access | PERMISSIVE | ['codebi_app'] | ALL | true | true |
| codebi | sow_estimates | flask_scope_review | PERMISSIVE | ['codebi_app'] | ALL | true | true |
| codebi | sow_events | flask_sow_access | PERMISSIVE | ['codebi_app'] | ALL | true | true |
| codebi | sow_messages | flask_scope_review | PERMISSIVE | ['codebi_app'] | ALL | true | true |
| codebi | sow_tools | flask_sow_access | PERMISSIVE | ['codebi_app'] | ALL | true | true |
| codebi | sow_types | flask_sow_access | PERMISSIVE | ['codebi_app'] | ALL | true | true |
| codebi | state_regions | flask_catalog_read | PERMISSIVE | ['codebi_app'] | SELECT | true | — |
| codebi | statements_of_work | flask_sow_access | PERMISSIVE | ['codebi_app'] | ALL | true | true |
| codebi | time_entries | flask_scope_review | PERMISSIVE | ['codebi_app'] | ALL | true | true |
| codebi | timezones | flask_catalog_read | PERMISSIVE | ['codebi_app'] | SELECT | true | — |
| codebi | tools | flask_sow_access | PERMISSIVE | ['codebi_app'] | ALL | true | true |
| codebi | user_roles | flask_backend_access | PERMISSIVE | ['codebi_app'] | ALL | true | true |
| codebi | users | flask_backend_access | PERMISSIVE | ['codebi_app'] | ALL | true | true |

#### storage

| schemaname | tablename | policyname | permissive | roles | cmd | using_expression | with_check |
| --- | --- | --- | --- | --- | --- | --- | --- |
| storage | objects | client_branding_insert | PERMISSIVE | ['authenticated'] | INSERT | — | ((bucket_id = 'client-branding'::text) AND codebi_branding.can_access(name, true)) |
| storage | objects | client_branding_read | PERMISSIVE | ['authenticated'] | SELECT | ((bucket_id = 'client-branding'::text) AND codebi_branding.can_access(name, false)) | — |
| storage | objects | client_branding_update | PERMISSIVE | ['authenticated'] | UPDATE | ((bucket_id = 'client-branding'::text) AND codebi_branding.can_access(name, true)) | ((bucket_id = 'client-branding'::text) AND codebi_branding.can_access(name, true)) |


### Roles

| rolname | rolsuper | rolinherit | rolcreaterole | rolcreatedb | rolcanlogin | rolbypassrls |
| --- | --- | --- | --- | --- | --- | --- |
| anon | no | yes | no | no | no | no |
| authenticated | no | yes | no | no | no | no |
| authenticator | no | no | no | no | yes | no |
| codebi_app | no | no | no | no | yes | no |
| dashboard_user | no | yes | yes | yes | no | no |
| pgbouncer | no | yes | no | no | yes | no |
| postgres | no | yes | yes | yes | yes | yes |
| service_role | no | yes | no | no | no | yes |
| supabase_admin | yes | yes | yes | yes | yes | yes |
| supabase_auth_admin | no | no | yes | no | yes | no |
| supabase_etl_admin | no | yes | no | no | yes | yes |
| supabase_privileged_role | no | yes | no | no | no | no |
| supabase_read_only_user | no | yes | no | no | yes | yes |
| supabase_realtime_admin | no | no | no | no | no | no |
| supabase_replication_admin | no | yes | no | no | yes | no |
| supabase_storage_admin | no | no | yes | no | yes | no |


### Role memberships

| granted_role | member | admin_option | inherit_option | set_option |
| --- | --- | --- | --- | --- |
| anon | authenticator | no | no | yes |
| anon | postgres | yes | yes | yes |
| authenticated | authenticator | no | no | yes |
| authenticated | postgres | yes | yes | yes |
| authenticator | postgres | yes | yes | yes |
| authenticator | supabase_storage_admin | no | no | yes |
| codebi_app | postgres | yes | no | no |
| pg_create_subscription | postgres | yes | yes | yes |
| pg_monitor | postgres | yes | yes | yes |
| pg_monitor | supabase_etl_admin | no | yes | yes |
| pg_monitor | supabase_read_only_user | no | yes | yes |
| pg_read_all_data | postgres | yes | yes | yes |
| pg_read_all_data | supabase_etl_admin | no | yes | yes |
| pg_read_all_data | supabase_read_only_user | no | yes | yes |
| pg_signal_backend | postgres | yes | yes | yes |
| service_role | authenticator | no | no | yes |
| service_role | postgres | yes | yes | yes |
| supabase_privileged_role | postgres | no | yes | yes |
| supabase_privileged_role | supabase_etl_admin | no | yes | yes |


### Routines

#### auth

| schema | routine | arguments | returns | owner | security_definer | acl | kind |
| --- | --- | --- | --- | --- | --- | --- | --- |
| auth | email | — | text | supabase_auth_admin | no | ['=X/supabase_auth_admin', 'supabase_auth_admin=X/supabase_auth_admin', 'dashboard_user=X/supabase_auth_admin'] | f |
| auth | jwt | — | jsonb | supabase_auth_admin | no | ['=X/supabase_auth_admin', 'postgres=X/supabase_auth_admin', 'supabase_auth_admin=X/supabase_auth_admin', 'dashboard_user=X/supabase_auth_admin'] | f |
| auth | role | — | text | supabase_auth_admin | no | ['=X/supabase_auth_admin', 'supabase_auth_admin=X/supabase_auth_admin', 'dashboard_user=X/supabase_auth_admin'] | f |
| auth | uid | — | uuid | supabase_auth_admin | no | ['=X/supabase_auth_admin', 'supabase_auth_admin=X/supabase_auth_admin', 'dashboard_user=X/supabase_auth_admin'] | f |

#### codebi

| schema | routine | arguments | returns | owner | security_definer | acl | kind |
| --- | --- | --- | --- | --- | --- | --- | --- |
| codebi | portal_identity_context | p_auth_id uuid | jsonb | postgres | no | ['postgres=X/postgres'] | f |
| codebi | reject_history_change | — | trigger | postgres | no | ['postgres=X/postgres'] | f |
| codebi | set_updated_at | — | trigger | postgres | no | ['postgres=X/postgres'] | f |

#### codebi_branding

| schema | routine | arguments | returns | owner | security_definer | acl | kind |
| --- | --- | --- | --- | --- | --- | --- | --- |
| codebi_branding | can_access | object_name text, writing boolean | boolean | postgres | yes | ['postgres=X/postgres', 'authenticated=X/postgres'] | f |

#### extensions

| schema | routine | arguments | returns | owner | security_definer | acl | kind |
| --- | --- | --- | --- | --- | --- | --- | --- |
| extensions | armor | bytea | text | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | armor | bytea, text[], text[] | text | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | crypt | text, text | text | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | dearmor | text | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | decrypt | bytea, bytea, text | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | decrypt_iv | bytea, bytea, bytea, text | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | digest | bytea, text | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | digest | text, text | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | encrypt | bytea, bytea, text | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | encrypt_iv | bytea, bytea, bytea, text | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | gen_random_bytes | integer | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | gen_random_uuid | — | uuid | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | gen_salt | text | text | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | gen_salt | text, integer | text | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | grant_pg_cron_access | — | event_trigger | supabase_admin | no | ['=X/supabase_admin', 'supabase_admin=X*/supabase_admin', 'dashboard_user=X/supabase_admin'] | f |
| extensions | grant_pg_graphql_access | — | event_trigger | supabase_admin | no | ['=X/supabase_admin', 'supabase_admin=X/supabase_admin', 'postgres=X*/supabase_admin'] | f |
| extensions | grant_pg_net_access | — | event_trigger | supabase_admin | no | ['=X/supabase_admin', 'supabase_admin=X*/supabase_admin', 'dashboard_user=X/supabase_admin'] | f |
| extensions | hmac | bytea, bytea, text | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | hmac | text, text, text | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pg_stat_statements | showtext boolean, OUT userid oid, OUT dbid oid, OUT toplevel boolean, OUT queryid bigint, OUT query text, OUT plans bigint, OUT total_plan_time double precision, OUT min_plan_time double precision, OUT max_plan_time double precision, OUT mean_plan_time double precision, OUT stddev_plan_time double precision, OUT calls bigint, OUT total_exec_time double precision, OUT min_exec_time double precision, OUT max_exec_time double precision, OUT mean_exec_time double precision, OUT stddev_exec_time double precision, OUT rows bigint, OUT shared_blks_hit bigint, OUT shared_blks_read bigint, OUT shared_blks_dirtied bigint, OUT shared_blks_written bigint, OUT local_blks_hit bigint, OUT local_blks_read bigint, OUT local_blks_dirtied bigint, OUT local_blks_written bigint, OUT temp_blks_read bigint, OUT temp_blks_written bigint, OUT shared_blk_read_time double precision, OUT shared_blk_write_time double precision, OUT local_blk_read_time double precision, OUT local_blk_write_time double precision, OUT temp_blk_read_time double precision, OUT temp_blk_write_time double precision, OUT wal_records bigint, OUT wal_fpi bigint, OUT wal_bytes numeric, OUT jit_functions bigint, OUT jit_generation_time double precision, OUT jit_inlining_count bigint, OUT jit_inlining_time double precision, OUT jit_optimization_count bigint, OUT jit_optimization_time double precision, OUT jit_emission_count bigint, OUT jit_emission_time double precision, OUT jit_deform_count bigint, OUT jit_deform_time double precision, OUT stats_since timestamp with time zone, OUT minmax_stats_since timestamp with time zone | SETOF record | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pg_stat_statements_info | OUT dealloc bigint, OUT stats_reset timestamp with time zone | record | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pg_stat_statements_reset | userid oid, dbid oid, queryid bigint, minmax_only boolean | timestamp with time zone | postgres | no | ['postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgp_armor_headers | text, OUT key text, OUT value text | SETOF record | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgp_key_id | bytea | text | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgp_pub_decrypt | bytea, bytea | text | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgp_pub_decrypt | bytea, bytea, text | text | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgp_pub_decrypt | bytea, bytea, text, text | text | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgp_pub_decrypt_bytea | bytea, bytea | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgp_pub_decrypt_bytea | bytea, bytea, text | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgp_pub_decrypt_bytea | bytea, bytea, text, text | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgp_pub_encrypt | text, bytea | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgp_pub_encrypt | text, bytea, text | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgp_pub_encrypt_bytea | bytea, bytea | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgp_pub_encrypt_bytea | bytea, bytea, text | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgp_sym_decrypt | bytea, text | text | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgp_sym_decrypt | bytea, text, text | text | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgp_sym_decrypt_bytea | bytea, text | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgp_sym_decrypt_bytea | bytea, text, text | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgp_sym_encrypt | text, text | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgp_sym_encrypt | text, text, text | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgp_sym_encrypt_bytea | bytea, text | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgp_sym_encrypt_bytea | bytea, text, text | bytea | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | pgrst_ddl_watch | — | event_trigger | supabase_admin | no | ['=X/supabase_admin', 'supabase_admin=X/supabase_admin', 'postgres=X*/supabase_admin'] | f |
| extensions | pgrst_drop_watch | — | event_trigger | supabase_admin | no | ['=X/supabase_admin', 'supabase_admin=X/supabase_admin', 'postgres=X*/supabase_admin'] | f |
| extensions | set_graphql_placeholder | — | event_trigger | supabase_admin | no | ['=X/supabase_admin', 'supabase_admin=X/supabase_admin', 'postgres=X*/supabase_admin'] | f |
| extensions | uuid_generate_v1 | — | uuid | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | uuid_generate_v1mc | — | uuid | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | uuid_generate_v3 | namespace uuid, name text | uuid | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | uuid_generate_v4 | — | uuid | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | uuid_generate_v5 | namespace uuid, name text | uuid | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | uuid_nil | — | uuid | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | uuid_ns_dns | — | uuid | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | uuid_ns_oid | — | uuid | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | uuid_ns_url | — | uuid | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |
| extensions | uuid_ns_x500 | — | uuid | postgres | no | ['=X/postgres', 'postgres=X*/postgres', 'dashboard_user=X/postgres'] | f |

#### graphql_public

| schema | routine | arguments | returns | owner | security_definer | acl | kind |
| --- | --- | --- | --- | --- | --- | --- | --- |
| graphql_public | graphql | "operationName" text, query text, variables jsonb, extensions jsonb | jsonb | supabase_admin | no | ['=X/supabase_admin', 'supabase_admin=X/supabase_admin', 'postgres=X/supabase_admin', 'anon=X/supabase_admin', 'authenticated=X/supabase_admin', 'service_role=X/supabase_admin'] | f |

#### pgbouncer

| schema | routine | arguments | returns | owner | security_definer | acl | kind |
| --- | --- | --- | --- | --- | --- | --- | --- |
| pgbouncer | get_auth | p_usename text | TABLE(username text, password text) | supabase_admin | yes | ['supabase_admin=X/supabase_admin', 'pgbouncer=X/supabase_admin'] | f |

#### public

| schema | routine | arguments | returns | owner | security_definer | acl | kind |
| --- | --- | --- | --- | --- | --- | --- | --- |
| public | codebi_portal_context | — | jsonb | postgres | yes | ['postgres=X/postgres', 'service_role=X/postgres', 'authenticated=X/postgres'] | f |
| public | codebi_portal_login | p_provider text | jsonb | postgres | yes | ['postgres=X/postgres', 'service_role=X/postgres', 'authenticated=X/postgres'] | f |

#### realtime

| schema | routine | arguments | returns | owner | security_definer | acl | kind |
| --- | --- | --- | --- | --- | --- | --- | --- |
| realtime | apply_rls | wal jsonb, max_record_bytes integer | SETOF realtime.wal_rls | supabase_realtime_admin | no | ['=X/supabase_realtime_admin', 'supabase_realtime_admin=X/supabase_realtime_admin', 'postgres=X/supabase_realtime_admin', 'dashboard_user=X/supabase_realtime_admin', 'anon=X/supabase_realtime_admin', 'authenticated=X/supabase_realtime_admin', 'service_role=X/supabase_realtime_admin'] | f |
| realtime | authorize | role_name text, topic_name text, claims text, sub text, headers text, read_extensions text[], write_extensions text[], OUT read_allowed boolean[], OUT write_allowed boolean[] | record | supabase_realtime_admin | no | ['=X/supabase_realtime_admin', 'supabase_realtime_admin=X/supabase_realtime_admin', 'postgres=X/supabase_realtime_admin', 'dashboard_user=X/supabase_realtime_admin'] | f |
| realtime | broadcast_changes | topic_name text, event_name text, operation text, table_name text, table_schema text, new record, old record, level text | void | supabase_realtime_admin | no | ['=X/supabase_realtime_admin', 'supabase_realtime_admin=X/supabase_realtime_admin', 'postgres=X/supabase_realtime_admin', 'dashboard_user=X/supabase_realtime_admin'] | f |
| realtime | build_prepared_statement_sql | prepared_statement_name text, entity regclass, columns realtime.wal_column[] | text | supabase_realtime_admin | no | ['=X/supabase_realtime_admin', 'supabase_realtime_admin=X/supabase_realtime_admin', 'postgres=X/supabase_realtime_admin', 'dashboard_user=X/supabase_realtime_admin', 'anon=X/supabase_realtime_admin', 'authenticated=X/supabase_realtime_admin', 'service_role=X/supabase_realtime_admin'] | f |
| realtime | cast | val text, type_ regtype | jsonb | supabase_realtime_admin | no | ['=X/supabase_realtime_admin', 'supabase_realtime_admin=X/supabase_realtime_admin', 'postgres=X/supabase_realtime_admin', 'dashboard_user=X/supabase_realtime_admin', 'anon=X/supabase_realtime_admin', 'authenticated=X/supabase_realtime_admin', 'service_role=X/supabase_realtime_admin'] | f |
| realtime | check_equality_op | op realtime.equality_op, type_ regtype, val_1 text, val_2 text | boolean | supabase_realtime_admin | no | ['=X/supabase_realtime_admin', 'supabase_realtime_admin=X/supabase_realtime_admin', 'postgres=X/supabase_realtime_admin', 'dashboard_user=X/supabase_realtime_admin', 'anon=X/supabase_realtime_admin', 'authenticated=X/supabase_realtime_admin', 'service_role=X/supabase_realtime_admin'] | f |
| realtime | check_equality_op | op realtime.equality_op, type_ regtype, val_1 text, val_2 text, negate boolean | boolean | supabase_realtime_admin | no | ['=X/supabase_realtime_admin', 'supabase_realtime_admin=X/supabase_realtime_admin', 'postgres=X/supabase_realtime_admin', 'dashboard_user=X/supabase_realtime_admin', 'anon=X/supabase_realtime_admin', 'authenticated=X/supabase_realtime_admin', 'service_role=X/supabase_realtime_admin'] | f |
| realtime | filters_hash | filters realtime.user_defined_filter[] | bytea | supabase_realtime_admin | no | ['=X/supabase_realtime_admin', 'supabase_realtime_admin=X/supabase_realtime_admin', 'postgres=X/supabase_realtime_admin', 'dashboard_user=X/supabase_realtime_admin', 'anon=X/supabase_realtime_admin', 'authenticated=X/supabase_realtime_admin', 'service_role=X/supabase_realtime_admin'] | f |
| realtime | is_visible_through_filters | columns realtime.wal_column[], filters realtime.user_defined_filter[] | boolean | supabase_realtime_admin | no | ['=X/supabase_realtime_admin', 'supabase_realtime_admin=X/supabase_realtime_admin', 'postgres=X/supabase_realtime_admin', 'dashboard_user=X/supabase_realtime_admin', 'anon=X/supabase_realtime_admin', 'authenticated=X/supabase_realtime_admin', 'service_role=X/supabase_realtime_admin'] | f |
| realtime | list_changes | publication name, slot_name name, max_changes integer, max_record_bytes integer | TABLE(wal jsonb, is_rls_enabled boolean, subscription_ids uuid[], errors text[], slot_changes_count bigint) | supabase_realtime_admin | no | ['=X/supabase_realtime_admin', 'supabase_realtime_admin=X/supabase_realtime_admin', 'postgres=X/supabase_realtime_admin', 'dashboard_user=X/supabase_realtime_admin'] | f |
| realtime | list_changes_sync | publication name, slot_name name, max_changes integer, max_record_bytes integer | TABLE(wal jsonb, is_rls_enabled boolean, subscription_ids uuid[], errors text[], slot_changes_count bigint) | supabase_realtime_admin | no | ['=X/supabase_realtime_admin', 'supabase_realtime_admin=X/supabase_realtime_admin', 'postgres=X/supabase_realtime_admin', 'dashboard_user=X/supabase_realtime_admin'] | f |
| realtime | quote_wal2json | entity regclass | text | supabase_realtime_admin | no | ['=X/supabase_realtime_admin', 'supabase_realtime_admin=X/supabase_realtime_admin', 'postgres=X/supabase_realtime_admin', 'dashboard_user=X/supabase_realtime_admin', 'anon=X/supabase_realtime_admin', 'authenticated=X/supabase_realtime_admin', 'service_role=X/supabase_realtime_admin'] | f |
| realtime | send | payload jsonb, event text, topic text, private boolean | void | supabase_realtime_admin | no | ['=X/supabase_realtime_admin', 'supabase_realtime_admin=X/supabase_realtime_admin', 'postgres=X/supabase_realtime_admin', 'dashboard_user=X/supabase_realtime_admin'] | f |
| realtime | send_binary | payload bytea, event text, topic text, private boolean | void | supabase_realtime_admin | no | ['=X/supabase_realtime_admin', 'supabase_realtime_admin=X/supabase_realtime_admin', 'postgres=X/supabase_realtime_admin', 'dashboard_user=X/supabase_realtime_admin'] | f |
| realtime | settled_changes | slot_name name, max_changes integer, VARIADIC opts text[] | TABLE(lsn pg_lsn, xid xid, data text) | supabase_realtime_admin | no | ['=X/supabase_realtime_admin', 'supabase_realtime_admin=X/supabase_realtime_admin', 'postgres=X/supabase_realtime_admin', 'dashboard_user=X/supabase_realtime_admin'] | f |
| realtime | subscription_check_filters | — | trigger | supabase_realtime_admin | no | ['=X/supabase_realtime_admin', 'supabase_realtime_admin=X/supabase_realtime_admin', 'postgres=X/supabase_realtime_admin', 'dashboard_user=X/supabase_realtime_admin', 'anon=X/supabase_realtime_admin', 'authenticated=X/supabase_realtime_admin', 'service_role=X/supabase_realtime_admin'] | f |
| realtime | to_regrole | role_name text | regrole | supabase_realtime_admin | no | ['=X/supabase_realtime_admin', 'supabase_realtime_admin=X/supabase_realtime_admin', 'postgres=X/supabase_realtime_admin', 'dashboard_user=X/supabase_realtime_admin', 'anon=X/supabase_realtime_admin', 'authenticated=X/supabase_realtime_admin', 'service_role=X/supabase_realtime_admin'] | f |
| realtime | topic | — | text | supabase_realtime_admin | no | ['=X/supabase_realtime_admin', 'supabase_realtime_admin=X/supabase_realtime_admin', 'postgres=X/supabase_realtime_admin', 'dashboard_user=X/supabase_realtime_admin'] | f |
| realtime | wal2json_escape_identifier | name text | text | supabase_realtime_admin | no | ['=X/supabase_realtime_admin', 'supabase_realtime_admin=X/supabase_realtime_admin', 'postgres=X/supabase_realtime_admin', 'dashboard_user=X/supabase_realtime_admin'] | f |

#### storage

| schema | routine | arguments | returns | owner | security_definer | acl | kind |
| --- | --- | --- | --- | --- | --- | --- | --- |
| storage | allow_any_operation | expected_operations text[] | boolean | supabase_storage_admin | no | — | f |
| storage | allow_only_operation | expected_operation text | boolean | supabase_storage_admin | no | — | f |
| storage | can_insert_object | bucketid text, name text, owner uuid, metadata jsonb | void | supabase_storage_admin | no | — | f |
| storage | delete_lifecycle_objects | object_ids uuid[] | TABLE(id uuid, is_delete_marker boolean, metadata jsonb) | supabase_storage_admin | no | ['supabase_storage_admin=X/supabase_storage_admin', 'service_role=X/supabase_storage_admin'] | f |
| storage | enforce_bucket_lifecycle_service_role | — | trigger | supabase_storage_admin | no | — | f |
| storage | enforce_bucket_name_length | — | trigger | supabase_storage_admin | no | — | f |
| storage | enforce_versioning_status_transition | — | trigger | supabase_storage_admin | no | — | f |
| storage | extension | name text | text | supabase_storage_admin | no | — | f |
| storage | filename | name text | text | supabase_storage_admin | no | — | f |
| storage | foldername | name text | text[] | supabase_storage_admin | no | — | f |
| storage | get_common_prefix | p_key text, p_prefix text, p_delimiter text | text | supabase_storage_admin | no | — | f |
| storage | get_size_by_bucket | noncurrent_versions text, delete_markers text | TABLE(size bigint, bucket_id text) | supabase_storage_admin | no | — | f |
| storage | list_multipart_uploads_with_delimiter | bucket_id text, prefix_param text, delimiter_param text, max_keys integer, next_key_token text, next_upload_token text, raw_prefix_param text | TABLE(key text, id text, created_at timestamp with time zone) | supabase_storage_admin | no | — | f |
| storage | list_objects_with_delimiter | _bucket_id text, prefix_param text, delimiter_param text, max_keys integer, start_after text, next_token text, sort_order text, noncurrent_versions text, delete_markers text, next_token_archived_at timestamp with time zone, next_token_version text | TABLE(name text, id uuid, metadata jsonb, updated_at timestamp with time zone, created_at timestamp with time zone, last_accessed_at timestamp with time zone, version text, archived_at timestamp with time zone, is_delete_marker boolean, is_versioned boolean) | supabase_storage_admin | no | — | f |
| storage | operation | — | text | supabase_storage_admin | no | — | f |
| storage | protect_active_lifecycle_states | — | trigger | supabase_storage_admin | yes | ['supabase_storage_admin=X/supabase_storage_admin'] | f |
| storage | protect_bucket_control_columns | — | trigger | supabase_storage_admin | no | — | f |
| storage | protect_delete | — | trigger | supabase_storage_admin | no | — | f |
| storage | reconcile_lifecycle_state | target_bucket_id text, policy_changed boolean | void | supabase_storage_admin | yes | ['supabase_storage_admin=X/supabase_storage_admin', 'service_role=X/supabase_storage_admin'] | f |
| storage | search | prefix text, bucketname text, limits integer, levels integer, offsets integer, search text, sortcolumn text, sortorder text, noncurrent_versions text, delete_markers text | TABLE(name text, id uuid, updated_at timestamp with time zone, created_at timestamp with time zone, last_accessed_at timestamp with time zone, metadata jsonb, version text, archived_at timestamp with time zone, is_delete_marker boolean, is_versioned boolean) | supabase_storage_admin | no | — | f |
| storage | search_by_timestamp | p_prefix text, p_bucket_id text, p_limit integer, p_level integer, p_start_after text, p_sort_order text, p_sort_column text, p_sort_column_after text, noncurrent_versions text, delete_markers text, p_start_after_version text | TABLE(key text, name text, id uuid, updated_at timestamp with time zone, created_at timestamp with time zone, last_accessed_at timestamp with time zone, metadata jsonb, version text, archived_at timestamp with time zone, is_delete_marker boolean, is_versioned boolean) | supabase_storage_admin | no | — | f |
| storage | search_v2 | prefix text, bucket_name text, limits integer, levels integer, start_after text, sort_order text, sort_column text, sort_column_after text, noncurrent_versions text, delete_markers text, start_after_archived_at timestamp with time zone, start_after_version text, start_after_is_continuation boolean | TABLE(key text, name text, id uuid, updated_at timestamp with time zone, created_at timestamp with time zone, last_accessed_at timestamp with time zone, metadata jsonb, version text, archived_at timestamp with time zone, is_delete_marker boolean, is_versioned boolean) | supabase_storage_admin | no | — | f |
| storage | update_updated_at_column | — | trigger | supabase_storage_admin | no | — | f |
| storage | wake_lifecycle_state | — | trigger | supabase_storage_admin | yes | ['supabase_storage_admin=X/supabase_storage_admin'] | f |

#### vault

| schema | routine | arguments | returns | owner | security_definer | acl | kind |
| --- | --- | --- | --- | --- | --- | --- | --- |
| vault | _crypto_aead_det_decrypt | message bytea, additional bytea, key_id bigint, context bytea, nonce bytea | bytea | supabase_admin | no | ['supabase_admin=X/supabase_admin', 'postgres=X*/supabase_admin', 'service_role=X/supabase_admin'] | f |
| vault | _crypto_aead_det_encrypt | message bytea, additional bytea, key_id bigint, context bytea, nonce bytea | bytea | supabase_admin | no | ['supabase_admin=X/supabase_admin'] | f |
| vault | _crypto_aead_det_noncegen | — | bytea | supabase_admin | no | ['supabase_admin=X/supabase_admin'] | f |
| vault | create_secret | new_secret text, new_name text, new_description text, new_key_id uuid | uuid | supabase_admin | yes | ['supabase_admin=X/supabase_admin', 'postgres=X*/supabase_admin', 'service_role=X/supabase_admin'] | f |
| vault | update_secret | secret_id uuid, new_secret text, new_name text, new_description text, new_key_id uuid | void | supabase_admin | yes | ['supabase_admin=X/supabase_admin', 'postgres=X*/supabase_admin', 'service_role=X/supabase_admin'] | f |


### Triggers

#### codebi

| schema | relation | trigger_name | enabled_mode | definition |
| --- | --- | --- | --- | --- |
| codebi | audit_events | append_only | O | CREATE TRIGGER append_only BEFORE DELETE OR UPDATE ON codebi.audit_events FOR EACH ROW EXECUTE FUNCTION codebi.reject_history_change() |
| codebi | client_memberships | set_updated_at | O | CREATE TRIGGER set_updated_at BEFORE UPDATE ON codebi.client_memberships FOR EACH ROW EXECUTE FUNCTION codebi.set_updated_at() |
| codebi | clients | set_updated_at | O | CREATE TRIGGER set_updated_at BEFORE UPDATE ON codebi.clients FOR EACH ROW EXECUTE FUNCTION codebi.set_updated_at() |
| codebi | industries | set_updated_at | O | CREATE TRIGGER set_updated_at BEFORE UPDATE ON codebi.industries FOR EACH ROW EXECUTE FUNCTION codebi.set_updated_at() |
| codebi | sow_estimates | set_updated_at | O | CREATE TRIGGER set_updated_at BEFORE UPDATE ON codebi.sow_estimates FOR EACH ROW EXECUTE FUNCTION codebi.set_updated_at() |
| codebi | sow_events | append_only | O | CREATE TRIGGER append_only BEFORE DELETE OR UPDATE ON codebi.sow_events FOR EACH ROW EXECUTE FUNCTION codebi.reject_history_change() |
| codebi | sow_messages | append_only | O | CREATE TRIGGER append_only BEFORE DELETE OR UPDATE ON codebi.sow_messages FOR EACH ROW EXECUTE FUNCTION codebi.reject_history_change() |
| codebi | statements_of_work | set_updated_at | O | CREATE TRIGGER set_updated_at BEFORE UPDATE ON codebi.statements_of_work FOR EACH ROW EXECUTE FUNCTION codebi.set_updated_at() |
| codebi | time_entries | set_updated_at | O | CREATE TRIGGER set_updated_at BEFORE UPDATE ON codebi.time_entries FOR EACH ROW EXECUTE FUNCTION codebi.set_updated_at() |
| codebi | users | set_updated_at | O | CREATE TRIGGER set_updated_at BEFORE UPDATE ON codebi.users FOR EACH ROW EXECUTE FUNCTION codebi.set_updated_at() |

#### realtime

| schema | relation | trigger_name | enabled_mode | definition |
| --- | --- | --- | --- | --- |
| realtime | subscription | tr_check_filters | O | CREATE TRIGGER tr_check_filters BEFORE INSERT OR UPDATE ON realtime.subscription FOR EACH ROW EXECUTE FUNCTION realtime.subscription_check_filters() |

#### storage

| schema | relation | trigger_name | enabled_mode | definition |
| --- | --- | --- | --- | --- |
| storage | buckets | enforce_bucket_name_length_trigger | O | CREATE TRIGGER enforce_bucket_name_length_trigger BEFORE INSERT OR UPDATE OF name ON storage.buckets FOR EACH ROW EXECUTE FUNCTION storage.enforce_bucket_name_length() |
| storage | buckets | enforce_versioning_status_transition | O | CREATE TRIGGER enforce_versioning_status_transition BEFORE UPDATE OF versioning_status ON storage.buckets FOR EACH ROW EXECUTE FUNCTION storage.enforce_versioning_status_transition() |
| storage | buckets | protect_active_lifecycle_states | O | CREATE TRIGGER protect_active_lifecycle_states BEFORE DELETE ON storage.buckets FOR EACH ROW EXECUTE FUNCTION storage.protect_active_lifecycle_states() |
| storage | buckets | protect_bucket_control_insert | O | CREATE TRIGGER protect_bucket_control_insert BEFORE INSERT ON storage.buckets FOR EACH ROW EXECUTE FUNCTION storage.protect_bucket_control_columns('service_role') |
| storage | buckets | protect_bucket_control_update | O | CREATE TRIGGER protect_bucket_control_update BEFORE UPDATE OF lifecycle_configuration, lifecycle_configuration_generation ON storage.buckets FOR EACH ROW EXECUTE FUNCTION storage.protect_bucket_control_columns() |
| storage | buckets | protect_bucket_control_update_role | O | CREATE TRIGGER protect_bucket_control_update_role AFTER UPDATE OF lifecycle_configuration, lifecycle_configuration_generation ON storage.buckets FOR EACH ROW EXECUTE FUNCTION storage.enforce_bucket_lifecycle_service_role('service_role') |
| storage | buckets | protect_buckets_delete | O | CREATE TRIGGER protect_buckets_delete BEFORE DELETE ON storage.buckets FOR EACH STATEMENT EXECUTE FUNCTION storage.protect_delete() |
| storage | buckets | wake_lifecycle_on_policy_write | O | CREATE TRIGGER wake_lifecycle_on_policy_write AFTER INSERT OR UPDATE OF lifecycle_configuration_generation ON storage.buckets FOR EACH ROW EXECUTE FUNCTION storage.wake_lifecycle_state() |
| storage | buckets | wake_lifecycle_on_versioning_enable | O | CREATE TRIGGER wake_lifecycle_on_versioning_enable AFTER UPDATE OF versioning_status ON storage.buckets FOR EACH ROW WHEN ((new.lifecycle_configuration IS NOT NULL)) EXECUTE FUNCTION storage.wake_lifecycle_state() |
| storage | objects | protect_objects_delete | O | CREATE TRIGGER protect_objects_delete BEFORE DELETE ON storage.objects FOR EACH STATEMENT EXECUTE FUNCTION storage.protect_delete() |
| storage | objects | update_objects_updated_at | O | CREATE TRIGGER update_objects_updated_at BEFORE UPDATE ON storage.objects FOR EACH ROW EXECUTE FUNCTION storage.update_updated_at_column() |


### Sequences

#### auth

| schema | sequence | owner | acl | sql_type | seqstart | seqincrement | owned_column | runtime_usage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| auth | refresh_tokens_id_seq | supabase_auth_admin | ['supabase_auth_admin=rwU/supabase_auth_admin', 'dashboard_user=rwU/supabase_auth_admin', 'postgres=rwU/supabase_auth_admin'] | bigint | 1 | 1 | column id of table auth.refresh_tokens | no |

#### codebi

| schema | sequence | owner | acl | sql_type | seqstart | seqincrement | owned_column | runtime_usage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| codebi | clients_client_number_seq | postgres | ['postgres=rwU/postgres', 'codebi_app=U/postgres'] | bigint | 1 | 1 | column client_number of table codebi.clients | yes |
| codebi | statements_of_work_sow_number_seq | postgres | ['postgres=rwU/postgres', 'codebi_app=U/postgres'] | bigint | 1 | 1 | column sow_number of table codebi.statements_of_work | yes |
| codebi | test_id_seq | postgres | — | bigint | 1 | 1 | column id of table codebi.test | no |

#### history

| schema | sequence | owner | acl | sql_type | seqstart | seqincrement | owned_column | runtime_usage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| history | knowledge_entries_id_seq | postgres | — | bigint | 1 | 1 | column id of table history.knowledge_entries | no |

#### realtime

| schema | sequence | owner | acl | sql_type | seqstart | seqincrement | owned_column | runtime_usage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| realtime | subscription_id_seq | supabase_realtime_admin | ['supabase_realtime_admin=rwU/supabase_realtime_admin', 'postgres=rwU/supabase_realtime_admin', 'dashboard_user=rwU/supabase_realtime_admin', 'anon=U/supabase_realtime_admin', 'authenticated=U/supabase_realtime_admin', 'service_role=U/supabase_realtime_admin'] | bigint | 1 | 1 | column id of table realtime.subscription | no |


### Information schema migration tracker visibility

No rows returned.


### Extensions

| extname | extversion | nspname |
| --- | --- | --- |
| pg_stat_statements | 1.11 | extensions |
| pgcrypto | 1.3 | extensions |
| plpgsql | 1.0 | pg_catalog |
| supabase_vault | 0.3.1 | vault |
| uuid-ossp | 1.1 | extensions |


### Application effective grants

#### auth

| schema | relation | role | schema_usage | can_select | can_insert | can_update | can_delete |
| --- | --- | --- | --- | --- | --- | --- | --- |
| auth | audit_log_entries | anon | yes | no | no | no | no |
| auth | audit_log_entries | authenticated | yes | no | no | no | no |
| auth | audit_log_entries | codebi_app | no | no | no | no | no |
| auth | audit_log_entries | service_role | yes | no | no | no | no |
| auth | custom_oauth_providers | anon | yes | no | no | no | no |
| auth | custom_oauth_providers | authenticated | yes | no | no | no | no |
| auth | custom_oauth_providers | codebi_app | no | no | no | no | no |
| auth | custom_oauth_providers | service_role | yes | no | no | no | no |
| auth | flow_state | anon | yes | no | no | no | no |
| auth | flow_state | authenticated | yes | no | no | no | no |
| auth | flow_state | codebi_app | no | no | no | no | no |
| auth | flow_state | service_role | yes | no | no | no | no |
| auth | identities | anon | yes | no | no | no | no |
| auth | identities | authenticated | yes | no | no | no | no |
| auth | identities | codebi_app | no | no | no | no | no |
| auth | identities | service_role | yes | no | no | no | no |
| auth | instances | anon | yes | no | no | no | no |
| auth | instances | authenticated | yes | no | no | no | no |
| auth | instances | codebi_app | no | no | no | no | no |
| auth | instances | service_role | yes | no | no | no | no |
| auth | mfa_amr_claims | anon | yes | no | no | no | no |
| auth | mfa_amr_claims | authenticated | yes | no | no | no | no |
| auth | mfa_amr_claims | codebi_app | no | no | no | no | no |
| auth | mfa_amr_claims | service_role | yes | no | no | no | no |
| auth | mfa_challenges | anon | yes | no | no | no | no |
| auth | mfa_challenges | authenticated | yes | no | no | no | no |
| auth | mfa_challenges | codebi_app | no | no | no | no | no |
| auth | mfa_challenges | service_role | yes | no | no | no | no |
| auth | mfa_factors | anon | yes | no | no | no | no |
| auth | mfa_factors | authenticated | yes | no | no | no | no |
| auth | mfa_factors | codebi_app | no | no | no | no | no |
| auth | mfa_factors | service_role | yes | no | no | no | no |
| auth | mfa_recovery_code_sets | anon | yes | no | no | no | no |
| auth | mfa_recovery_code_sets | authenticated | yes | no | no | no | no |
| auth | mfa_recovery_code_sets | codebi_app | no | no | no | no | no |
| auth | mfa_recovery_code_sets | service_role | yes | no | no | no | no |
| auth | mfa_recovery_codes | anon | yes | no | no | no | no |
| auth | mfa_recovery_codes | authenticated | yes | no | no | no | no |
| auth | mfa_recovery_codes | codebi_app | no | no | no | no | no |
| auth | mfa_recovery_codes | service_role | yes | no | no | no | no |
| auth | oauth_authorizations | anon | yes | no | no | no | no |
| auth | oauth_authorizations | authenticated | yes | no | no | no | no |
| auth | oauth_authorizations | codebi_app | no | no | no | no | no |
| auth | oauth_authorizations | service_role | yes | no | no | no | no |
| auth | oauth_client_states | anon | yes | no | no | no | no |
| auth | oauth_client_states | authenticated | yes | no | no | no | no |
| auth | oauth_client_states | codebi_app | no | no | no | no | no |
| auth | oauth_client_states | service_role | yes | no | no | no | no |
| auth | oauth_clients | anon | yes | no | no | no | no |
| auth | oauth_clients | authenticated | yes | no | no | no | no |
| auth | oauth_clients | codebi_app | no | no | no | no | no |
| auth | oauth_clients | service_role | yes | no | no | no | no |
| auth | oauth_consents | anon | yes | no | no | no | no |
| auth | oauth_consents | authenticated | yes | no | no | no | no |
| auth | oauth_consents | codebi_app | no | no | no | no | no |
| auth | oauth_consents | service_role | yes | no | no | no | no |
| auth | one_time_tokens | anon | yes | no | no | no | no |
| auth | one_time_tokens | authenticated | yes | no | no | no | no |
| auth | one_time_tokens | codebi_app | no | no | no | no | no |
| auth | one_time_tokens | service_role | yes | no | no | no | no |
| auth | refresh_tokens | anon | yes | no | no | no | no |
| auth | refresh_tokens | authenticated | yes | no | no | no | no |
| auth | refresh_tokens | codebi_app | no | no | no | no | no |
| auth | refresh_tokens | service_role | yes | no | no | no | no |
| auth | saml_providers | anon | yes | no | no | no | no |
| auth | saml_providers | authenticated | yes | no | no | no | no |
| auth | saml_providers | codebi_app | no | no | no | no | no |
| auth | saml_providers | service_role | yes | no | no | no | no |
| auth | saml_relay_states | anon | yes | no | no | no | no |
| auth | saml_relay_states | authenticated | yes | no | no | no | no |
| auth | saml_relay_states | codebi_app | no | no | no | no | no |
| auth | saml_relay_states | service_role | yes | no | no | no | no |
| auth | schema_migrations | anon | yes | no | no | no | no |
| auth | schema_migrations | authenticated | yes | no | no | no | no |
| auth | schema_migrations | codebi_app | no | no | no | no | no |
| auth | schema_migrations | service_role | yes | no | no | no | no |
| auth | scim_tokens | anon | yes | no | no | no | no |
| auth | scim_tokens | authenticated | yes | no | no | no | no |
| auth | scim_tokens | codebi_app | no | no | no | no | no |
| auth | scim_tokens | service_role | yes | no | no | no | no |
| auth | scim_users | anon | yes | no | no | no | no |
| auth | scim_users | authenticated | yes | no | no | no | no |
| auth | scim_users | codebi_app | no | no | no | no | no |
| auth | scim_users | service_role | yes | no | no | no | no |
| auth | sessions | anon | yes | no | no | no | no |
| auth | sessions | authenticated | yes | no | no | no | no |
| auth | sessions | codebi_app | no | no | no | no | no |
| auth | sessions | service_role | yes | no | no | no | no |
| auth | sso_domains | anon | yes | no | no | no | no |
| auth | sso_domains | authenticated | yes | no | no | no | no |
| auth | sso_domains | codebi_app | no | no | no | no | no |
| auth | sso_domains | service_role | yes | no | no | no | no |
| auth | sso_providers | anon | yes | no | no | no | no |
| auth | sso_providers | authenticated | yes | no | no | no | no |
| auth | sso_providers | codebi_app | no | no | no | no | no |
| auth | sso_providers | service_role | yes | no | no | no | no |
| auth | users | anon | yes | no | no | no | no |
| auth | users | authenticated | yes | no | no | no | no |
| auth | users | codebi_app | no | no | no | no | no |
| auth | users | service_role | yes | no | no | no | no |
| auth | webauthn_challenges | anon | yes | no | no | no | no |
| auth | webauthn_challenges | authenticated | yes | no | no | no | no |
| auth | webauthn_challenges | codebi_app | no | no | no | no | no |
| auth | webauthn_challenges | service_role | yes | no | no | no | no |
| auth | webauthn_credentials | anon | yes | no | no | no | no |
| auth | webauthn_credentials | authenticated | yes | no | no | no | no |
| auth | webauthn_credentials | codebi_app | no | no | no | no | no |
| auth | webauthn_credentials | service_role | yes | no | no | no | no |

#### codebi

| schema | relation | role | schema_usage | can_select | can_insert | can_update | can_delete |
| --- | --- | --- | --- | --- | --- | --- | --- |
| codebi | alembic_version | anon | no | no | no | no | no |
| codebi | alembic_version | authenticated | no | no | no | no | no |
| codebi | alembic_version | codebi_app | yes | no | no | no | no |
| codebi | alembic_version | service_role | no | no | no | no | no |
| codebi | audit_events | anon | no | no | no | no | no |
| codebi | audit_events | authenticated | no | no | no | no | no |
| codebi | audit_events | codebi_app | yes | no | yes | no | no |
| codebi | audit_events | service_role | no | no | no | no | no |
| codebi | client_memberships | anon | no | no | no | no | no |
| codebi | client_memberships | authenticated | no | no | no | no | no |
| codebi | client_memberships | codebi_app | yes | yes | yes | yes | no |
| codebi | client_memberships | service_role | no | no | no | no | no |
| codebi | clients | anon | no | no | no | no | no |
| codebi | clients | authenticated | no | no | no | no | no |
| codebi | clients | codebi_app | yes | yes | yes | yes | no |
| codebi | clients | service_role | no | no | no | no | no |
| codebi | countries | anon | no | no | no | no | no |
| codebi | countries | authenticated | no | no | no | no | no |
| codebi | countries | codebi_app | yes | yes | no | no | no |
| codebi | countries | service_role | no | no | no | no | no |
| codebi | industries | anon | no | no | no | no | no |
| codebi | industries | authenticated | no | no | no | no | no |
| codebi | industries | codebi_app | yes | yes | no | no | no |
| codebi | industries | service_role | no | no | no | no | no |
| codebi | login_events | anon | no | no | no | no | no |
| codebi | login_events | authenticated | no | no | no | no | no |
| codebi | login_events | codebi_app | yes | no | yes | no | no |
| codebi | login_events | service_role | no | no | no | no | no |
| codebi | public_inquiries | anon | no | no | no | no | no |
| codebi | public_inquiries | authenticated | no | no | no | no | no |
| codebi | public_inquiries | codebi_app | yes | yes | yes | no | no |
| codebi | public_inquiries | service_role | no | no | no | no | no |
| codebi | roles | anon | no | no | no | no | no |
| codebi | roles | authenticated | no | no | no | no | no |
| codebi | roles | codebi_app | yes | yes | no | no | no |
| codebi | roles | service_role | no | no | no | no | no |
| codebi | sow_estimates | anon | no | no | no | no | no |
| codebi | sow_estimates | authenticated | no | no | no | no | no |
| codebi | sow_estimates | codebi_app | yes | yes | yes | yes | no |
| codebi | sow_estimates | service_role | no | no | no | no | no |
| codebi | sow_events | anon | no | no | no | no | no |
| codebi | sow_events | authenticated | no | no | no | no | no |
| codebi | sow_events | codebi_app | yes | yes | yes | no | no |
| codebi | sow_events | service_role | no | no | no | no | no |
| codebi | sow_messages | anon | no | no | no | no | no |
| codebi | sow_messages | authenticated | no | no | no | no | no |
| codebi | sow_messages | codebi_app | yes | yes | yes | no | no |
| codebi | sow_messages | service_role | no | no | no | no | no |
| codebi | sow_tools | anon | no | no | no | no | no |
| codebi | sow_tools | authenticated | no | no | no | no | no |
| codebi | sow_tools | codebi_app | yes | yes | yes | no | yes |
| codebi | sow_tools | service_role | no | no | no | no | no |
| codebi | sow_types | anon | no | no | no | no | no |
| codebi | sow_types | authenticated | no | no | no | no | no |
| codebi | sow_types | codebi_app | yes | yes | no | no | no |
| codebi | sow_types | service_role | no | no | no | no | no |
| codebi | state_regions | anon | no | no | no | no | no |
| codebi | state_regions | authenticated | no | no | no | no | no |
| codebi | state_regions | codebi_app | yes | yes | no | no | no |
| codebi | state_regions | service_role | no | no | no | no | no |
| codebi | statements_of_work | anon | no | no | no | no | no |
| codebi | statements_of_work | authenticated | no | no | no | no | no |
| codebi | statements_of_work | codebi_app | yes | yes | yes | yes | no |
| codebi | statements_of_work | service_role | no | no | no | no | no |
| codebi | test | anon | no | no | no | no | no |
| codebi | test | authenticated | no | no | no | no | no |
| codebi | test | codebi_app | yes | no | no | no | no |
| codebi | test | service_role | no | no | no | no | no |
| codebi | time_entries | anon | no | no | no | no | no |
| codebi | time_entries | authenticated | no | no | no | no | no |
| codebi | time_entries | codebi_app | yes | yes | yes | no | no |
| codebi | time_entries | service_role | no | no | no | no | no |
| codebi | timezones | anon | no | no | no | no | no |
| codebi | timezones | authenticated | no | no | no | no | no |
| codebi | timezones | codebi_app | yes | yes | no | no | no |
| codebi | timezones | service_role | no | no | no | no | no |
| codebi | tools | anon | no | no | no | no | no |
| codebi | tools | authenticated | no | no | no | no | no |
| codebi | tools | codebi_app | yes | yes | no | no | no |
| codebi | tools | service_role | no | no | no | no | no |
| codebi | user_invitations | anon | no | no | no | no | no |
| codebi | user_invitations | authenticated | no | no | no | no | no |
| codebi | user_invitations | codebi_app | yes | no | no | no | no |
| codebi | user_invitations | service_role | no | no | no | no | no |
| codebi | user_roles | anon | no | no | no | no | no |
| codebi | user_roles | authenticated | no | no | no | no | no |
| codebi | user_roles | codebi_app | yes | yes | yes | no | no |
| codebi | user_roles | service_role | no | no | no | no | no |
| codebi | users | anon | no | no | no | no | no |
| codebi | users | authenticated | no | no | no | no | no |
| codebi | users | codebi_app | yes | yes | yes | yes | no |
| codebi | users | service_role | no | no | no | no | no |

#### storage

| schema | relation | role | schema_usage | can_select | can_insert | can_update | can_delete |
| --- | --- | --- | --- | --- | --- | --- | --- |
| storage | bucket_lifecycle_states | anon | yes | no | no | no | no |
| storage | bucket_lifecycle_states | authenticated | yes | no | no | no | no |
| storage | bucket_lifecycle_states | codebi_app | no | no | no | no | no |
| storage | bucket_lifecycle_states | service_role | yes | yes | yes | yes | yes |
| storage | buckets | anon | yes | yes | yes | yes | yes |
| storage | buckets | authenticated | yes | yes | yes | yes | yes |
| storage | buckets | codebi_app | no | no | no | no | no |
| storage | buckets | service_role | yes | yes | yes | yes | yes |
| storage | buckets_analytics | anon | yes | yes | yes | yes | yes |
| storage | buckets_analytics | authenticated | yes | yes | yes | yes | yes |
| storage | buckets_analytics | codebi_app | no | no | no | no | no |
| storage | buckets_analytics | service_role | yes | yes | yes | yes | yes |
| storage | buckets_vectors | anon | yes | yes | no | no | no |
| storage | buckets_vectors | authenticated | yes | yes | no | no | no |
| storage | buckets_vectors | codebi_app | no | no | no | no | no |
| storage | buckets_vectors | service_role | yes | yes | no | no | no |
| storage | lifecycle_abandoned_artifacts | anon | yes | no | no | no | no |
| storage | lifecycle_abandoned_artifacts | authenticated | yes | no | no | no | no |
| storage | lifecycle_abandoned_artifacts | codebi_app | no | no | no | no | no |
| storage | lifecycle_abandoned_artifacts | service_role | yes | yes | yes | yes | yes |
| storage | migrations | anon | yes | no | no | no | no |
| storage | migrations | authenticated | yes | no | no | no | no |
| storage | migrations | codebi_app | no | no | no | no | no |
| storage | migrations | service_role | yes | no | no | no | no |
| storage | objects | anon | yes | yes | yes | yes | yes |
| storage | objects | authenticated | yes | yes | yes | yes | yes |
| storage | objects | codebi_app | no | no | no | no | no |
| storage | objects | service_role | yes | yes | yes | yes | yes |
| storage | s3_multipart_uploads | anon | yes | yes | no | no | no |
| storage | s3_multipart_uploads | authenticated | yes | yes | no | no | no |
| storage | s3_multipart_uploads | codebi_app | no | no | no | no | no |
| storage | s3_multipart_uploads | service_role | yes | yes | yes | yes | yes |
| storage | s3_multipart_uploads_parts | anon | yes | yes | no | no | no |
| storage | s3_multipart_uploads_parts | authenticated | yes | yes | no | no | no |
| storage | s3_multipart_uploads_parts | codebi_app | no | no | no | no | no |
| storage | s3_multipart_uploads_parts | service_role | yes | yes | yes | yes | yes |
| storage | vector_indexes | anon | yes | yes | no | no | no |
| storage | vector_indexes | authenticated | yes | yes | no | no | no |
| storage | vector_indexes | codebi_app | no | no | no | no | no |
| storage | vector_indexes | service_role | yes | yes | no | no | no |


## Inspection queries

The driver opened a read-only transaction with an 8-second statement timeout, 1-second lock timeout and pg_catalog search path. Only metadata was inspected.

**Database**

~~~sql
select current_database(), current_user, current_setting('server_version') as server_version, current_setting('transaction_read_only') as read_only, current_setting('pgrst.db_schemas',true) as pgrst_schemas;
~~~

**Schemas**

~~~sql
select nspname as schema, pg_get_userbyid(nspowner) as owner,nspacl as acl,has_schema_privilege(oid,'USAGE') as runtime_usage, has_schema_privilege(oid,'CREATE') as runtime_create from pg_namespace where nspname !~ '^pg_' and nspname <> 'information_schema' order by 1;
~~~

**Relations**

~~~sql
select n.nspname as schema,c.relname as relation,c.relkind as kind,pg_get_userbyid(c.relowner) as owner,c.relrowsecurity as rls,c.relforcerowsecurity as force_rls,case when c.relkind <> 'v' and c.reltuples>=0 then c.reltuples::bigint end as estimated_rows,c.relacl as acl,has_table_privilege(c.oid,'SELECT') as runtime_select,has_table_privilege(c.oid,'INSERT') as runtime_insert,has_table_privilege(c.oid,'UPDATE') as runtime_update,has_table_privilege(c.oid,'DELETE') as runtime_delete,c.reloptions as options FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND c.relkind IN ('r','p','m','f','v') ORDER BY 1,2;
~~~

**Columns**

~~~sql
select n.nspname as schema,c.relname as relation,a.attname as column_name,format_type(a.atttypid,a.atttypmod) as sql_type,a.attnotnull as not_null,pg_get_expr(d.adbin,d.adrelid) as default_or_generated_expression,a.attidentity as identity,a.attgenerated as generated,a.attacl as acl FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND c.relkind IN ('r','p','m','f','v') AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,2,a.attnum;
~~~

**Constraints**

~~~sql
select n.nspname as schema,c.relname as relation,k.conname as constraint_name,k.contype as kind,pg_get_constraintdef(k.oid) as definition,k.convalidated as validated FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' ORDER BY 1,2,3;
~~~

**Indexes**

~~~sql
select schemaname,tablename,indexname,indexdef from pg_indexes where schemaname !~ '^pg_' and schemaname <> 'information_schema' order by 1,2,3;
~~~

**Policies**

~~~sql
select schemaname,tablename,policyname,permissive,roles,cmd,qual as using_expression,with_check from pg_policies where schemaname !~ '^pg_' and schemaname <> 'information_schema' order by 1,2,3;
~~~

**Roles**

~~~sql
select rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin, rolbypassrls from pg_roles where rolname !~ '^pg_' order by 1;
~~~

**Role memberships**

~~~sql
select r.rolname as granted_role,m.rolname as member,a.admin_option,a.inherit_option,a.set_option from pg_auth_members a join pg_roles r on r.oid=a.roleid join pg_roles m on m.oid=a.member where r.rolname !~ '^pg_' or m.rolname !~ '^pg_' order by 1,2;
~~~

**Routines**

~~~sql
select n.nspname as schema,p.proname as routine,pg_get_function_identity_arguments(p.oid) as arguments,pg_get_function_result(p.oid) as returns,pg_get_userbyid(p.proowner) as owner,p.prosecdef as security_definer,p.proacl as acl,p.prokind as kind FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' ORDER BY 1,2,3;
~~~

**Triggers**

~~~sql
select n.nspname as schema,c.relname as relation,t.tgname as trigger_name,t.tgenabled as enabled_mode,pg_get_triggerdef(t.oid) as definition FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND NOT t.tgisinternal ORDER BY 1,2,3;
~~~

**Sequences**

~~~sql
select n.nspname as schema,c.relname as sequence,pg_get_userbyid(c.relowner) as owner,c.relacl as acl,format_type(s.seqtypid,NULL) as sql_type,s.seqstart,s.seqincrement,pg_describe_object(d.refclassid,d.refobjid,d.refobjsubid) as owned_column,has_sequence_privilege(c.oid,'USAGE') as runtime_usage FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_sequence s ON s.seqrelid=c.oid LEFT JOIN pg_depend d ON d.classid='pg_class'::regclass AND d.objid=c.oid AND d.refobjsubid>0 AND d.deptype IN ('i','a') WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' ORDER BY 1,2;
~~~

**Information schema migration tracker visibility**

~~~sql
select table_schema,table_name from information_schema.tables where table_name ~* '(migration|alembic|schema_version)' order by 1,2;
~~~

**Extensions**

~~~sql
select e.extname,e.extversion,n.nspname from pg_extension e join pg_namespace n on n.oid=e.extnamespace order by 1;
~~~

**Application effective grants**

~~~sql
select n.nspname as schema,c.relname as relation,r.rolname as role,has_schema_privilege(r.oid,n.oid,'USAGE') as schema_usage,has_table_privilege(r.oid,c.oid,'SELECT') as can_select,has_table_privilege(r.oid,c.oid,'INSERT') as can_insert,has_table_privilege(r.oid,c.oid,'UPDATE') as can_update,has_table_privilege(r.oid,c.oid,'DELETE') as can_delete from pg_namespace n join pg_class c on c.relnamespace=n.oid cross join pg_roles r where n.nspname in ('codebi','public','auth','storage','codebi_branding') and c.relkind in ('r','p','v','m','f') and r.rolname in ('codebi_app','anon','authenticated','service_role') order by 1,2,3;
~~~

## Approved Commerce implementation addendum - 24 November 2026

This dated addendum records changes made after the initial read-only audit and
approval to implement the Commerce foundation. Earlier findings describe their
inspection date and should not be treated as a current absence of Commerce.

The approved design uses the `commerce` schema, rather than the earlier proposed
`catalog` name. Eight Commerce tables and two `codebi` entitlement tables were
installed using `database/commerce/001_foundation.sql` and
`002_private_storage.sql`. Existing application records and the recorded Alembic
revision `0014_client_logo_background` were preserved. No migration history was
reset, stamped or rewritten.

Supabase Vault already existed. Narrow security-definer helpers now store
connection passwords and allow the worker to retrieve one password only for its
current authorized job lease. Ordinary application/worker roles have no general
Vault schema access. A restricted `commerce_worker` login and private
`commerce-artifacts` bucket/policies were installed. The generated worker URL is
stored only in ignored local configuration; no upload token was issued.

The existing The Saddle Consignor client was enabled for Commerce. Shiloh Tack
has separate blank catalog and inventory connections; there are no seeded vendor
URLs, passwords, extracted records, assumed Shopify headers or real login calls.

Flask now provides feature-gated entitlements, connection administration,
job/results/export views and versioned mapping/template configuration. A separate
PostgreSQL queue worker implements leases, bounded retries, entitlement checks,
immutable snapshots and export publication. Both Shiloh connectors are explicit
unimplemented placeholders and cannot report a successful extraction.

Validation: 113 automated tests passed. Live PostgreSQL rollback-only tests
verified Vault write/read, wrong-lease denial and cross-client foreign keys.
Post-install read-only checks verified grants, private storage and unchanged
Alembic history. The restricted worker connected and checked an empty queue.
Flask's restricted database connection read and rendered the registry/overview
and read connection settings successfully. No vendor network access, real file
upload, browser visual review, commit, push or droplet deployment was performed.

Remaining work: inspect actual portals and implement/test connectors together;
confirm product/variant SKU semantics and destination templates; provision and
renew a scoped Storage worker identity; review browser network restrictions,
artifact retention and server resource/service settings before production.
See `docs/COMMERCE_HUB_SETUP.md` for the setup sequence and current limits.

### Vendor module naming update - 25 November 2026

Vendor implementations are now grouped under
`commerce_hub/vendors/<database_vendor_code>/`. Shiloh authentication is in
`commerce_hub/vendors/shiloh_tack/login.py`, with separate `catalog.py` and
`inventory.py` adapters. Shared connector contracts and the registry are
`connector_contracts.py` and `connector_registry.py`; the worker no longer
imports a vendor-specific login error. Shared output modules are named
`export_files.py`, `export_mapping.py` and `artifact_storage.py`.

All imports and test patch paths were updated; the vendor test file is
`tests/test_vendor_shiloh_tack_login.py`. The full 146-test suite passes after
this structural refactor. Database objects, vendor codes, Vault references,
credentials, connector versions and worker startup commands were not changed.
Restart the local application/worker to load the relocated Python modules.
