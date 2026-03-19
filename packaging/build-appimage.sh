#!/bin/bash
# Build AppImage for ZeroTier GUI
# Usage: bash packaging/build-appimage.sh
#
# Prerequisites: wget, file (usually pre-installed on any Linux desktop)
# The script downloads all other tools automatically.

set -euo pipefail

PYTHON_APPIMAGE_VERSION="3.12.12"
PYTHON_APPIMAGE_FILE="python${PYTHON_APPIMAGE_VERSION}-cp312-cp312-manylinux_2_28_x86_64.AppImage"
APPIMAGETOOL_FILE="appimagetool-x86_64.AppImage"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$SCRIPT_DIR/build"
CACHE_DIR="$SCRIPT_DIR/cache"
APPDIR="$BUILD_DIR/AppDir"
OUTPUT="$BUILD_DIR/ZeroTier_GUI-x86_64.AppImage"

SOURCE_FILES="main.py main_window.py zerotier_client.py tray_icon.py resources.py database.py style.qss"

echo "==> Building ZeroTier GUI AppImage"

# ── 1. Download tools (cached) ──────────────────────────────────────────────

mkdir -p "$CACHE_DIR"

if [ ! -f "$CACHE_DIR/$PYTHON_APPIMAGE_FILE" ]; then
    echo "==> Downloading Python AppImage..."
    wget -q --show-progress -O "$CACHE_DIR/$PYTHON_APPIMAGE_FILE" \
        "https://github.com/niess/python-appimage/releases/download/python3.12/${PYTHON_APPIMAGE_FILE}"
fi

if [ ! -f "$CACHE_DIR/$APPIMAGETOOL_FILE" ]; then
    echo "==> Downloading appimagetool..."
    wget -q --show-progress -O "$CACHE_DIR/$APPIMAGETOOL_FILE" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/${APPIMAGETOOL_FILE}"
fi

chmod +x "$CACHE_DIR/$PYTHON_APPIMAGE_FILE" "$CACHE_DIR/$APPIMAGETOOL_FILE"

# ── 2. Prepare clean build directory ────────────────────────────────────────

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# ── 3. Extract Python AppImage → AppDir ─────────────────────────────────────

echo "==> Extracting Python AppImage..."
cd "$BUILD_DIR"
"$CACHE_DIR/$PYTHON_APPIMAGE_FILE" --appimage-extract > /dev/null 2>&1
mv squashfs-root "$APPDIR"

# ── 4. Install PyQt6 inside AppDir ──────────────────────────────────────────

echo "==> Installing PyQt6 (this may take a minute)..."
"$APPDIR/usr/bin/python3" -m pip install --no-cache-dir --upgrade pip > /dev/null 2>&1
"$APPDIR/usr/bin/python3" -m pip install --no-cache-dir \
    PyQt6 PyQt6-Qt6 PyQt6-sip 2>&1 | tail -1

# ── 5. Copy application sources ─────────────────────────────────────────────

echo "==> Copying application sources..."
mkdir -p "$APPDIR/usr/share/zerotier-gui"
for f in $SOURCE_FILES; do
    cp "$PROJECT_DIR/$f" "$APPDIR/usr/share/zerotier-gui/"
done

# ── 6. Create AppRun ────────────────────────────────────────────────────────

echo "==> Creating AppRun entry point..."
# python-appimage's AppRun is a symlink to usr/bin/python3.X — remove it first
# to avoid overwriting the Python wrapper through the symlink
rm -f "$APPDIR/AppRun"
cat > "$APPDIR/AppRun" << 'APPRUN'
#!/bin/bash
APPDIR="$(cd "$(dirname "$0")" && pwd)"

# Qt platform plugin path
export QT_PLUGIN_PATH="${APPDIR}/usr/lib/python3.12/site-packages/PyQt6/Qt6/plugins"

# Ensure Qt can find its libraries
export LD_LIBRARY_PATH="${APPDIR}/usr/lib/python3.12/site-packages/PyQt6/Qt6/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

exec "${APPDIR}/usr/bin/python3" -s "${APPDIR}/usr/share/zerotier-gui/main.py" "$@"
APPRUN
chmod 0755 "$APPDIR/AppRun"

# ── 7. Desktop entry and icon ───────────────────────────────────────────────

echo "==> Setting up desktop integration..."
# Desktop file must be in AppDir root for appimagetool
cp "$SCRIPT_DIR/zerotier-gui.desktop" "$APPDIR/zerotier-gui.desktop"
# Icon must be in AppDir root (appimagetool expects it next to .desktop)
cp "$SCRIPT_DIR/zerotier-gui.svg" "$APPDIR/zerotier-gui.svg"
# Replace Python icon with ZeroTier icon for file managers
rm -f "$APPDIR/.DirIcon" "$APPDIR/python.png"
ln -s "zerotier-gui.svg" "$APPDIR/.DirIcon"

# ── 8. Build the AppImage ───────────────────────────────────────────────────

echo "==> Building AppImage..."
cd "$BUILD_DIR"
ARCH=x86_64 "$CACHE_DIR/$APPIMAGETOOL_FILE" --no-appstream "$APPDIR" "$OUTPUT" 2>&1 | tail -3

echo ""
echo "==> AppImage built: $OUTPUT"
echo "==> Run with: chmod +x $OUTPUT && $OUTPUT"
