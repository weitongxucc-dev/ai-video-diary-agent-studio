# Git Workflow

This project uses a lightweight trunk-based workflow: `main` stays runnable, and every change is made on a short-lived branch.

## Branches

Use one branch per focused task:

```text
feature/<topic>  New product behavior or UI/API capability
fix/<topic>      Bug fixes, hardening, and risk reduction
chore/<topic>    Tooling, CI, docs, or process changes
```

Examples:

```text
feature/creator-profile
fix/runtime-settings-hardening
chore/git-workflow
```

Rules:

- Do not commit directly to `main` except for emergency recovery.
- Start from a clean, up-to-date `main`.
- Keep branches short-lived and focused on one outcome.
- Prefer small commits that each explain one coherent change.

## Commit Style

Use Conventional Commits:

```text
feat: add creator profile prompt context
fix: harden runtime settings updates
chore: document git workflow
test: cover edit-plan confirmation
docs: update 2.0 product status
```

Commit messages should describe the product or engineering outcome, not the tool action.

Good:

```text
fix: reject non-local runtime settings updates
```

Weak:

```text
update files
```

## Pull Requests

Push feature branches to origin and merge through a PR when possible.

PRs should include:

- Summary of behavior changed.
- Verification commands run.
- Known limitations or follow-up work.
- Screenshots for visible UI changes.

Prefer squash merge for noisy exploratory branches. Prefer normal merge or rebase merge when commits are intentionally structured and useful.

## Verification

Before pushing a branch, run the smallest meaningful focused tests first, then the relevant full checks.

For backend or workflow changes:

```bash
npm run test:api
```

For frontend contract or visual-system changes:

```bash
npm run test:web-contract
npm run test:pixel-style
npm run build:web
npm --dir apps/web exec tsc --noEmit
npm --dir apps/web lint
```

For docs-only changes:

```bash
git diff --check
```

If a command cannot be run, record the reason in the final report or PR body.

## Code Quality Rule

Every feature should move the codebase toward high cohesion and low coupling.

When touching a large file, do not start with a broad rewrite. Instead:

- Identify the responsibility being changed.
- Extract the smallest stable helper, service, or module that owns that responsibility.
- Keep API boundaries explicit.
- Add tests around behavior before refactoring.
- Leave unrelated cleanup for a separate branch.

This is especially important for:

- `apps/api/app/main.py`
- video and edit-plan processing modules
- draft packaging/import modules
- prompt-generation modules

## Codex Operating Rules

When Codex works on this repository:

1. Check `git status --short --branch` before editing.
2. Create or use a focused branch instead of working directly on `main`.
3. Use TDD for behavior changes: failing test first, minimal implementation, then refactor.
4. Keep commits scoped and named with Conventional Commits.
5. Run verification before commit.
6. Push the feature branch, not `main`, unless the user explicitly asks for direct main updates.
7. Report the branch name, commit hash, and verification results.

## Current Cleanup Policy

The existing early commits on `main` are kept as-is. Do not rewrite published history just to make it look cleaner.

From this point forward, use branch-based work and PR-style review so the repository history becomes cleaner naturally.
