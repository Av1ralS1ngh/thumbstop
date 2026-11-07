/* Thumbstop review desk: one component for the hosted app's results and the standalone review.html.
   ThumbstopReview.mount(element, view, {standalone, downloads}) renders from the JSON that export.review_data writes.
   The phone is an animatic: there is no footage, so it plays the script at its real timings, with the shot as the
   picture, the dialogue as captions, and (optionally) the lines read aloud by the browser's own on-device voices. */
(function () {
  "use strict";

  // ---------------------------------------------------------------- small helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const mm = (s) => { s = Math.max(0, s || 0); return Math.floor(s / 60) + ":" + String(Math.floor(s % 60)).padStart(2, "0"); };
  const secs = (ts) => String(ts).split(":").reduce((a, p) => a * 60 + parseFloat(p), 0);
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode: decisions live for this visit */ } },
  };
  const hash = (s) => { let h = 2166136261; for (const c of String(s)) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; };
  const HUES = [24, 32, 40, 14, 200, 212, 352, 28];

  // ---------------------------------------------------------------- voices (on-device only)
  // Only voices the operating system runs locally are used, so no script text is sent anywhere.
  const FEMALE_V = /samantha|karen|moira|tessa|fiona|victoria|allison|ava|susan|zira|serena|kate|veena|catherine|martha|nicky|zoe|joelle|flo|sandy|shelley|kathy|jenny|aria|libby|sonia|emma|olivia|isha/i;
  const MALE_V = /daniel|alex|fred|rishi|tom|oliver|aaron|arthur|david|mark|george|gordon|lee|reed|ralph|eddy|rocko|junior|guy|ryan|thomas|james|brian|christopher|eric|evan|nathan/i;
  const FEMALE_ROLE = /\b(lady|woman|maid|duchess|mother|sister|queen|girl|wife|matriarch|bride|aunt|daughter|miss|mrs|nurse|princess|grandmother|madam|mistress|countess)\b/i;
  const Voices = {
    list: [], ready: false, subs: new Set(), inited: false,
    init() {
      if (this.inited || !("speechSynthesis" in window)) return;
      this.inited = true;
      const load = () => {
        this.list = speechSynthesis.getVoices().filter((v) => v.localService && /^en([-_]|$)/i.test(v.lang));
        this.ready = this.list.length > 0;
        this.subs.forEach((f) => f());
      };
      load();
      speechSynthesis.addEventListener ? speechSynthesis.addEventListener("voiceschanged", load) : (speechSynthesis.onvoiceschanged = load);
    },
    enabled() { return this.ready && store.get("tsr-voices", false); },
    set(v) { store.set("tsr-voices", !!v); if (!v) this.stop(); },
    pick(who, g) {
      const female = g === "F" || (g !== "M" && FEMALE_ROLE.test(who));
      let pool = this.list.filter((v) => (female ? FEMALE_V : MALE_V).test(v.name));
      if (!pool.length) pool = this.list;
      const h = hash(who);
      return { voice: pool[h % pool.length], pitch: (female ? 1.02 : 0.9) + ((h >> 5) % 13) / 100 };
    },
    say(item, slot, speed) {
      if (!this.enabled()) return;
      const words = item.text.split(/\s+/).length;
      const fit = Math.min(1.6, Math.max(0.95, words / 2.8 / Math.max(0.7, slot)));
      const u = new SpeechSynthesisUtterance(item.text);
      const p = this.pick(item.who || "", item.g);
      if (p.voice) u.voice = p.voice;
      u.pitch = p.pitch;
      u.rate = Math.min(2.4, fit * speed);
      speechSynthesis.cancel();
      speechSynthesis.speak(u);
    },
    stop() { if ("speechSynthesis" in window) speechSynthesis.cancel(); },
  };


  // ---------------------------------------------------------------- sound: a small trailer score, made in the browser
  // No samples and no outside service. A low drone tightens toward the end, cuts whoosh, the hook lands on a hit, a
  // heartbeat and a riser build into the cut to black, which lands on a "braam", and the script's own sound cues
  // (a door, a slap, thunder, a gasp) get simple synthesized effects.
  const Sound = {
    ctx: null, out: null, noise: null, drone: null, subs: new Set(),
    enabled() { return store.get("tsr-sound", true); },
    set(v) { store.set("tsr-sound", !!v); if (!v) this.stopDrone(true); this.subs.forEach((f) => f()); },
    ready() {
      if (!this.ctx) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return null;
        const ctx = (this.ctx = new AC());
        const comp = ctx.createDynamicsCompressor();
        comp.threshold.value = -16; comp.ratio.value = 4;
        this.out = ctx.createGain(); this.out.gain.value = 0.75;
        this.out.connect(comp); comp.connect(ctx.destination);
        const buf = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate), d = buf.getChannelData(0);
        for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
        this.noise = buf;
      }
      if (this.ctx.state === "suspended") this.ctx.resume();
      return this.ctx;
    },
    env(g, t0, a, peak, hold, rel) {
      g.gain.setValueAtTime(0.0001, t0);
      g.gain.exponentialRampToValueAtTime(peak, t0 + a);
      g.gain.setValueAtTime(peak, t0 + a + hold);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + a + hold + rel);
      return t0 + a + hold + rel;
    },
    burst({ type = "bandpass", f0 = 1000, f1 = 0, q = 1, a = 0.01, peak = 0.3, hold = 0, rel = 0.2, delay = 0 }) {
      if (!this.ctx) return;
      const ctx = this.ctx, t0 = ctx.currentTime + delay;
      const src = ctx.createBufferSource(); src.buffer = this.noise; src.loop = true;
      const f = ctx.createBiquadFilter(); f.type = type; f.Q.value = q; f.frequency.setValueAtTime(f0, t0);
      if (f1) f.frequency.exponentialRampToValueAtTime(f1, t0 + a + hold + rel);
      const g = ctx.createGain(), end = this.env(g, t0, a, peak, hold, rel);
      src.connect(f); f.connect(g); g.connect(this.out);
      src.start(t0, Math.random()); src.stop(end + 0.05);
    },
    tone({ type = "sine", f0 = 110, f1 = 0, a = 0.005, peak = 0.5, hold = 0, rel = 0.4, delay = 0, detune = 0 }) {
      if (!this.ctx) return;
      const ctx = this.ctx, t0 = ctx.currentTime + delay;
      const o = ctx.createOscillator(); o.type = type; o.detune.value = detune; o.frequency.setValueAtTime(f0, t0);
      if (f1) o.frequency.exponentialRampToValueAtTime(f1, t0 + a + hold + rel);
      const g = ctx.createGain(), end = this.env(g, t0, a, peak, hold, rel);
      o.connect(g); g.connect(this.out); o.start(t0); o.stop(end + 0.05);
    },
    whoosh(big) { this.burst({ f0: 320, f1: big ? 3400 : 2300, q: 0.9, a: big ? 0.2 : 0.12, peak: big ? 0.3 : 0.13, rel: big ? 0.35 : 0.22 }); },
    hit() { this.tone({ f0: 118, f1: 34, peak: 0.85, rel: 0.95 }); this.burst({ type: "lowpass", f0: 900, peak: 0.3, rel: 0.12 }); },
    heartbeat() { this.tone({ f0: 64, f1: 46, peak: 0.5, rel: 0.17 }); this.tone({ f0: 58, f1: 43, peak: 0.34, rel: 0.19, delay: 0.21 }); },
    riser(len) {
      this.burst({ f0: 240, f1: 4600, q: 1.3, a: Math.max(0.3, len), peak: 0.2, rel: 0.04 });
      this.tone({ type: "sawtooth", f0: 98, f1: 196, a: Math.max(0.3, len), peak: 0.045, rel: 0.04 });
    },
    braam() {
      if (!this.ctx) return;
      const ctx = this.ctx, t0 = ctx.currentTime;
      const f = ctx.createBiquadFilter(); f.type = "lowpass"; f.Q.value = 2.2;
      f.frequency.setValueAtTime(160, t0); f.frequency.exponentialRampToValueAtTime(1200, t0 + 0.22); f.frequency.exponentialRampToValueAtTime(240, t0 + 2.4);
      const g = ctx.createGain(), end = this.env(g, t0, 0.03, 0.3, 0.55, 1.9);
      f.connect(g); g.connect(this.out);
      [[55, -9], [55, 8], [82.41, -5], [110, 6]].forEach(([hz, det]) => {
        const o = ctx.createOscillator(); o.type = "sawtooth"; o.frequency.value = hz; o.detune.value = det;
        o.connect(f); o.start(t0); o.stop(end + 0.05);
      });
      this.hit();
    },
    sfx(text) {
      const s = String(text).toLowerCase();
      if (/thunder|rumbl|storm|explo|cannon/.test(s)) return this.burst({ type: "lowpass", f0: 150, q: 0.7, a: 0.15, peak: 0.6, hold: 0.4, rel: 1.4 });
      if (/glass|shatter|smash/.test(s)) { this.burst({ type: "highpass", f0: 2600, a: 0.004, peak: 0.36, rel: 0.55 }); return this.tone({ f0: 3100, peak: 0.05, rel: 0.4 }); }
      if (/slap|smack|punch|strike|struck|whip|shove|blow/.test(s)) { this.burst({ f0: 2400, q: 0.8, a: 0.002, peak: 0.6, rel: 0.09 }); return this.tone({ f0: 140, f1: 60, peak: 0.4, rel: 0.15 }); }
      if (/door|slam|knock|bang|thud|crash|fall|drop|thump|collaps/.test(s)) { this.tone({ f0: 92, f1: 44, peak: 0.7, rel: 0.32 }); return this.burst({ type: "lowpass", f0: 600, peak: 0.3, rel: 0.12 }); }
      if (/footstep|step|boot|heel|gallop|hoof|hooves|march/.test(s)) { for (let i = 0; i < 4; i++) this.burst({ f0: 1300, q: 2, a: 0.003, peak: 0.24, rel: 0.05, delay: i * 0.32 }); return; }
      if (/gasp|breath|sigh|sob|inhal|exhal|pant|whimper/.test(s)) return this.burst({ f0: 1100, q: 1.2, a: 0.08, peak: 0.16, rel: 0.26 });
      if (/\bbells?\b|chime|toll/.test(s)) { [660, 1325, 1990].forEach((hz, i) => this.tone({ f0: hz, peak: 0.12 / (i + 1), rel: 2.2 })); return; }
      if (/sword|blade|steel|dagger|unsheath|metal|clang|scrape/.test(s)) { this.burst({ type: "highpass", f0: 4500, a: 0.004, peak: 0.24, rel: 0.6 }); return this.tone({ f0: 2350, peak: 0.06, rel: 0.9 }); }
      if (/rain|wind|gust|howl/.test(s)) return this.burst({ type: "lowpass", f0: 1400, q: 0.5, a: 0.4, peak: 0.12, hold: 0.8, rel: 0.8 });
      if (/fire|crackl|flame/.test(s)) { for (let i = 0; i < 6; i++) this.burst({ type: "highpass", f0: 3000, a: 0.002, peak: 0.04 + Math.random() * 0.1, rel: 0.03, delay: Math.random() * 1.2 }); return; }
      return this.burst({ f0: 800, f1: 1800, q: 0.8, a: 0.08, peak: 0.07, rel: 0.2 });
    },
    startDrone() {
      if (this.drone || !this.ready()) return;
      const ctx = this.ctx, t0 = ctx.currentTime;
      const f = ctx.createBiquadFilter(); f.type = "lowpass"; f.frequency.value = 170; f.Q.value = 3;
      const g = ctx.createGain(); g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(0.06, t0 + 1.2);
      const oscs = [[55, "sawtooth", -6], [55, "sawtooth", 6], [82.41, "triangle", 0], [41.2, "sine", 0]].map(([hz, type, det]) => {
        const o = ctx.createOscillator(); o.type = type; o.frequency.value = hz; o.detune.value = det; o.connect(f); o.start(t0); return o;
      });
      const lfo = ctx.createOscillator(), lg = ctx.createGain();
      lfo.frequency.value = 0.17; lg.gain.value = 38; lfo.connect(lg); lg.connect(f.frequency); lfo.start(t0);
      f.connect(g); g.connect(this.out);
      this.drone = { f, g, oscs: oscs.concat(lfo) };
    },
    updateDrone(tension, duck) {
      const d = this.drone;
      if (!d) return;
      tension = Math.min(1, Math.max(0, Number.isFinite(tension) ? tension : 0));
      const t0 = this.ctx.currentTime;
      d.f.frequency.setTargetAtTime(170 + 650 * Math.pow(tension, 1.6), t0, 0.3);
      d.g.gain.setTargetAtTime((0.05 + 0.09 * tension) * (duck ? 0.45 : 1), t0, 0.2);
    },
    stopDrone(hard) {
      const d = this.drone;
      if (!d) return;
      this.drone = null;
      const t0 = this.ctx.currentTime, rel = hard ? 0.03 : 0.25;
      d.g.gain.cancelScheduledValues(t0); d.g.gain.setValueAtTime(Math.max(0.0001, d.g.gain.value), t0); d.g.gain.linearRampToValueAtTime(0, t0 + rel);
      d.oscs.forEach((o) => o.stop(t0 + rel + 0.05));
    },
  };

  // ---------------------------------------------------------------- the phone player
  function viewerHTML(p) {
    const eps = [...new Set(p.segments.map((s) => s.ep))].join(" + ");
    const D = p.lines, dur = p.dur || 1;
    const pct = (t) => (100 * Math.min(dur, Math.max(0, t)) / dur).toFixed(2);
    const ticks = D.filter((x) => x.k === "dialogue" || x.k === "action").map((x) =>
      `<span class="sc-tick${x.k === "dialogue" ? " d" : ""}${x.hook ? " hk" : ""}${x.cliff ? " cl" : ""}" style="left:${pct(x.t)}%"></span>`).join("");
    const joins = D.filter((x) => x.k === "cut").map((x) => `<span class="sc-join" style="left:${pct(x.t)}%" title="${esc(x.text)}"></span>`).join("");
    const cliff = D.find((x) => x.cliff);
    const cliffLbl = cliff && cliff.t / dur > 0.18 ? `<span class="sc-lbl cl" style="left:${pct(cliff.t)}%">Cliffhanger</span>` : "";
    const next = p.next_ep ? `Watch episode ${p.next_ep}` : "Watch the next episode";
    return `
<div class="tsr-phone" tabindex="0" role="button" aria-label="Play or pause promo number ${p.rank}">
  <div class="ph-scene"><div class="ph-bg"></div><div class="ph-grain"></div><div class="ph-vignette"></div></div>
  <div class="ph-top"><span class="ph-ep">EP ${esc(eps)}</span><span class="ph-ring off"><svg viewBox="0 0 22 22" aria-hidden="true"><circle class="bgc" cx="11" cy="11" r="9"/><circle class="fg" cx="11" cy="11" r="9"/></svg><span>First 3 s</span></span><span class="ph-seg"></span></div><span class="ph-sfx"></span>
  <div class="ph-pic"><p class="ph-pictext"></p></div>
  <div class="ph-badges"><span class="ph-badge hook">The hook</span><span class="ph-badge cliff">Cliffhanger</span></div>
  <div class="ph-cap off"><span class="ph-who"></span><p class="ph-line"></p></div>
  <div class="ph-cutcard"></div>
  <div class="ph-bar"><i></i></div>
  <div class="ph-poster"><span class="pe">Episode ${esc(eps)} · ${mm(dur)}</span><h3>${esc(p.title)}</h3><span class="ph-big"></span><small>Plays from the script, at real speed</small></div>
  <div class="ph-end"><span class="tbc">To be continued…</span><p>${esc(p.title)}</p><span class="ph-cta">${next}</span>
    <small>This is the moment a viewer taps through to the show.</small><button class="ph-replay" type="button">Replay</button></div>
</div>
<div class="tsr-scrub" role="slider" tabindex="0" aria-label="Seek within the promo" aria-valuemin="0" aria-valuemax="${dur.toFixed(1)}" aria-valuenow="0">
  <div class="sc-track"><span class="sc-hook" style="width:${pct(3)}%"></span>${joins}<span class="sc-fill"></span>${ticks}<span class="sc-knob"></span></div>
  <span class="sc-lbl hk">Hook 3 s</span>${cliffLbl}
</div>
<div class="tsr-ctrls">
  <button class="tsr-play" type="button" aria-label="Play"></button>
  <span class="tsr-time"><b>0:00</b> <span>/ ${mm(dur)}</span></span><span class="tsr-grow"></span>
  <button class="tsr-pill tsr-rate" type="button" title="Playback speed">1×</button>
  <button class="tsr-ico tsr-sound" type="button" aria-pressed="true" aria-label="Score and sound effects"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 7.5h3l4.2-3.2v11.4L6 12.5H3z" fill="currentColor"/><path class="on" d="M13.2 7.4a3.6 3.6 0 0 1 0 5.2M15.4 5.2a6.8 6.8 0 0 1 0 9.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path class="off" d="M13 7.5l4.6 5M17.6 7.5L13 12.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>
  <button class="tsr-ico tsr-voice" type="button" aria-pressed="false" hidden aria-label="Read the lines aloud"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4.2 4h11.6c.9 0 1.7.8 1.7 1.7v6.6c0 .9-.8 1.7-1.7 1.7H9.4L5.6 17v-3H4.2c-.9 0-1.7-.8-1.7-1.7V5.7C2.5 4.8 3.3 4 4.2 4z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path class="on" d="M6.4 8h7.2M6.4 10.6h4.6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path class="off" d="M3 3l14 14" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>
</div>`;
  }

  function Player(root, p, hooks) {
    const $ = (s) => root.querySelector(s);
    const D = p.lines, dur = p.dur || 1;
    const phone = $(".tsr-phone"), bg = $(".ph-bg"), pic = $(".ph-pictext"), segEl = $(".ph-seg"), sfxEl = $(".ph-sfx");
    const cuts = D.filter((x) => x.k === "cut");
    const cap = $(".ph-cap"), whoEl = $(".ph-who"), lineEl = $(".ph-line"), bHook = $(".ph-badge.hook"), bCliff = $(".ph-badge.cliff");
    const cutEl = $(".ph-cutcard"), ring = $(".ph-ring"), ringFg = $(".ph-ring .fg"), bar = $(".ph-bar i");
    const scrub = $(".tsr-scrub"), fill = $(".sc-fill"), knob = $(".sc-knob"), playBtn = $(".tsr-play"), timeEl = $(".tsr-time b");
    const rateBtn = $(".tsr-rate"), voiceBtn = $(".tsr-voice"), soundBtn = $(".tsr-sound");
    const snd = () => !hooks.mute && Sound.enabled() && !!Sound.ready();
    const hookItem = D.find((x) => x.hook), cliffItem = D.find((x) => x.cliff);
    const stops = D.filter((x) => x.k === "dialogue" || x.k === "action" || x.k === "cut").map((x) => x.t);
    let t = 0, on = false, last = 0, rate = 1, raf = 0, started = false;
    let curShot, curPic, curLine, curWords = -1, curIdx = -1, lastDrone = 0;
    // sound cues on the promo's own timeline: whooshes on new shots, a hit on the hook and on joins, the script's
    // sound effects, and a heartbeat then a riser into the cut to black (the braam itself plays in end())
    const cues = [];
    D.forEach((x) => {
      if (x.k === "camera" && x.t > 0.3) cues.push({ t: x.t, fn: () => Sound.whoosh(false) });
      if (x.k === "cut") cues.push({ t: x.t, fn: () => { Sound.whoosh(true); Sound.hit(); } });
      if (x.k === "sfx") cues.push({ t: x.t, fn: () => Sound.sfx(x.text) });
    });
    if (hookItem) cues.push({ t: hookItem.t, fn: () => Sound.hit() });
    for (let b = Math.max(1, dur - 8); b < dur - 2.4; b += 0.8) cues.push({ t: b, fn: () => Sound.heartbeat() });
    cues.push({ t: Math.max(0.2, dur - 2.2), fn: () => Sound.riser(Math.min(2.2, dur) / rate) });
    cues.sort((a, b) => a.t - b.t);
    const fire = (a, b) => { for (const c of cues) if (c.t > a && c.t <= b) c.fn(); };

    const nextStart = (x) => { for (let j = D.indexOf(x) + 1; j < D.length; j++) if (D[j].k === "dialogue" || D[j].k === "cut") return D[j].t; return dur; };

    function kenBurns(go) {
      const cur = getComputedStyle(bg).transform;
      bg.style.transition = "none";
      bg.style.transform = go ? "scale(1)" : (cur === "none" ? "scale(1)" : cur);
      void bg.offsetWidth;
      bg.style.transition = "";
      if (go) bg.style.transform = "scale(1.12)";
    }

    function draw() {
      let shot = null, act = null, line = null, sfx = null, cut = null, idx = -1;
      for (let i = 0; i < D.length; i++) {
        const x = D[i];
        if (x.t > t + 0.001) break;
        if (x.k !== "cut") idx = i;
        if (x.k === "camera") shot = x;
        else if (x.k === "cut") { shot = null; act = null; line = null; if (t < x.t + 0.75) cut = x; }
        else if (x.k === "action" && t < x.e + 1.6) act = x;
        else if (x.k === "dialogue" && t < Math.max(x.e + 0.5, Math.min(x.e + 1.4, nextStart(x)))) line = x;
        else if (x.k === "sfx" && t < x.e + 0.9) sfx = x;
      }
      // the picture: what happens, else what the camera sees; a new shot is a cut
      if (shot !== curShot) {
        curShot = shot;
        const h = hash(shot ? shot.text : p.id);
        bg.style.setProperty("--h", HUES[h % HUES.length]);
        bg.style.setProperty("--x", 22 + (h >> 3) % 56 + "%");
        bg.style.setProperty("--y", 14 + (h >> 9) % 40 + "%");
        if (on) { bg.classList.remove("cut"); void bg.offsetWidth; bg.classList.add("cut"); kenBurns(true); }
      }
      const pi = act || shot;
      if (pi !== curPic) {
        curPic = pi;
        pic.classList.add("fade");
        pic.textContent = pi ? pi.text : "";
        pic.classList.toggle("act", !!pi && pi === act);
        requestAnimationFrame(() => pic.classList.remove("fade"));
      }
      pic.classList.toggle("is-hook", !!act && !!act.hook);
      pic.classList.toggle("is-cliff", !!act && !!act.cliff && t >= act.t);
      // captions, word by word at the line's own pace
      if (line !== curLine) {
        curLine = line; curWords = -1;
        if (line) {
          whoEl.innerHTML = line.who ? esc(line.who) + (line.how ? ` <em>· ${esc(line.how)}</em>` : "") : "";
          whoEl.style.display = line.who ? "" : "none";
          lineEl.innerHTML = line.text.split(/\s+/).map((w) => `<span class="w">${esc(w)}</span>`).join(" ");
          cap.classList.remove("off");
          cap.classList.toggle("is-hook", !!line.hook);
          cap.classList.toggle("is-cliff", !!line.cliff);
          if (on && t - line.t < 0.5 && !hooks.mute) Voices.say(line, nextStart(line) - line.t, rate);
        } else cap.classList.add("off");
      }
      if (line) {
        const ws = lineEl.children, n = ws.length, span = Math.max(0.3, (line.e - line.t) * 0.92);
        const k = on || t > line.t ? Math.min(n, Math.floor(((t - line.t) / span) * n) + 1) : n;
        if (k !== curWords) { curWords = k; for (let i = 0; i < n; i++) ws[i].classList.toggle("on", i < k); }
      }
      sfxEl.textContent = sfx ? "[" + sfx.text.replace(/\.$/, "") + "]" : "";
      sfxEl.classList.toggle("on", !!sfx);
      if (cuts.length) segEl.textContent = (cuts.filter((c) => c.t <= t + 0.001).length + 1) + " / " + (cuts.length + 1);
      cutEl.textContent = cut ? cut.text : "";
      cutEl.classList.toggle("on", !!cut);
      bHook.classList.toggle("on", !!hookItem && t >= hookItem.t && t < hookItem.e + 0.9);
      bCliff.classList.toggle("on", !!cliffItem && t >= cliffItem.t);
      ring.classList.toggle("off", !started || t >= 3);
      ringFg.style.strokeDashoffset = (56.55 * Math.max(0, 1 - t / 3)).toFixed(2);
      const pc = (100 * t / dur).toFixed(2) + "%";
      bar.style.width = pc; fill.style.width = pc; knob.style.left = pc;
      timeEl.textContent = mm(t);
      scrub.setAttribute("aria-valuenow", t.toFixed(1));
      if (idx !== curIdx) { curIdx = idx; hooks.onLine && hooks.onLine(idx, on); }
      if (on && !hooks.mute && Sound.drone && performance.now() - lastDrone > 120) { lastDrone = performance.now(); Sound.updateDrone(t / dur, !!line); }
    }

    function frame(now) {
      if (!on) return;
      const t0 = t;
      t = Math.max(0, t + (Math.max(0, now - last) / 1000) * rate); last = now;   // a frame can start just before play()
      if (snd()) fire(t0, Math.min(t, dur));
      if (t >= dur) { t = dur; draw(); end(); return; }
      draw();
      raf = requestAnimationFrame(frame);
    }
    function play() {
      if (on) return;
      if (t >= dur - 0.05) { t = 0; curLine = undefined; }
      started = true; on = true;
      phone.classList.add("started", "playing"); phone.classList.remove("ended");
      playBtn.classList.add("on"); playBtn.setAttribute("aria-label", "Pause");
      curLine = undefined; kenBurns(true);
      last = performance.now();
      hooks.onPlay && hooks.onPlay();
      if (snd()) { Sound.startDrone(); if (t < 0.05) fire(-1, 0.05); }
      raf = requestAnimationFrame(frame);
    }
    function pause() {
      on = false; cancelAnimationFrame(raf);
      phone.classList.remove("playing"); playBtn.classList.remove("on"); playBtn.setAttribute("aria-label", "Play");
      kenBurns(false); Voices.stop(); if (!hooks.mute) Sound.stopDrone(false);
    }
    function end() { const s = snd(); pause(); if (s) Sound.braam(); phone.classList.add("ended"); hooks.onEnd && hooks.onEnd(); }
    function seek(nt, keepPlaying = true) {
      t = Math.max(0, Math.min(dur - 0.01, nt));
      phone.classList.remove("ended");
      if (!started) { started = true; phone.classList.add("started"); }
      curLine = undefined; Voices.stop();
      draw();
      if (!keepPlaying) pause();
    }
    const toggle = () => (on ? pause() : play());
    const step = (dir) => {
      const target = dir > 0 ? stops.find((s) => s > t + 0.15) : [...stops].reverse().find((s) => s < t - 0.8);
      seek(target ?? (dir > 0 ? dur - 0.02 : 0));
    };

    phone.addEventListener("click", (e) => {
      if (e.target.closest(".ph-replay")) { seek(0); play(); return; }
      toggle();
    });
    phone.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); toggle(); } });
    playBtn.addEventListener("click", toggle);
    rateBtn.addEventListener("click", () => {
      rate = rate === 1 ? 1.5 : rate === 1.5 ? 2 : 1;
      rateBtn.textContent = rate + "×";
      if (on) Voices.stop();
    });
    const paintVoice = () => {
      voiceBtn.hidden = !Voices.ready;
      voiceBtn.setAttribute("aria-pressed", String(Voices.enabled()));
      voiceBtn.title = Voices.enabled() ? "Voices on: lines read aloud by your device's own voices" : "Voices off (click to read the lines aloud)";
    };
    voiceBtn.addEventListener("click", () => { Voices.set(!Voices.enabled()); paintVoice(); });
    Voices.subs.add(paintVoice); paintVoice();
    const paintSound = () => {
      soundBtn.setAttribute("aria-pressed", String(Sound.enabled()));
      soundBtn.title = Sound.enabled() ? "Sound on: a trailer-style score and effects, made in your browser" : "Sound off";
    };
    soundBtn.addEventListener("click", () => {
      Sound.set(!Sound.enabled());
      if (Sound.enabled() && on && !hooks.mute) Sound.startDrone();
    });
    Sound.subs.add(paintSound); paintSound();
    // scrubbing: drag anywhere on the bar
    const at = (e) => { const r = scrub.getBoundingClientRect(); return ((e.clientX - r.left) / r.width) * dur; };
    let dragging = false, wasOn = false;
    scrub.addEventListener("pointerdown", (e) => { dragging = true; wasOn = on; if (on) pause(); scrub.setPointerCapture(e.pointerId); seek(at(e)); });
    scrub.addEventListener("pointermove", (e) => { if (dragging) seek(at(e)); });
    const release = () => { if (!dragging) return; dragging = false; if (wasOn) play(); };
    scrub.addEventListener("pointerup", release);
    scrub.addEventListener("pointercancel", release);
    scrub.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); e.stopPropagation(); step(e.key === "ArrowRight" ? 1 : -1); }
    });

    draw();
    return {
      play, pause, toggle, seek, step,
      get playing() { return on; },
      destroy() { pause(); Voices.subs.delete(paintVoice); Sound.subs.delete(paintSound); },
    };
  }

  // ---------------------------------------------------------------- the desk
  const CHECK = {
    stranger: (s) => !s ? ["", "Not shown to a stranger"] : s.passed ? ["ok", "A stranger followed it"]
      : s.passed === false ? ["bad", "A stranger got lost"] : ["", "Not shown to a stranger"],
    policy: (pol) => ({ CLEAR: ["ok", "Clear to run as an ad"],
      CHECK: ["warn", "Ad check" + (pol.rules.length ? ": " + pol.rules.join(", ") : "")],
      HOLD: ["bad", "Hold as an ad" + (pol.rules.length ? ": " + pol.rules.join(", ") : "")] }[pol.status] || ["", "Ad policy not checked"]),
    cut: (c) => ({ high: ["ok", "Clean cuts"], medium: ["warn", "Tight cut, trim with care"], low: ["bad", "Risky cut"] }[c]),
  };
  const REASONS = ["weak hook", "not a cliffhanger", "needs context", "spoiler", "bad cut", "policy", "duplicate"];
  const VERB = { accept: "Accepted", nudge: "Nudged", reject: "Rejected" };
  const MARK = { accept: "✓", nudge: "±", reject: "✕" };

  function mount(root, view, opts) {
    opts = opts || {};
    Voices.init();
    const P = view.promos || [];
    const byId = Object.fromEntries(P.map((p) => [p.id, p]));
    const firsts = P.filter((p) => p.first);
    const KEY = "tsr:" + view.fp;
    let S = store.get(KEY, null) || { d: {}, t0: null };
    let filter = "all", selected = P.length ? P[0].id : null, player = null, userPlayed = false, tab = "script", deskVisible = false;
    const passed = P.filter((p) => p.stranger && p.stranger.passed).length;
    const flagged = P.filter((p) => p.flags.length).length;
    const pol = { CLEAR: 0, CHECK: 0, HOLD: 0 };
    P.forEach((p) => { if (p.policy.status in pol) pol[p.policy.status]++; });

    const downloads = (opts.downloads || []).length
      ? `<details class="tsr-dl"><summary class="tsr-btn">Download<span class="chev" aria-hidden="true"></span></summary><div class="tsr-menu">${opts.downloads.map((d) =>
          `<a href="${esc(d.href)}" download="${esc(d.name || "")}"><b>${esc(d.label)}</b>${d.desc ? `<small>${esc(d.desc)}</small>` : ""}</a>`).join("")}</div></details>` : "";
    const lede = P.length
      ? `Thumbstop read all ${view.episodes} episodes and found <b>${P.length} promos</b>, best first.` +
        (firsts.length ? ` <b>Post these ${firsts.length} first</b>, then work down the list.` : "") +
        ` Press play to watch each one run from the script, then accept, nudge or reject it.`
      : "No clip passed the hard rules (length, a hook in the first 3 seconds, a cliffhanger at the end, no cut in the middle of a line). Try a wider length range or more episodes.";

    root.innerHTML = `
<section class="tsr">
  <header class="tsr-head">
    <div class="tsr-head-top">
      <div>${opts.standalone ? `<div class="tsr-brand"><span class="tsr-mark"></span><b>Thumbstop</b><span>· promo review</span></div>` : `<div class="tsr-eyebrow">Results</div>`}
        <h1>${esc(view.show)}</h1></div>
      ${downloads}
    </div>
    <p class="tsr-lede">${lede}</p>
    ${P.length ? `<dl class="tsr-kpis">
      <div><dt>Promos</dt><dd>${P.length}</dd></div>
      <div><dt>Post first</dt><dd>${firsts.length}</dd></div>
      <div><dt>A stranger followed</dt><dd>${passed}<span>/${P.length}</span></dd></div>
      <div><dt>With a warning</dt><dd>${flagged}</dd></div>
      <div><dt>As an ad</dt><dd class="pol"><span class="ok" title="clear">${pol.CLEAR}</span><span class="warn" title="check">${pol.CHECK}</span><span class="bad" title="hold">${pol.HOLD}</span></dd><small>clear · check · hold</small></div>
    </dl>
    <p class="tsr-note">Timecodes are at ${view.fps} fps from the script timestamps, so confirm them on picture.</p>` : ""}
  </header>
  ${firsts.length ? `<section class="tsr-first">
    <div class="tsr-sec"><div><div class="tsr-eyebrow">Start here</div><h2>Post these first</h2></div>
      <p>Early episodes, no spoilers and nothing on hold, so a new viewer reaches the payoff fast.</p></div>
    <div class="tsr-first-grid">${firsts.map((p) => `
      <button class="tsr-fcard" type="button" data-pick="${esc(p.id)}">
        <span class="tsr-mini"><span class="rk">#${p.rank}</span><span class="tri"></span><span class="len">${mm(p.dur)}</span></span>
        <span><h3>${esc(p.title)}</h3><span class="where">${esc(p.where)}</span>
        <p class="why">${esc(p.reason ? p.reason[0].toUpperCase() + p.reason.slice(1) + "." : p.why)}</p></span>
      </button>`).join("")}</div>
  </section>` : ""}
  ${P.length ? `<section class="tsr-desk">
    <div class="tsr-sec"><div><div class="tsr-eyebrow">All ${P.length}, best first</div><h2>Review every promo</h2></div>
      <div class="tsr-keys"><span><span class="tsr-kbd">Space</span> play</span><span><span class="tsr-kbd">←</span><span class="tsr-kbd">→</span> line</span>
        <span><span class="tsr-kbd">J</span><span class="tsr-kbd">K</span> promo</span><span><span class="tsr-kbd">A</span><span class="tsr-kbd">N</span><span class="tsr-kbd">R</span> decide</span></div></div>
    <div class="tsr-deskbar">
      <div class="tsr-filters" role="group" aria-label="Show"></div>
      <div class="tsr-progress"><span data-prog><b data-done>0</b>/${P.length} reviewed</span><span class="tsr-meter"><i></i></span><span>Time <b data-timer>0:00</b></span><span class="tsr-sum" data-sum></span></div>
      <span class="tsr-grow"></span>
      <button class="tsr-btn" type="button" data-export>Export decisions (CSV)</button>
      <button class="tsr-btn ghost" type="button" data-reset>Reset</button>
    </div>
    <div class="tsr-grid">
      <div class="tsr-qcol"><div class="tsr-legend"><span><i class="tsr-dot"></i>stranger</span><span><i class="tsr-dot"></i>ad check</span><span><i class="tsr-dot"></i>cut</span></div>
      <ol class="tsr-queue">${P.map((p) => `<li><button class="tsr-row" type="button" data-id="${esc(p.id)}">
        <span class="rk">#${p.rank}</span>
        <span><span class="q">${esc(p.title)}</span><span class="m">${p.first ? '<span class="first">Post first</span>' : ""}<span>Ep ${[...new Set(p.segments.map((s) => s.ep))].join("+")} · ${Math.round(p.dur)} s</span>
          <span class="tsr-dots">${[CHECK.stranger(p.stranger), CHECK.policy(p.policy), CHECK.cut(p.cut)].map(([c, t]) => `<i class="tsr-dot ${c}" title="${esc(t)}"></i>`).join("")}</span>${p.flags.length ? "<span title='Has a warning'>⚠</span>" : ""}</span></span>
        <span class="tsr-state" aria-hidden="true"></span></button></li>`).join("")}</ol></div>
      <div class="tsr-stage" aria-live="polite"><div class="tsr-stage-in"></div></div>
    </div>
  </section>` : ""}
  <div class="tsr-toast" role="status" aria-live="polite"></div>
</section>`;

    const $ = (s) => root.querySelector(s), $$ = (s) => [...root.querySelectorAll(s)];
    const desk = $(".tsr-desk"), stageIn = $(".tsr-stage-in"), toastEl = $(".tsr-toast");
    const save = () => store.set(KEY, S);
    const startTimer = () => { if (!S.t0) { S.t0 = Date.now(); save(); } };
    let toastT = 0;
    const toast = (msg) => { toastEl.textContent = msg; toastEl.classList.add("on"); clearTimeout(toastT); toastT = setTimeout(() => toastEl.classList.remove("on"), 1800); };
    const visible = () => P.filter((p) => filter === "all" || (filter === "first" && p.first) || (filter === "todo" && !S.d[p.id]) || (filter === "flag" && p.flags.length));

    function paintQueue() {
      if (!desk) return;
      const counts = { all: P.length, first: firsts.length, todo: P.filter((p) => !S.d[p.id]).length, flag: flagged };
      $(".tsr-filters").innerHTML = [["all", "All"], ["first", "Post first"], ["todo", "To review"], ["flag", "Warnings"]]
        .map(([k, l]) => `<button type="button" data-f="${k}" aria-pressed="${filter === k}">${l}<span>${counts[k]}</span></button>`).join("");
      const show = new Set(visible().map((p) => p.id));
      $$(".tsr-row").forEach((r) => {
        const d = S.d[r.dataset.id];
        r.parentElement.hidden = !show.has(r.dataset.id) && r.dataset.id !== selected;
        r.setAttribute("aria-current", String(r.dataset.id === selected));
        const st = r.querySelector(".tsr-state");
        st.className = "tsr-state" + (d ? " " + d.v : "");
        st.textContent = d ? MARK[d.v] : "";
      });
      const done = Object.keys(S.d).filter((k) => byId[k]).length;
      const n = { accept: 0, nudge: 0, reject: 0 };
      Object.entries(S.d).forEach(([k, d]) => { if (byId[k]) n[d.v]++; });
      $("[data-prog]").innerHTML = done === P.length ? `<b>All ${P.length}</b> reviewed` : `<b data-done>${done}</b>/${P.length} reviewed`;
      $("[data-sum]").innerHTML = done ? `<i class="ok">${n.accept} accepted</i><i class="warn">${n.nudge} nudged</i><i class="bad">${n.reject} rejected</i>` : "";
      $(".tsr-meter i").style.width = (100 * done) / P.length + "%";
      $("[data-export]").classList.toggle("primary", done === P.length);
      desk.classList.toggle("complete", done === P.length);
    }

    function tick() { const e = $("[data-timer]"); if (e) e.textContent = S.t0 ? mm((Date.now() - S.t0) / 1000) : "0:00"; }
    const timer = setInterval(tick, 1000);

    function linesHTML(p) {
      return `<ol class="tsr-lines">${p.lines.map((x, i) => x.k === "cut" ? `<li class="cut">${esc(x.text)}</li>` :
        `<li class="${x.k}${x.hook ? " hl-hook" : ""}${x.cliff ? " hl-cliff" : ""}" data-i="${i}" data-t="${x.t}"><time>${mm(x.t)}</time><span>` +
        (x.k === "dialogue" ? `<span class="who">${esc(x.who)}</span>${x.how ? `<span class="how">${esc(x.how)}</span>` : ""}`
          : `<span class="k">${{ camera: "Shot", action: "Action", sfx: "Sound" }[x.k] || x.k}</span>`) +
        (x.hook ? '<span class="mk h">Hook</span>' : "") + (x.cliff ? '<span class="mk c">Cliffhanger</span>' : "") +
        `<span class="tx">${esc(x.text)}</span></span></li>`).join("")}</ol>`;
    }
    function cutHTML(p) {
      return `<p><button class="tsr-btn" type="button" data-copy>Copy timecodes</button></p>` + p.segments.map((s) => `
        <div class="tsr-seg"><div class="tsr-seg-h"><b>Segment ${s.seg}</b><span>EP${String(s.ep).padStart(3, "0")}</span>
          <span>${esc(s.in_tc)} → ${esc(s.out_tc)}</span><span>${s.dur.toFixed(1)} s</span></div>
          <div class="tsr-lane" title="Where this cut sits in episode ${s.ep}"><i style="left:${(100 * s.a / s.ep_dur).toFixed(2)}%;width:${(100 * (s.b - s.a) / s.ep_dur).toFixed(2)}%"></i></div>
          <div class="tsr-lane-l"><span>Episode ${s.ep} · 0:00</span><span>${mm(s.a)} → ${mm(s.b)}</span><span>${mm(s.ep_dur)}</span></div>
          <dl class="tsr-cuts"><dt>Start on</dt><dd>${esc(s.in_on)}${s.in_visual ? `<small>${esc(s.in_visual)}</small>` : ""}</dd>
            <dt>End on</dt><dd>${esc(s.out_on)}</dd>${s.stop_before ? `<dt>Stop before</dt><dd>${esc(s.stop_before)}</dd>` : ""}</dl>
          <p class="tsr-safe">Cut safety <b class="${{ high: "ok", medium: "warn", low: "bad" }[s.conf]}">${s.conf}</b> · ${s.gap_in} s quiet before the in point, ${s.gap_out} s after the out point · handles −${s.h_pre} / +${s.h_post} s</p>
        </div>`).join("");
    }
    function strangerHTML(p) {
      const s = p.stranger;
      if (!s) return "<p>This promo was not shown to a stranger.</p>";
      return `<p><b>What they understood.</b> ${esc(s.premise || "Not recorded.")}</p>
        <p><b>They're left asking.</b> ${esc(s.question || "-")}${s.intent != null ? ` <span class="mono">(matches the intended question ${s.intent}/3)</span>` : ""}</p>
        <p><b>Lost on.</b> ${esc((s.lost || []).join(", ") || "nothing")}</p>
        <p class="tsr-note">The stranger is a different AI model that only sees this clip's text, with the names hidden.</p>`;
    }
    function adHTML(p) {
      const [, label] = CHECK.policy(p.policy);
      const ev = p.policy.evidence || [];
      return `<p><b>${esc(label)}.</b> The check reads only the script text, so a person decides anything flagged.</p>` +
        (ev.length ? `<ol class="tsr-lines">${ev.map((e) => `<li><time>${esc(e.level)}</time><span><span class="k">${esc(e.rule)}</span><span class="tx">${esc(e.text)}</span></span></li>`).join("")}</ol>`
          : "<p>Nothing in the script was flagged.</p>");
    }
    const PANELS = { script: ["Script", linesHTML], cut: ["Cut sheet", cutHTML], stranger: ["The stranger", strangerHTML], ad: ["Ad check", adHTML] };

    function renderPanel(p) {
      const panel = stageIn.querySelector(".tsr-panel");
      panel.innerHTML = PANELS[tab][1](p);
      panel.scrollTop = 0;
      stageIn.querySelectorAll(".tsr-tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
    }

    function paintDecided() {
      const d = S.d[selected], el = stageIn.querySelector(".tsr-decided");
      if (!el) return;
      el.hidden = !d;
      if (d) {
        el.className = "tsr-decided " + d.v;
        el.textContent = VERB[d.v] + (d.why ? " · " + d.why : "") + (d.v === "nudge" && (d.nin || d.nout) ? ` · in ${d.nin || 0} s, out ${d.nout || 0} s` : "");
      }
      stageIn.querySelectorAll(".tsr-dbtn").forEach((b) => b.setAttribute("aria-pressed", String(!!d && d.v === b.dataset.v)));
    }

    function select(id, o) {
      o = o || {};
      const p = byId[id];
      if (!p || !stageIn) return;
      if (player) player.destroy();
      selected = id;
      const hook = p.lines.find((x) => x.hook), cliff = p.lines.find((x) => x.cliff);
      const tags = [p.hook_type && "Hook: " + p.hook_type, p.cliff_type && "Ending: " + p.cliff_type, p.rank_reason].filter(Boolean);
      stageIn.innerHTML = `
        <div class="tsr-viewer">${viewerHTML(p)}</div>
        <div class="tsr-info">
          <div class="tsr-meta"><span class="tsr-rank">#${p.rank}</span>${p.first ? '<span class="tsr-chip">Post first</span>' : ""}<span>${esc(p.where)}</span><span>${Math.round(p.dur)} s</span><span class="tsr-decided" hidden></span></div>
          <h2 class="tsr-title">${esc(p.title)}</h2>
          <div class="tsr-moments">
            <button class="tsr-moment hook" type="button" data-seek="${hook ? hook.t : 0}"><span class="lb"><b>The hook</b><i>${mm(hook ? hook.t : 0)} ▸</i></span><p>${esc(p.hook)}</p></button>
            <button class="tsr-moment cliff" type="button" data-seek="${cliff ? Math.max(0, cliff.t - 2) : Math.max(0, p.dur - 5)}"><span class="lb"><b>The cliffhanger</b><i>${mm(cliff ? cliff.t : p.dur)} ▸</i></span><p>${esc(p.cliff)}</p></button>
          </div>
          <p class="tsr-why"><b>Why it works.</b> ${esc(p.why)}</p>
          <div class="tsr-checks">${[CHECK.stranger(p.stranger), CHECK.policy(p.policy), CHECK.cut(p.cut)].map(([c, t]) => `<span class="tsr-check"><i class="tsr-dot ${c}"></i>${esc(t)}</span>`).join("")}</div>
          ${p.flags.map((f) => `<p class="tsr-flag">⚠ ${esc(f)}</p>`).join("")}
          ${tags.length ? `<p class="tsr-note" style="margin-top:10px">${esc(tags.join(" · "))}</p>` : ""}
          <div class="tsr-tabs" role="tablist">${Object.entries(PANELS).map(([k, [l]]) => `<button type="button" role="tab" data-tab="${k}">${l}</button>`).join("")}</div>
          <div class="tsr-panel" role="tabpanel"></div>
          <div class="tsr-decide">
            <div class="tsr-decide-row">
              <button class="tsr-dbtn accept" type="button" data-v="accept">Accept <span class="tsr-kbd">A</span></button>
              <button class="tsr-dbtn nudge" type="button" data-v="nudge">Nudge <span class="tsr-kbd">N</span></button>
              <button class="tsr-dbtn reject" type="button" data-v="reject">Reject <span class="tsr-kbd">R</span></button>
              <span class="tsr-nav"><button type="button" data-prev aria-label="Previous promo" title="Previous (K)">↑</button><button type="button" data-next aria-label="Next promo" title="Next (J)">↓</button></span>
            </div>
            <div class="tsr-extra" data-extra="nudge"><span>Move the cut by</span>
              <label>start <input type="number" step="0.1" data-nin value="${esc((S.d[id] || {}).nin || 0)}"> s</label>
              <label>end <input type="number" step="0.1" data-nout value="${esc((S.d[id] || {}).nout || 0)}"> s</label>
              <button class="tsr-btn primary" type="button" data-save-nudge>Save</button></div>
            <div class="tsr-extra" data-extra="reject"><span>Why?</span>${REASONS.map((r) => `<button class="tsr-reason" type="button" data-why="${r}">${r}</button>`).join("")}
              <button class="tsr-reason" type="button" data-why="">skip</button></div>
          </div>
        </div>`;
      stageIn.classList.remove("swap"); void stageIn.offsetWidth; stageIn.classList.add("swap");
      player = Player(stageIn.querySelector(".tsr-viewer"), p, {
        onPlay() { userPlayed = true; startTimer(); },
        onLine(i, playing) {
          if (tab !== "script") return;
          const panel = stageIn.querySelector(".tsr-panel");
          panel.querySelectorAll("li.now").forEach((x) => x.classList.remove("now"));
          const li = panel.querySelector(`li[data-i="${i}"]`);
          if (li) {
            li.classList.add("now");
            if (playing) { const top = li.offsetTop - panel.offsetTop; if (top < panel.scrollTop || top > panel.scrollTop + panel.clientHeight - 60) panel.scrollTop = top - 40; }
          }
        },
      });
      renderPanel(p);
      paintDecided();
      paintQueue();
      if (o.scroll) (window.matchMedia("(max-width: 980px)").matches ? $(".tsr-stage") : $(".tsr-grid")).scrollIntoView({ behavior: "smooth", block: "start" });
      if (o.autoplay) player.play();
    }

    function move(dir, onlyTodo) {
      const list = visible().length ? visible() : P;
      let i = list.findIndex((p) => p.id === selected);
      for (let n = 0; n < list.length; n++) {
        i = (i + dir + list.length) % list.length;
        if (!onlyTodo || !S.d[list[i].id]) return select(list[i].id, { autoplay: userPlayed });
      }
      if (onlyTodo) toast("All reviewed. Export your decisions when you're ready.");
    }

    function decide(v, extra) {
      extra = extra || {};
      startTimer();
      S.d[selected] = { v, nin: extra.nin ?? "", nout: extra.nout ?? "", why: extra.why ?? "", at: Math.round((Date.now() - S.t0) / 1000) };
      save();
      const p = byId[selected];
      toast(`${VERB[v]} #${p.rank}`);
      paintQueue(); paintDecided();
      setTimeout(() => move(1, true), 260);
    }

    function showExtra(kind) {
      stageIn.querySelectorAll(".tsr-extra").forEach((x) => x.classList.toggle("on", x.dataset.extra === kind));
      if (kind === "nudge") stageIn.querySelector("[data-nin]").focus();
    }

    // ---------------------------------------------------------------- events
    root.addEventListener("click", (e) => {
      const dl = root.querySelector(".tsr-dl[open]");
      if (dl && (!dl.contains(e.target) || e.target.closest(".tsr-menu a"))) dl.open = false;
      const b = e.target.closest("button");
      if (!b || !root.contains(b)) return;
      if (b.dataset.pick) return select(b.dataset.pick, { autoplay: true, scroll: true });
      if (b.classList.contains("tsr-row")) return select(b.dataset.id, { autoplay: userPlayed, scroll: window.matchMedia("(max-width: 980px)").matches });
      if (b.dataset.f) { filter = b.dataset.f; return paintQueue(); }
      if (b.dataset.tab) { tab = b.dataset.tab; return renderPanel(byId[selected]); }
      if (b.dataset.seek !== undefined) { player.seek(+b.dataset.seek); return player.play(); }
      if (b.dataset.v === "accept") return decide("accept");
      if (b.dataset.v === "nudge") return showExtra("nudge");
      if (b.dataset.v === "reject") return showExtra("reject");
      if (b.dataset.saveNudge !== undefined) return decide("nudge", { nin: stageIn.querySelector("[data-nin]").value, nout: stageIn.querySelector("[data-nout]").value });
      if (b.dataset.why !== undefined) return decide("reject", { why: b.dataset.why });
      if (b.dataset.prev !== undefined) return move(-1);
      if (b.dataset.next !== undefined) return move(1);
      if (b.dataset.copy !== undefined) {
        const p = byId[selected];
        const txt = p.segments.map((s) => `EP${String(s.ep).padStart(3, "0")}  ${s.in_tc} -> ${s.out_tc}  (${s.dur.toFixed(1)} s)`).join("\n");
        (navigator.clipboard ? navigator.clipboard.writeText(txt) : Promise.reject()).then(() => toast("Timecodes copied"), () => toast(txt));
        return;
      }
      if (b.dataset.export !== undefined) {
        const q = (v) => { v = String(v ?? ""); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; };
        const rows = [["promo_id", "decision", "nudge_in_s", "nudge_out_s", "reason", "seconds_since_start"]];
        P.forEach((p) => { const d = S.d[p.id]; if (d) rows.push([p.id, d.v, d.nin, d.nout, d.why, d.at]); });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob([rows.map((r) => r.map(q).join(",")).join("\n") + "\n"], { type: "text/csv" }));
        a.download = "review_decisions.csv"; a.click();
        return toast(`Exported ${rows.length - 1} decisions`);
      }
      if (b.dataset.reset !== undefined) {
        if (confirm("Clear every decision and the timer?")) { S = { d: {}, t0: null }; save(); paintQueue(); paintDecided(); tick(); }
      }
    });
    root.addEventListener("keydown", (e) => {
      if (e.target.matches && e.target.matches("[data-nin], [data-nout]") && e.key === "Enter") {
        e.preventDefault(); stageIn.querySelector("[data-save-nudge]").click();
      }
    });
    const typing = (el) => el && (el.isContentEditable || /^(input|select|textarea)$/i.test(el.tagName));
    function onKey(e) {
      if (!desk || !deskVisible || e.metaKey || e.ctrlKey || e.altKey || typing(e.target) || !player) return;
      const k = e.key.toLowerCase();
      if (k === " ") { e.preventDefault(); player.toggle(); }
      else if (k === "arrowright" || k === "arrowleft") { e.preventDefault(); player.step(k === "arrowright" ? 1 : -1); }
      else if (k === "j") move(1);
      else if (k === "k") move(-1);
      else if (k === "a") decide("accept");
      else if (k === "n") showExtra("nudge");
      else if (k === "r") showExtra("reject");
      else if (k === "escape") showExtra("");
    }
    document.addEventListener("keydown", onKey);
    let io = null;
    if (desk && "IntersectionObserver" in window) {
      io = new IntersectionObserver((es) => es.forEach((x) => { deskVisible = x.isIntersecting; }), { threshold: 0.2 });
      io.observe(desk);
    } else deskVisible = true;

    if (selected) select(selected);
    tick();
    return {
      select,
      destroy() { if (player) player.destroy(); clearInterval(timer); document.removeEventListener("keydown", onKey); if (io) io.disconnect(); },
    };
  }

  // a muted, looping preview of one promo (the hosted app's hero)
  function teaser(el, p) {
    el.innerHTML = viewerHTML(p);
    let loop = 0, visible = true;
    const pl = Player(el, p, { mute: true, onEnd() { loop = setTimeout(() => { if (visible) { pl.seek(0); pl.play(); } }, 2400); } });
    const go = () => { if (visible && !document.hidden && !pl.playing) pl.play(); };
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return pl;
    if ("IntersectionObserver" in window) {
      new IntersectionObserver((es) => es.forEach((x) => { visible = x.isIntersecting; visible ? go() : (clearTimeout(loop), pl.pause()); }), { threshold: 0.4 }).observe(el);
    }
    document.addEventListener("visibilitychange", () => (document.hidden ? pl.pause() : go()));
    setTimeout(go, 500);
    return pl;
  }

  window.ThumbstopReview = { mount, teaser, Voices, Sound };
})();
