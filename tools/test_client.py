#!/usr/bin/env python3
"""Cliente de prueba: simula las llamadas que haria xdg-desktop-portal
contra nuestro backend, sin pasar por el portal real todavia."""
import time
import dbus

BUS_NAME = "org.freedesktop.impl.portal.desktop.x11"
OBJECT_PATH = "/org/freedesktop/portal/desktop"
IFACE = "org.freedesktop.impl.portal.ScreenCast"

bus = dbus.SessionBus()
obj = bus.get_object(BUS_NAME, OBJECT_PATH)
iface = dbus.Interface(obj, IFACE)
props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")

print("Propiedades:", dict(props.GetAll(IFACE)))

handle = "/org/freedesktop/portal/desktop/request/1/t1"
session_handle = "/org/freedesktop/portal/desktop/session/1/s1"
app_id = "test-client"

resp, results = iface.CreateSession(handle, session_handle, app_id, {})
print("CreateSession ->", resp, dict(results))
assert resp == 0

resp, results = iface.SelectSources(handle, session_handle, app_id, {})
print("SelectSources ->", resp, dict(results))
assert resp == 0

resp, results = iface.Start(handle, session_handle, app_id, "", {})
print("Start ->", resp, dict(results))
assert resp == 0

print("Manteniendo el stream vivo 8s para que lo revisemos con pw-cli...")
time.sleep(8)

session_obj = bus.get_object(BUS_NAME, session_handle)
session_iface = dbus.Interface(session_obj, "org.freedesktop.impl.portal.Session")
session_iface.Close()
print("Close() enviado, listo.")
