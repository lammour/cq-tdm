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
    echo "  Ubuntu/Debian: sudo apt install python3 python3-pip python3-venv"
    echo "  Fedora: sudo dnf install python3 python3-pip"
    echo "  Arch: sudo pacman -S python python-pip"
    exit 1
fi

PYVER=$(python3 --version 2>&1 | cut -d' ' -f2)
echo -e "${GREEN}[OK]${NC} Python $PYVER found"

# Check/install pipx
if ! command -v pipx &> /dev/null; then
    echo ""
    echo "pipx not found. Installing pipx..."

    # Check pip first
    if ! python3 -m pip --version &> /dev/null; then
        echo -e "${RED}[ERROR]${NC} pip not found."
        echo "Please install pip: sudo apt install python3-pip"
        exit 1
    fi

    # Install pipx
    if python3 -m pip install --user pipx; then
        # Add to PATH for this session
        export PATH="$HOME/.local/bin:$PATH"
        # Ensure pipx paths are set up
        python3 -m pipx ensurepath --force > /dev/null 2>&1
        echo -e "${GREEN}[OK]${NC} pipx installed"
    else
        echo -e "${RED}[ERROR]${NC} Failed to install pipx"
        exit 1
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

# Create icons directory
mkdir -p "$HOME/.local/share/icons"
mkdir -p "$HOME/.local/share/applications"

# Download icon from GitHub
ICON_DEST="$HOME/.local/share/icons/cq-tdm.png"
ICON_URL="https://raw.githubusercontent.com/lammour/cq-tdm/main/src/cq_tdm/assets/icon.png"

echo ""
echo "Downloading icon..."
if curl -sL "$ICON_URL" -o "$ICON_DEST" 2>/dev/null || wget -q "$ICON_URL" -O "$ICON_DEST" 2>/dev/null; then
    echo -e "${GREEN}[OK]${NC} Icon installed"
    ICON_PATH="$ICON_DEST"
else
    echo -e "${YELLOW}[NOTE]${NC} Could not download icon, using default"
    ICON_PATH="utilities-system-monitor"
fi

# Create .desktop file
echo ""
echo "Creating application menu entry..."

DESKTOP_FILE="$HOME/.local/share/applications/cq-tdm.desktop"

cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=CQ TDM
GenericName=CT Quality Control
Comment=CT Scanner Internal Quality Control Software
Exec=$SCRIPT_PATH
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

# Handle desktop shortcut
DESKTOP_SHORTCUT="$HOME/Desktop/cq-tdm.desktop"
if [ -f "$DESKTOP_SHORTCUT" ]; then
    # Update existing shortcut (handles upgrades from older versions)
    echo "Updating existing Desktop shortcut..."
    cp "$DESKTOP_FILE" "$DESKTOP_SHORTCUT"
    chmod +x "$DESKTOP_SHORTCUT"
    gio set "$DESKTOP_SHORTCUT" metadata::trusted true 2>/dev/null
    echo -e "${GREEN}[OK]${NC} Desktop shortcut updated"
else
    # Ask about creating new shortcut
    echo ""
    read -p "Create Desktop shortcut? (y/N): " DESKTOP_CHOICE
    if [[ "$DESKTOP_CHOICE" =~ ^[Yy]$ ]]; then
        cp "$DESKTOP_FILE" "$DESKTOP_SHORTCUT" 2>/dev/null
        chmod +x "$DESKTOP_SHORTCUT" 2>/dev/null
        # Mark as trusted on GNOME
        gio set "$DESKTOP_SHORTCUT" metadata::trusted true 2>/dev/null
        echo -e "${GREEN}[OK]${NC} Desktop shortcut created"
    fi
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
    echo "      Restart your terminal or run:"
    echo "      source ~/.bashrc"
    echo ""
fi

echo "To uninstall later: pipx uninstall cq-tdm"
echo ""
