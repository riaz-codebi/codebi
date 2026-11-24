# Client onboarding

The public **Service portal** link opens the sign-in page. Existing customers sign in with
Google or Microsoft. New visitors submit an inquiry on **Contact**. The public signup toggle
and route have been removed.

The notes below describe the legacy OAuth registration flow. The app no longer starts new
sessions for that flow.

## Registration policy

1. Authenticate through Google/Microsoft and verify email via Supabase get_user.
2. Existing provisioned, active users keep their normal identity-linking/access flow.
3. A verified identity with no matching Auth UUID AND no reserved application email
   receives a restricted, encrypted onboarding session. It is not a portal session.
4. Redirect to /auth/register/client. Show verified sign-in email read-only, allow
   account display name, and reuse the compact company/contact/address fields and
   searchable lookup selectors. Company/contact emails can differ from sign-in email.
5. On submission, re-verify the provider identity and require exactly the same UUID
   and email. Create the user, NEW client, client_admin membership, login history and
   audit records in one SQLAlchemy transaction. Then rotate into a portal session.

The membership can create SOWs. No user_roles/global assignment is inserted; the user
type is always client. Form-supplied role, user type, Auth UUID, client ID and active
status are not accepted as authority. Company-name or email-domain matches never
join/claim existing clients. No automatic client is created from an empty OAuth profile;
the person must complete the company form.

Inactive accounts, reserved/provisioned emails, existing identities with no active
access, and an email already bound to another Auth UUID cannot bypass their status by
self-registering. Registered users who choose Create an account simply sign in to
their existing portal. Memberships and internal super-admin permissions are preserved.

Onboarding expires after 30 minutes. Tokens/verified identity remain encrypted server-side;
browser cookies contain only an opaque state ID and CSRF state. Country/state reference
reads are available only through the restricted onboarding context and expose no client
records. PostgreSQL uniqueness constraints prevent duplicate accounts; failure rolls back
the whole transaction, including the provisional user. The user can correct validation
errors and retry. No business schema migration or provider-setting change is required.

## Verification

Run the full unittest suite. Tests use an isolated SQLAlchemy database and mocked
provider exchange/get_user calls, covering both callback/session branches, CSRF,
expired state, company fields, authoritative email, invalid-data rollback, repeat
registration, reserved/inactive users, and privilege/client-ID tampering.

Browser verification remains manual (browser launch was declined in this session).
After restarting Flask:

- Open the public page, click Log in / Sign up, toggle Create an account, and close it.
- Confirm email/password are disabled and both social buttons work.
- Existing administrator signs in to their existing portal.
- A different, unprovisioned verified Google/Microsoft identity reaches client setup.
- Complete details, confirm it becomes that new client's administrator, and confirm
  it cannot open Primary Administration or see other clients.

No live user, client, or Auth identity was created during local tests. Google Places
address lookup is available through the Address heading's search icon/dialog and server-side adapter;
typing makes no lookup requests. Manual entry and standardized country/state/timezone
choices remain available. See `GOOGLE_PLACES_SETUP.md` for configuration and limits.
