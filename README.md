# Minnesota Republican Gubernatorial Primary — Live Model

County-level Bayesian live election-night model for the Minnesota Republican
gubernatorial primary (Lindell vs. Demuth vs. Qualls, plus Other), fed by
civicAPI once a race ID exists for it.

Results from [civicAPI](https://civicapi.org).

## FOUR-WAY, not two-candidate

Every prior model in this family (Michigan, Wisconsin, South Dakota, the MN
US Senate primary) tracked exactly two candidates and a single scalar
"margin." This race has three named candidates plus a real Other bucket, so
`minnesota_gop_gov_model.py` tracks a **share vector** (a dict of
`{lindell, demuth, qualls, other}` percentages summing to 100) everywhere the
Senate model tracked a margin. See that file's module docstring for exactly
what changed and what didn't.

## How it fits together

```
civicAPI  ──►  Render web service  ──►  Cloudflare Pages
 (poll)         (model + JSON API)        (the site)
```

One backend service. It polls civicAPI on a background thread, runs the model, and
serves the result over HTTP. The site reads that URL directly.

**This is a web service, not a cron job.** A cron container is destroyed after every
run, which wipes the turnout-calibration and shift state the model accumulates over
the night, and it has no URL for a site to read. The web service solves both by
staying alive.

## This model is NOT deductive

Same architecture as the Senate build: every county's full projection is a
credibility-weighted **blend** of its own observed results and a
(shift-adjusted) baseline, not counted-votes-held-fixed-plus-projected-
remainder. A single large county partially reporting is capped
(`MAX_SINGLE_COUNTY_SHARE`) so it can't read as a "consistent pattern" on its
own; a genuine multi-county pattern converges the shift toward the real swing
well before 100% reporting. Full reasoning in
`minnesota_gop_gov_model.py`'s module docstring.

## Files

| File | Does |
|---|---|
| `server.py` | background poller + JSON API. The entrypoint |
| `civicapi_feed.py` | API client, payload parsing, county name matching (four-way) |
| `minnesota_gop_gov_model.py` | baseline loading, credibility blending, shift shrinkage, turnout recalibration, Monte Carlo -- generalized to N candidates |
| `build_mn_gop_baseline.py` | builds `mn_gop_gov_baseline.csv` from the two supplied source tables |
| `mn_gop_gov_baseline.csv` | the 87-county baseline the model loads at startup |
| `mn-counties.geojson` | county shapes for the map -- reused as-is from the Senate build (same 87 counties, statewide race) |
| `index.html` / `app.js` / `style.css` | the static site |

## Endpoints

| Route | Returns |
|---|---|
| `/health` | uptime, cycle count, last error, whether `RACE_ID` is set |
| `/api/projection` | the current projection, county table, diagnostics |
| `/api/history` | one compact record per cycle since start |

CORS is open, so the site can be hosted anywhere.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `RACE_ID` | civicAPI race | **unset -- see Known limitations** |
| `N_SIMS` | Monte Carlo draws | `20000` |
| `POLL_INTERVAL` | seconds between cycles | `60` |
| `STATE_DIR` | optional disk path so turnout/shift state survives a restart | unset |

## Known limitations

- **civicAPI race ID is set (85511)** but **completely UNVERIFIED against the
  raw API payload** -- confirmed only via the human-facing results page at
  `civicapi.org/results/elections/85511`, which renders client-side and
  doesn't expose the JSON. `/api/v2/race/85511` itself hasn't been checked.
  `LINDELL_KEYS`/`DEMUTH_KEYS`/`QUALLS_KEYS` in `civicapi_feed.py` are still
  guesses at substring matches. Hit the endpoint directly (or watch the
  first Render deploy's logs) before trusting it live.
- **`percent_reporting` counts precincts, not votes**, same caution as every
  prior build in this family.
- **The baseline is thinner than prior builds.** It comes from a per-county
  Lindell/Demuth/Qualls/Other percentage table merged with a separate
  per-county turnout table, scaled to a 400,000 statewide target -- not a
  crowd-prediction map or a historical-primary coalition covariate like the
  Craig/Flanagan baseline had. **Sherburne County's row is a statewide-
  average placeholder** (`is_placeholder_baseline` in the model,
  `is_placeholder` in the CSV) -- the source percentage table had six rows
  labeled with Minnesota House district codes (HD27A, HD27B, HD3B, HD58B,
  HD66A, HD66B) instead of Sherburne, and those six rows don't map to any
  county so they were dropped rather than guessed at. Replace Sherburne's
  placeholder with a real number the moment one is available.
- **Evidence-prior constants** (`GLOBAL_EVIDENCE_PRIOR=48,000`,
  `REGIONAL_EVIDENCE_PRIOR=6,400`) are the Senate build's constants scaled
  down in proportion to this race's smaller 400k-vs-500k target turnout, not
  freshly tuned against this race's own synthetic scenarios. Retune if the
  shift looks too sticky or too twitchy on election night.
- **No coalition-shape covariate** (the Senate build used a 2020
  Sanders/Warren primary as a progressive-coalition proxy). None was
  supplied for this race; the baseline is the raw percentage table alone.
- **State is in memory.** A restart costs the shift/turnout calibration
  until counties report again. Set `STATE_DIR` to a mounted disk to avoid
  that.
