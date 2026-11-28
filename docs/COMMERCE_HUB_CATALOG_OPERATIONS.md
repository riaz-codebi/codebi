# Catalog refresh and recovery operations

## Current status

The Shiloh catalog pipeline is implemented. The existing Saddle Consignor catalog
connection is enabled and has a published catalog in the
configured Supabase database. Other connections require an explicit rollout flag
`vendor_connections.config.catalog_pipeline_enabled=true`; normal feature access
and connection credentials must also be valid. No droplet deployment or automatic
weekly scheduling was performed.

## Data model

- `commerce.products`: immutable complete-product JSONB observations for each job.
- `commerce.catalog_work_items`: saved ordered source links. A product is pending
  while `product_id` is NULL; its product insert and completion marker commit in
  one transaction. No tab/image/partial-product resume exists.
- `commerce.catalog_publications`: immutable run scope, statistics and publication time.
- `commerce.catalog_publication_items`: an immutable manifest of the merged catalog,
  referring to product observations rather than copying their JSON again.
- `commerce.catalog_heads`: the currently published manifest for each client/vendor.
- `commerce.vendor_catalog_leases`: a vendor-wide active-job lock and throttle window.

A full scan builds a new complete manifest. Items absent from it are absent/inactive
in the current view; historical rows are retained. A category scan updates product
versions and that category's membership, retaining unrelated products. Removing a
product from one category does not remove it globally; a later full scan establishes
global absence. A product with multiple category memberships still has one current
entry keyed by its vendor product ID within its client/vendor. Catalog operations
never replace inventory quantities or merchant cost.

Vendor breadcrumbs may not list every category membership. Category IDs inferred
from breadcrumb names are used only when the name is unique in the vendor list;
selected-category scans supply directly observed membership. Ambiguous/missing
memberships are not invented. Statistics retain original breadcrumb paths, including
unclassified products. This is not a claim to have discovered the entire vendor
category membership graph.

## Run flow

The Commerce Hub navigation shows one view at a time. Connections is the default;
Jobs and Exports use `?tab=jobs` and `?tab=exports`. Products and Inventory open
their respective result pages. The selected view is highlighted blue, and Refresh
retains it. Starting, cancelling or resuming a job returns to Jobs; its search,
date filtering and pagination retain the selected Jobs view.

Products shows the current published manifest, combining completed category
refreshes. The Selected categories badge means that a full-vendor refresh has
not yet established full coverage; displayed product observations are complete.
Failed/in-progress captures do not appear in this manifest. Search by product ID
or name and filter by an observed category path across the entire manifest, with
25 products per page. Category, Price, image count and Last captured supplement
each row; capture times use the client's configured timezone. These filters do
not contact the vendor. Images remain source URLs rather than downloaded files.

1. Authorize the client/vendor job and acquire its lease and vendor-wide lock.
2. Log in once for that attempt. Validate the selected category against the current
   source form; never silently substitute All products.
3. Discover and persist the complete selected listing manifest. Save pagination
   progress so discovery can resume if interrupted before detail capture.
4. Fetch at most two product details concurrently, sharing one request start per
   second across listing/detail requests. Store complete product JSONB individually.
5. Pause for two minutes after each 1,000 completed products if more remain. The
   pause deadline is persisted; the background heartbeat remains active.
6. Validate manifest completion and first/last listing signatures, then atomically
   insert a publication manifest, switch the head, and mark the job succeeded.

The vendor doesn't expose a transactional snapshot. The run records an observation
window; detectable listing drift blocks publication and requires a new run, rather
than reporting a guaranteed point-in-time snapshot. Empty/malformed searches and
missing required product identity/name never clear the published catalog. Genuine
empty-catalog publication would require a separate reviewed operation, not this path.

## Failure and resume

Completed observations remain staged while the previous published head stays visible.
Automatic transient retries reuse the same job and skip committed work. A failed or
blocked resumable job offers Resume in the portal. Access, the original requesting
user and credential version must still be valid; changed source/configuration may
require a new job. Attempts are bounded (up to five with explicit manual resume).
Cancelled jobs are not automatically resumed.

`429` honors a bounded Retry-After interval (up to 24 hours), stored in the shared
vendor throttle window. Temporary network/server failures back off; authentication
loss, access blocks and unrecognized product pages stop the run. No proxy rotation,
CAPTCHA bypass or repeated rejected-password loop is used. DNS pinning, public-IP
validation, HTTPS certificate checks, response-size limits and exception sanitization
remain in place. `/TEST/` and `/TESTDB/` are excluded.

The job heartbeat renews the 120-second lease every 15 seconds. Ownership/access are
rechecked at database writes and publication. A superseded or expired worker cannot
commit product completion or publish. A vendor-wide lock prevents two client jobs
from multiplying the request rate. In-flight requests can finish after cancellation,
but their results cannot be committed by an invalid owner.

## History and UI

Products shows the current published manifest, including combined category refreshes.
View on a completed job opens a dedicated run page with client-facing statistics and only the products captured in that extraction. Historical publication manifests remain available through the catalog results endpoint. Job progress
shows counts, stage and elapsed time; run statistics contain new/changed/removed
counts, images, tabs, categories and parsing coverage. Exports continue to identify
the chosen extraction run and its scope; inventory/Shopify development is separate.
No historical retention/deletion policy is enabled yet.

## Install and verify

Using the existing virtual environment from the repository root:

```powershell
.\app\.venv\Scripts\python.exe -B scripts\setup_catalog.py --rollback-check
.\app\.venv\Scripts\python.exe -B scripts\setup_catalog.py --apply
.\app\.venv\Scripts\python.exe -B -m unittest discover -s tests
.\app\.venv\Scripts\python.exe -B scripts\verify_catalog_recovery.py --rollback-check
```

The SQL adds five tables, scoped foreign keys and restricted grants, with RLS enabled
and anon/authenticated access revoked. Snapshot/publication rows remain immutable to
the worker; the web role can read published state but cannot switch catalog heads.
Business logic is Python, not database functions. Alembic history is not modified.
Production deployment must install this additive migration before serving the new
code. The configured database has already been migrated; setup verifies existing
installation rather than recreating it.

Live bounded verification can use `scripts/check_catalog_acceptance.py --run` for
Outerwear, or `--observe-job <job UUID>` to inspect an existing publication without
starting another scan. The rollback recovery verifier uses temporary fake source
records and no vendor network access, restoring the live publication before exit.

## Job history filters

Job ID is first, followed by job type/category, the recorded requester's current
display name, run date and the existing progress/result columns. Run date uses the
first start time, or queue creation time until the job starts, displayed in the
client's configured timezone. The relative date filters are rolling last 24 hours,
7 days or 30 days based on that timestamp.

The Jobs toolbar searches category labels, requester names and job IDs, with a
rolling date filter. The separate Scope and Category history filters were removed
following owner review; legacy query parameters for them no longer hide rows.
Queries cover all authorized history and paginate 25 rows at a time, rather than
filtering only the newest rows. Filter changes replace only the job-history region and preserve
focus; active-job polling restarts for newly displayed rows. Invalid credentials or
cross-client access never expose another client's history. Existing requester and
timestamp fields supply this metadata; no schema or environment change is required.

## Client-facing run and product views

The Jobs row has a single View link; statistics are not expanded in the table.
The run page shows a vertical job summary beside Categories captured, followed
by Product ID, Name, Images, Category, Price and View columns. It does not expose source JSON. Run pages
show the products captured by that job; the main Products page retains the combined
published catalog across category refreshes.

Product View opens a responsive native dialog with an image gallery, optional
content tabs, paragraphs, bullet lists, specification tables, option labels and
quantity pricing. Vendor HTML is not rendered directly: structured text blocks are
escaped and rendered using application templates. Images are restricted to HTTPS
Shiloh prodimages URLs, requested only for the opened product, with no portal referrer.
The product endpoint rechecks client/catalog permissions and rejects cross-client
or unpublished snapshots. Missing images and request errors have readable states;
close/reopen and late response cancellation are handled without stale content.

Owner confirmation on 26 November 2026 establishes that the Shiloh Product ID is
the catalog SKU. New captures store that ID as vendor_sku. Existing immutable
observations are not rewritten: presentation/export resolves a missing Shiloh SKU
from its source_key. Leading zeros and punctuation are preserved. Internal option
values are still not variant SKUs; inventory matching remains a separate source
verification task. The duplicate SKU column is removed from client product tables.
No schema change is needed for these presentation/identity changes.

## Interactive Shopify exports

The Exports tab lists active client/vendor/platform subscriptions from
`commerce.client_export_destinations`. Initial subscriptions are seeded only for
existing active clients already granted both catalog and Shopify-export access.
CodeBI administrators can enable another available vendor/template with Add export.
Client administrators and internal administrators edit mappings using the gear icon;
authorized client users can download, but cannot change mappings or subscriptions.

The reviewed `product_template.csv` headers are bundled in
`commerce_hub/templates/shopify_product_2026.json`, preserving all 61 names and
their order. The file contains no sample product data. Destination templates stay
shared; saved mappings are immutable versions specific to client/vendor/template.
Saving checks the previous mapping ID to reject concurrent stale edits. Checked
columns are emitted in template order; unchecked columns are omitted, rather than
blanking an existing merchant's Shopify fields. Required fields cannot be omitted.

Each field offers Vendor field, Fixed value, or Default value. Only allowlisted product
fields, observed specification keys and explicit built-in rules are accepted.
No Python, user expressions, credentials or raw HTML are evaluated. Combined
descriptions rebuild escaped structured sections, lists and tables.

The editor's right-hand sample panel reads a real product from the current
client/vendor publication, choosing the product with the most populated mapping
values by default. It shows friendly key/value pairs including vendor, category
path, leaf category, observed specifications and automatic values. The panel scrolls
independently and stays visible while the mapping table scrolls. Product ID lookup
changes the sample through an authorized read-only endpoint without changing or
saving any mapping fields. Missing values are explicit, preview text is escaped,
and selected mapping fields highlight the corresponding sample row. On narrow
screens the independently scrollable sample moves above the table.

View default values opens a modal with all eight rule names, explanations with
concrete examples, and actual values from the selected sample product. Changing the
sample updates the modal content without altering unsaved mapping selections.
The Default value label retains the existing internal mapping keys so previously
saved versions remain compatible.

For an unsaved mapping whose published catalog contains option dropdowns, initial
suggestions use fixed Option1 name = Size and Option2 name = Color, mapped to the
vendor's respective option-1/option-2 choice fields. Both columns of each optional
option pair must be included together. Missing source choices remain unavailable;
the editor does not invent colors or treat a comma-separated choice list as one
variant. Saving this configuration is allowed independently of the existing export
check for verified variant combinations/SKUs. Catalogs without dropdowns retain
the simple-product Title / Default Title suggestion. Saved versions are not rewritten.

Downloads are direct private POST responses, protected by CSRF and current access
checks. They read the current immutable published manifest, not merely the latest
category job, and audit its publication ID, mapping version, template ID, row/image
counts and file hash. They do not scrape or require the worker or Storage token.
There is no stored export artifact for these direct responses; legacy worker
exports and historical artifacts retain their existing endpoints.

Product IDs remain strings. Price is numeric, without labels/currency signs.
Each simple product supplies its base row and further image-only rows; original
gallery URLs and order are retained. Generated handles use the mapped product title,
lowercased with whitespace/punctuation normalized to hyphens, and collisions are
rejected rather than adding vendor or product-ID suffixes. These new-product defaults are not an automatic
match to pre-existing Shopify handles. Default status is draft and online publishing
is FALSE. The client must review and save the suggestions before a download.

Products with option dropdowns are blocked until actual variant SKUs/combinations
are verified. Do not create variants from raw vendor option tokens or repeat a
parent SKU as fabricated variant SKUs. Inventory and cost are not guessed. Missing
required data, invalid numbers, duplicate identities, incomplete packed dimensions
and spreadsheet formulas are blocked. Limits bound source data, product count,
CSV rows and output size.

Install/verify on a configured environment:

```powershell
.\app\.venv\Scripts\python.exe -B scripts/setup_export_mapping.py --rollback-check
.\app\.venv\Scripts\python.exe -B scripts/setup_export_mapping.py --apply
.\app\.venv\Scripts\python.exe -B scripts/setup_export_mapping.py
```

The additive migration is `database/commerce/004_export_destinations.sql`. It enables
RLS and revokes public/anon/authenticated access, granting only the restricted web
role the required operations. Installation is transactional, preserves explicit
subscription opt-outs, and leaves Alembic and existing catalog/mapping versions
unchanged. The installer uses DATABASE_URL only for installation; normal runtime
uses APP_DATABASE_URL.

## Acumatica inventory login verification

The Shiloh inventory adapter uses the login verification below for both connection
samples and inventory downloads. The exact approved host is shilohtack-portal.acumatica.com. The
normal login page defaults to Test, so the verifier explicitly selects Company,
matching the configured client portal. Its observed JavaScript/MFA pipeline runs in
an ephemeral Playwright browser. It does not use Acumatica's password-based API login,
change MFA flags, solve challenges, remember devices or retry rejected credentials.
An actual visible MFA/approval prompt stops with a safe, readable error.

The worker retrieves credentials through the existing lease-bound Vault function.
Only an authenticated protected Main page with authentication-cookie and sign-out
control evidence passes sign-in verification. Cleanup uses Acumatica's documented
cookie-session POST /entity/auth/logout, requires HTTP 204, removes the temporary
cookies and confirms the protected page returns to the login form. Observed UI
sign-out controls are a fallback. The login verifier itself changes no business
data. The combined Test connection action also reads the first sample page below.
No raw page bodies, cookies, token values, passwords,
HARs, traces or authenticated screenshots are saved or logged.

The browser's HTTP and WebSocket traffic travels through a local HTTPS CONNECT
proxy that permits only the approved host on port 443. All DNS answers must be
public; connections use the validated IPs directly, with multi-IP fallback.
Certificate validation remains end-to-end in the browser. External hosts, direct
IP/private-address redirects, other ports and unencrypted proxy requests are denied.
The proxy also bounds its lifetime and total traffic. Service workers are disabled.
The existing catalog HTTP transport and scraping behavior are unchanged.

The local Windows verifier uses installed Microsoft Edge. Linux workers need
Playwright plus its Chromium binary and OS libraries. Using the paths in the example
service file (review paths before running):

```bash
/var/www/codebisolutions.com/venv/bin/python -m pip install -r /var/www/codebisolutions.com/requirements.txt
sudo /var/www/codebisolutions.com/venv/bin/python -m playwright install-deps chromium
sudo install -d -m 0755 /var/lib/codebi-playwright
sudo env PLAYWRIGHT_BROWSERS_PATH=/var/lib/codebi-playwright /var/www/codebisolutions.com/venv/bin/python -m playwright install chromium
```

The example worker unit now sets PLAYWRIGHT_BROWSERS_PATH=/var/lib/codebi-playwright
so its www-data user can read the installed browser while ProtectHome stays enabled.
No droplet packages or production unit were changed during local implementation.
Restart the web application and worker after installing a deployment.

Local explicit verification and read-only observation:

```powershell
.\app\.venv\Scripts\python.exe -B scripts/check_inventory_connection.py --run
.\app\.venv\Scripts\python.exe -B scripts/check_inventory_connection.py --observe-job <job-uuid>
```

The helper creates or observes only an inventory test_connection job. It never reads
Vault directly or requests an extraction. An explicit live run uses one attempt;
MFA, rejection, password-expiry, license/session limits and cleanup failures are
reported as safe codes. --inspect-logout is a diagnostic mode that prints control
IDs/types only, not page text or input values. It is not needed for normal operation.

On 27 November 2026, a live Company-tenant check succeeded using the existing Vault
credentials, with no MFA requested and confirmed session logout. Earlier verification
jobs record cleanup-detection failures and remain in history; no history was erased.
The final succeeded test is 79027eaf-4d85-4187-b420-af04301c5e5e.

### Inventory first-page discovery

The inventory **Test connection** action now includes the first-page sample;
the separate Preview first page button has been removed. Legacy sample jobs and
their View sample links remain available. The existing worker and lease-bound
Vault credential reader are used. The browser signs in once to Company,
opens Orders > Catalog, reads the first grid, and signs out. It never follows
pagination, selects cart checkboxes, changes quantities or adds items to the cart.
Qty means order-entry quantity; This Warehouse is the displayed stock quantity.
Inventory IDs remain strings and monetary values preserve source decimal precision.

The sample lives in the job result JSONB, with an authorized Jobs > View sample
page. It does not publish Inventory records or replace the latest inventory data.
The separate Run extraction action now supports full inventory publication, as
described below. No database migration is required for either workflow.

The grid pager uses div.toolsBtn elements with data-cmd values PageFirst,
PagePrev, PageNext and PageLast, plus a tooltip attribute rather than title.
Disabled state can be on an inner toolBtnDisabled element. Detection is scoped to
the inventory grid and does not click the controls. A local live check confirmed
First/Previous disabled and Next/Last enabled on page one.

Acumatica sizes the first page according to the browser viewport. The preview
records its original row count, resizes to 1920 x 1170, waits for the grid to
settle, and captures the resized page without paging. The verified sample changed
from 10 to 26 rows in the same session. Future pagination must keep the viewport
fixed throughout a run so changing page sizes cannot cause gaps or duplicates.

Inventory samples now explicitly enable Show Available Items Only and verify its
checked state before capture. Discovery associates the exact filter label with
its checkbox and excludes controls inside the cart grid. The sample records the
unfiltered page count and zero-stock IDs, then waits for the filtered grid. A
successful live check removed eight zero-stock IDs from page one; the filtered
page still held 26 rows because later available items filled the page. Every row
in that sample had positive This Warehouse stock. No pagination was performed.

Implemented full-inventory workflow: read all available items
sequentially, with no category restriction or parallel product-detail requests.
Persist completed work separately for failure recovery. Publish only after the
whole run succeeds. A successful 60-item run supersedes an older 100-item run;
the 40 absent items must not remain active. Failed/partial runs never supersede
the previous successful inventory. Retain prior runs for history rather than
deleting their observations. A confirmed empty successful run also supersedes
the previous inventory; an unreadable or failed grid cannot be treated as empty.

### Inventory download and recovery

Run extraction on the inventory connection reads all available items, one page
at a time. The browser size is fixed before login. It signs in once, keeps the
availability checkbox enabled, and follows only the inventory grid's Next control
until Next and Last are disabled. No product-detail requests or cart actions occur.
The long-running browser retains the same host/DNS/TLS restrictions, with bounded
four-hour lifetime, 512 MiB traffic, 1,000 pages and 50,000 inventory records.

Each completed page inserts immutable commerce.inventory rows and a job JSONB
checkpoint in the same lease-fenced transaction. The job tracks page hashes and
counts. Retries use the same job and session-per-attempt authentication. They must
navigate through earlier pages to reach the resume point, but verify their IDs
against saved hashes and never reinsert completed rows. If page membership/order,
price or quantity changed, recovery blocks and requires a new run instead of
mixing old saved values with newer pages. An older failed run cannot supersede a
newer successful inventory download.

Only after the final page and successful sign-out does one transaction mark the
job succeeded/published. The Inventory tab selects that latest successful run;
older run rows remain historical and staged rows are invisible. There is no need
to delete or update previous inventory records, or change database grants. Failed
jobs with checkpoints expose Resume to the requester or administrator, subject to
unchanged credentials, current entitlement and the existing retry limit.

The administrative bounded test queues exactly three pages (or fewer if the end
is reached), publishes the completed bounded scope, and labels it Limited inventory
load. It is an explicit test scope, never represented as a full vendor download:

```powershell
.\app\.venv\Scripts\python.exe -B scripts/check_inventory_download.py --run-limited
```

Review artifacts: instance/discovery/INVENTORY_CURRENT_LOAD.md and
inventory_current_load.json. --observe-job <job-uuid> only observes an existing
three-page job; --resume-job <job-uuid> resumes a recoverable three-page test.
Do not use Run extraction to repeat a bounded test: that action
requests the full available inventory. No nightly schedule is installed.

Live acceptance on 27 November 2026: job
566a43c8-1b73-41b3-ae0a-6fe2108c9677 published three pages of 26 records each,
78 unique inventory IDs total, all with positive warehouse stock. The job resumed
after a page-transition failure and preserved the first page without reinserting
it. The current Inventory tab selects this limited run. The full vendor dataset
has not been downloaded; the complete traversal remains for the planned later run.

For an explicit local first-page run, use:

```powershell
.\app\.venv\Scripts\python.exe -B scripts/check_inventory_first_page.py --run
```

The helper uses the same worker path and saves review artifacts under ignored
instance/discovery/INVENTORY_FIRST_PAGE.md and inventory_first_page.json. It also
supports --observe-job <job-uuid> without enqueueing a second job. Failed discovery
records contain only bounded grid structure and safe callback diagnostics, never
raw authenticated pages, hidden form values, cookies or credentials.

References: [Acumatica session sign-out](https://beacon.acumatica.com/r/Integration-Development-Guide/REST-API-Examples/Basic-Requests/Sign-Out-from-the-Service)
and [Playwright browser installation](https://playwright.dev/python/docs/browsers).

### Combined Test connection and first-page sample

Both Shiloh Catalog and Inventory Test connection buttons open a confirmation
dialog explaining that credentials and data access will be checked by downloading
one sample page. Cancel queues nothing. Confirm queues one test_connection job.
The connection is marked verified only after login and sample parsing both pass.
Only the inventory sample applies Show Available Items Only. Neither test follows
pagination or publishes data to the product/inventory tables.

Catalog sampling authenticates once through CatalogClient, discovers product links
on the first default search page, and reads those products' detail pages in the
same session. Samples retain structured product fields; the client view shows
Product ID, name, price, images count and category. Source listing values do not
replace detail fields. Catalog rate limits and pinned transport are unchanged.

New samples are stored as connection_sample in commerce.jobs.result JSONB. Jobs
provides View sample as soon as the test succeeds. The sample page confirms
credentials and data access and explicitly states that published data was not
changed. Access requires the same client, administrator role and connections
entitlement used to start a connection test. Historical login-only tests do not
invent a sample retroactively. No migration or new database privilege is required.

Explicit local live acceptance, one page per connection:

```powershell
.\app\.venv\Scripts\python.exe -B scripts/check_connection_samples.py --run
```

Use --kind catalog or --kind inventory to test just one. Review artifacts are
instance/discovery/CONNECTION_SAMPLES.md and connection_samples.json. The helper
checks that published catalog and inventory source job IDs remain unchanged.

Combined live acceptance succeeded on 27 November 2026: catalog test
7335cbc0-3b83-44df-8781-98498e01c1b7 saved 24 products and inventory test
7356c125-c304-430d-a8a8-9a61cd1aa71f saved 26 available items. Both published
source job IDs remained unchanged. Inventory navigation now tolerates the portal
shell starting the same frame navigation while the reader opens the Catalog URL;
the resulting grid must still pass all sample checks before verification succeeds.

On the Droplet, a subsequent grid diagnostic showed the expected inventory
headers but no loaded rows, with /pages/sp/sp700000.aspx in blocked_read_callbacks.
That known inventory screen is now seeded into the read-page allowlist: the
portal does not always produce an observable navigation GET before its data
callback. Existing host checks, callback-signature checks and cart/write-command
denials remain in force. Validate the patch on the Droplet after pulling it; stop
the local development worker during that check so the shared queue is processed
by the intended server.
