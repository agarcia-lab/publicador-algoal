#!/usr/bin/env bash
# Deja el programador corriendo cada 10 minutos en tu Mac (launchd), aunque cierres Claude.
# Publica lo que toca del calendario, lee los números a las 07:30 y te manda el parte a las 08:00.
#   herramientas/instalar-programador.sh          instala (o reinstala)
#   herramientas/instalar-programador.sh quitar   lo para y lo borra
set -euo pipefail
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; raiz="$(cd "$aqui/.." && pwd)"
etiqueta="com.laformula.videomaster"; plist="$HOME/Library/LaunchAgents/$etiqueta.plist"

if [ "${1:-}" = "quitar" ]; then
  launchctl bootout "gui/$(id -u)/$etiqueta" 2>/dev/null || true; rm -f "$plist"; echo "Programador quitado."; exit 0
fi
[ "$(uname)" = "Darwin" ] || { echo "Esto es para Mac. En Linux: crontab -e →  */10 * * * * $aqui/programar tick"; exit 1; }

# macOS no deja a los procesos de fondo leer Descargas, Escritorio ni Documentos sin permiso: mejor fuera de ahí
case "$raiz" in
  "$HOME/Downloads"*|"$HOME/Desktop"*|"$HOME/Documents"*)
    echo "⚠️  El repo está en $raiz. macOS bloquea ahí a los procesos de fondo."
    echo "   Muévelo, por ejemplo:  mv \"$raiz\" ~/la-formula  y vuelve a lanzar este script desde allí."
    echo "   (O da «Acceso total al disco» a /bin/bash en Ajustes → Privacidad y seguridad.)"; exit 1;;
esac
[ -f "$raiz/.env" ] || { echo "Falta $raiz/.env: copia env.ejemplo y rellena IG_TOKEN y el almacén."; exit 1; }
chmod +x "$aqui/programar"; mkdir -p "$raiz/calendario" "$HOME/Library/LaunchAgents"

cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$etiqueta</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$aqui/programar</string><string>tick</string></array>
  <key>WorkingDirectory</key><string>$raiz</string>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$raiz/calendario/programador.log</string>
  <key>StandardErrorPath</key><string>$raiz/calendario/programador.log</string>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)/$etiqueta" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist"
echo "✅ Programador en marcha: cada 10 min. Registro: calendario/programador.log"
echo "   Para que publique a su hora, el Mac tiene que estar encendido (vale con la tapa abierta o «evitar reposo»)."
echo "   Despertarlo solo antes del parte:  sudo pmset repeat wakeorpoweron MTWRFSU 07:25:00"
