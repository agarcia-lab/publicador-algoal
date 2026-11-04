"""Instagram API con inicio de sesión de Instagram (graph.instagram.com, oficial y gratis).

Publica reels normales y Reels de prueba (solo los ve quien no te sigue), lee sus números y renueva la llave.
La llave vive en .env (IG_TOKEN): nunca en el chat ni en git.

  ig_api.py yo                                   tu ID y usuario (lo guarda en .env como IG_USER_ID)
  ig_api.py publicar VIDEO.mp4 [--pie F] [--portada F.png] [--prueba]
  ig_api.py insights MEDIA_ID                    vistas, alcance, guardados, compartidos, % que se va en 3 s, s de media
  ig_api.py renovar                              llave nueva (dura 60 días; el programador la renueva a los 50)
  ig_api.py comprobar                            llave, ID y almacén de vídeos: todo en verde o qué falta

La API no programa: publica en el momento de la llamada. Programar lo hace herramientas/programador.py.
Instagram descarga el vídeo de un enlace público: se sube antes a Cloudflare R2 (o S3) con las claves de .env.
"""
import argparse, json, mimetypes, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ENV = RAIZ / ".env"
VERSION = os.environ.get("IG_VERSION", "v23.0")
HOST = f"https://graph.instagram.com/{VERSION}"
METRICAS = ["views", "reach", "saved", "shares", "ig_reels_avg_watch_time", "reels_skip_rate"]


# ---------- .env ----------
def leer_env():
    datos = {}
    if ENV.exists():
        for linea in ENV.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                v = v.strip()
                v = "" if v.startswith("#") else v.split(" #")[0].strip()   # comentarios al final de la línea
                datos[k.strip()] = v.strip('"').strip("'")
    return {**datos, **{k: v for k, v in os.environ.items() if k in datos or k.startswith(("IG_", "R2_", "S3_"))}}


def guardar_env(**cambios):
    """Cambia o añade claves en .env sin tocar las demás."""
    lineas = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    hechas = set()
    for i, linea in enumerate(lineas):
        k = linea.split("=", 1)[0].strip()
        if k in cambios:
            lineas[i], _ = f"{k}={cambios[k]}", hechas.add(k)
    lineas += [f"{k}={v}" for k, v in cambios.items() if k not in hechas]
    ENV.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    os.chmod(ENV, 0o600)


def env(clave, obligatoria=True):
    v = leer_env().get(clave, "")
    if obligatoria and not v:
        sys.exit(f"Falta {clave} en .env (mira env.ejemplo).")
    return v


# ---------- llamadas ----------
class ErrorIG(Exception):
    pass


def pedir(metodo, url, datos=None):
    datos = {**(datos or {}), "access_token": env("IG_TOKEN")}
    cuerpo = urllib.parse.urlencode(datos).encode()
    if metodo == "GET":
        req = urllib.request.Request(f"{url}?{cuerpo.decode()}")
    else:
        req = urllib.request.Request(url, data=cuerpo, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode()).get("error", {})
        except Exception:
            err = {}
        msg = err.get("error_user_msg") or err.get("message") or str(e)
        if err.get("code") == 190:
            msg += " → la llave ha caducado o no vale: genera otra en el panel de Meta y ponla en .env (IG_TOKEN)."
        raise ErrorIG(msg) from None


def yo():
    r = pedir("GET", f"{HOST}/me", {"fields": "user_id,username"})
    if r.get("user_id") and leer_env().get("IG_USER_ID") != str(r["user_id"]):
        guardar_env(IG_USER_ID=r["user_id"])
    return r


def uid():
    return leer_env().get("IG_USER_ID") or yo()["user_id"]


# ---------- almacén público (R2 / S3) ----------
def enlace_publico(fichero):
    """Sube el fichero y devuelve su enlace público directo. Sin claves de R2/S3, usa VIDEO_URL_BASE (una carpeta
    que tú ya sincronizas con una web pública) + el nombre del fichero."""
    fichero = Path(fichero)
    e = leer_env()
    if e.get("R2_BUCKET"):
        try:
            import boto3
        except ImportError:
            sys.exit("Falta boto3: herramientas/.venv/bin/pip install boto3")
        cliente = boto3.client(
            "s3",
            endpoint_url=e.get("R2_ENDPOINT") or f"https://{e['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
            aws_access_key_id=e["R2_ACCESS_KEY_ID"], aws_secret_access_key=e["R2_SECRET_ACCESS_KEY"],
            region_name=e.get("R2_REGION", "auto"))
        clave = f"reels/{fichero.name}"
        tipo = mimetypes.guess_type(fichero.name)[0] or "application/octet-stream"
        cliente.upload_file(str(fichero), e["R2_BUCKET"], clave, ExtraArgs={"ContentType": tipo})
        return f"{e['R2_PUBLIC_URL'].rstrip('/')}/{urllib.parse.quote(clave)}"
    if e.get("VIDEO_URL_BASE"):
        return f"{e['VIDEO_URL_BASE'].rstrip('/')}/{urllib.parse.quote(fichero.name)}"
    sys.exit("No hay dónde dejar el vídeo con enlace público: rellena R2_* (o VIDEO_URL_BASE) en .env.")


# ---------- publicar ----------
def crear_contenedor(video_url, pie, prueba=False, portada_url=None):
    datos = {"media_type": "REELS", "video_url": video_url, "caption": pie,
             "share_to_feed": "false" if prueba else "true"}
    if prueba:
        datos["trial_params"] = json.dumps({"graduation_strategy": "MANUAL"})
    if portada_url:
        datos["cover_url"] = portada_url
    return pedir("POST", f"{HOST}/{uid()}/media", datos)["id"]


def esperar(contenedor, limite=900):
    inicio = time.time()
    while time.time() - inicio < limite:
        r = pedir("GET", f"{HOST}/{contenedor}", {"fields": "status_code,status"})
        estado = r.get("status_code")
        if estado == "FINISHED":
            return
        if estado in ("ERROR", "EXPIRED"):
            pista = ("EXPIRED: pasaron 24 h entre preparar y publicar; se vuelve a crear." if estado == "EXPIRED" else
                     "ERROR: el vídeo no cumple (MP4/MOV, H.264, audio AAC, 9:16, de 3 s a 15 min, < 300 MB).")
            raise ErrorIG(f"{pista} Detalle: {r.get('status', '')}")
        time.sleep(15)
    raise ErrorIG("Instagram lleva 15 min procesando: lo reintento en la próxima vuelta.")


def publicar(video, pie_txt=None, portada=None, prueba=False):
    """Todo el camino: subir al almacén → contenedor → esperar → publicar. Devuelve media_id y enlace."""
    video = Path(video)
    pie = Path(pie_txt).read_text(encoding="utf-8").strip() if pie_txt and Path(pie_txt).exists() else ""
    url = enlace_publico(video)
    portada_url = enlace_publico(portada) if portada and Path(portada).exists() else None
    contenedor = crear_contenedor(url, pie, prueba, portada_url)
    esperar(contenedor)
    media = pedir("POST", f"{HOST}/{uid()}/media_publish", {"creation_id": contenedor})["id"]
    try:
        enlace = pedir("GET", f"{HOST}/{media}", {"fields": "permalink"}).get("permalink", "")
    except ErrorIG:
        enlace = ""
    return {"media_id": media, "permalink": enlace, "contenedor": contenedor}


# ---------- números ----------
def insights(media_id):
    """Las métricas que existan para ese vídeo. Si una no está disponible, se piden de una en una y se salta."""
    def leer(lista):
        r = pedir("GET", f"{HOST}/{media_id}/insights", {"metric": ",".join(lista)})
        return {m["name"]: (m.get("values") or [{}])[0].get("value", m.get("total_value", {}).get("value"))
                for m in r.get("data", [])}
    try:
        datos = leer(METRICAS)
    except ErrorIG:
        datos = {}
        for m in METRICAS:
            try:
                datos.update(leer([m]))
            except ErrorIG:
                pass
    if datos.get("ig_reels_avg_watch_time") is not None:
        datos["seg_media"] = round(datos["ig_reels_avg_watch_time"] / 1000, 1)   # viene en milisegundos
    return datos


# ---------- llave ----------
def renovar():
    url = "https://graph.instagram.com/refresh_access_token"
    r = pedir("GET", url, {"grant_type": "ig_refresh_token"})
    guardar_env(IG_TOKEN=r["access_token"], IG_TOKEN_FECHA=date.today().isoformat())
    return {"dias": round(r.get("expires_in", 0) / 86400)}


def dias_de_llave():
    f = leer_env().get("IG_TOKEN_FECHA")
    return (date.today() - date.fromisoformat(f)).days if f else None


def comprobar():
    ok = True
    try:
        r = yo()
        print(f"✅ Llave válida: @{r.get('username')} (ID {r.get('user_id')})")
    except ErrorIG as e:
        ok = False
        print(f"❌ Llave: {e}")
    d = dias_de_llave()
    print(f"{'✅' if d is not None and d < 50 else '⚠️ '} Llave renovada hace {d if d is not None else '?'} días "
          f"(se renueva sola a los 50; caduca a los 60)")
    e = leer_env()
    if e.get("R2_BUCKET") or e.get("VIDEO_URL_BASE"):
        print("✅ Almacén de vídeos configurado")
    else:
        ok = False
        print("❌ Falta el almacén de vídeos (R2_* o VIDEO_URL_BASE en .env)")
    return ok


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="orden", required=True)
    sub.add_parser("yo")
    q = sub.add_parser("publicar")
    q.add_argument("video"); q.add_argument("--pie"); q.add_argument("--portada")
    q.add_argument("--prueba", action="store_true", help="Reel de prueba: solo para quien no te sigue")
    q = sub.add_parser("insights"); q.add_argument("media_id")
    sub.add_parser("renovar")
    sub.add_parser("comprobar")
    a = p.parse_args()
    try:
        if a.orden == "yo":
            print(json.dumps(yo(), ensure_ascii=False))
        elif a.orden == "publicar":
            print(json.dumps(publicar(a.video, a.pie, a.portada, a.prueba), ensure_ascii=False))
        elif a.orden == "insights":
            print(json.dumps(insights(a.media_id), ensure_ascii=False))
        elif a.orden == "renovar":
            print(json.dumps(renovar(), ensure_ascii=False))
        elif a.orden == "comprobar":
            sys.exit(0 if comprobar() else 1)
    except ErrorIG as e:
        sys.exit(f"❌ {e}")


if __name__ == "__main__":
    main()
