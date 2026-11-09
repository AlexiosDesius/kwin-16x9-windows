#!/bin/bash
# Install (or upgrade) the "16:9 Windows" KWin script for the current user, enable it and reload it.
# Needs Plasma 6. Re-run after editing or after `git pull`.
set -euo pipefail
cd "$(dirname "$0")"
ID=aspect169windows

die() { echo "error: $*" >&2; exit 1; }

# The Qt 6 qdbus tool has different names on different distros.
QDBUS=""
for c in qdbus6 qdbus-qt6 qdbus; do
    if command -v "$c" >/dev/null 2>&1; then QDBUS="$c"; break; fi
done
[ -n "$QDBUS" ] || die "qdbus not found (package: qt6-qttools / qdbus-qt6 / qt6-tools)"
command -v kpackagetool6 >/dev/null 2>&1 || die "kpackagetool6 not found: this needs Plasma 6"
command -v kwriteconfig6 >/dev/null 2>&1 || die "kwriteconfig6 not found: this needs Plasma 6"
"$QDBUS" org.kde.KWin /KWin >/dev/null 2>&1 || die "KWin isn't reachable over D-Bus: run this inside your Plasma session"

if kpackagetool6 --type=KWin/Script --show "$ID" >/dev/null 2>&1; then
    kpackagetool6 --type=KWin/Script --upgrade package
else
    kpackagetool6 --type=KWin/Script --install package
fi
kwriteconfig6 --file kwinrc --group Plugins --key "${ID}Enabled" true
# unload first so an upgrade takes effect, then let KWin load the enabled script again
"$QDBUS" org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript "$ID" >/dev/null || true
"$QDBUS" org.kde.KWin /KWin reconfigure
echo "Installed: System Settings > Window Management > KWin Scripts > 16:9 Windows."
