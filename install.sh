#!/bin/bash
# Install "16:9 Windows" (KWin script) and enable it. Re-run after editing.
set -euo pipefail
cd "$(dirname "$0")"
if kpackagetool6 --type=KWin/Script --show aspect169windows >/dev/null 2>&1; then
    kpackagetool6 --type=KWin/Script --upgrade package
else
    kpackagetool6 --type=KWin/Script --install package
fi
kwriteconfig6 --file kwinrc --group Plugins --key aspect169windowsEnabled true
# reload the script so an upgrade takes effect
qdbus org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript aspect169windows >/dev/null
qdbus org.kde.KWin /KWin reconfigure
echo "Installed: System Settings > Window Management > KWin Scripts > 16:9 Windows."
