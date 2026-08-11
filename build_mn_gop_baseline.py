"""
Minnesota Republican Gubernatorial Primary -- baseline builder.
Lindell vs. Demuth vs. Qualls (+ Other), three-way + Other, unlike the
two-candidate Craig/Flanagan Senate primary model this repo family started
from.

WHERE EACH PIECE COMES FROM

1. CANDIDATE SHARES per county come from lindell_win_scenario_by_county.csv
   (Wilson-supplied). That file has 92 rows: 86 real county names plus six
   rows labeled HD27A / HD27B / HD3B / HD58B / HD66A / HD66B -- Minnesota
   House district codes, not counties. Those six rows do not map to any of
   the 87 counties and are DROPPED here rather than guessed at.

2. TURNOUT WEIGHTS per county come from a second Wilson-supplied table
   (Jensen/Lacey/Carney reporting numbers) -- per Wilson's clarification,
   that table's candidate columns were not meant to carry over; only its
   per-county vote totals were, as turnout weights to scale up to a 400,000
   statewide target. That table has all 87 real counties.

3. MERGE: joining on county name catches every county except Sherburne,
   which has a turnout weight (5,535 raw votes -- about 1.7% of statewide
   turnout) but no Lindell/Demuth/Qualls/Other split, since its row in the
   first table was one of the six replaced by HD codes.

   SHERBURNE HAS NO REAL BASELINE. It is filled here with the statewide
   average split (computed across the other 86 counties) as an explicit
   placeholder -- NOT a real read for that county. Flagged in the output
   CSV, the model docstring, and README "Known limitations". Replace with
   real Sherburne numbers the moment they're available; this is a stand-in,
   not a signal.

4. Each county's four shares are renormalized to sum to exactly 100 (source
   percentages were rounded to one decimal and occasionally sum to 99.9 or
   100.1).

5. TURNOUT scales the 87-county raw total (323,055) up to TARGET_TURNOUT
   (400,000) by a single uniform factor -- no per-county turnout model
   beyond what the supplied table already encodes.
"""

import pandas as pd

TARGET_TURNOUT = 400_000
CANDIDATES = ("lindell", "demuth", "qualls", "other")

# ---------------------------------------------------------------------
# Table 1: Lindell/Demuth/Qualls/Other percentages (86 real counties + 6
# non-county HD rows, dropped below)
# ---------------------------------------------------------------------
PCT_RAW = """Aitkin,50.2,22.8,27.0,0
Anoka,37.3,37.1,23.3,2.3
Becker,58.1,22.2,7.7,12.0
Beltrami,37.9,27.8,34.4,0
Benton,36.5,44.4,10.7,8.4
Big Stone,36.6,17.2,46.1,0
Blue Earth,37.6,26.1,28.1,8.2
Brown,31.9,36.1,17.1,14.9
Carlton,32.0,17.7,50.3,0
Carver,35.8,31.8,17.6,14.8
Cass,46.3,28.8,15.8,9.1
Chippewa,34.4,23.4,30.9,11.3
Chisago,48.4,28.3,14.9,8.3
Clay,45.8,30.1,24.1,0
Clearwater,67.2,22.4,10.4,0
Cook,49.4,37.5,7.2,5.8
Cottonwood,43.5,22.6,16.9,16.9
Crow Wing,38.5,16.2,17.6,27.7
Dakota,34.4,45.9,16.3,3.5
Dodge,39.3,35.2,20.4,5.2
Douglas,37.5,29.6,27.7,5.2
Faribault,43.6,14.8,41.5,0
Fillmore,50.8,25.0,16.6,7.5
Freeborn,29.6,15.6,19.7,35.0
Goodhue,40.0,22.9,34.5,2.6
Grant,26.8,50.8,22.4,0
HD27A,35.5,40.8,23.7,0
HD27B,58.4,7.6,17.2,16.8
HD3B,32.5,36.8,20.2,10.4
HD58B,35.6,36.5,14.4,13.5
HD66A,32.2,43.8,15.0,9.0
HD66B,35.1,34.2,29.2,1.5
Hennepin,33.1,33.4,23.7,9.8
Houston,43.8,7.2,41.7,7.4
Hubbard,45.0,19.2,26.8,9.1
Isanti,45.8,21.3,21.2,11.7
Itasca,53.0,32.4,14.6,0
Jackson,37.3,15.9,39.4,7.4
Kanabec,42.8,39.9,9.9,7.4
Kandiyohi,39.7,40.7,19.6,0
Kittson,40.7,12.8,39.9,6.6
Koochiching,39.2,56.8,0,4.0
Lac Qui Parle,33.4,63.5,3.2,0
Lake,67.4,20.2,5.2,7.1
Lake of the Woods,21.3,41.6,33.2,3.9
Le Sueur,36.4,27.7,27.7,8.2
Lincoln,32.3,51.9,0,15.8
Lyon,28.0,47.8,15.4,8.8
Mahnomen,54.6,7.9,37.5,0
Marshall,63.7,27.4,3.8,5.1
Martin,37.1,22.4,35.9,4.5
McLeod,47.0,36.5,16.2,0.3
Meeker,36.5,28.9,19.7,14.9
Mille Lacs,55.3,33.3,10.8,0.6
Morrison,33.9,50.4,15.8,0
Mower,37.8,23.1,31.2,7.8
Murray,42.4,27.7,22.8,7.1
Nicollet,46.3,24.9,28.4,0.4
Nobles,39.1,30.6,22.1,8.2
Norman,35.4,28.1,10.3,26.1
Olmsted,40.1,34.7,15.1,10.2
Otter Tail,49.1,38.6,6.6,5.6
Pennington,52.0,3.0,45.0,0
Pine,45.1,32.5,21.7,0.7
Pipestone,43.6,13.2,43.2,0
Polk,33.7,43.6,11.2,11.4
Pope,29.3,20.8,50.0,0
Ramsey,35.8,40.8,17.7,5.6
Red Lake,17.9,69.8,12.3,0
Redwood,41.9,41.2,1.9,15.0
Renville,37.4,31.1,31.4,0
Rice,37.7,27.7,13.6,21.1
Rock,34.4,22.0,6.3,37.4
Roseau,44.2,24.8,17.6,13.4
Scott,37.2,33.8,20.9,8.1
Sibley,28.5,39.7,31.8,0
St. Louis,53.3,38.9,6.8,1.0
Stearns,33.7,63.0,3.3,0
Steele,42.3,20.6,33.9,3.2
Stevens,31.3,14.8,31.4,22.5
Swift,41.8,28.8,20.4,9.0
Todd,39.0,26.9,34.1,0
Traverse,25.2,52.2,16.3,6.3
Wabasha,43.8,23.0,28.1,5.2
Wadena,42.5,37.1,9.5,11.0
Waseca,61.9,19.1,6.3,12.7
Washington,37.7,41.7,20.5,0
Watonwan,32.7,12.2,51.5,3.6
Wilkin,29.3,40.9,29.8,0
Winona,42.1,41.4,10.3,6.2
Wright,34.0,38.8,27.2,0
Yellow Medicine,37.0,22.8,18.2,22.0"""

# ---------------------------------------------------------------------
# Table 2: turnout weights only (all 87 real counties, incl. Sherburne)
# ---------------------------------------------------------------------
TURNOUT_RAW = """Hennepin,35834
Dakota,21275
Washington,17627
Anoka,17204
St. Louis,13485
Olmsted,13394
Ramsey,13259
Stearns,12437
Scott,9742
Otter Tail,7500
Wright,7412
Crow Wing,6354
Sherburne,5535
Goodhue,5347
Carver,5282
Itasca,5218
Morrison,4564
Blue Earth,4310
Rice,4310
Freeborn,3769
Brown,3572
Isanti,3571
Douglas,3546
Kandiyohi,3340
Winona,3283
Chisago,3209
Cass,3139
Steele,3106
Le Sueur,2939
Beltrami,2903
Becker,2893
Mower,2847
Nicollet,2816
Benton,2641
Polk,2549
Mille Lacs,2361
Todd,2200
Wabasha,2152
Martin,2101
Fillmore,2055
McLeod,2042
Dodge,2024
Clay,2021
Aitkin,2006
Hubbard,1959
Meeker,1957
Pine,1878
Carlton,1839
Houston,1759
Waseca,1758
Redwood,1705
Wadena,1511
Faribault,1480
Sibley,1478
Lyon,1467
Roseau,1409
Nobles,1375
Kanabec,1304
Lake,1230
Marshall,1171
Watonwan,1153
Clearwater,1088
Jackson,1065
Koochiching,1012
Rock,987
Murray,892
Pipestone,879
Pope,871
Grant,788
Pennington,777
Cottonwood,754
Renville,736
Chippewa,704
Yellow Medicine,693
Stevens,624
Traverse,578
Lac Qui Parle,574
Swift,566
Wilkin,556
Lake of the Woods,503
Cook,487
Kittson,464
Big Stone,448
Norman,441
Red Lake,363
Mahnomen,317
Lincoln,281"""


def build() -> pd.DataFrame:
    pct_rows = []
    for line in PCT_RAW.strip().splitlines():
        parts = line.split(",")
        name = parts[0]
        if name.startswith("HD"):
            continue  # not a county -- see module docstring
        vals = [float(x) for x in parts[1:5]]
        total = sum(vals)
        vals = [100.0 * v / total for v in vals]  # renormalize to exactly 100
        pct_rows.append((name,) + tuple(vals))
    pct = pd.DataFrame(pct_rows, columns=["county"] + list(CANDIDATES))

    turnout_rows = [tuple(line.split(",")) for line in TURNOUT_RAW.strip().splitlines()]
    turnout = pd.DataFrame(turnout_rows, columns=["county", "raw_turnout"])
    turnout["raw_turnout"] = turnout["raw_turnout"].astype(int)

    df = turnout.merge(pct, on="county", how="left")

    missing = df[df["lindell"].isna()]["county"].tolist()
    if missing != ["Sherburne"]:
        raise ValueError(f"Unexpected missing-baseline set: {missing}")

    # Sherburne placeholder: statewide average of the 86 known counties,
    # turnout-weighted. NOT a real read -- see module docstring.
    known = df.dropna(subset=["lindell"])
    avg = {c: (known[c] * known["raw_turnout"]).sum() / known["raw_turnout"].sum()
           for c in CANDIDATES}
    for c in CANDIDATES:
        df.loc[df["county"] == "Sherburne", c] = avg[c]
    df["is_placeholder"] = df["county"] == "Sherburne"

    scale = TARGET_TURNOUT / df["raw_turnout"].sum()
    df["turnout"] = (df["raw_turnout"] * scale).round().astype(int)
    drift = TARGET_TURNOUT - df["turnout"].sum()
    if drift != 0:
        df.loc[df["turnout"].idxmax(), "turnout"] += drift

    for c in CANDIDATES:
        df[f"{c}_votes"] = (df["turnout"] * df[c] / 100).round().astype(int)

    return df[["county", "turnout"] + list(CANDIDATES) +
              [f"{c}_votes" for c in CANDIDATES] + ["is_placeholder"]]


if __name__ == "__main__":
    df = build()
    turnout = df["turnout"].sum()
    print(f"Counties: {len(df)}  (placeholder: "
          f"{df.loc[df['is_placeholder'], 'county'].tolist()})")
    print(f"Statewide turnout: {turnout:,}")
    for c in CANDIDATES:
        v = df[f"{c}_votes"].sum()
        print(f"  {c.capitalize():8s} {v:>8,}  {100*v/turnout:5.2f}%")
    df.round(3).to_csv("mn_gop_gov_baseline.csv", index=False)
    print("\nWrote mn_gop_gov_baseline.csv")
