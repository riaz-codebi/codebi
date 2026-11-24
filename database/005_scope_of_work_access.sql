-- Backend permissions only. Workflow and client isolation remain in Flask services.
-- No new tables, schema changes, business functions or changes to existing records.
BEGIN;
GRANT SELECT ON codebi.sow_types, codebi.tools TO codebi_app;
GRANT SELECT, INSERT, UPDATE ON codebi.statements_of_work TO codebi_app;
GRANT SELECT, INSERT, DELETE ON codebi.sow_tools TO codebi_app;
GRANT SELECT, INSERT ON codebi.sow_events TO codebi_app;
GRANT USAGE ON SEQUENCE codebi.statements_of_work_sow_number_seq TO codebi_app;
DO $$
DECLARE tab text;
BEGIN
    FOREACH tab IN ARRAY ARRAY['sow_types', 'tools', 'statements_of_work', 'sow_tools', 'sow_events'] LOOP
        EXECUTE format('ALTER TABLE codebi.%I ENABLE ROW LEVEL SECURITY', tab);
        EXECUTE format('REVOKE ALL ON codebi.%I FROM PUBLIC, anon, authenticated', tab);
        EXECUTE format('DROP POLICY IF EXISTS flask_sow_access ON codebi.%I', tab);
        EXECUTE format('CREATE POLICY flask_sow_access ON codebi.%I FOR ALL TO codebi_app USING (true) WITH CHECK (true)', tab);
    END LOOP;
END;
$$;
COMMIT;
