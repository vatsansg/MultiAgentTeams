# PLAN.md

## What We Built

Agent Console: a single-user Flask dashboard that runs the
ClaudeMultiAgent_ManagedAgent BA-to-Architect delivery pipeline (Delivery
Lead coordinator + Roger the Business Analyst + Michael the Solution
Architect) without a notebook. It provides:

- Session-based login (`auth.py`), single user via env-configured credentials.
- An org-chart view of the agent roster, read live from `pipeline.py` so
  adding a specialist there adds a card here automatically.
- A projects table: create a project (name + brief), run it on demand, or
  give it a cron expression / interval schedule (`scheduler.py` +
  APScheduler).
- A run-history table that polls itself every 4s for status and every 30s
  for a live cost estimate on in-flight runs, with per-run BRD/TDD
  download links and expandable error detail.
- Orphaned-run recovery on startup (a run stuck "running" after a crash
  gets reconciled to "failed" instead of hanging forever).
- A danger-zone teardown flow that deletes/archives every platform
  resource this app created (sessions, environment, memory stores, agents),
  behind a real confirmation modal rather than a browser `confirm()`.

## What We Improved

Ran a Ralph Loop design pass (visual system rebuild -> live-Chrome QA and
bug fixes -> this documentation) verified against the frontend-design
skill's quality bar each cycle. Full detail in
`docs/2026-08-17-frontend-design-pass.md`; summary:

| Area | Before | After |
|---|---|---|
| Typography | System font stack (Arial/Segoe UI feel) | Sora (display) + IBM Plex Sans (body) + IBM Plex Mono (data), via Google Fonts |
| Color | Ad hoc hex values scattered through the CSS | Token-based system: ink/primary/semantic scales + spacing/radius/shadow/motion custom properties |
| Login | Unstyled centered box | Split brand panel (dark, dot-grid texture, feature bullets) + form panel; already-logged-in users now redirect straight to the dashboard instead of seeing the form again |
| Responsiveness | No breakpoints; fixed-width cards and a 9-column table would overflow on mobile | Two breakpoints (860px, 720px), flex-wrap org chart, horizontal-scroll table wrapper |
| Loading feedback | None - a slow request looked like a dead click | Global submit-loading state (disabled button + spinner + "Working…") for every form, added once in `base.html` |
| Empty states | Plain muted text ("No projects yet - add one below.") | Icon + heading + one line of next-step guidance |
| Bugs | `white-space: pre-wrap` leaking from the brief popup into every modal (huge blank gaps); a flex-container text-splitting bug in the teardown checkbox label; a sticky table header fighting the sticky topbar | All three found via live QA and fixed |
| New-project form | No way to back out once expanded; a 3-row inline textarea for a brief that could reasonably run long | Explicit Cancel button (resets fields and collapses the form); brief entry moved to a popup editor with a live word counter capped at 10,000 words, a one-line preview in the form, and a custom required-check that re-opens the editor with an inline error instead of failing silently |

A second, smaller cycle added the Cancel button and brief-editor popup
above - implemented, then verified live (normal entry, the 10,000-word
over-limit state, empty-brief validation, Cancel-resets-and-collapses, and
a full create→verify→delete round trip) rather than just read back from
the CSS/JS. See `docs/2026-08-17-frontend-design-pass.md` for the first
cycle's detail.

## Future Roadmap

- **Markdown rendering** for the brief-preview modal (currently shows raw
  `#`/`**`/etc. as plain text).
- **Toast/flash confirmation on success**, not just on error - "Save
  schedule", "Create project", and "Delete platform resources" currently
  redirect silently on success.
- **Pagination** for run history beyond the current 30-row cap.
- **Manual mobile device pass** - responsive CSS was verified by code
  review (the sandbox's browser-automation viewport resize didn't take
  effect), so a real-device check is still worth doing.
- **Dark mode** - the token system was built to make this a small follow-up
  (swap `:root` values under `prefers-color-scheme`), not attempted here.
- **Multi-user support** if this ever needs to serve more than one person -
  today it's intentionally a single shared login.
