BEGIN;
CREATE TABLE commerce.client_export_destinations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES codebi.clients(id),
    vendor_id uuid NOT NULL REFERENCES commerce.vendors(id),
    platform text NOT NULL CHECK (platform ~ '^[a-z][a-z0-9_]{0,39}$'),
    template_id uuid NOT NULL REFERENCES commerce.destination_templates(id),
    is_active boolean NOT NULL DEFAULT true,
    updated_by uuid REFERENCES codebi.users(id),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(client_id,vendor_id,platform)
);
ALTER TABLE commerce.client_export_destinations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON commerce.client_export_destinations FROM PUBLIC,anon,authenticated;
CREATE POLICY export_destination_backend ON commerce.client_export_destinations
    FOR ALL TO codebi_app USING (true) WITH CHECK (true);
GRANT SELECT,INSERT ON commerce.client_export_destinations TO codebi_app;
GRANT UPDATE (template_id,is_active,updated_by,updated_at) ON commerce.client_export_destinations TO codebi_app;
COMMENT ON TABLE commerce.client_export_destinations IS 'Client export destinations v1';
COMMIT;
