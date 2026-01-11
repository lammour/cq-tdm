#!/bin/bash

echo ""
echo "========================================"
echo "  CQ TDM Installer for Linux"
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
    echo "Please install Python 3.10+ using your package manager:"
    echo "  Ubuntu/Debian: sudo apt install python3 python3-pip"
    echo "  Fedora: sudo dnf install python3 python3-pip"
    echo "  Arch: sudo pacman -S python python-pip"
    exit 1
fi

PYVER=$(python3 --version 2>&1 | cut -d' ' -f2)
echo -e "${GREEN}[OK]${NC} Python $PYVER found"

# Check pip
if ! command -v pip3 &> /dev/null && ! python3 -m pip --version &> /dev/null; then
    echo -e "${RED}[ERROR]${NC} pip not found."
    echo "Please install pip for Python 3"
    exit 1
fi

# Install cq-tdm
echo ""
echo "Installing CQ TDM..."
if python3 -m pip install --user --upgrade cq-tdm; then
    echo -e "${GREEN}[OK]${NC} CQ TDM installed"
else
    echo -e "${RED}[ERROR]${NC} Failed to install CQ TDM"
    exit 1
fi

# Find the executable
SCRIPT_PATH=$(python3 -c "import site; print(site.getusersitepackages().replace('lib/python3.', 'bin').rsplit('/', 1)[0])")/bin/cq-tdm
if [ ! -f "$SCRIPT_PATH" ]; then
    SCRIPT_PATH="$HOME/.local/bin/cq-tdm"
fi
if [ ! -f "$SCRIPT_PATH" ]; then
    SCRIPT_PATH=$(which cq-tdm 2>/dev/null || echo "")
fi

if [ -z "$SCRIPT_PATH" ] || [ ! -f "$SCRIPT_PATH" ]; then
    echo -e "${YELLOW}[WARNING]${NC} Could not find cq-tdm executable automatically."
    echo "You can still run the app with: python3 -m cq_tdm"
    SCRIPT_PATH="python3 -m cq_tdm"
    USE_PYTHON_MODULE=1
fi

echo -e "${GREEN}[OK]${NC} Executable: $SCRIPT_PATH"

# Determine icon path
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ICON_SOURCE="$SCRIPT_DIR/../assets/icon.png"
ICON_DEST="$HOME/.local/share/icons/cq-tdm.png"

# Create icons directory if needed
mkdir -p "$HOME/.local/share/icons"
mkdir -p "$HOME/.local/share/applications"

# Copy icon if exists
if [ -f "$ICON_SOURCE" ]; then
    cp "$ICON_SOURCE" "$ICON_DEST"
    echo -e "${GREEN}[OK]${NC} Icon installed"
    ICON_PATH="$ICON_DEST"
else
    echo -e "${YELLOW}[NOTE]${NC} No icon file found at $ICON_SOURCE"
    echo "      Using default icon. Add icon.png to assets/ for custom icon."
    ICON_PATH="utilities-system-monitor"
fi

# Create .desktop file
echo ""
echo "Creating application menu entry..."

DESKTOP_FILE="$HOME/.local/share/applications/cq-tdm.desktop"

if [ "$USE_PYTHON_MODULE" = "1" ]; then
    EXEC_LINE="python3 -m cq_tdm"
else
    EXEC_LINE="$SCRIPT_PATH"
fi

cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=CQ TDM
GenericName=CT Quality Control
Comment=CT Scanner Quality Control Software (Controle Qualite Tomodensitometrie)
Exec=$EXEC_LINE
Icon=$ICON_PATH
Terminal=false
Categories=Science;Medical;
Keywords=CT;scanner;DICOM;quality;control;radiology;
EOF

chmod +x "$DESKTOP_FILE"
echo -e "${GREEN}[OK]${NC} Application menu entry created"

# Update desktop database
if command -v update-desktop-database &> /dev/null; then
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null
fi

# Ask about desktop shortcut
echo ""
read -p "Create Desktop shortcut? (y/N): " DESKTOP_CHOICE
if [[ "$DESKTOP_CHOICE" =~ ^[Yy]$ ]]; then
    cp "$DESKTOP_FILE" "$HOME/Desktop/cq-tdm.desktop" 2>/dev/null
    chmod +x "$HOME/Desktop/cq-tdm.desktop" 2>/dev/null
    # Mark as trusted on GNOME
    gio set "$HOME/Desktop/cq-tdm.desktop" metadata::trusted true 2>/dev/null
    echo -e "${GREEN}[OK]${NC} Desktop shortcut created"
fi

echo ""
echo "========================================"
echo "  Installation complete!"
echo "========================================"
echo ""
echo "You can now:"
echo "  - Find 'CQ TDM' in your application menu"
echo "  - Run 'cq-tdm' from the terminal"
echo ""

# Check if ~/.local/bin is in PATH
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo -e "${YELLOW}[NOTE]${NC} ~/.local/bin is not in your PATH"
    echo "      Add this to your ~/.bashrc or ~/.zshrc:"
    echo "      export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
fi
