# Searchable client fields

Industry, Country, State/region, and Timezone are searchable single-select controls.
They retain compact form spacing and native-select fallback. There are no CDN calls
or third-party JavaScript libraries; the enhancement is served from
`app/static/js/searchable-select.js`.

The inputs support typed filtering, accent-insensitive matching, ISO-code search,
Arrow Up/Down, Home/End, Enter, Escape and Tab. Labels/combobox/listbox relationships
are exposed through ARIA. Values are still submitted by native select elements;
typing arbitrary text never creates a catalog entry. Required timezone validation
focuses the visible control. Industry values come from existing database rows.

## Database setup

Run the reviewed reference migration using the existing virtual environment:

```powershell
.\app\.venv\Scripts\python.exe -B -m alembic upgrade 0002_reference_catalogs
```

It adds `codebi.countries` (249), `codebi.state_regions` (5,046), and
`codebi.timezones` (598), and `clients.state_region_code`. Country and timezone
foreign keys and a country/state composite foreign key reject mismatched changed
values. Lookup tables have RLS and SELECT-only access for codebi_app; direct
anon/authenticated access is denied. No stored business functions are introduced.

The migration was inserted ahead of the verified-unapplied 0002_retire_auth_rpc
revision; its revision ID is unchanged. The explicit target above does not execute
that optional cleanup. The original 0001 adoption checks are now frozen against
original columns so future ORM additions cannot alter baseline requirements.

Seed data and source/license information are checked in under database/data. The
application needs no pycountry runtime dependency. Catalog updates require another
reviewed data migration; do not edit an already applied migration's seed.

## Existing client values

Known stored state names/abbreviations are linked only when the country match is
unambiguous. Original state text is retained by the migration; subsequent standard
selections save the readable name plus ISO code. Unmatched historical state text is
displayed as a saved value and can be retained on the same client/country. It is not
silently replaced, nor made available to new clients.

Existing nonstandard country/timezone keys are preserved. Their constraints remain
NOT VALID if old unmatched rows exist; new changed keys are still checked. The
constraints are validated during migration when all existing values match. Inactive
catalog values can be retained by existing records but not newly assigned. No client
records or existing industry entries are deleted. Automatic state linking updates
that client's update timestamp through the existing integrity trigger.

## Country-dependent states

Choose Country first, then State/region. The authenticated global-admin endpoint
`/portal/admin/reference/regions?country=US` supplies only that country's rows.
Country changes clear the old state. Concurrent requests are cancelled/ignored so
an older response cannot repopulate the wrong country's states. Saving is blocked
while loading or after a load failure; Retry fetches again. Countries with no listed
subdivisions can be saved without a state. State is optional; timezone remains required.

Timezone uses IANA identifiers such as America/Chicago rather than fixed UTC offsets
that would become inaccurate with daylight saving changes. All database validation
stays in Python services alongside existing constraints. The lookup API exposes no
client records and is denied to ordinary client users and client administrators.

## Verification

Run the unittest suite for actual service queries, validation, preserved legacy values,
inactive catalogs, and country-scoped API access. Browser launch was declined during
this change, so interactive behavior still needs a manual browser check:

1. Select United States; type T in State/region and choose Tennessee or Texas.
2. Change country and confirm the old state clears and only new-country states appear.
3. Search industry and timezone, use keyboard navigation and save the client.
4. Confirm saved choices persist on edit, and the form remains compact on smaller screens.
