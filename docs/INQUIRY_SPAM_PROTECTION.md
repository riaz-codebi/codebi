# Inquiry spam protection

The public `/contact` POST now checks the submission before saving a
`public_inquiries` record or sending either the team email or sender confirmation.
No external classifier, new API key, package or PostgreSQL migration is required.

## Checks

- Existing CSRF validation remains mandatory. A second signed form token is bound
  to that browser's CSRF value. It expires after two hours. Missing, altered or
  expired tokens require the visitor to review and submit the refreshed form.
- An off-screen field catches basic autofill bots. Filled traps receive the usual
  acknowledgment without storing an inquiry or sending mail.
- Submissions within two seconds of loading the form ask the visitor to wait and
  retry; legitimate form values and the original valid token are retained.
- Content checks reject single random mixed-case identifiers like the supplied
  spam samples, link-only messages, four or more links, script/iframe markup,
  forum-link markup and control characters in sender/name/company fields.
  The visitor can revise the message and retry. Languages, countries and free
  email providers are not blacklisted. A foreign-language message alone is not
  reliable evidence of spam. There is no user-controlled subject field; the
  notification subject is fixed by Flask.
- Submission limits are three per claimed email and five per browser session
  in 15 minutes. A public peer IP is limited to ten per 15 minutes; IPv6 peers
  share a /64 bucket. A global ceiling allows 30 otherwise eligible submissions
  per hour, bounding mail volume even when bots rotate addresses and cookies.
- A duplicate message from the same normalized email within 15 minutes does not
  create another inquiry or send either email. The existing five-minute sender
  confirmation cooldown is retained for distinct follow-up messages.

These are conservative local rules, not a semantic AI classifier or verification
that the submitted email belongs to the visitor. Determined bots can still submit
plausible text. Monitor results after deployment before tightening rules; CAPTCHA
or email verification is a separate possible next layer if abuse continues.

## Runtime and deployment

The existing Gunicorn workers share atomic counters in
`instance/inquiry_usage.sqlite3`, next to the existing auth session store. Only
HMAC identifiers, timestamps and counts are stored there, with expired counters
removed. No message text, plain email address or plain IP is stored in that file.
Logging records only the rejection category or exception type.

The application user needs write access to the existing instance directory.
If the counter store is unavailable, the form returns 503 and sends no mail.
Limits persist across worker restarts as long as the file and `SECRET_KEY` remain
the same. Multiple servers would need a shared counter store before scaling out.

Flask deliberately ignores arbitrary `X-Forwarded-For` headers. When Nginx is the
private/loopback peer, the peer-IP limit is skipped, preventing all visitors from
being grouped as one sender. Email/session limits and the global ceiling still
apply. Enable real-client IP handling only after reviewing the actual trusted
proxy chain and direct access restrictions; no proxy configuration was changed.

Defaults are in `create_app` and can be overridden through application config:
`INQUIRY_MIN_SECONDS`, `INQUIRY_SESSION_LIMIT`, `INQUIRY_EMAIL_LIMIT`,
`INQUIRY_IP_LIMIT`, and `INQUIRY_HOURLY_LIMIT`. Tests use an isolated counter path
derived from their temporary auth stores and disable timing only where timing
is not being tested. Timing, concurrency, multilingual input, spoofed forwarded
headers, duplicates and failure behavior have dedicated tests.

Deploy the code/template/CSS changes together and restart the existing Flask
service. No database setup script or new `.env` credential is needed. Changes
remain local until explicitly committed, pushed and deployed; existing spam
messages and saved inquiries are not deleted by this change.
