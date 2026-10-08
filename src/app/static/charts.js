// Chart.js rendering for the data pages. Data comes from authenticated JSON endpoints or an embedded JSON block.
const PALETTE = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00"];

function lineChart(canvasId, labels, datasets, yTitle, xTitle, opts = {}) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  new Chart(ctx, {
    type: "line",
    data: { labels, datasets: datasets.map((d, i) => ({
      borderColor: PALETTE[i % PALETTE.length], backgroundColor: PALETTE[i % PALETTE.length],
      borderWidth: 1.5, pointRadius: labels.length > 200 ? 0 : 2, spanGaps: !!opts.spanGaps, tension: 0, ...d })) },
    options: { responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      scales: { y: { title: { display: true, text: yTitle } }, x: { title: { display: true, text: xTitle }, ticks: { maxTicksLimit: 12 } } },
      plugins: { legend: { display: datasets.length > 1 } } },
  });
}

function barChart(canvasId, labels, values, yTitle, xTitle, label, color = "#0072B2") {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  new Chart(ctx, {
    type: "bar",
    data: { labels, datasets: [{ label, data: values, backgroundColor: color }] },
    options: { responsive: true, maintainAspectRatio: false,
      scales: { y: { title: { display: true, text: yTitle }, beginAtZero: true }, x: { title: { display: true, text: xTitle }, ticks: { maxTicksLimit: 12 } } },
      plugins: { legend: { display: false } } },
  });
}

async function getJson(url) {
  const r = await fetch(url, { credentials: "same-origin" });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || "Request failed");
  return body;
}

function showError(msg) {
  const el = document.getElementById("chart-error");
  if (el) { el.textContent = msg; el.classList.remove("d-none"); }
}

// Align several {labels, values} series on one sorted label axis (missing = null).
function merge(seriesList) {
  const labels = [...new Set(seriesList.flatMap((s) => s.labels))].sort();
  return { labels, aligned: seriesList.map((s) => { const m = new Map(s.labels.map((l, i) => [l, s.values[i]])); return labels.map((l) => (m.has(l) ? m.get(l) : null)); }) };
}

async function initHistory(root) {
  try {
    const d = await getJson(root.dataset.api);
    const xTitle = d.granularity === "daily" ? "Date (UTC day)" : "Month";
    lineChart("c-temp", d.labels, [
      { label: "Mean (C)", data: d.temperature_mean_c },
      { label: "Lowest hourly value (C)", data: d.temperature_min_c, borderDash: [4, 3] },
      { label: "Highest hourly value (C)", data: d.temperature_max_c, borderDash: [4, 3] }], "Temperature at 2 m (C)", xTitle);
    barChart("c-rain", d.labels, d.precipitation_mm, "Precipitation (" + d.precipitation_unit + ")", xTitle, "Precipitation");
    lineChart("c-hum", d.labels, [{ label: "Relative humidity (%)", data: d.humidity_pct }], "Relative humidity at 2 m (%)", xTitle);
    lineChart("c-wind", d.labels, [{ label: "Wind speed (m/s)", data: d.wind_m_s }], "Wind speed at 2 m (m/s)", xTitle);
  } catch (e) { showError(e.message); }
}

async function initCompare(root) {
  try {
    const d = await getJson(root.dataset.api);
    const names = Object.keys(d.cities);
    const years = d.cities[names[0]].years;
    const series = (key) => names.map((n) => ({ label: n, data: d.cities[n][key] }));
    lineChart("c-temp", years, series("temperature_c"), "Annual mean temperature (C)", "Year");
    lineChart("c-hum", years, series("humidity_pct"), "Annual mean relative humidity (%)", "Year");
    lineChart("c-wind", years, series("wind_m_s"), "Annual mean wind speed (m/s)", "Year");
    lineChart("c-rain", years, series("precipitation_mm"), "Annual precipitation (mm/year, complete years)", "Year");
  } catch (e) { showError(e.message); }
}

async function initCurrent(root) {
  try {
    const d = await getJson(root.dataset.api);
    const note = document.getElementById("history-note");
    if (!d.weather.labels.length && !d.openaq_observed.labels.length) {
      note.textContent = "No readings stored for this city in the selected window yet. History accumulates as polling runs.";
      return;
    }
    lineChart("c-cur-temp", d.weather.labels.map((l) => l.slice(5, 16).replace("T", " ")), [{ label: "Temperature at 2 m (C), Open-Meteo modelled", data: d.weather.temperature_2m }], "Temperature (C)", "UTC time");
    const m = merge([{ labels: d.air_quality_modelled.labels, values: d.air_quality_modelled.pm2_5 },
                     { labels: d.openaq_observed.labels, values: d.openaq_observed.median_pm25 }]);
    lineChart("c-cur-pm", m.labels.map((l) => l.slice(5, 16).replace("T", " ")), [
      { label: "PM2.5 MODELLED (Open-Meteo / CAMS)", data: m.aligned[0], borderDash: [5, 3] },
      { label: "PM2.5 OBSERVED (OpenAQ, median of active monitors)", data: m.aligned[1], showLine: false, pointRadius: 4 }], "PM2.5 (ug/m3)", "UTC time", { spanGaps: true });
  } catch (e) { showError(e.message); }
}

async function initComparison(root) {
  const note = document.getElementById("cmp-note"), box = document.getElementById("cmp-summary");
  try {
    const d = await getJson(root.dataset.api);
    const src = d.current_source;
    note.textContent = "Reference: " + d.reference_source + "; years " + d.reference_years.join("-") + ". Current: " + src.name +
      (src.grid_latitude != null ? ", grid " + src.grid_latitude + ", " + src.grid_longitude + ", model elevation " + src.elevation_m + " m" : "") +
      ". Times are UTC; a reading is matched to the reference of its UTC hour.";
    const names = { temperature: "Temperature at 2 m", humidity: "Relative humidity at 2 m" };
    let rows = "";
    for (const [key, v] of Object.entries(d.variables)) {
      const l = v.latest;
      rows += l ? "<tr><td>" + names[key] + "</td><td>" + l.value + " " + v.unit + "</td><td>" + (l.ref_mean ?? "n/a") + " " + v.unit + " (+-" + (l.ref_sd ?? "n/a") + ", n=" + (l.samples ?? 0) + ")</td><td>" +
        (l.difference ?? "n/a") + "</td><td>" + l.assessment + "</td><td>" + l.time + "</td></tr>"
        : "<tr><td>" + names[key] + "</td><td colspan=\"5\" class=\"text-muted\">no readings stored in the last 24 hours</td></tr>";
    }
    const age = d.latest_age_minutes == null ? "no data" : d.latest_age_minutes + " min old" + (d.stale ? " (STALE)" : "");
    box.innerHTML = "<table class=\"table table-sm mb-1\"><thead><tr><th>Variable</th><th>Latest value</th><th>Historical reference</th><th>Difference</th><th>Assessment</th><th>Reading time (UTC)</th></tr></thead><tbody>" + rows + "</tbody></table><p class=\"small text-muted\">Latest reading: " + age + ".</p>";
    for (const [key, id] of [["temperature", "c-cmp-temp"], ["humidity", "c-cmp-hum"]]) {
      const v = d.variables[key], s = v.series;
      if (!s.length) continue;
      lineChart(id, s.map((r) => r.time.slice(5, 16).replace("T", " ")), [
        { label: "Current (Open-Meteo, modelled)", data: s.map((r) => r.value), borderWidth: 2.5 },
        { label: "Historical mean (POWER, same UTC hour)", data: s.map((r) => r.ref_mean), borderDash: [6, 3], pointRadius: 0 },
        { label: "Mean + 1 SD", data: s.map((r) => (r.ref_mean == null ? null : +(r.ref_mean + r.ref_sd).toFixed(2))), borderDash: [2, 3], borderWidth: 1, pointRadius: 0 },
        { label: "Mean - 1 SD", data: s.map((r) => (r.ref_mean == null ? null : +(r.ref_mean - r.ref_sd).toFixed(2))), borderDash: [2, 3], borderWidth: 1, pointRadius: 0 }],
        names[key] + " (" + v.unit + ")", "UTC time");
    }
  } catch (e) { note.textContent = e.message; note.classList.add("text-danger"); }
}

function initTrends(payload) {
  const t = payload.trend;
  lineChart("c-trend-temp", t.temperature.years, [{ label: "Annual mean temperature (C)", data: t.temperature.values },
    { label: "Theil-Sen trend", data: t.temperature.fit, borderDash: [6, 3], pointRadius: 0 }], "Annual mean temperature (C)", "Year");
  lineChart("c-trend-rain", t.precipitation.years, [{ label: "Annual precipitation (mm)", data: t.precipitation.values },
    { label: "Theil-Sen trend", data: t.precipitation.fit, borderDash: [6, 3], pointRadius: 0 }], "Annual precipitation (mm/year)", "Year");
  const a = payload.anomalies, years = Object.keys(a.zscore_flags_by_year);
  barChart("c-anom", years, years.map((y) => a.zscore_flags_by_year[y]), "Days flagged (|robust z| > 3.5)", "Year", "Flagged days", "#D55E00");
  const e = payload.extremes;
  if (e) {
    barChart("c-hot", e.years, e.hot_days_max_ge_35c, "Days with an hourly value >= 35 C", "Year", "Hot days", "#D55E00");
    barChart("c-frost", e.years, e.frost_days_min_lt_0c, "Days with an hourly value < 0 C", "Year", "Frost days", "#0072B2");
    barChart("c-heavy", e.years, e.heavy_precip_days_ge_10mm, "Days with >= 10 mm", "Year", "Heavy precipitation days", "#009E73");
    barChart("c-annual-rain", e.years, e.precipitation_total_mm, "Annual precipitation (mm; empty = incomplete)", "Year", "Annual precipitation", "#0072B2");
  }
}

function initForecast(fc) {
  const labels = [...fc.history.months, ...fc.forecast.months];
  const pad = (arr, offset) => labels.map((l) => { const i = offset.indexOf(l); return i < 0 ? null : arr[i]; });
  const unit = fc.unit;
  lineChart("c-forecast", labels, [
    { label: "History (derived from POWER reanalysis)", data: pad(fc.history.values, fc.history.months) },
    { label: "Model prediction for held-out test months", data: pad(fc.test_predictions.values, fc.test_predictions.months), borderDash: [4, 3] },
    { label: "FORECAST (model-derived)", data: pad(fc.forecast.values, fc.forecast.months), borderDash: [8, 4], borderWidth: 2.5 },
    { label: "Approx. lower bound", data: pad(fc.forecast.lower, fc.forecast.months), borderDash: [2, 3], borderWidth: 1, pointRadius: 0 },
    { label: "Approx. upper bound", data: pad(fc.forecast.upper, fc.forecast.months), borderDash: [2, 3], borderWidth: 1, pointRadius: 0 }],
    "Monthly value (" + unit + ")", "Month");
}

const cmpRoot = document.getElementById("cmp-root");
if (cmpRoot) initComparison(cmpRoot);
const root = document.getElementById("chart-root");
if (root) {
  const kinds = { compare: initCompare, current: initCurrent };
  (kinds[root.dataset.kind] || initHistory)(root);
}
const payloadEl = document.getElementById("payload");
if (payloadEl) {
  const payload = JSON.parse(payloadEl.textContent);
  if (payloadEl.dataset.kind === "trends") initTrends(payload);
  if (payloadEl.dataset.kind === "forecast") initForecast(payload);
}
