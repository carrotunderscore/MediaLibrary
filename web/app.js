/* Media Library – frontend */
(() => {
  "use strict";

  const app = document.getElementById("app");
  const toastEl = document.getElementById("toast");
  const castbar = document.getElementById("castbar");
  const modalRoot = document.getElementById("modalRoot");

  const S = {
    settings: {}, lan_ip: "", base_url: "", ffmpeg: true, tmdb: false,
    cast: null, devices: [], castBarKey: null, lastCastSave: 0,
    home: null, player: null, hls: null, downloadsTimer: null,
    playbackLogs: (() => { try { return JSON.parse(sessionStorage.getItem("mlPlaybackLogs") || "[]"); } catch (_) { return []; } })(),
    dlTab: "search", dlResults: null, dlQuery: "", scanPoller: null,
    show: null, showSeason: null,
    navFocus: null, currentHash: location.hash || "#/", hashStack: [],
    backNav: false, scrollMemory: new Map(), padSeen: false, pads: new Map(), mouseNav: false,
  };

  /* ------------------------------------------------------------- utils */
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));

  async function api(path, options = {}) {
    const opts = { headers: { "Content-Type": "application/json" }, ...options };
    if (opts.body !== undefined && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
    const res = await fetch(path, opts);
    if (!res.ok) {
      let msg = res.statusText || "Fel";
      try {
        const data = await res.json();
        msg = data.detail || data.error || msg;
      } catch (_) { /* ignore */ }
      throw new Error(msg);
    }
    return res.json();
  }

  const CONSOLE_EVENTS = new Set(["open-request", "play-session-created", "hls-error", "hls-fatal", "video-error", "open-failed", "playback-fallback"]);

  function logPlayback(event, details = {}) {
    const entry = { at: new Date().toISOString(), event, details };
    S.playbackLogs.push(entry);
    if (S.playbackLogs.length > 300) S.playbackLogs.splice(0, S.playbackLogs.length - 300);
    try { sessionStorage.setItem("mlPlaybackLogs", JSON.stringify(S.playbackLogs)); } catch (_) { /* storage can be disabled */ }
    if (CONSOLE_EVENTS.has(event)) console.warn(`Media Library playback ${JSON.stringify(entry)}`);
    const view = document.getElementById("playbackLogView");
    if (view) view.textContent = S.playbackLogs.slice(-30).map((item) => `${item.at} ${item.event} ${JSON.stringify(item.details)}`).join("\n");
  }

  let toastTimer = null;
  function toast(message, kind = "") {
    toastEl.textContent = message;
    toastEl.className = `toast show ${kind}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.className = "toast"; }, 4200);
  }

  const fmtBytes = (n) => {
    if (!n) return "0 GB";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0, v = Number(n);
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v >= 10 || i < 2 ? 0 : 1)} ${units[i]}`;
  };
  const fmtTime = (sec) => {
    sec = Math.max(0, Math.floor(sec || 0));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
             : `${m}:${String(s).padStart(2, "0")}`;
  };
  const fmtRuntime = (min) => {
    if (!min) return "";
    return min >= 60 ? `${Math.floor(min / 60)} h ${min % 60} min` : `${min} min`;
  };
  const fmtSpeed = (bps) => `${fmtBytes(bps)}/s`;
  const fmtEta = (sec) => {
    if (!sec || sec > 8640000) return "";
    if (sec > 3600) return `${Math.round(sec / 3600)} h`;
    if (sec > 60) return `${Math.round(sec / 60)} min`;
    return `${sec} s`;
  };
  const pct = (v) => `${Math.round((v || 0) * 100)}%`;
  const ratingText = (r) => (r ? `★ ${Number(r).toFixed(1)}` : "");

  const posterHtml = (item, cls = "poster") => item.poster
    ? `<img class="${cls}" loading="lazy" src="${esc(item.poster)}" alt="">`
    : `<div class="fallback">${esc(item.title || "Okänd")}</div>`;

  const backButtonHtml = `<button class="back-btn" data-action="go-back" title="Tillbaka (Esc)">
    <svg viewBox="0 0 24 24" width="15" height="15"><path fill="currentColor" d="M20 11H8.4l5.3-5.3-1.4-1.4L4.6 12l7.7 7.7 1.4-1.4L8.4 13H20z"/></svg>
    Tillbaka</button>`;

  /* ------------------------------------------------------------- router */
  function setNav(section) {
    document.querySelectorAll(".nav a").forEach((a) => {
      a.classList.toggle("active", a.dataset.nav === section);
    });
  }

  async function route() {
    clearInterval(S.downloadsTimer); S.downloadsTimer = null;
    const hash = location.hash.replace(/^#/, "") || "/";
    const parts = hash.split("/").filter(Boolean);
    setNav(parts[0] || "home");
    if (!S.backNav) window.scrollTo({ top: 0 });
    try {
      if (!parts.length) return await renderHome();
      if (parts[0] === "movies") return await renderMovies();
      if (parts[0] === "shows") return await renderShows();
      if (parts[0] === "downloads") return await renderDownloads();
      if (parts[0] === "settings") return await renderSettings();
      if (parts[0] === "search") return await renderSearch(decodeURIComponent(parts[1] || ""));
      if (parts[0] === "title" && parts[1] === "movie") return await renderMovie(parts[2]);
      if (parts[0] === "title" && parts[1] === "show") return await renderShow(parts[2]);
      return await renderHome();
    } catch (err) {
      app.innerHTML = `<div class="page"><h1 class="page-title">Något gick fel</h1>
        <p class="page-sub">${esc(err.message)}</p>
        <a class="btn" href="#/">Tillbaka</a></div>`;
    } finally {
      S.currentHash = location.hash || "#/";
    }
  }

  /* ------------------------------------------------------------- cards */
  function card(item, opts = {}) {
    const isShow = item.kind === "show";
    const isEpisode = item.kind === "episode";
    const href = isShow ? `#/title/show/${item.id}`
      : isEpisode && item.show_id ? `#/title/show/${item.show_id}`
      : `#/title/movie/${item.id}`;
    const progress = item.progress || 0;
    const action = opts.resume
      ? `data-action="play" data-id="${item.id}" data-start="${Math.max(0, progress * (item.duration || 0) - 6)}"`
      : `data-action="open" data-href="${href}"`;
    const sub = isShow ? `${item.episode_count || 0} avsnitt`
      : isEpisode ? `S${String(item.season || 0).padStart(2, "0")}E${String(item.episode || 0).padStart(2, "0")}`
      : (item.year || "");
    return `
      <article class="card" ${action} tabindex="0" role="button" aria-label="${esc(item.title)}">
        ${posterHtml(item)}
        ${item.rating ? `<span class="badge">★ ${Number(item.rating).toFixed(1)}</span>` : ""}
        <div class="card-actions">
          <button class="mini-btn" title="Spela" data-action="play"
            data-id="${item.id}" data-start="${Math.max(0, progress * (item.duration || 0) - 6)}">
            <svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>
          </button>
          ${opts.remove ? `<button class="mini-btn danger" title="Ta bort från Fortsätt titta (filen ligger kvar)"
            data-action="continue-remove" data-id="${item.resume_id ?? item.id}">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
          </button>` : ""}
        </div>
        <div class="card-body">
          <div class="card-title">${esc(item.title)}</div>
          <div class="card-sub">${item.rating ? `<span class="rating">★ ${Number(item.rating).toFixed(1)}</span>` : ""}
            <span>${esc(sub)}</span>${item.has_subtitles !== undefined ? `<span class="res-sub">${item.has_subtitles ? "CC" : "Ingen text"}</span>` : ""}</div>
        </div>
        ${progress > 0.01 && progress < 0.98 ? `<div class="progress-line"><i style="width:${pct(progress)}"></i></div>` : ""}
      </article>`;
  }

  function rowHtml(title, items, opts = {}) {
    const hint = opts.remove ? `${items.length} titlar · hovra för att ta bort` : `${items.length} titlar`;
    return `<section class="row">
      <div class="row-head"><h2 class="row-title">${esc(title)}</h2>
        <span class="row-hint">${hint}</span></div>
      <div class="row-scroller">${items.map((it) => card(it, opts)).join("")}</div>
    </section>`;
  }

  /* ------------------------------------------------------------- home */
  async function renderHome() {
    app.innerHTML = `<div class="loading-screen"><div class="spinner"></div></div>`;
    const data = await api("/api/home");
    S.home = data;
    const stats = data.stats || {};
    const hasContent = (stats.movies || 0) + (stats.episodes || 0) > 0;

    if (!hasContent) {
      app.innerHTML = `
        <div class="page page-narrow" style="padding-top:140px">
          <h1 class="page-title">Ditt bibliotek är tomt</h1>
          <p class="page-sub">Lägg till en mapp med filmer och serier, sedan skannar vi in allt automatiskt.</p>
          <div class="panel" style="max-width:640px">
            <h3>Kom igång</h3>
            <p class="hint">1. Ange biblioteksmappar &nbsp;·&nbsp; 2. Lägg in en gratis TMDB-nyckel &nbsp;·&nbsp; 3. Skanna</p>
            <div class="field-row">
              <a class="btn primary" href="#/settings">Öppna inställningar</a>
              <button class="btn" data-action="scan">Skanna nu</button>
            </div>
          </div>
        </div>`;
      return;
    }

    const hero = data.hero;
    let heroHtml = "";
    if (hero) {
      const resume = hero.progress > 0.01 && hero.progress < 0.95;
      const playStart = resume ? Math.max(0, hero.progress * (hero.duration || 0) - 6) : 0;
      const isShow = hero.kind === "show";
      heroHtml = `
        <section class="hero">
          <div class="hero-bg" style="${hero.backdrop ? `background-image:url('${esc(hero.backdrop)}')` : ""}"></div>
          <div class="hero-content">
            <div class="hero-kicker">▸ ${hero.kind === "show" ? "Serie" : "Film"} i biblioteket</div>
            <h1 class="hero-title">${esc(hero.title)}</h1>
            <div class="hero-meta">
              ${hero.rating ? `<span class="pill gold">★ ${Number(hero.rating).toFixed(1)}</span>` : ""}
              ${hero.year ? `<span>${hero.year}</span>` : ""}
              ${hero.runtime ? `<span>${fmtRuntime(hero.runtime)}</span>` : ""}
              ${isShow ? `<span>${hero.episode_count || 0} avsnitt</span>` : ""}
              ${hero.progress > 0.01 ? `<span>${pct(hero.progress)} sett</span>` : ""}
            </div>
            <p class="hero-overview">${esc(hero.overview || "Ingen beskrivning tillgänglig.")}</p>
            <div class="hero-actions">
              ${isShow
                ? `<a class="btn primary" href="#/title/show/${hero.id}">Öppna serien</a>`
                : `<button class="btn primary" data-action="play" data-id="${hero.id}" data-start="${playStart}">
                     <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>
                     ${resume ? "Fortsätt titta" : "Spela"}</button>`}
              ${hero.kind !== "show" ? `<button class="btn" data-action="cast" data-id="${hero.id}" data-start="${playStart}">
                <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M1 18v3h3a3 3 0 00-3-3zm0-4v2a5 5 0 015 5h2a7 7 0 00-7-7zm0-4v2a9 9 0 019 9h2c0-6.08-4.93-11-11-11zM3 4h18a1 1 0 011 1v13h-6v-2h4V6H4v3H2V5a1 1 0 011-1z"/></svg>
                Casta</button>` : ""}
              <a class="btn ghost" href="${isShow ? `#/title/show/${hero.id}` : `#/title/movie/${hero.id}`}">Mer info</a>
            </div>
          </div>
        </section>`;
    }

    const scan = data.scan || {};
    const banners = [];
    if (scan.running) {
      banners.push(`<div class="status-line"><span class="dot warn"></span>${esc(scan.message || "Skannar…")}</div>`);
    }
    if (!S.tmdb) {
      banners.push(`<div class="status-line"><span class="dot warn"></span>Lägg in en gratis TMDB-nyckel för att få affischer, handling och betyg. <a href="#/settings" style="color:var(--accent)">Inställningar →</a></div>`);
    }
    const bannerHtml = banners.length
      ? `<div style="padding:0 34px 8px">${banners.join("")}</div>`
      : "";

    app.innerHTML = `${heroHtml}<div class="rows">${bannerHtml}${data.rows.map((r) => rowHtml(r.title, r.items, {
      resume: r.kind === "continue", remove: r.kind === "continue",
    })).join("")}</div>`;
  }

  /* ------------------------------------------------------------- movies / shows */
  async function renderMovies() {
    app.innerHTML = `<div class="page"><h1 class="page-title">Filmer</h1>
      <p class="page-sub">Alla filmer i dina mappar.</p>
      <div class="library-filters">
        <div class="filter"><span>Genre</span><select id="movieGenre"><option value="">Alla</option></select></div>
        <div class="filter"><span>Sortera</span><select id="movieSort"><option value="title">Bokstavsordning</option><option value="year">År</option><option value="added">Nyligen tillagda</option></select></div>
        <span class="filter-count" id="movieCount"></span>
      </div>
      <div class="grid" id="movieGrid"><div class="spinner"></div></div></div>`;
    const data = await api("/api/movies");
    const grid = document.getElementById("movieGrid");
    S.movieItems = data.items;
    const genres = [...new Set(data.items.flatMap((it) => it.genres || []))].sort((a, b) => a.localeCompare(b, "sv"));
    document.getElementById("movieGenre").innerHTML += genres.map((g) => `<option value="${esc(g)}">${esc(g)}</option>`).join("");
    const render = () => {
      let items = [...S.movieItems];
      const genre = document.getElementById("movieGenre").value;
      if (genre) items = items.filter((it) => (it.genres || []).includes(genre));
      const sort = document.getElementById("movieSort").value;
      items.sort((a, b) => sort === "year" ? (b.year || 0) - (a.year || 0) : sort === "added" ? (b.added_at || 0) - (a.added_at || 0) : a.title.localeCompare(b.title, "sv"));
      grid.innerHTML = items.length ? items.map((it) => card(it)).join("") : `<p class="empty">Inga filmer hittades för filtret.</p>`;
      const count = document.getElementById("movieCount");
      if (count) count.textContent = `${items.length} av ${S.movieItems.length} filmer`;
    };
    document.getElementById("movieGenre").onchange = render;
    document.getElementById("movieSort").onchange = render;
    render();
  }

  async function renderShows() {
    app.innerHTML = `<div class="page"><h1 class="page-title">Serier</h1>
      <p class="page-sub">Dina serier och säsonger.</p>
      <div class="library-filters">
        <div class="filter"><span>Genre</span><select id="showGenre"><option value="">Alla</option></select></div>
        <div class="filter"><span>Sortera</span><select id="showSort"><option value="title">Bokstavsordning</option><option value="year">År</option><option value="added">Nyligen tillagda</option></select></div>
        <span class="filter-count" id="showCount"></span>
      </div>
      <div class="grid" id="showGrid"><div class="spinner"></div></div></div>`;
    const data = await api("/api/shows");
    const grid = document.getElementById("showGrid");
    S.showItems = data.items;
    const genres = [...new Set(data.items.flatMap((it) => it.genres || []))].sort((a, b) => a.localeCompare(b, "sv"));
    document.getElementById("showGenre").innerHTML += genres.map((g) => `<option value="${esc(g)}">${esc(g)}</option>`).join("");
    const render = () => {
      let items = [...S.showItems];
      const genre = document.getElementById("showGenre").value;
      if (genre) items = items.filter((it) => (it.genres || []).includes(genre));
      const sort = document.getElementById("showSort").value;
      items.sort((a, b) => sort === "year" ? (b.year || 0) - (a.year || 0) : sort === "added" ? (b.added_at || 0) - (a.added_at || 0) : a.title.localeCompare(b.title, "sv"));
      grid.innerHTML = items.length ? items.map((it) => card(it)).join("") : `<p class="empty">Inga serier hittades för filtret.</p>`;
      const count = document.getElementById("showCount");
      if (count) count.textContent = `${items.length} av ${S.showItems.length} serier`;
    };
    document.getElementById("showGenre").onchange = render;
    document.getElementById("showSort").onchange = render;
    render();
  }

  async function renderSearch(q) {
    document.getElementById("searchInput").value = q;
    const data = await api(`/api/search?q=${encodeURIComponent(q)}`);
    app.innerHTML = `<div class="page">
      <h1 class="page-title">Sökresultat</h1>
      <p class="page-sub">${data.movies.length + data.shows.length} träffar för “${esc(q)}”</p>
      ${data.shows.length ? `<h2 class="section-title" style="margin:10px 0 14px">Serier</h2>
        <div class="grid">${data.shows.map((it) => card(it)).join("")}</div>` : ""}
      ${data.movies.length ? `<h2 class="section-title" style="margin:30px 0 14px">Filmer</h2>
        <div class="grid">${data.movies.map((it) => card(it)).join("")}</div>` : ""}
      ${!data.movies.length && !data.shows.length ? `<p class="empty">Inget matchade sökningen.</p>` : ""}
    </div>`;
  }

  /* ------------------------------------------------------------- movie detail */
  async function renderMovie(id) {
    app.innerHTML = `<div class="loading-screen"><div class="spinner"></div></div>`;
    const item = await api(`/api/item/${id}`);
    const localSubs = (await api(`/api/subtitles/${id}`)).subtitles || [];
    const meta = item.meta || {};
    const start = item.progress > 0.01 ? Math.max(0, item.progress * (item.duration || 0) - 6) : 0;
    const resume = start > 0;
    const info = item.info || {};
    const quality = [info.height ? `${info.height}p` : "", (info.vcodec || "").toUpperCase(),
      (info.acodec || "").toUpperCase(), fmtBytes(info.size)].filter(Boolean).join(" · ");
    const imdb = meta.imdb_id ? `https://www.imdb.com/title/${meta.imdb_id}/` : null;

    app.innerHTML = `
      <section class="title-hero">
        ${backButtonHtml}
        <div class="bg" style="${item.backdrop ? `background-image:url('${esc(item.backdrop)}')` : ""}"></div>
        <div class="title-inner">
          <div class="title-poster">${posterHtml({ ...item, poster: item.poster || item.backdrop })}</div>
          <div class="title-info">
            <h1 class="title-name">${esc(item.title)}</h1>
            <div class="meta-line">
              ${item.year ? `<span class="pill">${item.year}</span>` : ""}
              ${item.rating ? `<span class="pill gold">★ ${Number(item.rating).toFixed(1)} ${meta.votes ? `<span style="opacity:.6">(${meta.votes})</span>` : ""}</span>` : ""}
              ${meta.runtime ? `<span>${fmtRuntime(meta.runtime)}</span>` : ""}
              ${(meta.genres || []).map((g) => `<span class="pill">${esc(g)}</span>`).join("")}
              ${item.matched ? "" : `<span class="pill" title="Ingen TMDB-matchning">Ingen matchning</span>`}
            </div>
            ${meta.tagline ? `<p class="overview" style="font-style:italic;opacity:.8">${esc(meta.tagline)}</p>` : ""}
            <p class="overview">${esc(meta.overview || "Ingen beskrivning hittades. Byt matchning för att hämta information från TMDB.")}</p>
            <div class="title-actions">
              <button class="btn primary" data-action="play" data-id="${item.id}" data-start="${start}">
                <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>
                ${resume ? "Fortsätt titta" : "Spela"}</button>
              <button class="btn" data-action="cast" data-id="${item.id}" data-start="${start}">Casta</button>
              ${imdb ? `<a class="btn ghost" href="${imdb}" target="_blank" rel="noopener">IMDb ↗</a>` : ""}
              <button class="btn ghost" data-action="rematch" data-kind="movie" data-id="${item.id}">Byt matchning</button>
              <button class="btn ghost" data-action="watched" data-id="${item.id}" data-next="0">Markera som sedd</button>
              <button class="btn ghost" data-action="reveal-file" data-id="${item.id}" title="Öppna filens mapp i Utforskaren">
                <svg viewBox="0 0 24 24" width="15" height="15"><path fill="currentColor" d="M3 5h6l2 2h10a1 1 0 011 1v11a1 1 0 01-1 1H3a1 1 0 01-1-1V6a1 1 0 011-1z"/></svg>
                Öppna mappen</button>
              <button class="btn danger" data-action="delete-file" data-id="${item.id}"
                data-title="${esc(item.title)}" data-path="${esc(item.path)}">Radera från disk</button>
              <span class="pill">${localSubs.length ? `Undertext finns (${localSubs.length})` : "Ingen undertext"}</span>
              ${!localSubs.length ? `<button class="btn" data-action="generate-subtitle" data-id="${item.id}">Generera undertext</button>` : ""}
            </div>
            <dl class="facts">
              <div class="fact"><dt>Fil</dt><dd class="mono" style="word-break:break-all">${esc(item.path.split(/[\\/]/).pop())}</dd></div>
              <div class="fact"><dt>Bild & ljud</dt><dd>${esc(quality || "Okänd")}</dd></div>
              <div class="fact"><dt>Upplösning</dt><dd>${info.width || "?"} × ${info.height || "?"}</dd></div>
              ${meta.directors && meta.directors.length ? `<div class="fact"><dt>Regi</dt><dd>${esc(meta.directors.join(", "))}</dd></div>` : ""}
            </dl>
          </div>
        </div>
      </section>
      ${(meta.cast || []).length ? `<section class="section"><div class="section-head"><h2 class="section-title">Skådespelare</h2></div>
        <div class="cast-strip">${meta.cast.map((p) => `
          <div class="person">
            ${p.profile ? `<img src="${esc(p.profile)}" alt="">` : `<div class="avatar">${esc((p.name || "?").slice(0, 2))}</div>`}
            <div class="pname">${esc(p.name)}</div>
            <div class="prole">${esc(p.character || "")}</div>
          </div>`).join("")}</div></section>` : ""}
    `;
  }

  /* ------------------------------------------------------------- show detail */
  async function renderShow(id) {
    app.innerHTML = `<div class="loading-screen"><div class="spinner"></div></div>`;
    const data = await api(`/api/show/${id}`);
    S.show = data;
    const seasonNumbers = data.seasons.map((s) => s.season);
    if (S.showSeason === null || !seasonNumbers.includes(S.showSeason)) {
      const next = data.seasons.find((s) => s.episodes.some((e) => (e.progress || 0) > 0.01 && (e.progress || 0) < 0.95));
      S.showSeason = next ? next.season : (seasonNumbers[0] ?? 1);
    }
    const show = data.show;
    const meta = data.meta || {};
    const imdb = meta.imdb_id ? `https://www.imdb.com/title/${meta.imdb_id}/` : null;
    const firstEp = data.seasons.flatMap((s) => s.episodes)[0];
    app.innerHTML = `
      <section class="title-hero">
        ${backButtonHtml}
        <div class="bg" style="${show.backdrop ? `background-image:url('${esc(show.backdrop)}')` : ""}"></div>
        <div class="title-inner">
          <div class="title-poster">${posterHtml({ ...show })}</div>
          <div class="title-info">
            <h1 class="title-name">${esc(show.title)}</h1>
            <div class="meta-line">
              ${show.year ? `<span class="pill">${show.year}</span>` : ""}
              ${show.rating ? `<span class="pill gold">★ ${Number(show.rating).toFixed(1)}</span>` : ""}
              <span>${data.episode_count} avsnitt</span>
              ${(meta.genres || []).map((g) => `<span class="pill">${esc(g)}</span>`).join("")}
              ${show.matched ? "" : `<span class="pill">Ingen matchning</span>`}
            </div>
            <p class="overview">${esc(meta.overview || "Ingen beskrivning hittades. Byt matchning för att hämta information från TMDB.")}</p>
            <div class="title-actions">
              ${firstEp ? `<button class="btn primary" data-action="play" data-id="${firstEp.id}" data-start="${firstEp.progress > 0.01 ? Math.max(0, firstEp.progress * (firstEp.duration || 0) - 6) : 0}">
                <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>
                ${firstEp.progress > 0.01 ? "Fortsätt titta" : `Spela S${String(firstEp.season).padStart(2, "0")}E${String(firstEp.episode).padStart(2, "0")}`}</button>` : ""}
              ${firstEp ? `<button class="btn" data-action="cast" data-id="${firstEp.id}" data-start="0">Casta</button>` : ""}
              ${imdb ? `<a class="btn ghost" href="${imdb}" target="_blank" rel="noopener">IMDb ↗</a>` : ""}
              <button class="btn ghost" data-action="rematch" data-kind="show" data-id="${show.id}">Byt matchning</button>
            </div>
          </div>
        </div>
      </section>
      <section class="episodes-panel">
        <div class="section-head">
          <h2 class="section-title">Avsnitt</h2>
          <div class="season-tabs">
            ${data.seasons.map((s) => `<button class="chip ${s.season === S.showSeason ? "active" : ""}"
              data-action="season" data-season="${s.season}">Säsong ${s.season}</button>`).join("")}
          </div>
        </div>
        <div id="episodeList">${episodesHtml()}</div>
      </section>
      ${(meta.cast || []).length ? `<section class="section"><div class="section-head"><h2 class="section-title">Skådespelare</h2></div>
        <div class="cast-strip">${meta.cast.map((p) => `
          <div class="person">
            ${p.profile ? `<img src="${esc(p.profile)}" alt="">` : `<div class="avatar">${esc((p.name || "?").slice(0, 2))}</div>`}
            <div class="pname">${esc(p.name)}</div>
            <div class="prole">${esc(p.character || "")}</div>
          </div>`).join("")}</div></section>` : ""}
    `;
  }

  function episodesHtml() {
    if (!S.show) return "";
    const season = S.show.seasons.find((s) => s.season === S.showSeason);
    if (!season || !season.episodes.length) return `<p class="empty">Inga avsnitt i den här säsongen.</p>`;
    return season.episodes.map((ep) => {
      const start = ep.progress > 0.01 ? Math.max(0, ep.progress * (ep.duration || 0) - 6) : 0;
      const watched = ep.progress > 0.95;
      return `<div class="ep-row" data-action="play" data-id="${ep.id}" data-start="${start}" tabindex="0">
        <div class="ep-num">${ep.episode}</div>
        ${ep.still ? `<img class="ep-thumb" loading="lazy" src="${esc(ep.still)}" alt="">`
          : `<div class="ep-thumb placeholder">▶</div>`}
        <div class="ep-main">
          <div class="ep-title">${esc(ep.title)} ${watched ? `<span class="ep-watched">✓</span>` : ""}</div>
          <div class="ep-overview">${esc(ep.overview || "")}</div>
          <div class="res-sub">${ep.has_subtitles ? "Undertext finns" : "Ingen undertext"}</div>
        </div>
        <div class="ep-side">
          ${ep.runtime ? `<span>${ep.runtime} min</span>` : ep.duration ? `<span>${fmtTime(ep.duration)}</span>` : ""}
          ${ep.progress > 0.01 && !watched ? `<span>${pct(ep.progress)}</span>` : ""}
          ${!ep.has_subtitles ? `<button class="btn small" data-action="generate-subtitle" data-id="${ep.id}">Generera text</button>` : ""}
          <button class="mini-btn" data-action="watched" data-id="${ep.id}" data-next="${watched ? 0 : 1}" title="Markera sedd">
            <svg viewBox="0 0 24 24" width="13" height="13"><path fill="currentColor" d="M9 16.2l-3.5-3.5L4 14.2 9 19 20 8l-1.4-1.4z"/></svg>
          </button>
          <button class="mini-btn" data-action="reveal-file" data-id="${ep.id}" title="Öppna filens mapp">
            <svg viewBox="0 0 24 24" width="13" height="13"><path fill="currentColor" d="M3 5h6l2 2h10a1 1 0 011 1v11a1 1 0 01-1 1H3a1 1 0 01-1-1V6a1 1 0 011-1z"/></svg>
          </button>
          <button class="mini-btn danger" data-action="delete-file" data-id="${ep.id}"
            data-title="${esc(`${S.show?.show?.title || ""} S${String(ep.season).padStart(2, "0")}E${String(ep.episode).padStart(2, "0")}`.trim())}"
            data-path="${esc(ep.path || "")}" title="Radera från disk">
            <svg viewBox="0 0 24 24" width="13" height="13"><path fill="currentColor" d="M9 3h6l1 2h4v2H4V5h4l1-2zM6 9h12l-1 11H7L6 9z"/></svg>
          </button>
        </div>
      </div>`;
    }).join("");
  }

  /* ------------------------------------------------------- playback modes */
  const H264_MSE_TYPE = 'video/mp4; codecs="avc1.640028,mp4a.40.2"';

  function mseSupports(type) {
    try { return !!(window.MediaSource && MediaSource.isTypeSupported(type)); } catch (_) { return false; }
  }

  function playModeOrder() {
    if (mseSupports(H264_MSE_TYPE)) return ["hls", "fmp4", "webm"];
    const probe = document.createElement("video");
    if (probe.canPlayType('video/mp4; codecs="avc1.640028"')) return ["fmp4", "webm"];
    if (probe.canPlayType("video/webm")) return ["webm"];
    return [];
  }

  function preferredPlayMode() {
    let saved = "";
    try { saved = sessionStorage.getItem("mlPlayMode") || ""; } catch (_) { /* ignored */ }
    const order = playModeOrder();
    return order.includes(saved) ? saved : order[0];
  }

  function rememberPlayMode(mode) {
    try { sessionStorage.setItem("mlPlayMode", mode); } catch (_) { /* ignored */ }
  }

  function modeChain(startMode) {
    const order = playModeOrder();
    const index = order.indexOf(startMode);
    return index >= 0 ? order.slice(index) : [startMode];
  }

  function codecSummary() {
    const probe = document.createElement("video");
    return [
      `MSE H.264: ${mseSupports(H264_MSE_TYPE) ? "ja" : "nej"}`,
      `MSE WebM/VP9: ${mseSupports('video/webm; codecs="vp9"') ? "ja" : "nej"}`,
      `MP4 direkt: ${probe.canPlayType("video/mp4") || "nej"}`,
      `WebM direkt: ${probe.canPlayType("video/webm") || "nej"}`,
      `Läge: ${preferredPlayMode()}`,
    ].join(" · ");
  }

  function detachMedia() {
    if (S.hls) { S.hls.destroy(); S.hls = null; }
    const video = S.player?.video;
    if (!video) return;
    try { video.pause(); } catch (_) { /* ignored */ }
    video.removeAttribute("src");
    try { video.load(); } catch (_) { /* ignored */ }
  }

  function tryFallback(reason) {
    const p = S.player;
    if (!p || p.switching) return false;
    const chain = p.modeChain || [];
    const index = chain.indexOf(p.mode);
    const next = index >= 0 ? chain.slice(index + 1)[0] : null;
    if (!next) return false;
    const resumeAt = Math.max(0, p.offset + (p.video.currentTime || 0));
    p.switching = true;
    p.mode = next;
    rememberPlayMode(next);
    logPlayback("playback-fallback", { reason: String(reason), from: chain[index], to: next, resumeAt });
    toast(next === "webm" ? "Vyn klarar inte H.264 – spelar med WebM (VP9) i stället…" : "Byter uppspelningsmetod…");
    restartAt(resumeAt).finally(() => { p.switching = false; });
    return true;
  }

  /* ------------------------------------------------------------- player */
  async function openPlayer(itemId, start = 0) {
    logPlayback("open-request", { itemId, start, capabilities: codecSummary() });
    S.playerReturn = S.navFocus?.isConnected ? S.navFocus : null;
    try {
      if (!playModeOrder().length) {
        toast("Den här vyn kan inte spela video (varken H.264 eller WebM). Öppna appen i Chrome eller Edge.", "error");
        return;
      }
      const detail = await api(`/api/item/${itemId}`);
      const startMode = preferredPlayMode();
      const play = await api(`/api/play/${itemId}?start=${start}&direct=0&mode=${startMode}`);
      logPlayback("play-session-created", { itemId, mode: play.mode, sid: play.sid, transcode: play.transcode, duration: play.duration, url: play.url });
      const total = detail.duration || (play.duration || 0);
      const title = detail.kind === "episode"
        ? `${detail.show?.title || ""} · S${String(detail.season).padStart(2, "0")}E${String(detail.episode).padStart(2, "0")}`
        : detail.title;
      const sub = detail.kind === "episode"
        ? (detail.meta?.episode?.name || detail.title || "")
        : `${detail.year || ""}${detail.meta?.runtime ? ` · ${fmtRuntime(detail.meta.runtime)}` : ""}`;

      const overlay = document.createElement("div");
      overlay.className = "player";
      overlay.id = "playerOverlay";
      overlay.innerHTML = `
        <div class="player-stage">
        <video id="videoEl" autoplay playsinline></video>
        <div class="player-top">
          <button class="icon-btn" data-action="player-close" title="Stäng">
            <svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M15.4 7.4L14 6l-6 6 6 6 1.4-1.4L10.8 12z"/></svg>
          </button>
          <div>
            <div class="player-title">${esc(title)}</div>
            <div class="player-sub">${esc(sub)}</div>
          </div>
          <span class="transcode-tag">${play.mode === "hls" ? "HLS" : play.mode === "webm" ? "WebM · VP9" : play.mode === "fmp4" ? "MP4 · H.264" : "Direktuppspelning"}</span>
        </div>
        <div class="player-center">
          <button class="player-big" data-action="player-toggle" id="bigPlay">
            <svg viewBox="0 0 24 24" width="30" height="30"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>
          </button>
        </div>
        <div class="subtitle-layer" id="subtitleLayer"></div>
        </div>
        <div class="player-controls">
          <div class="seek-wrap">
            <span class="time" id="curTime">0:00</span>
            <div class="seek" id="seekBar"><i></i><b></b></div>
            <span class="time" id="durTime">${fmtTime(total)}</span>
          </div>
          <div class="player-row">
            <button class="icon-btn" data-action="player-toggle" title="Spela/pausa">
              <svg viewBox="0 0 24 24" width="22" height="22"><path fill="currentColor" d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>
            </button>
            <button class="icon-btn" data-action="player-seek" data-delta="-10" title="-10 s">
              <svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M12 5V1L7 6l5 5V7a6 6 0 11-6 6H4a8 8 0 108-8z"/></svg>
            </button>
            <button class="icon-btn" data-action="player-seek" data-delta="10" title="+10 s">
              <svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M12 5V1l5 5-5 5V7a6 6 0 106 6h2a8 8 0 11-8-8z"/></svg>
            </button>
            <button class="btn small" data-action="player-cast" data-id="${itemId}" data-start="${start}">Casta</button>
            <button class="btn small" data-action="player-subtitles" title="Undertexter">CC Undertexter</button>
            <button class="icon-btn" data-action="player-fullscreen" id="fsBtn" title="Fullskärm (f)">
              <svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M4 4h6v2H6v4H4V4zm10 0h6v6h-2V6h-4V4zM4 14h2v4h4v2H4v-6zm14 0h2v6h-6v-2h4v-4z"/></svg>
            </button>
            <span style="flex:1"></span>
            <button class="btn small ghost" data-action="player-close">Stäng</button>
          </div>
        </div>`;
      document.body.appendChild(overlay);
      document.body.classList.add("locked");
      const video = overlay.querySelector("#videoEl");
      S.player = {
        item: detail, play, offset: play.mode === "direct" ? 0 : (play.start || 0), total,
        lastSave: 0, overlay, video, ended: false, subtitleTrack: null,
        mode: play.mode, modeChain: modeChain(play.mode), switching: false,
      };
      attachMedia(play, video);
      wirePlayer();
      saveProgress();
      if (play.mode === "hls") toast("Transkodar – uppspelningen startar om några sekunder…");
    } catch (err) {
      logPlayback("open-failed", { itemId, start, message: err.message, stack: err.stack });
      toast(`Kunde inte öppna spelaren: ${err.message}`, "error");
    }
  }

  function attachMedia(play, video) {
    if (S.hls) { S.hls.destroy(); S.hls = null; }
    if (play.mode !== "hls") {
      video.src = play.url;
      if (play.mode === "direct" && play.start > 1) {
        video.addEventListener("loadedmetadata", () => { video.currentTime = play.start; }, { once: true });
      }
      video.play().catch((error) => logPlayback("video-play-rejected", { message: error.message, name: error.name, mode: play.mode }));
      return;
    }
    if (window.Hls && Hls.isSupported()) {
      S.hls = new Hls({ maxBufferLength: 30, backBufferLength: 60 });
      S.hls.loadSource(play.url);
      S.hls.attachMedia(video);
      S.hls.on(Hls.Events.MANIFEST_PARSED, (_event, data) => {
        logPlayback("hls-manifest-parsed", { url: play.url, levels: data?.levels?.map((level) => ({ bitrate: level.bitrate, width: level.width, height: level.height, codecs: level.codecs })) });
        video.play().catch((error) => logPlayback("video-play-rejected", { message: error.message, name: error.name }));
      });
      S.hls.on(Hls.Events.ERROR, (_e, data) => {
        if (data.fatal || data.details === "bufferAppendError" || data.type === Hls.ErrorTypes.NETWORK_ERROR) {
          logPlayback("hls-error", {
            fatal: data.fatal, type: data.type, details: data.details, reason: data.reason,
            url: data.url, response: data.response ? { code: data.response.code, text: data.response.text } : null,
            error: data.error ? { name: data.error.name, message: data.error.message, stack: data.error.stack, toString: String(data.error) } : null,
            frag: data.frag ? { sn: data.frag.sn, type: data.frag.type, level: data.frag.level, url: data.frag.url, duration: data.frag.duration } : null,
            context: data.context ? { type: data.context.type, url: data.context.url, responseType: data.context.responseType } : null,
            buffer: { video: video.error ? { code: video.error.code, message: video.error.message } : null, currentTime: video.currentTime, readyState: video.readyState, networkState: video.networkState, buffered: Array.from(video.buffered || []).map((_, index) => [video.buffered.start(index), video.buffered.end(index)]) },
          });
        }
        if (!data.fatal) return;
        const details = `${data.type || "fel"}${data.details ? ` · ${data.details}` : ""}${data.response?.code ? ` · HTTP ${data.response.code}` : ""}`;
        if (data.type === Hls.ErrorTypes.NETWORK_ERROR && !S.hlsRetrying) {
          S.hlsRetrying = true;
          toast(`HLS-nätverksfel (${details}); försöker igen…`, "error");
          S.hls.startLoad();
        } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR && !S.hlsRecovering) {
          S.hlsRecovering = true;
          toast(`HLS-mediafel (${details}); återhämtar…`, "error");
          S.hls.recoverMediaError();
        } else {
          logPlayback("hls-fatal", { type: data.type, details: data.details, reason: data.reason, url: data.url, response: data.response, error: data.error ? { name: data.error.name, message: data.error.message, stack: data.error.stack, toString: String(data.error) } : null, frag: data.frag ? { sn: data.frag.sn, type: data.frag.type, url: data.frag.url } : null });
          if (tryFallback(details)) return;
          toast(`HLS-fel (${details}): strömmen kunde inte spelas`, "error");
        }
      });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = play.url;
      video.play().catch(() => {});
    } else {
      toast("Webbläsaren kan inte spela HLS – använd Casta eller Chrome/Edge", "error");
    }
  }

  function wirePlayer() {
    const p = S.player;
    if (!p) return;
    const video = p.video;
    const seekBar = p.overlay.querySelector("#seekBar");
    const cur = p.overlay.querySelector("#curTime");
    const dur = p.overlay.querySelector("#durTime");
    dur.textContent = fmtTime(p.total);

    const update = () => {
      const abs = p.offset + (video.currentTime || 0);
      const ratio = p.total ? Math.min(1, abs / p.total) : 0;
      seekBar.querySelector("i").style.width = pct(ratio);
      seekBar.querySelector("b").style.left = pct(ratio);
      cur.textContent = fmtTime(abs);
      if (video.paused) showPlayerUi(false);
      renderSubtitles(p);
    };
    video.addEventListener("timeupdate", update);
    // rAF-loopen kan strypas när fönstret inte ritas om, så undertexten
    // uppdateras även på de här händelserna.
    for (const eventName of ["seeking", "seeked", "ratechange", "loadedmetadata", "canplay"]) {
      video.addEventListener(eventName, () => renderSubtitles(p));
    }
    for (const eventName of ["error", "stalled", "waiting", "playing", "loadedmetadata", "canplay"]) {
      video.addEventListener(eventName, () => logPlayback(`video-${eventName}`, {
        currentTime: video.currentTime, duration: video.duration, readyState: video.readyState,
        networkState: video.networkState, error: video.error ? { code: video.error.code, message: video.error.message } : null,
      }));
    }
    const syncPlayState = () => {
      p.overlay.classList.toggle("playing", !video.paused && !video.ended);
      showPlayerUi(!video.paused);
    };
    for (const eventName of ["play", "playing", "pause", "ended", "emptied"]) {
      video.addEventListener(eventName, syncPlayState);
    }
    syncPlayState();
    video.addEventListener("play", () => showPlayerUi());
    applySubtitleTrack(p);
    video.addEventListener("ended", () => {
      p.ended = true;
      markWatched(p.item.id, true);
      closePlayer();
    });
    video.addEventListener("error", () => {
      const current = S.player;
      if (!current || current.switching) return;
      const mediaError = video.error;
      logPlayback("video-error", { code: mediaError?.code, message: mediaError?.message, mode: current.mode, currentSrc: video.currentSrc });
      if (tryFallback(`mediafel ${mediaError?.code || "okänt"}`)) return;
      toast(`Videofilen kunde inte spelas${mediaError?.code ? ` (mediafel ${mediaError.code})` : ""}`, "error");
    });

    // Vänsterklick i bilden pausar/spelar, dubbelklick växlar fullskärm – som YouTube.
    video.addEventListener("click", () => { togglePlayer(); });
    video.addEventListener("dblclick", () => { togglePlayerFullscreen(); });

    seekBar.addEventListener("click", (ev) => {
      const rect = seekBar.getBoundingClientRect();
      const ratio = Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width));
      restartAt(ratio * p.total);
    });

    p.overlay.addEventListener("mousemove", () => showPlayerUi());
    p.overlay.addEventListener("pointerdown", () => showPlayerUi());
    // Tangentbordet (piltangenter, J/K/L, F) ska också väcka raden.
    p.onKeyWake = (ev) => {
      if (S.player !== p || ev.ctrlKey || ev.metaKey || ev.altKey) return;
      if (["Shift", "Control", "Alt", "Meta"].includes(ev.key)) return;
      showPlayerUi();
    };
    document.addEventListener("keydown", p.onKeyWake);

    syncPlayerMetrics(p);
    startSubtitleLoop(p);
    if (window.ResizeObserver) {
      p.resizeObserver = new ResizeObserver(() => syncPlayerMetrics(p));
      p.resizeObserver.observe(p.overlay);
    }
    p.onWindowResize = () => syncPlayerMetrics(p);
    window.addEventListener("resize", p.onWindowResize);

    p.saveTimer = setInterval(() => {
      if (!p.ended) saveProgress();
    }, 10000);
  }

  /* --------------------------------------------------------- undertexter
     Undertexterna ritas här i stället för av webbläsarens <track>-renderare:
     den lägger texten under kontrollraden och kan i Qt-vyn lämna kvar gamla
     repliker så att hela bilden fylls av text. Egen rendering ger rätt
     placering, max tre rader och bara de cues som faktiskt gäller nu. */

  function parseSubtitleTime(value) {
    const parts = String(value || "").trim().replace(",", ".").split(":").map(Number);
    if (!parts.length || parts.some((part) => Number.isNaN(part))) return 0;
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    return parts[0];
  }

  function cleanSubtitleLine(line) {
    return String(line)
      .replace(/\{[^}]*\}/g, "")
      .replace(/<[^>]*>/g, "")
      .replace(/&nbsp;/gi, " ")
      .replace(/&lt;/gi, "<")
      .replace(/&gt;/gi, ">")
      .replace(/&amp;/gi, "&")
      .replace(/\s+/g, " ")
      .trim();
  }

  function parseSubtitleCues(text) {
    const cues = [];
    const blocks = String(text || "").replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n").split(/\n{2,}/);
    for (const block of blocks) {
      const lines = block.split("\n");
      const index = lines.findIndex((line) => line.includes("-->") );
      if (index < 0) continue;
      const times = lines[index].match(/\d{1,3}:\d{2}(?::\d{2})?[.,]\d{1,3}/g);
      if (!times || times.length < 2) continue;
      const start = parseSubtitleTime(times[0]);
      const end = parseSubtitleTime(times[1]);
      if (!(end > start)) continue;
      const body = lines.slice(index + 1).map(cleanSubtitleLine).filter(Boolean).join("\n");
      if (body) cues.push({ start, end, text: body });
    }
    cues.sort((a, b) => a.start - b.start);
    return cues;
  }

  function activeSubtitleText(player) {
    const cues = player.cues || [];
    if (!cues.length) return "";
    const time = (player.offset || 0) + (player.video.currentTime || 0);
    const seen = new Set();
    const lines = [];
    for (const cue of cues) {
      if (cue.end <= time) continue;
      if (cue.start > time) break;
      for (const line of cue.text.split("\n")) {
        const key = line.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        lines.push(line);
      }
    }
    // Fler än tre rader samtidigt går inte att läsa – visa de senaste.
    return lines.slice(-3).join("\n");
  }

  function renderSubtitles(player, force = false) {
    if (!player) return;
    if (!player.subtitleLayer) player.subtitleLayer = player.overlay.querySelector("#subtitleLayer");
    const layer = player.subtitleLayer;
    if (!layer) return;
    const text = player.subtitleTrack ? activeSubtitleText(player) : "";
    if (!force && text === player.subtitleShown) return;
    player.subtitleShown = text;
    if (!text) {
      layer.classList.remove("visible");
      layer.replaceChildren();
      return;
    }
    layer.replaceChildren(...text.split("\n").map((line) => {
      const span = document.createElement("span");
      span.textContent = line;
      return span;
    }));
    layer.classList.add("visible");
  }

  function stopSubtitleLoop(player) {
    if (player?.subtitleFrame) cancelAnimationFrame(player.subtitleFrame);
    if (player) player.subtitleFrame = 0;
  }

  function startSubtitleLoop(player) {
    stopSubtitleLoop(player);
    const tick = () => {
      if (S.player !== player) return;
      renderSubtitles(player);
      player.subtitleFrame = requestAnimationFrame(tick);
    };
    player.subtitleFrame = requestAnimationFrame(tick);
  }

  // Kontrollraden ligger under bilden i fönsterläge och ovanpå i fullskärm,
  // så höjden mäts upp och styr var undertexten landar.
  function syncPlayerMetrics(player) {
    if (!player) return;
    const stage = player.overlay.querySelector(".player-stage");
    const controls = player.overlay.querySelector(".player-controls");
    if (!stage || !controls) return;
    const stageHeight = Math.max(1, Math.round(stage.getBoundingClientRect().height));
    stage.style.setProperty("--stage-h", String(stageHeight));
    player.overlay.style.setProperty("--controls-h", `${Math.round(controls.getBoundingClientRect().height)}px`);
  }

  async function applySubtitleTrack(player) {
    const video = player.video;
    for (const element of [...video.querySelectorAll("track")]) element.remove();
    for (const track of video.textTracks) track.mode = "disabled";
    const wanted = player.subtitleTrack;
    if (!wanted) {
      player.cues = [];
      player.subtitleKey = "";
      renderSubtitles(player, true);
      return;
    }
    if (player.subtitleKey === wanted.name && (player.cues || []).length) {
      renderSubtitles(player, true);
      return;
    }
    const token = (player.subtitleToken = (player.subtitleToken || 0) + 1);
    try {
      const response = await fetch(`${wanted.url}&offset=0`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const cues = parseSubtitleCues(await response.text());
      if (player.subtitleToken !== token) return;
      player.subtitleKey = wanted.name;
      player.cues = cues;
      logPlayback("subtitle-loaded", { name: wanted.name, cues: cues.length });
      renderSubtitles(player, true);
    } catch (err) {
      if (player.subtitleToken !== token) return;
      logPlayback("subtitle-load-failed", { name: wanted.name, message: err.message });
      toast(`Kunde inte läsa undertexten: ${err.message}`, "error");
    }
  }

  // Första körningen saknar mätdata. GPU-körning med whisper small ligger
  // runt 6 s per videominut på en modern GPU, CPU runt 45 s.
  const DEFAULT_SECONDS_PER_MINUTE = 10;

  function fmtDuration(seconds) {
    const total = Math.max(0, Math.round(seconds || 0));
    if (total < 60) return `${total} s`;
    const minutes = Math.round(total / 60);
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return rest ? `${hours} h ${rest} min` : `${hours} h`;
  }

  function estimateGenerationText(stats, durationSeconds) {
    const minutes = Math.max(0.5, (durationSeconds || 0) / 60);
    const perMinute = Number(stats?.average_seconds_per_minute) || DEFAULT_SECONDS_PER_MINUTE;
    const based = stats?.average_seconds_per_minute == null
      ? "uppskattning innan första körningen – sedan används din dators uppmätta hastighet"
      : `snitt ${Math.round(perMinute)} s per videominut från ${stats.count} tidigare körningar`;
    return `Beräknad tid: cirka ${fmtDuration(perMinute * minutes)} för ${fmtDuration(durationSeconds)} video (${based}).`;
  }

  async function refreshGenerationEstimate() {
    const element = document.getElementById("subtitleGenerateEstimate");
    if (!element) return;
    let stats = {};
    try { stats = await api("/api/subtitle-generator/stats"); } catch (_) { /* estimate still shown */ }
    if (!document.getElementById("subtitleGenerateEstimate")) return;
    element.textContent = estimateGenerationText(stats, S.player?.item?.duration || 0);
  }

  function renderSubtitleTracks() {
    const p = S.player;
    const container = document.getElementById("subtitleResults");
    if (!p || !container) return;
    const tracks = S.localSubtitleTracks || [];
    container.innerHTML = `<div class="subtitle-options">
      <button class="btn ${p.subtitleTrack ? "" : "primary"}" data-action="subtitle-off">Av – spela utan text</button>
      ${tracks.length ? tracks.map((track, idx) => `<button class="btn ${p.subtitleTrack?.name === track.name ? "primary" : ""}" data-action="subtitle-select" data-idx="${idx}">${esc(track.name)} <span class="res-sub">${esc(track.language || "Lokal")}</span></button>`).join("") : `<p class="empty">Inga lokala undertexter hittades. Sök online eller generera en undertext nedan.</p>`}
    </div>`;
  }

  async function openSubtitleMenu() {
    const p = S.player;
    if (!p) return;
    const playerOverlay = p.overlay;
    playerOverlay.classList.add("modal-open");
    openModal(`<h3>Undertexter</h3><p class="hint">Välj en undertext som ligger bredvid videon, sök online eller låt den lokala undertextgeneratorn skapa en.</p>
      <div class="modal-block">
        <h4>Generera undertext</h4>
        <p class="hint" id="subtitleGenerateEstimate">Beräknar tid…</p>
        <div class="field-row">
          <button class="btn" data-action="subtitle-generate" data-language="sv">Generera svenska</button>
          <button class="btn" data-action="subtitle-generate" data-language="en">Generera engelska</button>
        </div>
        <div class="gen-progress" id="subtitleGenerateBar" hidden><i id="subtitleGenerateFill"></i></div>
        <p class="hint" id="subtitleGenerateStatus"></p>
      </div>
      <div id="subtitleResults"><div class="spinner" style="margin:15px auto"></div></div>
      <div class="field-row" style="margin:14px 0"><select class="input" id="subtitleLanguage"><option value="sv">Svenska</option><option value="en">English</option></select>
      <button class="btn primary" data-action="subtitle-search">Sök online</button></div>`);
    const subtitleBackdrop = modalRoot.querySelector(".modal-backdrop");
    if (subtitleBackdrop) subtitleBackdrop.style.zIndex = "250";
    modalRoot.querySelector(".modal-close")?.focus();
    refreshGenerationEstimate();
    resumeGenerationWatch();
    try {
      const data = await api(`/api/subtitles/${p.item.id}`);
      S.localSubtitleTracks = data.subtitles || [];
      renderSubtitleTracks();
    } catch (err) {
      const container = document.getElementById("subtitleResults");
      if (container) container.innerHTML = `<p class="empty">${esc(err.message)}</p>`;
    }
  }

  function setGenerationButtonsDisabled(disabled) {
    document.querySelectorAll('[data-action="subtitle-generate"]').forEach((button) => { button.disabled = disabled; });
  }

  function renderGenerationProgress(job) {
    const bar = document.getElementById("subtitleGenerateBar");
    const fill = document.getElementById("subtitleGenerateFill");
    const status = document.getElementById("subtitleGenerateStatus");
    if (!bar || !fill || !status) return;
    const progress = Math.max(0, Math.min(1, Number(job.progress) || 0));
    bar.hidden = false;
    fill.style.width = `${(progress * 100).toFixed(1)}%`;
    if (job.state !== "running") return;
    const languageName = job.language === "sv" ? "svensk" : "engelsk";
    const elapsed = fmtTime(job.elapsed_seconds || 0);
    let eta = " · beräknar återstående tid…";
    if (progress > 0.015) {
      const remaining = (job.elapsed_seconds || 0) * (1 - progress) / progress;
      eta = ` · cirka ${fmtDuration(remaining)} kvar`;
    }
    status.textContent = `Genererar ${languageName} undertext: ${Math.round(progress * 100)} % · ${elapsed} förflutit${eta}`;
  }

  async function finishGeneration(job) {
    const p = S.player;
    const status = document.getElementById("subtitleGenerateStatus");
    const bar = document.getElementById("subtitleGenerateBar");
    if (bar) bar.hidden = true;
    setGenerationButtonsDisabled(false);
    if (!p) return;
    try {
      const data = await api(`/api/subtitles/${p.item.id}`);
      S.localSubtitleTracks = data.subtitles || [];
    } catch (_) { /* listan hämtas nästa gång rutan öppnas */ }
    const lang = job.language;
    const track = (S.localSubtitleTracks || []).find((entry) => entry.name.toLowerCase().endsWith(`.${lang}.srt`))
      || (S.localSubtitleTracks || []).find((entry) => entry.name.toLowerCase().endsWith(`.${lang}.vtt`));
    if (track) {
      p.subtitleTrack = track;
      applySubtitleTrack(p);
    }
    renderSubtitleTracks();
    const total = fmtDuration(job.result?.elapsed_seconds ?? job.elapsed_seconds);
    if (status) status.textContent = `Klart på ${total}${track ? ` – ${track.name} är påslagen.` : "."}`;
    toast(`Undertext klar på ${total}`, "ok");
    refreshGenerationEstimate();
  }

  async function watchGeneration(jobId) {
    while (true) {
      let job;
      try {
        job = await api(`/api/subtitle-jobs/${jobId}`);
      } catch (_) {
        await new Promise((resolve) => setTimeout(resolve, 2500));
        continue;
      }
      if (!document.getElementById("subtitleGenerateStatus")) return; // rutan stängd
      renderGenerationProgress(job);
      if (job.state === "running") {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        continue;
      }
      if (job.state === "error") {
        const status = document.getElementById("subtitleGenerateStatus");
        const bar = document.getElementById("subtitleGenerateBar");
        if (bar) bar.hidden = true;
        setGenerationButtonsDisabled(false);
        if (status) status.textContent = `Misslyckades: ${job.error || "okänt fel"}`;
        toast(job.error || "Undertextgenereringen misslyckades", "error");
        return;
      }
      await finishGeneration(job);
      return;
    }
  }

  async function resumeGenerationWatch() {
    const p = S.player;
    if (!p) return;
    try {
      const data = await api(`/api/subtitles/${p.item.id}/generate/status`);
      const job = data?.job;
      if (!job || job.state !== "running" || !document.getElementById("subtitleGenerateStatus")) return;
      setGenerationButtonsDisabled(true);
      renderGenerationProgress(job);
      await watchGeneration(job.id);
    } catch (_) { /* ingen pågående generering */ }
  }

  async function generateSubtitleFromPlayer(language) {
    const p = S.player;
    if (!p) return;
    setGenerationButtonsDisabled(true);
    const status = document.getElementById("subtitleGenerateStatus");
    if (status) status.textContent = "Startar undertextgenereringen…";
    try {
      const job = await api(`/api/subtitles/${p.item.id}/generate`, { method: "POST", body: { language, model: "small" } });
      renderGenerationProgress(job);
      await watchGeneration(job.id);
    } catch (err) {
      setGenerationButtonsDisabled(false);
      if (status) status.textContent = `Misslyckades: ${err.message}`;
      toast(err.message, "error");
    }
  }

  function selectSubtitle(index) {
    const track = S.localSubtitleTracks?.[index];
    if (!track || !S.player) return;
    S.player.subtitleTrack = track;
    applySubtitleTrack(S.player);
    closeModal();
    toast(`Undertext vald: ${track.name}`, "ok");
  }

  async function searchOnlineSubtitles() {
    const p = S.player;
    if (!p) return;
    const searchButton = document.querySelector('[data-action="subtitle-search"]');
    if (searchButton) { searchButton.disabled = true; searchButton.textContent = "Söker…"; }
    const language = document.getElementById("subtitleLanguage")?.value || "sv";
    const container = document.getElementById("subtitleResults");
    if (container) container.innerHTML = `<div class="spinner" style="margin:15px auto"></div>`;
    try {
      const data = await api(`/api/subtitles/${p.item.id}/search?language=${encodeURIComponent(language)}`);
      S.remoteSubtitleResults = data.results || [];
      if (!container) return;
      if (!S.remoteSubtitleResults.length) {
        container.innerHTML = `<p class="empty">Inga undertexter hittades på OpenSubtitles för valt språk.</p>`;
        return;
      }
      container.innerHTML = `<div class="subtitle-options">${S.remoteSubtitleResults.map((result, idx) => `<div class="subtitle-result"><div><strong>${esc(result.name)}</strong><div class="res-sub">${esc(result.language_name || result.language)} · ${Number(result.downloads || 0).toLocaleString("sv-SE")} nedladdningar${result.hearing_impaired ? " · hörselskadade" : ""}</div></div><button class="btn small primary" data-action="subtitle-download" data-idx="${idx}">Ladda ned</button></div>`).join("")}</div>`;
    } catch (err) {
      if (container) container.innerHTML = `<p class="empty">${esc(err.message)}</p>`;
    } finally {
      const searchButton = document.querySelector('[data-action="subtitle-search"]');
      if (searchButton) { searchButton.disabled = false; searchButton.textContent = "Sök online"; }
    }
  }

  async function downloadSubtitle(index) {
    const p = S.player;
    const result = S.remoteSubtitleResults?.[index];
    if (!p || !result) return;
    const button = document.querySelector(`[data-action="subtitle-download"][data-idx="${index}"]`);
    if (button) { button.disabled = true; button.textContent = "Laddar ned…"; }
    try {
      const saved = await api(`/api/subtitles/${p.item.id}/download`, { method: "POST", body: { file_id: result.file_id, language: result.language || document.getElementById("subtitleLanguage")?.value || "sv" } });
      const data = await api(`/api/subtitles/${p.item.id}`);
      S.localSubtitleTracks = data.subtitles || [];
      const container = document.getElementById("subtitleResults");
      if (container) {
        const local = S.localSubtitleTracks;
        container.innerHTML = `<div class="subtitle-options">
          <button class="btn ${p.subtitleTrack ? "" : "primary"}" data-action="subtitle-off">Av – spela utan text</button>
          ${local.length ? local.map((track, idx) => `<button class="btn ${p.subtitleTrack?.name === track.name ? "primary" : ""}" data-action="subtitle-select" data-idx="${idx}">${esc(track.name)} <span class="res-sub">${esc(track.language || "Lokal")}</span></button>`).join("") : `<p class="empty">Nedladdningen sparades, men ingen spelbar SRT/VTT-fil hittades.</p>`}
        </div>
        <p class="status-line">Nedladdad: ${esc(saved.name)}. Välj undertexten ovan för att slå på den.</p>
        ${S.remoteSubtitleResults?.length ? `<h4>Fler träffar</h4><div class="subtitle-options">${S.remoteSubtitleResults.map((entry, idx) => `<div class="subtitle-result"><div><strong>${esc(entry.name)}</strong><div class="res-sub">${esc(entry.language_name || entry.language)} · ${Number(entry.downloads || 0).toLocaleString("sv-SE")} nedladdningar</div></div><button class="btn small primary" data-action="subtitle-download" data-idx="${idx}" ${idx === index ? "disabled" : ""}>${idx === index ? "Nedladdad" : "Ladda ned"}</button></div>`).join("")}</div>` : ""}`;
      }
      toast(`Undertext sparad: ${saved.name}. Välj den i listan för att använda den.`, "ok");
    } catch (err) {
      toast(err.message, "error");
      if (button) { button.disabled = false; button.textContent = "Försök igen"; }
    }
  }

  function saveProgress() {
    const p = S.player;
    if (!p || p.ended) return;
    const abs = p.offset + (p.video.currentTime || 0);
    api(`/api/progress/${p.item.id}`, { method: "POST", body: { position: abs, duration: p.total } })
      .catch(() => {});
  }

  async function restartAt(offset) {
    const p = S.player;
    if (!p) return;
    saveProgress();
    const previousSid = p.play?.sid;
    detachMedia();
    try {
      const play = await api(`/api/play/${p.item.id}?start=${Math.max(0, offset)}&direct=0&mode=${p.mode || "auto"}`);
      if (previousSid && previousSid !== play.sid) api(`/api/hls/stop/${previousSid}`, { method: "POST" }).catch(() => {});
      p.play = play;
      p.mode = play.mode;
      p.offset = play.mode === "direct" ? 0 : (play.start || 0);
      attachMedia(play, p.video);
      applySubtitleTrack(p);
      toastEl.className = "toast";
    } catch (err) {
      toast(`Kunde inte hoppa: ${err.message}`, "error");
    }
  }

  async function closePlayer() {
    const p = S.player;
    if (!p) return;
    if (inFullscreen()) setPlayerFullscreen(false);
    if (!p.ended) saveProgress();
    clearInterval(p.saveTimer);
    stopSubtitleLoop(p);
    p.resizeObserver?.disconnect();
    if (p.onWindowResize) window.removeEventListener("resize", p.onWindowResize);
    if (p.onKeyWake) document.removeEventListener("keydown", p.onKeyWake);
    clearTimeout(p.hideTimer);
    if (S.hls) { S.hls.destroy(); S.hls = null; }
    if (p.play?.sid) api(`/api/hls/stop/${p.play.sid}`, { method: "POST" }).catch(() => {});
    p.overlay.remove();
    document.body.classList.remove("locked");
    S.player = null;
    // Tillbaka från spelaren: sätt ringen på samma avsnitt igen, så pilarna
    // fortsätter i listan i stället för att hoppa upp till sidans början.
    const back = S.playerReturn;
    const episodeIndex = back?.classList?.contains("ep-row")
      ? [...document.querySelectorAll(".ep-row")].indexOf(back) : -1;
    const navMode = document.body.classList.contains("nav-mode");
    S.playerReturn = null;
    if (location.hash.startsWith("#/title/show")) {
      try { await renderShow(location.hash.split("/").pop()); } catch (_) { /* ignore */ }
      if (navMode) {
        const rows = [...document.querySelectorAll(".ep-row")];
        if (episodeIndex >= 0 && rows[episodeIndex]) setNavFocus(rows[episodeIndex], { silent: true });
      }
    } else if (navMode && back?.isConnected) {
      setNavFocus(back, { silent: true });
    }
  }

  function togglePlayer() {
    const p = S.player;
    if (!p) return;
    if (p.video.paused) p.video.play().catch(() => {});
    else p.video.pause();
  }

  /* ------------------------------------------------- insyn i fullskärm
     Rad, topprad, mittknapp och muspekare göms i fullskärm efter en stunds
     inaktivitet – som på YouTube. Allt som rör musen eller tangentbordet tar
     tillbaka dem, och i fönsterläge syns raden alltid. */
  const PLAYER_IDLE_MS = 2800;

  function playerUiIdleAllowed(p) {
    return Boolean(p) && inFullscreen() && !p.video.paused && !p.video.ended
      && !p.overlay.classList.contains("modal-open");
  }

  function showPlayerUi(restartTimer = true) {
    const p = S.player;
    if (!p) return;
    p.overlay.classList.remove("hide-ui");
    clearTimeout(p.hideTimer);
    p.hideTimer = null;
    if (!restartTimer || !playerUiIdleAllowed(p)) return;
    p.hideTimer = setTimeout(() => {
      const still = S.player;
      if (!playerUiIdleAllowed(still)) return;
      still.overlay.classList.add("hide-ui");
    }, PLAYER_IDLE_MS);
  }

  // Skalets brygga (Qt-appen) sätts upp första gången den behövs.
  let shellFullscreen = false;

  function fullscreenElement() {
    return document.fullscreenElement || document.webkitFullscreenElement || null;
  }

  function inFullscreen() {
    return Boolean(fullscreenElement()) || shellFullscreen;
  }

  function syncFullscreenButton() {
    const button = document.getElementById("fsBtn");
    if (button) button.title = inFullscreen() ? "Lämna fullskärm (f)" : "Fullskärm (f)";
    const player = S.player;
    if (!player) return;
    player.overlay.classList.toggle("is-fullscreen", inFullscreen());
    // Fullskärm utan musrörelse ska också tona bort raden efter en stund.
    showPlayerUi();
    // Raden byter mellan egen plats och överlägg – mät om direkt och efter
    // att layouten hunnit sätta sig.
    syncPlayerMetrics(player);
    setTimeout(() => syncPlayerMetrics(player), 80);
  }

  function shellBridge() {
    if (window.__shellBridge !== undefined) return Promise.resolve(window.__shellBridge);
    if (!window.QWebChannel || !window.qt || !window.qt.webChannelTransport) {
      // qwebchannel.js injiceras först när dokumentet är klart; vänta in det
      // i stället för att ge upp direkt när app.js körs under parsningen.
      if (document.readyState === "complete") {
        window.__shellBridge = null;
        return Promise.resolve(null);
      }
      return new Promise((resolve) => {
        window.addEventListener("load", () => { shellBridge().then(resolve); }, { once: true });
      });
    }
    return new Promise((resolve) => {
      try {
        new QWebChannel(window.qt.webChannelTransport, (channel) => {
          const shell = channel.objects.shell || null;
          window.__shellBridge = shell;
          if (shell && shell.fullscreenChanged) {
            shell.fullscreenChanged.connect((on) => { shellFullscreen = Boolean(on); syncFullscreenButton(); });
          }
          resolve(shell);
        });
      } catch (_) {
        window.__shellBridge = null;
        resolve(null);
      }
    });
  }

  // Fönsterknappar, flytt och kantdragning i det ramlösa desktop-skalet.
  function installWindowChrome(shell) {
    document.body.classList.add("shell");
    if (shell.maximizedChanged) {
      shell.maximizedChanged.connect((on) => document.body.classList.toggle("maximized", Boolean(on)));
    }

    document.getElementById("windowControls")?.addEventListener("click", (ev) => {
      const button = ev.target.closest("[data-window-action]");
      if (!button) return;
      const action = button.dataset.windowAction;
      if (action === "minimize") shell.minimize();
      else if (action === "maximize") shell.toggleMaximize();
      else if (action === "close") shell.closeWindow();
    });

    const topbar = document.getElementById("topbar");
    const interactive = (target) => Boolean(target.closest("input, button, a, select, .searchbox, .window-controls"));
    if (topbar) {
      topbar.addEventListener("mousedown", (ev) => {
        if (ev.button !== 0 || interactive(ev.target)) return;
        shell.startDrag();
      });
      topbar.addEventListener("dblclick", (ev) => {
        if (interactive(ev.target)) return;
        shell.toggleMaximize();
      });
    }

    installEdgeResize(shell);
  }

  function installEdgeResize(shell) {
    const MARGIN = 6;
    const CURSORS = {
      top: "ns-resize", bottom: "ns-resize", left: "ew-resize", right: "ew-resize",
      "top-left": "nwse-resize", "bottom-right": "nwse-resize",
      "top-right": "nesw-resize", "bottom-left": "nesw-resize",
    };
    let edge = "";
    const detect = (ev) => {
      if (document.body.classList.contains("maximized") || inFullscreen()) return "";
      const top = ev.clientY <= MARGIN;
      const bottom = ev.clientY >= window.innerHeight - MARGIN;
      const left = ev.clientX <= MARGIN;
      const right = ev.clientX >= window.innerWidth - MARGIN;
      if (!top && !bottom && !left && !right) return "";
      const vertical = top ? "top" : bottom ? "bottom" : "";
      const horizontal = left ? "left" : right ? "right" : "";
      return vertical && horizontal ? `${vertical}-${horizontal}` : (vertical || horizontal);
    };
    document.addEventListener("mousemove", (ev) => {
      const next = detect(ev);
      if (next === edge) return;
      edge = next;
      document.body.style.cursor = CURSORS[edge] || "";
    });
    document.addEventListener("mousedown", (ev) => {
      if (ev.button !== 0) return;
      const found = detect(ev);
      if (!found) return;
      ev.preventDefault();
      ev.stopPropagation();
      shell.startResize(found);
    }, true);
  }

  function exitPlayerFullscreen() {
    if (!fullscreenElement()) return;
    try {
      const result = document.exitFullscreen
        ? document.exitFullscreen()
        : (document.webkitExitFullscreen ? document.webkitExitFullscreen() : null);
      // Ett avvisat löfte här betyder bara att vyn redan lämnat fullskärm.
      if (result && typeof result.catch === "function") result.catch(() => {});
    } catch (_) { /* redan ute */ }
  }

  async function setPlayerFullscreen(on) {
    const p = S.player;
    if (!p) return;
    if (document.fullscreenEnabled) {
      try {
        if (on && !fullscreenElement()) {
          const request = p.overlay.requestFullscreen || p.overlay.webkitRequestFullscreen;
          if (!request) throw new Error("fullskärm stöds inte i den här vyn");
          const result = request.call(p.overlay);
          if (result && typeof result.catch === "function") {
            result.catch((err) => toast(`Kunde inte växla fullskärm: ${err.message}`, "error"));
          }
        } else if (!on && fullscreenElement()) {
          exitPlayerFullscreen();
        }
      } catch (err) {
        toast(`Kunde inte växla fullskärm: ${err.message}`, "error");
      }
      return;
    }
    const shell = await shellBridge();
    if (!shell) {
      toast("Fullskärm stöds inte i den här vyn", "error");
      return;
    }
    if (on === shellFullscreen) return;
    shellFullscreen = on;
    shell.setFullscreen(on);
    syncFullscreenButton();
  }

  function togglePlayerFullscreen() {
    return setPlayerFullscreen(!inFullscreen());
  }

  function seekPlayer(delta) {
    const p = S.player;
    if (!p) return;
    const target = Math.max(0, Math.min(p.total - 2, p.offset + p.video.currentTime + delta));
    restartAt(target);
  }

  async function removeFromContinue(itemId) {
    try {
      await api(`/api/progress/${itemId}`, { method: "DELETE" });
      toast("Borttagen från Fortsätt titta – filen ligger kvar på disken", "ok");
      await renderHome();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  function openDeleteDialog(itemId, title, path) {
    openModal(`<h3>Radera från disk?</h3>
      <p class="hint">Filen tas bort permanent från datorn och titeln försvinner ur biblioteket. Det går inte att ångra.</p>
      <div class="modal-block">
        <strong>${esc(title || "Titeln")}</strong>
        <div class="res-sub mono" style="margin-top:6px;word-break:break-all">${esc(path || "")}</div>
      </div>
      <div class="modal-actions">
        <button class="btn ghost" data-action="close-modal">Avbryt</button>
        <button class="btn danger" data-action="delete-file-confirm" data-id="${itemId}">Radera permanent</button>
      </div>`);
  }

  async function deleteItemNow(itemId) {
    const button = document.querySelector('[data-action="delete-file-confirm"]');
    if (button) { button.disabled = true; button.textContent = "Raderar…"; }
    try {
      const result = await api(`/api/item/${itemId}?files=1`, { method: "DELETE" });
      closeModal();
      const count = (result.files || []).length;
      toast(count ? `Raderade ${count} fil${count === 1 ? "" : "er"} från disken` : "Titeln togs bort ur biblioteket", "ok");
      if (location.hash.startsWith("#/title/show")) await renderShow(location.hash.split("/").pop());
      else if (location.hash && location.hash !== "#/") location.hash = "#/";
      else await renderHome();
    } catch (err) {
      toast(err.message, "error");
      if (button) { button.disabled = false; button.textContent = "Radera permanent"; }
    }
  }

  async function markWatched(itemId, watched) {
    try {
      await api(`/api/watched/${itemId}`, { method: "POST", body: { watched } });
      if (S.show) {
        S.show = await api(`/api/show/${S.show.show.id}`);
        const list = document.getElementById("episodeList");
        if (list) list.innerHTML = episodesHtml();
      }
    } catch (err) { toast(err.message, "error"); }
  }

  /* ------------------------------------------------------------- casting */
  async function castPlay(itemId, start = 0) {
    try {
      const uuid = S.cast?.device?.uuid || null;
      toast("Startar på Chromecast…");
      const res = await api("/api/cast/play", { method: "POST", body: { item_id: Number(itemId), start: Number(start), uuid } });
      toast("Spelar på Chromecast", "ok");
      pollCast(true);
    } catch (err) {
      toast(`Cast misslyckades: ${err.message}`, "error");
      openCastModal();
    }
  }

  async function pollCast(force = false) {
    try {
      const st = await api("/api/cast/status");
      S.cast = st;
      const dot = document.getElementById("castDot");
      dot.classList.toggle("on", !!st.connected);
      renderCastBar(st);
      const now = Date.now();
      if (st.connected && st.current && st.state === "PLAYING" && now - S.lastCastSave > 12000) {
        S.lastCastSave = now;
        api(`/api/progress/${st.current.item_id}`, {
          method: "POST",
          body: { position: st.position || 0, duration: st.duration || 0 },
        }).catch(() => {});
      }
    } catch (_) { /* servern kanske startar om */ }
  }

  function renderCastBar(st) {
    if (!st.connected) {
      castbar.classList.add("hidden");
      S.castBarKey = null;
      return;
    }
    const cur = st.current;
    const key = cur ? `${cur.item_id}-${cur.start}` : "idle";
    if (key !== S.castBarKey) {
      S.castBarKey = key;
      castbar.classList.remove("hidden");
      castbar.innerHTML = cur ? `
        ${cur.thumb ? `<img class="cast-thumb" src="${esc(cur.thumb)}" alt="">` : `<div class="cast-thumb"></div>`}
        <div class="cast-now">
          <div class="cast-info">
            <div class="cast-title">${esc(cur.title)}</div>
            <div class="cast-sub">${esc(cur.subtitle || "")} · ${esc(st.device?.name || "Chromecast")}</div>
          </div>
        </div>
        <div class="cast-progress">
          <span class="time" id="castPos">0:00</span>
          <div class="seek" id="castSeek"><i></i><b></b></div>
          <span class="time" id="castDur">${fmtTime(st.duration || 0)}</span>
        </div>
        <div class="cast-actions">
          <button class="icon-btn" data-action="cast-toggle" title="Spela/pausa">
            <svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="${st.state === "PLAYING" ? "M6 5h4v14H6zM14 5h4v14h-4z" : "M8 5v14l11-7z"}"/></svg>
          </button>
          <button class="icon-btn" data-action="cast-stop" title="Stopp">
            <svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M6 6h12v12H6z"/></svg>
          </button>
          <input class="vol" type="range" min="0" max="1" step="0.05" value="${st.volume ?? 0}" data-action="cast-volume">
          <button class="icon-btn" data-action="cast-disconnect" title="Koppla från">
            <svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M18.3 5.7L12 12l6.3 6.3-1.4 1.4L10.6 13.4 5.7 18.3 4.3 16.9l4.9-4.9L3 5.7 4.4 4.3l15.3 15.3-1.4 1.4z"/></svg>
          </button>
        </div>` : `
        <div class="cast-now"><div class="cast-info"><div class="cast-title">Ansluten till ${esc(st.device?.name || "Chromecast")}</div>
          <div class="cast-sub">Inget spelas</div></div></div>
        <div style="flex:1"></div>
        <div class="cast-actions"><button class="btn small" data-action="cast-disconnect">Koppla från</button></div>`;
    }
    if (cur) {
      const pos = st.position || 0;
      const ratio = st.duration ? Math.min(1, pos / st.duration) : 0;
      const posEl = document.getElementById("castPos");
      if (posEl) posEl.textContent = fmtTime(pos);
      const durEl = document.getElementById("castDur");
      if (durEl) durEl.textContent = fmtTime(st.duration || 0);
      const seek = document.getElementById("castSeek");
      if (seek) {
        seek.querySelector("i").style.width = pct(ratio);
        seek.querySelector("b").style.left = pct(ratio);
        seek.onclick = (ev) => {
          const rect = seek.getBoundingClientRect();
          const r = Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width));
          castPlay(cur.item_id, Math.max(0, r * (st.duration || 0) - 2));
        };
      }
      const pauseBtn = castbar.querySelector('[data-action="cast-toggle"] path');
      if (pauseBtn) pauseBtn.setAttribute("d", st.state === "PLAYING" ? "M6 5h4v14H6zM14 5h4v14h-4z" : "M8 5v14l11-7z");
    }
    castbar.classList.remove("hidden");
  }

  async function openCastModal() {
    openModal(`<h3>Casta till Chromecast</h3><p class="hint">Appen använder standardmottagaren och anpassar allt till H.264/1080p för äldre enheter.</p>
      <div id="deviceList"><div class="status-line"><span class="spinner" style="width:16px;height:16px"></span> Söker på nätverket…</div></div>
      <div class="field" style="margin-top:16px">
        <label>Anslut direkt via IP (om sökningen inte hittar enheten)</label>
        <div class="field-row"><input class="input" id="castHost" placeholder="192.168.0.7">
          <button class="btn small" data-action="cast-connect-host">Anslut</button></div>
      </div>`);
    try {
      const data = await api("/api/cast/devices?force=1");
      S.devices = data.devices || [];
      const list = document.getElementById("deviceList");
      if (!list) return;
      list.innerHTML = S.devices.length
        ? S.devices.map((d) => `<div class="device" data-action="cast-connect" data-uuid="${esc(d.uuid)}" data-host="${esc(d.host)}">
            <div class="device-icon">📺</div>
            <div><div class="device-name">${esc(d.name)}</div>
              <div class="device-sub">${esc(d.model || "Chromecast")} · ${esc(d.host)}${d.old ? " · anpassas för 1:a gen" : ""}</div></div>
          </div>`).join("")
        : `<p class="empty">Ingen Chromecast hittades. Kontrollera att datorn och Chromecasten är på samma nätverk (2,4 GHz för 1:a gen).</p>`;
    } catch (err) {
      const list = document.getElementById("deviceList");
      if (list) list.innerHTML = `<p class="empty">${esc(err.message)}</p>`;
    }
  }

  /* ------------------------------------------------------------- downloads */
  async function renderDownloads() {
    app.innerHTML = `
      <div class="page">
        <h1 class="page-title">Nedladdningar</h1>
        <p class="page-sub">Sök via Prowlarr/Jackett eller klistra in en magnetlänk. Klara filer importeras automatiskt till biblioteket.</p>
        <div class="tabs">
          <button class="tab ${S.dlTab === "search" ? "active" : ""}" data-action="dl-tab" data-tab="search">Sök & lägg till</button>
          <button class="tab ${S.dlTab === "active" ? "active" : ""}" data-action="dl-tab" data-tab="active">Aktiva</button>
          <button class="tab ${S.dlTab === "imported" ? "active" : ""}" data-action="dl-tab" data-tab="imported">Importerat</button>
        </div>
        <div id="dlBody"><div class="spinner"></div></div>
      </div>`;
    await renderDownloadsBody();
  }

  async function renderDownloadsBody() {
    const body = document.getElementById("dlBody");
    if (!body) return;
    clearInterval(S.downloadsTimer); S.downloadsTimer = null;
    const data = await api("/api/downloads");
    S.dlData = data;

    if (S.dlTab === "search") {
      body.innerHTML = `
        <div class="panel" style="margin-bottom:18px">
          <h3>Sök torrents</h3>
          <p class="hint">Söker via Prowlarr/Jackett på datorn och skickar valda träffar direkt till qBittorrent.</p>
          <div class="search-row">
            <input class="input" id="dlQuery" placeholder="t.ex. The Bear S02 1080p" value="${esc(S.dlQuery)}">
            <button class="btn primary" data-action="dl-search">Sök</button>
          </div>
          <div id="dlResults">${S.dlResults ? dlResultsHtml(S.dlResults) : ""}</div>
        </div>
        <div class="panel">
          <h3>Magnet / .torrent</h3>
          <p class="hint">Lägg till direkt i qBittorrent. Filer som blir klara importeras automatiskt.</p>
          <div class="field"><textarea class="input" id="magnetInput" placeholder="magnet:?xt=urn:btih:…"></textarea></div>
          <div class="field-row">
            <button class="btn primary" data-action="dl-add-magnet">Lägg till magnet</button>
            <label class="btn" style="cursor:pointer">Välj .torrent<input type="file" id="torrentFile" accept=".torrent" hidden></label>
          </div>
          ${data.error ? `<div class="status-line"><span class="dot bad"></span>qBittorrent: ${esc(data.error)}</div>`
            : `<div class="status-line"><span class="dot ok"></span>qBittorrent är ansluten</div>`}
          <div class="status-line"><span class="dot ${data.settings?.import?.enabled ? "ok" : "warn"}"></span>
            Auto-import: ${data.settings?.import?.enabled ? "på" : "av"} (${esc(data.settings?.import?.mode || "hardlink")})</div>
        </div>`;
      return;
    }

    if (S.dlTab === "active") {
      body.innerHTML = `
        ${data.error ? `<div class="panel"><div class="status-line"><span class="dot bad"></span>${esc(data.error)}</div>
          <p class="hint">Aktivera Web UI i qBittorrent (Verktyg → Inställningar → Web UI) och fyll i adress/uppgifter i appens inställningar.</p></div>` : ""}
        <div id="torrentList">${torrentsHtml(data.torrents)}</div>`;
      S.downloadsTimer = setInterval(refreshTorrents, 4000);
      return;
    }

    body.innerHTML = `<div class="list">
      <div class="list-head"><span>Fil</span><span>Torrent</span><span>Importerad</span></div>
      ${(data.imports || []).length ? data.imports.map((i) => `
        <div class="list-row" style="grid-template-columns:1fr 1fr 160px">
          <div><div class="res-title">${esc((i.path || "").split(/[\\/]/).pop() || i.name || "")}</div>
            <div class="res-sub">${esc(i.status || "")}</div></div>
          <div class="res-sub mono" style="overflow:hidden;text-overflow:ellipsis">${esc(i.name || "")}</div>
          <div class="res-sub">${i.imported_at ? new Date(i.imported_at * 1000).toLocaleString("sv-SE") : ""}</div>
        </div>`).join("") : `<p class="empty" style="padding:20px">Inget importerat än.</p>`}
    </div>`;
  }

  function torrentsHtml(torrents) {
    if (!torrents || !torrents.length) return `<p class="empty">Inga torrenter i qBittorrent.</p>`;
    return torrents.map((t) => `
      <div class="torrent" data-hash="${esc(t.hash)}">
        <div class="torrent-head">
          <div><div class="torrent-name">${esc(t.name)}</div>
            <div class="torrent-meta">
              <span>${fmtBytes(t.size)}</span>
              <span>${t.percent}%</span>
              <span class="state-tag">${esc(t.state_text)}</span>
              ${t.download_speed ? `<span>↓ ${fmtSpeed(t.download_speed)}</span>` : ""}
              ${t.upload_speed ? `<span>↑ ${fmtSpeed(t.upload_speed)}</span>` : ""}
              <span>${t.seeds} seeds</span>
              <span>${t.leechers} leechers</span>
              ${t.eta && !t.completed ? `<span>${fmtEta(t.eta)} kvar</span>` : ""}
            </div>
          </div>
        </div>
        <div class="bar ${t.completed ? "done" : ""}"><i style="width:${Math.max(2, t.percent)}%"></i></div>
        <div class="torrent-actions">
          <button class="btn small" data-action="torrent-files" data-hash="${esc(t.hash)}">Filer / prioritet</button>
          ${["pausedDL", "pausedUP", "stoppedDL", "stoppedUP"].includes(t.state)
            ? `<button class="btn small" data-action="torrent" data-hash="${esc(t.hash)}" data-act="resume">Återuppta</button>`
            : `<button class="btn small" data-action="torrent" data-hash="${esc(t.hash)}" data-act="pause">Pausa</button>`}
          ${t.completed ? `<button class="btn small primary" data-action="torrent" data-hash="${esc(t.hash)}" data-act="import">Importera till biblioteket</button>` : ""}
          <button class="btn small" data-action="torrent" data-hash="${esc(t.hash)}" data-act="delete">Ta bort torrent</button>
          <button class="btn small danger" data-action="torrent" data-hash="${esc(t.hash)}" data-act="delete_files">Ta bort + filer</button>
        </div>
      </div>`).join("");
  }

  async function refreshTorrents() {
    if (S.dlTab !== "active") return;
    try {
      const data = await api("/api/downloads");
      const list = document.getElementById("torrentList");
      if (list) list.innerHTML = torrentsHtml(data.torrents);
    } catch (_) { /* ignore */ }
  }

  function dlResultsHtml(data) {
    if (!data) return "";
    const errors = (data.errors || []).map((e) => `<div class="status-line"><span class="dot bad"></span>${esc(e)}</div>`).join("");
    if (!data.configured) return `<p class="empty">Ingen indexerare konfigurerad. Lägg till och testa Prowlarr eller Jackett under <a href="#/settings" style="color:var(--accent)">Inställningar → Indexerare</a>.</p>`;
    if (!data.results?.length) return `${errors}<p class="empty">${data.errors?.length ? "Sökningen misslyckades hos indexeraren. Kontrollera URL och API-nyckel i Inställningar." : "Inga träffar för sökningen. Prova en annan titel."}</p>`;
    return `${errors}
      <div class="list">
        <div class="list-head"><span>Namn</span><span>Indexerare</span><span>Storlek</span><span>Seeds</span><span></span></div>
        ${data.results.map((r, idx) => `
          <div class="list-row">
            <div style="min-width:0"><div class="res-title" title="${esc(r.title)}">${esc(r.title)}</div>
              <div class="res-sub">${esc((r.categories || []).join(", "))}</div></div>
            <div class="res-sub">${esc(r.indexer)}</div>
            <div>${fmtBytes(r.size)}</div>
            <div class="seeds ${r.seeders < 3 ? "low" : ""}">${r.seeders}</div>
            <div><button class="btn small primary" data-action="dl-add-result" data-idx="${idx}">Ladda ner</button></div>
          </div>`).join("")}
      </div>`;
  }

  /* ------------------------------------------------------------- settings */
  async function renderSettings() {
    const st = await api("/api/state");
    const subtitleStats = await api("/api/subtitle-generator/stats");
    S.settings = st.settings;
    const s = st.settings;
    const qbit = s.qbit || {};
    const prowlarr = s.prowlarr || {};
    const jackett = s.jackett || {};
    const imp = s.import || {};
    const tr = s.transcode || {};

    app.innerHTML = `
      <div class="page page-narrow">
        <h1 class="page-title">Inställningar</h1>
        <p class="page-sub">Allt sparas lokalt i <span class="mono">data/settings.json</span>.</p>

        <div class="settings-grid">
          <div class="panel">
            <h3>Bibliotek</h3>
            <p class="hint">En mapp per rad. Alla videofiler under mapparna hittas automatiskt.</p>
            <div class="field"><textarea class="input" id="setRoots" spellcheck="false">${esc((s.library_roots || []).join("\n"))}</textarea></div>
            <div class="field-row">
              <button class="btn primary" data-action="save-settings">Spara</button>
              <button class="btn" data-action="scan">Skanna om</button>
              <button class="btn ghost" data-action="match-again">Matcha om metadata</button>
            </div>
            <div id="scanStatus" class="status-line">
              <span class="dot ${st.scan?.running ? "warn" : "ok"}"></span>
              ${esc(st.scan?.message || "Redo")} ·
              ${st.stats?.movies || 0} filmer, ${st.stats?.shows || 0} serier, ${fmtBytes(st.stats?.bytes || 0)}
            </div>
          </div>

          <div class="panel">
            <h3>TMDB (omslag & information)</h3>
            <p class="hint">Gratis nyckel från themoviedb.org → Settings → API. Behövs för affischer, handling och betyg.</p>
            <div class="field"><label>API-nyckel (v3) eller läs-token (v4)</label>
              <input class="input" id="setTmdb" type="password" placeholder="${s.tmdb_key_set ? "•••••••• (sparad)" : "Klistra in nyckel"}"></div>
            <div class="field-row">
              <button class="btn primary" data-action="save-settings">Spara</button>
              <button class="btn" data-action="test-tmdb">Testa</button>
              <span id="tmdbStatus" class="status-line" style="margin:0">
                <span class="dot ${s.tmdb_key_set ? "ok" : "bad"}"></span>${s.tmdb_key_set ? "Nyckel sparad" : "Ingen nyckel"}</span>
            </div>
          </div>

          <div class="panel">
            <h3>qBittorrent</h3>
            <p class="hint">Aktivera Web UI i qBittorrent under Verktyg → Inställningar → Web UI.</p>
            <div class="field"><label>Adress</label><input class="input" id="setQbitUrl" value="${esc(qbit.url || "http://localhost:8080")}"></div>
            <div class="field"><label>Användarnamn</label><input class="input" id="setQbitUser" value="${esc(qbit.username || "")}"></div>
            <div class="field"><label>Lösenord</label><input class="input" id="setQbitPass" type="password" placeholder="${qbit.password_set ? "•••••••• (sparat)" : "Lösenord"}"></div>
            <div class="field-row">
              <button class="btn primary" data-action="save-settings">Spara</button>
              <button class="btn" data-action="test-qbit">Testa anslutning</button>
            </div>
            <div id="qbitStatus" class="status-line"></div>
          </div>

          <div class="panel">
            <h3>Indexerare</h3>
            <p class="hint">Prowlarr eller Jackett samlar dina torrentsajter. Används för sökningen under Nedladdningar.</p>
            <div class="field"><label>Prowlarr-adress</label><input class="input" id="setProwlarrUrl" value="${esc(prowlarr.url || "")}" placeholder="http://localhost:9696"></div>
            <div class="field"><label>Prowlarr API-nyckel</label><input class="input" id="setProwlarrKey" type="password" placeholder="${prowlarr.api_key_set ? "•••••••• (sparad)" : "API-nyckel"}"></div>
            <div class="field"><label>Jackett-adress</label><input class="input" id="setJackettUrl" value="${esc(jackett.url || "")}" placeholder="http://localhost:9117"></div>
            <div class="field"><label>Jackett API-nyckel</label><input class="input" id="setJackettKey" type="password" placeholder="${jackett.api_key_set ? "•••••••• (sparad)" : "API-nyckel"}"></div>
            <div class="field-row">
              <button class="btn primary" data-action="save-settings">Spara</button>
              <button class="btn" data-action="test-indexers">Testa</button>
            </div>
            <div id="indexerStatus" class="status-line"></div>
          </div>

          <div class="panel">
            <h3>Generera undertexter</h3>
            <p class="hint">Kör den lokala Undertextgeneratorn. Genomsnittlig genereringstid: ${subtitleStats.average_seconds_per_minute == null ? "inte beräknad ännu" : `${subtitleStats.average_seconds_per_minute} sekunder per videominut`} (${subtitleStats.count} lyckade genereringar).</p>
          </div>            <div class="panel">
              <h3>Uppspelningslogg</h3>
              <p class="hint">Visar diagnostik från senaste uppspelningsförsöken i den här app-sessionen. Kopiera loggen efter ett fel.</p>
              <p class="hint mono">${esc(codecSummary())}</p>
              <div class="field-row">
                <button class="btn" data-action="copy-playback-log">Kopiera logg</button>
                <button class="btn ghost" data-action="clear-playback-log">Rensa logg</button>
              </div>
              <pre id="playbackLogView" class="playback-log">${esc(S.playbackLogs.slice(-30).map((entry) => `${entry.at} ${entry.event} ${JSON.stringify(entry.details)}`).join("\\n") || "Inga uppspelningsfel loggade ännu.")}</pre>
            </div>

            <div class="panel">
            <h3>Undertexter (OpenSubtitles)</h3>
            <p class="hint">Lägg till en API-nyckel från ditt OpenSubtitles-konto för att söka och hämta svenska eller engelska undertexter. Nedladdade filer sparas bredvid videon.</p>
            <div class="field"><label>OpenSubtitles API key</label>
              <input class="input" id="setSubtitleKey" type="password" placeholder="${s.opensubtitles?.api_key_set ? "•••••••• (sparad)" : "API key"}"></div>
            <div class="field-row"><button class="btn primary" data-action="save-settings">Spara</button>
              <span class="status-line"><span class="dot ${s.opensubtitles?.api_key_set ? "ok" : "warn"}"></span>${s.opensubtitles?.api_key_set ? "API-nyckel sparad" : "Nyckel krävs för onlinesökning"}</span></div>
          </div>

          <div class="panel">
            <h3>Import & nedladdade filer</h3>
            <p class="hint">När en torrent är klar hittas den största videofilen, döps om och läggs i biblioteket.</p>
            <label class="check"><input type="checkbox" id="setImportEnabled" ${imp.enabled ? "checked" : ""}> Importera automatiskt</label>
            <div class="field"><label>Läge</label>
              <select class="input" id="setImportMode">
                <option value="hardlink" ${imp.mode === "hardlink" ? "selected" : ""}>Hårdlänk (rekommenderas – delar filen, inget extra utrymme)</option>
                <option value="copy" ${imp.mode === "copy" ? "selected" : ""}>Kopiera</option>
                <option value="move" ${imp.mode === "move" ? "selected" : ""}>Flytta (slutar seeda)</option>
              </select></div>
            <div class="field"><label>Filmmapp (tom = biblioteksmapp/Filmer)</label><input class="input" id="setMoviesDir" value="${esc(imp.movies_dir || "")}"></div>
            <div class="field"><label>Seriemapp (tom = biblioteksmapp/Serier)</label><input class="input" id="setTvDir" value="${esc(imp.tv_dir || "")}"></div>
            <div class="field"><label>Nedladdningsmapp för nya torrenter (tom = qBittorrents standard)</label><input class="input" id="setDownloadDir" value="${esc(imp.download_dir || "")}"></div>
            <button class="btn primary" data-action="save-settings">Spara</button>
          </div>

          <div class="panel">
            <h3>Chromecast & transcoding</h3>
            <p class="hint">1:a generationens Chromecast klarar H.264 High ≤ 1080p30 + AAC. Allt annat (HEVC, 10-bit, DTS/AC3) transkodas live.</p>
            <div class="field"><label>Max höjd</label>
              <select class="input" id="setMaxHeight">
                ${[720, 1080].map((h) => `<option value="${h}" ${Number(tr.max_height) === h ? "selected" : ""}>${h}p</option>`).join("")}
              </select></div>
            <div class="field"><label>Max videobitrate</label>
              <select class="input" id="setBitrate">
                ${["4M", "6M", "8M", "12M"].map((b) => `<option value="${b}" ${tr.max_bitrate === b ? "selected" : ""}>${b}</option>`).join("")}
              </select></div>
            <label class="check"><input type="checkbox" id="setForceTranscode" ${tr.force ? "checked" : ""}> Tvinga transkodning (om direct play strular)</label>
            <div class="field-row">
              <button class="btn primary" data-action="save-settings">Spara</button>
              <button class="btn" data-action="open-cast-modal">Hantera Chromecast</button>
            </div>
            <div class="status-line"><span class="dot ${st.ffmpeg ? "ok" : "bad"}"></span>ffmpeg: ${st.ffmpeg ? "installerad" : "saknas"}</div>
            <div class="status-line"><span class="dot ${st.cast?.connected ? "ok" : ""}"></span>${st.cast?.connected ? `Ansluten: ${esc(st.cast.device?.name || "")}` : "Ingen Chromecast ansluten"}</div>
            <div class="status-line" style="color:var(--muted)">Serveradress för cast: <span class="mono">${esc(st.base_url)}</span></div>
          </div>
        </div>
      </div>`;
  }

  async function saveSettings() {
    const get = (id) => document.getElementById(id);
    const payload = {
      library_roots: get("setRoots").value.split("\n").map((v) => v.trim()).filter(Boolean),
      import: {
        enabled: get("setImportEnabled").checked,
        mode: get("setImportMode").value,
        movies_dir: get("setMoviesDir").value.trim(),
        tv_dir: get("setTvDir").value.trim(),
        download_dir: get("setDownloadDir").value.trim(),
      },
      transcode: {
        max_height: Number(get("setMaxHeight").value),
        max_bitrate: get("setBitrate").value,
        force: get("setForceTranscode").checked,
      },
      qbit: {
        url: get("setQbitUrl").value.trim(),
        username: get("setQbitUser").value.trim(),
        password: get("setQbitPass").value,
      },
      prowlarr: { url: get("setProwlarrUrl").value.trim(), api_key: get("setProwlarrKey").value },
      jackett: { url: get("setJackettUrl").value.trim(), api_key: get("setJackettKey").value },
      opensubtitles: { api_key: get("setSubtitleKey").value.trim() },
    };
    const tmdbKey = get("setTmdb").value.trim();
    if (tmdbKey) payload.tmdb_key = tmdbKey;
    try {
      await api("/api/settings", { method: "POST", body: payload });
      toast("Inställningar sparade", "ok");
      await renderSettings();
    } catch (err) { toast(err.message, "error"); }
  }

  /* ------------------------------------------------------------- modal */
  function openModal(html) {
    if (!S.modalReturn) S.modalReturn = S.navFocus?.isConnected ? S.navFocus : null;
    modalRoot.innerHTML = `<div class="modal-backdrop">
      <div class="modal" role="dialog" aria-modal="true" data-modal-content>
        <button class="icon-btn modal-close" data-action="close-modal">✕</button>
        ${html}
      </div></div>`;
  }
  function closeModal() {
    modalRoot.innerHTML = "";
    S.player?.overlay.classList.remove("modal-open");
    const back = S.modalReturn;
    S.modalReturn = null;
    if (back?.isConnected && document.body.classList.contains("nav-mode")) {
      setNavFocus(back, { silent: true });
    }
  }

  async function openRematch(kind, id) {
    openModal(`<h3>Byt matchning</h3>
      <p class="hint">Sök på TMDB och välj rätt titel.</p>
      <div class="search-row"><input class="input" id="rematchQuery" placeholder="Titel…">
        <button class="btn primary" data-action="rematch-search" data-kind="${kind}" data-id="${id}">Sök</button></div>
      <div id="rematchResults"></div>`);
    const input = document.getElementById("rematchQuery");
    input.focus();
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") doRematchSearch(kind, id);
    });
  }

  async function doRematchSearch(kind, id) {
    const q = document.getElementById("rematchQuery")?.value.trim();
    if (!q) return;
    const box = document.getElementById("rematchResults");
    box.innerHTML = `<div class="spinner" style="margin:20px auto"></div>`;
    try {
      const data = await api(`/api/tmdb/search?q=${encodeURIComponent(q)}&media=${kind === "show" ? "tv" : "movie"}`);
      box.innerHTML = data.results.length ? data.results.map((r) => `
        <div class="device" data-action="rematch-pick" data-kind="${kind}" data-id="${id}" data-tmdb="${r.tmdb_id}">
          ${r.poster ? `<img src="${esc(r.poster)}" style="width:42px;height:63px;object-fit:cover;border-radius:6px">`
            : `<div class="device-icon">🎬</div>`}
          <div><div class="device-name">${esc(r.title)}</div>
            <div class="device-sub">${r.year || ""} ${r.rating ? `· ★ ${r.rating}` : ""}</div></div>
        </div>`).join("") : `<p class="empty">Inga träffar.</p>`;
    } catch (err) {
      box.innerHTML = `<p class="empty">${esc(err.message)}</p>`;
    }
  }

  /* ------------------------------------------------------------- events */
  modalRoot.addEventListener("click", async (ev) => {
    if (ev.target instanceof Element && ev.target.classList.contains("modal-backdrop")) {
      closeModal();
      return;
    }
    const el = ev.target instanceof Element ? ev.target.closest("[data-action]") : null;
    if (!el || !modalRoot.contains(el)) return;
    const a = el.dataset.action;
    try {
      if (a === "close-modal") {
        closeModal();
      } else if (a === "subtitle-off") {
        if (S.player) {
          S.player.subtitleTrack = null;
          applySubtitleTrack(S.player);
          closeModal();
          toast("Undertexter av");
        }
      } else if (a === "subtitle-select") {
        await selectSubtitle(Number(el.dataset.idx));
      } else if (a === "subtitle-generate") {
        await generateSubtitleFromPlayer(el.dataset.language || "sv");
      } else if (a === "subtitle-search") {
        await searchOnlineSubtitles();
      } else if (a === "subtitle-download") {
        await downloadSubtitle(Number(el.dataset.idx));
      }
    } catch (err) {
      toast(err.message, "error");
    }
  });

  document.addEventListener("click", async (ev) => {
    const el = ev.target.closest("[data-action]");
    if (!el) return;
    const a = el.dataset.action;
    try {
      if (a === "open") { location.hash = el.dataset.href; }
      else if (a === "play") { ev.stopPropagation(); openPlayer(Number(el.dataset.id), Number(el.dataset.start || 0)); }
      else if (a === "cast") { ev.stopPropagation(); castPlay(el.dataset.id, el.dataset.start || 0); }
      else if (a === "scan") {
        const r = await api("/api/scan", { method: "POST" });
        toast(r.started ? "Skanner biblioteket…" : "En skanning pågår redan");
        startScanPoll();
      }
      else if (a === "match-again") {
        const result = await api("/api/match/again", { method: "POST" });
        toast(result.started ? "Matchar biblioteket mot TMDB…" : (result.message || "En skanning pågår redan"));
        startScanPoll();
      }
      else if (a === "watched") {
        ev.stopPropagation();
        const watched = el.dataset.next !== "0";
        await markWatched(Number(el.dataset.id), watched);
        toast(watched ? "Markerad som sedd" : "Markerad som osedd", "ok");
        if (location.hash.startsWith("#/title/movie")) { renderMovie(el.dataset.id); }
      }
      else if (a === "continue-remove") {
        ev.stopPropagation();
        await removeFromContinue(Number(el.dataset.id));
      }
      else if (a === "delete-file") {
        ev.stopPropagation();
        openDeleteDialog(Number(el.dataset.id), el.dataset.title, el.dataset.path);
      }
      else if (a === "reveal-file") {
        ev.stopPropagation();
        try {
          const result = await api(`/api/reveal/${el.dataset.id}`, { method: "POST" });
          if (!result.selected) toast("Filen är borta – öppnade mappen i stället");
        } catch (err) {
          toast(err.message, "error");
        }
      }
      else if (a === "go-back") { ev.stopPropagation(); goBackPage(); }
      else if (a === "delete-file-confirm") {
        ev.stopPropagation();
        await deleteItemNow(Number(el.dataset.id));
      }
      else if (a === "season") {
        S.showSeason = Number(el.dataset.season);
        document.querySelectorAll(".season-tabs .chip").forEach((c) => c.classList.toggle("active", Number(c.dataset.season) === S.showSeason));
        const list = document.getElementById("episodeList");
        if (list) list.innerHTML = episodesHtml();
      }
      else if (a === "rematch") openRematch(el.dataset.kind, el.dataset.id);
      else if (a === "rematch-search") doRematchSearch(el.dataset.kind, el.dataset.id);
      else if (a === "rematch-pick") {
        const res = await api(`/api/match/${el.dataset.kind}/${el.dataset.id}`, { method: "POST", body: { tmdb_id: Number(el.dataset.tmdb) } });
        closeModal();
        toast("Matchning uppdaterad", "ok");
        if (el.dataset.kind === "show") { S.showSeason = null; renderShow(el.dataset.id); }
        else renderMovie(el.dataset.id);
      }
      else if (a === "settings-test") { /* legacy */ }
      else if (a === "save-settings") await saveSettings();
      else if (a === "copy-playback-log") {
        const text = S.playbackLogs.map((entry) => `${entry.at} ${entry.event} ${JSON.stringify(entry.details)}`).join("\\n") || "Inga uppspelningsfel loggade ännu.";
        await navigator.clipboard.writeText(text);
        toast("Uppspelningsloggen kopierad", "ok");
      }
      else if (a === "clear-playback-log") {
        S.playbackLogs = [];
        try { sessionStorage.removeItem("mlPlaybackLogs"); } catch (_) { /* ignored */ }
        const view = document.getElementById("playbackLogView");
        if (view) view.textContent = "Inga uppspelningsfel loggade ännu.";
        toast("Uppspelningsloggen rensad", "ok");
      }
      else if (a === "test-qbit") {
        const st = document.getElementById("qbitStatus");
        st.innerHTML = `<span class="spinner" style="width:14px;height:14px"></span> Testar…`;
        const r = await api("/api/settings/test/qbit", { method: "POST" });
        st.innerHTML = r.ok ? `<span class="dot ok"></span>Ansluten (qBittorrent ${esc(r.version)})`
          : `<span class="dot bad"></span>${esc(r.error || "Misslyckades")}`;
      }
      else if (a === "test-tmdb") {
        const st = document.getElementById("tmdbStatus");
        const r = await api("/api/settings/test/tmdb", { method: "POST" });
        st.innerHTML = r.ok ? `<span class="dot ok"></span>${esc(r.message || "TMDB fungerar")}${r.title ? ` · Exempel: ${esc(r.title)}${r.poster ? " (affisch hittad)" : " (testträffen saknar affisch)"}` : ""}` : `<span class="dot bad"></span>${esc(r.error || "Fel")}`;
      }
      else if (a === "test-indexers") {
        const st = document.getElementById("indexerStatus");
        st.innerHTML = `<span class="spinner" style="width:14px;height:14px"></span> Testar…`;
        const r = await api("/api/settings/test/indexers", { method: "POST" });
        st.innerHTML = r.ok ? `<span class="dot ok"></span>${r.count} träffar${(r.errors || []).length ? ` · ${esc(r.errors.join(", "))}` : ""}`
          : `<span class="dot bad"></span>${esc(r.error || (r.errors || []).join(", ") || "Ingen indexerare")}`;
      }
      else if (a === "open-cast-modal") openCastModal();
      else if (a === "close-modal") closeModal();
      else if (a === "cast-connect" || a === "cast-connect-host") {
        const uuid = a === "cast-connect" ? el.dataset.uuid : null;
        const host = a === "cast-connect" ? el.dataset.host : document.getElementById("castHost")?.value.trim();
        const r = await api("/api/cast/connect", { method: "POST", body: { uuid, host } });
        if (r.ok) { closeModal(); toast(`Ansluten till ${r.device?.name || "Chromecast"}`, "ok"); pollCast(true); }
        else toast(r.error || "Kunde inte ansluta", "error");
      }
      else if (a === "cast-disconnect") { await api("/api/cast/disconnect", { method: "POST" }); pollCast(true); }
      else if (a === "cast-toggle") {
        const playing = S.cast?.state === "PLAYING";
        await api("/api/cast/control", { method: "POST", body: { action: playing ? "pause" : "play" } });
        pollCast(true);
      }
      else if (a === "cast-stop") { await api("/api/cast/control", { method: "POST", body: { action: "stop" } }); pollCast(true); }
      else if (a === "dl-tab") { S.dlTab = el.dataset.tab; await renderDownloads(); }
      else if (a === "dl-search") { await doDownloadSearch(); }
      else if (a === "dl-add-magnet") {
        const magnet = document.getElementById("magnetInput")?.value.trim();
        if (!magnet) return toast("Klistra in en magnetlänk", "error");
        const r = await api("/api/downloads/add", { method: "POST", body: { magnet } });
        toast("Torrenten tillagd i qBittorrent", "ok");
        document.getElementById("magnetInput").value = "";
        S.dlTab = "active"; await renderDownloads();
      }
      else if (a === "dl-add-result") {
        const idx = Number(el.dataset.idx);
        const result = (S.dlResults?.results || [])[idx];
        if (!result) return;
        el.disabled = true; el.textContent = "Lägger till…";
        const r = await api("/api/downloads/add", { method: "POST", body: { result } });
        toast("Torrenten tillagd i qBittorrent", "ok");
        S.dlTab = "active"; await renderDownloads();
      }
      else if (a === "torrent-files") {
        const hash = el.dataset.hash;
        const result = await api(`/api/downloads/${encodeURIComponent(hash)}/files`);
        const files = result.files || [];
        openModal(`<h3>Filer och prioritet</h3><p class="hint">Hög prioritet försöker hämta den filen före övriga filer i torrenten.</p>
          <div class="file-priority-list">${files.map((f) => `<div class="subtitle-result"><div><strong>${esc(f.name)}</strong><div class="res-sub">${fmtBytes(f.size)} · ${pct(f.progress || 0)}</div></div>
            <select class="input" data-action="file-priority" data-hash="${esc(hash)}" data-file-id="${f.index}">
              <option value="0" ${f.priority === 0 ? "selected" : ""}>Hoppa över</option>
              <option value="1" ${f.priority === 1 ? "selected" : ""}>Låg</option>
              <option value="6" ${f.priority === 6 ? "selected" : ""}>Normal</option>
              <option value="7" ${f.priority === 7 ? "selected" : ""}>Hög</option>
            </select></div>`).join("") || `<p class="empty">Inga filuppgifter tillgängliga ännu.</p>`}</div>`);
      }
      else if (a === "torrent") {
        const hash = el.dataset.hash, act = el.dataset.act;
        if (act === "delete" || act === "delete_files") {
          if (!confirm(act === "delete_files" ? "Ta bort torrenten och dess filer?" : "Ta bort torrenten från qBittorrent?")) return;
        }
        const r = await api(`/api/downloads/${encodeURIComponent(hash)}/${act}`, { method: "POST" });
        if (act === "import") toast(`Importerad: ${r.path || ""}`, "ok");
        else toast("Klart", "ok");
        S.dlData = null;
        await renderDownloadsBody();
      }
      else if (a === "generate-subtitle") {
        el.disabled = true; el.textContent = "Genererar…";
        try {
          const job = await api(`/api/subtitles/${el.dataset.id}/generate`, { method: "POST", body: { language: "en", model: "small" } });
          let state = job;
          while (state.state === "running") {
            await new Promise((resolve) => setTimeout(resolve, 1500));
            state = await api(`/api/subtitle-jobs/${job.id}`);
            el.textContent = `Genererar… ${Math.round((state.progress || 0) * 100)} %`;
          }
          if (state.state === "error") throw new Error(state.error || "Undertextgenereringen misslyckades");
          toast(`Undertext klar på ${fmtDuration(state.result?.elapsed_seconds ?? state.elapsed_seconds)}`, "ok");
          if (location.hash.startsWith("#/title/show/")) await renderShow(location.hash.split("/").pop());
          else await renderMovie(el.dataset.id);
        } catch (err) { el.disabled = false; el.textContent = "Generera undertext"; toast(err.message, "error"); }
      }
      else if (a === "player-close") await closePlayer();
      else if (a === "player-subtitles") {
        ev.stopPropagation();
        openSubtitleMenu();
      }
      else if (a === "subtitle-language") { S.subtitleLanguage = el.value; }
      else if (a === "player-toggle") togglePlayer();
      else if (a === "player-fullscreen") togglePlayerFullscreen();
      else if (a === "player-seek") seekPlayer(Number(el.dataset.delta));
      else if (a === "player-cast") {
        await closePlayer();
        castPlay(el.dataset.id, el.dataset.start || 0);
      }
      else if (a === "cast-volume") { /* handled below */ }
    } catch (err) {
      toast(err.message, "error");
    }
  });

  document.addEventListener("change", async (ev) => {
    const el = ev.target;
    if (el.matches('[data-action="file-priority"]')) {
      try {
        await api(`/api/downloads/${encodeURIComponent(el.dataset.hash)}/files/${el.dataset.fileId}/priority`, { method: "POST", body: { priority: Number(el.value) } });
        toast("Filprioritet uppdaterad", "ok");
      } catch (err) { toast(err.message, "error"); }
    }
  });

  document.addEventListener("input", (ev) => {
    if (ev.target.matches('[data-action="cast-volume"]')) {
      api("/api/cast/control", { method: "POST", body: { action: "volume", value: Number(ev.target.value) } }).catch(() => {});
    }
    if (ev.target.id === "torrentFile" && ev.target.files?.[0]) {
      const file = ev.target.files[0];
      const reader = new FileReader();
      reader.onload = async () => {
        try {
          const b64 = String(reader.result).split(",")[1];
          await api("/api/downloads/add", { method: "POST", body: { torrent_b64: b64, filename: file.name } });
          toast("Torrenten tillagd", "ok");
          S.dlTab = "active";
          await renderDownloads();
        } catch (err) { toast(err.message, "error"); }
      };
      reader.readAsDataURL(file);
    }
  });

  async function doDownloadSearch() {
    const q = document.getElementById("dlQuery")?.value.trim();
    if (!q) return;
    S.dlQuery = q;
    const box = document.getElementById("dlResults");
    box.innerHTML = `<div class="spinner" style="margin:20px auto"></div>`;
    try {
      S.dlResults = await api(`/api/downloads/search?q=${encodeURIComponent(q)}`);
      box.innerHTML = dlResultsHtml(S.dlResults);
    } catch (err) { box.innerHTML = `<p class="empty">${esc(err.message)}</p>`; }
  }

  document.getElementById("searchInput").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      const q = ev.target.value.trim();
      if (q) location.hash = `#/search/${encodeURIComponent(q)}`;
    }
  });

  document.getElementById("castBtn").addEventListener("click", openCastModal);

  /* ------------------------------------------- navigering med handkontroll */
  const NAV_SELECTOR = [
    ".nav a", ".topbar .brand", ".icon-btn", ".win-btn", ".card", ".ep-row", ".btn",
    ".chip", ".filter", ".person", ".back-btn",
    // Textfält är också navigerbara: A sätter fokus så DeckLink-tangentbordet
    // (eller ett riktigt tangentbord) kan skriva i dem. B/Esc lämnar fältet.
    "input.input", "textarea.input", "select.input", "#searchInput",
  ].join(",");
  // Små överläggsknappar på korten nås bara när inget annat finns i den riktningen.
  const NAV_SELECTOR_ALL = `${NAV_SELECTOR},.mini-btn`;

  // Små syntetiserade klickljud – samma känsla som i ett konsolgränssnitt.
  let navAudio = null;
  function navCtx() {
    if (!navAudio) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      try { navAudio = new Ctx(); } catch (_) { return null; }
    }
    if (navAudio.state === "suspended") navAudio.resume().catch(() => {});
    return navAudio;
  }

  // Mjuk, kort "tick" med en liten luftig klang ovanpå – samma känsla som
  // navigeringsljudet i ett konsolgränssnitt. Byggs i WebAudio, inga filer behövs.
  let navNoise = null;
  function noiseBuffer(ctx) {
    if (navNoise) return navNoise;
    const len = Math.floor(ctx.sampleRate * 0.06);
    const buf = ctx.createBuffer(1, len, ctx.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < len; i += 1) {
      const env = Math.pow(1 - i / len, 3);
      data[i] = (Math.random() * 2 - 1) * env;
    }
    navNoise = buf;
    return buf;
  }

  function navSound(kind = "move") {
    const ctx = navCtx();
    if (!ctx) return;
    const presets = {
      move: { from: 1620, to: 1980, dur: 0.055, gain: 0.05, noise: 0.05, bright: 3200 },
      select: { from: 720, to: 1180, dur: 0.12, gain: 0.07, noise: 0.028, bright: 2600 },
      back: { from: 900, to: 520, dur: 0.14, gain: 0.06, noise: 0.03, bright: 2200 },
    };
    const preset = presets[kind] || presets.move;
    const now = ctx.currentTime;
    const master = ctx.createGain();
    master.gain.value = 1;
    master.connect(ctx.destination);

    const osc = ctx.createOscillator();
    const tone = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(preset.from, now);
    osc.frequency.exponentialRampToValueAtTime(preset.to, now + preset.dur);
    tone.gain.setValueAtTime(0.0001, now);
    tone.gain.exponentialRampToValueAtTime(preset.gain, now + 0.006);
    tone.gain.exponentialRampToValueAtTime(0.0001, now + preset.dur);
    osc.connect(tone).connect(master);
    osc.start(now);
    osc.stop(now + preset.dur + 0.02);

    const air = ctx.createBufferSource();
    const band = ctx.createBiquadFilter();
    const airGain = ctx.createGain();
    air.buffer = noiseBuffer(ctx);
    band.type = "bandpass";
    band.frequency.value = preset.bright;
    band.Q.value = 0.9;
    airGain.gain.value = preset.noise;
    air.connect(band).connect(airGain).connect(master);
    air.start(now);
    air.stop(now + 0.065);
  }

  function navCandidates(withOverlays = false) {
    const modal = modalRoot.querySelector(".modal");
    const scope = modal || document;
    return [...scope.querySelectorAll(withOverlays ? NAV_SELECTOR_ALL : NAV_SELECTOR)].filter((el) => {
      if (el.disabled) return false;
      const rect = el.getBoundingClientRect();
      if (rect.width < 8 || rect.height < 8) return false;
      const style = getComputedStyle(el);
      return style.visibility !== "hidden" && style.display !== "none" && Number(style.opacity || 1) > 0.15;
    });
  }

  function setNavFocus(el, { silent = false, reveal = true } = {}) {
    if (!el || !el.isConnected) return;
    document.body.classList.add("nav-mode");
    if (S.navFocus !== el) {
      S.navFocus?.classList.remove("focused");
      S.navFocus = el;
      el.classList.add("focused");
      if (!silent) navSound("move");
    }
    if (reveal) el.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
  }

  function clearNavFocus() {
    S.navFocus?.classList.remove("focused");
    S.navFocus = null;
  }

  function leaveNavMode() {
    if (!document.body.classList.contains("nav-mode")) return;
    document.body.classList.remove("nav-mode");
  }

  function bestInDirection(items, current, direction) {
    if (!current) return null;
    const from = current.getBoundingClientRect();
    const cx = from.left + from.width / 2;
    const cy = from.top + from.height / 2;
    let best = null;
    let bestScore = Infinity;
    for (const el of items) {
      if (el === current || el.contains(current) || current.contains(el)) continue;
      const rect = el.getBoundingClientRect();
      const dx = (rect.left + rect.width / 2) - cx;
      const dy = (rect.top + rect.height / 2) - cy;
      // Avstånd mellan kanterna i sidled – 0 när objekten ligger på samma rad.
      // Med mittpunktsavstånd hoppade navigeringen förbi breda rader (t.ex.
      // avsnittslistan) till förmån för ett smalt objekt längre ned.
      const gapX = Math.max(0, rect.left - from.right, from.left - rect.right);
      const gapY = Math.max(0, rect.top - from.bottom, from.top - rect.bottom);
      // I sidled mäts avståndet till objektets kant: ett brett fält ska inte
      // "äga" allt som ligger till höger om dess mittpunkt. Ligger objekten
      // ovanpå varandra i sidled används mittpunktsavståndet i stället.
      const rightGap = rect.left - from.right;
      const leftGap = from.left - rect.right;
      let primary = 0;
      let secondary = 0;
      if (direction === "left") {
        if (-dx <= 6) continue;
        primary = leftGap >= 0 ? leftGap : Math.abs(dx);
        secondary = gapY;
      } else if (direction === "right") {
        if (dx <= 6) continue;
        primary = rightGap >= 0 ? rightGap : Math.abs(dx);
        secondary = gapY;
      } else if (direction === "up") {
        if (-dy <= 6) continue;
        primary = -dy;
        secondary = gapX;
      } else {
        if (dy <= 6) continue;
        primary = dy;
        secondary = gapX;
      }
      const score = primary + secondary * 2.4;
      if (score < bestScore) { bestScore = score; best = el; }
    }
    return best;
  }

  function spatialMove(direction) {
    const items = navCandidates();
    if (!items.length) return;
    const current = S.navFocus && S.navFocus.isConnected ? S.navFocus : null;
    if (!current) {
      // Första trycket: börja på sidans första objekt i stället för logotypen i
      // toppraden – och hoppa över bakåtknappen, som ligger överst på sidan.
      const inPage = items.filter((el) => el.closest("#app"));
      const primary = inPage.filter((el) => !el.matches(".back-btn"));
      setNavFocus(primary[0] || inPage[0] || items[0]);
      return;
    }
    let best = bestInDirection(items, current, direction)
      || bestInDirection(navCandidates(true), current, direction);
    if (!best && direction === "up" && !current.closest(".topbar")) {
      // Ingen träff ovanför (t.ex. när sidan är nedscrollad) – gå till menyn.
      const bar = navCandidates().filter((el) => el.closest(".topbar"));
      best = bar.find((el) => el.matches(".nav a")) || bar[0] || null;
    }
    if (best) setNavFocus(best);
  }

  function activateNavFocus() {
    const el = S.navFocus;
    if (!el || !el.isConnected) return;
    navSound("select");
    if (el.matches(".filter")) {
      const select = el.querySelector("select");
      if (select) { select.focus(); select.click(); }
      return;
    }
    if (el.matches("select")) { el.focus(); el.click(); return; }
    if (el.matches("input, textarea")) {
      el.focus();
      if (el.select && !el.matches("[type=range],[type=file],[type=checkbox]")) el.select();
      return;
    }
    el.click();
  }

  function goBackPage() {
    const target = S.hashStack.pop() || "#/";
    if (!target || target === location.hash) return;
    clearNavFocus();
    navSound("back");
    S.backNav = true;
    location.hash = target;
  }

  function handlePad(action) {
    S.mouseNav = false;
    if (S.player) {
      if (action === "a") { togglePlayer(); navSound("select"); }
      else if (action === "b") { closePlayer(); navSound("back"); }
      else if (action === "left") seekPlayer(-10);
      else if (action === "right") seekPlayer(10);
      else if (action === "start") { togglePlayerFullscreen(); navSound("select"); }
      return;
    }
    const modalOpen = Boolean(modalRoot.querySelector(".modal-backdrop"));
    if (S.navFocus && !S.navFocus.isConnected) clearNavFocus();
    if (modalOpen && S.navFocus && !modalRoot.contains(S.navFocus)) clearNavFocus();
    if (action === "a") {
      if (S.navFocus) activateNavFocus();
      else spatialMove("down");
      return;
    }
    if (action === "b") {
      if (modalOpen) { closeModal(); navSound("back"); }
      else if (location.hash && location.hash !== "#/") goBackPage();
      return;
    }
    if (["left", "right", "up", "down"].includes(action)) spatialMove(action);
  }

  // setInterval i stället för requestAnimationFrame: rAF stannar när fönstret
  // ligger i bakgrunden, och då ska kontrollen ändå fungera.
  function padConnected() {
    if (S.padSeen) return;
    S.padSeen = true;
    toast("Kontroll ansluten – styrkors flyttar, A väljer, B går bakåt", "ok");
    navSound("select");
  }

  // Desktop-skalet läser handkontrollen själv (Qt-webvyn saknar ibland Gamepad
  // API) och anropar de här två funktionerna i stället.
  window.__mlPadInput = handlePad;
  window.__mlPadConnected = padConnected;

  function pollGamepads() {
    if (window.__mlNativePad) return;
    const timestamp = performance.now();
    const pads = navigator.getGamepads ? navigator.getGamepads() : [];
    for (const pad of pads) {
      if (!pad || !pad.connected) continue;
      padConnected();
      const state = S.pads.get(pad.index) || { held: {}, next: {} };
      const down = {
        up: Boolean(pad.buttons[12]?.pressed) || (pad.axes[1] ?? 0) < -0.55,
        down: Boolean(pad.buttons[13]?.pressed) || (pad.axes[1] ?? 0) > 0.55,
        left: Boolean(pad.buttons[14]?.pressed) || (pad.axes[0] ?? 0) < -0.55,
        right: Boolean(pad.buttons[15]?.pressed) || (pad.axes[0] ?? 0) > 0.55,
        a: Boolean(pad.buttons[0]?.pressed),
        b: Boolean(pad.buttons[1]?.pressed),
        start: Boolean(pad.buttons[9]?.pressed),
      };
      for (const [action, pressed] of Object.entries(down)) {
        const was = Boolean(state.held[action]);
        state.held[action] = pressed;
        if (!pressed) { state.next[action] = 0; continue; }
        if (was && timestamp < (state.next[action] || 0)) continue;
        handlePad(action);
        state.next[action] = timestamp + (was ? 130 : 330);
      }
      S.pads.set(pad.index, state);
    }
  }

  window.addEventListener("gamepadconnected", padConnected);

  document.addEventListener("focusin", (ev) => {
    if (S.mouseNav) return;
    if (ev.target.matches(NAV_SELECTOR)) setNavFocus(ev.target, { silent: true, reveal: false });
  });

  document.addEventListener("pointerdown", () => {
    S.mouseNav = true;
    leaveNavMode();
  }, true);

  document.addEventListener("mousemove", () => {
    leaveNavMode();
    if (S.navFocus) clearNavFocus();
  }, { passive: true });

  window.addEventListener("hashchange", async () => {
    const from = S.currentHash;
    const to = location.hash || "#/";
    const returning = S.backNav;
    let restore = returning;
    if (returning) {
      S.backNav = false;
    } else if (from && from !== to) {
      S.scrollMemory.set(from, window.scrollY);
      // Går vi tillbaka till föregående sida (t.ex. webbläsarens bakåtknapp)
      // ska stacken krympa i stället för att få en ny post.
      if (S.hashStack[S.hashStack.length - 1] === to) { S.hashStack.pop(); restore = true; }
      else S.hashStack.push(from);
    }
    clearNavFocus();
    await route();
    if (restore) {
      const top = S.scrollMemory.get(to) || 0;
      requestAnimationFrame(() => window.scrollTo({ top }));
      setTimeout(() => { if (location.hash === to) window.scrollTo({ top }); }, 140);
    }
  });

  setInterval(pollGamepads, 50);

  window.addEventListener("scroll", () => {
    document.getElementById("topbar").classList.toggle("solid", window.scrollY > 24);
  });

  document.addEventListener("keydown", (ev) => {
    if (!S.player) return;
    // Do not let player shortcuts steal keystrokes from native controls/dialogs.
    if (modalRoot.querySelector(".modal-backdrop")) return;
    if (ev.target.matches("input, select, textarea, select option")) return;
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
    const key = ev.key;
    if (key === "Escape") {
      // Escape lämnar fullskärm först, sedan stängs spelaren.
      if (inFullscreen()) { ev.preventDefault(); setPlayerFullscreen(false); }
      else closePlayer();
    } else if (key === " " || key === "k" || key === "K") {
      if (key === " " && ev.target.matches("button")) return;
      ev.preventDefault();
      togglePlayer();
    } else if (key === "ArrowRight") { ev.preventDefault(); seekPlayer(10); }
    else if (key === "ArrowLeft") { ev.preventDefault(); seekPlayer(-10); }
    else if (key === "l" || key === "L") { ev.preventDefault(); seekPlayer(30); }
    else if (key === "j" || key === "J") { ev.preventDefault(); seekPlayer(-30); }
    else if (key === "f" || key === "F") { ev.preventDefault(); togglePlayerFullscreen(); }
  });

  // Piltangenter/Enter/Esc navigerar gränssnittet (fungerar både med tangentbord,
  // DeckLink-telefonen som skickar tangenttryck och handkontroller).
  document.addEventListener("keydown", (ev) => {
    S.mouseNav = false;
    if (S.player) return;
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
    // Textfält: skriv som vanligt, men Escape/B lämnar fältet så navigeringen
    // fortsätter där den var.
    if (ev.target.matches("input, textarea")) {
      if (ev.key !== "Escape") return;
      ev.preventDefault();
      ev.stopPropagation();
      const field = ev.target;
      field.blur();
      // Ringen stannar på fältet så pilarna fortsätter därifrån i stället för
      // att hoppa tillbaka till sidans början.
      if (!S.navFocus) setNavFocus(field, { reveal: false });
      navSound("back");
      return;
    }
    if (ev.target.matches("select, option, [contenteditable]")) return;
    const modalOpen = Boolean(modalRoot.querySelector(".modal-backdrop"));
    const key = ev.key;
    if (key === "Escape" || key === "Backspace") {
      if (modalOpen) {
        ev.preventDefault();
        closeModal();
        navSound("back");
        return;
      }
      if (key === "Escape" && location.hash && location.hash !== "#/") {
        ev.preventDefault();
        goBackPage();
      } else if (key === "Backspace") {
        ev.preventDefault();
      }
      return;
    }
    if (modalOpen && S.navFocus && !modalRoot.contains(S.navFocus)) clearNavFocus();
    if (key === "Enter" && S.navFocus) { ev.preventDefault(); activateNavFocus(); return; }
    const dirs = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };
    if (!dirs[key]) return;
    // Piltangenterna flyttar fokus – första trycket tar tag i första objektet.
    ev.preventDefault();
    spatialMove(dirs[key]);
  }, true);

  for (const name of ["fullscreenchange", "webkitfullscreenchange"]) {
    document.addEventListener(name, syncFullscreenButton);
  }

  function startScanPoll() {
    clearInterval(S.scanPoller);
    S.scanPoller = setInterval(async () => {
      try {
        const st = await api("/api/scan/status");
        const line = document.getElementById("scanStatus");
        if (line) line.innerHTML = `<span class="dot ${st.running ? "warn" : "ok"}"></span>${esc(st.message || "")} · ${st.found || 0} filer`;
        if (!st.running) {
          clearInterval(S.scanPoller);
          toast("Biblioteket uppdaterat", "ok");
          if ((location.hash || "#/") === "#/" ) route();
        }
      } catch (_) { clearInterval(S.scanPoller); }
    }, 1500);
  }

  /* ------------------------------------------------------------- boot */
  (async function boot() {
    shellBridge().then((shell) => { if (shell) installWindowChrome(shell); });
    try {
      const st = await api("/api/state");
      S.settings = st.settings;
      S.lan_ip = st.lan_ip;
      S.base_url = st.base_url;
      S.ffmpeg = st.ffmpeg;
      S.tmdb = st.tmdb;
      S.cast = st.cast;
      if (!st.ffmpeg) toast("ffmpeg saknas – transcoding fungerar inte", "error");
      await route();
      if (st.scan?.running) startScanPoll();
      pollCast();
      setInterval(pollCast, 4000);
    } catch (err) {
      app.innerHTML = `<div class="page"><h1 class="page-title">Servern svarar inte</h1>
        <p class="page-sub">${esc(err.message)}</p><button class="btn" onclick="location.reload()">Försök igen</button></div>`;
    }
  })();
})();
