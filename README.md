# 16:9 Windows (KWin script)

Keeps normal windows at **16:9 while preserving their pixel count** (width × height):
- **New windows** are reshaped about 100 ms after they open, so the app's own sizing and window rules apply first.
- **Any window** is reshaped when you finish moving or resizing it with the mouse or keyboard. Resizing therefore
  sets the window's area, and the shape snaps back to 16:9 when you let go.
- Left alone: tiled, maximized, fullscreen and borderless windows, dialogs and special windows. Geometry changes
  made by apps or scripts don't trigger it, only your own moves and resizes.
- If 16:9 at that pixel count doesn't fit the screen, the window shrinks and stays 16:9. Its centre stays in place,
  then the window is moved back on screen if needed.
- The ratio is `ASPECT_W`/`ASPECT_H` at the top of `package/contents/code/main.js`.

## Install / remove
    ./install.sh      # also re-run after editing
    ./uninstall.sh
Toggle it in System Settings → Window Management → KWin Scripts → "16:9 Windows". Plugin id:
`aspect169windows`.

## Unused: fit-to-content helper
`service/fitwindow.py` is the earlier Meta+Ctrl+F "grow until the scrollbars disappear" helper. It read scrollbars
over AT-SPI and was D-Bus activated as `org.kde.FitWindow`. It is no longer installed or called; it's kept only for
reference.

## Sharing
    cd package && zip -r -X ../16x9-windows.kwinscript .
Recipients install it in System Settings → Window Management → KWin Scripts → Install from File…, then tick it
and click Apply. It needs Plasma 6.
