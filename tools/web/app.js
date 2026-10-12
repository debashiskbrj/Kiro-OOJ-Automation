"use strict";

const $ = (id) => document.getElementById(id);
// Cards are shown in the order the pipeline actually runs them. SCC produces the
// genuine TID set that GCRS, Polygon and ADS then use, so SCC comes before GCRS.
const SOURCE_LABELS = {
  tickets: "SIM-T Tickets",
  eagle: "EagleEye",
  scc: "SCC",
  pnama: "PNAMA",
  dsr: "DSR",
  gcrs: "GCRS",
  polygon: "Polygon",
  ads: "ADS",
};
// The one-by-one step order the operator sees. SCC runs before EagleEye (SCC produces
// the genuine TID set the later lanes use).
const STEP_ORDER = ["tickets", "scc", "eagle", "pnama", "dsr", "gcrs", "polygon", "ads"];

let midwayReady = false;
let runActive = false;
let pollTimer = null;
// SCC's automated browser search is retired (SCC now blocks it). The run now pauses
// after the SIM-T ticket pull (Phase A) and the SCC progress card itself prompts for
// a manual upload; the message/color below survive each renderSources() re-render
// since the file input/button/status line are recreated from status.scc_phase on
// every poll tick.
let sccUploadMessage = "";
let sccUploadOk = null;
// Last full /status payload, kept so sccUpload() can re-render the SCC card
// immediately (e.g. to show "Uploading\u2026") without waiting for the next poll tick.
let lastStatus = {};
// Phases where Phase A has already exited (no live subprocess) but the run has not
// finished \u2014 we are simply paused waiting on the operator's SCC upload. tick()'s
// "run finished" check must not treat this pause as the run being over.
const SCC_PAUSE_PHASES = ["awaiting_scc_upload", "scc_uploaded"];

async function getJSON(url) {
  const res = await fetch(url, { cache: "no-store" });
  return res.json();
}
async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return { ok: res.ok, data: await res.json().catch(() => ({})) };
}

// -------------------------------------------------------- preflight / midway
async function refreshPreflight() {
  let pre;
  try {
    pre = await getJSON("/preflight");
  } catch (e) {
    $("midway-state").textContent = "Cannot reach the local server.";
    return;
  }
  midwayReady = !!pre.midway_ok;
  const state = $("midway-state");
  if (pre.midway_ok) {
    state.textContent = "Midway access granted.";
    state.style.color = "var(--ok)";
  } else {
    state.textContent = "Midway session expired or missing. Click to grant.";
    state.style.color = "var(--warn)";
  }

  const startBtn = $("start-btn");
  const startState = $("start-state");
  if (!pre.edge_profile_exists) {
    startBtn.disabled = true;
    startState.textContent = "Automation Edge profile not found.";
  } else if (pre.edge_profile_busy) {
    startBtn.disabled = true;
    startState.textContent = "The automation Edge window is open. Close it, then this unlocks.";
  } else if (!pre.midway_ok) {
    startBtn.disabled = true;
    startState.textContent = "Grant Midway access first.";
  } else if (!runActive) {
    startBtn.disabled = false;
    startState.textContent = "Ready.";
  }
}

// -------------------------------------------------------- SCC manual upload
// The file input/button/status line live INSIDE the SCC progress card and are
// recreated by renderSources() on every poll tick, so this function writes its
// result into module-level variables (sccUploadMessage/sccUploadOk) instead of
// touching the DOM directly \u2014 renderSources() is the single source of truth for
// that line's text/color, and a direct DOM write here would be clobbered by the
// next poll's re-render.
async function sccUpload() {
  const input = $("scc-file-input");
  const file = input && input.files && input.files[0];
  if (!file) {
    sccUploadMessage = "Choose a CSV file first.";
    sccUploadOk = false;
    renderSources(lastStatus || {});
    return;
  }
  sccUploadMessage = "Uploading\u2026";
  sccUploadOk = null;
  renderSources(lastStatus || {});
  let contentBase64;
  try {
    contentBase64 = await fileToBase64(file);
  } catch (e) {
    sccUploadMessage = "Could not read the file.";
    sccUploadOk = false;
    renderSources(lastStatus || {});
    return;
  }
  const { ok, data } = await postJSON("/scc/upload", {
    filename: file.name,
    content_base64: contentBase64,
  });
  if (!ok || !data.uploaded) {
    sccUploadMessage = "Rejected: " + (data.reason || "unknown error");
    sccUploadOk = false;
    renderSources(lastStatus || {});
    return;
  }
  // The server auto-continues (Phase B) in the same /scc/upload call and returns the
  // result under data.continue. Surface a clear failure if Phase B did not start, and
  // make sure polling is alive so the progress cards advance.
  const cont = data.continue || {};
  if (cont.started === false) {
    sccUploadMessage = "Upload accepted but the run could not resume: " + (cont.reason || "unknown error");
    sccUploadOk = false;
    renderSources(lastStatus || {});
    return;
  }
  sccUploadMessage =
    "Uploaded \u201c" + (data.filename || file.name) + "\u201d (" + data.rows + " rows). Resuming the pipeline\u2026";
  sccUploadOk = true;
  runActive = true;
  // Re-arm polling immediately (it may have been cleared) and poll now so the SCC card
  // flips from the upload control to a running step without waiting for the next tick.
  startPolling();
}
// The SCC card's file input/button are recreated on every renderSources() call, so
// bind the click handler once via delegation on the stable #sources container.
$("sources").addEventListener("click", (event) => {
  if (event.target && event.target.id === "scc-upload-btn") {
    sccUpload();
  }
});

$("midway-btn").addEventListener("click", async () => {
  $("midway-state").textContent = "Opening the Midway PIN prompt in a terminal window\u2026";
  const { data } = await postJSON("/midway", {});
  if (!data.launched) {
    $("midway-state").textContent = "Could not open the prompt: " + (data.reason || "unknown");
    return;
  }
  // Poll until the cookie becomes valid.
  const wait = setInterval(async () => {
    await refreshPreflight();
    if (midwayReady) clearInterval(wait);
  }, 2000);
});

// -------------------------------------------------------- start run
$("start-btn").addEventListener("click", async () => {
  const adsApproved = $("ads-approve").checked;
  const allowSourceFailures = $("allow-source-failures").checked;
  $("start-btn").disabled = true;
  $("start-state").textContent = "Starting\u2026";
  const { ok, data } = await postJSON("/run", {
    ads_approved: adsApproved,
    allow_source_failures: allowSourceFailures,
  });
  if (!ok || !data.started) {
    $("start-state").textContent = "Could not start: " + (data.reason || "unknown");
    $("start-btn").disabled = false;
    return;
  }
  runActive = true;
  $("stop-btn").disabled = false;
  $("start-state").textContent = data.partial
    ? "Running (partial \u2014 ADS not approved, no download will be produced)."
    : "Running the full pipeline\u2026";
  startPolling();
});

// -------------------------------------------------------- stop run
$("stop-btn").addEventListener("click", async () => {
  $("stop-btn").disabled = true;
  $("start-state").textContent = "Stopping\u2026";
  const { ok, data } = await postJSON("/stop", {});
  if (!ok || !data.stopped) {
    $("start-state").textContent = "Could not stop: " + (data.reason || "unknown");
    $("stop-btn").disabled = false;
    return;
  }
  runActive = false;
  $("start-state").textContent = "Run stopped. Click Start run to run again.";
  refreshPreflight();
});

// -------------------------------------------------------- polling
function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(tick, 2000);
  tick();
}

async function tick() {
  let status;
  try {
    status = await getJSON("/status");
  } catch (e) {
    return;
  }
  lastStatus = status;
  renderSources(status);
  renderLog(status.log_tail || []);
  renderTable();
  renderFinish(status);

  // Keep Stop enabled only while a run is actually running.
  $("stop-btn").disabled = !status.running;

  if (!status.running && !SCC_PAUSE_PHASES.includes(status.scc_phase) && (status.stamp || status.stopped)) {
    // Run finished or was stopped (not merely paused awaiting the SCC upload).
    clearInterval(pollTimer);
    pollTimer = null;
    runActive = false;
    if (status.stopped) {
      $("start-state").textContent = "Run stopped. Click Start run to run again.";
    }
    refreshPreflight();
  }
}

function renderSources(status) {
  const sources = status.sources || {};
  const sccPhase = status.scc_phase;
  const inSccPause = sccPhase === "awaiting_scc_upload" || sccPhase === "scc_uploaded";
  const wrap = $("sources");

  // Preserve the operator's chosen SCC file across re-renders. renderSources runs on
  // every 2s poll and rebuilds all cards so the other steps (SIM-T, EagleEye, ...)
  // update live. A fresh <input type="file"> would lose the selected file, so we DETACH
  // the existing input node and re-attach the SAME node into the rebuilt SCC card,
  // keeping its selection intact. All other cards still rebuild normally.
  let preservedFileInput = null;
  if (inSccPause) {
    const existing = $("scc-file-input");
    if (existing) {
      preservedFileInput = existing;
      if (preservedFileInput.parentNode) {
        preservedFileInput.parentNode.removeChild(preservedFileInput);
      }
    }
  }

  wrap.innerHTML = "";
  STEP_ORDER.forEach((key, i) => {
    const src = sources[key] || { status: "pending", counts: {} };
    const el = document.createElement("div");

    if (key === "scc" && inSccPause) {
      // SCC's automated browser lane is retired; the run pauses here until the
      // operator uploads SCC's native export CSV, rendered INSIDE this card.
      const uploading = sccPhase === "scc_uploaded";
      el.className = "source step-running";
      let messageHtml = "";
      if (sccUploadMessage) {
        const color = sccUploadOk === false ? "var(--warn)" : sccUploadOk === true ? "var(--ok)" : "var(--muted)";
        messageHtml = `<p id="scc-upload-state" class="state" style="color:${color}">${escapeHtml(sccUploadMessage)}</p>`;
      } else {
        messageHtml = `<p id="scc-upload-state" class="state">No SCC file uploaded yet.</p>`;
      }
      el.innerHTML =
        `<div class="step-head">` +
        `<span class="step-num">${i + 1}</span>` +
        `<span class="name">${SOURCE_LABELS[key] || key}</span>` +
        `<span class="badge awaiting">awaiting upload</span>` +
        `</div>` +
        `<p class="hint">Download the SIM-T TIDs from card 1, search them in SCC, export the CSV, and upload it here.</p>` +
        `<div class="btn-row scc-upload-box" id="scc-upload-row"></div>` +
        messageHtml;
      wrap.appendChild(el);

      // Fill the upload row: reuse the preserved input (keeps the selected file) or
      // create a fresh one on the first render into the pause state.
      const row = el.querySelector("#scc-upload-row");
      const fileInput = preservedFileInput || document.createElement("input");
      if (!preservedFileInput) {
        fileInput.type = "file";
        fileInput.id = "scc-file-input";
        fileInput.accept = ".csv";
      }
      fileInput.disabled = uploading;
      const uploadBtn = document.createElement("button");
      uploadBtn.id = "scc-upload-btn";
      uploadBtn.type = "button";
      uploadBtn.textContent = "Upload SCC CSV";
      uploadBtn.disabled = uploading;
      row.appendChild(fileInput);
      row.appendChild(uploadBtn);
      return;
    }

    el.className = `source step-${src.status}`;
    const counts = Object.entries(src.counts || {})
      .filter(([, v]) => v !== null && v !== undefined)
      .map(([k, v]) => `${k}: ${v}`)
      .join("\n");
    const dl = src.downloadable
      ? `<a class="src-dl" href="/source-download?name=${encodeURIComponent(key)}">\u2913 Download CSV</a>`
      : "";
    const pct = typeof src.percent === "number" ? src.percent : 0;
    const showBar = src.status === "running" || src.status === "done";
    const bar = showBar
      ? `<div class="pbar"><div class="pbar-fill ${src.status}" style="width:${pct}%"></div>` +
        `<span class="pbar-label">${pct}%</span></div>`
      : "";
    el.innerHTML =
      `<div class="step-head">` +
      `<span class="step-num">${i + 1}</span>` +
      `<span class="name">${SOURCE_LABELS[key] || key}</span>` +
      `<span class="badge ${src.status}">${src.status}</span>` +
      `</div>` +
      bar +
      `<div class="counts">${counts || ""}</div>` +
      dl;
    wrap.appendChild(el);
  });
}

function renderLog(lines) {
  const pre = $("log");
  pre.textContent = lines.join("\n");
  pre.scrollTop = pre.scrollHeight;
}

async function renderTable() {
  let table;
  try {
    table = await getJSON("/table");
  } catch (e) {
    return;
  }
  $("row-count").textContent = table.count ? `${table.count} rows` : "";
  const thead = document.querySelector("#data-table thead");
  const tbody = document.querySelector("#data-table tbody");
  if (!table.columns || !table.columns.length) {
    thead.innerHTML = "";
    tbody.innerHTML = "";
    return;
  }
  thead.innerHTML = "<tr>" + table.columns.map((c) => `<th>${escapeHtml(c)}</th>`).join("") + "</tr>";
  tbody.innerHTML = table.rows
    .map((r) => "<tr>" + r.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("") + "</tr>")
    .join("");
}

function renderFinish(status) {
  const banner = $("verify-banner");
  const dlBtn = $("download-btn");
  const note = $("desktop-note");

  // Paused after SIM-T awaiting the manual SCC upload: Phase A has exited (so
  // status.running is false and status.stamp is set), but the run is NOT finished.
  // Show the paused state and never fall through to the "partial run complete" banner.
  if (SCC_PAUSE_PHASES.includes(status.scc_phase)) {
    banner.className = "banner pending";
    banner.textContent = "Paused \u2014 upload SCC's export CSV in the SCC card (step 2) to resume the run.";
    dlBtn.disabled = true;
    return;
  }

  if (status.running) {
    banner.className = "banner pending";
    banner.textContent = "Run in progress\u2026 verification happens automatically at the end.";
    dlBtn.disabled = true;
    return;
  }
  if (status.stopped) {
    banner.className = "banner warn";
    banner.textContent = "Run stopped by you. No workbook was produced. Click Start run to run again.";
    dlBtn.disabled = true;
    return;
  }
  if (status.publication_blocked) {
    banner.className = "banner error";
    const b = status.publication_blocked;
    banner.textContent =
      "Blocked \u2014 not publishable. Failed: " +
      JSON.stringify(b.failed_required || []) +
      " Skipped: " +
      JSON.stringify(b.skipped_required || []);
    dlBtn.disabled = true;
    return;
  }
  if (status.publishable) {
    banner.className = "banner ok";
    banner.textContent = "Verified \u2014 read-back and polygon checks passed. Download is ready.";
    dlBtn.disabled = false;
    note.textContent = status.desktop_file
      ? "Saved to Desktop: " + status.desktop_file
      : "Click Download to save the workbook (also copied to your Desktop).";
    return;
  }
  if (status.workbook_downloadable) {
    // A PARTIAL workbook: some sources were left blank but the file was produced.
    const blanks = status.blank_sources || [];
    banner.className = "banner warn";
    banner.textContent =
      "PARTIAL \u2014 workbook ready to download. Blank source(s): " +
      (blanks.length ? blanks.join(", ") : "none") +
      ". Those sheets are empty; the rest of the data is filled.";
    dlBtn.disabled = false;
    note.textContent = status.desktop_file
      ? "Saved to Desktop: " + status.desktop_file
      : "Click Download to save the partial workbook (also copied to your Desktop).";
    return;
  }
  if (status.partial && status.stamp && !status.running) {
    banner.className = "banner warn";
    banner.textContent = "Partial run complete (ADS not approved). No downloadable workbook was produced.";
    dlBtn.disabled = true;
    return;
  }
}

$("download-btn").addEventListener("click", () => {
  window.location.href = "/download";
});

// -------------------------------------------------------- restart server
$("restart-btn").addEventListener("click", async () => {
  const btn = $("restart-btn");
  const state = $("restart-state");
  if (btn.dataset.busy === "1") return;
  const original = btn.textContent;
  btn.dataset.busy = "1";
  btn.disabled = true;
  btn.classList.add("working");
  btn.textContent = "Restarting\u2026";
  state.textContent = "Stopping any run and relaunching the local server\u2026";
  try {
    await postJSON("/restart", {});
  } catch (e) {
    // The server may drop the connection as it shuts down; that's expected.
  }
  // Poll until the fresh server answers, then reload the page.
  let waited = 0;
  const poll = setInterval(async () => {
    waited += 1;
    try {
      const res = await fetch("/preflight", { cache: "no-store" });
      if (res.ok) {
        clearInterval(poll);
        state.textContent = "Server is back \u2014 reloading\u2026";
        setTimeout(() => window.location.reload(), 400);
        return;
      }
    } catch (e) {
      // still down; keep waiting
    }
    if (waited === 1) state.textContent = "Waiting for the server to come back up\u2026";
    if (waited > 40) {
      clearInterval(poll);
      btn.dataset.busy = "";
      btn.disabled = false;
      btn.classList.remove("working");
      btn.textContent = original;
      state.textContent = "Server did not come back automatically. Re-open the page or relaunch the .bat.";
    }
  }, 1000);
});

// -------------------------------------------------------- copy live log
$("copy-log-btn").addEventListener("click", async (event) => {
  // Don't toggle the <details> when clicking the button inside <summary>.
  event.preventDefault();
  event.stopPropagation();
  const btn = $("copy-log-btn");
  const original = btn.textContent;
  let text = "";
  try {
    const data = await getJSON("/log");
    text = (data && data.text) || "";
  } catch (e) {
    text = "";
  }
  // Fall back to whatever is on screen if the full log endpoint returned nothing.
  if (!text) text = $("log").textContent || "";
  const done = (ok) => {
    btn.textContent = ok ? "Copied!" : "Copy failed";
    setTimeout(() => { btn.textContent = original; }, 1500);
  };
  try {
    await navigator.clipboard.writeText(text);
    done(true);
  } catch (e) {
    // Clipboard API can be blocked; fall back to a temporary textarea + execCommand.
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      done(ok);
    } catch (e2) {
      done(false);
    }
  }
});

function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// ======================================================== post comments (5)
// A dedicated, gated flow: upload -> dry-run preview -> (checkbox) -> confirm post.
// Mirrors the start/stop button + guard-state pattern above, with its own 2s poll.
let pcPollTimer = null;
let pcUploaded = false;

async function pcUpload() {
  const input = $("pc-file-input");
  const file = input && input.files && input.files[0];
  if (!file) {
    $("pc-upload-state").textContent = "Choose an .xlsx file first.";
    return;
  }
  if (!file.name.toLowerCase().endsWith(".xlsx")) {
    $("pc-upload-state").textContent = "Only .xlsx files are supported.";
    return;
  }
  $("pc-upload-btn").disabled = true;
  $("pc-upload-state").textContent = "Uploading and parsing\u2026";
  let contentBase64;
  try {
    contentBase64 = await fileToBase64(file);
  } catch (e) {
    $("pc-upload-state").textContent = "Could not read the file.";
    $("pc-upload-btn").disabled = false;
    return;
  }
  const { ok, data } = await postJSON("/post-comments/upload", {
    filename: file.name,
    content_base64: contentBase64,
  });
  $("pc-upload-btn").disabled = false;
  if (!ok || !data.started) {
    $("pc-upload-state").textContent = "Could not start preview: " + (data.reason || "unknown");
    return;
  }
  pcUploaded = true;
  $("pc-upload-state").textContent = "Parsing \u201c" + (data.filename || file.name) + "\u201d\u2026";
  // Reset the confirm gate for the new upload.
  $("pc-confirm-check").checked = false;
  pcStartPolling();
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () => {
      // reader.result is a data URL: strip the "data:...;base64," prefix.
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.readAsDataURL(file);
  });
}

function pcStartPolling() {
  if (pcPollTimer) clearInterval(pcPollTimer);
  pcPollTimer = setInterval(pcTick, 2000);
  pcTick();
}

async function pcTick() {
  let status;
  try {
    status = await getJSON("/post-comments/status");
  } catch (e) {
    return;
  }
  pcRenderPreview(status);
  pcRenderLog(status.log_tail || []);
  pcRenderProgress(status);
  pcRefreshGate(status);

  $("pc-stop-btn").disabled = !status.running;
  const hasReport = !!status.report_available;
  $("pc-report-download-btn").disabled = !hasReport;
  // Offer retry only once a posting pass has finished and left failures behind.
  const rows = status.report_rows || [];
  const hasFailures = rows.some((r) => String(r.Status || "").startsWith("Failed"));
  $("pc-retry-btn").disabled = status.running || !hasFailures;

  if (!status.running && pcPollTimer && status.mode) {
    // A pass finished; keep polling lightly so a later retry/report still updates,
    // but stop the tight loop once nothing is running and the preview is settled.
    if (!status.dry_run_summary || status.exit_code !== null) {
      // leave the timer running at 2s; it is cheap and local. Stop only on confirm end.
    }
  }
}

function pcRenderPreview(status) {
  const wrap = $("pc-preview");
  const summary = status.dry_run_summary;
  if (!summary) {
    wrap.innerHTML = status.running
      ? '<p class="hint">Parsing the uploaded file\u2026</p>'
      : "";
    return;
  }
  const malformed = summary.malformed_rows || [];
  const preview = summary.preview || [];
  let html =
    '<div class="banner ok">Preview ready for \u201c' +
    escapeHtml(summary.input_file || "") +
    '\u201d \u2014 ' +
    "<strong>" + summary.total_rows + "</strong> rows, " +
    "<strong>" + summary.unique_url_count + "</strong> unique URL(s), " +
    "<strong>" + malformed.length + "</strong> malformed row(s).</div>";
  if (malformed.length) {
    html +=
      '<details class="pc-malformed"><summary>' +
      malformed.length +
      " malformed row(s) (excluded)</summary><ul>" +
      malformed
        .map((m) => "<li>row " + escapeHtml(m.row_index) + ": " + escapeHtml(m.reason) + "</li>")
        .join("") +
      "</ul></details>";
  }
  html += '<div class="pc-preview-list">';
  preview.forEach((item) => {
    html +=
      '<div class="pc-preview-item"><div class="pc-preview-url">' +
      escapeHtml(item.url) +
      ' <span class="pill">' + item.tid_count + " TID(s)</span></div>" +
      '<pre class="pc-comment">' + escapeHtml(item.comment_text) + "</pre></div>";
  });
  html += "</div>";
  if (summary.unique_url_count > preview.length) {
    html += '<p class="hint">Showing first ' + preview.length + " of " + summary.unique_url_count + " URL(s).</p>";
  }
  wrap.innerHTML = html;
}

function pcRefreshGate(status) {
  // Enable Post only when a successful dry-run exists for the current upload AND the
  // confirmation checkbox is ticked AND nothing is currently running.
  const ready = !!status.dry_run_ready && !!status.dry_run_summary;
  const checked = $("pc-confirm-check").checked;
  $("pc-post-btn").disabled = !(ready && checked) || status.running;
}

function pcRenderProgress(status) {
  const wrap = $("pc-progress");
  const rows = status.report_rows || [];
  if (!rows.length) {
    wrap.innerHTML = "";
    return;
  }
  const total = rows.length;
  const done = rows.filter((r) => String(r.Status || "") === "Success").length;
  const failed = rows.filter((r) => String(r.Status || "").startsWith("Failed")).length;
  const pct = total ? Math.round((done * 100) / total) : 0;
  let html =
    '<div class="pbar"><div class="pbar-fill ' +
    (failed ? "" : "done") +
    '" style="width:' + pct + '%"></div><span class="pbar-label">' + pct + "%</span></div>";
  html +=
    '<p class="hint">Posted ' +
    '<span class="badge done">' + done + " ok</span> " +
    '<span class="badge error">' + failed + " failed</span> of " +
    total + " ticket row(s).</p>";
  wrap.innerHTML = html;
}

function pcRenderLog(lines) {
  const pre = $("pc-log");
  if (!pre) return;
  pre.textContent = lines.join("\n");
  pre.scrollTop = pre.scrollHeight;
}

$("pc-upload-btn").addEventListener("click", pcUpload);
$("pc-confirm-check").addEventListener("change", async () => {
  // Re-evaluate the gate immediately on toggle (don't wait for the next poll).
  try {
    const status = await getJSON("/post-comments/status");
    pcRefreshGate(status);
  } catch (e) {
    /* ignore */
  }
});
$("pc-post-btn").addEventListener("click", async () => {
  $("pc-post-btn").disabled = true;
  $("pc-post-state").textContent = "Starting the posting run\u2026";
  const { ok, data } = await postJSON("/post-comments/confirm", {});
  if (!ok || !data.started) {
    $("pc-post-state").textContent = "Could not start: " + (data.reason || "unknown");
    return;
  }
  $("pc-post-state").textContent = "Posting comments to tickets\u2026";
  $("pc-stop-btn").disabled = false;
  pcStartPolling();
});
$("pc-stop-btn").addEventListener("click", async () => {
  $("pc-stop-btn").disabled = true;
  $("pc-post-state").textContent = "Stopping\u2026";
  const { ok, data } = await postJSON("/post-comments/stop", {});
  if (!ok || !data.stopped) {
    $("pc-post-state").textContent = "Could not stop: " + (data.reason || "unknown");
    return;
  }
  $("pc-post-state").textContent = "Posting stopped.";
});
$("pc-retry-btn").addEventListener("click", async () => {
  $("pc-retry-btn").disabled = true;
  $("pc-post-state").textContent = "Retrying failed tickets\u2026";
  const { ok, data } = await postJSON("/post-comments/retry-failed", {});
  if (!ok || !data.started) {
    $("pc-post-state").textContent = "Could not retry: " + (data.reason || "unknown");
    return;
  }
  $("pc-post-state").textContent = "Retrying failed tickets\u2026";
  $("pc-stop-btn").disabled = false;
  pcStartPolling();
});
$("pc-report-download-btn").addEventListener("click", () => {
  window.location.href = "/post-comments/report-download";
});

// -------------------------------------------------------- boot
refreshPreflight();
setInterval(() => { if (!runActive) refreshPreflight(); }, 5000);
