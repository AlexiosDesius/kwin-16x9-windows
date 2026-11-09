#!/bin/bash
# Remove the "16:9 Windows" KWin script for the current user.
set -uo pipefail
ID=aspect169windows

QDBUS=""
for c in qdbus6 qdbus-qt6 qdbus; do
    if command -v "$c" >/dev/null 2>&1; then QDBUS="$c"; break; fi
done

kwriteconfig6 --file kwinrc --group Plugins --key "${ID}Enabled" --delete
if [ -n "$QDBUS" ]; then
    "$QDBUS" org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript "$ID" >/dev/null
    "$QDBUS" org.kde.KWin /KWin reconfigure
else
    echo "qdbus not found: the script stays active until you log out" >&2
fi
kpackagetool6 --type=KWin/Script --remove "$ID"
echo "Removed."
