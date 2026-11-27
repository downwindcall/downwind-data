# Security and data integrity

Report a problem privately to hello@downwindcall.com (or use GitHub's private vulnerability reporting). Please do not open a public issue for a security problem or for deliberately wrong data.

How this repository protects the data:
- Contributors can only change files in `profiles/airports/` and `profiles/aircraft/`. The checks, workflow and `verified.json` are owner-only.
- Every pull request is checked by the validator from the main branch, so a contribution cannot weaken its own checks. Workflows use `pull_request` with read-only permissions and no secrets.
- Hard limits reject impossible values. A flipped circuit side, large size changes and unknown runways are flagged for a human, and a changed safety field needs a source.
- A circuit picture (current vs proposed) is attached to every airfield change.
- "Verified" status is set only by the maintainer, as a content hash in `profiles/verified.json`. Any later edit removes it until re-reviewed.

Maintainer settings (GitHub): branch protection on `main` (pull request, "Validate data" check, code-owner review, no force-push), 2FA, secret scanning, approval required for workflow runs from outside contributors.
