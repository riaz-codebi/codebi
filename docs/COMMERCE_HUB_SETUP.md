# Commerce Hub foundation

Current release, 27 November 2026: catalog refreshes, available-only inventory
downloads, resumable jobs, combined connection/sample tests and the export mapper
are implemented. See [current operations](COMMERCE_HUB_CATALOG_OPERATIONS.md) for
the implemented flows and worker/browser deployment requirements. The foundation
notes below record the initial setup state; the connections are now configured
and samples/catalog/inventory test loads have been captured.

Foundation implemented 24 November 2026. Shiloh catalog login testing was
implemented and successfully verified using the saved Vault credentials on
25 November 2026. Catalog extraction and the inventory connector remain pending.
No products have been extracted.

## What is already configured

The database selected by the existing root `.env` now has:

- Eight `commerce` tables: `vendors`, `vendor_connections`, `products`,
  `inventory`, `destination_templates`, `mapping_rules`, `jobs`, and `exports`.
- `codebi.commercial_apps` and `codebi.client_app_access` for manual feature
  entitlements, independent of billing.
- A Shiloh Tack vendor record and two **blank** connections (catalog/inventory)
  for the existing **The Saddle Consignor** client. No URLs or vendor credentials
  were seeded. That client has the initial Commerce features enabled.
- Scoped Vault password helpers, a restricted `commerce_worker` SQL login,
  and the private `commerce-artifacts` Storage bucket and policies.

Supabase Vault was already installed. You do **not** need to create another Vault
or create passwords manually in its dashboard. A future connection-settings save
stores the password in Vault; the connection table stores only its reference.
Normal connection URLs, approved hostnames, usernames and nonsecret configuration
belong in `commerce.vendor_connections`.

The ignored local `.env` contains the generated worker database connection and
`COMMERCE_HUB_ENABLED=true`. Values were not displayed. Existing application
data and the recorded Alembic revision `0014_client_logo_background` were preserved.
The new SQL is tracked separately; do not reset or stamp Alembic to install it.

## Your next steps, in order

1. Restart your local Flask process using your usual command. The feature flag
   is read at startup. The existing `app/.venv` already has the added dependencies.
   No virtual-environment activation is required when using its Python directly.

   ```powershell
   cd C:\Projects\codebi
   .\app\.venv\Scripts\python.exe -B run.py
   ```

   Stop the old local process with Ctrl+C first. `run.py` is the local development
   entry point; keep using Gunicorn/systemd for the production application.
   With Commerce enabled and the worker URL configured, both this command and
   `python -m flask --app run run` start one background Commerce worker
   automatically. The debug reloader parent owns it, so reloads do not create
   a worker per web process. Worker exits are retried after five seconds; changes
   to Commerce Python files restart the worker to load current connector imports.
   Stopping the local server stops its worker. Flask CLI commands such as
   `routes`, test factories and Gunicorn imports never start this local supervisor.
2. Sign in as your CodeBI administrator, open **Commerce Hub**, then open
   **The Saddle Consignor**. Review the two unconfigured connection cards, jobs,
   products, inventory and exports. Existing client members see only their
   entitled client. The administrator can adjust per-client features here.
   Navigation groups Commerce Hub under **Commercial tools**. The registry lists
   only enabled clients, with live name search and vendor access filters. Its
   Access column shows Shiloh catalog/inventory, while other permissions remain
   in the settings popup. Search filters the already authorized client list
   immediately in the browser without requesting the server on each keystroke;
   the search field's built-in cross clears the name filter. Use a row's gear
   icon to change access in the Save/Cancel popup, or **Add client** to grant an
   existing active client access. Click the client row or hub icon to open its hub.
3. The initial catalog login has been verified. Configure the inventory connection
   when ready; keep credentials out of chat. A CodeBI administrator sets the
   approved HTTPS URLs. A client administrator
   can then enter/replace the username and password in Connection settings.
   Settings open read-only with Cancel/Edit. Edit unlocks permitted fields and
   becomes Save. A stored password is indicated by five placeholder stars; focusing
   it in Edit mode clears the placeholder for a replacement. Leaving it empty
   preserves the stored secret. The actual password is never returned to the
   form or downloaded through the portal.
4. The local worker runs automatically with `run.py` or `flask run`; a second terminal is no
   longer required for normal use. The following is an optional one-job diagnostic:

   ```powershell
   cd C:\Projects\codebi
   .\app\.venv\Scripts\python.exe -B -m commerce_hub.worker --once
   ```

   With an empty queue it exits successfully without output. Outside `run.py`,
   omit `--once` to run continuously and use Ctrl+C to stop it. Production still
   runs the worker as a separate systemd service, independently of Gunicorn.
   Catalog **Test connection** now uses the site's dealer login request and an
   authenticated-page check, with credentials obtained through the job-scoped
   Vault helper and cookies kept only in memory. No CAPTCHA was required in the
   successful live test. Extraction and inventory placeholders remain blocked.
5. Before the first real file upload, configure the worker's **Storage identity**
   as described below. This can wait until connector/export integration. The
   private bucket already exists, but no worker upload token was created.
6. For connector development, we will inspect the catalog and inventory portals
   together, confirm the exact data fields, SKU/variant meaning and pagination,
   then implement and test one connector at a time. We also need an agreed
   destination template before producing Shopify files. None is assumed today.

## Storage identity: deferred until real exports

The worker uses `COMMERCE_STORAGE_TOKEN`, a short-lived JWT whose role is
`commerce_worker`. Its policies allow access only to this application's private
bucket. Flask downloads check the current user's client entitlement and feature,
then forward their verified Supabase access token. Files are not public links.

For a project that still accepts its existing legacy HS256 signing secret,
the following optional owner-run helper issues a seven-day worker token:

```powershell
.\app\.venv\Scripts\python.exe -B scripts/issue_commerce_storage_token.py
```

It prompts privately for the **existing** legacy signing secret using a hidden
terminal prompt, verifies bucket access, and writes only the scoped token to the
ignored `.env`. Do not paste the signing secret into chat, commit it, or rotate
the project's signing keys for this setup. Do not substitute a service-role key.
The helper is optional and is not suitable for a project that no longer accepts
legacy signing secrets; we must configure a compatible machine identity in that
case. Token expiry/renewal must be operationally arranged before unattended
production exports; the current helper does not automate renewal.

After saving a new token, restart the worker. Without a valid token, uploads
remain blocked and jobs report a storage configuration error.

## How the pieces fit

Vendor code is grouped by the existing database vendor code. New vendors follow
this layout rather than adding vendor files to the package root:

```text
commerce_hub/
  connector_contracts.py       # Common credentials, records and safe errors
  connector_registry.py        # Vendor code + connection type -> adapter
  vendors/
    shiloh_tack/
      login.py                 # Shiloh dealer authentication
      catalog.py               # Shiloh catalog adapter
      inventory.py             # Shiloh inventory adapter (pending)
  export_files.py              # CSV/XLSX generation and template inspection
  export_mapping.py            # Declarative destination mapping
  artifact_storage.py          # Private artifact upload/download
  models.py                    # Commerce database models
  worker.py                    # Vendor-independent job processing
```

Vendor adapter class names also identify the vendor, such as
`ShilohCatalogConnector`. Tests for vendor login are named
`tests/test_vendor_shiloh_tack_login.py`. The worker imports shared connector
errors and the registry, not Shiloh-specific code. Flask `routes.py` and
`services.py` retain their conventional names inside the `app/commerce` package.
This naming refactor does not change database vendor codes, Vault references,
stored credentials, connector version strings or the worker startup command.

Flask owns application authorization, connection administration, entitlements,
job requests and mapping configuration. SQL constraints enforce tenant
references, uniqueness and immutable source snapshots. The only security-definer
database functions are the narrow Vault/Storage boundaries; scraping and
business transformations live in Python.

The worker claims PostgreSQL jobs with leases, checks requester access and
credential versions, performs connector I/O outside database transactions, then
publishes immutable extraction snapshots. Expired leases can be recovered;
stale workers cannot publish. Retries are bounded. Original vendor attributes
remain JSONB and are independent of Shopify mapping versions.

Exports pin source runs, mapping and destination-template versions. Mapping
rules are validated configuration, not executable Python. Exact SKU matching
preserves leading zeros and case. Shopify export remains blocked until product
versus variant granularity is explicitly confirmed for both sources. CSV output
guards against spreadsheet formulas; Excel text is written as text.

Current defensive limits are 10,000 records / 32 MB buffered extraction data,
50 MB artifacts, and 2 MB template uploads. Review these against real portal
volumes before production. The connector implementation must add redirect,
DNS/private-network and browser-request restrictions before any vendor network
access is enabled. No browser engine or selectors are assumed in the foundation.

A crash or revocation after an upload but before publication can leave a private,
unpublished object. Agree retention and an operator cleanup procedure before
production exports; no automatic deletion of client artifacts is enabled.

## Installing on another environment later

Do not repeat installation in the current database unnecessarily. The scripts
are for controlled, additive setup and reject an incomplete existing schema.

```powershell
cd C:\Projects\codebi
.\app\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\app\.venv\Scripts\python.exe -B scripts/setup_commerce.py
.\app\.venv\Scripts\python.exe -B scripts/setup_commerce.py --apply --configure-worker --enable-local --enable-client-name "The Saddle Consignor"
.\app\.venv\Scripts\python.exe -B scripts/verify_commerce.py
```

The target must already have the existing portal schema, the named active client,
Supabase Vault and ignored `.env` configuration. Inspection uses
`APP_DATABASE_URL`; installation uses the administrative `DATABASE_URL`.
Runtime Flask continues using `codebi_app`; the worker uses only its own login.
Do not use the administrative URL for either runtime.

The ordered scripts are `database/commerce/001_foundation.sql` followed by
`database/commerce/002_private_storage.sql`. The setup helper applies both in a
transaction, generates the worker password, and safely writes the URL locally.
Existing worker passwords are preserved unless explicitly rotated. Never print
or copy a connection URL into Git or chat.

## Droplet deployment, a later step

These code changes have **not** been committed, pushed or deployed. Once reviewed
and published to Git, install dependencies in the droplet's existing virtual
environment, enable `COMMERCE_HUB_ENABLED=true` there, securely provision the
restricted worker URL and eventual Storage token, and restart `codebi.service`.
If the droplet uses the same database, do not reinstall/reset its schema or
rotate the worker password just to deploy code.

`deploy/commerce-worker.service` is an example for the known
`/var/www/codebisolutions.com` deployment and `venv/bin/python`. Review the user,
paths, `.env` read permissions and memory limits against the actual server before
installing it as a separate systemd service. No production service/configuration
was changed. Browser dependencies and systemd sandbox compatibility must be
reviewed when the real connectors are implemented.

## Verification

The automated suite passes 160 tests, including Commerce and login tests for tenant
isolation, role checks, password redaction, CSRF, lease recovery, immutable
snapshots, versioned mapping, formula-safe output and revoked-access publication.
Run tests with the optional feature disabled globally; Commerce tests enable it
explicitly in their isolated fixtures:

```powershell
$env:COMMERCE_HUB_ENABLED='false'
.\app\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
Remove-Item Env:COMMERCE_HUB_ENABLED
```

Live PostgreSQL verification exercised Vault write/read, wrong-lease denial and
cross-client foreign keys in a fully rolled-back transaction. Post-install checks
confirmed grants, Vault boundaries, private bucket and unchanged Alembic history.
The worker connected using its restricted login. A subsequent live catalog
login test on 25 November 2026 succeeded and set the catalog connection Verified.
No scraping, customer export or Storage upload was performed.

The login checker restricts hosts and endpoints, pins validated public DNS IPs,
verifies the original TLS hostname, refuses redirects and limits response sizes.
Both approved hostnames normalize to `www.shilohtack.com` before any request;
the apex redirects there, so no credential-bearing redirect is needed. Connection
attempts can fall back across the already validated public IP list, closing failed
TLS sockets. Login tokens support JavaScript assignments and hidden `lc` inputs;
conflicting tokens stop the test. Bounded success suffixes are accepted, but a
success response still requires authenticated-page evidence from actual logout
controls. Hidden controls and unrelated page text are not sufficient evidence.
Login errors do not automatically retry a password submission. If a future
response requires an interactive challenge, the test stops with a safe status.
Extraction/browser work must extend these restrictions for its additional requests.

The restricted Flask connection also passed registry/overview template rendering
and connection reads against the installed database. To repeat that read-only
smoke check, run `scripts/verify_commerce.py --application` using the same Python.
