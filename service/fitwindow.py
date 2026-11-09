#!/usr/bin/python3 -I
"""fitwindow: D-Bus activated helper for the "Fit Window to Content" KWin script.

The KWin script calls org.kde.FitWindow.Fit(...) with the active window's id, pid, caption, frame geometry and
the usable screen area. We then loop:
  1. find that window's accessible frame (AT-SPI, matched by pid + caption) and its main scrollbars,
  2. if a scrollbar still has range (content overflows), compute a bigger size and apply it through a one-shot
     KWin script,
  3. wait for the app to relayout and measure again,
until no scrollbar has range, the window fills the screen area in that direction, or growing stops helping.

Scrollbar ranges are in pixels for most Qt views, but some views count lines or items. So the first step assumes
pixels, and later steps scale by how much the range actually shrank per pixel grown (a secant step).

Only ever grows the window: an app gives no "natural size", so the smallest size without scrollbars can't be known.
"""
import json
import os
import sys
import tempfile
import time

import gi
gi.require_version('Atspi', '2.0')
from gi.repository import Atspi, Gio, GLib

BUS_NAME = 'org.kde.FitWindow'
OBJ_PATH = '/org/kde/FitWindow'
IFACE_XML = """
<node>
  <interface name="org.kde.FitWindow">
    <method name="Fit">
      <!-- JSON: id, pid, caption, x, y, width, height, areaX, areaY, areaWidth, areaHeight,
           aspect ([w, h] to reshape to at the end, keeping the pixel count; null to leave the shape) -->
      <arg type="s" name="windowJson" direction="in"/>
    </method>
  </interface>
</node>
"""

STEP_DELAY_MS = 250      # time for the app to relayout after a resize
MAX_STEPS = 10
IDLE_EXIT_S = 300        # quit when unused; D-Bus activation restarts us
A11Y_WARMUP_MS = 2000    # after switching accessibility on, apps need a moment to publish their trees
MARGIN = 2               # px of slack on each grow step

DEBUG = bool(os.environ.get('FITWINDOW_DEBUG'))


def log(*a):
    if DEBUG:
        print('fitwindow:', *a, file=sys.stderr, flush=True)


# --- accessibility -------------------------------------------------------------------------------------------------

def a11y_enabled(bus):
    try:
        v = bus.call_sync('org.a11y.Bus', '/org/a11y/bus', 'org.freedesktop.DBus.Properties', 'Get',
                          GLib.Variant('(ss)', ('org.a11y.Status', 'IsEnabled')), None,
                          Gio.DBusCallFlags.NONE, 2000, None)
        return v.unpack()[0]
    except GLib.Error:
        return False


def enable_a11y(bus):
    """Turn on AT-SPI for this session (until logout), so toolkits publish their widget trees."""
    bus.call_sync('org.a11y.Bus', '/org/a11y/bus', 'org.freedesktop.DBus.Properties', 'Set',
                  GLib.Variant('(ssv)', ('org.a11y.Status', 'IsEnabled', GLib.Variant('b', True))), None,
                  Gio.DBusCallFlags.NONE, 2000, None)


def children(acc):
    try:
        n = acc.get_child_count()
    except GLib.Error:
        return
    for i in range(n):
        try:
            c = acc.get_child_at_index(i)
        except GLib.Error:
            continue
        if c is not None:
            yield c


def find_frame(pid, caption):
    """The accessible top-level of the window: same process, best caption match, preferring the active one."""
    desktop = Atspi.get_desktop(0)
    best, best_score = None, -1
    for app in children(desktop):
        try:
            if app.get_process_id() != pid:
                continue
        except GLib.Error:
            continue
        for win in children(app):
            try:
                name = win.get_name() or ''
                states = win.get_state_set()
            except GLib.Error:
                continue
            score = 0
            if name == caption:
                score += 4
            elif name and (name in caption or caption in name):
                score += 2
            if states.contains(Atspi.StateType.ACTIVE):
                score += 1
            if score > best_score:
                best, best_score = win, score
    return best


LIST_ROLES = (Atspi.Role.LIST, Atspi.Role.TABLE, Atspi.Role.TREE, Atspi.Role.TREE_TABLE, Atspi.Role.LIST_BOX)
ITEM_SAMPLE = 300        # children of a list looked at for its content size (plus the last one)


def collect(frame, frame_ext, depth=0, out=None):
    """Showing scrollbars as ('bar', orientation, range, length) and item views as ('list', accessible, extents),
    with each item view inside the window (hidden tabs report showing views larger than the window)."""
    if out is None:
        out = []
    if depth > 60:
        return out
    for c in children(frame):
        try:
            role = c.get_role()
            st = c.get_state_set()
        except GLib.Error:
            continue
        if not st.contains(Atspi.StateType.SHOWING):
            continue
        if role == Atspi.Role.SCROLL_BAR:
            try:
                v = c.get_value_iface()
                if v is None:
                    continue
                rng = v.get_maximum_value() - v.get_minimum_value()
                ext = c.get_extents(Atspi.CoordType.WINDOW)
            except GLib.Error:
                continue
            # orientation from the states: extents can be bogus (Kate reports its vertical bar as 60x4)
            if st.contains(Atspi.StateType.HORIZONTAL):
                horiz = True
            elif st.contains(Atspi.StateType.VERTICAL):
                horiz = False
            else:
                horiz = ext.width > ext.height
            out.append(('bar', 'h' if horiz else 'v', rng, max(ext.width, ext.height)))
        elif role in LIST_ROLES:
            try:
                ext = c.get_extents(Atspi.CoordType.WINDOW)
            except GLib.Error:
                continue
            if (ext.width > 0 and ext.height > 0 and ext.x >= 0 and ext.y >= 0
                    and ext.x + ext.width <= frame_ext.width + 1 and ext.y + ext.height <= frame_ext.height + 1):
                out.append(('list', c, ext))
            # items don't contain further views worth the walk
        else:
            collect(c, frame_ext, depth + 1, out)
    return out


def list_overflow(lst, ext):
    """Pixels the items of an item view reach beyond its visible area, per orientation."""
    try:
        n = lst.get_child_count()
    except GLib.Error:
        return {'h': 0, 'v': 0}
    idx = list(range(min(n, ITEM_SAMPLE)))
    if n > ITEM_SAMPLE:
        idx.append(n - 1)
    left, top = ext.x, ext.y
    right, bottom = ext.x + ext.width, ext.y + ext.height
    for i in idx:
        try:
            c = lst.get_child_at_index(i)
            if c is None:
                continue
            e = c.get_extents(Atspi.CoordType.WINDOW)
        except GLib.Error:
            continue
        if e.width <= 0 or e.height <= 0:
            continue
        left, top = min(left, e.x), min(top, e.y)
        right, bottom = max(right, e.x + e.width), max(bottom, e.y + e.height)
    return {'h': (right - left) - ext.width, 'v': (bottom - top) - ext.height}


def overflow(frame):
    """Per orientation, how far the main view overflows: the range of its scrollbar when the toolkit exposes
    scrollbars (pixels, or lines/items for some views), else pixels measured from the main item view's items."""
    frame_ext = frame.get_extents(Atspi.CoordType.WINDOW)
    found = collect(frame, frame_ext)
    result = {}
    bars = {}
    for kind, *rest in found:
        if kind == 'bar':
            o, rng, length = rest
            if length > bars.get(o, (0, 0.0))[0]:
                bars[o] = (length, rng)
    lists = [(e.width * e.height, l, e) for kind, l, e in (f for f in found if f[0] == 'list')]
    main = max(lists, key=lambda t: t[0]) if lists else None
    by_items = list_overflow(main[1], main[2]) if main else {'h': 0, 'v': 0}
    for o in ('h', 'v'):
        if o in bars:
            result[o] = max(0.0, bars[o][1])
        else:
            result[o] = float(max(0, by_items[o]))
    return result


# --- KWin --------------------------------------------------------------------------------------------------------

RESIZE_JS = """
(function () {
    const w = workspace.windowList().find(c => c.internalId.toString() === %(id)s);
    if (!w) return;
    w.setMaximize(false, false);
    w.frameGeometry = {x: %(x)d, y: %(y)d, width: %(w)d, height: %(h)d};
})();
"""


class KWin:
    def __init__(self, bus):
        self.bus = bus
        self.dir = tempfile.mkdtemp(prefix='fitwindow-', dir=os.environ.get('XDG_RUNTIME_DIR'))
        self.n = 0

    def _call(self, path, iface, method, args, rtype=None):
        return self.bus.call_sync('org.kde.KWin', path, iface, method, args,
                                  GLib.VariantType(rtype) if rtype else None,
                                  Gio.DBusCallFlags.NONE, 5000, None)

    def set_geometry(self, window_id, x, y, w, h):
        self.n += 1
        name = f'fitwindow-step-{os.getpid()}-{self.n}'
        path = os.path.join(self.dir, name + '.js')
        with open(path, 'w') as f:
            f.write(RESIZE_JS % {'id': json.dumps(window_id), 'x': x, 'y': y, 'w': w, 'h': h})
        try:
            sid = self._call('/Scripting', 'org.kde.kwin.Scripting', 'loadScript',
                             GLib.Variant('(ss)', (path, name)), '(i)').unpack()[0]
            if sid < 0:
                log('loadScript failed')
                return
            self._call(f'/Scripting/Script{sid}', 'org.kde.kwin.Script', 'run', None)
        finally:
            try:
                self._call('/Scripting', 'org.kde.kwin.Scripting', 'unloadScript', GLib.Variant('(s)', (name,)))
            except GLib.Error:
                pass
            try:
                os.unlink(path)
            except OSError:
                pass


# --- the fitting loop -------------------------------------------------------------------------------------------

class FitJob:
    def __init__(self, service, window_id, pid, caption, geom, area, aspect=None):
        self.svc = service
        self.aspect = aspect
        self.id, self.pid, self.caption = window_id, pid, caption
        self.x, self.y, self.w, self.h = geom
        self.ax, self.ay, self.aw, self.ah = area
        self.steps = 0
        self.prev = {}       # orientation -> (range before last grow, px grown)
        self.done = set()    # orientations we gave up on (at screen size / no progress)

    def start(self, delay_ms=0):
        GLib.timeout_add(max(1, delay_ms), self.step)

    def step(self):
        try:
            self._step()
        except Exception as e:  # never let one window's trouble kill the service
            log('error:', repr(e))
            self.finish()
        return False

    def _step(self):
        frame = find_frame(self.pid, self.caption)
        if frame is None:
            log('no accessible frame for pid', self.pid, repr(self.caption))
            return self.finish()
        frame.clear_cache()   # states (showing) are cached by libatspi
        ov = overflow(frame)
        log(f'step {self.steps}: size {self.w}x{self.h} overflow {ov}')

        grow = {}
        for o, size, limit in (('h', self.w, self.aw), ('v', self.h, self.ah)):
            rng = ov[o]
            if rng <= 0 or o in self.done:
                continue
            if size >= limit:
                self.done.add(o)
                continue
            if o in self.prev:
                r0, grown = self.prev[o]
                per_px = (r0 - rng) / grown if grown else 0
                if per_px <= 0:
                    # growing didn't shrink the range: the view doesn't reflow with the window in this direction
                    self.done.add(o)
                    continue
                px = rng / per_px
            else:
                px = rng           # assume pixel units first
            grow[o] = int(px + 0.999) + MARGIN

        if not grow or self.steps >= MAX_STEPS:
            return self.finish()

        nw = min(self.aw, self.w + grow.get('h', 0))
        nh = min(self.ah, self.h + grow.get('v', 0))
        # keep the top-left corner where it is, unless that pushes the window off the usable area
        nx = max(self.ax, min(self.x, self.ax + self.aw - nw))
        ny = max(self.ay, min(self.y, self.ay + self.ah - nh))
        for o, old, new in (('h', self.w, nw), ('v', self.h, nh)):
            if o in grow:
                self.prev[o] = (ov[o], new - old)
        self.x, self.y, self.w, self.h = nx, ny, nw, nh
        self.steps += 1
        self.svc.kwin.set_geometry(self.id, nx, ny, nw, nh)
        self.start(STEP_DELAY_MS)


    def finish(self):
        """Reshape to the requested aspect ratio with the same pixel count, then end the job."""
        try:
            if self.aspect:
                x, y, w, h = aspect_geometry((self.x, self.y, self.w, self.h), (self.ax, self.ay, self.aw, self.ah),
                                             self.aspect)
                log(f'finished: {self.w}x{self.h} -> {w}x{h} ({self.aspect[0]:g}:{self.aspect[1]:g})')
                if (x, y, w, h) != (self.x, self.y, self.w, self.h):
                    self.svc.kwin.set_geometry(self.id, x, y, w, h)
            else:
                log(f'finished: {self.w}x{self.h}')
        finally:
            self.svc.job_finished(self)


def aspect_geometry(geom, area, aspect):
    """Same pixel count as geom at the given aspect ratio, shrunk (keeping the ratio) to fit area; centred on
    geom, then moved back inside area. Mirrors aspectGeometry() in the KWin script."""
    x, y, w, h = geom
    ax, ay, aw, ah = area
    rw, rh = aspect
    nh = (w * h * rh / rw) ** 0.5
    nw = nh * rw / rh
    s = min(1.0, aw / nw, ah / nh)
    nw, nh = round(nw * s), round(nh * s)
    nx = round(x + (w - nw) / 2)
    ny = round(y + (h - nh) / 2)
    nx = max(ax, min(nx, ax + aw - nw))
    ny = max(ay, min(ny, ay + ah - nh))
    return nx, ny, nw, nh


class Service:
    def __init__(self):
        self.loop = GLib.MainLoop()
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.kwin = KWin(self.bus)
        self.jobs = {}
        self.idle_src = None
        node = Gio.DBusNodeInfo.new_for_xml(IFACE_XML)
        self.bus.register_object(OBJ_PATH, node.interfaces[0], self.on_call, None, None)
        Gio.bus_own_name_on_connection(self.bus, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                                       None, lambda *a: self.loop.quit())
        self.arm_idle()

    def arm_idle(self):
        if self.idle_src:
            GLib.source_remove(self.idle_src)
        self.idle_src = GLib.timeout_add_seconds(IDLE_EXIT_S, self.on_idle)

    def on_idle(self):
        if self.jobs:
            return True
        self.loop.quit()
        return False

    def on_call(self, conn, sender, path, iface, method, params, invocation):
        if method != 'Fit':
            invocation.return_dbus_error('org.freedesktop.DBus.Error.UnknownMethod', method)
            return
        invocation.return_value(None)
        try:
            d = json.loads(params.unpack()[0])
            wid, pid, caption = str(d['id']), int(d['pid']), str(d['caption'])
            x, y, w, h = (int(d[k]) for k in ('x', 'y', 'width', 'height'))
            ax, ay, aw, ah = (int(d[k]) for k in ('areaX', 'areaY', 'areaWidth', 'areaHeight'))
            aspect = d.get('aspect')
            aspect = (float(aspect[0]), float(aspect[1])) if aspect else None
        except (ValueError, KeyError, TypeError) as e:
            log('bad request:', repr(e))
            return
        self.arm_idle()
        if wid in self.jobs:
            return   # already fitting this window
        delay = 0
        if not a11y_enabled(self.bus):
            log('enabling accessibility')
            try:
                enable_a11y(self.bus)
            except GLib.Error as e:
                log('could not enable accessibility:', e.message)
                return
            delay = A11Y_WARMUP_MS
        job = FitJob(self, wid, pid, caption, (x, y, w, h), (ax, ay, aw, ah), aspect)
        self.jobs[wid] = job
        job.start(delay)

    def job_finished(self, job):
        self.jobs.pop(job.id, None)

    def run(self):
        try:
            self.loop.run()
        finally:
            try:
                os.rmdir(self.kwin.dir)
            except OSError:
                pass


if __name__ == '__main__':
    Atspi.init()
    Service().run()
