# CQ TDM

Application de bureau multiplateforme pour réaliser le **CQI (Contrôle Qualité Interne)** des scanners CT, conforme aux exigences réglementaires de l'ANSM.

## Présentation

CQ TDM traite les images DICOM de fantômes d'eau pour calculer et suivre les métriques de qualité des scanners CT dans le cadre du contrôle qualité interne, conformément à la Décision ANSM du 18/12/2025 (*Décision fixant les modalités du contrôle de qualité des tomodensitomètres*).

## Fonctionnalités

- **Chargement d'images DICOM** : Charger et visualiser des séries DICOM CT
- **Analyse du fantôme d'eau** :
  - Exactitude et stabilité du nombre CT de l'eau
  - Mesures d'uniformité
  - Bruit (écart-type)
- **Spectre de Puissance du Bruit (SPB/NPS)** :
  - Calcul SPB 2D avec moyennage radial
  - Calcul de la fréquence moyenne
  - Ajustement polynomial de degré 11
  - Comparaison visuelle des spectres
- **Gestion des ROI** :
  - Détection automatique du fantôme
  - Import/export des configurations ROI (JSON)
- **Génération de rapports PDF** : Rapports conformes à la réglementation avec statut de conformité
- **Base de données des appareils** : Suivi de plusieurs scanners CT et de leur historique CQ

## Cadre réglementaire

Basé sur la Décision ANSM du 18/12/2025.

**Avertissement** : L'auteur ne garantit pas la validité des résultats. L'utilisateur est responsable de la vérification et de la validation des mesures avant toute utilisation réglementaire.

### Tolérances clés
| Métrique | Critère |
|----------|---------|
| Exactitude du nombre CT | [-7, +7] HU |
| Uniformité | [-7, +7] HU |
| Stabilité du bruit | ±10% ou ±0.2 HU |
| Stabilité de la fréquence moyenne SPB | ±10% |

## Installation

### Méthode 1 : Script d'installation (recommandée)

**Windows :**
1. Téléchargez [`install-windows.bat`](https://raw.githubusercontent.com/lammour/cq-tdm/main/installers/install-windows.bat)
2. Double-cliquez pour exécuter

**Linux :**
1. Téléchargez le script d'installation :
   ```bash
   curl -O https://raw.githubusercontent.com/lammour/cq-tdm/main/installers/install-linux.sh
   ```
2. Rendez le script exécutable :
   ```bash
   chmod +x install-linux.sh
   ```
3. Exécutez le script :
   ```bash
   ./install-linux.sh
   ```

**macOS :**
1. Téléchargez le script d'installation :
   ```bash
   curl -O https://raw.githubusercontent.com/lammour/cq-tdm/main/installers/install-macos.sh
   ```
2. Rendez le script exécutable :
   ```bash
   chmod +x install-macos.sh
   ```
3. Exécutez le script :
   ```bash
   ./install-macos.sh
   ```

Le script installe l'application et crée un raccourci dans le menu Démarrer/Applications (ou ~/Applications sur macOS).

### Méthode 2 : pip / pipx

**Avec pip :**
```bash
pip install cq-tdm
```

**Avec pipx (recommandé) :**

[pipx](https://pipx.pypa.io/) installe les applications Python dans des environnements isolés, évitant les conflits de dépendances.

1. Installez pipx si ce n'est pas déjà fait :
   ```bash
   # Linux (Debian/Ubuntu)
   sudo apt install pipx

   # macOS (Homebrew)
   brew install pipx

   # Ou via pip
   pip install --user pipx
   pipx ensurepath
   ```
2. Installez CQ TDM :
   ```bash
   pipx install cq-tdm
   ```

### Méthode 3 : Téléchargement direct

Des exécutables pré-compilés sont disponibles dans les [Releases](https://github.com/lammour/cq-tdm/releases).

> **Note Windows :** L'exécutable n'est pas signé numériquement. Windows affichera un avertissement de sécurité. Cliquez sur "Informations complémentaires" → "Exécuter quand même" pour continuer.

### Installation pour le développement

```bash
# Cloner le dépôt
git clone https://github.com/lammour/cq-tdm.git
cd cq-tdm

# Créer et activer l'environnement virtuel
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# ou
.venv\Scripts\activate     # Windows

# Installer en mode développement
pip install -e .
```

## Utilisation

```bash
# Lancer l'application
python -m cq_tdm
# ou
cq-tdm
```

### Démarrage rapide

1. Lancer l'application
2. Charger une série DICOM
3. Modifier les détails de l'appareil, les valeurs de référence et les coupes de mesure
4. Sauvegarder
5. Vérifier les artéfacts
6. Générer le rapport PDF

## Structure du projet

```
src/cq_tdm/
├── core/
│   ├── dicom_loader.py    # Gestion des fichiers DICOM
│   ├── water_phantom.py   # Analyse HU et uniformité
│   ├── nps.py             # Spectre de Puissance du Bruit
│   └── device_database.py # Suivi des scanners
├── gui/
│   ├── main_window.py     # Fenêtre principale
│   └── image_viewer.py    # Visualiseur DICOM avec ROI
└── reports/
    └── pdf_report.py      # Génération de rapports PDF
```

## Tests

```bash
# Lancer les tests de validation SPB
pytest tests/test_nps_validation.py -v
```

La suite de tests valide les calculs SPB par rapport aux données de référence iQMetrix (images de référence ANSM).

## Références

- Décision ANSM 18/12/2025 - Contrôle de qualité des tomodensitomètres
- Guide d'application ANSM
