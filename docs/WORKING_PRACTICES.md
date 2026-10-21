# Working practices

Operational conventions for this repository that are not architectural
decisions. For decisions, see `docs/DECISIONS.md`.

## Commits and multi-chat work

On 2026-10-21, commits e9feb29 and 7148e54 were created from one
chat session while a second chat session was adding growth-feature
code to the same repository. The commit messages describe the
token system, but the commits contain growth code that the second
session produced. This is a multi-chat artifact, not a
force-push-worthy error; the code is committed and pushes cleanly.

Going forward: commit at the end of each chat session, before
starting another. Note the chat name in the commit message. Check
git status before any major task.
