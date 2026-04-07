# Open Items

Tracked here until filed as proper tickets.

## P0 — Must Fix

- [ ] **Consistent pagination** — Ticket list has per-page selector (25/50/100) and proper count display. All other paginated lists use basic prev/next. Standardize all lists to match ticket_list pattern.
- [ ] **Public repo unauthenticated view** — Public projects should be fully browsable without login. Currently some views may redirect to login. Audit all fossil views for anonymous access on public repos.
- [ ] **Branch protection push enforcement** — Rules are currently advisory. Need Fossil hook integration to actually block pushes to protected branches.

## P1 — Should Do

- [ ] **Fossil sync to fossilrepo.io** — Automate git→fossil sync as part of deploy or CI pipeline
