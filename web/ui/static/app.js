// ambient-gen web UI. Plain ES module, no framework or build step.
// Everything on screen is built from /api/capabilities:
//   compose  -- ambient.py's settings, presets (with resolved values), patches
//   release  -- process-audio's music pipeline (masters, Short, videos, captions)

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "style") n.style.cssText = v; // via CSSOM: the CSP blocks style attributes
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v === true) n.setAttribute(k, "");
    else n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) n.append(kid);
  return n;
};

// ------------------------------------------------------------------ API
async function api(path, opts = {}) {
  const init = { ...opts, headers: { ...(opts.headers || {}) } };
  if (init.method && init.method !== "GET") init.headers["X-Requested-With"] = "ambient-gen";
  if (init.body && typeof init.body !== "string") {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(init.body);
  }
  const res = await fetch(path, init);
  const data = res.headers.get("Content-Type")?.includes("json") ? await res.json() : null;
  if (!res.ok) throw new Error(data?.error || `${res.status} ${res.statusText}`);
  return data;
}

function banner(msg) {
  $("#banner-text").textContent = msg;
  $("#banner").hidden = !msg;
}
$("#banner-close").addEventListener("click", () => banner(""));

// ------------------------------------------------------------------ theme
const themeBtn = $("#theme-toggle");
function applyTheme(t) { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }
try { applyTheme(localStorage.getItem("ag-theme")); } catch {}
themeBtn.addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  const next = dark ? "light" : "dark";
  applyTheme(next);
  try { localStorage.setItem("ag-theme", next); } catch {}
});

// ------------------------------------------------------------------ state
let caps = null;              // /api/capabilities
let P = {};                   // param path -> param
let presets = {};             // name -> preset
let patchValues = {};         // patch name -> synth values
const form = {
  preset: null,
  title: "",
  overrides: {},              // compose settings the user changed: path -> value
  release: { on: true, type: null, outputs: [], formats: [], values: {} },
  mpc: false,
  midi: false,
};

// ------------------------------------------------------------------ compose model

// A JS port of ambient.py's load_settings(): defaults <- each layer's
// patch (or the kit's) <- the preset <- the user's changes.
function resolve(presetName, overrides) {
  const preset = presets[presetName];
  const given = { ...(preset?.settings || {}), ...overrides };
  const S = {};
  for (const p of caps.compose.params) S[p.path] = p.default;
  const kitName = given["groove.kit"] ?? S["groove.kit"];
  const kit = caps.compose.kits[kitName] || {};
  for (const layer of Object.keys(caps.compose.layers)) {
    const patch = kit[layer] || given[`${layer}.patch`] || S[`${layer}.patch`];
    S[`${layer}.patch`] = patch;
    for (const [k, v] of Object.entries(patchValues[patch] || {})) S[`${layer}.synth.${k}`] = v;
  }
  for (const [k, v] of Object.entries(given)) {
    if (k.endsWith(".patch") && k.split(".")[0] in kit) continue;
    if (k in P) S[k] = v;
  }
  return S;
}

const current = () => resolve(form.preset, form.overrides);
// the value a setting would have without the user's change to it
function baseline(path) {
  const o = { ...form.overrides };
  delete o[path];
  return resolve(form.preset, o)[path];
}

function family(preset) {
  const r = preset.resolved;
  if (!r["groove.enabled"]) return "ambient";
  return r["groove.style"] === "techno" ? "techno" : "lofi";
}
const FAMILIES = [
  ["ambient", "Ambient", "Drifting chords, bells and pads -- from sleep music to cathedrals."],
  ["lofi", "Lo-fi beats", "Half-time drums, 808 slides, hi-hat rolls, vinyl."],
  ["techno", "Techno", "Four-on-the-floor, acid lines, builds and drops -- made for long jams."],
];
const RELEASE_TYPE = { ambient: "ambient", lofi: "trap", techno: "techno" };

// ------------------------------------------------------------------ option widgets
const fmt = (v) => (typeof v === "number" ? (Number.isInteger(v) ? String(v) : String(+v.toFixed(4))) : String(v));
const human = (path) => path.split(".").pop().replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

// A control for one ambient.py setting. onChange(value) is called with the
// typed value; the caller decides whether it counts as a change.
function control(p, value, onChange) {
  const id = "c_" + p.path.replace(/\W/g, "_");
  if (p.type === "bool") {
    const cb = el("input", { type: "checkbox", id, checked: !!value, onchange: () => onChange(cb.checked) });
    return el("label", { class: "switch" }, cb, el("span", { class: "muted small", text: value ? "On" : "Off" }));
  }
  if (p.choices && p.choices.length) {
    const sel = el("select", { id, onchange: () => onChange(p.type === "str" ? sel.value : Number(sel.value)) },
      p.choices.map((c) => el("option", { value: String(c), selected: String(c) === String(value), text: fmt(c).replace(/_/g, " ") })));
    return sel;
  }
  if (p.type === "int" || p.type === "float") {
    const step = p.type === "int" ? 1 : stepFor(p);
    const num = el("input", { type: "number", id, value: fmt(value), step, min: p.min ?? undefined, max: p.max ?? undefined,
      onchange: () => { const v = Number(num.value); if (num.value !== "" && Number.isFinite(v)) onChange(p.type === "int" ? Math.round(v) : v); } });
    if (p.min === null || p.max === null) return num;
    const log = p.min > 0 && p.max / p.min >= 50; // frequencies and times feel right on a log slider
    const toSlider = (v) => (log ? Math.log(v / p.min) / Math.log(p.max / p.min) : (v - p.min) / (p.max - p.min)) * 1000;
    const fromSlider = (s) => { const t = s / 1000; return log ? p.min * Math.pow(p.max / p.min, t) : p.min + t * (p.max - p.min); };
    const range = el("input", { type: "range", min: 0, max: 1000, step: 1, value: toSlider(value), "aria-label": p.path,
      oninput: () => { num.value = fmt(snap(fromSlider(Number(range.value)), p)); },
      onchange: () => onChange(snap(fromSlider(Number(range.value)), p)) });
    return el("div", { class: "range-row" }, range, num);
  }
  const txt = el("input", { type: "text", id, value: value ?? "", onchange: () => onChange(txt.value) });
  return txt;
}
function stepFor(p) {
  const span = (p.max ?? 1) - (p.min ?? 0);
  return span >= 1000 ? 1 : span >= 50 ? 0.5 : span >= 5 ? 0.05 : 0.01;
}
function snap(v, p) {
  if (p.type === "int") return Math.round(v);
  const step = stepFor(p);
  const r = Math.round(v / step) * step;
  return +Math.min(p.max, Math.max(p.min, r)).toFixed(4);
}

// One option card: label, reset link when changed, control, help.
function optCard(p, { label, hint, wide, extra } = {}) {
  const S = current();
  const value = S[p.path];
  const changed = p.path in form.overrides;
  const set = (v) => {
    if (JSON.stringify(v) === JSON.stringify(baseline(p.path))) delete form.overrides[p.path];
    else form.overrides[p.path] = v;
    renderCompose();
  };
  return el("div", { class: `opt${changed ? " changed" : ""}${wide ? " wide" : ""}`, "data-path": p.path },
    el("div", { class: "opt-top" },
      el("label", { class: "opt-label", for: "c_" + p.path.replace(/\W/g, "_"), text: label || human(p.path) }),
      changed ? el("button", { type: "button", class: "reset", text: "reset", onclick: () => { delete form.overrides[p.path]; renderCompose(); } }) : null),
    control(p, value, set),
    extra || null,
    el("div", { class: "opt-hint", text: hint || p.help }));
}

// ------------------------------------------------------------------ step 1: presets
function renderPresets() {
  const box = $("#presets");
  box.replaceChildren();
  for (const [fam, title, desc] of FAMILIES) {
    const list = caps.compose.presets.filter((p) => family(p) === fam);
    if (!list.length) continue;
    const grid = el("div", { class: "choice-grid", role: "radiogroup", "aria-label": title });
    for (const p of list) {
      const r = p.resolved;
      const tags = [`${r["harmony.key"]} ${r["harmony.scale"].replace(/_/g, " ")}`];
      if (r["groove.enabled"]) tags.push(`${r["groove.bpm"]} BPM`);
      grid.append(el("button", { type: "button", class: "choice", role: "radio", "aria-checked": String(form.preset === p.name),
        onclick: () => choosePreset(p.name) },
        el("span", { class: "choice-title", text: p.name.replace(/_/g, " ") }),
        el("span", { class: "choice-desc", text: p.description }),
        el("span", { class: "tags" }, tags.map((t) => el("span", { class: "badge", text: t })))));
    }
    box.append(el("div", { class: "preset-family" }, el("div", { class: "subhead", text: `${title} · ${desc}` }), grid));
  }
}

function choosePreset(name) {
  form.preset = name;
  // keep the length, title and a fixed seed across presets; drop sound tweaks
  const keep = {};
  for (const k of ["minutes", "seed"]) if (k in form.overrides) keep[k] = form.overrides[k];
  form.overrides = keep;
  form.release.type = RELEASE_TYPE[family(presets[name])];
  renderPresets();
  renderCompose();
  renderRelease();
}

// ------------------------------------------------------------------ step 2: easy + advanced
const KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

function renderEasy() {
  const S = current();
  const box = $("#easy");
  box.replaceChildren();

  // title: a plain field (it names the files and the video)
  const title = el("input", { type: "text", id: "c_title", value: form.title, maxlength: 120, placeholder: "Untitled",
    oninput: () => { form.title = title.value; renderSummary(); } });
  box.append(el("div", { class: "opt wide" },
    el("div", { class: "opt-top" }, el("label", { class: "opt-label", for: "c_title", text: "Title" })), title,
    el("div", { class: "opt-hint", text: "Names the files, and becomes the video and caption title." })));

  const quick = el("div", { class: "quick" }, [3, 10, 30, 60, 120].map((m) =>
    el("button", { type: "button", text: m >= 60 ? `${m / 60} h` : `${m} min`,
      onclick: () => { if (m === baseline("minutes")) delete form.overrides.minutes; else form.overrides.minutes = m; renderCompose(); } })));
  box.append(optCard(P["minutes"], { label: "Length (minutes)", hint: "How long the song is. Rendering runs 10-40x faster than real time.", extra: quick }));
  box.append(optCard(P["harmony.key"], { label: "Key" }));
  box.append(optCard(P["harmony.scale"], { label: "Scale", hint: "Major/lydian sound bright, minor/dorian/phrygian darker, pentatonics can't sound wrong." }));
  if (S["groove.enabled"]) box.append(optCard(P["groove.bpm"], { label: "Tempo (BPM)" }));
  if (S["groove.enabled"] && S["groove.style"] === "techno") {
    box.append(optCard(P["techno.density"], { label: "Busyness", hint: "How many elements tend to play at once." }));
    box.append(optCard(P["groove.kit"], { label: "Drum kit" }));
  } else if (S["groove.enabled"]) {
    box.append(optCard(P["groove.swing"], { label: "Swing" }));
    box.append(optCard(P["groove.kit"], { label: "Drum kit" }));
  } else {
    box.append(optCard(P["reverb.seconds"], { label: "Room size (reverb seconds)" }));
    box.append(optCard(P["harmony.chord_seconds_max"], { label: "Longest chord (seconds)", hint: "Lower = the harmony moves more." }));
  }

  // seed: empty = a new piece every time
  const seedVal = form.overrides.seed;
  const seed = el("input", { type: "number", id: "c_seed", min: 1, step: 1, placeholder: "Random", value: seedVal ?? "",
    onchange: () => { const v = Math.round(Number(seed.value)); if (seed.value === "" || !(v > 0)) delete form.overrides.seed; else form.overrides.seed = v; renderCompose(); } });
  box.append(el("div", { class: `opt${seedVal ? " changed" : ""}` },
    el("div", { class: "opt-top" }, el("label", { class: "opt-label", for: "c_seed", text: "Seed" }),
      seedVal ? el("button", { type: "button", class: "reset", text: "random", onclick: () => { delete form.overrides.seed; renderCompose(); } }) : null),
    seed,
    el("div", { class: "opt-hint", text: "Leave empty for a new piece every time. The same seed and settings make the identical song." })));
}

// group order and labels for Advanced
const GROUPS = [
  ["", "Piece"], ["harmony", "Harmony"], ["groove", "Groove"], ["techno", "Techno arrangement"],
  ["pad", "Pad"], ["bass", "Bass"], ["bells", "Bells / stabs"], ["lead", "Lead"],
  ["kick", "Kick"], ["snare", "Snare / clap"], ["hats", "Hi-hats"], ["openhat", "Open hat"], ["perc", "Percussion"],
  ["air", "Air"], ["vinyl", "Vinyl"], ["reverb", "Reverb"], ["master", "Master"],
];
const advOpen = new Set();
let advQuery = "";
let advOnlyChanged = false;

function groupRelevant(g, S) {
  const drums = ["kick", "snare", "hats"];
  if (g === "groove") return true;
  if (g === "techno") return S["groove.enabled"] && S["groove.style"] === "techno";
  if (drums.includes(g)) return S["groove.enabled"];
  if (g === "openhat" || g === "perc") return S["groove.enabled"] && S["groove.style"] === "techno";
  return true;
}

function renderAdvanced() {
  const S = current();
  const box = $("#adv-groups");
  box.replaceChildren();
  const q = advQuery.trim().toLowerCase();
  const match = (p) => (!q || p.path.toLowerCase().includes(q) || (p.help || "").toLowerCase().includes(q))
    && (!advOnlyChanged || p.path in form.overrides);
  let shown = 0;
  for (const [g, label] of GROUPS) {
    if (!groupRelevant(g, S)) continue;
    const mine = caps.compose.params.filter((p) => (g === "" ? !p.path.includes(".") : p.path.split(".")[0] === g));
    const plain = mine.filter((p) => !p.path.includes(".synth.") && match(p));
    const synth = mine.filter((p) => p.path.includes(".synth.") && match(p));
    if (!plain.length && !synth.length) continue;
    shown += plain.length + synth.length;
    const changed = mine.filter((p) => p.path in form.overrides).length;
    const det = el("details", { class: "adv-group", open: !!q || advOnlyChanged || advOpen.has(g),
      ontoggle: () => (det.open ? advOpen.add(g) : advOpen.delete(g)) },
      el("summary", {}, label, changed ? el("span", { class: "changed-count", text: `${changed} changed` }) : null,
        el("span", { class: "count", text: `${plain.length + synth.length}` })));
    if (plain.length) det.append(el("div", { class: "opt-grid" }, plain.map((p) => optCard(p, { extra: el("div", { class: "opt-path", text: p.path }) }))));
    if (synth.length) {
      const sid = g + ".synth";
      const sdet = el("details", { class: "adv-group", open: !!q || advOnlyChanged || advOpen.has(sid),
        ontoggle: () => (sdet.open ? advOpen.add(sid) : advOpen.delete(sid)) },
        el("summary", {}, `Synth sound: ${S[g + ".patch"]}`, el("span", { class: "count", text: `${synth.length}` })),
        el("div", { class: "opt-grid" }, synth.map((p) => optCard(p, { label: human(p.path), extra: el("div", { class: "opt-path", text: p.path }) }))));
      det.append(el("div", { class: "adv-sub" }, sdet));
    }
    box.append(det);
  }
  if (!shown) box.append(el("p", { class: "empty", text: q ? `No settings match "${advQuery}".` : "Nothing changed yet." }));
  const n = Object.keys(form.overrides).length;
  $("#adv-summary").textContent = `· ${caps.compose.params.length} settings${n ? ` · ${n} changed` : ""}`;
}
$("#adv-search").addEventListener("input", (e) => { advQuery = e.target.value; renderAdvanced(); });
$("#adv-changed").addEventListener("change", (e) => { advOnlyChanged = e.target.checked; renderAdvanced(); });
$("#reset-compose").addEventListener("click", () => { form.overrides = {}; renderCompose(); });

function renderCompose() {
  // keep focus and scroll stable across re-renders
  const y = window.scrollY;
  renderEasy();
  renderAdvanced();
  renderReleaseNotes();
  renderSummary();
  window.scrollTo(0, y);
}

// ------------------------------------------------------------------ step 3: release
let REL = null; // process-audio's release capabilities ({pipeline, formats, ...}), or null

// generated songs aren't live hardware recordings: the server fills in this
// "how it was made" caption text unless the user writes their own
const METHOD_DEFAULT = "Composed and rendered by ambient-gen, a generative music engine in Python: " +
  "every note, sound and arrangement grown from a seed.";

// the song's starting key, which beats process-audio's estimate in the
// captions and tags; the server fills it in the same way (jobs.SongKey)
function songKey() {
  const S = current();
  return S["harmony.key"] && S["harmony.scale"] ? `${S["harmony.key"]} ${S["harmony.scale"].replaceAll("_", " ")}` : "";
}

function relDefault(o) {
  if (o.id === "method") return METHOD_DEFAULT;
  if (o.id === "key") return songKey();
  const t = form.release.type;
  return o.defaults_by_type && t in o.defaults_by_type ? o.defaults_by_type[t] : o.default;
}
function relActive(o) {
  const r = form.release;
  if (o.requires_analysis) return false; // needs an uploaded-file analysis the song job doesn't do
  if (o.applies_to && !o.applies_to.includes(r.type)) return false;
  if (o.requires_output && !o.requires_output.some((x) => r.outputs.includes(x))) return false;
  if (o.requires_option) {
    for (const [k, want] of Object.entries(o.requires_option)) {
      const opt = REL.pipeline.options.find((x) => x.id === k);
      const v = k in r.values ? r.values[k] : relDefault(opt);
      if (Array.isArray(want) ? !want.includes(v) : v !== want) return false;
    }
  }
  return true;
}

function relControl(o, value, set) {
  const id = "r_" + o.id;
  switch (o.kind) {
    case "bool": {
      const cb = el("input", { type: "checkbox", id, checked: !!value, disabled: !!o.disabled_reason, onchange: () => set(cb.checked) });
      return el("label", { class: "switch" }, cb, el("span", { class: "muted small", text: value ? "On" : "Off" }));
    }
    case "choice":
      if (o.choices.length <= 4) {
        return el("div", { class: "seg", role: "group" }, o.choices.map((c) =>
          el("button", { type: "button", "aria-pressed": String(String(value) === c.value), text: c.label, onclick: () => set(c.value) })));
      }
      {
        const sel = el("select", { id, onchange: () => set(sel.value) },
          o.choices.map((c) => el("option", { value: c.value, selected: String(value) === c.value, text: c.label })));
        return sel;
      }
    case "number": {
      const num = el("input", { type: "number", id, value: value ?? o.suggest ?? "", min: o.min, max: o.max, step: o.step ?? "any",
        onchange: () => { if (num.value === "") set(undefined); else set(Number(num.value)); } });
      return o.unit ? el("div", { class: "inline-row" }, num, el("span", { class: "muted small", text: o.unit })) : num;
    }
    case "time_list": {
      const txt = el("input", { type: "text", id, value: (value || []).join(", "), placeholder: "e.g. 1:30, 4:05",
        onchange: () => set(txt.value.split(",").map((s) => s.trim()).filter(Boolean)) });
      return txt;
    }
    default: {
      const txt = el("input", { type: "text", id, value: value ?? "", placeholder: o.kind === "time" ? "m:ss" : "",
        onchange: () => set(txt.value) });
      return txt;
    }
  }
}

function relCard(o) {
  const r = form.release;
  const dflt = relDefault(o);
  const value = o.id in r.values ? r.values[o.id] : dflt;
  const changed = o.id in r.values;
  const set = (v) => {
    if (v === undefined || JSON.stringify(v) === JSON.stringify(dflt)) delete r.values[o.id];
    else r.values[o.id] = v;
    renderRelease();
  };
  return el("div", { class: `opt${changed ? " changed" : ""}${o.disabled_reason ? " disabled" : ""}` },
    el("div", { class: "opt-top" }, el("label", { class: "opt-label", for: "r_" + o.id, text: o.label }),
      changed ? el("button", { type: "button", class: "reset", text: "reset", onclick: () => { delete r.values[o.id]; renderRelease(); } }) : null),
    relControl(o, value, set),
    o.help ? el("div", { class: "opt-hint", text: o.help }) : null,
    o.detail_by_type?.[r.type] ? el("div", { class: "opt-detail", text: o.detail_by_type[r.type] }) : null,
    o.disabled_reason ? el("div", { class: "opt-reason", text: o.disabled_reason }) : null);
}

function relGroups(advanced) {
  const groups = REL.pipeline.groups || [];
  const out = [];
  for (const g of groups) {
    const opts = REL.pipeline.options.filter((o) => o.group === g.id && !!o.advanced === advanced && relActive(o));
    if (opts.length) out.push(el("div", { class: "opt-group" }, el("h3", { text: g.label }), el("div", { class: "opt-grid" }, opts.map(relCard))));
  }
  return out;
}

function renderRelease() {
  const sec = $("#step-release");
  if (!REL) {
    sec.classList.add("release-off");
    $("#release-on").checked = false;
    $("#release-on").disabled = true;
    $("#release-on-label").textContent = "Off";
    $("#release-unavailable").hidden = false;
    $("#release-unavailable").textContent = caps.release.reason || "Mastering and videos aren't available.";
    renderSummary();
    return;
  }
  const r = form.release;
  sec.classList.toggle("release-off", !r.on);
  $("#release-on").checked = r.on;
  $("#release-on-label").textContent = r.on ? "On" : "Off";

  $("#release-type").replaceChildren(...REL.pipeline.types.map((t) =>
    el("button", { type: "button", "aria-pressed": String(r.type === t.id), text: t.label,
      onclick: () => { r.type = t.id; renderRelease(); } })));

  $("#release-outputs").replaceChildren(...REL.pipeline.outputs.map((o) => {
    const cb = el("input", { type: "checkbox", checked: r.outputs.includes(o.id), disabled: o.available === false,
      onchange: () => { r.outputs = cb.checked ? [...r.outputs, o.id] : r.outputs.filter((x) => x !== o.id); renderRelease(); } });
    return el("label", { class: "chip", title: o.disabled_reason || "" }, cb, el("span", {}, o.label, o.slow ? el("small", { text: "slow" }) : null));
  }));

  const wantsAudio = r.outputs.some((id) => REL.pipeline.outputs.find((o) => o.id === id)?.formats);
  $("#release-formats").replaceChildren(...REL.formats.map((f) => {
    const cb = el("input", { type: "checkbox", checked: r.formats.includes(f.id), disabled: !f.available || !wantsAudio,
      onchange: () => { r.formats = cb.checked ? [...r.formats, f.id] : r.formats.filter((x) => x !== f.id); renderRelease(); } });
    return el("label", { class: "chip" }, cb, el("span", { text: f.label }));
  }));

  $("#release-basic").replaceChildren(...relGroups(false));
  $("#release-adv").replaceChildren(...relGroups(true));
  const n = Object.keys(r.values).length;
  $("#release-adv-summary").textContent = n ? `· ${n} changed` : "";
  renderReleaseNotes();
  renderSummary();
}
$("#release-on").addEventListener("change", (e) => { form.release.on = e.target.checked; renderRelease(); });
$("#export-mpc").addEventListener("change", (e) => { form.mpc = e.target.checked; renderSummary(); });
$("#export-midi").addEventListener("change", (e) => { form.midi = e.target.checked; renderSummary(); });

function renderReleaseNotes() {
  if (!REL) return;
  const r = form.release;
  const minutes = current()["minutes"];
  const videos = r.outputs.filter((o) => o.startsWith("video_"));
  const preview = r.values.max_seconds;
  const note = $("#release-slow");
  if (r.on && r.outputs.includes("video_16x9") && minutes > 10 && !preview) {
    note.hidden = false;
    note.textContent = `A full-length ${minutes}-minute YouTube video takes a while to render (roughly as long as the song or more). ` +
      `To try things out first, set "Preview length" under Advanced → Video.`;
  } else if (r.on && videos.length && preview) {
    note.hidden = false;
    note.className = "note info";
    note.textContent = `Videos are limited to ${preview} s (Preview length).`;
  } else {
    note.hidden = true;
    note.className = "note";
  }
}

// ------------------------------------------------------------------ run
function request() {
  const req = { title: form.title.trim(), preset: form.preset, values: { ...form.overrides } };
  if (form.mpc) req.mpc = true;
  if (form.midi) req.midi = true;
  if (REL && form.release.on) {
    const r = form.release;
    const values = {};
    for (const [k, v] of Object.entries(r.values)) {
      const o = REL.pipeline.options.find((x) => x.id === k);
      if (o && relActive(o)) values[k] = v;
    }
    req.release = { type: r.type, values, outputs: r.outputs, formats: r.outputs.some((id) => REL.pipeline.outputs.find((o) => o.id === id)?.formats) ? r.formats : [] };
  }
  return req;
}

function renderSummary() {
  if (!form.preset) return;
  const S = current();
  const parts = [form.preset.replace(/_/g, " "), `${fmt(S.minutes)} min`, `${S["harmony.key"]} ${S["harmony.scale"].replace(/_/g, " ")}`];
  if (S["groove.enabled"]) parts.push(`${fmt(S["groove.bpm"])} BPM`);
  const n = Object.keys(form.overrides).filter((k) => k !== "minutes" && k !== "seed").length;
  if (n) parts.push(`${n} setting${n > 1 ? "s" : ""} changed`);
  if (REL && form.release.on) {
    const labels = form.release.outputs.map((id) => REL.pipeline.outputs.find((o) => o.id === id)?.label.split(" (")[0]);
    parts.push("then: " + (labels.join(", ") || "nothing selected"));
  }
  const extras = [form.mpc && "MPC project", form.midi && "MIDI"].filter(Boolean);
  if (extras.length) parts.push("+ " + extras.join(", "));
  $("#run-summary").textContent = parts.join(" · ");
  $("#run-btn").disabled = !!(REL && form.release.on && !form.release.outputs.length);
}

async function run() {
  const btn = $("#run-btn");
  btn.disabled = true;
  try {
    const job = await api("/api/jobs", { method: "POST", body: request() });
    banner("");
    location.hash = `#/job/${job.id}`;
    refreshJobs();
  } catch (e) {
    banner(e.message);
  } finally {
    btn.disabled = false;
  }
}
$("#run-btn").addEventListener("click", run);

async function showPlan() {
  try {
    const plan = await api("/api/plan", { method: "POST", body: request() });
    const body = $("#plan-body");
    body.replaceChildren(
      el("dl", { class: "kv" }, el("dt", { text: "Preset" }), el("dd", { text: form.preset }),
        el("dt", { text: "Seed" }), el("dd", { text: form.overrides.seed ? String(plan.seed) : `${plan.seed} (random; a new one is picked when you create it)` })),
      ...plan.commands.map((c) => el("div", {}, el("div", { class: "subhead", text: c.label }), el("pre", { class: "mono", text: c.command }))));
    $("#plan-dialog").showModal();
  } catch (e) {
    banner(e.message);
  }
}
$("#plan-btn").addEventListener("click", showPlan);
for (const id of ["#plan-close", "#plan-cancel"]) $(id).addEventListener("click", () => $("#plan-dialog").close());
$("#plan-run").addEventListener("click", () => { $("#plan-dialog").close(); run(); });

// ------------------------------------------------------------------ jobs drawer
const drawer = $("#jobs-drawer");
$("#jobs-toggle").addEventListener("click", () => { drawer.hidden = !drawer.hidden; $("#jobs-toggle").setAttribute("aria-expanded", String(!drawer.hidden)); if (!drawer.hidden) refreshJobs(); });
$("#jobs-close").addEventListener("click", () => { drawer.hidden = true; $("#jobs-toggle").setAttribute("aria-expanded", "false"); });

const when = (iso) => new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });

async function refreshJobs() {
  let list = [];
  try { list = await api("/api/jobs"); } catch { return; }
  const active = list.filter((j) => j.state === "running" || j.state === "queued").length;
  $("#jobs-active").hidden = !active;
  $("#jobs-active").textContent = active;
  const ul = $("#job-list");
  ul.replaceChildren(...(list.length ? list.map((j) => el("li", { class: "job-item" },
    el("a", { href: `#/job/${j.id}`, text: j.title, onclick: () => { drawer.hidden = true; } }),
    el("span", { class: `state ${j.state}`, text: j.state }),
    el("span", { class: "muted", text: `${j.preset.replace(/_/g, " ")} · seed ${j.seed} · ${when(j.created_at)}` }),
    j.state === "running" || j.state === "queued" ? null :
      el("button", { type: "button", class: "btn ghost small danger", text: "Delete",
        onclick: async () => { if (confirm(`Delete "${j.title}" and its files?`)) { await api(`/api/jobs/${j.id}`, { method: "DELETE" }).catch((e) => banner(e.message)); refreshJobs(); } } })))
    : [el("li", { class: "empty", text: "No songs yet." })]));
}

// ------------------------------------------------------------------ job view
let es = null;
let viewJob = null;
let logLines = [];

function openJob(id) {
  $("#wizard").hidden = true;
  $("#jobview").hidden = false;
  if (es) es.close();
  logLines = [];
  $("#job-log").textContent = "";
  $("#job-results").replaceChildren();
  es = new EventSource(`/api/jobs/${id}/events`);
  es.addEventListener("job", (e) => { const m = JSON.parse(e.data); renderJob(m.job); });
  es.addEventListener("log", (e) => {
    logLines.push(JSON.parse(e.data).line);
    if (logLines.length > 400) logLines = logLines.slice(-400);
    const pre = $("#job-log");
    pre.textContent = logLines.join("\n");
  });
  es.onerror = () => {}; // the browser reconnects on its own
}

const size = (b) => (b > 1e9 ? `${(b / 1e9).toFixed(2)} GB` : b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`);
let renderedFiles = "";

function renderJob(j) {
  viewJob = j;
  $("#job-title").textContent = j.title;
  const st = $("#job-state");
  st.className = `state ${j.state}`;
  st.textContent = j.state;
  const r = j.request;
  const facts = [`preset ${j.preset.replace(/_/g, " ")}`, `seed ${j.seed}`];
  if (r.values.minutes) facts.push(`${r.values.minutes} min`);
  if (r.release) facts.push(`released as ${r.release.type}`);
  if (r.mpc) facts.push("MPC project");
  facts.push(when(j.created_at));
  $("#job-facts").replaceChildren(...facts.map((f) => el("span", { text: f })));
  $("#job-error").hidden = !j.error;
  $("#job-error").textContent = j.error || "";

  $("#job-steps").replaceChildren(...j.steps.map((s) => {
    const pct = s.total ? Math.min(100, Math.round((100 * s.done) / s.total)) : null;
    return el("li", { class: s.status },
      el("span", { class: "dot" }),
      el("span", { class: "step-label" }, el("span", { class: "stage-tag", text: s.stage === "song" ? "song" : "release" }), s.label),
      el("span", { class: "step-meta", text: s.status === "running" ? (pct !== null ? `${pct}%` : s.detail || "working…") : s.status === "skipped" ? "skipped" : "" }),
      s.status === "running" ? el("div", { class: `bar${pct === null ? " indeterminate" : ""}` }, el("div", { class: "bar-fill", style: pct !== null ? `width:${pct}%` : null })) : null);
  }));

  const running = j.state === "running" || j.state === "queued";
  $("#job-cancel").hidden = !running;
  $("#job-delete").hidden = running;
  $("#job-zip").hidden = !j.files.length;
  $("#job-zip").href = `/api/jobs/${j.id}/zip`;
  $("#job-commands").textContent = (j.commands || []).map((c) => `# ${c.label}\n${c.command}`).join("\n\n");

  // results: only rebuild when the file list changes (players keep playing)
  const key = JSON.stringify(j.files.map((f) => [f.name, f.size]));
  if (key !== renderedFiles) {
    renderedFiles = key;
    renderResults(j);
  }
  if (!running) refreshJobs();
}

const KIND_LABEL = { song: "Song", settings: "Settings (seed + every value)", master: "Master", clip: "Short / Reel clip",
  video_16x9: "YouTube video (16:9)", video_9x16: "Short / Reel video (9:16)", video_1x1: "Instagram feed video (1:1)",
  captions: "Captions", analysis: "Analysis", mpc: "MPC project (zip)", midi: "MIDI file" };

function renderResults(j) {
  const box = $("#job-results");
  box.replaceChildren();
  const groups = [["song", "Song"], ["release", "Masters & videos"]];
  for (const [stage, title] of groups) {
    const files = j.files.filter((f) => f.stage === stage);
    if (!files.length) continue;
    const list = el("div", { class: "result-list" });
    for (const f of files) {
      const url = `/api/jobs/${j.id}/files/${f.index}`;
      const ext = f.name.split(".").pop().toLowerCase();
      const media = ["wav", "mp3", "flac"].includes(ext) ? el("audio", { controls: true, preload: "none", src: `${url}?inline=1` })
        : ext === "mp4" ? el("video", { controls: true, preload: "metadata", src: `${url}?inline=1`, class: f.kind === "video_9x16" ? "portrait" : null })
        : ["png", "jpg"].includes(ext) ? el("img", { src: `${url}?inline=1`, alt: f.name })
        : ext === "txt" ? textPreview(url) : null;
      list.append(el("div", { class: "result" },
        el("div", { class: "result-top" },
          el("span", { class: "result-name", text: f.name }),
          el("span", { class: "badge", text: KIND_LABEL[f.kind] || f.kind }),
          f.format ? el("span", { class: "badge", text: f.format }) : null,
          el("span", { class: "muted small", text: size(f.size) }),
          el("a", { class: "btn small", href: url, download: f.name, text: "Download" })),
        media));
    }
    box.append(el("section", { class: "card result-group" }, el("h2", { text: title }), list));
  }
}
function textPreview(url) {
  const pre = el("pre", { text: "…" });
  fetch(`${url}?inline=1`).then((r) => r.text()).then((t) => { pre.textContent = t; }).catch(() => {});
  return pre;
}

$("#job-cancel").addEventListener("click", () => viewJob && api(`/api/jobs/${viewJob.id}/cancel`, { method: "POST" }).catch((e) => banner(e.message)));
$("#job-delete").addEventListener("click", async () => {
  if (!viewJob || !confirm(`Delete "${viewJob.title}" and its files?`)) return;
  try { await api(`/api/jobs/${viewJob.id}`, { method: "DELETE" }); location.hash = "#/"; refreshJobs(); } catch (e) { banner(e.message); }
});

// load a job's request back into the form
function loadRequest(req, { newSeed }) {
  form.preset = presets[req.preset] ? req.preset : form.preset;
  form.title = req.title || "";
  form.overrides = { ...(req.values || {}) };
  if (newSeed) delete form.overrides.seed;
  if (req.release && REL) {
    form.release = { on: true, type: req.release.type, outputs: [...req.release.outputs], formats: [...(req.release.formats || [])],
      values: { ...req.release.values } };
    if (form.title && form.release.values.title === form.title) delete form.release.values.title;
  } else {
    form.release.on = false;
  }
  form.mpc = !!req.mpc;
  form.midi = !!req.midi;
  $("#export-mpc").checked = form.mpc;
  $("#export-midi").checked = form.midi;
  location.hash = "#/";
  renderPresets();
  renderCompose();
  renderRelease();
}
$("#job-edit").addEventListener("click", () => viewJob && loadRequest(viewJob.request, { newSeed: false }));
$("#job-again").addEventListener("click", async () => {
  if (!viewJob) return;
  const req = structuredClone(viewJob.request);
  delete req.values.seed;
  try {
    const job = await api("/api/jobs", { method: "POST", body: req });
    location.hash = `#/job/${job.id}`;
    refreshJobs();
  } catch (e) { banner(e.message); }
});

// ------------------------------------------------------------------ routing
function route() {
  const m = location.hash.match(/^#\/job\/([0-9a-f]{20})$/);
  if (m) { openJob(m[1]); return; }
  if (es) { es.close(); es = null; }
  renderedFiles = "";
  $("#jobview").hidden = true;
  $("#wizard").hidden = false;
}
window.addEventListener("hashchange", route);

// ------------------------------------------------------------------ start
async function start() {
  try {
    caps = await api("/api/capabilities");
  } catch (e) {
    banner("Couldn't load settings: " + e.message);
    return;
  }
  for (const p of caps.compose.params) P[p.path] = p;
  for (const p of caps.compose.presets) presets[p.name] = p;
  for (const p of caps.compose.patches) patchValues[p.name] = p.values;
  REL = caps.release.available ? caps.release.caps : null;
  if (REL) {
    form.release.outputs = REL.pipeline.outputs.filter((o) => o.default && o.available !== false).map((o) => o.id);
    form.release.formats = ["wav24", "mp3"].filter((f) => REL.formats.some((x) => x.id === f && x.available));
  }
  const first = presets.cathedral_majestic ? "cathedral_majestic" : caps.compose.presets[0].name;
  choosePreset(first);
  route();
  refreshJobs();
  setInterval(refreshJobs, 8000);
}
start();
