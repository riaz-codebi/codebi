# Google address lookup

Client creation, client editing, and verified client self-registration use the same
Flask adapter for Google Places API (New). Geoapify is no longer used. No PostgreSQL
migration is required.

## Configuration

Set `GOOGLE_PLACES_API_KEY` in the ignored `.env`, then restart Flask. The key stays
on the server and must allow Places API (New). IP restrictions must match the
calling server's outbound IPv4 or IPv6; local testing may use IPv6 even when an
IPv4 address was detected separately. Keep production's droplet address allowed.

Optional limits, with defaults:

```dotenv
GOOGLE_PLACES_MONTHLY_LIMIT=500
GOOGLE_PLACES_PER_MINUTE_LIMIT=10
```

The monthly limit counts **combined search and details attempts** in a UTC calendar
month. Failed Google requests count too, and no automatic retry is performed. The
minute limit is per authenticated identity (including pending verified registrants).
Limits are reserved atomically in `instance/address_usage.sqlite3`, shared by local
Gunicorn workers. Storage errors block lookup while leaving manual entry available.
Keep this instance file persistent across deploys; deleting it resets counters.
Separate installations (local PC versus droplet) have independent counters, so this
is not a billing-account-wide spending cap. Other scripts, including the diagnostic
script, do not use these counters. Google quotas/billing monitoring remain separate.

## Request behavior

- Typing, pasting, editing fields, opening the form, or saving a client: no Google calls.
- The search icon beside the **Address** heading opens a compact dialog with a
  query textbox, search icon, and close cross. Opening/closing it makes no requests.
  The query initially includes any existing street, city, and postal code; it can
  be replaced without changing the card's fields.
- Clicking the dialog's search icon (or Enter in the query): one Autocomplete request,
  restricted to the selected US/Canada country, or both when country is blank.
  Minimum query length is five characters.
- Choosing a suggestion: one Place Details request with only address components,
  formatted address, provider attributions, and IANA timezone. Requesting `timeZone`
  uses the Place Details Pro billing category (currently 5,000 free monthly requests,
  then $17 per 1,000 at the first paid tier). No separate Time Zone API is needed.
  The selected address fills the card and closes the dialog; no OK/Cancel step.
- Each selection token expires after ten minutes, is identity-bound and single-use.
  Arbitrary place IDs cannot be submitted to the details action.
- No request is made for every suggestion. The five returned suggestions are shown
  without fetching details until the user explicitly chooses one.
- Suites/floors in address line 2 are preserved. Country/state/timezone selectors
  synchronize with the selected address. Timezone must match an active catalog value;
  otherwise it stays unchanged and the form asks the user to review it. All fields
  remain editable.

The dialog's search icon is disabled during requests. Typing dismisses stale suggestions
without launching another request. Manual entry remains available when the provider
fails, a limit is reached, or a country outside the US/Canada is selected.

## Verification

Run `app/.venv/Scripts/python.exe -B -m unittest discover -s tests` on Windows.
Automated checks cover real protected Flask routes, exact upstream request counts,
US/Canadian state mapping, single-use selections, secret redaction, monthly/minute
limits, failed requests, and concurrent worker reservations.

Run `node tests/address_lookup_events.cjs` where Node is available. This executes the
actual frontend event handlers against a minimal DOM with mocked fetch, checking
zero requests during typing, duplicate-click prevention, one search/one selection
request, field filling, and no extra requests on editing or form submission.
It does not replace a visual browser check.

Browser check after restarting Flask:

1. Open Create client and DevTools > Network, filtered to `address-suggestions`.
2. Type in the card, then open its heading search icon and type a query. Expect zero
   matching requests. Closing with the cross should preserve the card.
3. Click the dialog's search icon. Expect one POST with `action=search`.
4. Select one suggestion. Expect one POST with `action=details`; check populated
   address/city/state/postal/timezone fields, unchanged suite, and a closed dialog.
5. Edit the address without clicking Find. Expect no additional lookup requests.

Google requests go from Flask to Google; the browser only sees same-origin endpoints.
The displayed results include Google Maps attribution. Do not bulk persist suggestion
lists; the adapter stores only expiring place IDs/selection state, not address lists.
Use this feature for users entering and confirming their own street address, following
Google's end-user-address Autocomplete exception. Public terms and privacy pages must
meet Google's requirements before public launch.

Official references:
- https://developers.google.com/maps/documentation/places/web-service/place-autocomplete
- https://developers.google.com/maps/documentation/places/web-service/place-details
- https://developers.google.com/maps/documentation/places/web-service/policies
- https://developers.google.com/maps/billing-and-pricing/pricing
