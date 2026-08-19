# 2026-08-17 - Frontend design pass (Ralph Loop + frontend-design skill)

## Why

The app was functionally complete but looked like a hackathon/tutorial
project: default system fonts, no responsive breakpoints, no loading/empty
states, a plain unstyled login form, and a couple of real layout bugs
hiding under the surface. Goal: make it look and feel like a dashboard a
professional product team shipped, on both desktop and mobile.

## What Ralph found

A pass over `templates/`, `static/style.css`, and every route in
`dashboard.py`/`auth.py` before touching anything, looking for both visual
and structural issues:

- No custom typography - system font stack only (Arial/Segoe UI feel).
- No responsive breakpoints anywhere; the two data tables (9 columns for
  run history) and the fixed-width agent cards would overflow on mobile.
- No loading feedback on any of the ~8 forms (Run now, Create project,
  Save schedule, Delete, Stop, Teardown) - a slow request looked like a
  dead click.
- Empty states were a single muted text cell ("No projects yet - add one
  below.") with no visual weight or next-step guidance.
- Login page was an unstyled centered box with no product framing.
- Already-logged-in users hitting `/login` saw the login form again
  instead of being redirected to the dashboard.
- Two real rendering bugs, only visible once real data was on screen:
  1. `.modal-body { white-space: pre-wrap }` was written for the raw-brief
     popup (a `<pre>` tag) but applied to *every* modal, including the
     teardown modal's normal `<p>`/`<ul>` markup - it produced large
     phantom blank gaps between every block because it preserved the
     template's source whitespace/newlines literally.
  2. The teardown modal's vault-opt-in `<label>` used `display: flex` with
     raw text + a `<strong>` as direct children - each text run became its
     own anonymous flex item, visually splitting one sentence into three
     disconnected columns.

## What was improved - Cycle 1 (visual system)

- New typography: `Sora` (display/headings) + `IBM Plex Sans` (body) +
  `IBM Plex Mono` (tool labels, cost figures, error text) via Google Fonts -
  replacing the generic system-font stack per the frontend-design skill's
  guidance against default/overused fonts.
- New color system: warm off-white background, ink-900/indigo primary,
  semantic success/warning/danger/info tokens, all as CSS custom properties
  (spacing, radius, shadow, and motion scales included) instead of hardcoded
  values - one place to retune the whole palette later.
- Rebuilt the login page as a split panel: dark brand panel with a dot-grid
  texture, headline, and feature bullets on the left; the form on a clean
  white panel on the right. Collapses to a single column under 860px.
- Added a small logomark (three-node org-chart glyph, matching what the
  app actually visualizes) used as the favicon and in the topbar/login brand.
- Section headers across the dashboard now follow one pattern: an eyebrow
  label, an `h2`, and a one-line description - replacing bare `<h2>` tags.
- Empty states for the projects and run-history tables became icon +
  heading + one line of guidance instead of a muted text string.
- Added a global loading state: any submitted form (unless its `onsubmit`
  confirm was cancelled) disables its button and shows "Working…" with a
  spinner, via one listener in `base.html` - no per-form JS needed.
- Status pills and run-status text got a dot/shape cue in addition to
  color, tables got hover states and a horizontal-scroll wrapper for
  mobile, and buttons/inputs got consistent focus-visible rings.

## What was improved - Cycle 2 (bug fixes + refinement, found via live QA)

Round 1 was verified against a running instance (Chrome, logged in, real
seeded data - one project, two completed runs) rather than just reading the
CSS back. That live pass caught issues static review missed:

- Removed `position: sticky; top: 0` from table headers - with no
  scrollable ancestor around the tables, it pinned each header to the very
  top of the *viewport* while scrolling, competing with the already-sticky
  topbar instead of doing anything useful.
- Fixed the `.modal-body` pre-wrap bug and the teardown-checkbox flex-split
  bug described above (both confirmed visually before and after the fix).
- Fixed a clipped input placeholder ("leave blank for manual-only") by
  giving schedule-form inputs `flex: 1 1 180px` instead of shrink-to-content,
  and shortening the copy to "blank = manual only".
- Added the already-logged-in redirect on `/login` (`auth.py`).
- Verified the full auth loop live: logged out -> wrong password shows the
  error flash -> correct password redirects to the dashboard ->
  already-logged-in visit to `/login` bounces straight back to the
  dashboard.
- Verified project create + delete against the local SQLite DB directly
  (test row was inserted and removed from `data/agent_console.sqlite3`,
  not left behind) after a Chrome-automation `confirm()` dialog froze the
  tab mid-QA - see the note below.

## How quality was verified (frontend-design skill)

Each cycle was checked against the skill's checklist rather than just
"does it look nicer": a distinctive, non-default type pairing; a cohesive
color system driven by tokens, not a generic purple-gradient-on-white
default; restrained, purposeful motion (button loading spinners, modal
fade/pop, a pulse on "running" status - not decoration for its own sake);
deliberate spatial composition (the section-head pattern, the org chart);
and background/visual detail beyond flat white (the radial-gradient body
wash, the login panel's dot-grid texture, the danger-zone card's tinted
gradient). Verification was done live in Chrome at desktop width
(1440x900): login (logged-out, error, logged-in-redirect), dashboard with
real seeded data (org chart, populated + would-be-empty table structure,
expanded schedule form, expanded new-project form, brief modal, teardown
modal), and button/input hover and focus states.

One tooling limitation: the browser-automation `resize_window` call did
not change the actual CDP screenshot viewport in this sandbox (tried twice,
including a fresh tab), so the two responsive breakpoints (720px, 860px)
were verified by code review of the CSS media queries rather than a live
narrow-viewport screenshot. Flagged in `CLAUDE.md`'s Next Steps.

## Incident note

Mid-QA, clicking a "Delete" button (which uses `onsubmit="return
confirm(...)"`) triggered Chrome's native confirm dialog, which froze the
automated tab (a known limitation of the browser-automation tool - native
dialogs block the CDP connection). Per the tool's own guidance, the
attempted click/screenshot calls were not retried past a couple of
attempts; instead the one test row that action would have deleted was
removed directly from `data/agent_console.sqlite3` (a local, no-side-effect
operation - the row was never anything but locally-created QA data), and a
fresh tab picked the QA pass back up. No real project or run data was
affected.

## Result

Two full design + verification cycles (visual system, then live-QA bug
fixes and polish) plus this documentation pass. See `PLAN.md` for the
before/after summary and `CLAUDE.md` for durable notes on the design
system and open follow-ups.
