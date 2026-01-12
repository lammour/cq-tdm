# CQ TDM

Logiciel d'analyse des images du fantôme d'eau pour le contrôle qualité des tomodensitomètres.

## Présentation

CQ TDM (ou cq-tdm) analyse les images DICOM des fantômes d'eau pour évaluer le nombre CT de l'eau, l'uniformité, la magnitude et le spectre de puissance du bruit et les artefacts. Le logiciel a pour objectif de suivre la décision ANSM du 18/12/2025 fixant les modalités du contrôle de qualité des tomodensitomètres. Le calcul du spectre de puissance du bruit a pour objectif de se rapprocher au plus près de la méthode utilisée par [IQMetrix-CT](https://github.com/SFPM/iQMetrix-CT) et des résultats de référence disponibles sur le site de l'ANSM.

![CQ TDM - Capture d'écran](docs/screenshot.png)

## Fonctionnalités

- **Chargement d'images DICOM** : Charger et visualiser des séries DICOM CT
- **Analyse du fantôme d'eau** :
  - Nombre CT de l'eau
  - Uniformité
  - Constance de la magnitude du bruit
- **Spectre de Puissance du Bruit (SPB/NPS)** :
  - Calcul du SPB 1D avec ajustement polynomial de degré 11
  - Calcul de la fréquence moyenne
  - Affichage du spectre
- **Gestion des ROI** :
  - Détection automatique du fantôme et placement des ROI
  - Export des ROI (JSON, compatible avec IQMetrix-CT)
- **Génération de rapports PDF**
- **Base de données des appareils** :
  - Enregistrement des valeurs de référence (magnitude du bruit et fréquence moyenne du SPB) et des informations d'identification
  - Détection automatique des appareils enregistrés
  - Possibilité d'utiliser une base de données commune et en réseau entre plusieurs postes

## Cadre réglementaire

**Avertissement** : L'auteur ne garantit pas la conformité avec la décision ANSM du 18/12/2025, ni la validité des résultats. L'utilisateur est responsable de la vérification et de la validation des mesures avant toute utilisation réglementaire.

L'auteur s'efforce à ne pas changer les méthodes de calcul entre chaque version majeure du logiciel. Une attention à la reproductibilité des résultats doit tout de même être maintenue par l'utilisateur à chaque mise à jour.

Un protocole de test automatisé est disponible avec le code source du logiciel, celui-ci compare les résultats du calcul de la fréquence moyenne du SPB avec les références fournies par l'ANSM :

```bash
# Lancer les tests de validation SPB
pytest tests/test_nps_validation.py -v
```


## Installation

Plusieurs méthodes d'installation sont disponibles, décrites ici par ordre de recommandation. Les trois méthodes peuvent être réalisées sur un poste de travail sans droits administrateur.

### Méthode 1 : Script d'installation

**Windows :**
1. S'il n'est pas déjà installé sur votre poste, installez [Python, version 3.10 ou supérieure](https://www.python.org/downloads/). Au cours de l'installation vérifiez que la case "Add Python to PATH" est cochée.
2. Téléchargez [`install-windows.bat`](https://raw.githubusercontent.com/lammour/cq-tdm/main/installers/install-windows.bat) *(clic droit sur le lien → "Enregistrer le lien sous...")*
3. Double-cliquez sur `install-windows.bat` pour l'exécuter

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

**macOS : (non testé !!!)**
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

Le script installe l'application sous le nom **CQ TDM** et crée un raccourci dans le menu Démarrer/Applications (ou ~/Applications sur macOS).

### Méthode 2 : pip / pipx

1. Installez pip ou [pipx](https://pipx.pypa.io/) (recommandé)

2. Si vous utilisez pip, la création d'un environnement virtuel est fortement recommandée

3. Installez le logiciel :
  - Avec pip :
  ```bash
  pip install cq-tdm
  ```
  - Avec pipx :
  ```bash
  pipx install cq-tdm
  ```

### Méthode 3 : Téléchargement direct des exécutables

Des exécutables pré-compilés sont disponibles :

| Plateforme | Téléchargement |
|------------|----------------|
| Windows | [CQ_TDM-windows-x86_64.exe](https://github.com/lammour/cq-tdm/releases/latest/download/CQ_TDM-windows-x86_64.exe) |
| Linux | [CQ_TDM-linux-x86_64](https://github.com/lammour/cq-tdm/releases/latest/download/CQ_TDM-linux-x86_64) |
| macOS (Apple Silicon) | [CQ_TDM-macos-arm64](https://github.com/lammour/cq-tdm/releases/latest/download/CQ_TDM-macos-arm64) |
| macOS (Intel) | [CQ_TDM-macos-x86_64](https://github.com/lammour/cq-tdm/releases/latest/download/CQ_TDM-macos-x86_64) |

> **Note pour Windows :** L'exécutable n'est pas signé numériquement. Windows affichera un avertissement de sécurité. Cliquez sur "Informations complémentaires" → "Exécuter quand même" pour continuer.

## Installation pour le développement

Testé sous Ubuntu 24.04.3 LTS avec Python 3.12.

```bash
# Cloner le dépôt
git clone https://github.com/lammour/cq-tdm.git
cd cq-tdm

# Créer et activer l'environnement virtuel
python -m venv .venv
source .venv/bin/activate

# Installer en mode développement
pip install -e .
```

## Utilisation

1. Lancer l'application
2. Charger une série DICOM
3. Modifier les détails de l'appareil, les valeurs de référence et les coupes de mesure
4. Enregistrer
5. Vérifier les artéfacts
6. Générer le rapport PDF
