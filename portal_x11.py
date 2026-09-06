#!/usr/bin/env python3
"""
Backend de xdg-desktop-portal para org.freedesktop.impl.portal.ScreenCast
usando captura X11 (ximagesrc) + PipeWire, para escritorios sin Mutter/KWin
(XFCE, i3, etc).

PROTOTIPO: sin dialogo de permisos (auto-aprueba toda solicitud). Soporta
elegir entre pantalla completa o una ventana especifica, pero esa eleccion
se hace de antemano (`--choose-source`), no durante Start(): ver
prompt_source_choice() para el porque. Pensado para uso personal en una
sola maquina.
"""
import json
import os
import sys
import secrets
import subprocess
import time

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib

import dbus
import dbus.mainloop.glib
import dbus.service

Gst.init(None)
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

BUS_NAME = "org.freedesktop.impl.portal.desktop.x11"
OBJECT_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.impl.portal.ScreenCast"
SESSION_IFACE = "org.freedesktop.impl.portal.Session"
PROPS_IFACE = "org.freedesktop.DBus.Properties"

SOURCE_TYPE_MONITOR = 1
SOURCE_TYPE_WINDOW = 2
CURSOR_MODE_EMBEDDED = 2

STATE_DIR = os.path.expanduser("~/.config/xdg-desktop-portal-x11")
STATE_FILE = os.path.join(STATE_DIR, "source.json")


def log(msg):
    print(f"[x11-portal] {msg}", flush=True)


def list_windows():
    """Ventanas normales (no paneles/escritorio) como [(xid_hex, titulo)]."""
    try:
        out = subprocess.check_output(["wmctrl", "-l"], text=True)
    except Exception:
        return []

    windows = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        wid_hex, desktop, _host, title = parts
        if desktop == "-1":
            # Paneles, escritorio y demas ventanas "pegajosas" sin
            # relacion con lo que el usuario querria compartir.
            continue
        title = title.strip()
        if not title:
            continue
        windows.append((wid_hex, title))
    return windows


def prompt_source_choice():
    """Selector estilo Discord: 'Pantalla completa' o una ventana especifica.

    Muestra el dialogo (bloqueante) y GUARDA la eleccion en STATE_FILE, para
    que Start() la pueda leer despues al instante sin volver a preguntar.
    Pensado para correrse a mano ANTES de darle a "conectar" en la app de
    casting: el handshake RTSP con la TV tiene su propio limite de tiempo, y
    esperar a que el usuario elija en un dialogo durante el propio Start()
    lo hace vencer (visto en la practica: la Roku se desconecta si el
    dialogo tarda mas de unos pocos segundos en cerrarse).
    """
    windows = list_windows()

    rows = ["SCREEN", "Pantalla completa"]
    for wid_hex, title in windows:
        rows += [wid_hex, title]

    try:
        result = subprocess.run(
            ["zenity", "--list",
             "--title=Compartir en Roku",
             "--text=Que quieres transmitir la proxima vez que conectes?",
             "--width=420", "--height=320",
             "--column=id", "--column=Ventana",
             "--hide-column=1", "--print-column=1"] + rows,
            capture_output=True, text=True, timeout=120,
        )
    except Exception as e:
        log(f"selector de ventana fallo: {e}")
        return

    choice = result.stdout.strip()
    if result.returncode != 0 or not choice:
        return  # cancelado, no tocar la eleccion guardada

    xid = None
    if choice != "SCREEN":
        try:
            xid = int(choice, 16)
        except ValueError:
            xid = None

    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump({"xid": xid}, f)
    log(f"eleccion guardada: xid={xid!r}")


def load_source_choice():
    """Lectura instantanea (sin dialogo) de la eleccion guardada por
    prompt_source_choice(). Devuelve None (pantalla completa) si no hay
    eleccion guardada, o si la ventana elegida ya no existe."""
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
    except Exception:
        return None

    xid = data.get("xid")
    if xid is None:
        return None

    xid_hex = f"0x{xid:08x}"
    if not any(wid_hex.lower() == xid_hex.lower() for wid_hex, _title in list_windows()):
        log(f"la ventana elegida ({xid_hex}) ya no existe, usando pantalla completa")
        return None
    return xid


def find_pw_node(node_name, timeout=3.0):
    """Busca en pw-dump el nodo recien creado por nombre, y devuelve (id, serial)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            out = subprocess.check_output(["pw-dump"], text=True)
            data = json.loads(out)
        except Exception:
            time.sleep(0.1)
            continue
        for obj in data:
            if obj.get("type") != "PipeWire:Interface:Node":
                continue
            props = obj.get("info", {}).get("props", {})
            if props.get("node.name") == node_name:
                return obj.get("id"), props.get("object.serial")
        time.sleep(0.1)
    return None, None


class Session(dbus.service.Object):
    def __init__(self, bus, session_handle, service):
        super().__init__(bus, session_handle)
        self.session_handle = session_handle
        self.service = service
        self.pipeline = None
        self.bus_watch_id = None
        self.closed = False

    def set_pipeline(self, pipeline):
        self.pipeline = pipeline
        gst_bus = pipeline.get_bus()
        gst_bus.add_signal_watch()
        self.bus_watch_id = gst_bus.connect("message", self._on_bus_message)

    def _on_bus_message(self, _bus, message):
        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            log(f"pipeline de captura fallo: {err} ({debug}), cerrando sesion")
            self._teardown()

    def _teardown(self):
        if self.closed:
            return
        self.closed = True
        if self.pipeline is not None:
            gst_bus = self.pipeline.get_bus()
            if self.bus_watch_id is not None:
                gst_bus.disconnect(self.bus_watch_id)
                self.bus_watch_id = None
            gst_bus.remove_signal_watch()
            self.pipeline.set_state(Gst.State.NULL)
            # Esperar a que PipeWire de verdad libere el global del nodo
            # antes de seguir, para que un Start() inmediato despues no
            # se tope con un ID reciclado a medio propagar (causaba
            # "invalid global" / "target not found" en el cliente).
            # Recortado a 0.8s (de 2s): el cliente suele reintentar en
            # <1s cuando esto pasa por su propia inestabilidad de red,
            # y queremos poder atenderlo lo antes posible.
            self.pipeline.get_state(int(0.8 * Gst.SECOND))
            self.pipeline = None
        self.service.sessions.pop(self.session_handle, None)
        log(f"sesion cerrada: {self.session_handle}")
        # Avisar al cliente (via la senal estandar del portal) que esta
        # sesion ya no sirve, para que pida una nueva en vez de reintentar
        # contra un stream muerto para siempre.
        self.Closed()
        self.remove_from_connection()

    @dbus.service.signal(SESSION_IFACE)
    def Closed(self):
        pass

    @dbus.service.method(SESSION_IFACE, in_signature="", out_signature="")
    def Close(self):
        log(f"Close() pedido por el cliente: {self.session_handle}")
        self._teardown()

    @dbus.service.method(PROPS_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        if interface == SESSION_IFACE and prop == "version":
            return dbus.UInt32(1)
        raise dbus.exceptions.DBusException(
            "org.freedesktop.DBus.Error.UnknownProperty"
        )

    @dbus.service.method(PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface == SESSION_IFACE:
            return dbus.Dictionary({"version": dbus.UInt32(1)}, signature="sv")
        return dbus.Dictionary({}, signature="sv")


class ScreenCastService(dbus.service.Object):
    def __init__(self, bus):
        super().__init__(bus, OBJECT_PATH)
        self.bus = bus
        self.sessions = {}

    # ---- org.freedesktop.DBus.Properties ----
    @dbus.service.method(PROPS_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        if interface != SCREENCAST_IFACE:
            raise dbus.exceptions.DBusException(
                "org.freedesktop.DBus.Error.UnknownInterface"
            )
        return self.GetAll(interface)[prop]

    @dbus.service.method(PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != SCREENCAST_IFACE:
            return dbus.Dictionary({}, signature="sv")
        return dbus.Dictionary(
            {
                "AvailableSourceTypes": dbus.UInt32(SOURCE_TYPE_MONITOR | SOURCE_TYPE_WINDOW),
                "AvailableCursorModes": dbus.UInt32(CURSOR_MODE_EMBEDDED),
                "version": dbus.UInt32(4),
            },
            signature="sv",
        )

    # ---- org.freedesktop.impl.portal.ScreenCast ----
    @dbus.service.method(
        SCREENCAST_IFACE, in_signature="oosa{sv}", out_signature="ua{sv}"
    )
    def CreateSession(self, handle, session_handle, app_id, options):
        log(f"CreateSession app_id={app_id!r} session_handle={session_handle}")
        session = Session(self.bus, session_handle, self)
        self.sessions[session_handle] = session
        return (0, dbus.Dictionary({}, signature="sv"))

    @dbus.service.method(
        SCREENCAST_IFACE, in_signature="oosa{sv}", out_signature="ua{sv}"
    )
    def SelectSources(self, handle, session_handle, app_id, options):
        log(f"SelectSources session_handle={session_handle} options={dict(options)}")
        if session_handle not in self.sessions:
            log("SelectSources: sesion desconocida")
            return (2, dbus.Dictionary({}, signature="sv"))
        # 'types'/'multiple'/'cursor_mode' del caller se ignoran: la
        # eleccion real (pantalla completa vs. ventana) se hace en Start()
        # con nuestro propio selector (ver pick_source()), no aqui.
        return (0, dbus.Dictionary({}, signature="sv"))

    @dbus.service.method(
        SCREENCAST_IFACE, in_signature="oossa{sv}", out_signature="ua{sv}"
    )
    def Start(self, handle, session_handle, app_id, parent_window, options):
        log(f"Start session_handle={session_handle}")
        session = self.sessions.get(session_handle)
        if session is None:
            log("Start: sesion desconocida")
            return (2, dbus.Dictionary({}, signature="sv"))

        xid = load_source_choice()
        if xid is not None:
            log(f"capturando ventana especifica: xid={xid:#x}")
            source_elem = f"ximagesrc xid={xid} use-damage=0 show-pointer=1 ! "
        else:
            source_elem = "ximagesrc use-damage=0 show-pointer=1 ! "

        node_name = "x11-portal-" + secrets.token_hex(4)
        pipeline_desc = (
            source_elem +
            "video/x-raw,framerate=30/1 ! videoconvert ! "
            # leaky=downstream + max-size-buffers=1: nunca dejar que se
            # acumulen frames viejos con referencias colgadas, que era lo
            # que disparaba "gst_buffer_remove_memory_range: assertion
            # gst_buffer_is_writable failed" dentro de pipewiresink y
            # tumbaba el pipeline sin que nadie mas se enterara.
            # (Se probo max-size-buffers=3 para el cursor desaparecido en
            # movimiento, pero causo mas caidas de conexion; revertido.)
            "queue max-size-buffers=1 leaky=downstream ! "
            f"pipewiresink client-name={node_name} mode=provide use-bufferpool=false "
            f'stream-properties="props,media.class=(string)Video/Source,'
            f'node.autoconnect=(boolean)false,node.name=(string){node_name}"'
        )
        log(f"pipeline: {pipeline_desc}")
        try:
            pipeline = Gst.parse_launch(pipeline_desc)
        except GLib.Error as e:
            log(f"error armando el pipeline: {e}")
            return (2, dbus.Dictionary({}, signature="sv"))

        pipeline.set_state(Gst.State.PLAYING)
        ret, state, _pending = pipeline.get_state(5 * Gst.SECOND)
        if ret == Gst.StateChangeReturn.FAILURE or state != Gst.State.PLAYING:
            log(f"el pipeline no llego a PLAYING (ret={ret}, state={state}), abortando")
            pipeline.set_state(Gst.State.NULL)
            pipeline.get_state(2 * Gst.SECOND)
            return (2, dbus.Dictionary({}, signature="sv"))
        session.set_pipeline(pipeline)

        node_id, serial = find_pw_node(node_name)
        if node_id is None:
            log("no aparecio el nodo en pipewire a tiempo, abortando")
            pipeline.set_state(Gst.State.NULL)
            pipeline.get_state(2 * Gst.SECOND)
            session.set_pipeline(None)
            return (2, dbus.Dictionary({}, signature="sv"))

        log(f"nodo pipewire listo: id={node_id} serial={serial}")
        # Dar tiempo a que el registro de PipeWire propague el nuevo global
        # a todos los clientes (incluyendo al que va a consumir este stream)
        # antes de anunciarlo como listo.
        time.sleep(0.3)

        stream_props = {
            "position": (dbus.Int32(0), dbus.Int32(0)),
            "source_type": dbus.UInt32(
                SOURCE_TYPE_WINDOW if xid is not None else SOURCE_TYPE_MONITOR
            ),
        }
        if serial is not None:
            stream_props["pipewire-serial"] = dbus.UInt64(int(serial))

        streams = dbus.Array(
            [(dbus.UInt32(int(node_id)), dbus.Dictionary(stream_props, signature="sv"))],
            signature="(ua{sv})",
        )
        results = dbus.Dictionary({"streams": streams}, signature="sv")
        return (0, results)


def main():
    bus = dbus.SessionBus()
    bus_name = dbus.service.BusName(BUS_NAME, bus)  # noqa: F841 (mantener viva la referencia)
    ScreenCastService(bus)
    log(f"corriendo como {BUS_NAME} en {OBJECT_PATH}")
    GLib.MainLoop().run()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--choose-source":
        # No toca D-Bus ni GStreamer: solo actualiza STATE_FILE para que el
        # servicio (corriendo aparte, activado por D-Bus) lo lea en el
        # siguiente Start().
        prompt_source_choice()
    else:
        main()
