// El publicador de tu marca en Cloudflare. Cada 5 minutos mira la cola y publica en Instagram lo que toca;
// por la mañana lee los números y a las 08:00 manda el parte por Telegram. No necesita el Mac encendido.
//
// La cola vive en KV (privado): cola:<id> lo pendiente, pub:<id> lo publicado, reg:<fecha>:... lo que pasa,
// metricas:<fecha> los números del día, tareas (lo que apunta Claude), orden:parte (mandar el parte ya).
// Los vídeos, en R2 (reels-de-tu-marca), que Instagram descarga por su dirección pública.
// MODO = "prueba": hace todo menos publicar y avisa de lo que habría publicado. MODO = "real": publica.

const G = "https://graph.facebook.com/v23.0";
const PREPARAR_ANTES = 45;   // min: se manda a Instagram antes, para que a la hora ya esté procesado
const RETRASO_MAX = 360;     // min: si algo se pasa 6 h de su hora no sale a ciegas, avisa
const HORA_METRICAS = "06:30", HORA_PARTE = "08:00";
const METRICAS = "views,reach,saved,shares,ig_reels_avg_watch_time,reels_skip_rate";
const MEDIR_POR_VUELTA = 20;  // Cloudflare gratis deja 50 llamadas por vuelta
const DIAS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];
const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

// ---------- la hora de Madrid ----------
function ahoraLocal(env) {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
    timeZone: env.ZONA || "Europe/Madrid", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date()).map((x) => [x.type, x.value]));
  const fecha = `${p.year}-${p.month}-${p.day}`, hora = `${p.hour}:${p.minute}`;
  return { fecha, hora, txt: `${fecha} ${hora}` };
}
const minutos = (s) => Date.parse(s.replace(" ", "T") + ":00Z") / 60000;
function sumarDias(fecha, n) {
  const d = new Date(fecha + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

// ---------- guardar ----------
async function lista(env, prefijo) {
  const out = [];
  let cursor;
  do {
    const r = await env.KV.list({ prefix: prefijo, cursor });
    out.push(...r.keys);
    cursor = r.list_complete ? null : r.cursor;
  } while (cursor);
  return out;
}
const resumen = (x) => ({
  cuando: x.cuando, tipo: x.tipo, nombre: x.nombre, combo: x.combo || "", estado: x.estado,
  media_id: x.media_id || "", publicado_en: x.publicado_en || "", permalink: x.permalink || "",
  grupo: x.grupo || x.padre || x.nombre,
});
const EN_COLA = ["pendiente", "procesando", "listo"];
const guardar = (env, pre, x) => env.KV.put(pre + x.id, JSON.stringify(x), { metadata: resumen(x) });
async function apuntar(env, t, tipo, datos = {}) {
  const meta = { ts: t.txt, tipo, ...datos };
  if (meta.error) meta.error = String(meta.error).slice(0, 200);
  const k = `reg:${t.fecha}:${t.hora}-${Math.random().toString(36).slice(2, 7)}`;
  await env.KV.put(k, "", { metadata: meta, expirationTtl: 90 * 86400 });
}

// ---------- Telegram e Instagram ----------
async function tg(env, metodo, datos) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return {};
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${metodo}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(datos),
  }).catch(() => null);
  return r ? r.json().catch(() => ({})) : {};
}
async function telegram(env, texto, botones) {
  const d = { chat_id: env.TELEGRAM_CHAT_ID, text: texto.slice(0, 4000), disable_web_page_preview: true };
  if (botones) d.reply_markup = { inline_keyboard: botones };
  return tg(env, "sendMessage", d);
}

class ErrorIG extends Error {
  constructor(msg, codigo) { super(msg); this.codigo = codigo; }
}
async function ig(env, metodo, ruta, datos = {}) {
  const p = new URLSearchParams({ ...datos, access_token: env.IG_TOKEN });
  const r = metodo === "GET" ? await fetch(`${G}/${ruta}?${p}`) : await fetch(`${G}/${ruta}`, { method: "POST", body: p });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.error) {
    const e = j.error || {};
    throw new ErrorIG(e.error_user_msg || e.message || `HTTP ${r.status}`, e.code);
  }
  return j;
}

// ---------- publicar ----------
const publica = (env, clave) => `${env.R2_PUBLIC_URL.replace(/\/$/, "")}/${encodeURI(clave)}`;
async function crearContenedor(env, x) {
  const d = {
    media_type: "REELS", video_url: publica(env, x.video_key), caption: x.pie || "",
    share_to_feed: x.tipo === "prueba" ? "false" : "true",
  };
  if (x.portada_key) d.cover_url = publica(env, x.portada_key);
  if (x.tipo === "prueba") d.trial_params = JSON.stringify({ graduation_strategy: "MANUAL" });
  return (await ig(env, "POST", `${env.IG_USER_ID}/media`, d)).id;
}

const etiqueta = (x) => `${x.nombre}${x.combo ? " " + x.combo : ""} (${x.tipo === "prueba" ? "prueba" : "reel normal"})`;

async function fallo(env, t, x, e) {
  x.intentos = (x.intentos || 0) + 1;
  x.error = String(e.message || e).slice(0, 300);
  const llave = e.codigo === 190;
  if (x.intentos >= 3 || llave) {
    x.estado = "error";
    await telegram(env, `⚠️ ${etiqueta(x)} no se ha podido publicar: ${x.error}` +
      (llave ? "\nLa llave de Instagram no vale: hay que generar otra." : ""));
  } else {
    x.estado = "pendiente";
    delete x.contenedor;
  }
  await apuntar(env, t, "error", { id: x.id, nombre: x.nombre, error: x.error, intento: x.intentos });
}

async function publicar(env, t, x, ensayo) {
  if (ensayo) {
    x.estado = "ensayado";
    await apuntar(env, t, "ensayado", { id: x.id, nombre: x.nombre, tipo_video: x.tipo, combo: x.combo || "" });
    await telegram(env, `🧪 Ensayo: ahora (${t.hora}) habría publicado ${etiqueta(x)}. Instagram lo tenía procesado y listo.`);
    return false;
  }
  const r = await ig(env, "POST", `${env.IG_USER_ID}/media_publish`, { creation_id: x.contenedor });
  x.media_id = r.id;
  x.estado = "publicado";
  x.publicado_en = t.txt;
  try { x.permalink = (await ig(env, "GET", r.id, { fields: "permalink" })).permalink || ""; } catch { /* sin enlace */ }
  await guardar(env, "pub:", x);
  await env.KV.delete("cola:" + x.id);
  await apuntar(env, t, "publicado", { id: x.id, nombre: x.nombre, tipo_video: x.tipo, combo: x.combo || "", permalink: x.permalink });
  if (x.tipo !== "prueba") await telegram(env, `✅ ${x.nombre} publicado en Instagram: ${x.permalink}`);
  return true;
}

// ---------- su sí, por Telegram ----------
// Lo que entra con «nube … --espera» queda en estado "espera": el bot le manda el enlace del vídeo con dos botones.
// Solo vale lo que llega de SU chat (TELEGRAM_CHAT_ID). Sin su sí, a su hora no sale: queda "sin_si" y se le avisa.
const DIA_CORTO = (c) => { const d = new Date(c.slice(0, 10) + "T12:00:00Z"); return `${DIAS[d.getUTCDay()].slice(0, 3)} ${d.getUTCDate()}`; };

async function preguntar(env, t, todas) {
  const cola = (todas || await lista(env, "cola:")).filter((k) => k.metadata?.estado === "espera");
  const grupos = {};
  for (const k of cola) (grupos[k.metadata.grupo || k.metadata.nombre] ||= []).push(k);
  for (const [g, ks] of Object.entries(grupos)) {
    const xs = (await Promise.all(ks.map((k) => env.KV.get(k.name, "json")))).filter((x) => x?.estado === "espera");
    if (!xs.length || xs.every((x) => x.preguntado)) continue;
    if (g.startsWith("semana") && !(await env.KV.get(`pide:${g}`))) continue;   // la semana se pregunta entera, con «nube pide»
    xs.sort((a, b) => a.cuando.localeCompare(b.cuando));
    const normales = xs.filter((x) => x.tipo !== "prueba"), pruebas = xs.filter((x) => x.tipo === "prueba");
    const semana = g.startsWith("semana");
    const l = [semana ? `🗓 La semana entera (${g}): con un sí sale todo esto. Nada sale sin tu sí.` : `🙋 ¿Sale el ${g}? Nada sale sin tu sí.`, ""];
    if (normales.length) l.push(`📲 Reels normales${normales.some((x) => x.tiktok) ? " (Instagram y TikTok)" : ""}:`);
    for (const x of normales) {
      l.push(`${DIA_CORTO(x.cuando)} ${x.cuando.slice(11)} · ${x.nombre}${x.opciones?.length ? ` (mix ${x.opciones.map((o) => o.etiqueta).join("/")}: gana el que mejor vaya en sus pruebas)` : ""}`);
      l.push(`   ${publica(env, x.video_key)}`);
    }
    if (pruebas.length) {
      l.push("", `🧪 ${pruebas.length} prueba${pruebas.length > 1 ? "s" : ""} (solo para quien no te sigue):`);
      const porVideo = {};
      for (const p of pruebas) (porVideo[p.padre || p.nombre] ||= []).push(p);
      for (const [v, ps] of Object.entries(porVideo)) {
        l.push(`${v}: ${ps.length}, ${DIA_CORTO(ps[0].cuando)} → ${DIA_CORTO(ps[ps.length - 1].cuando)}`);
        if (!semana || ps.length <= 2) for (const p of ps) l.push(`   ${DIA_CORTO(p.cuando)} ${p.cuando.slice(11)} · ${p.combo || p.nombre} · ${publica(env, p.video_key)}`);
        else l.push(`   la primera: ${publica(env, ps[0].video_key)}`);
      }
    }
    const r = await telegram(env, l.join("\n"),
      [[{ text: semana ? "✅ Sí, toda la semana" : "✅ Sí, que salga", callback_data: `si:${g}` }, { text: "❌ No", callback_data: `no:${g}` }]]);
    if (!r.ok) continue;
    for (const x of xs) { x.preguntado = t.txt; await guardar(env, "cola:", x); }
    await apuntar(env, t, "preguntado", { nombre: g });
  }
}

async function decidir(env, t, g, si) {
  const ks = (await lista(env, "cola:")).filter((k) => k.metadata?.estado === "espera" && (k.metadata.grupo || k.metadata.nombre) === g);
  const xs = [];
  for (const k of ks) {
    const x = await env.KV.get(k.name, "json");
    if (!x || x.estado !== "espera") continue;
    x.estado = si ? "pendiente" : "rechazado";
    x.decidido = t.txt;
    await guardar(env, "cola:", x);
    xs.push(x);
  }
  await apuntar(env, t, si ? "si" : "no", { nombre: g, cuantos: xs.length });
  if (!xs.length) return `No queda nada de ${g} esperando tu sí (ya estaba decidido o ya pasó su hora).`;
  if (!si) return `👌 ${g} no sale. Queda parado hasta que me digas.`;
  xs.sort((a, b) => a.cuando.localeCompare(b.cuando));
  const normales = xs.filter((x) => x.tipo !== "prueba"), np = xs.length - normales.length;
  return `👍 Hecho: ${g} entra en la cola.` +
    (normales.length ? `\nReels normales: ${normales.map((x) => `${x.nombre} ${DIA_CORTO(x.cuando)} ${x.cuando.slice(11)}`).join(", ")}.` : "") +
    (np ? `\nPruebas: ${np}, de ${DIA_CORTO(xs.find((x) => x.tipo === "prueba").cuando)} en adelante.` : "") +
    (normales.some((x) => x.tiktok) ? "\n🎵 TikTok: Claude lo deja programado desde tu Chrome." : "");
}

// Escucha a Telegram: con `hasta` (ms), se queda esperando botones hasta esa hora y los contesta al momento.
async function respuestas(env, t, hasta = 0) {
  if (!env.TELEGRAM_BOT_TOKEN) return;
  let offset = Number(await env.KV.get("tg:offset")) || 0;
  do {
    const espera = hasta ? Math.max(0, Math.min(50, Math.floor((hasta - Date.now()) / 1000))) : 0;
    const r = await tg(env, "getUpdates", { offset, timeout: espera, allowed_updates: ["message", "callback_query"] });
    if (r.ok && r.result?.length) {
      for (const u of r.result) await atender(env, ahoraLocal(env), u);
      offset = r.result[r.result.length - 1].update_id + 1;
      await env.KV.put("tg:offset", String(offset));
    } else if (!r.ok) break;
  } while (hasta && Date.now() < hasta - 3000);
}

async function atender(env, t, u) {
  {
    const cb = u.callback_query, msg = cb ? cb.message : u.message;
    const suyo = String(msg?.chat?.id) === String(env.TELEGRAM_CHAT_ID);
    let orden = null;
    if (cb) {
      const [q, g] = (cb.data || "").split(":");
      if (g && (q === "si" || q === "no")) orden = { si: q === "si", g };
    } else {
      const m = (msg?.text || "").trim().match(/^(s[ií]|vale|dale|ok|no)\s+(\S+)$/i);
      if (m) orden = { si: m[1].toLowerCase() !== "no", g: m[2] };
    }
    if (suyo && orden) {
      if (cb) {   // primero se contesta al botón, para que no se quede cargando
        await tg(env, "answerCallbackQuery", { callback_query_id: cb.id, text: orden.si ? "Recibido ✅" : "Parado" });
        await tg(env, "editMessageReplyMarkup", { chat_id: msg.chat.id, message_id: msg.message_id, reply_markup: { inline_keyboard: [] } });
      }
      await telegram(env, await decidir(env, t, orden.g, orden.si));
    } else if (cb) {
      await tg(env, "answerCallbackQuery", { callback_query_id: cb.id });
    }
  }
}

// ---------- el mix: a las 08:00 del día D, el ángulo que mejor fue en sus pruebas previas pasa a ser el reel normal ----------
async function elegir(env, t, todas) {
  if (t.hora < HORA_PARTE) return;
  const m = (await env.KV.get(`metricas:${t.fecha}`, "json")) || {};
  for (const k of todas || await lista(env, "cola:")) {
    const md = k.metadata || {};
    if (md.tipo === "prueba" || !md.cuando?.startsWith(t.fecha) || !EN_COLA.includes(md.estado)) continue;
    const x = await env.KV.get(k.name, "json");
    if (!x?.opciones?.length || x.elegido) continue;
    const quedan = minutos(x.cuando) - minutos(t.txt);
    const conNumeros = x.opciones.map((o) => ({ ...o, n: m[o.prueba_id] })).filter((o) => o.n && n(o.n.reels_skip_rate) !== null);
    if (conNumeros.length < x.opciones.length && !m.__fin && quedan > PREPARAR_ANTES + 30) continue;   // aún leyendo números
    let gana, motivo;
    if (conNumeros.length) {
      gana = conNumeros.reduce((a, b) => {
        const sa = n(a.n.reels_skip_rate), sb = n(b.n.reels_skip_rate);
        return sa < sb || (sa === sb && (n(a.n.views) || 0) >= (n(b.n.views) || 0)) ? a : b;
      });
      motivo = conNumeros.map((o) => `${o.etiqueta}: se va el ${fmt(o.n.reels_skip_rate, " %")} en 3 s, ${fmt(o.n.views)} vistas`).join(" · ");
    } else {
      gana = x.opciones[0];
      motivo = "sus pruebas aún no tienen números: va el primero";
    }
    x.video_key = gana.video_key;
    if (gana.portada_key) x.portada_key = gana.portada_key;
    if (gana.pie) x.pie = gana.pie;
    x.combo = gana.etiqueta;
    x.elegido = { etiqueta: gana.etiqueta, motivo, ts: t.txt };
    await guardar(env, "cola:", x);
    await apuntar(env, t, "elegido", { nombre: x.nombre, combo: gana.etiqueta, motivo: motivo.slice(0, 180) });
  }
}

async function vuelta(env) {
  const inicio = Date.now(), t = ahoraLocal(env), ya = minutos(t.txt), ensayo = env.MODO !== "real";
  try { await respuestas(env, t); } catch { /* Telegram caído: la próxima vuelta */ }
  const claves = (await lista(env, "cola:")).sort((a, b) =>
    (a.metadata?.cuando || "").localeCompare(b.metadata?.cuando || ""));
  try { await preguntar(env, t, claves); } catch { /* idem */ }
  let publicadoYa = false, creados = 0;
  for (const k of claves) {
    const m = k.metadata || {};
    if (m.estado === "espera" && m.cuando && minutos(m.cuando) <= ya) {
      const x = await env.KV.get(k.name, "json");
      if (x) {
        x.estado = "sin_si";
        await guardar(env, "cola:", x);
        await apuntar(env, t, "sin_si", { id: x.id, nombre: x.nombre, cuando: x.cuando });
        await telegram(env, `⏸ ${etiqueta(x)} tocaba a las ${x.cuando.slice(11)} y no tenía tu sí: no ha salido.`);
      }
      continue;
    }
    if (!EN_COLA.includes(m.estado) || !m.cuando) continue;
    const falta = minutos(m.cuando) - ya;
    if (m.estado === "pendiente" && falta > PREPARAR_ANTES) continue;
    const x = await env.KV.get(k.name, "json");
    if (!x) continue;
    const antes = JSON.stringify(x);
    if (-falta > RETRASO_MAX) {
      x.estado = "atrasado";
      await apuntar(env, t, "atrasado", { id: x.id, nombre: x.nombre, cuando: x.cuando });
      await telegram(env, `⏸ ${etiqueta(x)} tenía que salir el ${x.cuando} y no salió. No lo publico a ciegas: dime qué hacemos.`);
      await guardar(env, "cola:", x);
      continue;
    }
    try {
      if (x.estado === "pendiente") {
        if (creados >= 3) continue;
        x.contenedor = await crearContenedor(env, x);
        x.estado = "procesando";
        creados++;
      } else if (x.estado === "procesando") {
        const s = await ig(env, "GET", x.contenedor, { fields: "status_code,status" });
        if (s.status_code === "FINISHED") x.estado = "listo";
        else if (s.status_code === "EXPIRED") { x.estado = "pendiente"; delete x.contenedor; }
        else if (s.status_code === "ERROR") {
          throw new ErrorIG(`Instagram no acepta el vídeo (MP4 H.264, AAC, 9:16, de 3 s a 15 min). ${s.status || ""}`);
        }
      }
      if (x.estado === "listo" && falta <= 0 && !publicadoYa) {
        publicadoYa = true;   // uno por vuelta, a ritmo de persona
        if (await publicar(env, t, x, ensayo)) continue;
      }
    } catch (e) {
      await fallo(env, t, x, e);
    }
    if (JSON.stringify(x) !== antes) await guardar(env, "cola:", x);   // KV gratis: 1.000 escrituras al día
  }
  await medir(env, t);
  await elegir(env, t, claves);
  const ordenParte = await env.KV.get("orden:parte");
  if (ordenParte || (t.hora >= HORA_PARTE && !(await env.KV.get(`hecho:parte:${t.fecha}`)))) {
    await telegram(env, await parte(env, t, ensayo));
    if (ordenParte) await env.KV.delete("orden:parte");
    else await env.KV.put(`hecho:parte:${t.fecha}`, "1", { expirationTtl: 10 * 86400 });
  }
  try { await respuestas(env, t, inicio + 280000); } catch { /* la próxima vuelta */ }
}

// ---------- números ----------
async function insights(env, media) {
  const leer = async (l) => {
    const r = await ig(env, "GET", `${media}/insights`, { metric: l });
    const o = {};
    for (const m of r.data || []) o[m.name] = m.values?.[0]?.value ?? m.total_value?.value;
    return o;
  };
  let d;
  try { d = await leer(METRICAS); } catch {
    try { d = await leer("views,reach,saved,shares,ig_reels_avg_watch_time"); } catch (e) { return { error: String(e.message).slice(0, 120) }; }
  }
  if (d.ig_reels_avg_watch_time != null) d.seg_media = Math.round(d.ig_reels_avg_watch_time / 100) / 10;
  return d;
}

async function medir(env, t) {
  if (t.hora < HORA_METRICAS) return;
  const k = `metricas:${t.fecha}`;
  const hechas = (await env.KV.get(k, "json")) || {};
  if (hechas.__fin) return;
  const desde = minutos(t.txt) - 30 * 1440;
  const faltan = (await lista(env, "pub:")).filter((p) => p.metadata?.media_id &&
    minutos(p.metadata.publicado_en) >= desde && !hechas[p.name.slice(4)]);
  for (const p of faltan.slice(0, MEDIR_POR_VUELTA)) {
    hechas[p.name.slice(4)] = { ...p.metadata, ...(await insights(env, p.metadata.media_id)) };
  }
  if (faltan.length <= MEDIR_POR_VUELTA) hechas.__fin = t.txt;
  await env.KV.put(k, JSON.stringify(hechas), { expirationTtl: 120 * 86400 });
}

// ---------- el parte ----------
const n = (v) => (v === null || v === undefined || v === "" || isNaN(Number(v)) ? null : Number(v));
const fmt = (v, suf = "") => (n(v) === null ? "–" : Number(v).toLocaleString("es-ES", { maximumFractionDigits: 1 }) + suf);
const mediana = (l) => { const s = [...l].sort((a, b) => a - b); return s.length ? (s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2) : null; };

async function parte(env, t, ensayo) {
  const d = new Date(t.fecha + "T12:00:00Z");
  const lineas = [`${ensayo ? "🧪 (ensayo) " : ""}☀️ Parte del ${DIAS[d.getUTCDay()]} ${d.getUTCDate()} ${MESES[d.getUTCMonth()]}`];

  // 1. Ayer
  const ayer = (await lista(env, `reg:${sumarDias(t.fecha, -1)}:`)).map((k) => k.metadata || {});
  const pub = ayer.filter((e) => e.tipo === "publicado");
  const normales = pub.filter((e) => e.tipo_video !== "prueba"), pruebas = pub.filter((e) => e.tipo_video === "prueba");
  const l1 = [];
  for (const e of normales) l1.push(`✅ ${e.nombre} publicado ${e.permalink || ""}`.trim());
  if (pruebas.length) l1.push(`✅ ${pruebas.length} pruebas: ${pruebas.map((e) => e.nombre + (e.combo ? " " + e.combo : "")).join(", ")}`);
  for (const e of ayer.filter((e) => e.tipo === "ensayado")) l1.push(`🧪 Habría publicado ${e.nombre}${e.combo ? " " + e.combo : ""}`);
  // TikTok lo programa Claude desde su Chrome (TikTok Studio) y lo apunta aquí con «nube tiktok»
  const tiktok = (await lista(env, "tk:")).map((k) => k.metadata || {});
  for (const x of tiktok.filter((x) => x.cuando?.startsWith(sumarDias(t.fecha, -1)))) l1.push(`🎵 TikTok: ${x.nombre} salió a las ${x.cuando.slice(11)}`);
  for (const e of ayer.filter((e) => e.tipo === "error")) l1.push(`❌ ${e.nombre}: ${e.error}`);
  for (const e of ayer.filter((e) => e.tipo === "atrasado")) l1.push(`⏸ ${e.nombre} no salió a su hora`);
  lineas.push("", "1. Ayer", ...(l1.length ? l1 : ["Sin publicaciones ayer."]));

  // 2. Números
  const m = (await env.KV.get(`metricas:${t.fecha}`, "json")) || {};
  const filas = Object.entries(m).filter(([id]) => id !== "__fin").map(([id, v]) => ({ id, ...v }));
  const med = mediana(filas.filter((f) => f.tipo !== "prueba" && n(f.views) !== null).map((f) => n(f.views)));
  const recientes = filas.filter((f) => f.publicado_en && minutos(t.txt) - minutos(f.publicado_en) <= 3 * 1440)
    .sort((a, b) => b.publicado_en.localeCompare(a.publicado_en));
  const l2 = recientes.map((f) => `${f.nombre}${f.combo ? " " + f.combo : ""} · ${f.tipo === "prueba" ? "prueba" : "normal"} · ` +
    `${fmt(f.views)} vistas${f.tipo !== "prueba" && med && n(f.views) ? ` (${fmt(n(f.views) / med)}× tu mediana)` : ""} · ` +
    `se van ${fmt(f.reels_skip_rate, " %")} en 3 s · ${fmt(f.seg_media, " s")} de media`);
  if (med) l2.push(`Tu mediana de reels normales (30 días): ${fmt(med)} vistas.`);
  const conSkip = filas.filter((f) => f.tipo === "prueba" && n(f.reels_skip_rate) !== null);
  if (conSkip.length) {
    const mejor = conSkip.reduce((a, b) => (n(a.reels_skip_rate) <= n(b.reels_skip_rate) ? a : b));
    l2.push(`🏆 Mejor gancho en pruebas: ${mejor.nombre}${mejor.combo ? " " + mejor.combo : ""} (se va el ${fmt(mejor.reels_skip_rate, " %")} en 3 s).`);
  }
  if (!m.__fin && filas.length) l2.push("(Aún leyendo números: faltan algunos vídeos.)");
  lineas.push("", "2. Números", ...(l2.length ? l2 : ["Aún no hay números de lo publicado."]));

  // 3. Hoy sale
  const cola = (await lista(env, "cola:")).map((k) => k.metadata || {});
  const hoy = cola.filter((x) => x.cuando?.startsWith(t.fecha) && [...EN_COLA, "espera"].includes(x.estado))
    .sort((a, b) => a.cuando.localeCompare(b.cuando));
  const l3 = hoy.map((x) => `${x.cuando.slice(11)} ${etiqueta(x)}${x.estado === "espera" ? " · espera tu sí" : ""}`);
  for (const e of (await lista(env, `reg:${t.fecha}:`)).map((k) => k.metadata || {}).filter((e) => e.tipo === "elegido"))
    l3.push(`🏆 Mix ${e.nombre}: sale el ángulo ${e.combo} (${e.motivo}).`);
  lineas.push("", "3. Hoy sale en Instagram", ...(l3.length ? l3 : ["Nada programado."]));
  const tkHoy = tiktok.filter((x) => x.cuando?.startsWith(t.fecha)).sort((a, b) => a.cuando.localeCompare(b.cuando));
  lineas.push("", "En TikTok", ...(tkHoy.length ? tkHoy.map((x) => `${x.cuando.slice(11)} ${x.nombre}`) : ["Nada programado."]));
  const tkProximos = tiktok.filter((x) => x.cuando > t.fecha + " 23:59").length;

  // 4. Lo tuyo
  const l4 = [];
  if (hoy.some((x) => x.tipo !== "prueba") && !tkHoy.length) l4.push("🎵 Hoy hay reel en Instagram y no en TikTok: dime y lo programo desde tu Chrome.");
  if (!tkProximos) l4.push("🎵 En TikTok no hay nada programado para los próximos días.");
  if (!hoy.some((x) => x.tipo !== "prueba")) l4.push("📅 Hoy no sale reel normal: dime qué vídeo va.");
  for (const x of cola.filter((x) => ["error", "atrasado"].includes(x.estado))) l4.push(`⚠️ Decidir qué hacemos con ${etiqueta(x)} (${x.estado}).`);
  const esperan = [...new Set(cola.filter((x) => x.estado === "espera").map((x) => x.grupo || x.nombre))];
  for (const g of esperan) l4.push(`🙋 Falta tu sí a ${g}: pulsa el botón del mensaje (o escribe «sí ${g}»).`);
  const ultimoNormal = cola.filter((x) => x.tipo !== "prueba" && ["pendiente", "espera"].includes(x.estado)).map((x) => x.cuando.slice(0, 10)).sort().pop();
  const reserva = ultimoNormal ? Math.round((minutos(ultimoNormal + " 12:00") - minutos(t.fecha + " 12:00")) / 1440) : 0;
  if (reserva <= 2) l4.push(`🎥 Quedan reels normales para ${reserva} día${reserva === 1 ? "" : "s"}: toca editar o grabar.`);
  const tareas = (await env.KV.get("tareas", "json")) || [];
  for (const x of tareas.filter((x) => !x.hecha && x.para <= t.fecha)) l4.push(`${x.quien === "claude" ? "🤖" : "☐"} ${x.texto}`);
  lineas.push("", "4. Lo tuyo hoy", ...(l4.length ? l4 : ["Nada: todo va solo."]));
  return lineas.join("\n");
}

export default {
  async scheduled(_evento, env) {
    await vuelta(env);   // con await: así Cloudflare deja que la vuelta escuche a Telegram hasta 4 min 40 s
  },
};
