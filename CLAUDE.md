# CLAUDE.md

Guidance for Claude Code (or any future contributor) working in this repo.

## What this is

Agent Console is a small Flask app that puts the ClaudeMultiAgent_ManagedAgent
BA-to-Architect delivery pipeline behind a login, a dashboard, an on-demand
"Run now" button, and per-project scheduling. See `README.md` for the full
feature description and `RUNBOOK.md` for setup/operating steps - this file
is about how the code and UI are put together, for anyone editing them.

## Stack

- Flask (blueprints: `auth.py`, `dashboard.py`), Jinja2 templates, vanilla
  CSS/JS - no frontend build step, no framework.
- SQLite (`db.py`) for projects/runs. APScheduler (`scheduler.py`) for
  cron/interval triggers. `pipeline.py` wraps the Anthropic Managed Agents
  calls; `run_manager.py` runs a pipeline call in a background thread per
  run so the dashboard never blocks on it.
- Single-user session auth (`auth.py`) - no user table, credentials come
  from `CONSOLE_USERNAME`/`CONSOLE_PASSWORD` env vars.

Run locally: `python app.py` (serves on `0.0.0.0:5000`, `use_reloader=False`
- restart the process after editing any `.py` file; templates and
  `static/style.css` are read fresh per request, no restart needed for those).

## Frontend architecture

- `templates/base.html` - shell: topbar, flash messages, favicon, Google
  Fonts (`Sora` for display/headings, `IBM Plex Sans` for body, `IBM Plex
  Mono` for tool labels/costs/error text), and a global `submit` listener
  that puts any submitted form's button into a disabled "Working…" state
  (see "Loading states" below).
- `templates/login.html` - split-panel layout: dark brand panel (left,
  hidden under 860px) + white form panel (right).
- `templates/dashboard.html` - the single dashboard page: agent roster (org
  chart), projects table, run history table, danger zone. All three list
  sections follow the same `.section-head` pattern (`eyebrow` + `h2` +
  one-line description) and render an `.empty-state` block (icon + heading
  + one line of guidance) when their data is empty, instead of a bare "no
  rows" string.
- `static/style.css` - one file, organized as a design system: CSS custom
  properties at the top (`--ink-*`, `--primary*`, semantic colors, spacing
  scale `--sp-*`, radius/shadow/motion tokens), then components, then a
  `@media` block at the bottom for the two breakpoints (860px, 720px).

### Design system notes for future edits

- **Don't reintroduce `white-space: pre-wrap` on `.modal-body` generally.**
  It's scoped to `#brief-modal-text` only (a real `<pre>`, for the raw
  project brief). The teardown modal's `.modal-body` holds normal
  `<p>`/`<ul>` markup - `pre-wrap` on that produces large phantom gaps
  because it preserves the whitespace/newlines from the template source.
- **Don't put raw text + inline elements directly inside a
  `display: flex` container.** `.teardown-vault-option` is a flex `<label>`;
  the checkbox is a flex item and the *rest of the text is wrapped in one
  `<span>`* rather than left as bare text nodes next to a `<strong>` -
  otherwise each text run becomes its own anonymous flex item and the
  sentence visually splits into separate columns.
- Buttons get their loading state from `base.html`'s global `submit`
  listener, not per-form JS - it checks `e.defaultPrevented` so it correctly
  skips forms whose `onsubmit="return confirm(...)"` was cancelled. Any new
  form with a submit button gets this for free.
- Status pills (`.pill-ok` / `.pill-pending`) and run status text
  (`.status-running` / `-success` / `-failed`) carry a color **and** a
  shape/dot cue (not color alone), so they're not relying on color vision
  to read.
- The org chart and both tables are intentionally plain-CSS responsive
  (flex-wrap, `.table-scroll` with `overflow-x: auto`, media queries) -
  no JS layout logic. Verified via Chrome DevTools device emulation, not the
  browser-automation `resize_window` tool - that tool's window resize did
  not affect the CDP screenshot viewport in this sandbox, so re-check
  responsiveness manually in a real browser if you change breakpoints.
- **The "New project" brief field is a popup editor, not an inline
  textarea.** The actual form field (`#brief-field-value`) is a hidden
  `<textarea name="brief">`; the visible UI is a one-line preview plus a
  "Write brief…" button (`#open-brief-editor`) that opens
  `#brief-editor-modal`, a large textarea with a live word counter capped
  at 10,000 words (soft cap - typing past it is allowed, but `Save brief`
  disables and the counter turns red until trimmed back under the limit).
  Because the real field is `hidden`, browsers exempt it from native
  `required` validation - the empty-brief check is done by hand on the
  form's `submit` listener (see `new-project-form` in `dashboard.html`),
  which also re-opens the editor so the error is immediately actionable,
  not just a blocked click. Closing the editor via Cancel/×/overlay/Escape
  discards unsaved changes (with a `confirm()` guard if the draft differs
  from the saved value) rather than silently keeping them - don't change
  that to a silent save, it'd make the visible preview lie about what's
  about to be submitted.

## Data model

`db.py` owns two tables: `projects` (name, brief, cron/interval, schedule
flag) and `runs` (one row per pipeline execution, FK to project with
`ON DELETE CASCADE`). No ORM - plain `sqlite3` with `Row` factory. See the
module docstring and `fail_orphaned_running_runs()` for the one non-obvious
behavior (a run stuck "running" after a crash gets reconciled to "failed"
on next startup).

## Conventions

- No JS framework, no bundler. Keep new interactivity as small inline
  `<script>` blocks near the markup they control (see `dashboard.html`'s
  brief-modal / teardown-modal / polling IIFEs) rather than adding a build
  step.
- Prefer editing `static/style.css`'s existing tokens over hardcoding new
  colors/spacing - the whole point of the token layer is that a palette or
  spacing change is a handful of `:root` edits, not a find-and-replace.
- Flash messages use two categories only: `error`, `success` (see
  `.flash-error` / `.flash-success` in the CSS). Don't invent new
  categories without adding a matching CSS rule.

## Next Steps

- **Markdown rendering in the brief modal.** It currently shows the raw
  project brief (headings, `**bold**`, etc.) as plain preformatted text.
  A small client-side Markdown renderer (or server-side pre-render) would
  make long briefs much easier to read.
- **Real device testing pass.** All responsive work was verified by code
  review (breakpoints, flex-wrap, scroll containers) and desktop
  screenshots; the automated browser tool couldn't resize its viewport in
  this environment. Worth a manual pass on an actual phone/tablet before
  calling mobile support fully verified.
- **Toast-style success feedback.** Actions like "Save schedule" or
  "Create project" currently just redirect back to the dashboard with no
  flash message confirming success (only errors flash today) - a quiet win
  for perceived responsiveness.
- **Run history pagination.** `db.list_runs(limit=30)` silently caps at 30;
  there's no "load more" or pagination UI once a project has a long history.
- **Empty-state screenshots weren't captured live** (the seeded dev DB
  already had a project and two runs) - the empty-state markup was verified
  by code review against the populated-state structure, not visually. Worth
  a quick look with a fresh/empty database.
- **Dark mode** was not implemented - the design system's tokens make it a
  plausible follow-up (swap `:root` values under `prefers-color-scheme`)
  but it wasn't part of this pass.
