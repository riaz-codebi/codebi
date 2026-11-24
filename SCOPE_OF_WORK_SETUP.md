# Scope-of-work workflow

- Any active client user can create a draft and submit it directly to CodeBI. Draft visibility is limited to the author, client admins, and CodeBI. Submitted requests are visible to active members of that client. Existing requests with the old pending approval status appear as Ready to submit.
- CodeBI starts review, sends an estimate, or declines with a reason. Active members of the client account and CodeBI can use the scope's conversation after submission. Messages show the author's name and Central Time. A client message on a legacy Clarification needed scope returns it to Under review.
- The submission email and client message notifications go to the CodeBI team. CodeBI message notifications go to the original author. The estimate email goes to the author if they are an active client admin, otherwise to all active client admins on the account.
- Hourly estimates save the hours range, manual USD rate, and calculated minimum and maximum costs. Fixed project estimates save the USD price. Both require an estimated completion date when issued.
- Any active client admin on that account can accept or reject the issued estimate in the portal. Rejected estimates stay in the Rejected requests queue. CodeBI can start work, record time, and mark the request complete after acceptance.
- The Activity timeline records transitions and messages with the actor and Central Time. Scope changes, estimates, messages, and their audit events are written in the same database transaction. The portal sends notification emails after the transaction commits.

## Database setup

Apply the scoped revision through the administrative migration connection before deploying the updated app:

```powershell
.\app\.venv\Scripts\python.exe -m alembic upgrade 0010_scope_ai_solution_options
```

This upgrade grants the restricted Flask role access to the existing review tables and adds pricing columns and constraints to estimates. Existing estimates keep their original values. RLS stays enabled. The optional Auth RPC retirement remains on its own migration branch.

## Verification

Run `.\app\.venv\Scripts\python.exe -m unittest discover -s tests`. The scope tests cover tenant isolation, direct submission, clarification and replies, routing, estimate decisions, the rejected queue, and work completion. Email sends are mocked.
