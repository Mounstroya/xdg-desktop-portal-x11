# xdg-desktop-portal-x11

A minimal `org.freedesktop.impl.portal.ScreenCast` backend for **X11 desktops
that have no compositor-level screen sharing support** - XFCE, i3, Openbox,
and similar. GNOME (Mutter) and KDE (KWin) already implement this portal
themselves; window managers built on plain X11 generally don't, which means
apps built against the portal API (GNOME Network Displays, browsers doing
WebRTC screen share, OBS's "Portal" capture source, etc.) simply fail with
`org.freedesktop.portal.ScreenCast no existe` / "no ScreenCast implementation
available" on those desktops.

This project exists because that gap has no lightweight fix: the only other
portal backends that implement ScreenCast are `xdg-desktop-portal-gnome` and
`xdg-desktop-portal-kde`, both of which require running (part of) that whole
desktop environment. On X11 that's unnecessary - unlike Wayland, X11 doesn't
sandbox window contents, so any client can grab the screen directly via
`XShm`/`XComposite` without compositor cooperation. This backend does exactly
that: it captures the desktop with GStreamer's `ximagesrc` and republishes it
as a PipeWire `Video/Source` node, which is what portal-aware apps expect to
receive back from `Start()`.

## What it does and doesn't do

- Captures the whole X11 screen (single monitor / no window picker) as
  `Video/Source` over PipeWire, with the pointer drawn in.
- No permission dialog: every `CreateSession`/`Start` request is
  auto-approved. This is meant for a single-user personal machine, not a
  multi-user or sandboxed (Flatpak) setup.
- Recovers from an internal pipeline failure by tearing the session down and
  emitting the standard `Session.Closed` signal, so well-behaved clients
  (GNOME Network Displays included) request a fresh session instead of
  retrying against a dead stream forever.
- Does not implement window/region selection, multiple simultaneous
  sources, or audio capture (that's a separate portal interface).

## Install

```sh
git clone <this-repo>
cd xdg-desktop-portal-x11
./install.sh
```

This only touches `~/.local/share` and `~/.config` - no root, no system
files. It registers the backend for whatever desktop is in
`$XDG_CURRENT_DESKTOP` at install time (defaults to XFCE if unset).

Requires: `python3-dbus`, `python3-gi` (PyGObject), GStreamer with
`ximagesrc` (`gstreamer1.0-plugins-good`) and `gstreamer1.0-pipewire`.

## Uninstall

```sh
./uninstall.sh
```

## Testing without a real portal-aware app

```sh
python3 tools/test_client.py
```

Calls `CreateSession`/`SelectSources`/`Start`/`Close` directly over D-Bus and
prints the resulting PipeWire node id/serial, holding the stream open for a
few seconds so you can inspect it with `pw-cli ls Node` or `pw-top` from
another terminal.

`tools/extract_ts.py` is a small diagnostic script used while debugging
audio/video sync issues in an app consuming this portal - given a pcap of the
RTP traffic it reconstructs the raw elementary stream so it can be inspected
with `ffprobe`. Not needed for normal use.

## Why this exists / backstory

Written while trying to get Wi-Fi Display (Miracast) casting working from
XFCE via [GNOME Network Displays](https://gitlab.gnome.org/GNOME/gnome-network-displays),
which - like most portal-aware screen-casting apps - has no fallback for
desktops without a real portal backend. See
[this fork's `xfce-roku-audio-fixes` branch](https://github.com/Mounstroya/gnome-network-displays/tree/xfce-roku-audio-fixes)
for the patches that came out of getting the actual casting session working
end-to-end (audio included) once this portal was in place.
