#!/bin/bash
# Remove "16:9 Windows".
set -uo pipefail
kwriteconfig6 --file kwinrc --group Plugins --key aspect169windowsEnabled --delete
qdbus org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript aspect169windows >/dev/null
qdbus org.kde.KWin /KWin reconfigure
kpackagetool6 --type=KWin/Script --remove aspect169windows
echo "Removed."
