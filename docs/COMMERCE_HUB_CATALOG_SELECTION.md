# Shiloh Tack catalog selection

Implemented locally on 26 November 2026 as an addition to the planned database-backed catalog pipeline.

The portal offers All products (default) and Selected category. The latter reveals
an existing searchable dropdown using the vendor's original category IDs/labels.
Typing filters choices locally. Switching to All products disables/clears the
category field. Server validation rejects unknown modes and missing/unknown category
IDs. Category selection is supported for Shiloh catalog jobs, not inventory or
login tests. The selection and server-resolved label are immutable job payload
metadata, revalidated by the worker and passed to the catalog adapter. Jobs,
results, export-run choices and export manifests retain the scope label/selection.

`commerce_hub/vendors/shiloh_tack/categories.json` is a vendor-choice snapshot
captured from the authenticated search form (350 entries). It contains no credentials.
It is reference metadata, not product storage. Refresh it from a new bounded vendor
form capture when source categories change; validate unique IDs before replacing
it. Portal page loads/typing must not log in to the vendor or access Vault. A future
production scan should verify the selected ID against that session's source form
and report a missing category rather than silently broadening the scan.

`search.py` constructs only reviewed search parameters: page number, blank text/max
price, default word matching, selected `scat` or blank for All, sortby=9, and blank
manufacturer/color/size filters. Every pagination request retains the selection.
Parent-category inclusion follows the vendor's own search semantics; it is not
inferred by matching our saved breadcrumbs.

Live verification: Outerwear, vendor ID 440, returned 15 unique product references
on one page, with complete category pagination traversal and one login. No product
details or product snapshots were written in that probe. The category review is
saved under `instance/discovery/SHILOH_CATEGORY_FILTER_REVIEW.md`.

The selection UI is now connected to the database-backed catalog pipeline for
explicitly enabled connections. See COMMERCE_HUB_CATALOG_OPERATIONS.md for current
persistence, publication, recovery and rollout behavior. The original category-only
discovery check did not itself require a schema change; the later catalog pipeline
uses the additive 003_catalog_publication.sql migration.

For that pipeline, a selected-category run must stay scoped to its saved selection
through resume and retries. Missing products outside the selected category must
never be marked removed or overwritten by a partial refresh. Historical comparisons
must distinguish category coverage from full-catalog coverage.
