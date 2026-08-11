# Deploy: step by step

Two things to stand up.

1. **Render** runs the model and serves it at a URL. About 15 minutes.
2. **Cloudflare Pages** serves the site, which reads that URL. About 5 minutes.

No local setup, no terminal, no API tokens, no object storage. Everything happens in
three browser tabs: GitHub, Render, Cloudflare.

Everything is flat. No folders anywhere, because GitHub's web uploader does not
preserve them.

**Before you start:** the civicAPI race ID (85511) is set but unverified
against the actual API payload (only the human-facing results page was
reachable, not the raw JSON endpoint) -- see README.md "Known limitations".
You can deploy everything below now; watch the first deploy's logs closely
(Part 2.5) to confirm the candidate matching actually works.

---

## Part 1 — Create the repo

1. Create a new GitHub repo, e.g. `mn-gop-gov-primary`
2. Go to `github.com/<you>/mn-gop-gov-primary/upload/main`
3. Select and drag in all of these, flattened (no `web/` folder — the three site
   files go at the repo root alongside everything else):
   ```
   README.md
   DEPLOY.md
   server.py
   civicapi_feed.py
   minnesota_gop_gov_model.py
   build_mn_gop_baseline.py
   mn_gop_gov_baseline.csv
   mn-counties.geojson
   index.html
   app.js
   style.css
   render.yaml
   requirements.txt
   ```
4. Commit message: `Initial model and site`
5. Click **Commit changes**

### Check

Your repo should contain exactly those 13 files at the root, no folders.

---

## Part 2 — Render

### 2.1 Create the service

1. Log in at **dashboard.render.com**
2. Click **New +** in the top right
3. Choose **Blueprint**
4. If Render hasn't seen your GitHub yet, connect it and grant access to
   `mn-gop-gov-primary`
5. Select the repo
6. Render reads `render.yaml` and shows **one** service: `mn-gop-gov-primary-model`
7. Blueprint name: anything
8. Click **Apply** (or **Create**)

**If Render can't find `render.yaml`**, it's not at the repo root — recheck Part 1.

### 2.2 Nothing to configure

No secrets needed. `RACE_ID` is already set to `85511` in `render.yaml`.

### 2.3 Watch the first build closely

Open the service, then the **Logs** tab. First build takes 2-4 minutes. Once
it finishes you want to see, in this order:

```
poller started: race 85511 every 60s, 20000 sims
serving on :10000
   matched: <Lindell's exact name> / <Demuth's exact name> / <Qualls's exact name>
[HH:MM:SS] 0.0% counted | 0 cty | L 39.2 D 34.7 Q 19.6 O 6.5 | leader lindell +4.5
```

repeating once a minute. Before polls close, `0.0% counted | 0 cty` is
correct -- that's the pre-election baseline. **This race's ID was only
confirmed against civicAPI's human-facing results page, not the raw API
payload** -- if `matched:` doesn't appear, or a fetch error shows up instead
of a `[HH:MM:SS]` line, that's the first thing to debug (wrong ID, or the
race hasn't opened in civicAPI's system yet even though the results page
exists).

### 2.4 Read the log carefully. This is your only pre-flight.

**`serving on :10000`** — the HTTP server is up.

**`matched: <name> / <name> / <name>`** — the model found Lindell, Demuth,
and Qualls in the civicAPI payload.

If you instead see:
```
!! CANDIDATE MATCH FAILED for ['lindell', 'demuth'] -- fix the matching KEYS in civicapi_feed.py
```
the feed spells one or more names differently than the matcher expects. Fix
it on GitHub:
1. Open `civicapi_feed.py`, click the pencil icon
2. Near the top find:
   ```python
   LINDELL_KEYS = ("lindell",)
   DEMUTH_KEYS = ("demuth",)
   QUALLS_KEYS = ("qualls",)
   ```
3. Add the actual spelling from the error message, lowercase
4. **Commit changes** to `main` — Render redeploys automatically

**No `!! UNMATCHED COUNTIES:` line.** If it appears, a county name in the feed
didn't match the model's 87 and got silently dropped. Fix the same way, editing
`normalize_county()` in `civicapi_feed.py`.

### 2.5 Get your URL and test it

Render shows the URL at the top of the service page, e.g.
`https://mn-gop-gov-primary-model.onrender.com`. **Copy it.**

Open in your browser:

| URL | Should show |
|---|---|
| `<your-url>/health` | `{"ok": true, "cycles": 3, "race_id_set": true, ...}` with cycles counting up |
| `<your-url>/api/projection` | a large JSON blob with `projection`, `counties`, `diagnostics` |
| `<your-url>/api/history` | a list of past cycles |

### 2.6 Plan note

`render.yaml` specifies `starter`, not `free` — Render spins free services down
after a period without traffic, and a spun-down service stops polling. Change it
in the service's **Settings** if you want to reconsider.

---

## Part 3 — Cloudflare Pages

### 3.1 Point the site at Render

1. In GitHub, open `app.js`, click the pencil icon
2. Line 4 reads:
   ```js
   const API_BASE = "https://mn-gop-gov-primary-model.onrender.com";
   ```
3. Replace with your actual Render URL from 2.5. **No trailing slash.**
4. **Commit changes** to `main`

### 3.2 Create the Pages project

1. Cloudflare dashboard → **Workers & Pages** → **Create** → **Pages** tab
2. **Connect to Git**, authorize GitHub if prompted, grant access to
   `mn-gop-gov-primary`
3. Select the repo, click **Begin setup**

### 3.3 Build settings

| Setting | Value |
|---|---|
| Production branch | `main` |
| Framework preset | **None** |
| Build command | **leave empty** |
| Build output directory | **`/`** |
| Root directory | `/` |

No build — the site is plain files, Cloudflare just copies them. The `.py`/`.csv`
files get copied alongside `index.html` too; they're never linked to and do no harm.

4. **Save and Deploy**

### 3.4 Open the site

Cloudflare gives you a URL like `https://mn-gop-gov-primary.pages.dev`.

**What you should see:** status pill top-right, headline shows Lindell
leading with a margin over Demuth (pre-election baseline), win-probability
bars for all four, county table says "No counties reporting yet."

**If the pill says "reconnecting":** open the browser console (F12).
- **CORS error** → `API_BASE` in `app.js` is wrong. Recheck 3.1
- **404 / connection refused** → Render service is down, check its logs
- **"waiting for first results"** → Render is up, first cycle hasn't finished

### 3.5 Optional: custom domain

Pages project → **Custom domains** → **Set up a custom domain**.

---

## Part 4 — Before polls close

**Service still awake.** Open `<render-url>/health`, `cycles` should be roughly one
per minute since start, and `race_id_set` should read `true`.

**Site updates on its own.** Leave it open a minute — timestamp should tick forward
without reloading.

**Sherburne's placeholder baseline.** If you've gotten real Sherburne numbers
since deploying, update `mn_gop_gov_baseline.csv` (or rerun
`build_mn_gop_baseline.py` with a corrected source table) before polls close
-- see README.md "Known limitations".

---

## Part 5 — Election night

### What the page is telling you

**The win-probability bars** are drawn from the actual 20,000 simulations.

**The county table** shows each county's *raw* reported split against
baseline — unmodeled, exactly what's been counted — next to the model's
blended projection.

**The two maps mean something different from a two-candidate model's.** Each
county is colored by whichever candidate is *leading* in it, shaded by how
far ahead of second place. This model isn't deductive, so "Counted so far"
and "Model projection" aren't "actual" vs. "remainder" -- the projection map
shows the full blended call for every county, present from the first cycle
on.

**Model state** shows the statewide shift per candidate. A single large
county reporting won't move any candidate's shift much on its own by design
-- watch for it to move once a real multi-county pattern shows up.

### What not to do

**Don't read the win probabilities as a call early.** At low reporting the
model is still mostly showing you the baseline -- and this baseline is
thinner than prior builds (no map read, no coalition covariate, one county's
worth of placeholder data).

**Don't restart the Render service** unless you have to — state is in
memory, a restart costs the shift/turnout calibration until counties report
again.

### If something breaks

**Feed goes down.** Nothing to do — each cycle catches its own exceptions and
keeps serving the last good projection.

**Need to change a parameter.** Edit on GitHub, commit to `main`, Render
redeploys in a few minutes — you lose in-memory state, weigh that against
the fix.

| Constant | File | Controls |
|---|---|---|
| `GLOBAL_EVIDENCE_PRIOR` | `minnesota_gop_gov_model.py` | how much statewide evidence it takes before the shift trusts observed results over the baseline, per candidate |
| `MAX_SINGLE_COUNTY_SHARE` | `minnesota_gop_gov_model.py` | how much any one county (even Hennepin) can move any candidate's shift alone |
| `MOMENTUM_MAX_DRIFT` | `minnesota_gop_gov_model.py` | how far a well-reported county's blended projection can stray from its own raw results, per candidate |
| `TURNOUT_FULL_TRUST_PCT` / `TURNOUT_CLAMP` | `minnesota_gop_gov_model.py` | when/how far feed-implied turnout replaces the baseline |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Render can't find `render.yaml` | not at repo root | redo Part 1 |
| No `serving on` line, service restarts | server didn't bind | check logs for a traceback above it |
| `!! CANDIDATE MATCH FAILED` | feed spells names differently | edit `LINDELL_KEYS` / `DEMUTH_KEYS` / `QUALLS_KEYS` on GitHub |
| `!! UNMATCHED COUNTIES` | county name variant | edit `normalize_county()` on GitHub |
| `/api/projection` says no projection yet | first cycle not done | wait 60 seconds |
| Site pill stuck on "reconnecting" | wrong `API_BASE`, or Render down | check browser console for CORS vs 404 |
| Site shows nothing but the header | Pages output directory wrong | should be `/`, not `web` |
| Shift looks stuck near zero late in the night | evidence still below `GLOBAL_EVIDENCE_PRIOR` | check how many counties are actually reporting first |

---

## Timeline

| When | Do |
|---|---|
| Now | Parts 1-3 (race ID already set, but unverified -- watch Part 2.3/2.4's logs closely) |
| Any time before polls open | Part 4 |
| Polls close | Results start |
| Through the night | Part 5 |
