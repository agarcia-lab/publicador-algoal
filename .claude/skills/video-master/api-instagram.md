# Conectar Instagram por la API oficial y montar el programador

Una vez. Hazle **una pregunta cada vez** y espera la respuesta. Requisitos: cuenta de Instagram profesional (creador o
empresa) y pública, una cuenta gratis en developers.facebook.com, y un sitio con enlace público para los vídeos.

## 1. La app de Meta y la llave (10 min, la hace él; tú le guías)

1. developers.facebook.com → Mis aplicaciones → **Crear aplicación** → caso de uso **Otro** → tipo **Empresa**.
2. Añadir el producto **Instagram** → **Configuración de la API con inicio de sesión de Instagram**.
3. Añadir su cuenta de Instagram → **Generar token** → iniciar sesión → copiar la llave.
4. `cp env.ejemplo .env` y que pegue la llave **él, en el fichero** (`IG_TOKEN=…`) y la fecha de hoy en
   `IG_TOKEN_FECHA`. 🚨 Nunca en el chat. Si la pega en el chat, dile que la regenere.

No hace falta que Meta revise la app: la cuenta es suya, así que ya puede publicar, subir pruebas y leer sus números.

## 2. El almacén de vídeos (Cloudflare R2, gratis hasta 10 GB)

1. dash.cloudflare.com → R2 → **Crear bucket** (p. ej. `reels-mi-marca`).
2. En el bucket: Ajustes → **Acceso público → r2.dev** → activar. Esa URL va en `R2_PUBLIC_URL`.
3. R2 → **Gestionar tokens de API** → crear con permiso de lectura y escritura sobre ese bucket → `R2_ACCESS_KEY_ID`,
   `R2_SECRET_ACCESS_KEY` y el ID de cuenta en `R2_ACCOUNT_ID`.
4. `herramientas/.venv/bin/pip install boto3`

## 3. Comprobar (2 min)

```bash
herramientas/.venv/bin/python herramientas/ig_api.py comprobar   # llave, ID y almacén en verde
herramientas/.venv/bin/python herramientas/ig_api.py publicar "Editor IA/Resultados/<un-video>_editado_v1.mp4" \
  --pie "Editor IA/Resultados/<un-video>.pie.txt" --prueba                  # una prueba real: solo la ve quien no le sigue
```

Con su sí antes de la prueba real. Si sale bien: «Tu primera prueba está publicada, solo la ve quien no te sigue: <enlace>».

## 4. El programador (cada 10 min, aunque cierre Claude)

```bash
herramientas/instalar-programador.sh
```

- 🚨 Si el repo está en Descargas, Escritorio o Documentos, macOS lo bloquea en segundo plano: el script avisa.
  Muévelo a `~/la-formula` (o `~/Proyectos/la-formula`) antes.
- El Mac tiene que estar encendido a la hora de publicar. Para que despierte antes del parte:
  `sudo pmset repeat wakeorpoweron MTWRFSU 07:25:00` (lo ejecuta él).
- Si estaba dormido más de 6 h desde la hora prevista, no publica a ciegas: lo marca «atrasado» y sale en el parte.

## Si algo falla

| Lo que ves | Qué pasa | Qué haces |
|---|---|---|
| Contenedor en `ERROR` | El vídeo no cumple: MP4/MOV, H.264, AAC, 9:16, de 3 s a 15 min, < 300 MB | El export de la guía (MP4 1080×1920) ya cumple; si es otro, recodifícalo |
| Error al crear el contenedor | El enlace no es público o no apunta al .mp4 directo | Abre el enlace en una ventana privada |
| `EXPIRED` | 24 h entre preparar y publicar | Se vuelve a crear solo en la siguiente vuelta |
| Error de llave (código 190) | Caducó (60 días) | Nueva en el panel de Meta → `.env`, y `IG_TOKEN_FECHA` de hoy |
| Una métrica no sale | Esa cuenta o ese vídeo no la tiene aún | `ig_api` la salta y sigue con las demás |
| Muchas publicaciones | Límite de 100 por API cada 24 h | De sobra: 1 normal + 1 prueba al día |

## Lo siguiente (cuando lo pida)

Contestar solo a los comentarios con la palabra («comenta X y te lo mando») por la API sí pide que Meta revise la app
(permisos de comentarios y mensajes). Hasta entonces, la automatización de Meta Business Suite o ManyChat.
