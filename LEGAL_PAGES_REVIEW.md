# Privacy and terms pages: review and deployment

Public routes added: `/privacy` and `/terms`. Both are linked from the site footer,
are accessible without sign-in, and have their own titles and responsive styling.
The wording is an initial draft for the owner's content review; adding it to Git
does not verify the factual accuracy of every business practice or deploy the pages.

## Review files

- `app/templates/legal/privacy.html`: collection, authentication, purposes, processors,
  Google user data, address search, storage/security, cookies, retention/deletion,
  user requests, children and updates.
- `app/templates/legal/terms.html`: accounts, acceptable use, supplied information,
  separate scope-of-work agreements, providers, ownership, availability and closure.
- `app/templates/legal/_layout.html`: date and contact address shared by both pages.

The draft uses `contact@codebisolutions.com`, matching the existing Contact page.
Before deploying, review that the mailbox is monitored; the company identity, listed
providers, security statements, actual data use, retention/deletion practices,
international processing, and commercial terms match your operations. No specific
retention duration, governing state, liability cap, or deletion deadline was invented.

## Google branding

After content review and deployment to DigitalOcean, open these exact public URLs
without signing in and confirm HTTP 200 and the full policy text:

- https://codebisolutions.com/privacy
- https://codebisolutions.com/terms

Set the corresponding Google Auth Platform Branding links to those URLs. Keep the
homepage as https://codebisolutions.com/. Then select "I have fixed the issues" and
request branding re-verification. If approved, publish the verified branding when
Google offers that action. A Git push alone does not make the pages live.

The policy pages work with the original marketing site independently of the pending
portal implementation. No environment values, Auth credentials or database migrations
are required to add these public pages.

References consulted:
- https://support.google.com/cloud/answer/15549049
- https://developers.google.com/terms/api-services-user-data-policy
- https://developers.google.com/maps/documentation/places/web-service/policies
