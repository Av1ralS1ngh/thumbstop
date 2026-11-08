/* Thumbstop hosted app shell: pick scripts and a model, run, watch the five steps light up, then review the promos.
   The results are the same review desk as the standalone review.html (review.js). */
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const size = (n) => (n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const form = $("#run"), modesEl = $("#modes"), drop = $("#drop"), fileIn = $("#file"), dropIn = $("#drop-in"), dropFile = $("#drop-file");
  const go = $("#go"), goText = go.querySelector("span"), errEl = $("#run-err"), noteEl = $("#run-note"), adv = $("#adv");
  const steps = [...document.querySelectorAll("#steps li")], runbar = $("#runbar"), runbarText = $("#runbar-text");
  const results = $("#results"), jump = $("#jump");
  const STAGE_TEXT = ["Reading the scripts", "Marking hooks and cliffhangers", "Cutting every pairing", "Checking with a cold viewer", "Ranking", "Done"];
  const FILES = {
    "promos.csv": ["Promos (CSV)", "Every segment with timecodes, the hook, the cliffhanger and why it works"],
    "promos.json": ["Promos (JSON)", "Everything, plus the cold viewer's notes and the cut sheets"],
    "selects.edl": ["EDL for Premiere or Resolve", "All picks back to back, ready to import"],
    "review.html": ["Review page", "This review desk as one file, to share or use offline"],
  };
  const SHORT = { recorded: "Claude Opus 4.6 + GPT-OSS, replayed", offline: "No AI, simple keyword rules",
    bedrock: "Live, with your AWS keys", custom: "Live, any OpenAI-compatible API" };
  let info = { modes: [], default: "offline", recorded_covers_pack: false, demo: { episodes: 3 } };
  let upload = null, uploading = false, busy = false, review = null;

  const mode = () => (form.querySelector("input[name=mode]:checked") || {}).value;
  const source = () => form.querySelector("input[name=source]:checked").value;
  const val = (n) => (form.elements[n] ? form.elements[n].value : "");

  function showErr(msg, kind) {
    errEl.hidden = !msg;
    errEl.innerHTML = msg ? esc(msg) + (kind === "not_recorded" ? `<div><button type="button" class="tsr-btn" data-offline>Try Offline rules instead</button></div>` : "") : "";
  }

  function sync() {
    const m = mode(), src = source(), rec = m === "recorded";
    if (m === "bedrock" || m === "custom") $("#more").open = true;
    drop.hidden = src !== "upload";
    $("#keys-bedrock").hidden = m !== "bedrock";
    $("#keys-custom").hidden = m !== "custom";
    adv.classList.toggle("dim", rec);
    $("#adv-note").hidden = !rec;
    adv.querySelectorAll("input").forEach((i) => { i.disabled = rec; });
    $("#run-badge").textContent = rec || m === "offline" ? "No key needed" : "Uses your key";
    let note = "";
    if (src === "demo") {
      note = rec ? "Replays a real Claude + GPT-OSS run in about a second."
        : m === "offline" ? "Keyword rules only, so you can see the pipeline with no AI."
          : "Runs the models live on the demo show with your key.";
      if (rec && info.recorded_covers_pack) note += " Have the input pack? Choose Your scripts to replay the real 50-episode run.";
    } else if (rec) {
      note = info.recorded_covers_pack
        ? "The recorded run covers the assignment's input pack, uploaded as it was sent. Other scripts need Offline rules or your own model."
        : "The recorded run only covers the demo show. Use Offline rules or your own model for other scripts.";
    } else note = m === "offline" ? "Rules only, no AI: quick, but the picks are rough." : "Every model call runs live with your key.";
    noteEl.textContent = note;
    go.disabled = busy;
  }

  async function loadInfo() {
    try { info = await (await fetch("/api/info", { cache: "no-store" })).json(); } catch (e) { /* keep defaults */ }
    const card = (m) => `<label class="mode"><input type="radio" name="mode" value="${esc(m.id)}"${m.id === info.default ? " checked" : ""}>` +
      `<span><b>${esc(m.label)}</b><small>${esc(SHORT[m.id] || m.desc)}</small></span></label>`;
    modesEl.innerHTML = info.modes.filter((m) => m.id === "recorded" || m.id === "offline").map(card).join("");
    $("#modes-more").innerHTML = info.modes.filter((m) => m.id !== "recorded" && m.id !== "offline").map(card).join("");
    $("#demo-sub").textContent = `${info.demo.episodes} short episodes, original fiction`;
    sync();
  }

  // ---------------------------------------------------------------- upload
  function xhrUpload(f, onProgress) {
    return new Promise((resolve, reject) => {
      const x = new XMLHttpRequest();
      x.open("POST", "/api/upload");
      x.setRequestHeader("Content-Type", "application/zip");
      x.setRequestHeader("X-Filename", f.name.replace(/[^\x20-\x7e]/g, "_"));
      x.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
      x.onload = () => { let d = {}; try { d = JSON.parse(x.responseText); } catch (e) { /* not json */ }
        x.status === 200 ? resolve(d) : reject(new Error(d.error || "The upload failed.")); };
      x.onerror = () => reject(new Error("The upload failed. Check your connection and try again."));
      x.send(f);
    });
  }
  async function sendFile(f) {
    if (!f) return;
    showErr("");
    if (!/\.zip$/i.test(f.name)) return showErr("Please choose a .zip file of episode scripts.");
    upload = null; uploading = true;
    dropIn.hidden = true; dropFile.hidden = false;
    dropFile.innerHTML = `<b>${esc(f.name)}</b><span class="bar"><i></i></span><small>${size(f.size)}</small>`;
    sync();
    try {
      upload = await xhrUpload(f, (p) => { const b = dropFile.querySelector(".bar i"); if (b) b.style.width = (p * 100).toFixed(0) + "%"; });
      dropFile.innerHTML = `<span class="ok">✓</span><b>${esc(upload.name)}</b><small>${upload.episodes} episode files · ${size(upload.size)}</small>`;
      go.focus({ preventScroll: true });   // ready: Enter runs it
    } catch (e) {
      dropIn.hidden = false; dropFile.hidden = true;
      showErr(e.message);
    }
    uploading = false;
    fileIn.value = "";
    sync();
  }
  fileIn.addEventListener("change", () => sendFile(fileIn.files[0]));
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, () => drop.classList.add("over")));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, () => drop.classList.remove("over")));

  // ---------------------------------------------------------------- the five steps
  function paintSteps(k, failed) {
    steps.forEach((li, i) => {
      li.classList.toggle("done", i < k);
      li.classList.toggle("active", i === k && k < 5 && !failed);
    });
    runbarText.textContent = STAGE_TEXT[Math.min(k, 5)] + (k < 5 && !failed ? "…" : "");
  }
  function resetSteps() { steps.forEach((li) => { li.classList.remove("done", "active"); li.querySelector("em").textContent = ""; }); }
  function fillCounts(meta, view) {
    const f = meta.funnel || {}, ing = meta.ingest || "";
    const eps = (/episodes: (\d+)/.exec(ing) || [])[1] || view.episodes;
    const beats = ((/Beats: ([^\n]*)/.exec(ing) || [])[1] || "").match(/\d+/g);
    const n = (x) => (x == null ? "–" : Number(x).toLocaleString());
    const counts = [
      `${n(eps)} episodes` + (beats ? ` · ${n(beats.reduce((a, b) => a + +b, 0))} beats` : ""),
      `${n(f.anchors)} endings · ${n(f.hook_beats)} openings`,
      `${n(f.valid_candidates)} clean cuts`,
      f.shortlisted ? `${n(f.gate_passed)} of ${n(f.shortlisted)} followed` : "Not run",
      `${n(f.ranked)} ranked · ${view.promos.filter((p) => p.first).length} to post first`,
    ];
    steps.forEach((li, i) => { li.querySelector("em").textContent = counts[i]; });
    const u = meta.usage || {};
    runbarText.innerHTML = `<b>Done</b> · ${n(u.calls)} model calls${u.cache_hits ? ` (+${n(u.cache_hits)} replayed)` : ""} · $${(u.usd || 0).toFixed(2)} · ${Math.max(1, Math.round(meta.seconds || 0))} s`;
  }

  // ---------------------------------------------------------------- run
  function setBusy(on) {
    busy = on;
    go.classList.toggle("busy", on);
    goText.textContent = on ? "Finding promos" : "Find promos";
    runbar.classList.toggle("live", on);
    sync();
  }

  async function runIt(o) {
    showErr("");
    const src = source(), m = mode();
    if (src === "upload" && !upload) return showErr(uploading ? "Wait for the upload to finish." : "Choose a .zip of episode files first.");
    const body = {
      mode: m, source: src, upload_id: upload && upload.upload_id,
      settings: { top: val("top"), min: val("min"), max: val("max"), episodes: val("episodes"), fast: form.elements.fast.checked },
      keys: m === "bedrock" ? { region: val("region"), key_id: val("key_id"), secret: val("secret"), token: val("token") }
        : m === "custom" ? { base_url: val("base_url"), model: val("model"), api_key: val("api_key") } : {},
    };
    setBusy(true);
    resetSteps(); paintSteps(0);
    runbar.hidden = false;
    if (review) { review.destroy(); review = null; }
    // a placeholder of what's coming, which also gives the page room to settle on the steps
    results.hidden = false; jump.hidden = true;
    results.innerHTML = `<div class="skel" aria-hidden="true"><i class="s1"></i><i class="s2"></i><i class="s3"></i>
      <div class="skel-row"><i></i><i></i><i></i></div><div class="skel-desk"><i></i><i></i></div></div>`;
    let jid;
    try {
      const r = await fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || "Could not start the run.");
      jid = d.job_id;
    } catch (e) {
      setBusy(false); runbar.hidden = true; resetSteps(); results.hidden = true;
      return showErr(e.message);
    }
    if (o.scroll) $("#how").scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
    let shown = 0, final = null;
    for (;;) {
      let d;
      try {
        const r = await fetch(`/api/jobs/${jid}`, { cache: "no-store" });
        d = await r.json();
        if (!r.ok) throw new Error(d.error || "Lost touch with the server.");
      } catch (e) { setBusy(false); paintSteps(shown, true); results.hidden = true; return showErr(e.message); }
      if (d.state === "error") {
        setBusy(false); paintSteps(shown, true); runbarText.textContent = "Stopped"; results.hidden = true;
        if (o.scroll) form.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
        return showErr(d.error, d.kind);
      }
      const target = d.state === "done" ? 5 : d.stage;
      // step through the stages at a readable pace, even when a recorded run finishes in a second
      while (shown < target) { shown++; paintSteps(shown); await sleep(reduced ? 0 : d.state === "done" ? 340 : 140); }
      if (d.state === "done") { final = d; break; }
      await sleep(450);
    }
    fillCounts(final.result.meta, final.result.view);
    showResults(jid, final, o);
    setBusy(false);
  }

  function showResults(jid, d, o) {
    const res = d.result, notes = [];
    if (res.same_family) notes.push("The annotator and the cold viewer are the same model family here, so the cold-viewer check is less independent.");
    if (d.fallbacks) notes.push(`${d.fallbacks} episode${d.fallbacks > 1 ? "s" : ""} fell back to simple rules because the model's answer was unusable.`);
    if (d.mode === "offline") notes.push("Offline mode uses keyword rules and no AI, so these picks are rough. The recorded AI run shows what the real thing finds.");
    results.innerHTML = notes.map((t) => `<p class="notice">${esc(t)}</p>`).join("") + `<div id="review-root"></div>`;
    results.hidden = false;
    $("#nav-results").hidden = false;
    review = window.ThumbstopReview.mount($("#review-root"), res.view, {
      downloads: res.files.map((name) => ({ label: (FILES[name] || [name])[0], desc: (FILES[name] || [])[1], href: `/api/jobs/${jid}/files/${name}`, name })),
    });
    // the results appear right under the steps; point the way only when they start below the screen
    requestAnimationFrame(() => { jump.hidden = results.getBoundingClientRect().top < window.innerHeight - 24; });
  }

  document.querySelectorAll("[data-goto]").forEach((b) => b.addEventListener("click", () => {
    const el = document.getElementById(b.dataset.goto);
    if (el) { el.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" }); if (b.dataset.goto === "run") setTimeout(() => go.focus({ preventScroll: true }), 500); }
  }));
  jump.addEventListener("click", () => { jump.hidden = true; results.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" }); });
  window.addEventListener("scroll", () => {
    if (!jump.hidden && results.getBoundingClientRect().top < window.innerHeight * 0.6) jump.hidden = true;
  }, { passive: true });
  form.addEventListener("change", (e) => { if (e.target.name === "mode" || e.target.name === "source") { showErr(""); sync(); } });
  form.addEventListener("submit", (e) => { e.preventDefault(); if (!busy) runIt({ scroll: true }); });
  errEl.addEventListener("click", (e) => {
    if (e.target.closest("[data-offline]")) {
      const r = form.querySelector('input[name=mode][value="offline"]');
      if (r) { r.checked = true; sync(); runIt({ scroll: true }); }
    }
  });

  fetch("/assets/teaser.json", { cache: "no-store" }).then((r) => r.json())
    .then((d) => window.ThumbstopReview.teaser($("#teaser"), d.promo)).catch(() => { $("#teaser").hidden = true; });

  // the top bar: a hairline once the page scrolls, and the section you're in
  const topbar = $("#topbar"), navLinks = [...document.querySelectorAll("[data-nav]")];
  const onScroll = () => topbar.classList.toggle("scrolled", window.scrollY > 8);
  window.addEventListener("scroll", onScroll, { passive: true }); onScroll();
  if ("IntersectionObserver" in window) {
    const seen = new Map();
    const spy = new IntersectionObserver((es) => {
      es.forEach((x) => seen.set(x.target.id, x.isIntersecting ? x.intersectionRatio : 0));
      const best = [...seen.entries()].sort((a, b) => b[1] - a[1])[0];
      navLinks.forEach((l) => l.classList.toggle("on", !!best && best[1] > 0 && l.dataset.nav === best[0]));
    }, { threshold: [0, 0.15, 0.4, 0.7], rootMargin: "-80px 0px -35% 0px" });
    ["run", "how", "results"].forEach((id) => spy.observe(document.getElementById(id)));
  }

  loadInfo().then(() => {
    if (new URLSearchParams(location.search).get("demo") === "1") runIt({ scroll: false });
  });
})();
