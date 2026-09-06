#!/bin/sh
# Installs the x11 ScreenCast portal backend for the current user only.
# No root required, no system files touched - everything lives under
# ~/.local, ~/.config and is trivially removable with uninstall.sh.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DESKTOP="${XDG_CURRENT_DESKTOP:-XFCE}"
DESKTOP_LOWER=$(echo "$DESKTOP" | tr '[:upper:]' '[:lower:]' | cut -d: -f1)

PORTAL_DIR="$HOME/.local/share/xdg-desktop-portal/portals"
SERVICE_DIR="$HOME/.local/share/dbus-1/services"
CONFIG_DIR="$HOME/.config/xdg-desktop-portal"

mkdir -p "$PORTAL_DIR" "$SERVICE_DIR" "$CONFIG_DIR"

cat > "$PORTAL_DIR/x11.portal" <<EOF
[portal]
DBusName=org.freedesktop.impl.portal.desktop.x11
Interfaces=org.freedesktop.impl.portal.ScreenCast
UseIn=$DESKTOP_LOWER
EOF

cat > "$SERVICE_DIR/org.freedesktop.impl.portal.desktop.x11.service" <<EOF
[D-BUS Service]
Name=org.freedesktop.impl.portal.desktop.x11
Exec=/usr/bin/python3 $SCRIPT_DIR/portal_x11.py
EOF

CONFIG_FILE="$CONFIG_DIR/${DESKTOP_LOWER}-portals.conf"
if [ -f "$CONFIG_FILE" ] && ! grep -q ScreenCast "$CONFIG_FILE"; then
    echo "org.freedesktop.impl.portal.ScreenCast=x11" >> "$CONFIG_FILE"
else
    cat > "$CONFIG_FILE" <<EOF
[preferred]
org.freedesktop.impl.portal.ScreenCast=x11
EOF
fi

echo "Instalado para el escritorio: $DESKTOP_LOWER ($CONFIG_FILE)"
echo "Reiniciando xdg-desktop-portal..."
systemctl --user restart xdg-desktop-portal.service 2>/dev/null || true

echo "Listo. Prueba con: python3 $SCRIPT_DIR/tools/test_client.py"
