# nube/: el publicador en Cloudflare

Publica en Instagram aunque tu Mac esté apagado. Es gratis (plan gratuito de Cloudflare).

## Qué necesitas
- Cuenta de Cloudflare, y en el Mac: `npx wrangler login`.
- Llave de Instagram (IG_TOKEN) y tu ID (IG_USER_ID), según api-instagram.md.
- Un bot de Telegram (con @BotFather) para los avisos: TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID.

## Pasos (desde la carpeta nube/)
1. Crea la cola (KV): `npx wrangler kv namespace create KV` → copia el id en wrangler.toml (REEMPLAZAR-ID-KV).
2. Crea el almacén de vídeos (R2): `npx wrangler r2 bucket create reels-de-tu-marca`, y en el panel: R2 → tu bucket → Ajustes → Acceso público (r2.dev). Esa URL va en R2_PUBLIC_URL de wrangler.toml.
3. Tu ID de Instagram: `python3 ../herramientas/ig_api.py yo` → ponlo en IG_USER_ID de wrangler.toml.
4. Secretos (te los pide en la terminal, no van al repo):
   `npx wrangler secret put IG_TOKEN`
   `npx wrangler secret put TELEGRAM_BOT_TOKEN`
   `npx wrangler secret put TELEGRAM_CHAT_ID`
5. Despliegue: `npx wrangler deploy`.
6. Prueba con MODO = "prueba" (no publica, solo te avisa). Cuando lo veas bien, cambia a MODO = "real" y vuelve a desplegar.

## Desde tu Mac
Pon en `.env` las claves R2 (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET, R2_PUBLIC_URL). Con eso, `herramientas/nube` sube los vídeos a R2 y mete la cola.

## Comandos
Mira `herramientas/nube` (sin argumentos) para la lista completa.
