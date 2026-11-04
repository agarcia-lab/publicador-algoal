"""El parte de la mañana: lo que se hizo ayer, cómo van los números, lo que te toca hoy y lo que hace Claude hoy.

Lo escribe en calendario/partes/AAAA-MM-DD.md y, con --enviar, te avisa en el Mac y por Telegram si hay
TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en .env. Lo lanza solo el programador a las 08:00.
"""
import json, statistics, subprocess, sys, urllib.parse, urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ig_api  # noqa: E402
import programador as pg  # noqa: E402

RAIZ = pg.RAIZ
TRABAJO = RAIZ / "videos" / "trabajo"
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fmt(v, suf=""):
    n = num(v)
    if n is None:
        return "–"
    return (f"{n:,.0f}".replace(",", ".") if n >= 100 else f"{n:g}") + suf


def ayer(reg, hoy):
    d = (hoy - timedelta(days=1)).isoformat()
    lineas = []
    for e in reg:
        if e["ts"][:10] != d:
            continue
        if e["tipo"] == "publicado":
            lineas.append(f"✅ Publicado {e['nombre']} ({e.get('tipo_video', '')}{' ' + e['combo'] if e.get('combo') else ''})")
        elif e["tipo"] == "error":
            lineas.append(f"❌ {e['nombre']}: {e['error'][:140]}")
        elif e["tipo"] == "atrasado":
            lineas.append(f"⏸ {e['nombre']} no salió (el Mac estaba apagado o dormido a las {e['cuando'][11:]})")
        elif e["tipo"] in ("trabajo", "programado"):
            lineas.append(f"🛠 {e.get('texto') or 'Programado ' + e['nombre'] + ' para ' + e.get('cuando', '')}")
        elif e["tipo"] == "llave":
            lineas.append("🔑 Llave de Instagram renovada" if "error" not in e else f"❌ Llave: {e['error']}")
    return lineas or ["Sin movimientos ayer."]


def rendimiento(cal, ult, hoy):
    publicados = [x for x in cal if x["estado"] == "publicado" and x["id"] in ult]
    normales = [num(ult[x["id"]]["views"]) for x in publicados if x["tipo"] == "normal"]
    mediana = statistics.median([v for v in normales if v is not None]) if [v for v in normales if v] else None
    recientes = [x for x in publicados
                 if (hoy - datetime.fromisoformat(x["publicado_en"]).date()).days <= 3]
    lineas = []
    if recientes:
        lineas.append("| Vídeo | Tipo | Vistas | Se van en 3 s | Seg. de media |")
        lineas.append("|---|---|---|---|---|")
        for x in sorted(recientes, key=lambda x: x["publicado_en"], reverse=True):
            m = ult[x["id"]]
            veces = ""
            if x["tipo"] == "normal" and mediana and num(m["views"]):
                veces = f" ({num(m['views']) / mediana:.1f}× tu mediana)"
            lineas.append(f"| {x['nombre']}{' ' + x['combo'] if x.get('combo') else ''} | {x['tipo']} | "
                          f"{fmt(m['views'])}{veces} | {fmt(m['skip_rate'], ' %')} | {fmt(m['seg_media'], ' s')} |")
    else:
        lineas.append("Nada publicado en los últimos 3 días con números aún.")
    if mediana:
        lineas.append(f"\nTu mediana (reels normales, 30 días): {fmt(mediana)} vistas.")
    # La mejor prueba en marcha: el gancho que menos gente echa en 3 s
    pruebas = [x for x in publicados if x["tipo"] == "prueba" and num(ult[x["id"]]["skip_rate"]) is not None]
    if pruebas:
        mejor = min(pruebas, key=lambda x: num(ult[x["id"]]["skip_rate"]))
        lineas.append(f"Mejor gancho en pruebas: {mejor['nombre']} {mejor.get('combo', '')} "
                      f"(se va el {fmt(ult[mejor['id']]['skip_rate'], ' %')} en 3 s).")
    return lineas


def pendientes_tuyos(cal, hoy):
    """Lo que solo puedes hacer tú: grabar, aprobar, decidir."""
    t = []
    res = pg.RESULTADOS
    programados = {x["nombre"] for x in cal if x["tipo"] == "normal"}
    for d in sorted(TRABAJO.glob("*")) if TRABAJO.exists() else []:
        if not (d / "hablado.json").exists() or d.name in programados:
            continue
        editado = pg.ultima_version(d.name)
        plan = next(iter(res.glob(f"{d.name}*plan*")), None) if res.exists() else None
        if editado:
            t.append(f"👀 Ver con sonido «{editado.name}» y darme el visto bueno (o las correcciones) para programarlo")
        elif plan:
            t.append(f"✍️ Aprobar el plan por escenas de «{d.name}» (Editor IA/Resultados/{plan.name})")
        else:
            t.append(f"🎥 Grabar «{d.name}» (guion listo: 3 ganchos + cuerpo) y dejarlo en Editor IA/Material crudo/")
    for x in cal:
        if x["estado"] in ("error", "atrasado"):
            t.append(f"⚠️ Decidir qué hacemos con {x['nombre']} ({x['estado']}: {x.get('error', 'no salió a su hora')[:80]})")
    normales_hoy = [x for x in cal if x["tipo"] == "normal" and x["cuando"][:10] == hoy.isoformat()]
    if not normales_hoy:
        t.append("📅 Hoy no sale ningún reel normal: dime tema o «busca inspiración» y te hago el guion")
    d = ig_api.dias_de_llave()
    if d is not None and d >= 55:
        t.append(f"🔑 La llave de Instagram caduca en {max(60 - d, 0)} días y no se ha podido renovar: genera otra en el panel de Meta")
    return t


def tareas_de(quien, hoy):
    return [f"☐ {x['texto']} `{x['id']}`" for x in pg.cargar(pg.TAREAS, [])
            if x["quien"] == quien and not x["hecha"] and x["para"] <= hoy.isoformat()]


def lo_de_claude(cal, reg, hoy):
    t = []
    for x in sorted(cal, key=lambda x: x["cuando"]):
        if x["estado"] == "pendiente" and x["cuando"][:10] == hoy.isoformat():
            t.append(f"📤 {x['cuando'][11:]} publicar {x['nombre']} ({x['tipo']}{' ' + x['combo'] if x.get('combo') else ''})")
    # Comparar las pruebas cuando la última lleva 24 h fuera
    comparados = {e.get("nombre") for e in reg if e["tipo"] == "comparado"}
    padres = {x.get("padre") for x in cal if x["tipo"] == "prueba"} - {None}
    for p in padres:
        suyas = [x for x in cal if x.get("padre") == p]
        if p not in comparados and suyas and all(x["estado"] == "publicado" for x in suyas):
            ultima = max(datetime.fromisoformat(x["publicado_en"]) for x in suyas)
            if (datetime.now(ultima.tzinfo) - ultima) >= timedelta(hours=24):
                t.append(f"🏆 Comparar las {len(suyas)} pruebas de «{p}» y apuntar el gancho ganador en marca/lecciones.md")
    t.append("📊 Leer los números de todo lo publicado (07:30) y vigilar el alcance de la cuenta")
    return t


def hacer(enviar=False):
    hoy = pg.ahora().date()
    cal, reg, ult = pg.cargar(pg.CALENDARIO, []), pg.leer_registro(), pg.ultimas_metricas()
    titulo = f"☀️ Parte del {DIAS[hoy.weekday()]} {hoy.day} {MESES[hoy.month - 1]}"
    bloques = [
        ("1. Lo que se hizo ayer", ayer(reg, hoy)),
        ("2. Rendimiento", rendimiento(cal, ult, hoy)),
        ("3. Lo que te toca hoy", pendientes_tuyos(cal, hoy) + tareas_de("tu", hoy)),
        ("4. Lo que hago yo hoy", lo_de_claude(cal, reg, hoy) + tareas_de("claude", hoy)),
    ]
    texto = titulo + "\n\n" + "\n\n".join(f"**{t}**\n" + "\n".join(f"- {l}" if not l.startswith(("|", "\n")) else l
                                                                     for l in ls) for t, ls in bloques) + "\n"
    destino = pg.CAL / "partes" / f"{hoy.isoformat()}.md"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(texto, encoding="utf-8")
    print(texto)
    if enviar:
        avisar(titulo, texto, destino)
    return texto


def avisar(titulo, texto, fichero):
    try:   # macOS: notificación
        subprocess.run(["osascript", "-e", f'display notification "Ábrelo en calendario/partes" with title "{titulo}"'],
                       check=False, capture_output=True, timeout=10)
    except Exception:
        pass
    e = ig_api.leer_env()
    if e.get("TELEGRAM_BOT_TOKEN") and e.get("TELEGRAM_CHAT_ID"):
        datos = urllib.parse.urlencode({"chat_id": e["TELEGRAM_CHAT_ID"], "text": texto.replace("**", "")[:4000]}).encode()
        try:
            urllib.request.urlopen(f"https://api.telegram.org/bot{e['TELEGRAM_BOT_TOKEN']}/sendMessage", data=datos, timeout=20)
        except Exception as err:
            pg.apuntar("error", nombre="parte", error=f"Telegram: {err}")


if __name__ == "__main__":
    hacer(enviar="--enviar" in sys.argv)
