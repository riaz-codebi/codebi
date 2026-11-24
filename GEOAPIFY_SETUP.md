# Address autocomplete

**Replaced by Google Places at the user's request after unsatisfactory results.**
The old Geoapify key is no longer read by Flask. The forms now use click-triggered
Google lookup; see `GOOGLE_PLACES_SETUP.md`. Manual address entry remains active.
The notes below describe the previous integration, retained for reference only.

The existing GEOAPIFY_API_KEY in the ignored .env is read at Flask startup. It is
never rendered into HTML or sent to the browser. Production needs the same environment
variable on the Droplet. Server-side keys may be restricted to the server's outbound
IP in Geoapify; browser-referrer restrictions are unsuitable for this backend integration.

Address Line 1 requests suggestions after three characters and a 350 ms typing pause.
Authenticated super administrators and verified pending self-registration sessions use
separate POST endpoints with CSRF protection. Neither endpoint is available to anonymous
visitors or ordinary client accounts. Requests are limited to five suggestions and
filtered by the chosen country, if any. Addresses are posted to Flask rather than put
in browser-facing query strings. Flask calls Geoapify over HTTPS with an eight-second
timeout and does not return upstream errors or request URLs to the browser.

Selecting a suggestion fills street/address line 1, city, postal code, country, matching
ISO subdivision and matching IANA timezone. The existing country/state loader is awaited
before selecting the state. Ambiguous or missing state/timezone values require manual
review. Address Line 2 is never overwritten; Geoapify's address_line2 is locality text,
not a client's suite/floor. Fields remain editable and nothing is saved until submission.
Provider failures or no matches leave manual entry available. Suggestions display Geoapify
and OpenStreetMap attribution. No map, third-party JS dependency, or schema migration is used.

The configured key returned HTTP 200 and a suggestion for one public-address verification
query. Tests mock upstream responses and check access, CSRF, country/code mapping, key
non-disclosure, failures and short-query suppression. Interactive browser verification
remains manual: type an address, choose a suggestion by mouse/keyboard, confirm fields,
verify suite text stays unchanged, then save. Restart Flask after environment/code changes.
