-- Run after 001 and 002. No table grants or broad RLS policies are added.
-- Only two user-scoped RPC endpoints are exposed in public (Supabase's default API schema).
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '60s';

-- Private helper: must never be granted to browser roles.
CREATE FUNCTION codebi.portal_identity_context(p_auth_id uuid)
RETURNS jsonb LANGUAGE sql STABLE
SET search_path = '' AS $$
    SELECT COALESCE((
        SELECT jsonb_build_object(
            'allowed', u.is_active AND (
                (u.user_type = 'internal' AND EXISTS (
                    SELECT 1 FROM codebi.user_roles r WHERE r.user_id = u.id AND r.role_code = 'super_admin'
                )) OR EXISTS (
                    SELECT 1 FROM codebi.client_memberships m JOIN codebi.clients c ON c.id = m.client_id
                    WHERE m.user_id = u.id AND m.is_active AND c.is_active
                )
            ),
            'user_id', u.id, 'display_name', u.display_name,
            'is_super_admin', u.user_type = 'internal' AND EXISTS (
                SELECT 1 FROM codebi.user_roles r WHERE r.user_id = u.id AND r.role_code = 'super_admin'
            ),
            'clients', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', c.id, 'name', c.name, 'role', m.role_code,
                    'can_create_sows', m.can_create_sows OR m.role_code = 'client_admin'
                ) ORDER BY c.name, c.id)
                FROM codebi.client_memberships m JOIN codebi.clients c ON c.id = m.client_id
                WHERE m.user_id = u.id AND m.is_active AND c.is_active
            ), '[]'::jsonb)
        ) FROM codebi.users u WHERE u.auth_user_id = p_auth_id AND u.is_active
    ), '{"allowed": false}'::jsonb);
$$;
REVOKE ALL ON FUNCTION codebi.portal_identity_context(uuid) FROM PUBLIC, anon, authenticated;

CREATE FUNCTION public.codebi_portal_context()
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = '' AS $$
    SELECT codebi.portal_identity_context(auth.uid());
$$;
REVOKE ALL ON FUNCTION public.codebi_portal_context() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.codebi_portal_context() TO authenticated;

CREATE FUNCTION public.codebi_portal_login(p_provider text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path = '' AS $$
DECLARE
    v_auth_id uuid := auth.uid();
    v_email text;
    v_verified boolean;
    v_user codebi.users%ROWTYPE;
    v_context jsonb;
BEGIN
    IF v_auth_id IS NULL OR p_provider NOT IN ('google', 'azure') OR p_provider IS NULL THEN
        RETURN '{"allowed": false}'::jsonb;
    END IF;
    SELECT lower(btrim(email)), email_confirmed_at IS NOT NULL INTO v_email, v_verified
    FROM auth.users WHERE id = v_auth_id;

    -- Require provider-maintained identity data, NOT editable user_metadata.
    -- Microsoft should supply xms_edov/email optional claims so Supabase can
    -- establish email_verified. Never disable this check to work around a login.
    IF NOT COALESCE(v_verified, false) OR NOT EXISTS (
        SELECT 1 FROM auth.identities i
        WHERE i.user_id = v_auth_id AND i.provider = p_provider
          AND lower(btrim(i.identity_data ->> 'email')) = v_email
          AND i.identity_data ->> 'email_verified' = 'true'
    ) THEN
        INSERT INTO codebi.login_events (provider, outcome, reason_code)
        VALUES (p_provider, 'denied', 'unverified_identity');
        RETURN '{"allowed": false}'::jsonb;
    END IF;

    -- An existing identity binding takes priority; email only links an unbound,
    -- explicitly provisioned record. Row locking prevents competing first links.
    SELECT * INTO v_user FROM codebi.users
    WHERE auth_user_id = v_auth_id OR (auth_user_id IS NULL AND email = v_email)
    ORDER BY (auth_user_id IS NOT NULL) DESC LIMIT 1 FOR UPDATE;
    IF NOT FOUND OR NOT v_user.is_active THEN
        INSERT INTO codebi.login_events (provider, outcome, reason_code)
        VALUES (p_provider, 'denied', 'not_provisioned_or_inactive');
        RETURN '{"allowed": false}'::jsonb;
    END IF;

    -- Check access before binding. Do not create users, memberships or roles here.
    IF NOT (
        (v_user.user_type = 'internal' AND EXISTS (
            SELECT 1 FROM codebi.user_roles WHERE user_id = v_user.id AND role_code = 'super_admin'
        )) OR EXISTS (
            SELECT 1 FROM codebi.client_memberships m JOIN codebi.clients c ON c.id = m.client_id
            WHERE m.user_id = v_user.id AND m.is_active AND c.is_active
        )
    ) THEN
        INSERT INTO codebi.login_events (user_id, provider, outcome, reason_code)
        VALUES (v_user.id, p_provider, 'denied', 'no_active_access');
        RETURN '{"allowed": false}'::jsonb;
    END IF;

    UPDATE codebi.users SET auth_user_id = v_auth_id, last_login_at = now()
    WHERE id = v_user.id;
    INSERT INTO codebi.login_events (user_id, provider, outcome)
    VALUES (v_user.id, p_provider, 'success');
    v_context := codebi.portal_identity_context(v_auth_id);
    RETURN v_context;
END;
$$;
REVOKE ALL ON FUNCTION public.codebi_portal_login(text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.codebi_portal_login(text) TO authenticated;

COMMENT ON FUNCTION public.codebi_portal_login(text) IS 'Verified OAuth identity -> provisioned active portal user. No arbitrary user ID or role inputs.';
COMMENT ON FUNCTION public.codebi_portal_context() IS 'Returns only the caller portal identity and active client memberships.';
NOTIFY pgrst, 'reload schema';
COMMIT;
