const form = document.getElementById("rack");
const el = (id) => document.getElementById(id);
const STORE = "swarmscope:settings";
const HOSTS = "swarmscope:hosts";
const LAST_RUN = "swarmscope:last-run";
const SAVED = ["host", "paths", "user_count", "spawn_rate", "run_time", "delay_min", "delay_max", "max_retries", "backoff"];
const SWARM = ["user_count", "spawn_rate", "host", "paths", "delay_min", "delay_max", "max_retries", "backoff"];
const ACTIVE = new Set(["spawning", "running"]);
const STOPPING = new Set(["stopping", "cleanup"]);
const STATE_TEXT = { ready: "Idle", spawning: "Ramping", running: "Running", stopping: "Stopping", cleanup: "Stopping", stopped: "Stopped", missing: "Idle", offline: "Engine offline" };
const MAX_POINTS = 600;
const RETRY_RE = /retry \d+\/\d+/;

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const whole = new Intl.NumberFormat("en");
let history = [];
let state = "ready";
let startedAt = null;
let elapsedAtStop = 0;
let run = null;
let firstThrottleIndex = null;
let launchGraceUntil = 0;
let shifted = 0;
let lastUsers = 0;
let probes = [];
let probing = false;
let probeTimer = null;
let launching = false;
const PROBE_EVERY_MS = 15000;

const store = {
  get(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch {}
  },
};

function bytes(n) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n < 10 && i ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
}

function mmss(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** Writes a number in the dot-matrix face, with decimals and unit set in the text face so "." never reads as "+". */
function dots(node, text, unit = "") {
  const [int, frac] = String(text).split(".");
  node.replaceChildren(int);
  if (frac !== undefined) node.append(Object.assign(document.createElement("span"), { className: "frac", textContent: `.${frac}` }));
  if (unit) node.append(Object.assign(document.createElement("span"), { className: "unit", textContent: unit }));
}

function seconds(text) {
  const value = text.trim();
  if (!value) return null;
  if (/^\d+$/.test(value)) return Number(value);
  const match = /^(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?$/i.exec(value);
  return match && (match[1] || match[2] || match[3]) ? (+match[1] || 0) * 3600 + (+match[2] || 0) * 60 + (+match[3] || 0) : NaN;
}

function num(name) {
  const value = parseFloat(form.elements[name].value);
  return Number.isFinite(value) ? value : 0;
}

function estimate() {
  const users = Math.max(num("user_count"), 0);
  const ramp = Math.max(num("spawn_rate"), 0.1);
  const pause = Math.max((num("delay_min") + num("delay_max")) / 2, 0) + 0.15;
  const rate = users / pause;
  const retries = Math.max(Math.round(num("max_retries")), 0);
  const duration = seconds(form.elements.run_time.value);
  const rampTime = users / ramp;
  const box = el("estimate");

  el("e-rate").textContent = `about ${compact.format(rate)} req/s at peak`;
  if (duration == null || Number.isNaN(duration)) {
    box.dataset.level = "open";
    el("e-total").replaceChildren(`about ${compact.format(Math.round(rate * 60))} per minute until you press Stop`);
    return;
  }
  box.dataset.level = "";
  const total = duration <= rampTime ? (ramp * duration * duration) / 2 / pause : rate * (duration - rampTime / 2);
  const worst = total * (1 + retries);
  el("e-total").replaceChildren(`about ${compact.format(Math.round(total))}`);
  if (retries) {
    el("e-total").append(" ", Object.assign(document.createElement("small"), { textContent: `(up to ${compact.format(worst)} if every request retries)` }));
  }
}

function validate() {
  const host = form.elements.host.value.trim().replace(/\/+$/, "");
  if (!/^https?:\/\/[^\s/]+/i.test(host)) return ["host", "Enter a full URL, like https://yoursite.com"];
  const users = num("user_count");
  if (users < 1 || !Number.isInteger(users)) return ["user_count", "Users must be a whole number, 1 or more."];
  if (num("spawn_rate") <= 0) return ["spawn_rate", "Ramp must be above 0 users per second."];
  if (Number.isNaN(seconds(form.elements.run_time.value))) return ["run_time", "Duration looks like 90s, 5m or 1h30m. Leave it empty to run until you press Stop."];
  if (num("delay_min") > num("delay_max")) return ["delay_max", "Delay max must be at least delay min."];
  const retries = num("max_retries");
  if (retries < 0 || retries > 10 || !Number.isInteger(retries)) return ["max_retries", "Retries must be a whole number from 0 to 10."];
  if (num("backoff") < 0) return ["backoff", "Backoff can't be negative."];
  return null;
}

function clearError() {
  el("error").textContent = "";
  for (const input of form.querySelectorAll("[aria-invalid]")) {
    input.removeAttribute("aria-invalid");
    const described = (input.getAttribute("aria-describedby") || "").replace("error", "").trim();
    described ? input.setAttribute("aria-describedby", described) : input.removeAttribute("aria-describedby");
  }
}

function showError(name, message, focus = true) {
  clearError();
  el("error").textContent = message;
  if (!name) return;
  const input = form.elements[name];
  input.setAttribute("aria-invalid", "true");
  input.setAttribute("aria-describedby", `${input.getAttribute("aria-describedby") || ""} error`.trim());
  if (focus) {
    input.focus({ preventScroll: true });
    input.scrollIntoView({ block: "center", behavior: "smooth" });
  }
}

function restore() {
  const saved = store.get(STORE, {});
  for (const name of SAVED) if (saved[name] != null) form.elements[name].value = saved[name];
  if (saved.probe != null) form.elements.probe.checked = saved.probe;
  renderHosts();
  showTarget(saved.host);
  elapsedAtStop = (store.get(LAST_RUN, null)?.seconds || 0) * 1000;
}

function persist() {
  store.set(STORE, { ...Object.fromEntries(SAVED.map((n) => [n, form.elements[n].value])), probe: form.elements.probe.checked });
}

function renderHosts() {
  el("recent-hosts").replaceChildren(...store.get(HOSTS, []).map((value) => Object.assign(document.createElement("option"), { value })));
}

function rememberHost(host) {
  store.set(HOSTS, [host, ...store.get(HOSTS, []).filter((h) => h !== host)].slice(0, 6));
  renderHosts();
}

function showTarget(host) {
  const target = el("target");
  if (!host) { target.textContent = "No target set"; return; }
  target.replaceChildren("Target ", Object.assign(document.createElement("b"), { textContent: host }));
}

function syncPresets() {
  for (const button of document.querySelectorAll(".presets .chip-btn")) {
    const on = form.elements.user_count.value === button.dataset.users && form.elements.spawn_rate.value === button.dataset.ramp;
    button.setAttribute("aria-pressed", String(on));
  }
}

function clock() {
  el("clock").textContent = mmss((startedAt ? Date.now() - startedAt : elapsedAtStop) / 1000);
}

function setState(next) {
  const wasActive = ACTIVE.has(state);
  if (ACTIVE.has(next) && !startedAt) startedAt = Date.now();
  if (!ACTIVE.has(next) && startedAt) { elapsedAtStop = Date.now() - startedAt; startedAt = null; }
  state = next;
  el("led").dataset.state = next;
  el("state").textContent = STATE_TEXT[next] || next;

  const running = ACTIVE.has(next);
  const stopping = STOPPING.has(next);
  el("controls").disabled = running || stopping;
  el("bar-stop").hidden = !running;
  const fire = el("fire");
  fire.disabled = stopping || launching;
  fire.dataset.mode = running ? "stop" : "start";
  fire.textContent = launching ? "Starting" : stopping ? "Stopping" : running ? "Stop" : "Start";
  if (launching && !running) el("state").textContent = form.elements.probe.checked ? "Measuring baseline" : "Starting";
  el("readouts").dataset.live = String(running);
  if (wasActive && !running && run) finishRun();
}

function statusOf(message) {
  const match = /(?:HTTP |code=)(\d{3})/.exec(message);
  return match ? Number(match[1]) : 0;
}

function cleanError(message) {
  return message
    .replace(/^CatchResponseError\('(.*)'\)$/, "$1")
    .replace(/^LocustBadStatusCode\(code=(\d+)\)$/, "HTTP $1, gave up")
    .replace(/^(\w+Error)\((.*)\)$/, "$1: $2");
}

/** Splits Locust's error table into retried attempts, throttling and hard failures. */
function pushback(errors) {
  const out = { retries: 0, throttled: 0, broken: 0 };
  for (const e of errors) {
    const code = statusOf(e.error);
    if (RETRY_RE.test(e.error)) out.retries += e.occurrences;
    if (code === 429) out.throttled += e.occurrences;
    else if (code === 0 || code >= 500) out.broken += e.occurrences;
  }
  return out;
}

function errorRate(total, retries) {
  const attempts = total.num_requests || 0;
  const logical = Math.max(attempts - retries, 0);
  const finalFails = Math.max((total.num_failures || 0) - retries, 0);
  return { logical, finalFails, pct: logical ? (finalFails / logical) * 100 : 0 };
}

function pct(value) {
  if (value > 0 && value < 1) return value.toFixed(1);
  return value >= 10 ? String(Math.round(value)) : value.toFixed(1);
}

function renderPushback(errors, counts) {
  const set = (id, value, cls) => { dots(el(id), whole.format(value)); el(id).className = value ? cls : ""; };
  set("p-retries", counts.retries, "warn");
  set("p-429", counts.throttled, "bad");
  set("p-5xx", counts.broken, "bad");

  const alert = el("throttle-alert");
  if (ACTIVE.has(state) && counts.throttled && run?.first429 != null) {
    alert.replaceChildren(Object.assign(document.createElement("b"), { textContent: "The host is throttling you" }), ` since ${mmss(run.first429)}. From here on you're measuring its rate limit, not your site.`);
    alert.hidden = false;
  } else {
    alert.hidden = true;
  }

  const top = [...errors].sort((a, b) => b.occurrences - a.occurrences).slice(0, 12);
  el("errors").replaceChildren(...(top.length ? top.map((e) => {
    const li = document.createElement("li");
    const text = document.createElement("span");
    text.append(Object.assign(document.createElement("code"), { textContent: e.name }), cleanError(e.error));
    li.append(Object.assign(document.createElement("b"), { textContent: compact.format(e.occurrences) }), text);
    return li;
  }) : [Object.assign(document.createElement("li"), { className: "calm", textContent: "No errors yet." })]));
}

function renderRows(stats) {
  const rows = stats.filter((s) => s.name !== "Aggregated");
  if (!rows.length) {
    el("rows").replaceChildren(Object.assign(document.createElement("tr"), { className: "empty", innerHTML: '<td colspan="7">Waiting for traffic.</td>' }));
    return;
  }
  el("rows").replaceChildren(...rows.map((s) => {
    const tr = document.createElement("tr");
    const cells = [s.name, whole.format(s.num_requests), whole.format(s.num_failures), Math.round(s.median_response_time || 0), Math.round(s["response_time_percentile_0.95"] || 0), Math.round(s.max_response_time || 0), bytes(s.avg_content_length || 0)];
    cells.forEach((value, i) => {
      const td = document.createElement("td");
      td.textContent = value;
      if (i === 0) td.title = value;
      if (i === 2 && s.num_failures) td.className = "bad";
      tr.append(td);
    });
    return tr;
  }));
}

function trackRun(data, counts, err, p95) {
  if (!run) return;
  const users = data.user_count || 0;
  const rps = data.current_rps || 0;
  if (users > run.peakUsers || (users === run.peakUsers && p95 > run.p95AtPeak)) { run.peakUsers = users; run.p95AtPeak = p95; }
  run.peakRps = Math.max(run.peakRps, rps);
  run.maxP95 = Math.max(run.maxP95, p95);
  if (counts.throttled && run.first429 == null) {
    run.first429 = (Date.now() - run.startedAt) / 1000;
    firstThrottleIndex = history.length - 1;
  }
  run.requests = err.logical;
  run.attempts = (data.stats.at(-1) || {}).num_requests || 0;
  run.errPct = err.pct;
  run.throttled = counts.throttled;
  run.retries = counts.retries;
}

/** The page-load number we headline: LCP when the browser reports one, else the load event. */
function pageTime(probe) {
  return probe.lcp ?? probe.load;
}

function secs(ms) {
  return ms == null ? "n/a" : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`;
}

function probeTarget() {
  const host = form.elements.host.value.trim().replace(/\/+$/, "");
  const first = form.elements.paths.value.replace(/\n/g, ",").split(",").map((p) => p.trim()).find(Boolean) || "/";
  return host + "/" + first.replace(/^\/+/, "");
}

async function probeOnce(users) {
  if (probing) return null;
  probing = true;
  const url = probeTarget();
  const at = startedAt ? (Date.now() - startedAt) / 1000 : 0;
  const seq = history.length + shifted;
  try {
    const response = await fetch("ui/probe", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }) });
    const result = await response.json();
    const entry = { ...result, url, at, users, seq };
    if (result.ok || response.status !== 409) probes.push(entry);
    renderProbes();
    return entry;
  } catch (error) {
    const entry = { ok: false, error: `Page probe failed: ${error.message}`, url, at, users, seq };
    probes.push(entry);
    renderProbes();
    return entry;
  } finally {
    probing = false;
  }
}

function startProbing() {
  stopProbing();
  probeTimer = setInterval(() => { if (ACTIVE.has(state)) probeOnce(lastUsers); }, PROBE_EVERY_MS);
}

function stopProbing() {
  clearInterval(probeTimer);
  probeTimer = null;
}

function renderProbes() {
  const ok = probes.filter((p) => p.ok);
  const baseline = ok[0];
  const latest = ok.at(-1);
  if (latest) {
    const ms = pageTime(latest);
    ms < 1000 ? dots(el("r-page"), String(Math.round(ms)), "ms") : dots(el("r-page"), (ms / 1000).toFixed(1), "s");
    el("r-page").classList.toggle("hot", ms > 2500 || (baseline && ms > pageTime(baseline) * 2));
  }
  const failed = probes.filter((p) => !p.ok).at(-1);
  el("probe-note").textContent = failed && !latest ? failed.error : `One fresh-cache visit to ${probeTarget()} every 15 s while the crowd runs. The first row is the baseline, measured before any load.`;

  if (!probes.length) {
    el("probe-rows").replaceChildren(Object.assign(document.createElement("tr"), { className: "empty", innerHTML: '<td colspan="8">No page loads measured yet.</td>' }));
    return;
  }
  el("probe-rows").replaceChildren(...[...probes].reverse().map((p) => {
    const tr = document.createElement("tr");
    if (!p.ok) {
      tr.className = "empty";
      tr.append(Object.assign(document.createElement("td"), { textContent: mmss(p.at) }), Object.assign(document.createElement("td"), { colSpan: 7, textContent: p.error }));
      return tr;
    }
    const slow = baseline && p !== baseline && pageTime(p) > pageTime(baseline) * 2;
    const cells = [p === baseline ? "baseline" : mmss(p.at), whole.format(p.users), secs(p.ttfb), secs(p.fcp), secs(p.lcp), secs(p.load), bytes(p.bytes || 0), p.requests];
    cells.forEach((value, i) => {
      const td = document.createElement("td");
      td.textContent = value;
      if (i === 4 && slow) td.className = "bad";
      tr.append(td);
    });
    return tr;
  }));
}

function pageSummary() {
  const ok = probes.filter((p) => p.ok);
  if (!ok.length) return {};
  const peak = ok.slice(1).reduce((best, p) => (!best || p.users > best.users || (p.users === best.users && pageTime(p) > pageTime(best)) ? p : best), null);
  return { pageAlone: pageTime(ok[0]), pagePeak: peak ? pageTime(peak) : null, pagePeakUsers: peak ? peak.users : null };
}

function finishRun() {
  stopProbing();
  Object.assign(run, pageSummary());
  run.seconds = (Date.now() - run.startedAt) / 1000;
  const previous = store.get(LAST_RUN, null);
  store.set(LAST_RUN, run);
  showVerdict(run, previous);
  run = null;
}

function showVerdict(summary, previous) {
  const stats = [
    ["Users held", whole.format(summary.peakUsers)],
    ["Peak rate", `${compact.format(summary.peakRps)} req/s`],
    ["P95 at peak", `${Math.round(summary.p95AtPeak)} ms`],
    ["Worst p95", `${Math.round(summary.maxP95)} ms`],
    ["Errors after retries", `${pct(summary.errPct)}%`, summary.errPct >= 1],
    ["First 429", summary.first429 == null ? "none" : `at ${mmss(summary.first429)}`, summary.first429 != null],
  ];
  if (summary.pageAlone != null) {
    const slow = summary.pagePeak != null && (summary.pagePeak > 2500 || summary.pagePeak > summary.pageAlone * 2);
    stats.push(["Page LCP alone", secs(summary.pageAlone)]);
    stats.push([summary.pagePeakUsers != null ? `Page LCP at ${whole.format(summary.pagePeakUsers)} users` : "Page LCP under load", secs(summary.pagePeak), slow]);
  }
  el("verdict-stats").replaceChildren(...stats.flatMap(([label, value, bad]) => [
    Object.assign(document.createElement("dt"), { textContent: label }),
    Object.assign(document.createElement("dd"), { textContent: value, className: bad ? "bad" : "" }),
  ]));

  const slowed = summary.pageAlone != null && summary.pagePeak != null && (summary.pagePeak > 2500 || summary.pagePeak > summary.pageAlone * 2);
  el("verdict-title").textContent =
    summary.first429 != null ? "Throttled by the host"
    : summary.errPct >= 1 ? "Errors under load"
    : slowed ? "Slowed down under load"
    : "Held up";
  const pageLine = summary.pageAlone != null && summary.pagePeak != null ? ` The page took ${secs(summary.pageAlone)} to show alone and ${secs(summary.pagePeak)} under load.` : "";
  el("verdict-lede").textContent = `${whole.format(summary.requests)} requests in ${mmss(summary.seconds)} against ${summary.host}.${pageLine}`;

  const prev = el("verdict-prev");
  if (previous && previous.peakUsers) {
    prev.textContent = `Previous run: ${whole.format(previous.peakUsers)} users, p95 ${Math.round(previous.p95AtPeak)} ms at peak, ${pct(previous.errPct)}% errors${previous.first429 == null ? "" : `, first 429 at ${mmss(previous.first429)}`}.`;
    prev.hidden = false;
  } else {
    prev.hidden = true;
  }
  el("verdict").hidden = false;
  el("trace").setAttribute("aria-label", `${el("verdict-title").textContent}. ${stats.map(([l, v]) => `${l} ${v}`).join(", ")}.`);
}

const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
const SWEEP_MS = 2600;
let idleFrame = null;

/** A free-running scope with no input: a faint noisy baseline and a sweeping beam. */
function drawIdle(pad, pw, ph, now) {
  const mid = pad.t + ph / 2;
  const still = reduceMotion.matches;
  const t = still ? 0 : now;
  const head = still ? 1 : (t % SWEEP_MS) / SWEEP_MS;
  const noise = (x) => Math.sin(x * 0.09 + t * 0.004) * 1.4 + Math.sin(x * 0.023 - t * 0.0021) * 2.2 + Math.sin(x * 0.31 + t * 0.011) * 0.6;
  const amber = color("--amber");

  ctx.save();
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = "rgba(255,178,56,.16)";
  ctx.beginPath();
  for (let x = 0; x <= pw; x += 3) {
    const y = mid + noise(x);
    x ? ctx.lineTo(pad.l + x, y) : ctx.moveTo(pad.l, y);
  }
  ctx.stroke();

  const headX = head * pw;
  const tail = Math.min(pw * 0.3, headX);
  const glow = ctx.createLinearGradient(pad.l + headX - tail, 0, pad.l + headX, 0);
  glow.addColorStop(0, "rgba(255,178,56,0)");
  glow.addColorStop(1, amber);
  ctx.strokeStyle = glow;
  ctx.lineWidth = 2;
  ctx.shadowColor = amber;
  ctx.shadowBlur = 10;
  ctx.beginPath();
  for (let x = headX - tail; x <= headX; x += 2) {
    const y = mid + noise(x);
    x === headX - tail ? ctx.moveTo(pad.l + x, y) : ctx.lineTo(pad.l + x, y);
  }
  ctx.stroke();

  ctx.fillStyle = amber;
  ctx.shadowBlur = 14;
  ctx.beginPath();
  ctx.arc(pad.l + headX, mid + noise(headX), 2.5, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

let screenOnView = true;
new IntersectionObserver(([entry]) => { screenOnView = entry.isIntersecting; }).observe(document.querySelector(".screen"));

/** Redraws the idle sweep while the screen is in view. */
function idleLoop(now) {
  idleFrame = null;
  if (history.length || ACTIVE.has(state)) return;
  if (screenOnView) draw(now);
  if (!reduceMotion.matches) idleFrame = requestAnimationFrame(idleLoop);
}

function startIdle() {
  if (idleFrame == null && !history.length && !ACTIVE.has(state)) idleFrame = requestAnimationFrame(idleLoop);
}

function render(data) {
  if (Date.now() < launchGraceUntil && !ACTIVE.has(data.state) && !STOPPING.has(data.state)) return;
  setState(data.state);
  const stats = data.stats || [];
  const total = stats.at(-1) || {};
  const live = ACTIVE.has(data.state);
  const errors = data.errors || [];
  const counts = pushback(errors);
  const err = errorRate(total, counts.retries);
  const p95 = data.current_response_time_percentiles?.["response_time_percentile_0.95"] || 0;
  const rps = live ? data.current_rps || 0 : 0;

  if (live) {
    lastUsers = data.user_count || 0;
    history.push({ rps, fail: data.current_fail_per_sec || 0, p95 });
    if (history.length > MAX_POINTS) {
      history.shift();
      shifted++;
      if (firstThrottleIndex != null) firstThrottleIndex--;
    }
    trackRun(data, counts, err, p95);
  }

  dots(el("r-rps"), rps >= 100 ? String(Math.round(rps)) : rps.toFixed(1));
  dots(el("r-users"), whole.format(live ? data.user_count || 0 : 0));
  dots(el("r-p95"), String(Math.round(live ? p95 : 0)), "ms");
  dots(el("r-err"), pct(err.pct), "%");
  el("r-err").classList.toggle("hot", err.pct >= 1);
  dots(el("r-429"), whole.format(counts.throttled));
  el("r-429").classList.toggle("hot", counts.throttled > 0);

  dots(el("s-reqs"), whole.format(err.logical));
  dots(el("s-fails"), whole.format(err.finalFails));
  dots(el("s-median"), String(Math.round(total.median_response_time || 0)), "ms");
  const [size, unit] = bytes((total.num_requests || 0) * (total.avg_content_length || 0)).split(" ");
  dots(el("s-bytes"), size, unit);

  renderRows(stats);
  renderPushback(errors, counts);

  const hasData = (total.num_requests || 0) > 0;
  el("nosignal").hidden = live || hasData || history.length > 0;
  if (live) el("verdict").hidden = true;
  if (!live && hasData && el("verdict").hidden && !run) {
    const last = store.get(LAST_RUN, null);
    if (last) showVerdict(last, null);
  }
  draw();
  startIdle();
}

function niceMax(value) {
  const exp = Math.pow(10, Math.floor(Math.log10(value)));
  const f = value / exp;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * exp;
}

const canvas = el("trace");
const ctx = canvas.getContext("2d");
const css = getComputedStyle(document.documentElement);
const color = (name) => css.getPropertyValue(name).trim();

function draw(now = performance.now()) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const pad = { t: 34, r: 18, b: 44, l: 18 };
  const pw = w - pad.l - pad.r;
  const ph = h - pad.t - pad.b;
  const cols = 10, rows = 6;

  ctx.lineWidth = 1;
  for (let i = 0; i <= cols; i++) {
    const x = Math.round(pad.l + (pw * i) / cols) + 0.5;
    ctx.strokeStyle = i === cols / 2 ? "rgba(235,227,212,.09)" : "rgba(235,227,212,.045)";
    ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, pad.t + ph); ctx.stroke();
  }
  for (let j = 0; j <= rows; j++) {
    const y = Math.round(pad.t + (ph * j) / rows) + 0.5;
    ctx.strokeStyle = j === rows / 2 ? "rgba(235,227,212,.09)" : "rgba(235,227,212,.045)";
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(pad.l + pw, y); ctx.stroke();
  }

  const span = Math.max(60, history.length);
  const maxRate = niceMax(Math.max(1, ...history.map((p) => Math.max(p.rps, p.fail))));
  const plotted = probes.filter((p) => p.ok && p.seq - shifted >= 0);
  const maxMs = niceMax(Math.max(10, ...history.map((p) => p.p95), ...plotted.map(pageTime)));

  ctx.font = `400 11px ${color("--mono")}`;
  ctx.textBaseline = "middle";
  ctx.fillStyle = color("--amber");
  ctx.textAlign = "left";
  ctx.fillText(`${compact.format(maxRate)} req/s`, pad.l, pad.t - 16);
  ctx.fillStyle = color("--cyan");
  ctx.textAlign = "right";
  ctx.fillText(`${compact.format(maxMs)} ms`, pad.l + pw, pad.t - 16);
  if (pw > 420) {
    ctx.fillStyle = color("--dim");
    ctx.textAlign = "center";
    ctx.fillText(`last ${mmss(span)}`, pad.l + pw / 2, pad.t - 16);
  }

  if (!history.length) {
    drawIdle(pad, pw, ph, now);
    return;
  }

  const step = pw / (span - 1);
  const xAt = (i) => pad.l + (i + (span - history.length)) * step;
  const yAt = (value, max) => pad.t + ph - (Math.min(value, max) / max) * ph;

  const trace = (pick, max, stroke, { dash = [], glow = 0, fill = false } = {}) => {
    ctx.save();
    const path = new Path2D();
    history.forEach((p, i) => (i ? path.lineTo(xAt(i), yAt(pick(p), max)) : path.moveTo(xAt(i), yAt(pick(p), max))));
    if (fill && history.length > 1) {
      const area = new Path2D(path);
      area.lineTo(xAt(history.length - 1), pad.t + ph);
      area.lineTo(xAt(0), pad.t + ph);
      const grad = ctx.createLinearGradient(0, pad.t, 0, pad.t + ph);
      grad.addColorStop(0, "rgba(255,178,56,.18)");
      grad.addColorStop(1, "rgba(255,178,56,0)");
      ctx.fillStyle = grad;
      ctx.fill(area);
    }
    ctx.setLineDash(dash);
    ctx.strokeStyle = stroke;
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.shadowColor = stroke;
    ctx.shadowBlur = glow;
    ctx.stroke(path);
    ctx.restore();
  };

  if (firstThrottleIndex != null && firstThrottleIndex >= 0) {
    const x = Math.round(xAt(firstThrottleIndex)) + 0.5;
    ctx.save();
    ctx.setLineDash([3, 4]);
    ctx.strokeStyle = color("--red");
    ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, pad.t + ph); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = color("--red");
    ctx.textAlign = x > pad.l + pw - 60 ? "right" : "left";
    ctx.fillText("first 429", x + (ctx.textAlign === "left" ? 6 : -6), pad.t + 12);
    ctx.restore();
  }

  trace((p) => p.p95, maxMs, color("--cyan"), { dash: [5, 4] });

  if (plotted.length) {
    ctx.save();
    ctx.strokeStyle = color("--green");
    ctx.fillStyle = color("--green");
    ctx.lineWidth = 2;
    ctx.beginPath();
    plotted.forEach((p, i) => {
      const x = xAt(Math.min(p.seq - shifted, history.length - 1));
      const y = yAt(pageTime(p), maxMs);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
    for (const p of plotted) {
      ctx.beginPath();
      ctx.arc(xAt(Math.min(p.seq - shifted, history.length - 1)), yAt(pageTime(p), maxMs), 4, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }
  trace((p) => p.fail, maxRate, color("--red"), { glow: 6 });
  trace((p) => p.rps, maxRate, color("--amber"), { glow: 10, fill: true });

  const last = history.at(-1);
  ctx.fillStyle = color("--amber");
  ctx.shadowColor = color("--amber");
  ctx.shadowBlur = 12;
  ctx.beginPath(); ctx.arc(xAt(history.length - 1), yAt(last.rps, maxRate), 3, 0, Math.PI * 2); ctx.fill();
  ctx.shadowBlur = 0;
}

async function poll() {
  try {
    const response = await fetch("stats/requests", { cache: "no-store" });
    if (!response.ok) throw new Error(response.statusText);
    render(await response.json());
  } catch {
    setState("offline");
  }
}

async function start() {
  if (launching || ACTIVE.has(state) || STOPPING.has(state)) return;
  const problem = validate();
  if (problem) { showError(...problem); return; }
  clearError();
  const host = form.elements.host.value.trim().replace(/\/+$/, "");
  form.elements.host.value = host;
  persist();
  rememberHost(host);

  const body = new URLSearchParams();
  for (const name of SWARM) body.set(name, form.elements[name].value.trim());
  const runTime = form.elements.run_time.value.trim();
  if (runTime) body.set("run_time", runTime);

  const fire = el("fire");
  launching = true;
  setState(state);
  probes = [];
  shifted = 0;
  history = [];
  renderProbes();
  try {
    if (form.elements.probe.checked) {
      const baseline = await probeOnce(0);
      if (!baseline?.ok) {
        showError(null, `${baseline?.error || "Baseline page load failed."} Starting without page load checks.`);
      }
    }
    await fetch("stats/reset", { method: "POST" });
    const response = await fetch("swarm", { method: "POST", body });
    const result = await response.json();
    if (!result.success) { showError(null, result.message); return; }
    firstThrottleIndex = null;
    elapsedAtStop = 0;
    startedAt = Date.now();
    run = { host, startedAt, peakUsers: 0, p95AtPeak: 0, peakRps: 0, maxP95: 0, first429: null, requests: 0, attempts: 0, errPct: 0, throttled: 0, retries: 0 };
    el("verdict").hidden = true;
    showTarget(host);
    launchGraceUntil = Date.now() + 4000;
    setState("spawning");
    if (probes.some((p) => p.ok)) {
      probes = probes.map((p) => ({ ...p, seq: 0 }));
      startProbing();
    }
    poll();
  } catch (error) {
    showError(null, `Could not reach the load engine: ${error.message}. Close Swarmscope and open it again.`);
  } finally {
    launching = false;
    setState(state);
  }
}

async function stop() {
  setState("stopping");
  try {
    await fetch("stop", { method: "POST" });
  } finally {
    poll();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  ACTIVE.has(state) ? stop() : start();
});

form.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.target.tagName === "TEXTAREA" || event.target.tagName === "BUTTON") return;
  if (!(event.metaKey || event.ctrlKey)) event.preventDefault();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && ACTIVE.has(state)) { event.preventDefault(); stop(); }
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && !ACTIVE.has(state) && !STOPPING.has(state)) { event.preventDefault(); start(); }
});

form.addEventListener("input", (event) => {
  estimate();
  persist();
  syncPresets();
  if (event.target.getAttribute("aria-invalid") === "true") {
    const problem = validate();
    if (!problem || problem[0] !== event.target.name) clearError();
  }
  if (event.target.name === "run_time" && Number.isNaN(seconds(event.target.value))) {
    showError("run_time", "Duration looks like 90s, 5m or 1h30m. Leave it empty to run until you press Stop.", false);
  }
});

for (const button of document.querySelectorAll(".presets .chip-btn")) {
  button.addEventListener("click", () => {
    form.elements.user_count.value = button.dataset.users;
    form.elements.spawn_rate.value = button.dataset.ramp;
    form.dispatchEvent(new Event("input"));
  });
}

el("bar-stop").addEventListener("click", stop);
el("mod-key").textContent = /Mac|iPhone|iPad/.test(navigator.platform) ? "Cmd" : "Ctrl";
const accessToken = document.querySelector('meta[name="swarmscope-token"]').content;
for (const link of document.querySelectorAll(".links a")) {
  const url = new URL(link.getAttribute("href"), location.href);
  url.searchParams.set("token", accessToken);
  link.href = url.href;
}
new ResizeObserver(() => draw()).observe(canvas);

try {
  if (!sessionStorage.getItem("swarmscope:seen")) {
    document.body.classList.add("rise");
    sessionStorage.setItem("swarmscope:seen", "1");
  }
} catch {}

restore();
estimate();
syncPresets();
clock();
setInterval(clock, 500);
setInterval(poll, 1000);
poll();
startIdle();
reduceMotion.addEventListener("change", startIdle);
