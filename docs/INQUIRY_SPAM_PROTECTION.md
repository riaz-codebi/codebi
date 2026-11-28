# Inquiry spam protection

The public `/contact` POST now checks the submission before saving a
`public_inquiries` record or sending either the team email or sender confirmation.
No external classifier, new API key or package is required. Staff review metadata
requires Alembic revision `0015_inquiry_review` before deploying the new application.

## Manual review and email behavior

Public submissions save a pending inquiry and show the browser a thank-you message.
They never call Resend: neither the team nor the supplied sender receives an email.
New inquiries appear in Primary Administration > Inquiries for global administrators.
Staff can mark an inquiry legitimate or delete it from the queue. Delete is a soft
delete that retains the record and review audit history, without sending an email.

Mark legitimate sends the sender one formatted acknowledgment with a plain-text
alternative. There is no team notification. The database stores reviewer, review
time and acknowledgment state. Row locks serialize competing review/send actions;
repeat clicks after success do not resend. A stable Resend idempotency key permits
explicit retry after delivery cannot be confirmed, within a conservative 23-hour
window. Beyond that window staff must check delivery manually. No automatic mail
retry runs in the worker. Historical records are marked legacy and are not resent
when approved, because their earlier acknowledgment delivery is unknown.

Run `scripts/setup_inquiry_review.py --rollback-check` to validate the migration in
a rolled-back transaction. At deployment run `scripts/setup_inquiry_review.py --apply`
using the migration owner credentials, then restart Flask. The default script mode
verifies the schema using the restricted application connection. The migration adds
columns/index and grants update only on the moderation columns; it preserves RLS
and adds no database business functions. It does not send email or delete records.

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
- Project details require at least 20 words containing letters. Number-only or
  punctuation padding does not count; contractions and hyphenated words count once.
  Browser validation gives immediate guidance, and server validation independently
  enforces the minimum before any inquiry write or email. Values remain available
  for correction after a validation error. Length is a detail requirement, not
  proof of authenticity; existing content and abuse checks remain mandatory.
- Submission limits are three per claimed email and five per browser session
  in 15 minutes. A public peer IP is limited to ten per 15 minutes; IPv6 peers
  share a /64 bucket. A global ceiling allows 30 otherwise eligible submissions
  per hour, bounding mail volume even when bots rotate addresses and cookies.
- A duplicate message from the same normalized email within 15 minutes does not
  create another inquiry. No public submission sends email, including duplicates.
- The observed Robertsum/Google bot profile is rejected as a combined match.
  Several exact generic pricing templates from the supplied multilingual samples
  ask for project details instead. Matching ignores case, punctuation and accents;
  it does not reject those languages or contextual pricing questions generally.
- Rotating addresses and browser sessions also share normalized name/company
  limits (five per hour) and message-body limits (three per hour), enforced before
  saving or emailing. Their counter identities are HMACed, just like email/IP keys.

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
`INQUIRY_IP_LIMIT`, `INQUIRY_HOURLY_LIMIT`, `INQUIRY_PROFILE_LIMIT`, and
`INQUIRY_BODY_LIMIT`, and `INQUIRY_MIN_WORDS`. Tests use an isolated counter path
derived from their temporary auth stores and disable timing only where timing
is not being tested. Timing, concurrency, multilingual input, spoofed forwarded
headers, duplicates and failure behavior have dedicated tests.

Deploy the code/template/CSS changes together after the inquiry review migration,
and restart the existing Flask service. No new `.env` credential is needed. Changes
remain local until explicitly committed, pushed and deployed; existing spam
messages and saved inquiries are not deleted by this change.
