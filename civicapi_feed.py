"""
civicAPI live feed for the Minnesota Republican Gubernatorial Primary.

Endpoint:  https://civicapi.org/api/v2/race/{race_id}
Race:      TODO -- no race ID yet. RACE_ID below is a placeholder; fetch_race
           will raise clearly if called before this is set to a real ID.
Auth:      none. Attribution required for non-personal use, so credit
           civicapi.org anywhere this output is published.

Structurally identical to the Senate client (same schema pattern, still
UNVERIFIED against a real payload for this race specifically -- get a sample
early once the race exists in civicAPI's system, per every prior build in
this family).

FOUR-WAY, UNLIKE THE SENATE CLIENT. The Senate client dropped anyone who
wasn't Craig or Flanagan into an unused "other" total. This one is genuinely
four-way: LINDELL_KEYS / DEMUTH_KEYS / QUALLS_KEYS match by substring same as
before, and everyone else in the field is summed into "other" and KEPT, not
dropped -- matching the baseline's real Other bucket (mn_gop_gov_baseline.csv
has real vote share for Other, it isn't zero).
"""

import re
import time
import unicodedata

try:
    import requests
except ImportError:
    requests = None

API_BASE = "https://civicapi.org/api/v2"
MN_GOP_GOV_PRIMARY = 85511  # 2026 MN Governor Republican Primary, from
                            # civicapi.org/results/elections/85511 -- NOT yet
                            # verified against the actual API payload (only the
                            # results page was reachable, not /api/v2/race/85511
                            # directly), so confirm this ID and the payload shape
                            # before relying on it live.

# Substring match keys -- VERIFY against the actual payload once reachable.
LINDELL_KEYS = ("lindell",)
DEMUTH_KEYS = ("demuth",)
QUALLS_KEYS = ("qualls",)

CANDIDATES = ("lindell", "demuth", "qualls", "other")

REQUEST_TIMEOUT = 15
MAX_RETRIES = 4


def normalize_county(name: str) -> str:
    """Same normalization as the Senate build: handles 'St. Louis' against
    'st_louis' or 'Saint Louis', 'Lac qui Parle' against odd casing/spacing,
    and a trailing 'County' if the feed adds one."""
    if name is None:
        return ""
    text = unicodedata.normalize("NFKD", str(name))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"\bcounty\b", " ", text)
    text = re.sub(r"\bsaint\b", "st", text)
    text = text.replace(".", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def build_county_lookup(county_names) -> dict:
    return {normalize_county(c): c for c in county_names}


def fetch_race(race_id=MN_GOP_GOV_PRIMARY, timeout: int = REQUEST_TIMEOUT,
               max_retries: int = MAX_RETRIES, session=None) -> dict:
    """GET a race payload, retrying on transient failure with backoff.
    Raises on exhaustion -- callers should catch and keep the last good
    snapshot. Also raises immediately if race_id is still the None
    placeholder, rather than sending a request that can't possibly work."""
    if race_id is None:
        raise RuntimeError(
            "MN_GOP_GOV_PRIMARY / RACE_ID is not set -- get the real civicAPI "
            "race ID for this primary and set it before deploying.")
    if requests is None:
        raise RuntimeError("requests is not installed: pip install requests")

    url = "{}/race/{}".format(API_BASE, race_id)
    getter = session.get if session is not None else requests.get
    last_error = None

    for attempt in range(max_retries):
        try:
            response = getter(url, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)

    raise RuntimeError("civicAPI fetch failed after {} attempts: {}".format(
        max_retries, last_error))


def _match_one(name: str, keys: tuple) -> bool:
    lowered = str(name).lower()
    return any(k in lowered for k in keys)


def extract_four_way(candidate_list: list) -> tuple:
    """Pull Lindell/Demuth/Qualls votes out of a candidate array; everyone
    else is summed into 'other' and kept (unlike the Senate client, which
    dropped its equivalent bucket). Returns (votes_dict, matched_names)."""
    votes = {c: 0 for c in CANDIDATES}
    matched = {"lindell": None, "demuth": None, "qualls": None}

    for entry in candidate_list or []:
        name = entry.get("name", "")
        n = int(entry.get("votes") or 0)
        if _match_one(name, LINDELL_KEYS):
            votes["lindell"] += n
            matched["lindell"] = name
        elif _match_one(name, DEMUTH_KEYS):
            votes["demuth"] += n
            matched["demuth"] = name
        elif _match_one(name, QUALLS_KEYS):
            votes["qualls"] += n
            matched["qualls"] = name
        else:
            votes["other"] += n

    return votes, matched


def parse_payload(payload: dict, county_names) -> dict:
    """Turn a civicAPI race payload into county-level four-way vote counts."""
    lookup = build_county_lookup(county_names)

    state_votes, matched_names = extract_four_way(payload.get("candidates"))

    records = {}
    unmatched = []

    for _slug, region in (payload.get("region_results") or {}).items():
        if str(region.get("type", "")).lower() not in ("county", ""):
            continue
        raw_name = region.get("name", _slug)
        key = normalize_county(raw_name)
        county = lookup.get(key)
        if county is None:
            unmatched.append(raw_name)
            continue

        votes, _ = extract_four_way(region.get("candidates"))
        if sum(votes.values()) <= 0:
            continue

        records[county] = {
            "votes": votes,
            "percent_precincts": region.get("percent_reporting"),
        }

    return {
        "election_name": payload.get("election_name"),
        "last_updated": payload.get("last_updated"),
        "percent_precincts_statewide": payload.get("percent_reporting"),
        "state_votes": state_votes,
        "candidate_names": matched_names,
        "counties": records,
        "unmatched": unmatched,
    }
