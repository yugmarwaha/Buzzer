---
name: context-sync
description: Keep context files, tracking docs, and project docs from going stale as the code changes
---

# Keep Context and Docs in Sync

Goal: a fresh AI session should be able to trust every doc in the repo.

## While working

After any change to a file under a directory that has its own `CLAUDE.md` (or `README.md`, if
your team built the hierarchy that way), check whether that file still accurately describes the
directory — then say so and offer to update it, rather than waiting to be asked. A parent
directory's context file only needs revisiting if the change affects what a reader would be
told from the top down (a new module, a changed responsibility) — not for every line-level edit.

Never regenerate a context file by re-reading the whole directory's source from scratch — read
the existing file first and edit only what's actually stale.

## End-of-task sweep

When a feature or task is finished, before proposing a commit:
1. Run `git diff main...HEAD --stat` and list every source directory that changed.
2. Run `verify-context` on those directories' CLAUDE.md files and propose fixes for anything stale or missing.
3. If a new source directory has no CLAUDE.md, propose one using `context-hierarchy`.
4. Root `CLAUDE.md`: update it if commands, structure, or the rules/skills table changed.
5. `docs/`: update any doc whose described behavior changed (design docs, `realtime.md`, etc.).
   If implementation departed from a design doc, update the doc to match.
6. Make the doc updates directly and leave them uncommitted for the user to review in the diff.
7. Doc updates go in the same branch and MR as the code change they describe.
