# 2026-08-17 - New-project form: Cancel button + brief popup editor

## Why

Follow-up request after the initial frontend design pass
(`2026-08-17-frontend-design-pass.md`): the "+ New project" form had no
way to back out short of collapsing the whole `<details>` (which didn't
even clear what you'd typed), and the "Project brief" field was a 3-row
inline `<textarea>` - workable for a short brief, cramped for anything
close to the "up to 10,000 words" ceiling this request asked for.

## What changed

- **Cancel button.** Sits next to "Create project" in the form. Resets
  every field (`form.reset()`), clears the brief preview back to "No brief
  written yet.", clears any pending validation error, and collapses the
  `<details class="new-project">` section - so reopening "+ New project"
  later never shows stale input from an abandoned attempt.
- **Brief popup editor.** The visible form no longer has a raw textarea.
  It shows a one-line, ellipsis-truncated preview of the current brief (or
  an italic "No brief written yet.") next to a "Write brief…" button. That
  button opens `#brief-editor-modal`: a large (`~46vh`) textarea with a
  live word counter ("N / 10,000 words") underneath. The real form field
  submitted to the server is a `hidden` `<textarea name="brief">`, kept in
  sync with the editor only when **Save brief** is clicked - closing the
  popup via Cancel, ×, an overlay click, or Escape discards the draft
  instead (with a `confirm()` guard if the draft differs from what's
  already saved, so a stray Escape can't silently lose a long brief).
- **10,000-word limit, soft-capped.** Typing past 10,000 words is still
  allowed (word-counting live on every keystroke, mid-typing, would be
  janky to hard-block correctly), but the counter turns red with an exact
  overage ("- over the limit by N") and **Save brief** disables until the
  draft is back at or under the limit.
- **Custom required-field validation.** A `hidden` textarea is exempt from
  native browser `required` validation, so an empty-brief submit is caught
  by hand on the form's `submit` listener: it blocks the submit, shows a
  red inline error under the preview, and re-opens the editor immediately
  - rather than just silently refusing the click, or (the pre-existing
  server-side behavior) redirecting back to an empty dashboard with no
  explanation at all.

## How it was verified

Live in Chrome against the running app (not just read back from the code):

1. Opened "+ New project", opened the brief editor, typed a real brief,
   watched the word counter update, saved, and confirmed the one-line
   preview showed the truncated text correctly.
2. Used the page's own JS console (via the browser-automation tool) to set
   the editor textarea to 10,005 generated words and confirm the counter
   turned red with "over the limit by 5" and `Save brief` became
   `disabled` - a state impractical to reach by actually typing 10k+ words
   by hand.
3. Trimmed back under the limit, saved, confirmed the preview updated and
   `Save brief` re-enabled.
4. Clicked Cancel with a filled-in name and no brief showing in the form -
   confirmed the whole section collapsed and, on reopening, every field
   (including the preview) was back to its empty starting state.
5. Filled in a name only (no brief) and clicked "Create project" - confirmed
   the submit was blocked, the red "Add a project brief before creating the
   project." error appeared, and the editor auto-opened.
6. Did a full round trip: name + brief via the editor + submit for real.
   The project appeared correctly in the projects table with the right
   truncated brief preview. Removed it directly from
   `data/agent_console.sqlite3` afterward (local-only cleanup, same
   approach as the first pass's incident note - no confirm()-dialog
   automation risk taken on the real Delete button).

## Result

Implemented and verified in one cycle (no further rework needed after the
live pass). `CLAUDE.md`'s design-system notes and `PLAN.md`'s improvement
table were updated to match.
