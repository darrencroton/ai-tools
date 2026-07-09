---
name: commit
description: Use this skill whenever creating or staging a git commit.
---

## Commit Message Format
- Line 1: short imperative summary (under 72 chars)
- Blank line
- Body: rationale + every new/changed/removed file with reason, grouped logically

## Steps
1. Run `git status` and `git diff` to review all changes
2. When it would save meaningful top-tier context, you may delegate routine commit mechanics to the least expensive reliable available worker, but the main agent must verify the staged files, final commit hash, and post-commit status.
3. Stage specific files by name — never `git add -A` or `git add .`
4. Commit using a HEREDOC:

```bash
git commit -m "$(cat <<'EOF'
Short summary

Detailed description

Changed files:
- path/to/file: reason
EOF
)"
```

5. Run `git rev-parse HEAD` immediately after a successful commit and record/copy the exact 40-character hash from that command when any workflow asks for the commit hash. Do not infer a full hash from abbreviated `git commit` output.
6. Run `git status` to confirm success
7. Never use `--no-verify` and never amend — if a hook fails, fix the issue and create a new commit
