// Page de revue d'une vidéo : lecteur borné, édition, décisions, raccourcis clavier.
"use strict";

const videoId = Number(location.pathname.split("/").pop());
const $ = (id) => document.getElementById(id);
const player = $("video");
const state = { clips: [], active: 0, limits: { min: 120, max: 300, title: 60 }, stopAt: null };

// --- Utilitaires -------------------------------------------------------------
const ts = (s) => {
  s = Math.max(0, s || 0);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = Math.floor(s % 60);
  const tenth = Math.floor((s * 10) % 10);
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0") + "." + tenth;
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const STATUS = {
  candidate: ["à revoir", ""], auto_rejected: ["écarté auto", "warn"], approved: ["validé", "ok"],
  rendered: ["validé · rendu", "ok"], rejected: ["rejeté", "no"], uploaded: ["publié", "ok"],
};

function toast(msg, bad = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (bad ? " bad" : "");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add("hidden"), 2500);
}

async function api(method, url, body) {
  const res = await fetch(url, {
    method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail || res.statusText;
    toast("Erreur : " + detail, true);
    throw new Error(detail);
  }
  return res.json();
}

// --- Chargement --------------------------------------------------------------
async function load() {
  const data = await api("GET", `/api/videos/${videoId}`);
  document.title = "Clipper — " + (data.video.title || data.video.source_id);
  $("vtitle").textContent = data.video.title || data.video.source_id;
  state.clips = data.clips;
  state.limits = data.limits;
  if (data.proxy_url) {
    if (!player.src.endsWith(data.proxy_url)) player.src = data.proxy_url;
  } else {
    player.classList.add("hidden");
    $("noproxy").classList.remove("hidden");
  }
  renderList();
  select(Math.min(state.active, state.clips.length - 1), false);
  // Place le lecteur au début du clip actif dès que la vidéo est prête.
  const seekToActive = () => { if (cur()) player.currentTime = cur().start; };
  if (player.readyState >= 1) seekToActive();
  else player.addEventListener("loadedmetadata", seekToActive, { once: true });
}

// --- Liste des clips ---------------------------------------------------------
function cardHTML(c, i) {
  const [label, cls] = STATUS[c.status] || [c.status, ""];
  const bad = c.duration < state.limits.min || c.duration > state.limits.max;
  const titles = (c.titles.length ? c.titles : [c.title || ""]).map((t, k) => `
      <label class="title-row">
        <input type="radio" name="t${c.id}" value="${k}" ${t === c.title ? "checked" : ""} data-field="choose" data-k="${k}">
        <span class="kbd">${k + 1}</span>
        <input type="text" class="title-input" maxlength="${state.limits.title}" value="${esc(t)}" data-field="title" data-k="${k}">
        <span class="count">${t.length}/${state.limits.title}</span>
      </label>`).join("");
  const frames = c.frames.map((f, k) => `
      <img src="${f}" class="frame ${k === c.thumb_frame ? "sel" : ""}" data-frame="${k}" loading="lazy" alt="Image ${k + 1}">`).join("");
  let render = "";
  if (c.status === "approved") render = '<span class="pill">rendu en attente…</span>';
  if (c.render_url) render = `<a class="pill ok" href="${c.render_url}" target="_blank">clip rendu ↗</a>`;
  if (c.yt_video_id) {
    const when = c.publish_at ? new Date(c.publish_at).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }) : "";
    render += ` <a class="pill ok" href="https://studio.youtube.com/video/${c.yt_video_id}/edit" target="_blank">YouTube · ${when} ↗</a>`;
  }
  return `
  <article class="card ${i === state.active ? "active" : ""} st-${c.status}" data-i="${i}" id="clip-${c.id}">
    <header>
      <span class="score s${Math.floor(c.score / 25)}">${c.score}</span>
      <div class="head">
        <div class="topic">${esc(c.topic)}</div>
        <div class="muted small">#${c.id} · ${ts(c.start)} → ${ts(c.end)} ·
          <span class="${bad ? "bad" : ""}">${ts(c.duration)}</span></div>
      </div>
      <span class="status ${cls}">${label}</span>
    </header>
    ${c.reject_reason ? `<p class="reason bad">✗ ${esc(c.reject_reason)}</p>` : ""}
    ${c.reason ? `<p class="reason">${esc(c.reason)}</p>` : ""}
    <div class="details">
      <div class="fields">
        <div class="field"><span class="label">Titres</span>${titles}</div>
        <label class="field"><span class="label">Description</span>
          <textarea rows="4" data-field="description">${esc(c.description || "")}</textarea>
          <span class="muted small footer">${esc(c.full_description.slice((c.description || "").trim().length).trim())}</span>
        </label>
        <label class="field"><span class="label">Tags</span>
          <input type="text" data-field="tags" value="${esc(c.tags.join(", "))}"></label>
      </div>
      <div class="thumb">
        ${c.thumb_url ? `<img src="${c.thumb_url}" class="thumb-img" alt="Miniature">` : '<div class="muted">Pas de miniature</div>'}
        <div class="thumb-text">
          <input type="text" data-field="thumb_text" value="${esc(c.thumb_text || "")}" placeholder="Texte de la miniature">
          <button data-act="thumbtext">Appliquer</button>
        </div>
        <div class="frames">${frames}</div>
        ${render}
      </div>
    </div>
  </article>`;
}

function renderList() {
  $("clips").innerHTML = state.clips.length
    ? state.clips.map(cardHTML).join("")
    : '<p class="muted">Aucun clip candidat pour cette vidéo.</p>';
  const n = (f) => state.clips.filter(f).length;
  $("counts").textContent = `${n((c) => c.status === "candidate")} à revoir · ` +
    `${n((c) => ["approved", "rendered", "uploaded"].includes(c.status))} validés · ${n((c) => c.status === "rejected")} rejetés`;
}

function replaceCard(i) {
  const old = document.querySelector(`.card[data-i="${i}"]`);
  if (!old) return renderList();
  const focusedField = document.activeElement && old.contains(document.activeElement) ? document.activeElement : null;
  if (focusedField) return; // ne pas écraser un champ en cours de saisie
  old.outerHTML = cardHTML(state.clips[i], i);
  const n = (f) => state.clips.filter(f).length;
  $("counts").textContent = `${n((c) => c.status === "candidate")} à revoir · ` +
    `${n((c) => ["approved", "rendered", "uploaded"].includes(c.status))} validés · ${n((c) => c.status === "rejected")} rejetés`;
}

// --- Sélection et lecteur ------------------------------------------------------
const cur = () => state.clips[state.active];

function select(i, seek = true) {
  if (!state.clips.length) return;
  i = Math.max(0, Math.min(i, state.clips.length - 1));
  document.querySelectorAll(".card.active").forEach((el) => el.classList.remove("active"));
  state.active = i;
  const el = document.querySelector(`.card[data-i="${i}"]`);
  if (el) {
    el.classList.add("active");
    el.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  updateBounds();
  if (seek) {
    player.pause();
    player.currentTime = cur().start;
  }
}

function updateBounds() {
  const c = cur();
  if (!c) return;
  $("tstart").textContent = ts(c.start);
  $("tend").textContent = ts(c.end);
  const d = c.end - c.start;
  $("tdur").textContent = ts(d);
  $("tdur").className = d < state.limits.min || d > state.limits.max ? "bad" : "";
}

function playFrom(t, stopAt) {
  player.currentTime = t;
  state.stopAt = stopAt;
  player.play();
}

player.addEventListener("timeupdate", () => {
  if (state.stopAt !== null && player.currentTime >= state.stopAt) {
    player.pause();
    state.stopAt = null;
  }
});

function togglePlay() {
  const c = cur();
  if (!c) return;
  if (!player.paused) return player.pause();
  const inside = player.currentTime >= c.start && player.currentTime < c.end - 0.2;
  playFrom(inside ? player.currentTime : c.start, c.end);
}

// --- Enregistrement ------------------------------------------------------------
async function patch(i, body) {
  const c = state.clips[i];
  const updated = await api("PATCH", `/api/clips/${c.id}`, body);
  state.clips[i] = updated;
  replaceCard(i);
  if (i === state.active) updateBounds();
  watchRenders();
  return updated;
}

// Ajustement des bornes : mise à jour immédiate à l'écran, envoi groupé après 400 ms.
let boundsTimer = null;
function nudge(which, delta) {
  const c = cur();
  if (!c || c.status === "uploaded") return;
  c[which] = Math.max(0, +(c[which] + delta).toFixed(3));
  c.duration = c.end - c.start;
  updateBounds();
  const i = state.active;
  // Repositionne la lecture pour entendre l'effet du changement.
  if (which === "start") playFrom(c.start, c.start + 5);
  else playFrom(Math.max(c.start, c.end - 5), c.end);
  clearTimeout(boundsTimer);
  boundsTimer = setTimeout(() => patch(i, { start: c.start, end: c.end }), 400);
}

async function decide(status) {
  const i = state.active;
  const c = cur();
  if (!c) return;
  if (status === "approved" && !c.title) return toast("Choisis un titre avant de valider.", true);
  clearTimeout(boundsTimer);
  await patch(i, { status, start: c.start, end: c.end });
  toast(status === "approved" ? "Validé — rendu lancé" : status === "rejected" ? "Rejeté" : "Décision annulée");
  if (status !== "candidate") {
    const next = state.clips.findIndex((x, k) => k > i && x.status === "candidate");
    if (next !== -1) select(next);
  }
}

function chooseTitle(k) {
  const c = cur();
  const t = c && c.titles[k];
  if (t) patch(state.active, { title: t });
}

// --- Événements ----------------------------------------------------------------
const ACTIONS = {
  "start-1": () => nudge("start", -1), "start+1": () => nudge("start", 1),
  "end-1": () => nudge("end", -1), "end+1": () => nudge("end", 1),
  play: togglePlay,
  head: () => playFrom(cur().start, cur().start + 6),
  tail: () => playFrom(Math.max(cur().start, cur().end - 6), cur().end),
  approve: () => decide("approved"), reject: () => decide("rejected"), reset: () => decide("candidate"),
};

document.addEventListener("click", (e) => {
  const act = e.target.closest("[data-act]");
  const card = e.target.closest(".card");
  if (card && Number(card.dataset.i) !== state.active) select(Number(card.dataset.i));
  if (act && act.dataset.act === "thumbtext") {
    const input = card.querySelector('[data-field="thumb_text"]');
    regenThumb({ text: input.value });
  } else if (act) {
    ACTIONS[act.dataset.act]?.();
  }
  const frame = e.target.closest("[data-frame]");
  if (frame) regenThumb({ frame: Number(frame.dataset.frame) });
});

async function regenThumb(body) {
  const i = state.active;
  toast("Miniature en cours…");
  const updated = await api("POST", `/api/clips/${cur().id}/thumbnail`, body);
  state.clips[i] = updated;
  document.activeElement?.blur();
  replaceCard(i);
  toast("Miniature mise à jour");
}

document.addEventListener("change", (e) => {
  const field = e.target.dataset.field;
  const card = e.target.closest(".card");
  if (!field || !card) return;
  const i = Number(card.dataset.i);
  const c = state.clips[i];
  if (field === "choose") return patch(i, { title: c.titles[Number(e.target.dataset.k)] });
  if (field === "title") {
    const k = Number(e.target.dataset.k);
    const titles = [...c.titles];
    const wasChosen = titles[k] === c.title;
    titles[k] = e.target.value.trim();
    return patch(i, wasChosen ? { titles, title: titles[k] } : { titles });
  }
  if (field === "tags") return patch(i, { tags: e.target.value.split(",").map((t) => t.trim()).filter(Boolean) });
  if (field === "description") return patch(i, { description: e.target.value });
  if (field === "thumb_text") return regenThumb({ text: e.target.value });
});

document.addEventListener("input", (e) => {
  if (e.target.dataset.field === "title") {
    const counter = e.target.parentElement.querySelector(".count");
    counter.textContent = `${e.target.value.length}/${state.limits.title}`;
  }
});

document.addEventListener("keydown", (e) => {
  const typing = ["INPUT", "TEXTAREA"].includes(e.target.tagName) && e.target.type !== "radio";
  if (typing) {
    if (e.key === "Escape" || (e.key === "Enter" && e.target.tagName === "INPUT")) e.target.blur();
    return;
  }
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const key = e.key.toLowerCase();
  const map = {
    j: () => select(state.active + 1), arrowdown: () => select(state.active + 1),
    k: () => select(state.active - 1), arrowup: () => select(state.active - 1),
    " ": togglePlay, v: ACTIONS.approve, x: ACTIONS.reject, u: ACTIONS.reset,
    a: ACTIONS["start-1"], z: ACTIONS["start+1"], q: ACTIONS["end-1"], s: ACTIONS["end+1"],
    d: ACTIONS.head, f: ACTIONS.tail,
    1: () => chooseTitle(0), 2: () => chooseTitle(1), 3: () => chooseTitle(2),
    e: () => focusField('.title-row input[type="radio"]:checked ~ .title-input', ".title-input"),
    t: () => focusField('[data-field="thumb_text"]'),
    "?": () => $("help").showModal(),
  };
  if (map[key]) {
    e.preventDefault();
    map[key]();
  }
});

function focusField(selector, fallback) {
  const card = document.querySelector(".card.active");
  const el = card && (card.querySelector(selector) || (fallback && card.querySelector(fallback)));
  if (el) { el.focus(); el.select?.(); }
}

$("helpbtn").addEventListener("click", () => $("help").showModal());

// --- Suivi des rendus ------------------------------------------------------------
let renderTimer = null;
function watchRenders() {
  if (renderTimer) return;
  renderTimer = setInterval(async () => {
    const q = await api("GET", "/api/render-queue");
    const busy = q.current !== null || q.pending.length;
    const pill = $("renderstate");
    pill.classList.toggle("hidden", !busy);
    pill.textContent = busy ? `Rendu en cours (${q.pending.length + (q.current !== null ? 1 : 0)})` : "";
    const waiting = state.clips.some((c) => c.status === "approved" && !c.render_fresh);
    if (!busy) {
      clearInterval(renderTimer);
      renderTimer = null;
      if (waiting) {
        const data = await api("GET", `/api/videos/${videoId}`);
        data.clips.forEach((c) => {
          const i = state.clips.findIndex((x) => x.id === c.id);
          if (i !== -1 && (c.render_fresh !== state.clips[i].render_fresh || c.status !== state.clips[i].status)) {
            state.clips[i] = c;
            replaceCard(i);
          }
        });
      }
    }
  }, 3000);
}

load().then(watchRenders);
