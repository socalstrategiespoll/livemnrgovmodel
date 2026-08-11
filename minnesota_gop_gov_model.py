"""
Minnesota Republican Gubernatorial Primary -- live county-level model.
Lindell vs. Demuth vs. Qualls vs. Other, FOUR-WAY (not two-candidate).

THIS IS A GENERALIZATION OF THE SENATE TEMPLATE, NOT A COPY

Every prior MN model in this family (minnesota_senate_model.py) tracked a
single scalar "margin" (Flanagan minus Craig). With four candidates there is
no single margin -- so every place the Senate model used a margin, this one
uses a SHARE VECTOR: a dict of {candidate: percent} that always sums to 100.
The underlying techniques (credibility blend, evidence-weighted shift
shrinkage, outlier dampening, single-county evidence cap, momentum clamp,
first-batch discount, turnout recalibration) are unchanged in spirit -- see
minnesota_senate_model.py's own docstring for the full reasoning behind each
one. What follows only documents what changed to go from 2 candidates to N.

NOT DEDUCTIVE, same as the Senate build: every county's full projected result
is effective_turnout * blended_shares, not counted-votes-held-fixed plus a
projected remainder.

STILL MINNESOTA-SPECIFIC (carried forward from the Senate build, unchanged):
Minnesota never separates absentee from Election Day in official results, and
no totals release before 8pm, but county reporting pace still has a real
batch-timing pattern with no fixed statewide rule -- hence MOMENTUM_TRIGGER_PCT
raised to 0.35 and FIRST_BATCH_DISCOUNT = 0.5, both direction-agnostic since
the feed can't verify which way (if any) a mode skew runs, or whether it even
applies the same way in a primary as it did in the Senate general.

WHAT CHANGED FOR THE SHIFT MACHINERY (per-candidate, not one scalar)

_recompute_shifts here runs the SAME evidence-weighted shrinkage independently
for each candidate's share-deviation-from-baseline, using the same per-county
evidence weight (turnout-scaled, single-county-capped) for every candidate --
only the "surprise" (this candidate's observed share minus baseline share)
differs across candidates for a given county. This is equivalent to running
the Senate model's exact one-candidate-vs-the-rest shift computation four
times. The four resulting shifts are NOT constrained to net to zero point-for-
point after shrinkage (independent shrinkage per candidate can drift the sum
slightly off 100); project_shares() renormalizes after blending, same as any
rounding cleanup, rather than building that constraint into the shrinkage math
itself -- simpler, and the drift this could introduce is negligible next to
GLOBAL_EVIDENCE_PRIOR/REGIONAL_EVIDENCE_PRIOR (unchanged from the Senate
build's vote-count scale: MAX_SINGLE_COUNTY_SHARE=0.25 still caps how much any
one county, even Hennepin, can look like a "consistent pattern" alone).

THE BASELINE ITSELF is unusually thin for this race -- see
build_mn_gop_baseline.py's docstring. Sherburne County's row is a statewide-
average placeholder, not a real read (`is_placeholder` in the baseline CSV).
Treat any Sherburne-specific diagnostic with real skepticism until that's
replaced with a real number.
"""

import math
from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np
import pandas as pd

CANDIDATES = ("lindell", "demuth", "qualls", "other")

BASELINE_PATH = "mn_gop_gov_baseline.csv"
TARGET_TURNOUT = 400_000

# ------------------------------------------------------------------
# Regions -- same geography as the Senate build, reused as-is (statewide
# race, same 87 counties). See NEW_MN_MODEL_GUIDE.md: reusable for any
# statewide MN race, would need subsetting only for a district-level race.
# ------------------------------------------------------------------
REGIONS = {
    "Metro": ["Anoka", "Carver", "Dakota", "Hennepin", "Ramsey", "Scott", "Washington"],
    "Southeast": ["Dodge", "Fillmore", "Freeborn", "Goodhue", "Houston", "Mower",
                  "Olmsted", "Rice", "Steele", "Wabasha", "Winona"],
    "Southwest": ["Cottonwood", "Faribault", "Jackson", "Lac Qui Parle", "Lincoln",
                  "Lyon", "Martin", "Murray", "Nobles", "Pipestone", "Redwood",
                  "Renville", "Rock", "Watonwan", "Yellow Medicine"],
    "South Central": ["Blue Earth", "Brown", "Le Sueur", "McLeod", "Nicollet",
                       "Sibley", "Waseca"],
    "Central": ["Benton", "Big Stone", "Chippewa", "Douglas", "Grant", "Kandiyohi",
                "Meeker", "Pope", "Sherburne", "Stearns", "Stevens", "Swift",
                "Todd", "Traverse", "Wright"],
    "Northwest": ["Becker", "Beltrami", "Clay", "Clearwater", "Hubbard", "Kittson",
                  "Lake of the Woods", "Mahnomen", "Marshall", "Norman",
                  "Otter Tail", "Pennington", "Polk", "Red Lake", "Roseau", "Wilkin"],
    "North Central": ["Aitkin", "Cass", "Chisago", "Crow Wing", "Isanti", "Kanabec",
                       "Mille Lacs", "Morrison", "Pine", "Wadena"],
    "Arrowhead": ["Carlton", "Cook", "Itasca", "Koochiching", "Lake", "St. Louis"],
}
COUNTY_REGION = {c: r for r, cs in REGIONS.items() for c in cs}

COUNTY_HETEROGENEITY = {
    "DEFAULT": 2.5,
    "Hennepin": 12.0,
    "Ramsey": 8.0,
    "St. Louis": 5.0,
}

# ------------------------------------------------------------------
# Tuning constants -- unchanged from the Senate build except where noted.
# ------------------------------------------------------------------
CREDIBILITY_EXPONENT = 2.0
OUTLIER_LAMBDA = 3.0
TAU_FLOOR = 0.08
N_SIMS = 20000

TURNOUT_FULL_TRUST_PCT = 25.0
TURNOUT_CLAMP = (0.40, 2.50)

MOMENTUM_TRIGGER_PCT = 0.35
MOMENTUM_MAX_DRIFT = 10.0

FIRST_BATCH_DISCOUNT = 0.5

MAX_SINGLE_COUNTY_SHARE = 0.25
GLOBAL_EVIDENCE_PRIOR = 48_000.0   # scaled down from the Senate build's 60,000
                                   # in proportion to this race's smaller
                                   # 400k-vs-500k target turnout -- same
                                   # vote-count-scale reasoning, not a fresh
                                   # guess. Retune once real returns arrive.
REGIONAL_EVIDENCE_PRIOR = 6_400.0  # same 400k/500k scaling from the Senate
                                   # build's 8,000

PRE_ELECTION_SHARE_SD = 9.0        # per leading-candidate share point,
                                   # analogous to the Senate build's
                                   # PRE_ELECTION_MARGIN_SD


def _load_baseline(path: str = BASELINE_PATH) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = set(df["county"]) - set(COUNTY_REGION)
    if missing:
        raise ValueError(f"No region for: {missing}")
    return df


@dataclass
class CountyState:
    name: str
    region: str
    baseline_shares: Dict[str, float]   # candidate -> percent, sums to 100
    expected_turnout: int
    calibrated_turnout: Optional[float] = None
    pct_reporting: float = 0.0
    votes: Dict[str, int] = field(default_factory=lambda: {c: 0 for c in CANDIDATES})
    is_first_batch: bool = False
    is_placeholder_baseline: bool = False

    @property
    def effective_turnout(self) -> float:
        return self.calibrated_turnout if self.calibrated_turnout is not None else self.expected_turnout

    @property
    def counted_votes(self) -> int:
        return sum(self.votes.values())

    @property
    def pct_counted(self) -> float:
        if self.effective_turnout <= 0:
            return 0.0
        return min(1.0, self.counted_votes / self.effective_turnout)

    @property
    def observed_shares(self) -> Optional[Dict[str, float]]:
        cv = self.counted_votes
        if cv <= 0:
            return None
        return {c: 100.0 * self.votes[c] / cv for c in CANDIDATES}

    @property
    def heterogeneity(self) -> float:
        return COUNTY_HETEROGENEITY.get(self.name, COUNTY_HETEROGENEITY["DEFAULT"])

    @property
    def credibility(self) -> float:
        p = self.pct_counted
        if p <= 0:
            return 0.0
        completeness_weight = p ** (1 / CREDIBILITY_EXPONENT)
        design_var = (self.heterogeneity ** 2) * (1 - p)
        noise_penalty = 1.0 / (1.0 + design_var / 50.0)
        cred = completeness_weight * noise_penalty
        if self.is_first_batch:
            cred *= FIRST_BATCH_DISCOUNT
        return min(0.995, cred)


class MinnesotaGOPGovModel:
    def __init__(self, baseline_path: str = BASELINE_PATH):
        df = _load_baseline(baseline_path)
        self.counties: Dict[str, CountyState] = {}
        for _, row in df.iterrows():
            self.counties[row["county"]] = CountyState(
                name=row["county"],
                region=COUNTY_REGION[row["county"]],
                baseline_shares={c: float(row[c]) for c in CANDIDATES},
                expected_turnout=int(row["turnout"]),
                is_placeholder_baseline=bool(row.get("is_placeholder", False)),
            )
        self.total_evidence_weight = 0.0
        self.statewide_shift: Dict[str, float] = {c: 0.0 for c in CANDIDATES}
        self.statewide_shift_var: Dict[str, float] = {c: TAU_FLOOR ** 2 for c in CANDIDATES}
        self.regional_shift: Dict[str, Dict[str, float]] = {
            r: {c: 0.0 for c in CANDIDATES} for r in REGIONS
        }
        self.turnout_pooled_ratio = 1.0

    # ------------------------------------------------------------
    def update_county(self, name: str, votes: Dict[str, int],
                      pct_reporting: Optional[float]) -> None:
        c = self.counties[name]
        clean = {cand: int(votes.get(cand, 0) or 0) for cand in CANDIDATES}
        new_counted = sum(clean.values())

        if c.counted_votes == 0 and new_counted > 0:
            c.is_first_batch = True
        elif new_counted > c.counted_votes:
            c.is_first_batch = False

        c.votes = clean
        c.pct_reporting = (pct_reporting or 0.0) / 100.0 if pct_reporting and pct_reporting > 1 else (pct_reporting or 0.0)

    # ------------------------------------------------------------
    def _recalibrate_turnout(self) -> None:
        """Unchanged from the Senate build -- candidate count doesn't affect
        turnout calibration at all, only how counted_votes gets split up."""
        rows = []
        for c in self.counties.values():
            if c.pct_reporting <= 0 or c.counted_votes <= 0:
                c.calibrated_turnout = None
                continue
            implied = c.counted_votes / c.pct_reporting
            raw_ratio = implied / c.expected_turnout
            clamped_ratio = float(np.clip(raw_ratio, *TURNOUT_CLAMP))
            weight = float(np.clip(c.pct_reporting * 100 / TURNOUT_FULL_TRUST_PCT, 0.0, 1.0))
            final_ratio = (1 - weight) * 1.0 + weight * clamped_ratio
            c.calibrated_turnout = c.expected_turnout * final_ratio
            rows.append((c.name, raw_ratio, c.expected_turnout))

        if len(rows) >= 5:
            ratios = np.clip([r[1] for r in rows], *TURNOUT_CLAMP)
            sizes = np.array([r[2] for r in rows], dtype=float)
            order = np.argsort(ratios)
            cumulative = np.cumsum(sizes[order]) / sizes.sum()
            median_idx = order[int(np.searchsorted(cumulative, 0.5))]
            self.turnout_pooled_ratio = float(ratios[median_idx])

            reporting_names = {r[0] for r in rows}
            reporting_turnout = sum(self.counties[n].expected_turnout for n in reporting_names)
            total_turnout = sum(c.expected_turnout for c in self.counties.values())
            strength = float(np.clip(reporting_turnout / total_turnout, 0.0, 1.0))
            applied = 1.0 + (self.turnout_pooled_ratio - 1.0) * strength
            for c in self.counties.values():
                if c.calibrated_turnout is None:
                    c.calibrated_turnout = c.expected_turnout * applied

    # ------------------------------------------------------------
    def _shift_for_candidate(self, candidate: str):
        """Same empirical-Bayes shrinkage as the Senate build's single-margin
        version, run independently for one candidate's share-deviation.
        Returns (statewide_shift, regional_shift_dict, total_evidence_weight)."""
        surprises, weights, regions = [], [], []
        for c in self.counties.values():
            om = c.observed_shares
            if om is None:
                continue
            w = c.counted_votes * (c.pct_counted ** (1 / CREDIBILITY_EXPONENT))
            surprises.append(om[candidate] - c.baseline_shares[candidate])
            weights.append(w)
            regions.append(c.region)

        if not surprises:
            return 0.0, {r: 0.0 for r in REGIONS}, 0.0

        surprises = np.array(surprises)
        weights = np.array(weights, dtype=float)
        regions = np.array(regions)

        base_w = weights
        if len(surprises) > 1:
            wmean0 = np.average(surprises, weights=base_w) if base_w.sum() > 0 else surprises.mean()
            resid = surprises - wmean0
            scale = max(np.std(resid), 1e-6)
            outlier_factor = 1.0 / (1.0 + (np.abs(resid) / (OUTLIER_LAMBDA * scale)) ** 2)
        else:
            outlier_factor = np.ones_like(surprises)

        w = base_w * outlier_factor
        w = np.minimum(w, MAX_SINGLE_COUNTY_SHARE * GLOBAL_EVIDENCE_PRIOR)
        total_weight = float(w.sum())

        if w.sum() <= 0:
            statewide = 0.0
        else:
            wmean = np.average(surprises, weights=w)
            shrink = w.sum() / (w.sum() + GLOBAL_EVIDENCE_PRIOR)
            statewide = shrink * wmean

        regional = {}
        for region in REGIONS:
            idx = regions == region
            if not idx.any() or w[idx].sum() <= 0:
                regional[region] = statewide
                continue
            r_wmean = np.average(surprises[idx], weights=w[idx])
            shrink = w[idx].sum() / (w[idx].sum() + REGIONAL_EVIDENCE_PRIOR)
            regional[region] = shrink * r_wmean + (1 - shrink) * statewide

        return statewide, regional, total_weight

    def _recompute_shifts(self) -> None:
        total_w = 0.0
        for candidate in CANDIDATES:
            statewide, regional, w = self._shift_for_candidate(candidate)
            self.statewide_shift[candidate] = statewide
            for r in REGIONS:
                self.regional_shift[r][candidate] = regional[r]
            total_w = max(total_w, w)  # evidence weight is county-driven, same across candidates
        self.total_evidence_weight = total_w

    # ------------------------------------------------------------
    def project_shares(self, c: CountyState) -> Dict[str, float]:
        """Four-way generalization of the Senate build's project_margin():
        adjust each candidate's baseline by its regional shift, blend with
        this county's own observed shares at `credibility` weight, momentum-
        clamp each candidate once well-reported, then renormalize (clipping
        negatives) so the four shares sum to exactly 100."""
        adjusted = {}
        for cand in CANDIDATES:
            adj = c.baseline_shares[cand] + self.regional_shift[c.region][cand]
            adjusted[cand] = float(np.clip(adj, 0.0, 100.0))

        om = c.observed_shares
        if om is None:
            total = sum(adjusted.values()) or 1.0
            return {cand: 100.0 * adjusted[cand] / total for cand in CANDIDATES}

        w = c.credibility
        blended = {cand: w * om[cand] + (1 - w) * adjusted[cand] for cand in CANDIDATES}

        if c.pct_counted >= MOMENTUM_TRIGGER_PCT:
            for cand in CANDIDATES:
                blended[cand] = float(np.clip(
                    blended[cand], om[cand] - MOMENTUM_MAX_DRIFT, om[cand] + MOMENTUM_MAX_DRIFT))

        blended = {cand: max(0.0, v) for cand, v in blended.items()}
        total = sum(blended.values()) or 1.0
        return {cand: 100.0 * blended[cand] / total for cand in CANDIDATES}

    # ------------------------------------------------------------
    def project(self) -> Dict:
        self._recalibrate_turnout()
        self._recompute_shifts()

        totals = {c: 0.0 for c in CANDIDATES}
        counted = {c: 0 for c in CANDIDATES}
        n_reported = 0
        for c in self.counties.values():
            shares = self.project_shares(c)
            turnout = c.effective_turnout
            for cand in CANDIDATES:
                totals[cand] += turnout * shares[cand] / 100.0
                counted[cand] += c.votes[cand]
            if c.counted_votes > 0:
                n_reported += 1

        grand_total = sum(totals.values())
        projected_turnout = sum(c.effective_turnout for c in self.counties.values())
        counted_total = sum(counted.values())
        pct_counted = counted_total / projected_turnout if projected_turnout else 0.0

        pcts = {c: 100 * totals[c] / grand_total for c in CANDIDATES}
        leader = max(pcts, key=pcts.get)
        runner_up = max((c for c in CANDIDATES if c != leader), key=pcts.get)
        lead_margin = pcts[leader] - pcts[runner_up]

        return {
            "pct": pcts,
            "votes": totals,
            "counted": counted,
            "leader": leader,
            "runner_up": runner_up,
            "lead_margin": lead_margin,
            "n_reported": n_reported,
            "projected_turnout": projected_turnout,
            "pct_counted": pct_counted,
            "statewide_shift": dict(self.statewide_shift),
            "regional_shift": {r: dict(v) for r, v in self.regional_shift.items()},
            "turnout_pooled_ratio": self.turnout_pooled_ratio,
            "total_evidence_weight": self.total_evidence_weight,
        }

    # ------------------------------------------------------------
    def run_simulation(self, n_sims: int = N_SIMS, seed: Optional[int] = None) -> Dict:
        """Vectorized Monte Carlo, generalized to N candidates: instead of one
        margin per county per sim, each sim draws a full share vector per
        county (Dirichlet-style noise around the point projection -- a shared
        statewide shock plus county-level noise applied per candidate, then
        clipped >=0 and renormalized), same shrink-with-credibility shape as
        the Senate build."""
        self._recalibrate_turnout()
        self._recompute_shifts()

        rng = np.random.default_rng(seed)
        counties = list(self.counties.values())
        n = len(counties)

        cred = np.array([c.credibility for c in counties])
        heterog = np.array([c.heterogeneity for c in counties])
        eff_turnout = np.array([c.effective_turnout for c in counties])
        pct_counted = np.array([c.pct_counted for c in counties])
        point_shares = np.array([[self.project_shares(c)[cand] for cand in CANDIDATES]
                                  for c in counties])  # (n_counties, n_candidates)

        base_sd = 8.0
        county_sd = base_sd * (1 - cred) ** 0.5 + heterog * (1 - cred) * 0.3
        county_sd = np.maximum(county_sd, 0.5)

        evidence_shrink = self.total_evidence_weight / (self.total_evidence_weight + GLOBAL_EVIDENCE_PRIOR)
        prior_sd = PRE_ELECTION_SHARE_SD * (1 - evidence_shrink)
        shift_var_avg = float(np.mean(list(self.statewide_shift_var.values())))
        statewide_sd = math.sqrt(shift_var_avg) * 15.0
        statewide_sd = math.sqrt(statewide_sd ** 2 + prior_sd ** 2)

        obs = np.array([[c.observed_shares[cand] if c.observed_shares else point_shares[i, j]
                         for j, cand in enumerate(CANDIDATES)]
                        for i, c in enumerate(counties)])
        momentum_active = pct_counted >= MOMENTUM_TRIGGER_PCT

        n_cand = len(CANDIDATES)
        shared_shock = rng.normal(0, statewide_sd, size=(n_sims, 1, n_cand))
        county_shock = rng.normal(0, 1, size=(n_sims, n, n_cand)) * county_sd[None, :, None]
        sim_shares = point_shares[None, :, :] + shared_shock + county_shock

        lo = obs[None, :, :] - MOMENTUM_MAX_DRIFT
        hi = obs[None, :, :] + MOMENTUM_MAX_DRIFT
        clipped = np.clip(sim_shares, lo, hi)
        sim_shares = np.where(momentum_active[None, :, None], clipped, sim_shares)
        sim_shares = np.clip(sim_shares, 0.0, 100.0)
        # Guard against the rare case where noise clips all four candidates
        # to 0 for one county in one sim -- dividing by that zero sum would
        # NaN out the whole simulation's statewide total, not just that one
        # county's contribution.
        row_sums = np.maximum(sim_shares.sum(axis=2, keepdims=True), 1e-9)
        sim_shares = sim_shares / row_sums * 100.0

        sim_votes = eff_turnout[None, :, None] * sim_shares / 100.0
        totals = sim_votes.sum(axis=1)  # (n_sims, n_candidates)
        grand = totals.sum(axis=1, keepdims=True)
        pcts = 100 * totals / grand

        win_counts = (pcts == pcts.max(axis=1, keepdims=True))
        win_prob = {cand: float(np.mean(win_counts[:, j])) for j, cand in enumerate(CANDIDATES)}

        result = {
            "n_sims": n_sims,
            "mean_pct": {cand: float(np.mean(pcts[:, j])) for j, cand in enumerate(CANDIDATES)},
            "median_pct": {cand: float(np.median(pcts[:, j])) for j, cand in enumerate(CANDIDATES)},
            "p05": {cand: float(np.percentile(pcts[:, j], 5)) for j, cand in enumerate(CANDIDATES)},
            "p25": {cand: float(np.percentile(pcts[:, j], 25)) for j, cand in enumerate(CANDIDATES)},
            "p75": {cand: float(np.percentile(pcts[:, j], 75)) for j, cand in enumerate(CANDIDATES)},
            "p95": {cand: float(np.percentile(pcts[:, j], 95)) for j, cand in enumerate(CANDIDATES)},
            "win_prob": win_prob,
        }
        return result


if __name__ == "__main__":
    model = MinnesotaGOPGovModel()
    proj = model.project()
    print("PRE-ELECTION (no votes counted)")
    for cand in CANDIDATES:
        print(f"  {cand.capitalize():8s} {proj['pct'][cand]:5.2f}%")
    print(f"  Leader: {proj['leader']} by {proj['lead_margin']:.2f} over {proj['runner_up']}")
    print(f"  Turnout: {proj['projected_turnout']:,.0f}")

    sim = model.run_simulation(seed=42)
    print("\n  Simulated win probabilities:")
    for cand in CANDIDATES:
        print(f"    {cand.capitalize():8s} {sim['win_prob'][cand]:.1%}")

    print("\nSIMULATED ELECTION NIGHT: Hennepin reports 40% in, "
          "running 10 points more Demuth than baseline")
    hennepin = model.counties["Hennepin"]
    turnout = int(hennepin.expected_turnout * 0.40)
    shares = dict(hennepin.baseline_shares)
    shares["demuth"] += 10.0
    shares["lindell"] -= 10.0
    votes = {c: int(turnout * shares[c] / 100) for c in CANDIDATES}
    model.update_county("Hennepin", votes, pct_reporting=35.0)
    proj = model.project()
    print(f"  Hennepin credibility: {hennepin.credibility:.3f}")
    for cand in CANDIDATES:
        print(f"  Statewide {cand.capitalize():8s} {proj['pct'][cand]:5.2f}%  "
              f"shift {proj['statewide_shift'][cand]:+.2f}")
