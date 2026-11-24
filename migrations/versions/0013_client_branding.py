"""Private, client-scoped Storage for portal logos."""
from alembic import op
import sqlalchemy as sa

revision = '0013_client_branding'
down_revision = '0012_public_inquiries'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('clients', sa.Column('logo_path', sa.Text()), schema='codebi')
    op.create_check_constraint('client_logo_own_path', 'clients',
        "logo_path = id::text || '/brand/logo.png'", schema='codebi')
    op.execute("""
        INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
        VALUES ('client-branding', 'client-branding', false, 2097152, ARRAY['image/png', 'application/octet-stream'])
    """)
    op.execute(r"""
        CREATE SCHEMA codebi_branding;
        CREATE FUNCTION codebi_branding.can_access(object_name text, writing boolean)
        RETURNS boolean LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE client_id uuid;
        BEGIN
            IF object_name !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/brand/(logo[.]png|[.]emptyFolderPlaceholder)$' THEN
                RETURN false;
            END IF;
            client_id := split_part(object_name, '/', 1)::uuid;
            RETURN EXISTS (
                SELECT 1 FROM codebi.users u
                JOIN codebi.clients c ON c.id = client_id
                WHERE u.auth_user_id = (SELECT auth.uid()) AND u.is_active
                  AND (
                    (u.user_type = 'internal' AND EXISTS (
                        SELECT 1 FROM codebi.user_roles r WHERE r.user_id = u.id AND r.role_code = 'super_admin'
                    ))
                    OR (NOT writing AND c.is_active AND object_name = c.logo_path AND EXISTS (
                        SELECT 1 FROM codebi.client_memberships m
                        WHERE m.user_id = u.id AND m.client_id = c.id AND m.is_active
                    ))
                  )
            );
        END
        $$;
        REVOKE ALL ON FUNCTION codebi_branding.can_access(text, boolean) FROM PUBLIC;
        GRANT USAGE ON SCHEMA codebi_branding TO authenticated;
        GRANT EXECUTE ON FUNCTION codebi_branding.can_access(text, boolean) TO authenticated;
    """)
    op.execute("""
        CREATE POLICY client_branding_read ON storage.objects FOR SELECT TO authenticated
        USING (bucket_id = 'client-branding' AND codebi_branding.can_access(name, false))
    """)
    op.execute("""
        CREATE POLICY client_branding_insert ON storage.objects FOR INSERT TO authenticated
        WITH CHECK (bucket_id = 'client-branding' AND codebi_branding.can_access(name, true))
    """)
    op.execute("""
        CREATE POLICY client_branding_update ON storage.objects FOR UPDATE TO authenticated
        USING (bucket_id = 'client-branding' AND codebi_branding.can_access(name, true))
        WITH CHECK (bucket_id = 'client-branding' AND codebi_branding.can_access(name, true))
    """)


def downgrade():
    raise RuntimeError('Client branding may contain uploaded files. Remove it through the Storage API before retiring the migration.')
