# Reference catalog seed

`reference_catalogs.json` is a checked-in migration input, not a runtime web request.
It contains 249 ISO 3166-1 countries, 5,046 ISO 3166-2 subdivisions, and 598 IANA
timezone identifiers (including supported aliases and UTC).

- Countries/subdivisions: pycountry 26.2.16, based on Debian iso-codes.
  Source: https://github.com/pycountry/pycountry . License: PYCOUNTRY_LICENSE.txt.
- Timezones: Python zoneinfo using tzdata 2026.6 / IANA 2026f.
  Source: https://www.iana.org/time-zones . The upstream tz database is public domain.

The application does not require pycountry at runtime. These are reference labels,
not a statement about geopolitical status. Updates should use a reviewed data migration,
preserving stored ISO codes and timezone keys rather than silently renaming client data.

Country values are two-letter ISO codes, subdivision values are full ISO codes such
as US-TX, and timezone values are IANA names such as America/Chicago. Industry choices
continue to use existing `codebi.industries` rows and are not replaced by this seed.
