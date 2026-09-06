#!/bin/sh
# Removes everything install.sh created. Safe to run even if some files
# are already gone.
DESKTOP="${XDG_CURRENT_DESKTOP:-XFCE}"
DESKTOP_LOWER=$(echo "$DESKTOP" | tr '[:upper:]' '[:lower:]' | cut -d: -f1)

rm -f "$HOME/.local/share/xdg-desktop-portal/portals/x11.portal"
rm -f "$HOME/.local/share/dbus-1/services/org.freedesktop.impl.portal.desktop.x11.service"

CONFIG_FILE="$HOME/.config/xdg-desktop-portal/${DESKTOP_LOWER}-portals.conf"
if [ -f "$CONFIG_FILE" ]; then
    sed -i '/org.freedesktop.impl.portal.ScreenCast=x11/d' "$CONFIG_FILE"
fi

pkill -f portal_x11.py 2>/dev/null || true
systemctl --user restart xdg-desktop-portal.service 2>/dev/null || true

echo "Desinstalado."
