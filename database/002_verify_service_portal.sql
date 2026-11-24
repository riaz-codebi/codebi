-- Read-only checks to run AFTER 001_service_portal.sql succeeds.
BEGIN READ ONLY;

-- Expect 17 rows, each with row_security_enabled = true.
SELECT tablename, rowsecurity AS row_security_enabled
FROM pg_tables
WHERE schemaname = 'codebi'
  AND tablename IN (
    'industries','clients','users','roles','user_roles','client_memberships',
    'user_invitations','login_events','sow_types','tools','statements_of_work',
    'sow_tools','sow_estimates','sow_messages','sow_events','time_entries','audit_events'
  )
ORDER BY tablename;

-- Expect 3 roles, 5 industries, 5 SOW types, and 11 tools.
SELECT 'roles' AS reference_table, count(*) FROM codebi.roles
UNION ALL SELECT 'industries', count(*) FROM codebi.industries
UNION ALL SELECT 'sow_types', count(*) FROM codebi.sow_types
UNION ALL SELECT 'tools', count(*) FROM codebi.tools;

-- Expect one provisioned global administrator; identity_linked is initially false.
SELECT u.email, u.user_type, u.is_active, r.role_code,
       u.auth_user_id IS NOT NULL AS identity_linked
FROM codebi.users u
JOIN codebi.user_roles r ON r.user_id = u.id
WHERE u.email = 'riaz@codebisolutions.com';

-- Expect zero rows: browser roles should have no direct table privileges.
SELECT table_name, grantee, privilege_type
FROM information_schema.table_privileges
WHERE table_schema = 'codebi'
  AND table_name <> 'test'
  AND grantee IN ('PUBLIC', 'anon', 'authenticated');

-- Existing test table should still be present; its data is not touched.
SELECT to_regclass('codebi.test') IS NOT NULL AS existing_test_table_present;
COMMIT;
