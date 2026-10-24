---
name: verify-context
description: Verify existing CLAUDE.md context files against their directories and report confirmed material issues
---

# Verify Context

Compare each existing `CLAUDE.md` with the real files in its directory in one bounded verification pass. Find only materially misleading inaccuracies or omissions.
If asked to edit, correct only evidence-backed issues that would mislead someone working in that directory, editing existing `CLAUDE.md` files in place without restructuring.
After factual verification, remove only duplicated, stale, or non-actionable wording. Preserve details that explain behavior, invariants, risks, or directory relationships; do not add new details during this shortening pass.
Do not automatically repeat verification or spawn another pass. Report confirmed issues and stop.
Never create parent files, modify source code or other files, or commit changes.
Report the files checked, the corrections made, and the final verification result.
