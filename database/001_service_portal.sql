-- Run ONCE in the Supabase SQL Editor as the project database administrator.
-- Foundation only: browser/API access stays denied until reviewed policies and
-- workflow functions are added with the application. No existing tables are dropped.
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '60s';
CREATE SCHEMA IF NOT EXISTS codebi;

CREATE TABLE codebi.industries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL UNIQUE CHECK (btrim(name) <> ''),
    description text,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE codebi.clients (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_number bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    client_code text GENERATED ALWAYS AS ('CLI-' || lpad(client_number::text, greatest(4, length(client_number::text)), '0')) STORED UNIQUE,
    name text NOT NULL CHECK (btrim(name) <> ''),
    legal_name text,
    industry_id uuid REFERENCES codebi.industries(id),
    website text,
    email text,
    phone text,
    contact_name text,
    contact_email text,
    contact_phone text,
    address_line_1 text,
    address_line_2 text,
    city text,
    state_region text,
    postal_code text,
    country_code text CHECK (country_code ~ '^[A-Z]{2}$'),
    timezone text NOT NULL DEFAULT 'UTC',
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- A provisioned user can exist before their first OAuth sign-in.
-- The application must link a verified identity through a controlled server flow.
CREATE TABLE codebi.users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    auth_user_id uuid UNIQUE REFERENCES auth.users(id) ON DELETE SET NULL,
    email text NOT NULL UNIQUE CHECK (email = lower(btrim(email)) AND email ~ '^[^[:space:]@]+@[^[:space:]@]+[.][^[:space:]@]+$'),
    display_name text NOT NULL CHECK (btrim(display_name) <> ''),
    phone text,
    user_type text NOT NULL CHECK (user_type IN ('internal', 'client')),
    is_active boolean NOT NULL DEFAULT true,
    last_login_at timestamptz,
    created_by uuid REFERENCES codebi.users(id),
    updated_by uuid REFERENCES codebi.users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE codebi.clients
    ADD COLUMN created_by uuid REFERENCES codebi.users(id),
    ADD COLUMN updated_by uuid REFERENCES codebi.users(id);

CREATE TABLE codebi.roles (
    code text PRIMARY KEY,
    name text NOT NULL,
    description text,
    scope text NOT NULL CHECK (scope IN ('global', 'client')),
    UNIQUE (code, scope)
);
CREATE TABLE codebi.user_roles (
    user_id uuid NOT NULL REFERENCES codebi.users(id),
    role_code text NOT NULL,
    role_scope text NOT NULL DEFAULT 'global' CHECK (role_scope = 'global'),
    granted_by uuid REFERENCES codebi.users(id),
    granted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, role_code),
    FOREIGN KEY (role_code, role_scope) REFERENCES codebi.roles(code, scope)
);
CREATE TABLE codebi.client_memberships (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES codebi.clients(id),
    user_id uuid NOT NULL REFERENCES codebi.users(id),
    role_code text NOT NULL DEFAULT 'client_user',
    role_scope text NOT NULL DEFAULT 'client' CHECK (role_scope = 'client'),
    can_create_sows boolean NOT NULL DEFAULT false,
    is_active boolean NOT NULL DEFAULT true,
    granted_by uuid REFERENCES codebi.users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, user_id),
    FOREIGN KEY (role_code, role_scope) REFERENCES codebi.roles(code, scope)
);
CREATE TABLE codebi.user_invitations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES codebi.clients(id),
    email text NOT NULL CHECK (email = lower(btrim(email)) AND position('@' in email) > 1),
    role_code text NOT NULL DEFAULT 'client_user',
    role_scope text NOT NULL DEFAULT 'client' CHECK (role_scope = 'client'),
    can_create_sows boolean NOT NULL DEFAULT false,
    invited_by uuid NOT NULL REFERENCES codebi.users(id),
    token_hash text UNIQUE,
    expires_at timestamptz NOT NULL,
    accepted_by uuid REFERENCES codebi.users(id),
    accepted_at timestamptz,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_at > created_at),
    CHECK ((accepted_by IS NULL) = (accepted_at IS NULL)),
    CHECK (accepted_at IS NULL OR (revoked_at IS NULL AND accepted_at <= expires_at)),
    FOREIGN KEY (role_code, role_scope) REFERENCES codebi.roles(code, scope)
);
CREATE TABLE codebi.login_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid REFERENCES codebi.users(id),
    provider text NOT NULL CHECK (provider IN ('google', 'azure')),
    outcome text NOT NULL CHECK (outcome IN ('success', 'denied', 'failure')),
    reason_code text,
    ip_address inet,
    user_agent text,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE codebi.sow_types (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL UNIQUE,
    description text,
    is_active boolean NOT NULL DEFAULT true
);
CREATE TABLE codebi.tools (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL UNIQUE,
    description text,
    is_active boolean NOT NULL DEFAULT true
);
CREATE TABLE codebi.statements_of_work (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sow_number bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    sow_code text GENERATED ALWAYS AS ('SOW-' || lpad(sow_number::text, greatest(6, length(sow_number::text)), '0')) STORED UNIQUE,
    client_id uuid NOT NULL REFERENCES codebi.clients(id),
    title text NOT NULL CHECK (btrim(title) <> ''),
    description text NOT NULL CHECK (btrim(description) <> ''),
    sow_type_id uuid NOT NULL REFERENCES codebi.sow_types(id),
    priority text NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    desired_completion_date date,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN (
        'draft', 'pending_client_approval', 'submitted', 'under_review',
        'clarification_needed', 'awaiting_acceptance', 'approved',
        'in_progress', 'completed', 'declined', 'cancelled')),
    created_by uuid NOT NULL REFERENCES codebi.users(id),
    updated_by uuid REFERENCES codebi.users(id),
    submitted_by uuid REFERENCES codebi.users(id),
    submitted_at timestamptz,
    accepted_estimate_id uuid,
    started_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, id),
    CHECK ((submitted_by IS NULL) = (submitted_at IS NULL)),
    CHECK (status NOT IN ('submitted', 'under_review', 'clarification_needed', 'awaiting_acceptance', 'approved', 'in_progress', 'completed') OR submitted_at IS NOT NULL),
    CHECK (status NOT IN ('approved', 'in_progress', 'completed') OR accepted_estimate_id IS NOT NULL),
    CHECK (status NOT IN ('in_progress', 'completed') OR started_at IS NOT NULL),
    CHECK (status <> 'completed' OR completed_at IS NOT NULL),
    CHECK (completed_at IS NULL OR (started_at IS NOT NULL AND completed_at >= started_at))
);
CREATE TABLE codebi.sow_tools (
    sow_id uuid NOT NULL REFERENCES codebi.statements_of_work(id),
    tool_id uuid NOT NULL REFERENCES codebi.tools(id),
    PRIMARY KEY (sow_id, tool_id)
);
CREATE TABLE codebi.sow_estimates (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL,
    sow_id uuid NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    scope_snapshot text NOT NULL CHECK (btrim(scope_snapshot) <> ''),
    feasibility text NOT NULL CHECK (feasibility IN ('feasible', 'not_feasible', 'needs_clarification')),
    min_hours numeric(10,2),
    max_hours numeric(10,2),
    assumptions text,
    exclusions text,
    notes text,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'issued', 'accepted', 'rejected', 'superseded')),
    created_by uuid NOT NULL REFERENCES codebi.users(id),
    issued_at timestamptz,
    decided_by uuid REFERENCES codebi.users(id),
    decided_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (sow_id, revision),
    UNIQUE (client_id, sow_id, id),
    FOREIGN KEY (client_id, sow_id) REFERENCES codebi.statements_of_work(client_id, id),
    CHECK ((min_hours IS NULL AND max_hours IS NULL) OR (min_hours IS NOT NULL AND max_hours IS NOT NULL AND min_hours > 0 AND max_hours >= min_hours AND max_hours < 'Infinity'::numeric)),
    CHECK (status NOT IN ('issued', 'accepted') OR feasibility <> 'feasible' OR min_hours IS NOT NULL),
    CHECK (status = 'draft' OR issued_at IS NOT NULL),
    CHECK ((decided_by IS NULL) = (decided_at IS NULL)),
    CHECK (status NOT IN ('accepted', 'rejected') OR decided_at IS NOT NULL),
    CHECK (status <> 'accepted' OR feasibility = 'feasible'),
    CHECK (decided_at IS NULL OR (issued_at IS NOT NULL AND decided_at >= issued_at))
);
CREATE UNIQUE INDEX sow_one_accepted_estimate ON codebi.sow_estimates(sow_id) WHERE status = 'accepted';
ALTER TABLE codebi.statements_of_work ADD CONSTRAINT sow_accepted_estimate_fk
    FOREIGN KEY (client_id, id, accepted_estimate_id)
    REFERENCES codebi.sow_estimates(client_id, sow_id, id);

CREATE TABLE codebi.sow_messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL,
    sow_id uuid NOT NULL,
    author_id uuid NOT NULL REFERENCES codebi.users(id),
    message_type text NOT NULL DEFAULT 'general' CHECK (message_type IN ('general', 'clarification', 'reply')),
    body text NOT NULL CHECK (btrim(body) <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, sow_id, id),
    FOREIGN KEY (client_id, sow_id) REFERENCES codebi.statements_of_work(client_id, id)
);
CREATE TABLE codebi.sow_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL,
    sow_id uuid NOT NULL,
    actor_id uuid NOT NULL REFERENCES codebi.users(id),
    event_type text NOT NULL CHECK (btrim(event_type) <> ''),
    from_status text,
    to_status text,
    estimate_id uuid,
    message_id uuid,
    details jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details) = 'object'),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (client_id, sow_id) REFERENCES codebi.statements_of_work(client_id, id),
    FOREIGN KEY (client_id, sow_id, estimate_id) REFERENCES codebi.sow_estimates(client_id, sow_id, id),
    FOREIGN KEY (client_id, sow_id, message_id) REFERENCES codebi.sow_messages(client_id, sow_id, id)
);
CREATE TABLE codebi.time_entries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL,
    sow_id uuid NOT NULL,
    worker_id uuid NOT NULL REFERENCES codebi.users(id),
    activity text NOT NULL CHECK (btrim(activity) <> ''),
    started_at timestamptz NOT NULL,
    ended_at timestamptz NOT NULL,
    duration_minutes numeric GENERATED ALWAYS AS (extract(epoch FROM (ended_at - started_at)) / 60.0) STORED,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'submitted', 'void')),
    internal_notes text,
    void_reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (client_id, sow_id) REFERENCES codebi.statements_of_work(client_id, id),
    CHECK (ended_at > started_at),
    CHECK (status <> 'void' OR nullif(btrim(void_reason), '') IS NOT NULL)
);
CREATE TABLE codebi.audit_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid REFERENCES codebi.clients(id),
    actor_id uuid NOT NULL REFERENCES codebi.users(id),
    action text NOT NULL CHECK (btrim(action) <> ''),
    entity_type text NOT NULL,
    entity_id uuid,
    changes jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(changes) = 'object'),
    occurred_at timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION codebi.set_updated_at() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;
CREATE FUNCTION codebi.reject_history_change() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'History records are append-only';
END;
$$;

-- Limit these changes to the explicitly listed new tables, leaving codebi.test alone.
DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'industries','clients','users','roles','user_roles','client_memberships',
        'user_invitations','login_events','sow_types','tools','statements_of_work',
        'sow_tools','sow_estimates','sow_messages','sow_events','time_entries','audit_events'
    ] LOOP
        EXECUTE format('ALTER TABLE codebi.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('REVOKE ALL ON TABLE codebi.%I FROM PUBLIC, anon, authenticated', table_name);
    END LOOP;
    FOREACH table_name IN ARRAY ARRAY[
        'industries','clients','users','client_memberships','statements_of_work','sow_estimates','time_entries'
    ] LOOP
        EXECUTE format('CREATE TRIGGER set_updated_at BEFORE UPDATE ON codebi.%I FOR EACH ROW EXECUTE FUNCTION codebi.set_updated_at()', table_name);
    END LOOP;
    FOREACH table_name IN ARRAY ARRAY['sow_messages','sow_events','audit_events'] LOOP
        EXECUTE format('CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON codebi.%I FOR EACH ROW EXECUTE FUNCTION codebi.reject_history_change()', table_name);
    END LOOP;
END;
$$;
REVOKE ALL ON FUNCTION codebi.set_updated_at() FROM PUBLIC;
REVOKE ALL ON FUNCTION codebi.reject_history_change() FROM PUBLIC;

CREATE INDEX clients_industry_idx ON codebi.clients(industry_id);
CREATE INDEX memberships_user_idx ON codebi.client_memberships(user_id, client_id);
CREATE INDEX invitations_email_idx ON codebi.user_invitations(email, client_id);
CREATE INDEX login_events_user_time_idx ON codebi.login_events(user_id, occurred_at DESC);
CREATE INDEX sows_client_status_idx ON codebi.statements_of_work(client_id, status, created_at DESC);
CREATE INDEX sows_type_idx ON codebi.statements_of_work(sow_type_id);
CREATE INDEX sow_tools_tool_idx ON codebi.sow_tools(tool_id);
CREATE INDEX estimates_sow_idx ON codebi.sow_estimates(client_id, sow_id, created_at DESC);
CREATE INDEX messages_sow_idx ON codebi.sow_messages(client_id, sow_id, created_at);
CREATE INDEX events_sow_idx ON codebi.sow_events(client_id, sow_id, occurred_at);
CREATE INDEX time_sow_idx ON codebi.time_entries(client_id, sow_id, status);
CREATE INDEX time_worker_idx ON codebi.time_entries(worker_id, started_at);
CREATE INDEX audit_client_time_idx ON codebi.audit_events(client_id, occurred_at DESC);

INSERT INTO codebi.roles (code, name, scope, description) VALUES
('super_admin', 'Super Administrator', 'global', 'Internal CodeBI administrator across all clients'),
('client_admin', 'Client Administrator', 'client', 'Manages users and approves statements of work for their client'),
('client_user', 'Client User', 'client', 'Views client work; creation requires separate permission');
INSERT INTO codebi.industries (name) VALUES
('Healthcare'), ('Technology'), ('Finance'), ('Professional Services'), ('Other');
INSERT INTO codebi.sow_types (name) VALUES
('New Development'), ('Enhancement'), ('Maintenance'), ('Troubleshooting'), ('Consulting');
INSERT INTO codebi.tools (name) VALUES
('Power BI'), ('Python'), ('Flask'), ('FastAPI'), ('SharePoint'), ('Power Automate'),
('Power Apps'), ('Excel'), ('PostgreSQL'), ('Supabase'), ('Other');
INSERT INTO codebi.users (email, display_name, user_type)
VALUES ('riaz@codebisolutions.com', 'Riaz Haider', 'internal');
INSERT INTO codebi.user_roles (user_id, role_code)
SELECT id, 'super_admin' FROM codebi.users WHERE email = 'riaz@codebisolutions.com';

COMMENT ON SCHEMA codebi IS 'CodeBI Service Portal. Access policies and controlled workflow functions must be installed before enabling application access.';
COMMENT ON TABLE codebi.users IS 'Provisioned portal users; no passwords or OAuth tokens. Supabase Auth owns authentication.';
COMMENT ON TABLE codebi.sow_estimates IS 'Versioned proposed scope and hours; workflow must freeze issued revisions and accept atomically.';
COMMENT ON TABLE codebi.time_entries IS 'Only submitted entries contribute to client totals. Internal notes must not be included in client-facing responses.';
COMMIT;
