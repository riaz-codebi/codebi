-- Commerce Hub foundation v1. Explicit additive SQL; do not alter Alembic history.
-- Apply once using scripts/setup_commerce.py --apply. No vendor URLs/passwords are seeded.
BEGIN;
CREATE SCHEMA commerce;
COMMENT ON SCHEMA commerce IS 'CodeBI Commerce Hub foundation v1';

CREATE TABLE codebi.commercial_apps (
	code TEXT NOT NULL,
	name TEXT NOT NULL,
	is_active BOOLEAN NOT NULL,
	PRIMARY KEY (code)
);

CREATE TABLE commerce.destination_templates (
	id UUID NOT NULL,
	platform TEXT NOT NULL,
	version TEXT NOT NULL,
	columns JSONB NOT NULL,
	source_sha256 TEXT NOT NULL,
	is_active BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (platform, version)
);

CREATE TABLE commerce.vendors (
	id UUID NOT NULL,
	code TEXT NOT NULL,
	name TEXT NOT NULL,
	is_active BOOLEAN NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (code)
);

CREATE TABLE codebi.client_app_access (
	client_id UUID NOT NULL,
	app_code TEXT NOT NULL,
	is_active BOOLEAN NOT NULL,
	features JSONB NOT NULL,
	updated_by UUID,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (client_id, app_code),
	FOREIGN KEY(client_id) REFERENCES codebi.clients (id),
	FOREIGN KEY(app_code) REFERENCES codebi.commercial_apps (code),
	FOREIGN KEY(updated_by) REFERENCES codebi.users (id)
);

CREATE TABLE commerce.mapping_rules (
	id UUID NOT NULL,
	client_id UUID NOT NULL,
	vendor_id UUID NOT NULL,
	template_id UUID NOT NULL,
	version INTEGER NOT NULL,
	rules JSONB NOT NULL,
	is_active BOOLEAN NOT NULL,
	created_by UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (client_id, id, vendor_id, template_id),
	UNIQUE (client_id, vendor_id, template_id, version),
	FOREIGN KEY(client_id) REFERENCES codebi.clients (id),
	FOREIGN KEY(vendor_id) REFERENCES commerce.vendors (id),
	FOREIGN KEY(template_id) REFERENCES commerce.destination_templates (id),
	FOREIGN KEY(created_by) REFERENCES codebi.users (id)
);

CREATE TABLE commerce.vendor_connections (
	id UUID NOT NULL,
	client_id UUID NOT NULL,
	vendor_id UUID NOT NULL,
	connection_type TEXT NOT NULL,
	portal_url TEXT,
	approved_host TEXT,
	username TEXT NOT NULL,
	credential_ref UUID,
	credential_version INTEGER NOT NULL,
	status TEXT NOT NULL,
	last_verified_at TIMESTAMP WITH TIME ZONE,
	last_error_code TEXT,
	config JSONB NOT NULL,
	created_by UUID,
	updated_by UUID,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (client_id, id, vendor_id),
	UNIQUE (client_id, vendor_id, connection_type),
	FOREIGN KEY(client_id) REFERENCES codebi.clients (id),
	FOREIGN KEY(vendor_id) REFERENCES commerce.vendors (id),
	FOREIGN KEY(created_by) REFERENCES codebi.users (id),
	FOREIGN KEY(updated_by) REFERENCES codebi.users (id)
);

CREATE TABLE commerce.jobs (
	id UUID NOT NULL,
	client_id UUID NOT NULL,
	vendor_id UUID NOT NULL,
	connection_id UUID,
	credential_version INTEGER,
	requested_by UUID NOT NULL,
	kind TEXT NOT NULL,
	status TEXT NOT NULL,
	payload JSONB NOT NULL,
	result JSONB NOT NULL,
	attempts INTEGER NOT NULL,
	max_attempts INTEGER NOT NULL,
	progress INTEGER NOT NULL,
	lease_token UUID,
	lease_expires_at TIMESTAMP WITH TIME ZONE,
	available_at TIMESTAMP WITH TIME ZONE NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE,
	finished_at TIMESTAMP WITH TIME ZONE,
	error_code TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (client_id, id, vendor_id),
	FOREIGN KEY(client_id, connection_id, vendor_id) REFERENCES commerce.vendor_connections (client_id, id, vendor_id),
	FOREIGN KEY(client_id) REFERENCES codebi.clients (id),
	FOREIGN KEY(vendor_id) REFERENCES commerce.vendors (id),
	FOREIGN KEY(requested_by) REFERENCES codebi.users (id)
);

CREATE TABLE commerce.exports (
	id UUID NOT NULL,
	client_id UUID NOT NULL,
	vendor_id UUID NOT NULL,
	job_id UUID NOT NULL,
	catalog_job_id UUID,
	inventory_job_id UUID,
	mapping_id UUID,
	template_id UUID,
	format TEXT NOT NULL,
	artifact_path TEXT NOT NULL,
	sha256 TEXT NOT NULL,
	row_count INTEGER NOT NULL,
	manifest JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (client_id, id),
	UNIQUE (job_id),
	FOREIGN KEY(client_id, job_id, vendor_id) REFERENCES commerce.jobs (client_id, id, vendor_id),
	FOREIGN KEY(client_id, catalog_job_id, vendor_id) REFERENCES commerce.jobs (client_id, id, vendor_id),
	FOREIGN KEY(client_id, inventory_job_id, vendor_id) REFERENCES commerce.jobs (client_id, id, vendor_id),
	FOREIGN KEY(client_id, mapping_id, vendor_id, template_id) REFERENCES commerce.mapping_rules (client_id, id, vendor_id, template_id),
	FOREIGN KEY(template_id) REFERENCES commerce.destination_templates (id)
);

CREATE TABLE commerce.inventory (
	id UUID NOT NULL,
	client_id UUID NOT NULL,
	vendor_id UUID NOT NULL,
	job_id UUID NOT NULL,
	source_key TEXT NOT NULL,
	vendor_sku TEXT,
	attributes JSONB NOT NULL,
	extracted_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (client_id, job_id, source_key),
	FOREIGN KEY(client_id, job_id, vendor_id) REFERENCES commerce.jobs (client_id, id, vendor_id)
);

CREATE TABLE commerce.products (
	id UUID NOT NULL,
	client_id UUID NOT NULL,
	vendor_id UUID NOT NULL,
	job_id UUID NOT NULL,
	source_key TEXT NOT NULL,
	vendor_sku TEXT,
	source_url TEXT,
	attributes JSONB NOT NULL,
	artifact_path TEXT,
	extracted_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (client_id, job_id, source_key),
	FOREIGN KEY(client_id, job_id, vendor_id) REFERENCES commerce.jobs (client_id, id, vendor_id)
);

ALTER TABLE codebi.client_app_access ADD CONSTRAINT client_app_access_foundation_check_1 CHECK (jsonb_typeof(features)='array');

ALTER TABLE commerce.vendor_connections ADD CONSTRAINT vendor_connections_foundation_check_1 CHECK (connection_type IN ('catalog','inventory'));

ALTER TABLE commerce.vendor_connections ADD CONSTRAINT vendor_connections_foundation_check_2 CHECK (status IN ('not_configured','unverified','verified','error','disabled'));

ALTER TABLE commerce.vendor_connections ADD CONSTRAINT vendor_connections_foundation_check_3 CHECK (credential_version>=0);

ALTER TABLE commerce.vendor_connections ADD CONSTRAINT vendor_connections_foundation_check_4 CHECK (jsonb_typeof(config)='object');

ALTER TABLE commerce.vendor_connections ADD CONSTRAINT vendor_connections_foundation_check_5 CHECK ((portal_url IS NULL AND approved_host IS NULL) OR (portal_url LIKE 'https://%' AND approved_host IS NOT NULL));

ALTER TABLE commerce.vendor_connections ADD CONSTRAINT vendor_connections_foundation_check_6 CHECK ((credential_ref IS NULL AND credential_version=0) OR (credential_ref IS NOT NULL AND credential_version>0));

ALTER TABLE commerce.jobs ADD CONSTRAINT jobs_foundation_check_1 CHECK (kind IN ('test_connection','catalog','inventory','export_vendor','export_shopify'));

ALTER TABLE commerce.jobs ADD CONSTRAINT jobs_foundation_check_2 CHECK (status IN ('queued','running','succeeded','failed','blocked','cancelled'));

ALTER TABLE commerce.jobs ADD CONSTRAINT jobs_foundation_check_3 CHECK (attempts>=0 AND max_attempts BETWEEN 1 AND 5);

ALTER TABLE commerce.jobs ADD CONSTRAINT jobs_foundation_check_4 CHECK (progress BETWEEN 0 AND 100);

ALTER TABLE commerce.jobs ADD CONSTRAINT jobs_foundation_check_5 CHECK (jsonb_typeof(payload)='object' AND jsonb_typeof(result)='object');

ALTER TABLE commerce.jobs ADD CONSTRAINT jobs_foundation_check_6 CHECK ((status='running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR (status<>'running' AND lease_token IS NULL AND lease_expires_at IS NULL));

ALTER TABLE commerce.jobs ADD CONSTRAINT jobs_foundation_check_7 CHECK ((kind IN ('test_connection','catalog','inventory') AND connection_id IS NOT NULL AND credential_version IS NOT NULL) OR (kind IN ('export_vendor','export_shopify') AND connection_id IS NULL));

ALTER TABLE commerce.destination_templates ADD CONSTRAINT destination_templates_foundation_check_1 CHECK (jsonb_typeof(columns)='array');

ALTER TABLE commerce.destination_templates ADD CONSTRAINT destination_templates_foundation_check_2 CHECK (source_sha256 ~ '^[0-9a-f]{64}$');

ALTER TABLE commerce.mapping_rules ADD CONSTRAINT mapping_rules_foundation_check_1 CHECK (version>0);

ALTER TABLE commerce.mapping_rules ADD CONSTRAINT mapping_rules_foundation_check_2 CHECK (jsonb_typeof(rules)='array');

ALTER TABLE commerce.products ADD CONSTRAINT products_foundation_check_1 CHECK (btrim(source_key)<>'');

ALTER TABLE commerce.products ADD CONSTRAINT products_foundation_check_2 CHECK (jsonb_typeof(attributes)='object');

ALTER TABLE commerce.inventory ADD CONSTRAINT inventory_foundation_check_1 CHECK (btrim(source_key)<>'');

ALTER TABLE commerce.inventory ADD CONSTRAINT inventory_foundation_check_2 CHECK (jsonb_typeof(attributes)='object');

ALTER TABLE commerce.exports ADD CONSTRAINT exports_foundation_check_1 CHECK (format IN ('csv','xlsx'));

ALTER TABLE commerce.exports ADD CONSTRAINT exports_foundation_check_2 CHECK (row_count>=0);

ALTER TABLE commerce.exports ADD CONSTRAINT exports_foundation_check_3 CHECK (sha256 ~ '^[0-9a-f]{64}$');

ALTER TABLE commerce.exports ADD CONSTRAINT exports_foundation_check_4 CHECK (jsonb_typeof(manifest)='object');

ALTER TABLE commerce.exports ADD CONSTRAINT exports_foundation_check_5 CHECK (artifact_path LIKE client_id::text || '/exports/' || job_id::text || '/%');

ALTER TABLE commerce.exports ADD CONSTRAINT exports_foundation_check_6 CHECK (mapping_id IS NULL OR template_id IS NOT NULL);

CREATE UNIQUE INDEX commerce_one_active_connection_job ON commerce.jobs(client_id,connection_id) WHERE status IN ('queued','running') AND connection_id IS NOT NULL;

CREATE INDEX commerce_job_claim ON commerce.jobs(available_at,created_at) WHERE status='queued';

CREATE INDEX commerce_job_leases ON commerce.jobs(lease_expires_at) WHERE status='running';

CREATE INDEX commerce_jobs_client ON commerce.jobs(client_id,created_at DESC);

CREATE INDEX commerce_products_source ON commerce.products(client_id,job_id,vendor_sku);

CREATE INDEX commerce_inventory_source ON commerce.inventory(client_id,job_id,vendor_sku);

CREATE INDEX commerce_exports_client ON commerce.exports(client_id,created_at DESC);

INSERT INTO codebi.commercial_apps(code,name,is_active) VALUES ('commerce_hub','Commerce Hub',true);

INSERT INTO commerce.vendors(id,code,name,is_active) VALUES (gen_random_uuid(),'shiloh_tack','Shiloh Tack',true);

-- Permissions and Vault helpers follow.
DO $$
DECLARE t text;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='commerce_worker') THEN
        CREATE ROLE commerce_worker NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
    FOREACH t IN ARRAY ARRAY['vendors','vendor_connections','destination_templates','mapping_rules','jobs','products','inventory','exports'] LOOP
        EXECUTE format('ALTER TABLE commerce.%I ALTER COLUMN id SET DEFAULT gen_random_uuid()',t);
        EXECUTE format('ALTER TABLE commerce.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('REVOKE ALL ON commerce.%I FROM PUBLIC,anon,authenticated',t);
        EXECUTE format('CREATE POLICY commerce_backend ON commerce.%I FOR ALL TO codebi_app,commerce_worker USING (true) WITH CHECK (true)',t);
    END LOOP;
    FOREACH t IN ARRAY ARRAY['commercial_apps','client_app_access'] LOOP
        EXECUTE format('ALTER TABLE codebi.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('REVOKE ALL ON codebi.%I FROM PUBLIC,anon,authenticated',t);
        EXECUTE format('CREATE POLICY commerce_access_backend ON codebi.%I FOR ALL TO codebi_app,commerce_worker USING (true) WITH CHECK (true)',t);
    END LOOP;
END;
$$;
ALTER TABLE commerce.vendor_connections ALTER COLUMN username SET DEFAULT '';
ALTER TABLE commerce.vendor_connections ALTER COLUMN credential_version SET DEFAULT 0;
ALTER TABLE commerce.vendor_connections ALTER COLUMN status SET DEFAULT 'not_configured';
ALTER TABLE commerce.vendor_connections ALTER COLUMN config SET DEFAULT '{}';
ALTER TABLE commerce.vendor_connections ALTER COLUMN created_at SET DEFAULT now();
ALTER TABLE commerce.vendor_connections ALTER COLUMN updated_at SET DEFAULT now();
ALTER TABLE codebi.client_app_access ALTER COLUMN updated_at SET DEFAULT now();
ALTER TABLE codebi.client_app_access ALTER COLUMN features SET DEFAULT '[]';
ALTER TABLE commerce.jobs ALTER COLUMN status SET DEFAULT 'queued';
ALTER TABLE commerce.jobs ALTER COLUMN attempts SET DEFAULT 0;
ALTER TABLE commerce.jobs ALTER COLUMN max_attempts SET DEFAULT 3;
ALTER TABLE commerce.jobs ALTER COLUMN progress SET DEFAULT 0;
ALTER TABLE commerce.jobs ALTER COLUMN payload SET DEFAULT '{}';
ALTER TABLE commerce.jobs ALTER COLUMN result SET DEFAULT '{}';
ALTER TABLE commerce.jobs ALTER COLUMN created_at SET DEFAULT now();
ALTER TABLE commerce.jobs ALTER COLUMN updated_at SET DEFAULT now();
ALTER TABLE commerce.jobs ALTER COLUMN available_at SET DEFAULT now();
ALTER TABLE commerce.products ALTER COLUMN extracted_at SET DEFAULT now();
ALTER TABLE commerce.inventory ALTER COLUMN extracted_at SET DEFAULT now();
ALTER TABLE commerce.mapping_rules ALTER COLUMN created_at SET DEFAULT now();
ALTER TABLE commerce.destination_templates ALTER COLUMN created_at SET DEFAULT now();
ALTER TABLE commerce.exports ALTER COLUMN created_at SET DEFAULT now();
GRANT USAGE ON SCHEMA commerce TO codebi_app,commerce_worker;
GRANT SELECT ON codebi.commercial_apps,codebi.client_app_access TO codebi_app,commerce_worker;
GRANT INSERT,UPDATE ON codebi.client_app_access TO codebi_app;
GRANT SELECT ON ALL TABLES IN SCHEMA commerce TO codebi_app,commerce_worker;
GRANT INSERT ON commerce.vendor_connections,commerce.mapping_rules,commerce.destination_templates,commerce.jobs TO codebi_app;
GRANT UPDATE (portal_url,approved_host,username,status,config,updated_by,updated_at,last_error_code) ON commerce.vendor_connections TO codebi_app;
GRANT UPDATE (credential_version,last_verified_at) ON commerce.vendor_connections TO codebi_app;
GRANT UPDATE (status,lease_token,lease_expires_at,error_code,finished_at,updated_at) ON commerce.jobs TO codebi_app;
GRANT UPDATE ON commerce.jobs TO commerce_worker;
GRANT INSERT ON commerce.products,commerce.inventory,commerce.exports TO commerce_worker;
GRANT UPDATE (status,last_verified_at,last_error_code,updated_at) ON commerce.vendor_connections TO commerce_worker;
GRANT USAGE ON SCHEMA codebi TO commerce_worker;
GRANT SELECT (id,is_active) ON codebi.clients TO commerce_worker;
GRANT SELECT (id,is_active,user_type) ON codebi.users TO commerce_worker;
GRANT SELECT (client_id,user_id,role_code,is_active) ON codebi.client_memberships TO commerce_worker;
GRANT SELECT ON codebi.user_roles TO commerce_worker;
-- Worker reads identity status only, never auth.users, login tokens or profile email.
CREATE POLICY commerce_worker_clients ON codebi.clients FOR SELECT TO commerce_worker USING (true);
CREATE POLICY commerce_worker_users ON codebi.users FOR SELECT TO commerce_worker USING (true);
CREATE POLICY commerce_worker_memberships ON codebi.client_memberships FOR SELECT TO commerce_worker USING (true);
CREATE POLICY commerce_worker_roles ON codebi.user_roles FOR SELECT TO commerce_worker USING (true);

CREATE FUNCTION commerce.store_connection_secret(p_client uuid,p_connection uuid,p_password text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE c commerce.vendor_connections; secret_id uuid; secret_name text;
BEGIN
    IF p_password IS NULL OR length(p_password)=0 OR length(p_password)>4096 THEN
        RAISE EXCEPTION 'Invalid credential length';
    END IF;
    SELECT * INTO STRICT c FROM commerce.vendor_connections WHERE id=p_connection AND client_id=p_client FOR UPDATE;
    secret_name := 'commerce/' || c.client_id::text || '/' || c.id::text || '/password';
    IF c.credential_ref IS NULL THEN
        SELECT vault.create_secret(p_password,secret_name,'Commerce vendor connection') INTO secret_id;
    ELSE
        IF NOT EXISTS(SELECT 1 FROM vault.secrets WHERE id=c.credential_ref AND name=secret_name) THEN
            RAISE EXCEPTION 'Credential reference mismatch';
        END IF;
        PERFORM vault.update_secret(c.credential_ref,p_password);
        secret_id := c.credential_ref;
    END IF;
    UPDATE commerce.vendor_connections SET credential_ref=secret_id,credential_version=credential_version+1,
        status='unverified',last_verified_at=NULL,last_error_code=NULL,updated_at=now() WHERE id=c.id;
END;
$$;
REVOKE ALL ON FUNCTION commerce.store_connection_secret(uuid,uuid,text) FROM PUBLIC,anon,authenticated,service_role,commerce_worker;
GRANT EXECUTE ON FUNCTION commerce.store_connection_secret(uuid,uuid,text) TO codebi_app;

CREATE FUNCTION commerce.worker_connection_secret(p_job uuid,p_lease uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE result text;
BEGIN
    SELECT s.decrypted_secret INTO result
    FROM commerce.jobs j JOIN commerce.vendor_connections c
      ON (c.client_id,c.id,c.vendor_id)=(j.client_id,j.connection_id,j.vendor_id)
    JOIN vault.decrypted_secrets s ON s.id=c.credential_ref
      AND s.name='commerce/'||c.client_id::text||'/'||c.id::text||'/password'
    JOIN codebi.clients org ON org.id=j.client_id AND org.is_active
    JOIN codebi.users u ON u.id=j.requested_by AND u.is_active
    JOIN codebi.client_app_access a ON a.client_id=j.client_id AND a.app_code='commerce_hub' AND a.is_active
    JOIN codebi.commercial_apps app ON app.code=a.app_code AND app.is_active
    WHERE j.id=p_job AND j.status='running' AND j.lease_token=p_lease AND j.lease_expires_at>now()
      AND j.kind IN ('test_connection','catalog','inventory') AND c.status<>'disabled'
      AND c.credential_version=j.credential_version
      AND a.features ? CASE WHEN j.kind='test_connection' THEN 'connections' ELSE j.kind END
      AND (EXISTS (SELECT 1 FROM codebi.client_memberships m WHERE m.client_id=j.client_id AND m.user_id=u.id AND m.is_active)
        OR (u.user_type='internal' AND EXISTS (SELECT 1 FROM codebi.user_roles r WHERE r.user_id=u.id AND r.role_code='super_admin')));
    IF result IS NULL THEN RAISE EXCEPTION 'Credential access denied'; END IF;
    RETURN result;
END;
$$;
REVOKE ALL ON FUNCTION commerce.worker_connection_secret(uuid,uuid) FROM PUBLIC,anon,authenticated,service_role,codebi_app;
GRANT EXECUTE ON FUNCTION commerce.worker_connection_secret(uuid,uuid) TO commerce_worker;
COMMIT;
