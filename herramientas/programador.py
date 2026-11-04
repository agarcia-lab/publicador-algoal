"""El programador: el calendario de publicaciones, los números de cada día y el parte de la mañana.

La API de Instagram no programa (publica al llamarla), así que esto corre cada 10 minutos en tu Mac (launchd,
herramientas/instalar-programador.sh) y publica lo que toca. Todo lo que pasa se apunta en calendario/registro.jsonl.

  programar tick                       la vuelta de cada 10 min: publica lo que toca, números (07:30), parte (08:00),
                                       renueva la llave a los 50 días
  programar normal NOMBRE --cuando "AAAA-MM-DD HH:MM"    la última versión de Editor IA/Resultados/NOMBRE_editado_vN.mp4
  programar pruebas NOMBRE             las 5 pruebas de Resultados/NOMBRE_pruebas-horario.txt
  programar ver                        lo que queda por salir
  programar quitar ID                  lo saca del calendario (no borra el vídeo)
  programar metricas                   lee ya los números de lo publicado en los últimos 30 días
  programar comparar NOMBRE            las 5 pruebas de un vídeo entre ellas → tabla para marca/lecciones.md
  programar apunta "texto" [--quien claude|tu] [--para AAAA-MM-DD]   una tarea para el parte
  programar hecho "texto"              apunta algo que Claude ha hecho (sale en «ayer» del parte)
  programar parte [--enviar]           el parte de hoy (ver herramientas/parte.py)
"""
import argparse, csv, json, re, sys, uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ig_api  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
CAL = RAIZ / "calendario"
CALENDARIO, REGISTRO, METRICAS, TAREAS = CAL / "calendario.json", CAL / "registro.jsonl", CAL / "metricas.csv", CAL / "tareas.json"
RESULTADOS = RAIZ / "Editor IA" / "Resultados"     # la guía de Converzzo: aquí está lo editado
SALIDA = RAIZ / "videos" / "salida"                 # (La Fórmula, por si se usa /edicion)


def ultima_version(nombre, extra=""):
    """El vídeo aprobado más reciente: Resultados/<nombre><extra>_editado_vN.mp4 con la N más alta."""
    def n(f):
        m = re.search(r"_v(\d+)\.mp4$", f.name)
        return int(m.group(1)) if m else 0
    hay = sorted(RESULTADOS.glob(f"{nombre}{extra}_editado_v*.mp4"), key=n)
    if hay:
        return hay[-1]
    viejo = SALIDA / f"{nombre}{extra}.mp4"
    return viejo if viejo.exists() else None
CAMPOS = ["fecha", "id", "nombre", "tipo", "combo", "media_id", "views", "reach", "saved", "shares", "seg_media", "skip_rate"]
HORA_METRICAS, HORA_PARTE = "07:30", "08:00"
MARGEN_RETRASO = timedelta(hours=6)   # con el Mac dormido: si llega más tarde, no publica a ciegas, te pregunta


def zona():
    try:
        m = json.loads((RAIZ / "marca" / "marca.json").read_text(encoding="utf-8"))
        return ZoneInfo(m.get("zona_horaria") or "Europe/Madrid")
    except Exception:
        return ZoneInfo("Europe/Madrid")


def ahora():
    return datetime.now(zona())


def cuando_de(texto):
    return datetime.strptime(texto, "%Y-%m-%d %H:%M").replace(tzinfo=zona())


def cargar(f, vacio):
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else vacio


def guardar(f, datos):
    CAL.mkdir(exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


def apuntar(tipo, **datos):
    CAL.mkdir(exist_ok=True)
    with REGISTRO.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": ahora().isoformat(timespec="minutes"), "tipo": tipo, **datos}, ensure_ascii=False) + "\n")


def choques(cal, cuando, tipo):
    """Nada a menos de 60 min de otro normal; nada a menos de 20 min de cualquier cosa."""
    c = cuando_de(cuando)
    malos = []
    for x in cal:
        if x["estado"] in ("quitado",):
            continue
        d = abs((cuando_de(x["cuando"]) - c).total_seconds()) / 60
        if d < 20 or (d < 60 and tipo == "normal" and x["tipo"] == "normal"):
            malos.append(x)
    return malos


def anadir(cal, **item):
    item = {"id": uuid.uuid4().hex[:6], "estado": "pendiente", **item}
    malos = choques(cal, item["cuando"], item["tipo"])
    if malos:
        print(f"⚠️  {item['cuando']} choca con {', '.join(m['nombre'] + ' ' + m['cuando'] for m in malos)}: muévelo.")
    cal.append(item)
    return item


# ---------- publicar ----------
def publicar_item(cal, x):
    x["estado"], x["intentos"] = "publicando", x.get("intentos", 0) + 1
    guardar(CALENDARIO, cal)
    try:
        r = ig_api.publicar(x["video"], x.get("pie"), x.get("portada"), prueba=x["tipo"] == "prueba")
        x.update(estado="publicado", publicado_en=ahora().isoformat(timespec="minutes"), **r)
        apuntar("publicado", id=x["id"], nombre=x["nombre"], tipo_video=x["tipo"], combo=x.get("combo", ""),
                permalink=r.get("permalink", ""))
        print(f"✅ Publicado {x['nombre']} ({x['tipo']}{' ' + x['combo'] if x.get('combo') else ''}) {r.get('permalink', '')}")
    except (ig_api.ErrorIG, SystemExit, Exception) as e:  # noqa: B014
        x["estado"] = "pendiente" if x["intentos"] < 3 else "error"
        x["error"] = str(e)
        apuntar("error", id=x["id"], nombre=x["nombre"], error=str(e), intento=x["intentos"])
        print(f"❌ {x['nombre']}: {e}")
    guardar(CALENDARIO, cal)


def tick(args=None):
    cal = cargar(CALENDARIO, [])
    t = ahora()
    # 1. Lo que toca: uno por vuelta (a ritmo de persona), el más antiguo primero
    for x in sorted(cal, key=lambda x: x["cuando"]):
        if x["estado"] != "pendiente" or cuando_de(x["cuando"]) > t:
            continue
        if t - cuando_de(x["cuando"]) > MARGEN_RETRASO:
            x["estado"] = "atrasado"
            apuntar("atrasado", id=x["id"], nombre=x["nombre"], cuando=x["cuando"])
            guardar(CALENDARIO, cal)
            continue
        publicar_item(cal, x)
        break
    hoy = t.date().isoformat()
    hechos = {(e["tipo"], e["ts"][:10]) for e in leer_registro()}
    # 2. La llave, a los 50 días
    d = ig_api.dias_de_llave()
    if d is not None and d >= 50 and ("llave", hoy) not in hechos:
        try:
            r = ig_api.renovar()
            apuntar("llave", dias=r["dias"])
        except Exception as e:
            apuntar("llave", error=str(e))
    # 3. Los números de cada día y el parte
    if t.strftime("%H:%M") >= HORA_METRICAS and ("metricas", hoy) not in hechos:
        metricas()
    if t.strftime("%H:%M") >= HORA_PARTE and ("parte", hoy) not in hechos:
        import parte
        parte.hacer(enviar=True)
        apuntar("parte")


# ---------- números ----------
def leer_registro():
    if not REGISTRO.exists():
        return []
    return [json.loads(l) for l in REGISTRO.read_text(encoding="utf-8").splitlines() if l.strip()]


def metricas(args=None):
    cal = cargar(CALENDARIO, [])
    limite = ahora() - timedelta(days=30)
    nuevo = not METRICAS.exists()
    filas = 0
    CAL.mkdir(exist_ok=True)
    with METRICAS.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        if nuevo:
            w.writeheader()
        for x in cal:
            if x["estado"] != "publicado" or not x.get("media_id"):
                continue
            if datetime.fromisoformat(x["publicado_en"]) < limite:
                continue
            try:
                m = ig_api.insights(x["media_id"])
            except Exception as e:
                apuntar("error", id=x["id"], nombre=x["nombre"], error=f"insights: {e}")
                continue
            w.writerow({"fecha": ahora().date().isoformat(), "id": x["id"], "nombre": x["nombre"], "tipo": x["tipo"],
                        "combo": x.get("combo", ""), "media_id": x["media_id"], "views": m.get("views"),
                        "reach": m.get("reach"), "saved": m.get("saved"), "shares": m.get("shares"),
                        "seg_media": m.get("seg_media"), "skip_rate": m.get("reels_skip_rate")})
            filas += 1
    apuntar("metricas", videos=filas)
    print(f"📊 Números de {filas} vídeos guardados en {METRICAS.relative_to(RAIZ)}")


def ultimas_metricas():
    """La última lectura de cada vídeo publicado."""
    if not METRICAS.exists():
        return {}
    ult = {}
    with METRICAS.open(encoding="utf-8") as f:
        for fila in csv.DictReader(f):
            ult[fila["id"]] = fila
    return ult


def comparar(args):
    """Las pruebas de un mismo vídeo entre ellas (nunca con el normal: ese lo ven también tus seguidores)."""
    cal, ult = cargar(CALENDARIO, []), ultimas_metricas()
    pruebas = [x for x in cal if x.get("padre") == args.nombre and x["tipo"] == "prueba" and x["id"] in ult]
    hablado = RAIZ / "videos" / "trabajo" / args.nombre / "hablado.json"
    ganchos = json.loads(hablado.read_text(encoding="utf-8")).get("ganchos", []) if hablado.exists() else []
    for x in pruebas:   # B1 → el gancho B (el 2.º de hablado.json), dicho tal cual
        letra = (x.get("combo") or "")[:1]
        if letra in "ABC" and letra and "ABC".index(letra) < len(ganchos):
            x["gancho"] = ganchos["ABC".index(letra)].get("dicho", "")
    if not pruebas:
        sys.exit("Aún no hay números de sus pruebas (espera 24 h desde la última).")
    num = lambda v: float(v) if v not in (None, "", "None") else None  # noqa: E731
    filas = sorted(pruebas, key=lambda x: num(ult[x["id"]]["skip_rate"]) if num(ult[x["id"]]["skip_rate"]) is not None else 999)
    print(f"## {args.nombre} ({ahora().strftime('%d-%b')})")
    for i, x in enumerate(filas):
        m = ult[x["id"]]
        print(f"- {x.get('combo') or x['nombre']} «{x.get('gancho', '')}»: se van {m['skip_rate']} % en 3 s · "
              f"{m['seg_media']} s de media · {m['views']} vistas{'  ← gana' if i == 0 else ''}")
    print("Aprendido: (qué tiene el gancho ganador que no tienen los otros). Probarlo en el siguiente.")
    apuntar("comparado", nombre=args.nombre)


# ---------- añadir ----------
def normal(args):
    cal = cargar(CALENDARIO, [])
    v = Path(args.video) if args.video else ultima_version(args.nombre)
    if not v or not v.exists():
        sys.exit(f"No encuentro {RESULTADOS.relative_to(RAIZ)}/{args.nombre}_editado_vN.mp4: primero la edición.")
    pie = Path(args.pie) if args.pie else next((p for p in (RESULTADOS / f"{args.nombre}.pie.txt",
                                                             SALIDA / f"{args.nombre}.pie.txt") if p.exists()), None)
    if not pie:
        sys.exit(f"Falta el pie: {RESULTADOS.relative_to(RAIZ)}/{args.nombre}.pie.txt")
    portada = Path(args.portada) if args.portada else v.with_suffix(".portada.png")
    x = anadir(cal, tipo="normal", nombre=args.nombre, cuando=args.cuando, video=str(v), pie=str(pie),
               portada=str(portada) if portada.exists() else "")
    guardar(CALENDARIO, cal)
    apuntar("programado", id=x["id"], nombre=args.nombre, cuando=args.cuando, tipo_video="normal")
    print(f"🗓  {args.nombre} ({v.name}) → {args.cuando} (id {x['id']})")


def pruebas(args):
    cal = cargar(CALENDARIO, [])
    horario = next((h for h in ([Path(args.horario)] if args.horario else []) +
                    [RESULTADOS / f"{args.nombre}_pruebas-horario.txt",
                     SALIDA / "pruebas" / f"{args.nombre}-pruebas-horario.txt"] if h.exists()), None)
    if not horario:
        sys.exit(f"Falta el horario de pruebas: {RESULTADOS.relative_to(RAIZ)}/{args.nombre}_pruebas-horario.txt")
    pie_padre = next((p for p in (RESULTADOS / f"{args.nombre}.pie.txt", SALIDA / f"{args.nombre}.pie.txt") if p.exists()), None)
    for linea in horario.read_text(encoding="utf-8").splitlines():
        m = re.match(r"(\S+\.mp4)\s+(\d{4}-\d\d-\d\d) (\d\d:\d\d)(?:\s+\((\w+)\))?", linea.strip())
        if not m:
            continue
        fichero, dia, hora, combo = m.groups()
        v = horario.parent / fichero
        if not v.exists():
            print(f"⚠️  No está {v.name}: esa prueba no se programa.")
            continue
        pie = v.with_suffix(".pie.txt") if v.with_suffix(".pie.txt").exists() else pie_padre
        portada = v.with_suffix(".portada.png")
        x = anadir(cal, tipo="prueba", nombre=v.stem, padre=args.nombre, combo=combo or "", cuando=f"{dia} {hora}",
                   video=str(v), pie=str(pie) if pie else "", portada=str(portada) if portada.exists() else "")
        apuntar("programado", id=x["id"], nombre=v.stem, cuando=x["cuando"], tipo_video="prueba")
        print(f"🗓  {v.stem}{' (' + combo + ')' if combo else ''} → {x['cuando']}")
    guardar(CALENDARIO, cal)


def ver(args=None):
    cal = cargar(CALENDARIO, [])
    for x in sorted(cal, key=lambda x: x["cuando"]):
        if x["estado"] in ("pendiente", "atrasado", "error", "publicando"):
            extra = f"  ⚠️ {x.get('error', '')}" if x["estado"] == "error" else ""
            print(f"{x['id']}  {x['cuando']}  {x['tipo']:6}  {x['estado']:9}  {x['nombre']} {x.get('combo', '')}{extra}")


def quitar(args):
    cal = cargar(CALENDARIO, [])
    for x in cal:
        if x["id"] == args.id:
            x["estado"] = "quitado"
            apuntar("quitado", id=x["id"], nombre=x["nombre"])
            print(f"Quitado {x['nombre']} {x['cuando']}")
    guardar(CALENDARIO, cal)


def apunta(args):
    tareas = cargar(TAREAS, [])
    tareas.append({"id": uuid.uuid4().hex[:6], "texto": args.texto, "quien": args.quien,
                   "para": args.para or ahora().date().isoformat(), "hecha": False})
    guardar(TAREAS, tareas)
    print(f"Apuntado para {args.quien}: {args.texto}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="orden", required=True)
    sub.add_parser("tick").set_defaults(f=tick)
    q = sub.add_parser("normal"); q.add_argument("nombre"); q.add_argument("--cuando", required=True)
    q.add_argument("--video"); q.add_argument("--pie"); q.add_argument("--portada"); q.set_defaults(f=normal)
    q = sub.add_parser("pruebas"); q.add_argument("nombre"); q.add_argument("--horario"); q.set_defaults(f=pruebas)
    sub.add_parser("ver").set_defaults(f=ver)
    q = sub.add_parser("quitar"); q.add_argument("id"); q.set_defaults(f=quitar)
    sub.add_parser("metricas").set_defaults(f=metricas)
    q = sub.add_parser("comparar"); q.add_argument("nombre"); q.set_defaults(f=comparar)
    q = sub.add_parser("apunta"); q.add_argument("texto"); q.add_argument("--quien", default="tu", choices=["tu", "claude"])
    q.add_argument("--para"); q.set_defaults(f=apunta)
    q = sub.add_parser("tachar"); q.add_argument("id")
    q.set_defaults(f=lambda a: guardar(TAREAS, [{**t, "hecha": t["hecha"] or t["id"] == a.id} for t in cargar(TAREAS, [])]))
    q = sub.add_parser("hecho"); q.add_argument("texto")
    q.set_defaults(f=lambda a: (apuntar("trabajo", texto=a.texto), print(f"Apuntado: {a.texto}")))
    q = sub.add_parser("parte"); q.add_argument("--enviar", action="store_true")
    q.set_defaults(f=lambda a: __import__("parte").hacer(enviar=a.enviar))
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
