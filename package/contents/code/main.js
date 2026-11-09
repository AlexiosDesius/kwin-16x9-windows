// 16:9 Windows: keep normal windows at 16:9 while preserving their pixel count (width x height).
//  - New windows are reshaped as they open.
//  - Any window is reshaped when you finish moving or resizing it (mouse or keyboard).
// Tiled, maximized, fullscreen and borderless windows, dialogs and special windows are left alone.

const ASPECT_W = 16, ASPECT_H = 9;

function reshapeable(w) {
    return w && w.managed && !w.deleted && w.normalWindow && !w.transient && !w.specialWindow
        && w.resizeable && !w.fullScreen && !w.noBorder && !w.tile && w.maximizeMode === 0;
}

// Same area as width x height, at 16:9; shrunk (keeping 16:9) if that doesn't fit the area.
// Keeps the window's centre, then moves it back inside the area if needed.
function aspectGeometry(g, a) {
    const pixels = g.width * g.height;
    let h = Math.sqrt(pixels * ASPECT_H / ASPECT_W);
    let w = h * ASPECT_W / ASPECT_H;
    const s = Math.min(1, a.width / w, a.height / h);
    w = Math.round(w * s);
    h = Math.round(h * s);
    let x = Math.round(g.x + (g.width - w) / 2);
    let y = Math.round(g.y + (g.height - h) / 2);
    x = Math.max(a.x, Math.min(x, a.x + a.width - w));
    y = Math.max(a.y, Math.min(y, a.y + a.height - h));
    return {x: x, y: y, width: w, height: h};
}

function reshape(w) {
    if (!reshapeable(w))
        return;
    const g = w.frameGeometry;
    const n = aspectGeometry(g, workspace.clientArea(KWin.MaximizeArea, w));
    // skip no-op changes (already 16:9 within rounding and in place)
    if (Math.abs(n.x - g.x) < 1 && Math.abs(n.y - g.y) < 1 && Math.abs(n.width - g.width) < 1
            && Math.abs(n.height - g.height) < 1)
        return;
    w.frameGeometry = n;
}

// Runs fn(w) after a short delay. Timers are kept referenced until they fire, so they can't be
// garbage-collected first.
const pending = new Set();
function later(ms, w, fn) {
    const t = new QTimer();
    t.singleShot = true;
    t.interval = ms;
    pending.add(t);
    t.timeout.connect(function () {
        pending.delete(t);
        fn(w);
    });
    t.start();
}

function watch(w) {
    // Fires once when an interactive move or resize ends. By then KWin has applied any snap, tile or maximize from
    // the drag, so reshapeable() sees the final state. Our own geometry change isn't interactive, so it doesn't
    // re-trigger this.
    w.interactiveMoveResizeFinished.connect(function () { reshape(w); });
}

workspace.windowList().forEach(watch);

workspace.windowAdded.connect(function (w) {
    watch(w);
    // new windows: wait a moment so the app's own initial sizing and KWin's placement / window rules settle first
    if (reshapeable(w))
        later(100, w, reshape);
});
