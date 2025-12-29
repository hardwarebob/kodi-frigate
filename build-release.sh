#!/bin/bash
# Build release ZIP for Kodi Frigate NVR Integration addon

set -e

# Get version from addon.xml
VERSION=$(sed -n 's/.*version="\([^"]*\)".*/\1/p' addon.xml | grep -v "^1.0$" | head -1)
ADDON_ID=$(sed -n 's/.*id="\([^"]*\)".*/\1/p' addon.xml | head -1)

RELEASE_NAME="${ADDON_ID}-${VERSION}.zip"
BUILD_DIR="build"
# Use kodi-frigate as the directory name inside ZIP (not the addon ID)
ADDON_DIR="kodi-frigate"

echo "Building ${RELEASE_NAME}..."

# Clean up any previous build
rm -f "../${RELEASE_NAME}"
rm -rf "${BUILD_DIR}"

# Create build directory structure
mkdir -p "${BUILD_DIR}/${ADDON_DIR}"

# Copy addon files
echo "Copying addon files..."
rsync -av \
  --exclude='.git' \
  --exclude='.claude' \
  --exclude='*.pyc' \
  --exclude='__pycache__' \
  --exclude='.DS_Store' \
  --exclude='.gitignore' \
  --exclude='build' \
  --exclude='*.zip' \
  --exclude='build-release.sh' \
  ./ "${BUILD_DIR}/${ADDON_DIR}/"

# Create ZIP
echo "Creating ZIP archive..."
cd "${BUILD_DIR}"
zip -r "../../${RELEASE_NAME}" "${ADDON_DIR}" -q

# Clean up build directory
cd ..
rm -rf "${BUILD_DIR}"

# Show result
if [ -f "../${RELEASE_NAME}" ]; then
  SIZE=$(du -h "../${RELEASE_NAME}" | cut -f1)
  echo ""
  echo "✓ Release built successfully!"
  echo "  File: ../${RELEASE_NAME}"
  echo "  Size: ${SIZE}"
  echo ""
  echo "Install in Kodi: Settings → Add-ons → Install from zip file"
else
  echo "✗ Build failed!"
  exit 1
fi
