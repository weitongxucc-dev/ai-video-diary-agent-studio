#!/bin/bash
# Build AI视频日记 Agent Studio macOS .app
# Prerequisites: node, pnpm, python3, pyinstaller
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DESKTOP_DIR="$SCRIPT_DIR"
WEB_DIR="$REPO_ROOT/apps/web"
API_DIR="$REPO_ROOT/apps/api"

echo "=== AI视频日记 Agent Studio Build ==="
echo ""

# ── Step 1: Build web frontend ──
echo "[1/5] Building web frontend..."
cd "$WEB_DIR"
pnpm install --frozen-lockfile
pnpm build
echo "  Materialising standalone deps..."
node "$DESKTOP_DIR/scripts/bundle-standalone.js"
echo "  Web frontend built."

# ── Step 2: Build API binary ──
echo "[2/5] Building API server binary..."
cd "$API_DIR"
if [ ! -f "pyinstaller.spec" ]; then
    echo "  Generating PyInstaller spec..."
    pyinstaller --name api-server --onefile \
        --hidden-import uvicorn.logging \
        --hidden-import uvicorn.loops.auto \
        --hidden-import app.models \
        --hidden-import app.main \
        --distpath "$DESKTOP_DIR/dist" \
        --workpath "$DESKTOP_DIR/build-api" \
        app/main.py
else
    pyinstaller pyinstaller.spec \
        --distpath "$DESKTOP_DIR/dist" \
        --workpath "$DESKTOP_DIR/build-api" \
        --clean --noconfirm
fi
echo "  API binary built."

# ── Step 3: Build Worker binary ──
echo "[3/5] Building automation worker binary..."
cd "$API_DIR"
pyinstaller worker.spec \
    --distpath "$DESKTOP_DIR/dist" \
    --workpath "$DESKTOP_DIR/build-worker" \
    --clean --noconfirm
echo "  Worker binary built."

# ── Step 4: Install Electron deps ──
echo "[4/5] Installing Electron dependencies..."
cd "$DESKTOP_DIR"
pnpm install
echo "  Electron deps installed."

# ── Step 5: Package as .app ──
echo "[5/5] Packaging macOS .app..."
pnpm build:mac
echo ""

echo "=== Build Complete ==="
echo "Output: $DESKTOP_DIR/dist/mac/"
ls -la "$DESKTOP_DIR/dist/mac/" 2>/dev/null || echo "(check dist/ for output)"
