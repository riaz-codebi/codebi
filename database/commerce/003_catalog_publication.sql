-- Catalog publication/resume v1. Additive; no product rows are deleted.

-- Business logic remains in Flask/worker Python. Apply with scripts/setup_catalog.py --apply.

BEGIN;

SET LOCAL lock_timeout='10s';

ALTER TABLE commerce.products ADD CONSTRAINT products_catalog_scoped_id UNIQUE (client_id,vendor_id,id);


CREATE TABLE commerce.catalog_work_items (
	job_id UUID NOT NULL,
	ordinal INTEGER NOT NULL,
	client_id UUID NOT NULL,
	vendor_id UUID NOT NULL,
	source_path TEXT NOT NULL,
	product_id UUID,
	completed_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (job_id, ordinal),
	UNIQUE (job_id, source_path),
	FOREIGN KEY(client_id, job_id, vendor_id) REFERENCES commerce.jobs (client_id, id, vendor_id),
	FOREIGN KEY(client_id, vendor_id, product_id) REFERENCES commerce.products (client_id, vendor_id, id)
)

;


CREATE TABLE commerce.catalog_publications (
	job_id UUID NOT NULL,
	client_id UUID NOT NULL,
	vendor_id UUID NOT NULL,
	selection JSONB NOT NULL,
	summary JSONB NOT NULL,
	published_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (job_id),
	UNIQUE (client_id, vendor_id, job_id),
	FOREIGN KEY(client_id, job_id, vendor_id) REFERENCES commerce.jobs (client_id, id, vendor_id)
)

;


CREATE TABLE commerce.catalog_publication_items (
	publication_id UUID NOT NULL,
	source_key TEXT NOT NULL,
	client_id UUID NOT NULL,
	vendor_id UUID NOT NULL,
	product_id UUID NOT NULL,
	category_ids JSONB NOT NULL,
	PRIMARY KEY (publication_id, source_key),
	FOREIGN KEY(client_id, vendor_id, publication_id) REFERENCES commerce.catalog_publications (client_id, vendor_id, job_id),
	FOREIGN KEY(client_id, vendor_id, product_id) REFERENCES commerce.products (client_id, vendor_id, id)
)

;


CREATE TABLE commerce.catalog_heads (
	client_id UUID NOT NULL,
	vendor_id UUID NOT NULL,
	publication_id UUID NOT NULL,
	PRIMARY KEY (client_id, vendor_id),
	FOREIGN KEY(client_id, vendor_id, publication_id) REFERENCES commerce.catalog_publications (client_id, vendor_id, job_id),
	FOREIGN KEY(client_id) REFERENCES codebi.clients (id),
	FOREIGN KEY(vendor_id) REFERENCES commerce.vendors (id)
)

;


CREATE TABLE commerce.vendor_catalog_leases (
	vendor_id UUID NOT NULL,
	job_id UUID,
	lease_token UUID,
	lease_expires_at TIMESTAMP WITH TIME ZONE,
	throttle_until TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (vendor_id),
	FOREIGN KEY(vendor_id) REFERENCES commerce.vendors (id),
	FOREIGN KEY(job_id) REFERENCES commerce.jobs (id)
)

;

ALTER TABLE commerce.catalog_work_items ADD CONSTRAINT catalog_work_valid CHECK (ordinal>0 AND length(source_path)<=1500 AND left(source_path,1)='/' AND left(source_path,2)<>'//' AND ((product_id IS NULL AND completed_at IS NULL) OR (product_id IS NOT NULL AND completed_at IS NOT NULL)));

ALTER TABLE commerce.catalog_publications ADD CONSTRAINT catalog_publication_json CHECK (jsonb_typeof(selection)='object' AND jsonb_typeof(summary)='object');

ALTER TABLE commerce.catalog_publication_items ADD CONSTRAINT catalog_memberships_array CHECK (jsonb_typeof(category_ids)='array');

ALTER TABLE commerce.vendor_catalog_leases ADD CONSTRAINT catalog_vendor_lease_consistent CHECK ((job_id IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) OR (job_id IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL));

CREATE INDEX catalog_work_pending ON commerce.catalog_work_items(job_id,ordinal) WHERE product_id IS NULL;

CREATE INDEX catalog_publications_client ON commerce.catalog_publications(client_id,vendor_id,published_at DESC);

CREATE INDEX catalog_publication_lookup ON commerce.catalog_publication_items(client_id,vendor_id,source_key);

ALTER TABLE commerce.catalog_work_items ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON commerce.catalog_work_items FROM PUBLIC,anon,authenticated;

CREATE POLICY catalog_backend ON commerce.catalog_work_items FOR ALL TO codebi_app,commerce_worker USING (true) WITH CHECK (true);

GRANT SELECT ON commerce.catalog_work_items TO codebi_app,commerce_worker;

ALTER TABLE commerce.catalog_publications ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON commerce.catalog_publications FROM PUBLIC,anon,authenticated;

CREATE POLICY catalog_backend ON commerce.catalog_publications FOR ALL TO codebi_app,commerce_worker USING (true) WITH CHECK (true);

GRANT SELECT ON commerce.catalog_publications TO codebi_app,commerce_worker;

ALTER TABLE commerce.catalog_publication_items ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON commerce.catalog_publication_items FROM PUBLIC,anon,authenticated;

CREATE POLICY catalog_backend ON commerce.catalog_publication_items FOR ALL TO codebi_app,commerce_worker USING (true) WITH CHECK (true);

GRANT SELECT ON commerce.catalog_publication_items TO codebi_app,commerce_worker;

ALTER TABLE commerce.catalog_heads ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON commerce.catalog_heads FROM PUBLIC,anon,authenticated;

CREATE POLICY catalog_backend ON commerce.catalog_heads FOR ALL TO codebi_app,commerce_worker USING (true) WITH CHECK (true);

GRANT SELECT ON commerce.catalog_heads TO codebi_app,commerce_worker;

ALTER TABLE commerce.vendor_catalog_leases ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON commerce.vendor_catalog_leases FROM PUBLIC,anon,authenticated;

CREATE POLICY catalog_backend ON commerce.vendor_catalog_leases FOR ALL TO codebi_app,commerce_worker USING (true) WITH CHECK (true);

GRANT SELECT ON commerce.vendor_catalog_leases TO codebi_app,commerce_worker;

GRANT INSERT ON commerce.catalog_work_items,commerce.catalog_publications,commerce.catalog_publication_items,commerce.catalog_heads,commerce.vendor_catalog_leases TO commerce_worker;

GRANT UPDATE (product_id,completed_at) ON commerce.catalog_work_items TO commerce_worker;

GRANT UPDATE (publication_id) ON commerce.catalog_heads TO commerce_worker;

GRANT UPDATE ON commerce.vendor_catalog_leases TO commerce_worker;

GRANT UPDATE (available_at,max_attempts) ON commerce.jobs TO codebi_app;

COMMENT ON TABLE commerce.catalog_heads IS 'Catalog publication/resume v1';

COMMIT;
