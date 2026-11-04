"""La cola del publicador en la nube (Cloudflare, nube/src/index.js): lo que sale en Instagram, sin depender del Mac.

Desde el Mac solo se mete y se mira la cola; publicar, leer números y mandar el parte lo hace Cloudflare.

  nube normal NOMBRE --cuando "AAAA-MM-DD HH:MM"      reel normal (videos/salida/NOMBRE.mp4, .pie.txt, .portada.png)
  nube pruebas NOMBRE F1.mp4 F2.mp4 … [--desde AAAA-MM-DD] [--combos B1,C2,…]
                                                    una prueba al día desde --desde (mañana), de 11:00 a 21:00,
                                                    en el primer hueco libre que no choque con nada
  nube mix NOMBRE --dia AAAA-MM-DD --angulo "A=normal_A.mp4,prueba_A.mp4" --angulo "B=…" [--espera]
                                                    el mix: la prueba de cada ángulo los días D-2 y D-1 (11:00) y el
                                                    reel normal el día D (20:30), con el ángulo que gane
  --grupo semana-AAAA-MM-DD   lo mete en el lote de esa semana; «nube pide semana-…» manda UN mensaje con todo
  nube si NOMBRE / nube no NOMBRE                   su sí (o su no) dado en el chat a lo que espera
  Con --espera, el bot de Telegram le manda el enlace y los botones «Sí, que salga» / «No»: sin su sí no sale.
  nube tiktok NOMBRE --cuando "AAAA-MM-DD HH:MM"    apunta lo programado en TikTok Studio, para el parte
  nube ver                                          lo que queda por salir
  nube quitar ID                                    lo saca de la cola (no borra nada de Instagram)
  nube apunta "texto" [--quien tu|claude] [--para AAAA-MM-DD]     una tarea para el parte
  nube parte                                        manda ya el parte por Telegram (en la próxima vuelta, ≤5 min)

🚨 Nada entra en la cola sin tu sí a ESE vídeo.
"""
import argparse, json, mimetypes, subprocess, sys, tempfile, uuid
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ig_api  # noqa: E402
import programador as pg  # noqa: E402

RAIZ = pg.RAIZ
NUBE = RAIZ / "nube"
HUECOS = ["11:00", "12:30", "14:00", "15:30", "17:00", "18:30", "21:00"]   # pruebas; el normal, 20:30


# ---------- Cloudflare ----------
def wr(*args):
    r = subprocess.run(["npx", "--yes", "wrangler", *args], cwd=NUBE, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"❌ Cloudflare: {(r.stderr or r.stdout).strip()[-400:]}")
    return r.stdout


def lista(prefijo):
    out = wr("kv", "key", "list", "--binding", "KV", "--remote", "--prefix", prefijo)
    return json.loads(out[out.index("["):]) if "[" in out else []


def poner(clave, valor, meta=None):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(valor, f, ensure_ascii=False)
    a = ["kv", "key", "put", clave, "--path", f.name, "--binding", "KV", "--remote"]
    if meta:
        a += ["--metadata", json.dumps(meta, ensure_ascii=False)]
    try:
        wr(*a)
    finally:
        Path(f.name).unlink(missing_ok=True)


def leer(clave):
    r = subprocess.run(["npx", "--yes", "wrangler", "kv", "key", "get", clave, "--binding", "KV", "--remote", "--text"],
                       cwd=NUBE, capture_output=True, text=True)
    try:
        return json.loads(r.stdout) if r.returncode == 0 else None
    except json.JSONDecodeError:
        return None


def resumen(x):
    r = {k: x.get(k, "") for k in ("cuando", "tipo", "nombre", "combo", "estado", "media_id", "publicado_en", "permalink")}
    r["grupo"] = x.get("grupo") or x.get("padre") or x.get("nombre")
    return r


def subir(fichero, clave):
    """Al almacén R2 (reels-de-tu-marca), de donde Instagram lo descarga."""
    import boto3
    e = ig_api.leer_env()
    c = boto3.client("s3", endpoint_url=f"https://{e['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
                     aws_access_key_id=e["R2_ACCESS_KEY_ID"], aws_secret_access_key=e["R2_SECRET_ACCESS_KEY"],
                     region_name="auto")
    c.upload_file(str(fichero), e["R2_BUCKET"], clave,
                  ExtraArgs={"ContentType": mimetypes.guess_type(str(fichero))[0] or "application/octet-stream"})
    return clave


def portada_jpg(png, clave):
    """Instagram solo acepta la portada en JPEG."""
    if not png or not Path(png).exists():
        return ""
    jpg = Path(tempfile.gettempdir()) / (Path(clave).name)
    subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "90", str(png), "--out", str(jpg)],
                   check=True, capture_output=True)
    subir(jpg, clave)
    jpg.unlink(missing_ok=True)
    return clave


# ---------- la cola ----------
def minutos(cuando):
    return datetime.strptime(cuando, "%Y-%m-%d %H:%M").timestamp() / 60


def choca(cola, cuando, tipo):
    """Nada a menos de 20 min de otra cosa; dos normales, a una hora como poco."""
    malos = []
    for k in cola:
        m = k.get("metadata") or {}
        if m.get("estado") not in ("pendiente", "procesando", "listo", "espera") or not m.get("cuando"):
            continue
        d = abs(minutos(m["cuando"]) - minutos(cuando))
        if d < 20 or (d < 60 and tipo == "normal" and m.get("tipo") == "normal"):
            malos.append(f"{m['nombre']} {m.get('combo', '')} {m['cuando']}".replace("  ", " "))
    return malos


def meter(cola, tipo, nombre, cuando, video, pie, portada, combo="", padre="", espera=False, grupo="", **extra):
    i = uuid.uuid4().hex[:6]
    print(f"⬆️  Subo {Path(video).name} ({Path(video).stat().st_size / 1e6:.0f} MB) a tu almacén…")
    x = {"id": i, "tipo": tipo, "nombre": nombre, "combo": combo, "padre": padre, "cuando": cuando, "pie": pie,
         "estado": "espera" if espera else "pendiente", "intentos": 0, "grupo": grupo or padre or nombre, **extra,
         "video_key": subir(video, f"reels/{nombre}-{i}.mp4"),
         "portada_key": portada_jpg(portada, f"reels/{nombre}-{i}.portada.jpg")}
    poner(f"cola:{i}", x, resumen(x))
    cola.append({"name": f"cola:{i}", "metadata": resumen(x)})
    print(f"🗓  {nombre}{' ' + combo if combo else ''} ({tipo}) → {cuando}  (id {i}){'  · espera su sí' if espera else ''}")
    return x


def pie_de(*candidatos):
    for p in candidatos:
        if p and Path(p).exists():
            return Path(p).read_text(encoding="utf-8").strip()
    return ""


def normal(a):
    v = Path(a.video) if a.video else pg.ultima_version(a.nombre)
    if not v or not v.exists():
        sys.exit(f"No encuentro el vídeo de {a.nombre} (videos/salida/{a.nombre}.mp4).")
    pie = pie_de(a.pie, v.with_suffix(".pie.txt"), pg.SALIDA / f"{a.nombre}.pie.txt", pg.RESULTADOS / f"{a.nombre}.pie.txt")
    if not pie:
        sys.exit(f"Falta el pie: videos/salida/{a.nombre}.pie.txt")
    portada = Path(a.portada) if a.portada else v.with_suffix(".portada.png")
    datetime.strptime(a.cuando, "%Y-%m-%d %H:%M")
    cola = lista("cola:")
    malos = choca(cola, a.cuando, "normal")
    if malos and not a.igual:
        sys.exit(f"⚠️  {a.cuando} choca con {', '.join(malos)}: elige otra hora (o --igual).")
    meter(cola, "normal", a.nombre, a.cuando, v, pie, portada, espera=a.espera, grupo=a.grupo or "",
          tiktok=not a.sin_tiktok)


def pruebas(a):
    cola = lista("cola:")
    combos = a.combos.split(",") if a.combos else []
    padre_pie = pie_de(pg.SALIDA / f"{a.nombre}.pie.txt", pg.RESULTADOS / f"{a.nombre}.pie.txt")
    padre_portada = pg.SALIDA / f"{a.nombre}.portada.png"
    dia = date.fromisoformat(a.desde) if a.desde else pg.ahora().date() + timedelta(days=1)
    for n, f in enumerate(a.ficheros):
        f = Path(f)
        if not f.exists():
            print(f"⚠️  No está {f}: esa prueba no entra.")
            continue
        d = (dia + timedelta(days=n)).isoformat()
        hueco = next((h for h in HUECOS if not choca(cola, f"{d} {h}", "prueba")), None)
        if not hueco:
            print(f"⚠️  El {d} ya no hay hueco libre de 11:00 a 21:00: {f.name} no entra.")
            continue
        portada = f.with_suffix(".portada.png") if f.with_suffix(".portada.png").exists() else padre_portada
        meter(cola, "prueba", f.stem, f"{d} {hueco}", f, pie_de(f.with_suffix(".pie.txt")) or padre_pie, portada,
              combo=combos[n] if n < len(combos) else "", padre=a.nombre, espera=a.espera, grupo=a.grupo or "")


def mix(a):
    """D-2 y D-1: una prueba de cada ángulo. Día D: el reel normal, con el ángulo que gane (lo elige la nube a las 08:00)."""
    d = date.fromisoformat(a.dia)
    angulos = []
    for t in a.angulo:
        et, _, fs = t.partition("=")
        normal_f, _, prueba_f = fs.partition(",")
        for f in (normal_f, prueba_f):
            if not Path(f).exists():
                sys.exit(f"No encuentro {f}")
        angulos.append((et.strip(), Path(normal_f), Path(prueba_f)))
    if len(angulos) < 2:
        sys.exit("El mix necesita 2 ángulos como poco.")
    cuando = f"{a.dia} {a.hora}"
    cola = lista("cola:")
    if choca(cola, cuando, "normal") and not a.igual:
        sys.exit(f"⚠️  {cuando} choca con {', '.join(choca(cola, cuando, 'normal'))}")
    pie_padre = pie_de(pg.SALIDA / f"{a.nombre}.pie.txt")
    opciones = []
    for n_, (et, normal_f, prueba_f) in enumerate(angulos):
        dia = (d - timedelta(days=len(angulos) - n_)).isoformat()
        hueco = next((h for h in HUECOS if not choca(cola, f"{dia} {h}", "prueba")), None)
        if not hueco:
            sys.exit(f"⚠️  El {dia} no hay hueco para la prueba del ángulo {et}.")
        p = meter(cola, "prueba", prueba_f.stem, f"{dia} {hueco}", prueba_f,
                  pie_de(prueba_f.with_suffix(".pie.txt")) or pie_padre, prueba_f.with_suffix(".portada.png"),
                  combo=et, padre=a.nombre, espera=a.espera, grupo=a.grupo or "")
        i = uuid.uuid4().hex[:6]
        print(f"⬆️  Subo {normal_f.name} (versión normal del ángulo {et})…")
        opciones.append({"etiqueta": et, "prueba_id": p["id"],
                         "video_key": subir(normal_f, f"reels/{a.nombre}-{et}-{i}.mp4"),
                         "portada_key": portada_jpg(normal_f.with_suffix(".portada.png"), f"reels/{a.nombre}-{et}-{i}.portada.jpg"),
                         "pie": pie_de(normal_f.with_suffix(".pie.txt")) or pie_padre})
    o = opciones[0]
    i = uuid.uuid4().hex[:6]
    x = {"id": i, "tipo": "normal", "nombre": a.nombre, "combo": "", "padre": "", "cuando": cuando, "pie": o["pie"],
         "estado": "espera" if a.espera else "pendiente", "intentos": 0, "grupo": a.grupo or a.nombre, "tiktok": True,
         "video_key": o["video_key"], "portada_key": o["portada_key"], "opciones": opciones}
    poner(f"cola:{i}", x, resumen(x))
    print(f"🗓  {a.nombre} (normal, mix {'/'.join(x['etiqueta'] for x in opciones)}) → {cuando}  (id {i})"
          f"{'  · espera su sí' if a.espera else ''}")


def decide(a, si):
    n_ = 0
    for k in lista("cola:"):
        m = k.get("metadata") or {}
        if m.get("estado") == "espera" and (m.get("grupo") or m.get("nombre")) == a.nombre:
            x = leer(k["name"])
            x["estado"] = "pendiente" if si else "rechazado"
            poner(k["name"], x, resumen(x))
            n_ += 1
    print(f"{'👍' if si else '👌'} {a.nombre}: {n_} {'entran en la cola' if si else 'quedan parados'}.")


def tiktok(a):
    """Apunta lo programado en TikTok Studio (desde su Chrome), para que salga en el parte."""
    datetime.strptime(a.cuando, "%Y-%m-%d %H:%M")
    i = uuid.uuid4().hex[:6]
    poner(f"tk:{i}", {"id": i, "nombre": a.nombre, "cuando": a.cuando}, {"nombre": a.nombre, "cuando": a.cuando})
    print(f"🎵 Apuntado en el parte: {a.nombre} en TikTok el {a.cuando} (id {i})")


def ver(_a=None):
    cola = sorted(lista("cola:"), key=lambda k: (k.get("metadata") or {}).get("cuando", ""))
    if not cola:
        print("La cola está vacía.")
    for k in cola:
        m = k.get("metadata") or {}
        print(f"{k['name'][5:]}  {m.get('cuando')}  {m.get('tipo'):7} {m.get('estado'):10} {m.get('nombre')} {m.get('combo', '')}")


def quitar(a):
    wr("kv", "key", "delete", f"cola:{a.id}", "--binding", "KV", "--remote")
    print(f"Quitado {a.id} de la cola.")


def apunta(a):
    tareas = leer("tareas") or []
    tareas.append({"id": uuid.uuid4().hex[:6], "texto": a.texto, "quien": a.quien,
                   "para": a.para or pg.ahora().date().isoformat(), "hecha": False})
    poner("tareas", tareas)
    print(f"Apuntado para {a.quien}: {a.texto}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="orden", required=True)
    q = s.add_parser("normal"); q.add_argument("nombre"); q.add_argument("--cuando", required=True)
    q.add_argument("--video"); q.add_argument("--pie"); q.add_argument("--portada")
    q.add_argument("--igual", action="store_true"); q.add_argument("--espera", action="store_true")
    q.add_argument("--sin-tiktok", action="store_true"); q.add_argument("--grupo"); q.set_defaults(f=normal)
    q = s.add_parser("pruebas"); q.add_argument("nombre"); q.add_argument("ficheros", nargs="+")
    q.add_argument("--desde"); q.add_argument("--combos"); q.add_argument("--grupo"); q.add_argument("--espera", action="store_true")
    q.set_defaults(f=pruebas)
    q = s.add_parser("mix"); q.add_argument("nombre"); q.add_argument("--grupo"); q.add_argument("--dia", required=True)
    q.add_argument("--hora", default="20:30"); q.add_argument("--angulo", action="append", required=True)
    q.add_argument("--espera", action="store_true"); q.add_argument("--igual", action="store_true"); q.set_defaults(f=mix)
    q = s.add_parser("si"); q.add_argument("nombre"); q.set_defaults(f=lambda a: decide(a, True))
    q = s.add_parser("no"); q.add_argument("nombre"); q.set_defaults(f=lambda a: decide(a, False))
    q = s.add_parser("pide"); q.add_argument("grupo")
    q.set_defaults(f=lambda a: (poner(f"pide:{a.grupo}", 1), print(f"📨 En ≤5 min te llega a Telegram {a.grupo} entera, con su botón.")))
    q = s.add_parser("tiktok"); q.add_argument("nombre"); q.add_argument("--cuando", required=True); q.set_defaults(f=tiktok)
    s.add_parser("ver").set_defaults(f=ver)
    q = s.add_parser("quitar"); q.add_argument("id"); q.set_defaults(f=quitar)
    q = s.add_parser("apunta"); q.add_argument("texto")
    q.add_argument("--quien", default="tu", choices=["tu", "claude"]); q.add_argument("--para")
    q.set_defaults(f=apunta)
    s.add_parser("parte").set_defaults(f=lambda a: (poner("orden:parte", 1), print("El parte sale en ≤5 min por Telegram.")))
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
