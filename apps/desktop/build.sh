#!/bin/bash
# Build AI视频日记 Agent Studio macOS .app
# Prerequisites: node, pnpm, python3, pyinstaller
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DESKTOP_DIR="$SCRIPT_DIR"
echo "=== AI视频日记 Agent Studio Build ==="
echo ""

# ── Single package entry ──
echo "[1/2] Installing Electron dependencies..."
cd "$DESKTOP_DIR"
pnpm install --frozen-lockfile

echo "[2/2] Building and packaging macOS app..."
pnpm build:mac
echo ""

echo "=== Build Complete ==="
echo "Output: $DESKTOP_DIR/dist/mac/"
ls -la "$DESKTOP_DIR/dist/mac/" 2>/dev/null || echo "(check dist/ for output)"
