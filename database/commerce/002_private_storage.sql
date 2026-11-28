-- Install after 001. Storage is private. No service-role bypass key is required.
BEGIN;
INSERT INTO storage.buckets(id,name,public,file_size_limit)
VALUES ('commerce-artifacts','commerce-artifacts',false,52428800)
ON CONFLICT(id) DO NOTHING;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM storage.buckets WHERE id='commerce-artifacts' AND public) THEN
        RAISE EXCEPTION 'Commerce artifact bucket must be private';
    END IF;
END; $$;
CREATE FUNCTION commerce.can_download_artifact(p_name text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path='' AS $$
SELECT EXISTS (
    SELECT 1 FROM commerce.exports e
    JOIN codebi.clients c ON c.id=e.client_id AND c.is_active
    JOIN codebi.client_app_access a ON a.client_id=e.client_id AND a.app_code='commerce_hub' AND a.is_active
    JOIN codebi.commercial_apps app ON app.code=a.app_code AND app.is_active
    JOIN codebi.users u ON u.auth_user_id=auth.uid() AND u.is_active
    WHERE e.artifact_path=p_name
      AND a.features ? CASE WHEN e.mapping_id IS NULL THEN 'vendor_export' ELSE 'shopify_export' END
      AND (EXISTS (SELECT 1 FROM codebi.client_memberships m WHERE m.client_id=c.id AND m.user_id=u.id AND m.is_active)
        OR (u.user_type='internal' AND EXISTS(SELECT 1 FROM codebi.user_roles r WHERE r.user_id=u.id AND r.role_code='super_admin')))
);
$$;
REVOKE ALL ON FUNCTION commerce.can_download_artifact(text) FROM PUBLIC,anon;
GRANT USAGE ON SCHEMA commerce TO authenticated;
GRANT EXECUTE ON FUNCTION commerce.can_download_artifact(text) TO authenticated;
CREATE POLICY commerce_artifact_download ON storage.objects FOR SELECT TO authenticated
USING (bucket_id='commerce-artifacts' AND commerce.can_download_artifact(name));
GRANT commerce_worker TO authenticator;
GRANT USAGE ON SCHEMA storage TO commerce_worker;
GRANT SELECT,INSERT ON storage.objects TO commerce_worker;
GRANT SELECT ON storage.buckets TO commerce_worker;
CREATE POLICY commerce_worker_bucket ON storage.buckets FOR SELECT TO commerce_worker USING (id='commerce-artifacts');
CREATE POLICY commerce_worker_upload ON storage.objects FOR INSERT TO commerce_worker
WITH CHECK (bucket_id='commerce-artifacts' AND name ~ '^[0-9a-f-]{36}/(exports|sources)/[0-9a-f-]{36}/[^/]+$' AND position('..' in name)=0);
CREATE POLICY commerce_worker_artifact_read ON storage.objects FOR SELECT TO commerce_worker
USING (bucket_id='commerce-artifacts');
COMMIT;
