#!/bin/bash
# Installation de CQ TDM sous Linux (pipx + entrée de menu).
# Le script gère lui-même ses erreurs : pas de "set -e".
set -u -o pipefail

echo ""
echo "========================================"
echo "  Installation de CQ TDM pour Linux"
echo "========================================"
echo ""

# Couleurs
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
note()  { echo -e "${YELLOW}[NOTE]${NC} $*"; }
error() { echo -e "${RED}[ERREUR]${NC} $*"; }

# Python 3.10 ou plus récent
if ! command -v python3 &> /dev/null; then
    error "Python 3 est introuvable."
    echo "Installez Python 3.10 ou plus récent avec le gestionnaire de paquets :"
    echo "  Ubuntu/Debian : sudo apt install python3 python3-venv pipx"
    echo "  Fedora :        sudo dnf install python3 pipx"
    echo "  Arch :          sudo pacman -S python python-pipx"
    exit 1
fi

PYVER=$(python3 --version 2>&1 | cut -d' ' -f2)
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    error "Python $PYVER est trop ancien : CQ TDM demande Python 3.10 ou plus récent."
    exit 1
fi
ok "Python $PYVER"

# pipx : paquet de la distribution de préférence. Sur les distributions
# récentes (Debian 12, Ubuntu 23.04 et suivantes), "pip install --user" est
# refusé pour le Python du système (PEP 668).
if ! command -v pipx &> /dev/null; then
    echo ""
    echo "pipx est introuvable : tentative d'installation…"
    if python3 -m pip install --user pipx &> /dev/null; then
        export PATH="$HOME/.local/bin:$PATH"
        python3 -m pipx ensurepath --force > /dev/null 2>&1
        ok "pipx installé"
    else
        error "pipx n'a pas pu être installé automatiquement."
        echo "Installez-le avec le gestionnaire de paquets, puis relancez ce script :"
        echo "  Ubuntu/Debian : sudo apt install pipx"
        echo "  Fedora :        sudo dnf install pipx"
        echo "  Arch :          sudo pacman -S python-pipx"
        exit 1
    fi
else
    ok "pipx"
fi

# Installation de cq-tdm
echo ""
echo "Installation de CQ TDM…"
if pipx install cq-tdm --force; then
    ok "CQ TDM installé"
else
    error "L'installation de CQ TDM a échoué."
    exit 1
fi

# pipx installe dans ~/.local/bin par défaut
SCRIPT_PATH="$HOME/.local/bin/cq-tdm"

if [ ! -f "$SCRIPT_PATH" ]; then
    SCRIPT_PATH=$(command -v cq-tdm 2>/dev/null || echo "")
fi

if [ -z "$SCRIPT_PATH" ] || [ ! -f "$SCRIPT_PATH" ]; then
    note "L'exécutable cq-tdm est introuvable."
    echo "       Rouvrez le terminal puis lancez « cq-tdm »."
    SCRIPT_PATH="$HOME/.local/bin/cq-tdm"
fi

ok "Exécutable : $SCRIPT_PATH"

mkdir -p "$HOME/.local/share/icons"
mkdir -p "$HOME/.local/share/applications"

# Icône, téléchargée depuis GitHub
ICON_DEST="$HOME/.local/share/icons/cq-tdm.png"
ICON_URL="https://raw.githubusercontent.com/lammour/cq-tdm/main/src/cq_tdm/assets/icon.png"

echo ""
echo "Téléchargement de l'icône…"
if curl -fsL "$ICON_URL" -o "$ICON_DEST" 2>/dev/null || wget -q "$ICON_URL" -O "$ICON_DEST" 2>/dev/null; then
    ok "Icône installée"
    ICON_PATH="$ICON_DEST"
else
    note "Icône non téléchargée : icône par défaut utilisée"
    ICON_PATH="utilities-system-monitor"
fi

# Entrée du menu des applications
echo ""
echo "Création de l'entrée du menu des applications…"

DESKTOP_FILE="$HOME/.local/share/applications/cq-tdm.desktop"

cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=CQ TDM
GenericName=Contrôle de qualité des tomodensitomètres
Comment=Contrôle de qualité interne des tomodensitomètres
Exec=$SCRIPT_PATH
Icon=$ICON_PATH
Terminal=false
Categories=Science;Medical;
Keywords=CT;scanner;tomodensitomètre;DICOM;qualité;contrôle;radiologie;
EOF

chmod +x "$DESKTOP_FILE"
ok "Entrée du menu créée"

if command -v update-desktop-database &> /dev/null; then
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null
fi

# Raccourci sur le bureau
DESKTOP_DIR=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")
DESKTOP_SHORTCUT="$DESKTOP_DIR/cq-tdm.desktop"
if [ -f "$DESKTOP_SHORTCUT" ]; then
    # Mise à jour d'un raccourci existant (versions précédentes)
    cp "$DESKTOP_FILE" "$DESKTOP_SHORTCUT"
    chmod +x "$DESKTOP_SHORTCUT"
    gio set "$DESKTOP_SHORTCUT" metadata::trusted true 2>/dev/null
    ok "Raccourci du bureau mis à jour"
elif [ -d "$DESKTOP_DIR" ] && [ -r /dev/tty ]; then
    # La question est lue sur le terminal, pas sur l'entrée standard : le
    # script fonctionne aussi lancé par "curl … | bash"
    echo ""
    read -r -p "Créer un raccourci sur le bureau ? (o/N) : " DESKTOP_CHOICE < /dev/tty || DESKTOP_CHOICE=""
    if [[ "$DESKTOP_CHOICE" =~ ^[OoYy]$ ]]; then
        cp "$DESKTOP_FILE" "$DESKTOP_SHORTCUT" 2>/dev/null
        chmod +x "$DESKTOP_SHORTCUT" 2>/dev/null
        # Raccourci approuvé sous GNOME
        gio set "$DESKTOP_SHORTCUT" metadata::trusted true 2>/dev/null
        ok "Raccourci du bureau créé"
    fi
fi

echo ""
echo "========================================"
echo "  Installation terminée"
echo "========================================"
echo ""
echo "CQ TDM se lance :"
echo "  - depuis le menu des applications (« CQ TDM ») ;"
echo "  - depuis un terminal, avec la commande « cq-tdm »."
echo ""

if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    note "~/.local/bin n'est pas dans le PATH."
    echo "       Rouvrez le terminal, ou lancez : source ~/.bashrc"
    echo ""
fi

echo "Pour désinstaller : pipx uninstall cq-tdm"
echo ""
