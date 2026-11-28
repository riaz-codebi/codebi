# Commerce Hub run history, monitoring and recovery requirements

Owner review recorded 26 November 2026. This document records future production
requirements; it does not implement an algorithm, migration, worker status change,
vector service or client-facing monitoring screen.

## Historical summaries

Every extraction run needs a stable ID, client/vendor scope, source configuration
version, start/end timestamps, final status and a saved statistical summary. The
client must be able to select and compare runs by date after deployment, rather
than seeing only the latest result. Use UTC timestamps internally and the chosen
display timezone for the portal. Historical summaries must not be overwritten
when later runs finish.

Useful metrics include:

- Pages discovered, fetched, completed and failed; unique product links discovered.
- Products attempted, completed, skipped, failed and retried; duplicate identities.
- Tab labels/counts, image-count distribution, dropdown counts and category coverage.
- Missing/unresolved product IDs, prices, category paths, tab labels or image URLs.
- Catalog Price and quantity-tier coverage; inventory join/missing-cost metrics later.
- New/changed products relative to a comparable completed run. Only report removals
  when coverage is complete enough to establish them; a failed partial run must
  not mark absent products as removed.
- Source notices and anomalies, including repeated empty tabs, reused option IDs
  and ambiguous variant identifiers.

## Timing and progress

Measure total elapsed time, active processing time, paused time and stage timings
for login, listing discovery, detail fetch/parse, persistence and export. Record
throughput and request latency so performance can be compared between runs.

The current observation of 234 pages at 24 cards per page implies at most 5,616
listing positions, not a verified unique-product total. The final page can be
partial, and duplicate, missing or changing source products need accounting.

Show the current stage, completed/discovered work, failures/retries, elapsed time
and a clearly approximate ETA based on measured throughput. Refine totals as the
listing manifest is discovered. Do not invent a full-run duration from page count
alone; the discovery pass did not establish reliable end-to-end production timings.

## Durable recovery before full scans

Job leases and job-level retries already exist. They do **not** currently provide
product-level resume: the prototype extraction path buffers a run before saving.
Durable whole-product completion records are required before enabling a full production scan. No intermediate checkpoint within a product is permitted: a product is either fully done or not done. If processing fails, restart that product from its detail fetch and skip products already fully committed in the same run.

The implementation design should provide:

1. A persisted work manifest with page/detail URL references and product identities
   once known, scoped to the run, client and vendor.
2. Per-item pending/in-progress/completed/failed state, attempt count and safe error
   code, with atomic commits of the whole product data and its completion marker. Never resume partway through tabs, images or other fields within a product.
3. Idempotent writes so retry/reclaim cannot duplicate completed products.
4. Restart at the failed/unfinished product, processing it from the beginning while preserving and skipping products already fully completed in that run.
5. Bounded retries with backoff for transient failures and rate-limit handling.
   Authentication loss stops further requests instead of repeatedly posting a password.
6. Clear treatment of cancel, pause, resume and terminal failure; current database
   status constraints must not be changed without a reviewed migration.
7. Final validation/publication so exports and normal client queries do not mistake
   an incomplete run for a complete catalog snapshot.

A last-page number alone is not a sufficient checkpoint. The requested Date Added
(Desc.) order can shift as the vendor adds products. Track identities and listing
drift, use overlap/deduplication or a verified stable traversal strategy, and record
the observation window. Do not claim a point-in-time vendor snapshot if the source
cannot provide one.

The staging/checkpoint model and least-privilege worker grants must be designed
before implementation; no such tables or policies are created by this document.

## Category and subcategory reporting

Preserve the exact detail breadcrumb plus ordered category levels for each product.
Track coverage, missing paths and parsing failures instead of silently dropping
uncategorized products. Build distinct-product counts for leaf categories and
ancestor rollups in each selected run. Parent/child totals must not be added as if
they represented disjoint sets.

The saved sample has breadcrumbs for 72/72 products and 37 distinct full paths.
These are sample observations, not whole-catalog counts. A detail breadcrumb may
not show every category in which a vendor lists an item. Inspect the vendor category
tree and preserve additional observed memberships before claiming complete coverage.

## Visible option labels

Store the business-facing dropdown label (for example Color or Size) separately
from HTML name/id. Resolve explicit labels, ARIA labels and the nearest unambiguous
option-container label; flag unresolved labels instead of guessing from option values.
For product 8084 the visible label is Color; `optn0` and `optn0x0` are only technical
metadata. Preserve option labels and raw values, with placeholder choices marked.
Raw option IDs are reused between products and are not automatically vendor SKUs.

## Discovery benchmark evidence ? 26 November 2026

The same 100 products on pages 71, 133, 136, 151 and 180 took 330.991 seconds
sequentially and 174.634 seconds with two concurrent fetches, no fixed 0.6-second
pause, and independent whole-product local saves. Both captures have identical
compared content fields and no failed detail fetches. Exactly one login POST was
made in the two-fetch run. These combined changes do not isolate concurrency alone.

The second run measured 160 heartbeat calls totaling 100.307 seconds and a mean
fetch/auth-marker duration of 1.0726 seconds per product. Stage durations overlap;
these numbers are not an additive decomposition of elapsed time. Benchmark a
suitable scheduled lease renewal while retaining access checks, cancellation,
fencing and whole-product atomic commits. Keep the request concurrency bounded.

Local discovery JSON/Markdown remains review material. Production must commit each
complete product to PostgreSQL JSONB rather than rewrite a growing flat file after
every item. No partial-product persistence or partial-product resume is intended.

## Scheduled worker heartbeat implemented locally ? 26 November 2026

`commerce_hub/job_heartbeat.py` now renews active jobs every 15 seconds independently
of connector callbacks, retaining the 120-second lease. `Worker.run_once` owns the
heartbeat lifecycle; callbacks update memory and surface background failures.
The existing `live` transaction still fences final writes by current token,
expiry, status and access. Lost leases cannot be revived. Completion stops the
heartbeat immediately instead of waiting for its next interval. Database errors
are surfaced without retaining/logging secret-bearing exception text. Separate
sessions are used for renewal; network calls do not hold a database transaction.
Export authorization/ownership checks before transformation and upload remain.
Cancellation/access changes are detected on scheduled renewals (normally within
15 seconds plus database latency), at processing barriers after a renewal failure,
and again by final fenced database writes. In-flight network requests may finish;
this does not grant a stale worker permission to commit results.

The repeated 100-product benchmark completed in 75.717 seconds (0.76 seconds per
product overall), versus 174.634 seconds with frequent database heartbeats and
330.991 seconds sequentially. Actual database renewal calls fell from 160 to 4;
one login POST, zero failed detail pages, identical compared content. This does
not implement complete-product persistence/resume or a full production extractor.
No schema, deployment or environment change was made for this optimization.

## Catalog persistence/publication implemented ? 26 November 2026

The database-backed catalog pipeline now implements whole-product commits, durable
resume, atomic full/category publications, current and historical views, per-run
statistics and bounded vendor pacing. See COMMERCE_HUB_CATALOG_OPERATIONS.md for
current implementation details and limitations. Earlier benchmark entries describe
the state at the time of each measurement; production catalog writes are now live
for the explicitly enabled Saddle Consignor connection.
