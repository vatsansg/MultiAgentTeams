# Productv1 manual testing workflow

A step-by-step pass through everything added in `Productv1` (see
`changerequestProductv1.md` for what changed and `docs/multi-agent-teams.md`
for the design). Check items off as you go; each step says what you should
see if it's working.

Prerequisite: the dev server is running (`python app.py`, or ask Claude to
start/restart it). The catalog now seeds 6 agent roles (Delivery Lead,
Roger, Michael, Smith, Jack, Donald) - if you're picking this up after an
earlier pass that only had 4, no DB wipe was needed: the app backfills
new catalog roles into the existing database automatically on startup.

---

## 1. Login

- [ ] Go to `http://127.0.0.1:5000`, log in with your `.env`
      `CONSOLE_USERNAME`/`CONSOLE_PASSWORD`.
- [ ] **Expect:** a left sidebar with **Dashboard / Agents / Teams / Agent
      Specs**. The dashboard shows a "No teams yet" prompt (no org chart
      yet - you haven't created a team).

## 2. Agents catalog (read-only)

- [ ] Sidebar → **Agents**.
- [ ] **Expect:** 6 cards - Delivery Lead (Coordinator), Roger (Business
      Analyst), Michael (Solution Architect), Smith (Developer), Jack (QA
      Tester (Local)), Donald (QA Tester (Cloud)). Smith's card shows
      skill chips: Node.js, Angular, React, Python, Java, ASP.NET Core,
      SQLite, MySQL, CosmosDB, SQL Server, Oracle, Responsive design.
      Jack's shows Test planning, Test case design, Functional testing,
      Local environment QA, Defect reporting, Requirements traceability.
      Donald's shows Regression testing, Cloud environment QA, Test case
      validation, Defect reporting, Requirements traceability.

## 3. Create a team with every role

- [ ] Sidebar → **Teams** → **+ Create team**.
- [ ] Name it `Full Delivery Team`, leave every specialist checkbox
      checked (Delivery Lead has no checkbox - always included), **Create
      team**.
- [ ] **Expect:** redirected to Teams, a flash message "created with 6
      agent(s)", and a card listing all 6 members, marked **unused**.

## 4. Create a team with a role subset

- [ ] **+ Create team** again. Name it `Docs Only Team`. **Uncheck
      Smith, Jack, and Donald**, leave Roger/Michael checked. **Create
      team**.
- [ ] **Expect:** flash "created with 3 agent(s)"; the card lists only
      Delivery Lead, Roger, Michael - no Smith/Jack/Donald.

## 5. Edit an existing team's membership

- [ ] On `Docs Only Team`'s card, click **Edit**.
- [ ] **Expect:** the same name/description fields, plus checkboxes -
      Smith, Jack, and Donald should all be **unchecked** (matches what
      you created).
- [ ] Check **Smith**, **Jack**, and **Donald**, click **Save**.
- [ ] **Expect:** flash mentioning the coordinator "will be recreated on
      the platform the next time this team runs" (only shown when
      something actually changed). Back on Teams, the card now lists 6
      members.
- [ ] Click **Edit** again, click **Save** with nothing changed.
- [ ] **Expect:** a plainer "Team updated." flash - no recreation
      language, since membership didn't actually change.

## 5b. Rename agents within a team

- [ ] On `Full Delivery Team`, click **Edit**.
- [ ] **Expect:** each role row now has a **Name for this team** text
      field, pre-filled with the catalog name (Roger, Michael, Smith,
      Jack, Donald).
- [ ] Change **Jack**'s name field to `Alice`, click **Save**.
- [ ] **Expect:** a flash explaining that a name change can appear in
      *other* agents' prompts too, so every agent on the team will be
      recreated (not just the coordinator, unlike a plain add/remove).
- [ ] Sidebar → **Dashboard**, select `Full Delivery Team`.
- [ ] **Expect:** the org chart's QA (Local) card now shows **Alice**,
      not Jack.
- [ ] Try renaming two different roles to the **same** name (e.g. set
      both Michael's and Smith's name field to `Sam`), **Save**.
- [ ] **Expect:** a clear error - "Two team members can't both be named
      'Sam'..." - and neither name should have actually changed (reload
      the Edit page to confirm).
- [ ] Rename Alice back to `Jack` (or leave it - either is fine for the
      rest of this workflow; `Jack` is used below for consistency, so
      rename it back).

## 6. Dashboard: team selection drives the org chart

- [ ] Sidebar → **Dashboard**.
- [ ] **Expect:** a team dropdown at the top (defaults to the first
      team), and below it an org chart - Delivery Lead on top, its
      current specialists below, each card showing "not created yet"
      (no platform agents exist yet).
- [ ] Switch the dropdown between `Full Delivery Team` and
      `Docs Only Team`.
- [ ] **Expect:** the org chart's specialist cards change to match
      whichever team is selected - 5 specialist cards (Roger, Michael,
      Smith, Jack, Donald) for `Full Delivery Team` vs. 2 (Roger,
      Michael) for `Docs Only Team` (unless you added Smith/Jack/Donald
      to it in step 5, in which case it's 5 there too).

## 7. Create a project

- [ ] On the dashboard, open **+ New project**.
- [ ] **Expect:** **Team** is the first field, defaulting to whichever
      team is currently selected in the dropdown above.
- [ ] Pick `Full Delivery Team`. Name: `Test Website Project`. Click
      **Write brief…**, enter something like *"A simple portfolio site
      for a freelance photographer - home, gallery, about, contact
      form."*, **Save brief**.
- [ ] Leave the schedule on "Manual only" for now. **Create project**.
- [ ] **Expect:** the project appears in the Projects table with a
      "manual only" pill and the correct Team name.

## 8. Schedule picker - try every mode

On the project's row, open **Schedule**, and try each of these (save
after each one, then reopen Schedule to try the next):

- [ ] **Every day**, 24-hour clock, `06:00:00`. Save. **Expect:** pill
      reads "Every day at 6:00:00 AM".
- [ ] **Every week**, weekday = Wednesday, 12-hour clock, `6:30 PM`.
      Save. **Expect:** pill reads "Every Wednesday at 6:30:00 PM".
- [ ] **Every month**, day = `15`, 24-hour clock, `09:05:00`. Save.
      **Expect:** pill reads "Day 15 of every month at 9:05:00 AM".
- [ ] **Every N minutes** = `30`. Save. **Expect:** pill reads "Every 30
      minutes".
- [ ] Set it back to **Manual only** and save, so it doesn't actually
      fire while you're testing (unless you want to leave a schedule
      running - see step 12).

## 9. Run it (real run - costs API credits and takes a few minutes)

Only do this if you're ready to spend real Anthropic API credits - it's
a genuine multi-agent run.

- [ ] Click **Run now** on the project.
- [ ] **Expect:** a new row appears in Run history showing `running`,
      then updates automatically (polled every 4s) to `success` or
      `failed` after anywhere from ~1 to several minutes (this team has
      6 agents doing real work in order: BRD, TDD, a website build with
      Smith's self-review loop, Jack's local test plan/cases/execution,
      Donald's cloud validation pass, Google Docs filing, Slack updates -
      the longest single run this app does, since it's the full roster).
- [ ] Once it's `success`: the org chart's agent cards on the dashboard
      now show real platform agent ids instead of "not created yet".
- [ ] Run history's **Files** column should show **BRD**, **TDD**,
      **Site (.zip)**, and **QA (.zip)** links - click each. BRD/TDD open
      as text; Site and QA download zips. Unzip Site and check for a
      `README.md` inside with real run instructions. Unzip QA and check
      for `test-plan.md`, `test-cases.md`, `qa-report-local.md` (Jack),
      and `qa-report-cloud.md` (Donald) - confirm the test cases actually
      reference specific BRD/TDD requirements, and the reports describe
      real pass/fail results, not placeholders.
- [ ] Check your configured Google Docs "Delivery" folder and Slack
      channel for the filed documents/updates.
- [ ] **Team** column in Run history should read `Full Delivery Team`.

If you'd rather not spend credits right now, skip to step 10 - the
automated smoke test (step 13) already exercises the full run lifecycle
against a fake pipeline, so the plumbing is covered either way.

## 10. Agent Specs - edit and validate

- [ ] Sidebar → **Agent Specs** → click **Edit** on Smith.
- [ ] Click **Validate** without changing anything. **Expect:** a
      "Validation passed" flash.
- [ ] Clear the **System prompt** field entirely, click **Save**.
      **Expect:** a "System prompt is required." error, and you're kept
      on the edit page (not redirected) - the change should NOT have
      been saved.
- [ ] Reload the page (or navigate away and back) to confirm Smith's
      original prompt is still intact.

## 11. Team deletion guard

- [ ] Sidebar → **Teams**. Try **Delete** on `Full Delivery Team` (the
      one your test project uses).
- [ ] **Expect:** the Delete button is disabled (or a flash explains it's
      still in use) - it must not delete while a project references it.
- [ ] Delete the test project first (Dashboard → project row → Delete),
      then retry deleting the team - **Expect:** this time it succeeds.

## 12. Danger zone (optional - only if you ran step 9)

Only do this once you're done testing and want to stop paying for
whatever got created on the Managed Agents platform.

- [ ] Dashboard → scroll to **Danger zone** → **Delete platform
      resources…**.
- [ ] **Expect:** the confirmation modal lists every team's agents by
      name, **including Jack and Donald** if `Full Delivery Team` ran -
      the teardown roster is fully dynamic (built from every team's
      actual membership), not a fixed list, so a new role never needs
      special-casing here.
- [ ] Confirm. **Expect:** a success flash naming what was removed - it
      should include an "archived" entry for every agent listed in the
      modal, Jack/Donald included.

## 13. Automated smoke test (fast, no API key needed)

- [ ] From a terminal: `python tests/test_smoke.py`
- [ ] **Expect:** 48 `PASS:` lines and a final `SMOKE TEST PASSED`,
      covering the full lifecycle above (including dynamic team
      composition, per-team agent renaming with cross-reference rewriting
      and cumulative-rename correctness across multiple edits, the
      coordinator-recreation-on-membership-change behavior, Jack/Donald
      appearing in the catalog/teardown roster, the `qa-artifacts.zip`
      download route, and all four schedule frequencies) against a fake
      pipeline - no real API calls, runs in a
      few seconds.

---

## If something looks wrong

Check the terminal running `python app.py` for a traceback, and the Run
history row's **Error** column (click to expand) for anything that
failed mid-run. `RUNBOOK.md`'s Step 12 has a troubleshooting table for
common pipeline errors (vault id format, MCP auth, etc).
