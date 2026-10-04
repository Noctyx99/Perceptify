(() => {
  if (window.__perceptify || !chrome?.runtime?.id) return;
  window.__perceptify = true;

  // ───────────────────────── helpers ─────────────────────────
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (x) => Math.round((Number(x) || 0) * 100);
  const compact = (n) => new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(Number(n) || 0);
  const int = (n) => (Number(n) || 0).toLocaleString("en");
  const rgba = (hex, a) => { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; };
  const getVideoId = () => {
    const u = new URL(location.href);
    if (u.pathname === "/watch") return u.searchParams.get("v");
    const m = u.pathname.match(/^\/shorts\/([\w-]{11})/);
    return m ? m[1] : null;
  };

  // Logo mark (dot uses currentColor so it works on light and dark surfaces)
  const MARK = `<svg viewBox="14 14 72 72" aria-hidden="true" focusable="false">
    <path d="M19 21C41 21 52 50.5 72 50.5 52 50.5 41 31 19 31Z" fill="#f26a35"/>
    <path d="M19 36.5C40 36.5 54 50.5 72 50.5 54 50.5 40 47 19 47Z" fill="#f98448"/>
    <path d="M19 53C40 53 54 50.5 72 50.5 54 50.5 40 63.5 19 63.5Z" fill="#fba273"/>
    <path d="M19 69C41 69 52 50.5 72 50.5 52 50.5 41 79 19 79Z" fill="#fdc6a0"/>
    <circle cx="79" cy="50.5" r="6.4" fill="currentColor"/></svg>`;
  const X = `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" fill="none"/></svg>`;
  const CHEV = `<svg class="chev" viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5L10.5 8 6 12.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>`;
  const PLAY = `<svg class="play" viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.2v9.6L12.6 8z" fill="currentColor"/></svg>`;

  const TONES = {
    hype: ["Hype", "#ff7a45"], funny: ["Funny", "#f2b544"], angry: ["Angry", "#ef5b5b"], wholesome: ["Wholesome", "#6fcf97"],
    sad: ["Somber", "#7b93ff"], divided: ["Divided", "#b79ad6"], critical: ["Critical", "#a3a8b0"], nostalgic: ["Nostalgic", "#d09a62"],
    chill: ["Relaxed", "#59b8c4"], awe: ["Awestruck", "#5cc0ff"],
  };
  const EMOTIONS = {
    laugh: ["laughs", "#f2b544"], anger: ["anger", "#ef5b5b"], awe: ["awe", "#5cc0ff"], sad: ["sad", "#7b93ff"],
    hype: ["hype", "#ff7a45"], shock: ["shock", "#e58bb7"], other: ["reaction", "#8a8a90"],
  };

  // ───────────────────── fonts (bypass page CSP) ─────────────────────
  const FONTS = [
    ["Perceptify Serif", "serif.woff2", "400", "normal"], ["Perceptify Serif", "serif-italic.woff2", "400", "italic"],
    ["Perceptify Sans", "sans-400.woff2", "400", "normal"], ["Perceptify Sans", "sans-500.woff2", "500", "normal"],
    ["Perceptify Sans", "sans-600.woff2", "600", "normal"],
    ["Perceptify Mono", "mono-400.woff2", "400", "normal"], ["Perceptify Mono", "mono-500.woff2", "500", "normal"],
  ];
  FONTS.forEach(async ([fam, file, weight, style]) => {
    try {
      const buf = await (await fetch(chrome.runtime.getURL("fonts/" + file))).arrayBuffer();
      const face = new FontFace(fam, buf, { weight, style });
      await face.load();
      document.fonts.add(face);
    } catch (_) { /* falls back to system fonts */ }
  });

  // ───────────────────────── panel (drawer) ─────────────────────────
  const panelHost = document.createElement("div");
  panelHost.id = "perceptify-panel";
  panelHost.style.cssText = "all:initial;position:fixed;z-index:2147483000;top:0;left:0;width:0;height:0;";
  const root = panelHost.attachShadow({ mode: "open" });
  root.innerHTML = `
    <link rel="stylesheet" href="${chrome.runtime.getURL("panel.css")}">
    <button class="launcher" type="button" aria-label="Open Perceptify"><span class="lm">${MARK}</span><span>Perceptify</span></button>
    <aside class="drawer" role="dialog" aria-label="Perceptify" aria-hidden="true" tabindex="-1">
      <div class="topbar">
        <div class="brand"><span class="lm">${MARK}</span><span class="wordmark">Perceptify</span></div>
        <button class="close" type="button" aria-label="Close panel">${X}</button>
      </div>
      <div class="body"></div>
    </aside>`;
  document.documentElement.appendChild(panelHost);
  const launcher = root.querySelector(".launcher"), drawer = root.querySelector(".drawer"), body = root.querySelector(".body");

  // ───────────────── inline button beside the comment Sort control ─────────────────
  const chipHost = document.createElement("span");
  chipHost.id = "perceptify-chip";
  const chipRoot = chipHost.attachShadow({ mode: "open" });
  chipRoot.innerHTML = `
    <style>
      :host { display: inline-flex; align-items: center; margin-left: 20px !important; vertical-align: middle; --bg: rgba(255,255,255,.1); --bgh: rgba(255,255,255,.18); --fg: #f1f1f1; }
      :host([data-theme="light"]) { --bg: rgba(0,0,0,.05); --bgh: rgba(0,0,0,.1); --fg: #0f0f0f; }
      button { all: unset; box-sizing: border-box; display: inline-flex; align-items: center; gap: 8px; height: 36px; padding: 0 14px 0 10px;
        border-radius: 18px; background: var(--bg); color: var(--fg); cursor: pointer; white-space: nowrap;
        font: 500 14px/1 Roboto, Arial, sans-serif; transition: background .15s; }
      button:hover { background: var(--bgh); }
      button:focus-visible { outline: 2px solid var(--fg); outline-offset: 2px; }
      svg { width: 18px; height: 18px; display: block; }
    </style>
    <button type="button" aria-label="Analyze this comment section with Perceptify">${MARK}<span>Perceptify</span></button>`;
  chipRoot.querySelector("button").addEventListener("click", toggle);

  function findAnchor() {
    const header = document.querySelector("ytd-comments-header-renderer");
    if (!header || !header.isConnected) return null;
    const sort = header.querySelector("#sort-menu, yt-sort-filter-sub-menu-renderer, ytd-sort-filter-sub-menu-renderer, ytd-comment-sort-menu");
    if (sort) return { el: sort, mode: "after" };
    const bar = header.querySelector("#title, #leading-section");
    return bar ? { el: bar, mode: "append" } : null;
  }

  function ensureButton() {
    if (!getVideoId()) { chipHost.remove(); launcher.style.display = "none"; return; }
    chipHost.dataset.theme = document.documentElement.hasAttribute("dark") ? "dark" : "light";
    const a = findAnchor();
    if (a) {
      const placed = a.mode === "after" ? a.el.nextElementSibling === chipHost : chipHost.parentElement === a.el;
      if (!placed) { a.mode === "after" ? a.el.insertAdjacentElement("afterend", chipHost) : a.el.appendChild(chipHost); }
      launcher.style.display = "none";
    } else {
      chipHost.remove();
      launcher.style.display = "flex"; // fallback: floating launcher
    }
  }
  let debounce = null;
  new MutationObserver(() => { clearTimeout(debounce); debounce = setTimeout(ensureButton, 350); })
    .observe(document.body, { childList: true, subtree: true });

  // ───────────────────────── state ─────────────────────────
  const cache = new Map();            // videoId -> report
  let runId = 0, tick = null, status = "idle";
  const stopTick = () => { clearInterval(tick); tick = null; };

  const isOpen = () => drawer.classList.contains("open");
  function open() { drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false"); drawer.focus({ preventScroll: true }); }
  function close() { drawer.classList.remove("open"); drawer.setAttribute("aria-hidden", "true"); }
  function toggle() {
    if (isOpen()) return close();
    open();
    const vid = getVideoId();
    if (!vid) return;
    if (cache.has(vid)) return render(cache.get(vid));
    if (status !== "loading") analyse(vid);
  }
  root.querySelector(".close").addEventListener("click", close);
  launcher.addEventListener("click", toggle);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && isOpen()) close(); });
  chrome.runtime.onMessage.addListener((m) => { if (m.type === "toggle") toggle(); });

  // ───────────────────────── views ─────────────────────────
  function intro() {
    status = "idle";
    body.innerHTML = `<div class="intro">
      <div class="mark-lg">${MARK}</div>
      <h1 class="display">See how<br><em>they</em> see it.</h1>
      <p class="lede">Perceptify reads this video's comments, title and transcript, then tells you what the audience actually thinks.</p>
      <button class="btn go" type="button">Analyze this video</button>
      <p class="fine mono">About 15 seconds · top 500 comments</p></div>`;
    body.querySelector(".go").addEventListener("click", () => analyse(getVideoId()));
  }

  const STEPS = ["Reading the comments", "Weighing by likes", "Finding the moments people reacted to", "Writing the read"];
  function loading() {
    status = "loading";
    let i = 0;
    body.innerHTML = `<div class="loading"><div class="progress"><i></i></div>
      <p class="label mono">Analyzing</p><p class="step">${STEPS[0]}…</p>
      <div class="skel w60"></div><div class="skel w90"></div><div class="skel w75"></div><div class="skel box"></div></div>`;
    const el = body.querySelector(".step");
    tick = setInterval(() => { i = Math.min(i + 1, STEPS.length - 1); if (el) el.textContent = STEPS[i] + "…"; }, 4000);
  }

  function showError(msg, vid) {
    stopTick(); status = "error";
    body.innerHTML = `<div class="intro error"><div class="mark-lg dim">${MARK}</div>
      <h2 class="display sm">Couldn't read the room.</h2><p class="lede">${esc(msg)}</p>
      <button class="btn retry" type="button">Try again</button></div>`;
    body.querySelector(".retry").addEventListener("click", () => analyse(vid, true));
  }

  function analyse(vid, refresh = false) {
    if (!vid) return;
    const me = ++runId;
    stopTick(); loading();
    try {
      chrome.runtime.sendMessage({ type: "analyze", videoId: vid, refresh }, (res) => {
        if (me !== runId) return;               // user navigated away
        stopTick();
        if (chrome.runtime.lastError || !res) return showError("Perceptify lost contact with its background worker. Refresh this page and try again.", vid);
        if (!res.ok) return showError(res.error, vid);
        cache.set(vid, res.data);
        try { render(res.data); } catch (e) { console.error("[Perceptify]", e); showError("The report came back in an unexpected shape. Try again.", vid); }
      });
    } catch (_) {
      showError("Perceptify was updated. Refresh this page to continue.", vid);
    }
  }

  function seek(sec) {
    const v = document.querySelector("video.html5-main-video") || document.querySelector("video");
    if (!v) return;
    v.currentTime = sec;
    const p = v.play();
    if (p && p.catch) p.catch(() => {});
  }

  const section = (label, aside, inner) => `<section class="sec"><div class="label mono"><span>${label}</span>${aside ? `<span class="aside">${aside}</span>` : ""}</div>${inner}</section>`;
  const scoreClass = (v, goodHigh) => { const s = goodHigh ? v : 100 - v; return s >= 70 ? "good" : s >= 40 ? "mid" : "bad"; };

  function render(r) {
    status = "done";
    const [toneName, tone] = TONES[r.mood?.tone] || TONES.chill;
    const s = r.sentiment || {};
    const dur = r.video?.duration || Math.max(60, ...(r.moments || []).map((m) => m.sec)) * 1.08;
    const stats = r.stats || {};

    const themes = (r.themes || []).map((t, i) => {
      const ev = (t.evidence || []).map((c) => `<li><p>${esc(c.text)}</p><div class="by mono">${esc(c.author || "viewer")} · ${compact(c.likes)} likes</div></li>`).join("");
      return `<li class="theme"><button class="theme-head" type="button" aria-expanded="false">
          <span class="idx mono">${String(i + 1).padStart(2, "0")}</span>
          <span class="tt">${esc(t.title)}</span><span class="pc mono">${pct(t.share)}%</span>${CHEV}
          <i class="share"><u style="width:${Math.max(4, pct(t.share))}%"></u></i></button>
        <div class="tb"><p class="ts-sum">${esc(t.summary)}</p><div class="label mono tight">Receipts</div><ul class="quotes">${ev}</ul></div></li>`;
    }).join("");

    const moments = r.moments || [];
    const dots = moments.map((m) => {
      const col = (EMOTIONS[m.emotion] || EMOTIONS.other)[1];
      return `<button class="pin" type="button" data-sec="${m.sec}" style="left:${Math.min(97, Math.max(3, (m.sec / dur) * 100))}%;--c:${col}" aria-label="Jump to ${esc(m.time)}: ${esc(m.label)}" title="${esc(m.time)} · ${esc(m.label)}"></button>`;
    }).join("");
    const rows = moments.map((m) => {
      const [word, col] = EMOTIONS[m.emotion] || EMOTIONS.other;
      return `<li><button class="moment" type="button" data-sec="${m.sec}">
        <span class="ts mono">${esc(m.time)}</span><span class="ml">${esc(m.label)}</span>
        <span class="mr mono"><span style="color:${col}">${word}</span> · ${m.mentions}×</span>${PLAY}</button></li>`;
    }).join("");

    const jokes = (r.running_jokes || []).map((j) => `<span class="tag">${esc(j)}</span>`).join("");
    const anger = r.anger || { score: 0, reason: "" }, cb = r.clickbait || { score: 50, verdict: "", reason: "" };

    body.innerHTML = `
      <header class="hero" style="--tone:${tone};--tint:${rgba(tone, 0.14)}">
        <div class="eyebrow mono"><i></i>Audience mood · ${esc(toneName)}</div>
        <h1 class="display">${esc(r.mood?.label || "Mixed bag")}</h1>
        <p class="verdict">${esc(r.verdict)}</p>
        <div class="vtitle mono">${esc(r.video?.title || "")}</div>
      </header>
      ${section("Sentiment", "", `
        <div class="sent" role="img" aria-label="${pct(s.positive)}% positive, ${pct(s.mixed)}% mixed, ${pct(s.negative)}% negative">
          <i class="pos" style="flex:${s.positive || 0}"></i><i class="mix" style="flex:${s.mixed || 0}"></i><i class="neg" style="flex:${s.negative || 0}"></i></div>
        <div class="legend mono"><span><b class="k pos"></b>${pct(s.positive)}% positive</span><span><b class="k mix"></b>${pct(s.mixed)}% mixed</span><span><b class="k neg"></b>${pct(s.negative)}% negative</span></div>`)}
      ${section("Summary", "", `<p class="tldr">${esc(r.tldr)}</p>${jokes ? `<div class="tags">${jokes}</div>` : ""}`)}
      <section class="sec duo">
        <div class="metric ${scoreClass(anger.score, false)}"><div class="label mono">Anger at creator</div>
          <div class="num mono">${anger.score}<small>/100</small></div><i class="rule"><u style="width:${anger.score}%"></u></i><p>${esc(anger.reason)}</p></div>
        <div class="metric ${scoreClass(cb.score, true)}"><div class="label mono">Promise kept</div>
          <div class="num mono">${cb.score}<small>/100</small></div><i class="rule"><u style="width:${cb.score}%"></u></i><p><b>${esc(cb.verdict)}.</b> ${esc(cb.reason)}</p></div>
      </section>
      ${section("Themes", "Tap for receipts", `<ol class="themes">${themes}</ol>`)}
      ${moments.length ? section("Reaction moments", "Tap to jump", `
        <div class="track"><i></i>${dots}</div>
        <div class="ends mono"><span>0:00</span><span>${esc(fmtDur(dur))}</span></div>
        <ul class="moments">${rows}</ul>`) : ""}
      <footer class="foot mono"><span>${int(stats.comments)} comments${stats.transcript ? " · transcript" : ""}${stats.engine === "mock" ? " · demo data" : ""}</span>
        <button class="link refresh" type="button">Refresh</button></footer>`;

    body.querySelectorAll(".theme-head").forEach((b) => b.addEventListener("click", () => {
      const li = b.parentElement, on = li.classList.toggle("open"); b.setAttribute("aria-expanded", String(on));
    }));
    body.querySelectorAll("[data-sec]").forEach((b) => b.addEventListener("click", () => seek(Number(b.dataset.sec))));
    body.querySelector(".refresh").addEventListener("click", () => analyse(getVideoId(), true));
    body.scrollTop = 0;
  }

  function fmtDur(sec) {
    sec = Math.round(sec);
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
  }

  // ───────────── YouTube is a single-page app: react to navigation ─────────────
  function onNav() {
    runId++; stopTick(); status = "idle";
    const vid = getVideoId();
    ensureButton();
    setTimeout(ensureButton, 800);
    if (!vid) return close();
    if (isOpen()) cache.has(vid) ? render(cache.get(vid)) : intro();
  }
  window.addEventListener("yt-navigate-finish", onNav);
  intro();
  ensureButton();
})();
