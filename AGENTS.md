# Agent instructions

Read `README.md` for what this repo does and how to run it.

## Session habits

- Before editing, read the whole file (or the whole function you're changing) and plan the change. If you've edited the same spot three times for one request, stop and re-read the request: repeated patches usually mean it was misread.
- When I correct you, re-read my message and say in one line what changes, then do it. Ask first only if the correction is ambiguous.
- When the same approach fails twice (a command, a fix that doesn't hold), change approach instead of retrying it. If a second approach also fails, stop and tell me what you tried, what failed and what you'd try next.
- Before you report back, re-read my original message and check off every part of it; finish what's missing or say which parts are left and why. In a long session, also re-read it before starting each new part.

## Specs and tasks

- Specs go in `docs/specs/` as `SPEC-<slug>.md`; plans, task lists, handoffs and run notes go in `docs/tasks/`; shipped or dropped work goes in `docs/archive/`. Create the folders when missing; nothing goes at the repo root.
- When work ships or is dropped, `git mv` its spec and tasks to `docs/archive/` under the same names, add a line to `docs/archive/README.md` (file, what shipped or why it was dropped, month) and fix any path that cites them (`git grep <file name>`).
- Never edit an archived spec to match today's code; new work gets a new spec.
- When `docs/specs/` or `docs/tasks/` holds something that looks finished or untouched for a month, list it and move it on my yes.
