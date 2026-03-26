#!/bin/bash
set -e

# ── Couleurs ──────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $1"; }
info() { echo -e "${YELLOW}→${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }

echo ""
echo "  MAC Monitor — Installation"
echo "  ──────────────────────────"
echo ""

# ── 1. Trouver Python 3 ───────────────────────────────────────
info "Recherche de Python 3..."
PYTHON=""
for candidate in \
    "$(brew --prefix python@3.13 2>/dev/null)/bin/python3" \
    "$(brew --prefix python@3.12 2>/dev/null)/bin/python3" \
    "$(brew --prefix python@3.11 2>/dev/null)/bin/python3" \
    "$(command -v python3)"; do
    if [ -x "$candidate" ] && "$candidate" -c "import sys; assert sys.version_info >= (3,11)" 2>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done
[ -z "$PYTHON" ] && fail "Python 3.11+ introuvable. Installe-le avec: brew install python@3.13"
ok "Python : $PYTHON ($(${PYTHON} --version))"

# ── 2. Dépendances pip ────────────────────────────────────────
info "Installation des dépendances pip..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Homebrew Python est "externally managed" depuis PEP 668
if "$PYTHON" -m pip install -q -r "$SCRIPT_DIR/requirements.txt" 2>/dev/null; then
    ok "Dépendances installées"
elif "$PYTHON" -m pip install -q --break-system-packages -r "$SCRIPT_DIR/requirements.txt" 2>/dev/null; then
    ok "Dépendances installées (--break-system-packages)"
else
    fail "Impossible d'installer les dépendances. Lance manuellement:\n  $PYTHON -m pip install --break-system-packages -r requirements.txt"
fi

# ── 3. LaunchAgent (démarrage automatique) ───────────────────
LABEL="com.macmonitor.app"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST="$PLIST_DIR/${LABEL}.plist"
mkdir -p "$PLIST_DIR"

# Déscharger l'ancien plist si différent (migration)
for old_label in com.owenmionnet.macmonitor com.macmonitor.menubar; do
    old_plist="$PLIST_DIR/${old_label}.plist"
    if [ -f "$old_plist" ]; then
        launchctl unload "$old_plist" 2>/dev/null || true
        rm -f "$old_plist"
        info "Ancien LaunchAgent ($old_label) supprimé"
    fi
done

info "Création du LaunchAgent..."
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON}</string>
        <string>${SCRIPT_DIR}/menubar_monitor.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/macmonitor.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/macmonitor.err</string>
</dict>
</plist>
PLIST_EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"
ok "LaunchAgent chargé — démarrage automatique activé"

# ── 4. Icône de l'app ─────────────────────────────────────────
info "Génération de l'icône..."
"$PYTHON" - <<'PYEOF'
import os, subprocess
from AppKit import (NSBitmapImageRep, NSBezierPath, NSColor,
                    NSGraphicsContext)

def make_png(size):
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, size, size, 8, 4, True, False, "NSCalibratedRGBColorSpace", 0, 0)
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)
    from Foundation import NSMakeRect, NSMakePoint

    r = size * 0.22
    bg = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(0, 0, size, size), r, r)
    NSColor.colorWithRed_green_blue_alpha_(0.10, 0.10, 0.13, 1.0).setFill(); bg.fill()

    cx, cy, cr = size*0.5, size*0.5, size*0.30
    circle = NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx-cr, cy-cr, cr*2, cr*2))
    NSColor.colorWithRed_green_blue_alpha_(0.18, 0.90, 0.62, 1.0).setFill(); circle.fill()

    NSColor.colorWithRed_green_blue_alpha_(0.18, 0.90, 0.62, 0.20).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx-cr-size*0.05, cy-cr-size*0.05, (cr+size*0.05)*2, (cr+size*0.05)*2)).fill()

    hs = size*0.18; hx = cx-hs/2; hy = cy-hs*0.1
    NSColor.colorWithRed_green_blue_alpha_(0.08, 0.08, 0.11, 1.0).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(hx, hy, hs, hs*0.85), hs*0.18, hs*0.18).fill()

    ew = hs*0.18
    for ex in (hx+hs*0.22, hx+hs*0.60):
        NSColor.colorWithRed_green_blue_alpha_(0.18, 0.90, 0.62, 1.0).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(ex, hy+hs*0.40, ew, ew*1.1)).fill()

    ant = NSBezierPath.bezierPath()
    ant.moveToPoint_(NSMakePoint(cx, hy+hs*0.85))
    ant.lineToPoint_(NSMakePoint(cx, hy+hs*1.20))
    NSColor.colorWithRed_green_blue_alpha_(0.18, 0.90, 0.62, 0.8).setStroke()
    ant.setLineWidth_(max(1, size*0.025)); ant.stroke()
    NSColor.colorWithRed_green_blue_alpha_(0.18, 0.90, 0.62, 1.0).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx-ew*0.6, hy+hs*1.18, ew*1.2, ew*1.2)).fill()

    NSColor.colorWithRed_green_blue_alpha_(1, 1, 1, 0.15).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx-cr*0.6, cy+cr*0.1, cr*0.5, cr*0.35)).fill()

    NSGraphicsContext.restoreGraphicsState()
    return rep.representationUsingType_properties_(4, None)

iconset = "/tmp/AppIcon.iconset"
os.makedirs(iconset, exist_ok=True)
for s in [16,32,64,128,256,512,1024]:
    make_png(s).writeToFile_atomically_(f"{iconset}/icon_{s}x{s}.png", True)
    if s <= 512:
        make_png(s*2).writeToFile_atomically_(f"{iconset}/icon_{s}x{s}@2x.png", True)

os.makedirs("/Applications/MAC Monitor.app/Contents/Resources", exist_ok=True)
subprocess.run(["iconutil", "-c", "icns", iconset,
                "-o", "/Applications/MAC Monitor.app/Contents/Resources/AppIcon.icns"],
               check=True)
PYEOF
ok "Icône générée"

# ── 5. App bundle toggle ──────────────────────────────────────
info "Création de MAC Monitor.app..."
APP="/Applications/MAC Monitor.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/MacOS/toggle" <<TOGGLE_EOF
#!/bin/bash
LABEL="com.macmonitor.app"
PLIST="\$HOME/Library/LaunchAgents/\${LABEL}.plist"
if launchctl list "\$LABEL" 2>/dev/null | grep -q '"PID"'; then
    launchctl unload "\$PLIST" 2>/dev/null
    osascript -e 'display notification "MAC Monitor arrêté" with title "MAC Monitor" subtitle "Cliquez pour relancer"'
else
    launchctl load -w "\$PLIST" 2>/dev/null
    osascript -e 'display notification "MAC Monitor démarré" with title "MAC Monitor" subtitle "Icône dans la barre de menu"'
fi
TOGGLE_EOF
chmod +x "$APP/Contents/MacOS/toggle"

cat > "$APP/Contents/Info.plist" <<INFO_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>MAC Monitor</string>
    <key>CFBundleDisplayName</key><string>MAC Monitor</string>
    <key>CFBundleIdentifier</key><string>com.macmonitor.toggle</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundleExecutable</key><string>toggle</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
INFO_EOF

/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister -f "$APP" 2>/dev/null || true
ok "MAC Monitor.app créé dans /Applications"

# ── Résumé ────────────────────────────────────────────────────
echo ""
echo "  Installation terminée ✓"
echo "  ─────────────────────────────────────────────────────"
echo "  • L'icône robot apparaît dans la barre de menu"
echo "  • Le monitor démarre automatiquement à la connexion"
echo "  • MAC Monitor.app dans Applications pour démarrer/arrêter"
echo ""
