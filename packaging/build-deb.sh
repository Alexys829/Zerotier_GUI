#!/bin/bash
# Build .deb package for ZeroTier GUI
# Usage: bash packaging/build-deb.sh [version]
# Example: bash packaging/build-deb.sh 1.0.0

set -euo pipefail

VERSION="${1:-1.0.0}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$SCRIPT_DIR/build"
PKG_ROOT="$BUILD_DIR/zerotier-gui_${VERSION}_all"

echo "==> Building zerotier-gui ${VERSION}"

# Clean previous build
rm -rf "$BUILD_DIR"
mkdir -p "$PKG_ROOT"

# --- DEBIAN metadata ---
mkdir -p "$PKG_ROOT/DEBIAN"
sed "s/@VERSION@/${VERSION}/" "$SCRIPT_DIR/DEBIAN/control" > "$PKG_ROOT/DEBIAN/control"
cp "$SCRIPT_DIR/DEBIAN/postinst" "$PKG_ROOT/DEBIAN/postinst"
cp "$SCRIPT_DIR/DEBIAN/postrm" "$PKG_ROOT/DEBIAN/postrm"
chmod 0755 "$PKG_ROOT/DEBIAN/postinst" "$PKG_ROOT/DEBIAN/postrm"

# --- Application sources ---
mkdir -p "$PKG_ROOT/usr/share/zerotier-gui"
for pyfile in main.py main_window.py zerotier_client.py tray_icon.py resources.py; do
    cp "$PROJECT_DIR/$pyfile" "$PKG_ROOT/usr/share/zerotier-gui/"
done

# --- Launcher script ---
mkdir -p "$PKG_ROOT/usr/bin"
cat > "$PKG_ROOT/usr/bin/zerotier-gui" << 'LAUNCHER'
#!/usr/bin/python3
import sys
sys.path.insert(0, "/usr/share/zerotier-gui")
from main import main
main()
LAUNCHER
chmod 0755 "$PKG_ROOT/usr/bin/zerotier-gui"

# --- Desktop entry ---
mkdir -p "$PKG_ROOT/usr/share/applications"
cp "$SCRIPT_DIR/zerotier-gui.desktop" "$PKG_ROOT/usr/share/applications/"

# --- Icon ---
mkdir -p "$PKG_ROOT/usr/share/icons/hicolor/scalable/apps"
cp "$SCRIPT_DIR/zerotier-gui.svg" "$PKG_ROOT/usr/share/icons/hicolor/scalable/apps/"

# --- Polkit policy ---
mkdir -p "$PKG_ROOT/usr/share/polkit-1/actions"
cp "$SCRIPT_DIR/com.github.zerotier-gui.policy" "$PKG_ROOT/usr/share/polkit-1/actions/"

# --- Polkit helper ---
mkdir -p "$PKG_ROOT/usr/lib/zerotier-gui"
cp "$SCRIPT_DIR/zerotier-gui-helper" "$PKG_ROOT/usr/lib/zerotier-gui/"
chmod 0755 "$PKG_ROOT/usr/lib/zerotier-gui/zerotier-gui-helper"

# --- Build the .deb ---
echo "==> Building .deb package..."
dpkg-deb --root-owner-group --build "$PKG_ROOT"

DEB_FILE="$BUILD_DIR/zerotier-gui_${VERSION}_all.deb"
echo "==> Package built: $DEB_FILE"
echo "==> Contents:"
dpkg-deb -c "$DEB_FILE"
echo ""
echo "==> Install with: sudo dpkg -i $DEB_FILE"
