# Render web service: polls civicAPI on a background thread and serves the
# projection. Same reasoning as the Senate/MI/WI builds for web service vs.
# cron job -- a cron container is destroyed after every run, wiping the
# turnout-calibration and shift state this model accumulates over the night.
#
# FOUR-WAY, UNLIKE THE SENATE SERVER. build_output()/build_county_table() key
# everything off CANDIDATES (lindell/demuth/qualls/other) instead of two
# hardcoded names. Still stdlib-only, single-process threading server, same
# reasoning as before: gunicorn with multiple workers would spawn multiple
# pollers fighting over the API. State is in memory unless STATE_DIR is set.

import json
import os
import threading
import time
import traceback

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from minnesota_gop_gov_model import MinnesotaGOPGovModel, REGIONS, CANDIDATES
from civicapi_feed import fetch_race, parse_payload, MN_GOP_GOV_PRIMARY


PORT = int(os.environ.get("PORT", 10000))
RACE_ID = os.environ.get("RACE_ID") or MN_GOP_GOV_PRIMARY
if RACE_ID is not None:
    RACE_ID = int(RACE_ID)
N_SIMS = int(os.environ.get("N_SIMS", 20000))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 60))
HISTORY_LIMIT = int(os.environ.get("HISTORY_LIMIT", 2000))
STATE_DIR = os.environ.get("STATE_DIR", "")
BASELINE_PATH = os.environ.get("BASELINE_PATH", "mn_gop_gov_baseline.csv")


class ModelState:
    def __init__(self):
        self.lock = threading.Lock()
        self.projection = None
        self.history = []
        self.error = None
        self.cycles = 0
        self.started_at = datetime.now(timezone.utc).isoformat()

    def publish(self, output: dict) -> None:
        with self.lock:
            self.projection = output
            self.history.append({
                "updated_at": output["updated_at"],
                "pct": output["projection"]["pct"],
                "leader": output["projection"]["leader"],
                "lead_margin": output["projection"]["lead_margin"],
                "win_prob": output["projection"]["win_prob"],
                "pct_counted": output["counted"]["pct_of_projected_turnout"],
                "counties_reporting": output["diagnostics"]["counties_reporting"],
                "statewide_shift": output["diagnostics"]["statewide_shift"],
            })
            if len(self.history) > HISTORY_LIMIT:
                self.history = self.history[-HISTORY_LIMIT:]
            self.error = None
            self.cycles += 1

    def fail(self, message: str) -> None:
        with self.lock:
            self.error = message

    def snapshot(self) -> tuple:
        with self.lock:
            return self.projection, list(self.history), self.error, self.cycles


STATE = ModelState()


def build_output(model: MinnesotaGOPGovModel, sim: dict, proj: dict,
                 parsed: dict, race_id) -> dict:
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "civicapi.org",
        "attribution": "Election results from civicAPI (civicapi.org)",
        "race_id": race_id,
        "election_name": parsed.get("election_name"),
        "feed_last_updated": parsed.get("last_updated"),
        "counted": {
            "votes": parsed.get("state_votes"),
            "pct_of_projected_turnout": round(100 * proj["pct_counted"], 2),
            "pct_precincts_reporting": parsed.get("percent_precincts_statewide"),
        },
        "turnout": {
            "projected": round(proj["projected_turnout"]),
            "turnout_vs_prior": round(proj["turnout_pooled_ratio"], 3),
        },
        "projection": {
            "pct": {c: round(proj["pct"][c], 2) for c in CANDIDATES},
            "votes": {c: int(proj["votes"][c]) for c in CANDIDATES},
            "leader": proj["leader"],
            "runner_up": proj["runner_up"],
            "lead_margin": round(proj["lead_margin"], 2),
            "win_prob": {c: round(sim["win_prob"][c], 4) for c in CANDIDATES},
            "median_pct": {c: round(sim["median_pct"][c], 2) for c in CANDIDATES},
            "interval_50": {c: [round(sim["p25"][c], 2), round(sim["p75"][c], 2)] for c in CANDIDATES},
            "interval_90": {c: [round(sim["p05"][c], 2), round(sim["p95"][c], 2)] for c in CANDIDATES},
        },
        "counties": build_county_table(model),
        "diagnostics": {
            "counties_reporting": proj["n_reported"],
            "statewide_shift": {c: round(v, 2) for c, v in proj["statewide_shift"].items()},
            "total_evidence_weight": round(proj["total_evidence_weight"]),
            "unmatched_counties": parsed.get("unmatched", []),
            "candidate_names": parsed.get("candidate_names"),
        },
        "regional_shift": {
            r: {c: round(v, 2) for c, v in shifts.items()}
            for r, shifts in proj["regional_shift"].items()
        },
    }


def build_county_table(model: MinnesotaGOPGovModel) -> list:
    """Per-county rows covering all 87 counties every cycle. Each row's raw
    'votes'/'pct' is the reported count, held EXACTLY fixed by the model
    (deductive); 'projected_final' is that same reported count plus the
    model's projection for whatever remains uncounted in that county (see
    minnesota_gop_gov_model.py's project_county_votes()) -- for a fully
    reported county the two are identical, since nothing is left to model."""
    rows = []
    for name, c in model.counties.items():
        raw_votes = dict(c.votes)
        raw_total = c.counted_votes
        raw_pct = {cand: (100 * raw_votes[cand] / raw_total if raw_total else None)
                   for cand in CANDIDATES}
        projected_pct = model.project_county_pct(c)
        remaining = max(0, c.effective_turnout - c.counted_votes)

        rows.append({
            "county": name,
            "region": c.region,
            "reporting": raw_total > 0,
            "votes": raw_votes,
            "pct": raw_pct,
            "expected_baseline": {cand: round(c.baseline_shares[cand], 1) for cand in CANDIDATES},
            "credibility": round(c.credibility, 3),
            "first_batch": c.is_first_batch,
            "placeholder_baseline": c.is_placeholder_baseline,
            "pct_precincts": round(c.pct_reporting * 100, 1) if c.pct_reporting else None,
            "pct_of_projected": round(100 * c.pct_counted, 1),
            "projected_total": int(c.effective_turnout),
            "calibrated_turnout": int(c.calibrated_turnout) if c.calibrated_turnout else None,
            "remaining": int(round(remaining)),
            "projected_final": {cand: round(projected_pct[cand], 1) for cand in CANDIDATES},
        })

    rows.sort(key=lambda r: (-sum(r["votes"].values()), -r["projected_total"]))
    return rows


def save_state(model: MinnesotaGOPGovModel) -> None:
    if not STATE_DIR:
        return
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        snap = {
            name: {"votes": c.votes, "pct_reporting": c.pct_reporting,
                   "is_first_batch": c.is_first_batch}
            for name, c in model.counties.items() if c.counted_votes > 0
        }
        with open(os.path.join(STATE_DIR, "feed_state.json"), "w") as handle:
            json.dump(snap, handle)
    except Exception:
        pass


def load_state(model: MinnesotaGOPGovModel) -> None:
    if not STATE_DIR:
        return
    path = os.path.join(STATE_DIR, "feed_state.json")
    try:
        with open(path) as handle:
            stored = json.load(handle)
        for name, rec in stored.items():
            if name in model.counties:
                model.update_county(name, rec["votes"], rec["pct_reporting"])
                model.counties[name].is_first_batch = rec.get("is_first_batch", False)
        print("restored {} counties from {}".format(len(stored), path), flush=True)
    except Exception:
        pass


def poller() -> None:
    model = MinnesotaGOPGovModel(BASELINE_PATH)
    load_state(model)
    county_names = list(model.counties.keys())

    print("poller started: race {} every {}s, {} sims".format(
        RACE_ID, POLL_INTERVAL, N_SIMS), flush=True)
    if RACE_ID is None:
        print("!! RACE_ID is not set -- every cycle will fail until it is. "
              "See civicapi_feed.py / render.yaml.", flush=True)

    while True:
        started = time.time()
        try:
            payload = fetch_race(RACE_ID)
            parsed = parse_payload(payload, county_names)

            for county, record in parsed["counties"].items():
                model.update_county(county, record["votes"], record.get("percent_precincts"))

            sim = model.run_simulation(n_sims=N_SIMS)
            proj = model.project()
            output = build_output(model, sim, proj, parsed, RACE_ID)
            STATE.publish(output)
            save_state(model)

            names = output["diagnostics"].get("candidate_names") or {}
            missing = [k for k in ("lindell", "demuth", "qualls") if not names.get(k)]
            if missing:
                print("!! CANDIDATE MATCH FAILED for {} -- fix the matching "
                      "KEYS in civicapi_feed.py".format(missing), flush=True)
            else:
                print("   matched: {} / {} / {}".format(
                    names["lindell"], names["demuth"], names["qualls"]), flush=True)
            if output["diagnostics"]["unmatched_counties"]:
                print("!! UNMATCHED COUNTIES: {} -- fix normalize_county() in "
                      "civicapi_feed.py".format(
                          output["diagnostics"]["unmatched_counties"]), flush=True)

            p = output["projection"]
            print("[{}] {:.1f}% counted | {} cty | L {:.1f} D {:.1f} Q {:.1f} O {:.1f} | "
                  "leader {} +{:.1f}".format(
                      datetime.now().strftime("%H:%M:%S"),
                      output["counted"]["pct_of_projected_turnout"],
                      output["diagnostics"]["counties_reporting"],
                      p["pct"]["lindell"], p["pct"]["demuth"], p["pct"]["qualls"], p["pct"]["other"],
                      p["leader"], p["lead_margin"]), flush=True)

        except Exception as exc:
            STATE.fail(str(exc))
            print("[{}] cycle failed, serving last good projection: {}".format(
                datetime.now().strftime("%H:%M:%S"), exc), flush=True)
            traceback.print_exc()

        time.sleep(max(1.0, POLL_INTERVAL - (time.time() - started)))


class Handler(BaseHTTPRequestHandler):

    def _send(self, body, status=200, content_type="application/json"):
        encoded = (body if isinstance(body, bytes) else json.dumps(body).encode("utf-8"))
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        projection, history, error, cycles = STATE.snapshot()

        if path in ("/", "/health"):
            return self._send({
                "ok": True, "cycles": cycles, "started_at": STATE.started_at,
                "last_error": error, "has_projection": projection is not None,
                "race_id_set": RACE_ID is not None,
            })
        if path == "/api/projection":
            if projection is None:
                return self._send({"error": "no projection yet", "last_error": error}, status=503)
            return self._send(projection)
        if path == "/api/history":
            return self._send({"count": len(history), "cycles": history})
        return self._send({"error": "not found"}, status=404)

    def log_message(self, *args):
        return


def main():
    thread = threading.Thread(target=poller, daemon=True)
    thread.start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("serving on :{}".format(PORT), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
