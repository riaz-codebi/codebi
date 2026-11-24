-- Privileges only; no portal business functions. Called by setup_app_database.py.
-- The script creates a dedicated LOGIN role and configures its password separately.
GRANT USAGE ON SCHEMA codebi TO codebi_app;
GRANT SELECT ON codebi.industries, codebi.roles TO codebi_app;
GRANT SELECT, INSERT, UPDATE ON codebi.clients, codebi.users, codebi.client_memberships TO codebi_app;
GRANT SELECT, INSERT ON codebi.user_roles TO codebi_app;
GRANT INSERT ON codebi.login_events, codebi.audit_events TO codebi_app;
GRANT USAGE ON SEQUENCE codebi.clients_client_number_seq TO codebi_app;

-- RLS remains enabled and anon/authenticated table access stays closed.
-- Only the trusted Flask role gets these policies. Python services isolate clients.
DO $$
DECLARE tab text;
BEGIN
    FOREACH tab IN ARRAY ARRAY['industries', 'roles', 'clients', 'users', 'client_memberships', 'user_roles', 'login_events', 'audit_events'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS flask_backend_access ON codebi.%I', tab);
        EXECUTE format('CREATE POLICY flask_backend_access ON codebi.%I FOR ALL TO codebi_app USING (true) WITH CHECK (true)', tab);
    END LOOP;
END;
$$;
