#!/bin/bash

echo ""
echo "========================================"
echo "  CQ TDM Installer for macOS"
echo "========================================"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}[ERROR]${NC} Python 3 not found."
    echo "Please install Python 3.10+ using one of these methods:"
    echo "  - Download from https://www.python.org/downloads/"
    echo "  - Using Homebrew: brew install python"
    exit 1
fi

PYVER=$(python3 --version 2>&1 | cut -d' ' -f2)
echo -e "${GREEN}[OK]${NC} Python $PYVER found"

# Check/install pipx
if ! command -v pipx &> /dev/null; then
    echo ""
    echo "pipx not found. Installing pipx..."

    # Try Homebrew first
    if command -v brew &> /dev/null; then
        if brew install pipx; then
            pipx ensurepath --force > /dev/null 2>&1
            export PATH="$HOME/.local/bin:$PATH"
            echo -e "${GREEN}[OK]${NC} pipx installed via Homebrew"
        else
            echo -e "${RED}[ERROR]${NC} Failed to install pipx via Homebrew"
            exit 1
        fi
    else
        # Fall back to pip
        if python3 -m pip install --user pipx; then
            export PATH="$HOME/.local/bin:$PATH"
            python3 -m pipx ensurepath --force > /dev/null 2>&1
            echo -e "${GREEN}[OK]${NC} pipx installed via pip"
        else
            echo -e "${RED}[ERROR]${NC} Failed to install pipx"
            echo "Try installing Homebrew first: https://brew.sh"
            exit 1
        fi
    fi
else
    echo -e "${GREEN}[OK]${NC} pipx found"
fi

# Install cq-tdm with pipx
echo ""
echo "Installing CQ TDM..."
if pipx install cq-tdm --force; then
    echo -e "${GREEN}[OK]${NC} CQ TDM installed"
else
    echo -e "${RED}[ERROR]${NC} Failed to install CQ TDM"
    exit 1
fi

# pipx installs to ~/.local/bin by default
SCRIPT_PATH="$HOME/.local/bin/cq-tdm"

if [ ! -f "$SCRIPT_PATH" ]; then
    SCRIPT_PATH=$(which cq-tdm 2>/dev/null || echo "")
fi

if [ -z "$SCRIPT_PATH" ] || [ ! -f "$SCRIPT_PATH" ]; then
    echo -e "${YELLOW}[WARNING]${NC} Could not find cq-tdm executable."
    echo "Try restarting your terminal and running 'cq-tdm'"
    SCRIPT_PATH="$HOME/.local/bin/cq-tdm"
fi

echo -e "${GREEN}[OK]${NC} Executable: $SCRIPT_PATH"

# Create macOS .app bundle
echo ""
echo "Creating application bundle..."

APP_DIR="$HOME/Applications/CQ TDM.app"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"

mkdir -p "$MACOS_DIR"
mkdir -p "$RESOURCES_DIR"

# Download icon from GitHub
ICON_URL="https://raw.githubusercontent.com/lammour/cq-tdm/main/src/cq_tdm/assets/icon.png"
ICON_DEST="$RESOURCES_DIR/icon.png"

if curl -sL "$ICON_URL" -o "$ICON_DEST" 2>/dev/null; then
    echo -e "${GREEN}[OK]${NC} Icon downloaded"

    # Convert PNG to ICNS if sips is available
    if command -v sips &> /dev/null; then
        ICONSET_DIR="$RESOURCES_DIR/icon.iconset"
        mkdir -p "$ICONSET_DIR"
        sips -z 16 16 "$ICON_DEST" --out "$ICONSET_DIR/icon_16x16.png" 2>/dev/null
        sips -z 32 32 "$ICON_DEST" --out "$ICONSET_DIR/icon_16x16@2x.png" 2>/dev/null
        sips -z 32 32 "$ICON_DEST" --out "$ICONSET_DIR/icon_32x32.png" 2>/dev/null
        sips -z 64 64 "$ICON_DEST" --out "$ICONSET_DIR/icon_32x32@2x.png" 2>/dev/null
        sips -z 128 128 "$ICON_DEST" --out "$ICONSET_DIR/icon_128x128.png" 2>/dev/null
        sips -z 256 256 "$ICON_DEST" --out "$ICONSET_DIR/icon_128x128@2x.png" 2>/dev/null
        sips -z 256 256 "$ICON_DEST" --out "$ICONSET_DIR/icon_256x256.png" 2>/dev/null
        sips -z 512 512 "$ICON_DEST" --out "$ICONSET_DIR/icon_256x256@2x.png" 2>/dev/null
        sips -z 512 512 "$ICON_DEST" --out "$ICONSET_DIR/icon_512x512.png" 2>/dev/null
        sips -z 1024 1024 "$ICON_DEST" --out "$ICONSET_DIR/icon_512x512@2x.png" 2>/dev/null
        iconutil -c icns "$ICONSET_DIR" -o "$RESOURCES_DIR/icon.icns" 2>/dev/null
        rm -rf "$ICONSET_DIR"
        ICON_FILE="icon.icns"
    else
        ICON_FILE="icon.png"
    fi
else
    echo -e "${YELLOW}[NOTE]${NC} Could not download icon"
    ICON_FILE=""
fi

# Create launcher script
cat > "$MACOS_DIR/cq-tdm-launcher" << EOF
#!/bin/bash
export PATH="\$HOME/.local/bin:\$PATH"
exec "$SCRIPT_PATH" "\$@"
EOF
chmod +x "$MACOS_DIR/cq-tdm-launcher"

# Create Info.plist
cat > "$CONTENTS_DIR/Info.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>CQ TDM</string>
    <key>CFBundleDisplayName</key>
    <string>CQ TDM</string>
    <key>CFBundleIdentifier</key>
    <string>com.cq-tdm.app</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleExecutable</key>
    <string>cq-tdm-launcher</string>
    <key>CFBundleIconFile</key>
    <string>$ICON_FILE</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.13</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
EOF

echo -e "${GREEN}[OK]${NC} Application bundle created: $APP_DIR"

echo ""
echo "========================================"
echo "  Installation complete!"
echo "========================================"
echo ""
echo "You can now:"
echo "  - Open 'CQ TDM' from ~/Applications"
echo "  - Run 'cq-tdm' from the terminal"
echo ""

# Check if ~/.local/bin is in PATH
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo -e "${YELLOW}[NOTE]${NC} ~/.local/bin is not in your PATH"
    echo "      Restart your terminal or add to your shell profile:"
    echo "      export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
fi

echo "To uninstall later:"
echo "  pipx uninstall cq-tdm"
echo "  rm -rf \"$APP_DIR\""
echo ""
