// ---------------------------------------------------------------------------
// Point this at your Render service URL. No trailing slash.
// ---------------------------------------------------------------------------
const API_BASE = "https://mn-gop-gov-primary-model.onrender.com";

const REFRESH_MS = 15000;
const STALE_AFTER_MS = 180000;

const CANDIDATES = ["lindell", "demuth", "qualls", "other"];
const LABELS = { lindell: "Lindell", demuth: "Demuth", qualls: "Qualls", other: "Other" };
// Same palette philosophy as the Senate build's Flanagan/Craig comment: all
// four are in the same party for this primary, so no red/blue signal is
// possible or meaningful -- four distinct qualitative hues instead.
const COLORS = { lindell: "#1F6F6B", demuth: "#96701A", qualls: "#5B4A8A", other: "#7B8177" };

const num = new Intl.NumberFormat("en-US");
const $ = (id) => document.getElementById(id);
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function signed(v, d = 1) {
  return (v >= 0 ? "+" : "\u2212") + Math.abs(v).toFixed(d);
}

// ---------------------------------------------------------------------------
// Win-probability bars -- replaces the two-candidate margin distribution
// curve, which doesn't generalize cleanly to four candidates.
// ---------------------------------------------------------------------------

function drawWinProb(winProb) {
  const wrap = $("winprob-bars");
  const entries = CANDIDATES.map((c) => [c, winProb[c] || 0]).sort((a, b) => b[1] - a[1]);
  wrap.innerHTML = entries.map(([cand, p]) => {
    const pct = (p * 100);
    const label = pct < 0.1 && pct > 0 ? "<0.1%" : pct.toFixed(pct < 1 ? 1 : 0) + "%";
    return `<div class="wp-row">
      <span class="wp-name cand-${cand}">${LABELS[cand]}</span>
      <div class="wp-track"><span class="wp-fill cand-${cand}-bg" style="width:${Math.max(pct, pct > 0 ? 1.5 : 0)}%"></span></div>
      <span class="wp-pct">${label}</span>
    </div>`;
  }).join("");
}

// ---------------------------------------------------------------------------
// Maps -- colored by LEADING candidate per county (not a two-color gradient).
// Same Albers projection tuned to Minnesota as the Senate build.
// ---------------------------------------------------------------------------

const MAP_W = 620;
const MAP_H = 700;

const MAPS = [
  { id: "counted", label: "Counted so far",
    note: "Leading candidate in the votes already reported. Grey has not reported." },
  { id: "projected", label: "Model projection",
    note: "The model's blended projection -- baseline and observed results combined, not just the raw count." },
];

let GEO = null;
let PATHS = {};
let LAST_COUNTIES = [];
let PINNED = null;
let VIEW = { x: 0, y: 0, k: 1 };

function albers() {
  const LAT0 = 46.4, LON0 = -93.4, P1 = 44.5, P2 = 48.3;
  const rad = Math.PI / 180;
  const n = 0.5 * (Math.sin(P1 * rad) + Math.sin(P2 * rad));
  const C = Math.cos(P1 * rad) ** 2 + 2 * n * Math.sin(P1 * rad);
  const rho0 = Math.sqrt(C - 2 * n * Math.sin(LAT0 * rad)) / n;
  return ([lon, lat]) => {
    const theta = n * (lon - LON0) * rad;
    const rho = Math.sqrt(Math.max(C - 2 * n * Math.sin(lat * rad), 1e-12)) / n;
    return [rho * Math.sin(theta), rho0 - rho * Math.cos(theta)];
  };
}

function buildPaths(geo) {
  const project = albers();
  const projected = geo.features.map((f) => {
    const polys = f.geometry.type === "Polygon"
      ? [f.geometry.coordinates]
      : f.geometry.coordinates;
    return {
      name: f.properties.name,
      polys: polys.map((poly) => poly.map((r) => r.map(project))),
    };
  });

  let minx = Infinity, maxx = -Infinity, miny = Infinity, maxy = -Infinity;
  projected.forEach((f) => f.polys.forEach((poly) => poly.forEach((r) => r.forEach(([x, y]) => {
    if (x < minx) minx = x;
    if (x > maxx) maxx = x;
    if (y < miny) miny = y;
    if (y > maxy) maxy = y;
  }))));

  const pad = 8;
  const k = Math.min((MAP_W - 2 * pad) / (maxx - minx), (MAP_H - 2 * pad) / (maxy - miny));
  const ox = (MAP_W - (maxx - minx) * k) / 2;
  const oy = (MAP_H - (maxy - miny) * k) / 2;

  const out = {};
  projected.forEach((f) => {
    let d = "";
    f.polys.forEach((poly) => poly.forEach((r) => {
      if (r.length < 4) return;
      d += "M" + r.map(([x, y]) =>
        ((x - minx) * k + ox).toFixed(1) + " " + ((maxy - y) * k + oy).toFixed(1)
      ).join("L") + "Z";
    }));
    out[f.name] = d;
  });
  return out;
}

function leaderOf(shares) {
  let best = null, bestV = -1, second = -1;
  CANDIDATES.forEach((c) => {
    const v = shares[c] || 0;
    if (v > bestV) { second = bestV; best = c; bestV = v; }
    else if (v > second) { second = v; }
  });
  return { leader: best, margin: bestV - second };
}

function rampColor(leader, margin) {
  if (!leader) return "#C7CCC2";
  const base = COLORS[leader];
  const t = Math.max(0, Math.min(1, margin / 40));
  const k = 0.35 + 0.65 * Math.pow(t, 0.75); // never fully washed out, never over-saturated
  const mid = [232, 234, 227];
  const rgb = hexToRgb(base);
  const c = mid.map((m, i) => Math.round(m + (rgb[i] - m) * k));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

function hexToRgb(hex) {
  const h = hex.replace("#", "");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

function countyRow(name) {
  return LAST_COUNTIES.find((r) => r.county === name);
}

function mapValue(row, mode) {
  if (!row) return { leader: null, margin: 0 };
  if (mode === "counted") {
    if (!row.reporting) return { leader: null, margin: 0 };
    return leaderOf(row.pct);
  }
  return leaderOf(row.projected_final);
}

async function loadGeo() {
  try {
    const res = await fetch("mn-counties.geojson", { cache: "no-store" });
    GEO = await res.json();
    PATHS = buildPaths(GEO);
    MAPS.forEach(buildOne);
    paintMaps();
  } catch (err) {
    console.error("failed to load geojson", err);
  }
}

function buildOne(map) {
  const g = $("shapes-" + map.id);
  g.innerHTML = "";
  Object.entries(PATHS).forEach(([name, d]) => {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    path.dataset.county = name;
    path.dataset.map = map.id;
    path.addEventListener("mouseenter", () => hover(name));
    path.addEventListener("mouseleave", () => hover(null));
    path.addEventListener("click", () => { PINNED = PINNED === name ? null : name; markSelected(); paintDetail(PINNED); });
    g.appendChild(path);
  });
  $("note-" + map.id).textContent = map.note;
}

let HOVERED = null;
function hover(name) {
  HOVERED = name;
  markSelected();
  paintDetail(PINNED || HOVERED);
}

function markSelected() {
  document.querySelectorAll(".map-cell svg path").forEach((p) => {
    p.classList.toggle("hov", p.dataset.county === HOVERED && !PINNED);
    p.classList.toggle("sel", p.dataset.county === PINNED);
  });
  $("unpin").hidden = !PINNED;
  $("pin-note").hidden = !PINNED;
}

function paintMaps() {
  if (!PATHS || !Object.keys(PATHS).length) return;
  MAPS.forEach((map) => {
    document.querySelectorAll(`path[data-map="${map.id}"]`).forEach((path) => {
      const row = countyRow(path.dataset.county);
      const { leader, margin } = mapValue(row, map.id);
      path.setAttribute("fill", rampColor(leader, margin));
    });
  });
  paintLegend();
}

function paintLegend() {
  const wrap = $("legend-candidates");
  wrap.innerHTML = CANDIDATES.map((c) =>
    `<span><i class="swatch" style="background:${COLORS[c]}"></i>${LABELS[c]}</span>`
  ).join("") + `<span><i class="swatch" style="background:#C7CCC2"></i>Not reporting</span>`;
}

function paintDetail(name) {
  const wrap = $("map-detail");
  if (!name) {
    wrap.innerHTML = `<p class="map-hint">Hover a county on either map. Click to pin it.</p>`;
    return;
  }
  const row = countyRow(name);
  if (!row) {
    wrap.innerHTML = `<p class="map-hint">${name} -- no data yet.</p>`;
    return;
  }
  const rows = CANDIDATES.map((c) => {
    const raw = row.pct[c];
    const proj = row.projected_final[c];
    return `<dt>${LABELS[c]}</dt><dd>${raw == null ? "—" : raw.toFixed(1) + "%"} / ${proj.toFixed(1)}%</dd>`;
  }).join("");
  wrap.innerHTML = `
    <h3>${name}${row.placeholder_baseline ? " <span style='font-size:0.6em;color:var(--flag)'>(placeholder baseline)</span>" : ""}</h3>
    <dl>${rows}</dl>
    <p class="split-note">Raw counted / model projection. ${row.pct_of_projected.toFixed(1)}% of projected turnout in.</p>
  `;
}

function zoomAt(svg, factor, cx, cy) {
  const box = svg.getBoundingClientRect();
  const px = ((cx - box.left) / box.width) * MAP_W;
  const py = ((cy - box.top) / box.height) * MAP_H;
  const k = Math.max(1, Math.min(12, VIEW.k * factor));
  VIEW.x = px - ((px - VIEW.x) / VIEW.k) * k;
  VIEW.y = py - ((py - VIEW.y) / VIEW.k) * k;
  VIEW.k = k;
  clampView();
  applyView();
}

function clampView() {
  const limX = MAP_W * (VIEW.k - 1);
  const limY = MAP_H * (VIEW.k - 1);
  VIEW.x = Math.max(-limX, Math.min(0, VIEW.x));
  VIEW.y = Math.max(-limY, Math.min(0, VIEW.y));
}

function applyView() {
  document.querySelectorAll(".map-cell svg g").forEach((g) => {
    g.setAttribute("transform", `translate(${VIEW.x},${VIEW.y}) scale(${VIEW.k})`);
  });
}

function initMapInteraction() {
  MAPS.forEach((map) => {
    const svg = document.getElementById("svg-" + map.id);

    svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      zoomAt(svg, e.deltaY < 0 ? 1.18 : 1 / 1.18, e.clientX, e.clientY);
    }, { passive: false });

    let drag = null;
    svg.addEventListener("pointerdown", (e) => {
      if (VIEW.k === 1) return;
      drag = { x: e.clientX, y: e.clientY, vx: VIEW.x, vy: VIEW.y };
      svg.setPointerCapture(e.pointerId);
      svg.classList.add("dragging");
    });
    svg.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const box = svg.getBoundingClientRect();
      VIEW.x = drag.vx + ((e.clientX - drag.x) / box.width) * MAP_W;
      VIEW.y = drag.vy + ((e.clientY - drag.y) / box.height) * MAP_H;
      clampView();
      applyView();
    });
    const stop = (e) => {
      if (!drag) return;
      drag = null;
      svg.releasePointerCapture(e.pointerId);
      svg.classList.remove("dragging");
    };
    svg.addEventListener("pointerup", stop);
    svg.addEventListener("pointercancel", stop);
  });

  $("zoom-in").addEventListener("click", () => {
    const svg = document.getElementById("svg-counted");
    const b = svg.getBoundingClientRect();
    zoomAt(svg, 1.5, b.left + b.width / 2, b.top + b.height / 2);
  });
  $("zoom-out").addEventListener("click", () => {
    const svg = document.getElementById("svg-counted");
    const b = svg.getBoundingClientRect();
    zoomAt(svg, 1 / 1.5, b.left + b.width / 2, b.top + b.height / 2);
  });
  $("zoom-reset").addEventListener("click", () => {
    VIEW = { x: 0, y: 0, k: 1 };
    applyView();
  });
  $("unpin").addEventListener("click", () => {
    PINNED = null;
    markSelected();
    paintDetail(null);
  });
}

// ---------------------------------------------------------------------------

let lastLead = null;

function animateMargin(el, to) {
  const from = lastLead;
  lastLead = to;
  if (reduceMotion || from === null || Math.abs(to - from) < 0.05) {
    el.textContent = signed(to);
    return;
  }
  const start = performance.now();
  const dur = 550;
  const tick = (now) => {
    const t = Math.min(1, (now - start) / dur);
    const eased = 1 - Math.pow(1 - t, 3);
    el.textContent = signed(from + (to - from) * eased);
    if (t < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function renderCounties(all) {
  const rows = (all || []).filter((r) => r.reporting);
  const body = $("county-rows");
  if (!rows.length) {
    body.innerHTML = `<tr class="empty"><td colspan="7">No counties reporting yet.</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((r) => {
    const { leader } = leaderOf(r.pct);
    return `<tr>
      <td class="name">${r.county}${r.placeholder_baseline ? " *" : ""}</td>
      <td class="num">${r.pct_of_projected.toFixed(0)}%</td>
      <td class="num cand-lindell">${r.pct.lindell == null ? "—" : r.pct.lindell.toFixed(1)}</td>
      <td class="num cand-demuth">${r.pct.demuth == null ? "—" : r.pct.demuth.toFixed(1)}</td>
      <td class="num cand-qualls">${r.pct.qualls == null ? "—" : r.pct.qualls.toFixed(1)}</td>
      <td class="num cand-other">${r.pct.other == null ? "—" : r.pct.other.toFixed(1)}</td>
      <td class="cand-${leader}">${LABELS[leader]}</td>
    </tr>`;
  }).join("");
}

function renderRegions(shifts) {
  const wrap = $("regions");
  const entries = Object.entries(shifts).map(([name, byCand]) => {
    const { leader, margin } = leaderOf(
      Object.fromEntries(CANDIDATES.map((c) => [c, Math.abs(byCand[c] || 0)]))
    );
    return [name, leader, byCand[leader] || 0];
  }).sort((a, b) => Math.abs(b[2]) - Math.abs(a[2]));

  wrap.innerHTML = entries.map(([name, cand, v]) => {
    return `<div class="region">
      <div class="region-name">${name}</div>
      <div class="region-val cand-${cand}">${LABELS[cand]} ${signed(v, 2)}</div>
    </div>`;
  }).join("");
}

function render(data) {
  const p = data.projection;
  const c = data.counted;
  const d = data.diagnostics;
  const t = data.turnout;

  $("lead-name").textContent = LABELS[p.leader];
  $("lead-name").className = "verdict-name cand-" + p.leader;
  $("lead-margin").className = "verdict-number cand-" + p.leader;
  animateMargin($("lead-margin"), p.lead_margin);

  $("verdict-sub").textContent = d.counties_reporting === 0
    ? "Pre-election baseline. No counties reporting."
    : `Leads ${LABELS[p.runner_up]} by ${p.lead_margin.toFixed(1)} points, from ${d.counties_reporting} ` +
      `${d.counties_reporting === 1 ? "county" : "counties"} and ${c.pct_of_projected_turnout.toFixed(1)}% of the projected vote.`;

  drawWinProb(p.win_prob);

  $("counted").textContent = c.pct_of_projected_turnout.toFixed(1) + "%";
  $("precincts").textContent =
    c.pct_precincts_reporting == null ? "—" : c.pct_precincts_reporting + "%";
  $("turnout").textContent = t.projected ? num.format(t.projected) : "—";

  CANDIDATES.forEach((cand) => {
    $("pct-" + cand).textContent = p.pct[cand].toFixed(1) + "%";
    $("votes-" + cand).textContent = num.format(p.votes[cand] || 0) + " projected";
  });

  $("tally-bar").innerHTML = CANDIDATES.map((cand) =>
    `<span class="tally-seg cand-${cand}-bg" style="width:${p.pct[cand]}%" title="${LABELS[cand]} ${p.pct[cand].toFixed(1)}%"></span>`
  ).join("");

  LAST_COUNTIES = data.counties || [];
  renderCounties(data.counties);
  paintMaps();
  renderRegions(data.regional_shift || {});

  $("d-counties").textContent = d.counties_reporting;
  $("d-shift-lindell").textContent = signed(d.statewide_shift.lindell, 2);
  $("d-shift-demuth").textContent = signed(d.statewide_shift.demuth, 2);
  $("d-shift-qualls").textContent = signed(d.statewide_shift.qualls, 2);

  const stamp = new Date(data.updated_at);
  const stale = Date.now() - stamp.getTime() > STALE_AFTER_MS;
  setPulse(stale ? "stale" : "live", stale ? "feed stale" : "live", stamp);

  if (d.unmatched_counties && d.unmatched_counties.length) {
    $("alert").textContent =
      "Not matched to a model county, and excluded from the projection: " +
      d.unmatched_counties.join(", ");
    $("alert").hidden = false;
  } else {
    $("alert").hidden = true;
  }
}

function setPulse(state, label, stamp) {
  $("pulse").dataset.state = state;
  $("pulse-label").textContent = label;
  if (stamp) {
    $("stamp").textContent = stamp.toLocaleTimeString([], {
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  }
}

async function tick() {
  try {
    const res = await fetch(API_BASE + "/api/projection", { cache: "no-store" });
    if (res.status === 503) {
      setPulse("connecting", "waiting for first results");
      return;
    }
    if (!res.ok) throw new Error("HTTP " + res.status);
    render(await res.json());
  } catch (err) {
    setPulse("stale", "reconnecting");
  }
}

initMapInteraction();
loadGeo();
tick();
setInterval(tick, REFRESH_MS);
