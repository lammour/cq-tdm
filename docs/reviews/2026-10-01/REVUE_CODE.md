# Revue de code — CQ TDM (date 2026-10-01, commit 0c60fae)

## 1. Résumé exécutif

Le code est globalement propre, bien commenté et le cœur métier (`core/`) est découpé de façon lisible ; la persistance est atomique (fichier temporaire + `replace`) et les critères ANSM sont centralisés dans `qc_history.py`, ce qui est une bonne base. En revanche la base `devices.json` **partagée entre plusieurs postes** (fonctionnalité annoncée dans le README, l. 40) n'est pas sûre : pas de verrou, pas de relecture avant écriture, chargement tout-ou-rien, et un partage réseau momentanément indisponible conduit à écraser silencieusement la base. Le chargement DICOM accepte sans filtrage des images hétérogènes (localizer, séries mélangées) et une identité DICOM vide fait fusionner des installations. Il n'y a **aucune CI de test/lint** (ruff : 312 violations ; sans `test_data/`, 22 tests sont en ERROR au lieu d'être ignorés).

Constats : **3 Critiques, 11 Majeurs, 25 Mineurs, 8 Suggestions** (47 au total).

Priorités : (1) C-01/C-02/C-03 — fiabiliser `DeviceDatabase` (relecture + fusion avant écriture, verrou de fichier, distinction « fichier absent / partage inaccessible », chargement tolérant run par run) ; (2) C-04/C-05/C-06 — filtrer la série chargée (UID, SOP class) et refuser une identité DICOM vide ou un `PixelSpacing` nul ; (3) C-08/C-14 — ajouter un workflow CI `pytest + ruff` et transformer l'assert de `conftest.py` en `skip` ; (4) C-13 — ne pas effacer l'action corrective lors du ré-export d'une même série ; (5) C-12 — ne plus dater silencieusement un contrôle du jour de l'analyse quand le DICOM n'a pas de date.

## 2. Méthode

**Fichiers lus en entier** : `src/cq_tdm/main.py`, `__main__.py`, `__init__.py` ; `core/` (`__init__`, `app_config`, `device_database`, `dicom_loader`, `dicom_locator`, `nps`, `qc_history`, `roi_geometry`, `trend_chart`, `utils`, `water_phantom`) ; `gui/` (`main_window.py` 3600 l., `image_viewer.py` 1938 l., `history_panel.py`, `theme.py`) ; `reports/pdf_report.py` (1586 l.) et `reports/__init__.py` ; `tests/*.py` et `conftest.py` ; `pyproject.toml`, `cq_tdm.spec`, `installers/*`, `.github/workflows/*.yml`, `scripts/package-test-data.sh`, `.gitignore`.

**Outils exécutés** (Python 3.11.15, pydicom 3.0.2, PySide6 6.11.2, numpy 2.4.6, reportlab 5.0.1, matplotlib 3.11.2) :

- `ruff check src tests` → **312 erreurs** : E501 ×160 (lignes > 100), UP045 ×58 (`Optional[X]` → `X | None`), I001 ×32 (imports non triés), N802 ×25 (surcharges Qt `mousePressEvent`…), F401 ×10 (imports inutilisés : 7 dans `core/__init__.py`, `QGridLayout` dans `main_window.py:16`, `Path` dans `tests/test_phantom_detection.py:8`, `DicomImage` dans `tests/test_roi_geometry.py:9`), N814 ×9 et N816/N813 (alias reportlab dans `pdf_report.py:28-65`), N806 ×6, E702 ×4 (`history_panel.py:239,243`), UP015 ×2, E402 ×1 (`main_window.py:88`), F841 ×1 (`tests/test_nps_validation.py:271`), UP035/UP037. 97 corrigeables par `--fix`.
- `ruff format --check src tests` → **25 fichiers seraient reformatés**, 6 déjà conformes.
- `python -m compileall -q src tests` → OK.
- `QT_QPA_PLATFORM=offscreen pytest -q` → premier essai en **INTERNALERROR** (`ImportError: libEGL.so.1`) : pytest-qt importe `QtGui` dès `pytest_configure`, donc toute la session tombe si les bibliothèques système Qt manquent (à prévoir dans un futur workflow CI : `apt-get install libegl1`). Après installation : **70 passed, 10 skipped, 22 errors en 3.9 s**. Les 22 ERROR sont tous dus à `tests/conftest.py:18` (`assert TEST_DATA_DIR.exists()`) pour `test_nps_validation.py` ; les 10 SKIP sont `TestAnsmSeries` (correctement marqués `skipif`).
- Scripts d'essai dans `scratchpad/code/` : `probe_dicom.py` (en-têtes DICOM atypiques), `probe_db.py` (base partagée, corruption), `probe_pdf.py` (caractères `<`/`&` dans les textes libres — **non reproduit** : reportlab 5 les tolère, aucun constat retenu).

**Limites** : pas d'exécution sous Windows (chemins UNC, `os.replace` sur fichier ouvert), pas de données DICOM réelles ni de `test_data/` ANSM, GUI testée uniquement en offscreen, pas d'accès API GitHub depuis cette session pour vérifier l'existence des versions d'actions épinglées (`@v7`, `@v8`).

## 3. Constats

### [C-01] Base `devices.json` partagée : perte de mises à jour entre postes (lost update)
- **Sévérité** : Critique
- **Fichier** : `src/cq_tdm/core/device_database.py:176-195` (`DeviceDatabase._load`), `:205-217` (`_save`), `:250-266` (`add_run`)
- **Constat** : le fichier est lu **une seule fois** dans `__init__` puis chaque opération réécrit l'intégralité de l'état mémoire (`_save`). Il n'y a ni verrou de fichier, ni relecture/fusion avant écriture, ni contrôle de `mtime`. Le nom du fichier temporaire est fixe (`devices.json.tmp`), donc deux écritures simultanées se tronquent mutuellement avant le `replace`.
- **Scénario de défaillance** : poste A ouvre CQ TDM à 9 h, poste B à 9 h 05 (même base réseau). A enregistre un contrôle à 10 h, B un autre à 10 h 30 → le contrôle de A disparaît. Reproduit dans `probe_db.py` : après `a.add_run(A1)` puis `b.add_run(B1)`, le disque ne contient que `['B1']`. De même une installation supprimée sur un poste est « ressuscitée » par la prochaine sauvegarde d'un autre poste.
- **Correction proposée** : dans `_save`, (1) prendre un verrou exclusif (`msvcrt.locking` / `fcntl.flock` sur un fichier `devices.json.lock`, avec timeout et message utilisateur) ; (2) relire le fichier sous verrou et **fusionner** par `device_id` et `run_id` (dernier `recorded_at` gagne, suppressions explicites via un journal ou confirmation) avant d'écrire ; (3) utiliser un nom de fichier temporaire unique (`tempfile.NamedTemporaryFile(dir=db_path.parent, delete=False)`) ; (4) exposer un `reload()` appelé avant toute écriture et au retour au premier plan de la fenêtre. À défaut, documenter clairement que la base ne doit pas être ouverte simultanément.
- **Confiance** : Confirmé (reproduit)

### [C-02] Partage réseau inaccessible au démarrage → base vide silencieuse, puis écrasement de la base réelle
- **Sévérité** : Critique
- **Fichier** : `src/cq_tdm/core/device_database.py:178-179` (`_load`), `src/cq_tdm/gui/main_window.py:1185-1190` (`MainWindow.__init__`)
- **Constat** : `if not self.db_path.exists(): return` ne distingue pas « base jamais créée » de « lecteur réseau/VPN non monté ». `load_error` reste `None`, aucun avertissement, l'application démarre avec zéro installation.
- **Scénario de défaillance** : `device_database_path = \\serveur\cq\devices.json`, VPN non connecté au lancement. L'utilisateur voit une liste vide, recrée son installation, exporte un PDF ; entre-temps le partage est revenu → `_save` écrase la base commune avec une base contenant une seule installation. Reproduit dans `probe_db.py` (cas 3 : après remise en ligne, le disque ne contient que `['other_x_y_z']`).
- **Correction proposée** : quand `config.device_database_path` est renseigné et que le fichier n'existe pas, vérifier `db_path.parent.exists()`/`is_dir()` ; si le dossier est absent, positionner `load_error` (« dossier inaccessible ») et passer la base en **lecture seule** (refuser `_save` avec un message explicite) jusqu'à ce que l'utilisateur choisisse une autre base. Avant toute écriture, si le fichier existe maintenant alors qu'il n'existait pas au chargement, recharger puis fusionner (cf. C-01).
- **Confiance** : Confirmé (reproduit)

### [C-03] Chargement tout-ou-rien : une seule entrée de contrôle invalide masque toutes les installations et expose la base à l'écrasement
- **Sévérité** : Critique
- **Fichier** : `src/cq_tdm/core/device_database.py:181-195` (`_load`), `:74` (`DeviceConfig.from_dict`), `src/cq_tdm/core/qc_history.py:110-113` (`QCRun.from_dict`)
- **Constat** : `QCRun.from_dict` fait `cls(**data)` ; `run_date` est obligatoire, donc un run sans ce champ (fichier édité à la main, version future qui renomme le champ, enregistrement partiel) lève `TypeError`, attrapé globalement dans `_load` → `self._devices = {}`. L'application avertit, crée un `.bak` (qui **écrase** le `.bak` précédent, `:197-203`) et la prochaine sauvegarde réécrit une base vide.
- **Scénario de défaillance** : reproduit dans `probe_db.py` (cas 1) : deux installations valides + un run `{"series_uid": "B", "noise": 3.0}` → `load_error = "QCRun.__init__() missing ... 'run_date'"`, **0 installation visible**.
- **Correction proposée** : rendre le chargement tolérant au niveau de chaque élément : `QCRun.from_dict` fournit `run_date=""` par défaut (ou lève une exception dédiée), `DeviceConfig.from_dict` ignore (en les comptant) les runs irrécupérables, `_load` n'abandonne que si le JSON lui-même est illisible ; remonter un `load_warnings: list[str]` affiché à l'utilisateur. Horodater les `.bak` (`devices.json.2026-10-01T10-00.bak`) et en garder plusieurs.
- **Confiance** : Confirmé (reproduit)

### [C-04] Aucun filtrage de la série chargée (UID, type d'image, dimensions)
- **Sévérité** : Majeur
- **Fichier** : `src/cq_tdm/core/dicom_loader.py:332-374` (`load_dicom_folder`), `:224-248` (`load_dicom_file`), `src/cq_tdm/core/nps.py:565` (`analyze_nps`)
- **Constat** : tout fichier lisible avec un `pixel_array` 2D est ajouté à la série : topogramme (localizer), capture secondaire monochrome (rapport de dose), coupes d'une autre série du même dossier, images de matrice différente. Aucune vérification de `SeriesInstanceUID`, `SOPClassUID`, `ImageType` (`LOCALIZER`), `Modality`, ni d'homogénéité `Rows/Columns/PixelSpacing`. `analyze_nps` applique le `pixel_size_mm` de la coupe médiane à toutes les coupes.
- **Scénario de défaillance** : export PACS d'une étude complète dans un dossier (topogramme + axiales + rapport de dose MONOCHROME2). Le topogramme (SliceLocation 0 ou absent) se place en tête ou au milieu de la liste triée ; la plage SPB par défaut peut l'inclure → ROI hors fantôme, spectre faux ou `ValueError` attrapée en silence ; le `series_uid` enregistré dans l'historique est celui de la coupe UH et ne correspond pas aux coupes SPB.
- **Correction proposée** : dans `load_dicom_folder`, regrouper par `SeriesInstanceUID`, exclure `ImageType` contenant `LOCALIZER`/`SECONDARY`, exiger `Modality == "CT"` et `SOPClassUID` CT Image Storage (`1.2.840.10008.5.1.4.1.1.2`), puis retenir la série la plus peuplée et remonter `series.warnings` (« 3 images d'une autre série ignorées ») ; vérifier que `rows/columns/pixel_spacing` sont identiques dans la série retenue, sinon lever une erreur explicite.
- **Confiance** : Probable (par lecture ; comportement de tri et d'inclusion vérifié dans le code)

### [C-05] Identité DICOM vide → `device_id == ""` : toutes les installations anonymisées fusionnent
- **Sévérité** : Majeur
- **Fichier** : `src/cq_tdm/core/device_database.py:99-113` (`generate_id`), `:238-247` (`save_device`), `src/cq_tdm/gui/main_window.py:128-134` (`DeviceManagerDialog._image_device_id`), `:393` (`_refresh_device_list`), `:3514-3519` (`_try_auto_detect_device`)
- **Constat** : si `Manufacturer`, `ManufacturerModelName`, `StationName` et `DeviceSerialNumber` sont vides (séries anonymisées, images ANSM, certains exports), `generate_id` renvoie `""`. `save_device` « régénère » un id vide, `_image_device_id` renvoie `""` (pas `None`) donc le bouton « Nouvelle installation depuis l'image chargée » est actif et crée/écrase l'entrée `""`.
- **Scénario de défaillance** : reproduit (`probe_db.py` bis) : deux `DeviceConfig.from_dicom("", "", "", "")` sauvegardés → **1 seule installation**, `find_device("", "", "", "")` renvoie la dernière (« CHU B »). Toute série anonymisée est ensuite « reconnue » comme cette installation, avec ses valeurs de référence et sa géométrie figée.
- **Correction proposée** : `generate_id` renvoie `""` → `save_device` lève `ValueError("identité DICOM vide")` ou génère `f"manual-{uuid4().hex[:8]}"` ; `_image_device_id` renvoie `None` quand l'id est vide ; `_try_auto_detect_device`/`find_device` ne tentent pas de reconnaissance sur une identité vide et affichent « Installation inconnue (série anonymisée) ».
- **Confiance** : Confirmé (reproduit)

### [C-06] `PixelSpacing` absent ou nul : 1 mm silencieux ou `ZeroDivisionError`
- **Sévérité** : Majeur
- **Fichier** : `src/cq_tdm/core/dicom_loader.py:271-275` (`load_dicom_file`), `src/cq_tdm/core/roi_geometry.py:295-296` (`ROIGeometry.from_phantom`), `src/cq_tdm/core/nps.py:280-285,323` (`compute_nps_2d`, `radial_average`)
- **Constat** : un `PixelSpacing` absent ou vide donne `(1.0, 1.0)` sans avertissement ; un `PixelSpacing` `[0, 0]` est accepté tel quel. Les distances en mm (ROI périphériques à 12,5 mm de la paroi, tailles mm du PDF, `matches()` de la géométrie figée, fréquences du SPB) deviennent fausses, ou l'analyse plante.
- **Scénario de défaillance** : reproduit (`probe_dicom.py`) : `PixelSpacing=[0,0]` → `ROIGeometry.from_phantom` lève `ZeroDivisionError` (attrapée par la GUI en « Erreur analyse UH » sans explication) ; `PixelSpacing` absent → image chargée avec pixel = 1 mm, ROIs placées comme si le champ faisait 512 mm, PDF indiquant « Taille pixel : 1,000 mm ».
- **Correction proposée** : dans `load_dicom_file`, si `PixelSpacing` est absent, tenter `ImagerPixelSpacing` puis `ReconstructionDiameter / Columns`, sinon lever `ValueError("PixelSpacing manquant")` (le fichier est alors listé dans `load_errors`) ; refuser `<= 0`. Ajouter un garde `if pixel_size_mm <= 0: raise ValueError` dans `ROIGeometry.from_phantom` et `compute_nps_2d`.
- **Confiance** : Confirmé (reproduit)

### [C-07] Images compressées (JPEG Lossless, JPEG 2000, RLE) non lisibles faute de décodeur déclaré
- **Sévérité** : Majeur
- **Fichier** : `pyproject.toml:12-20` (dependencies), `src/cq_tdm/core/dicom_loader.py:245` (`ds.pixel_array`)
- **Constat** : pydicom ne décode pas les transfer syntaxes compressées sans `pylibjpeg`/`gdcm`/`pillow` (JPEG baseline seulement). Aucune de ces dépendances optionnelles n'est déclarée ; le spec PyInstaller ne les embarque pas. Les exports PACS sont fréquemment en JPEG Lossless.
- **Scénario de défaillance** : dossier en JPEG 2000 → chaque fichier échoue avec `RuntimeError: ... unable to decompress` ; la GUI affiche « Aucun fichier DICOM trouvé … Première erreur : … » (`main_window.py:1803-1811`), message peu actionnable.
- **Correction proposée** : ajouter `pydicom[pixeldata]` (ou `pylibjpeg[all]>=2` + `pylibjpeg-libjpeg`, `pylibjpeg-openjpeg`) aux dépendances et aux `hiddenimports` des deux `.spec` ; dans `load_dicom_folder`, détecter `ds.file_meta.TransferSyntaxUID.is_compressed` et produire un message dédié (« série compressée : installer le décodeur / exporter en non compressé »).
- **Confiance** : Probable (comportement pydicom documenté ; non reproduit faute de fichier compressé)

### [C-08] Aucun workflow CI de tests/lint ; `conftest.py` transforme l'absence de données en ERROR
- **Sévérité** : Majeur
- **Fichier** : `.github/workflows/*.yml` (aucun job `pytest`/`ruff`), `tests/conftest.py:15-19` (`test_data_dir`), `:66-79` (`series_dir`, `dicom_dir`), `pyproject.toml:52-57` (`[tool.ruff]`)
- **Constat** : les trois workflows ne tournent qu'à la release ; rien ne s'exécute sur push/PR. `ruff` est configuré mais jamais lancé (312 violations). Sans `test_data/`, les fixtures lèvent `AssertionError` au setup → 22 ERROR (observé) alors que `test_phantom_detection.py:271` utilise correctement `skipif`.
- **Scénario de défaillance** : une régression dans `qc_history.py` ou `device_database.py` passe inaperçue jusqu'à la release ; un contributeur sans données ANSM voit une suite « rouge » et ne distingue plus une vraie régression.
- **Correction proposée** : (1) `conftest.py` : `if not TEST_DATA_DIR.exists(): pytest.skip("ANSM reference series not available", allow_module_level=False)` dans `test_data_dir`, ou un marqueur `@pytest.mark.ansm_data` + `--runansm` ; (2) nouveau workflow `ci.yml` sur `push`/`pull_request` : matrice Python 3.10/3.12/3.13 × ubuntu/windows, `apt-get install -y libegl1 libgl1 libxkbcommon0` sur Ubuntu, `pip install -e .[dev]`, `ruff check`, `ruff format --check`, `QT_QPA_PLATFORM=offscreen pytest -q` ; (3) faire dépendre `build.yml`/`publish.yml` de ce job.
- **Confiance** : Confirmé (exécuté)

### [C-09] Crash au démarrage si le dossier de configuration ne peut pas être créé
- **Sévérité** : Majeur
- **Fichier** : `src/cq_tdm/core/device_database.py:158-163` (`DeviceDatabase.__init__`), à comparer avec `src/cq_tdm/core/app_config.py:100-103` (`AppConfig.config_dir`, protégé)
- **Constat** : `config_dir.mkdir(parents=True, exist_ok=True)` n'est pas protégé ; `AppConfig.config_dir` l'est (« Read-only location: callers handle the failing open() ») mais `MainWindow.__init__` (`main_window.py:1187`) instancie `DeviceDatabase(None)` sans `try`.
- **Scénario de défaillance** : profil itinérant en lecture seule, `%APPDATA%` sur un partage défaillant, ou poste kiosque → `PermissionError`/`FileNotFoundError` dans `MainWindow()` → l'application ne s'ouvre pas (reproduit avec un `QStandardPaths` factice pointant sur `/proc/forbidden`).
- **Correction proposée** : entourer le `mkdir` d'un `try/except OSError`, mémoriser `self.load_error = "dossier de configuration inaccessible"` et laisser `_save` échouer proprement (déjà attrapé dans la GUI par `_persist`).
- **Confiance** : Confirmé (reproduit)

### [C-10] `add_run`/`update_run` sur une installation disparue → `KeyError` non géré depuis la GUI
- **Sévérité** : Majeur
- **Fichier** : `src/cq_tdm/core/device_database.py:255` (`add_run`), `:270` (`update_run`), `src/cq_tdm/gui/main_window.py:2660-2666` (`_record_run_in_history`, n'attrape que `OSError`), `:2778,2790,2913` (`update_run`)
- **Constat** : `self._devices[device_id]` lève `KeyError` si l'installation n'est plus dans la base alors que `MainWindow._current_device` tient encore l'objet (base rechargée par `_switch_database`, suppression sur un autre poste après fusion C-01, ou `DeviceManagerDialog` ayant supprimé l'installation puis `_after_device_manager` tombant sur `find_device` d'une image anonymisée).
- **Scénario de défaillance** : le PDF est généré, puis « Erreur inattendue : KeyError 'siemens_…' » ; le contrôle n'est pas dans l'historique et l'utilisateur ne sait pas pourquoi.
- **Correction proposée** : `add_run`/`update_run` lèvent une `DeviceNotFoundError(LookupError)` explicite ; la GUI l'attrape avec `(OSError, LookupError, ValueError)` et propose de recréer l'installation ; `_record_run_in_history` re-résout `self._device_db.get_device(id)` avant d'écrire.
- **Confiance** : Confirmé (KeyError reproduit) / Probable pour le chemin GUI

### [C-11] `DeviceManagerDialog` : erreurs d'E/S non gérées et `load_error` ignoré lors d'un changement de base
- **Sévérité** : Majeur
- **Fichier** : `src/cq_tdm/gui/main_window.py:688` (`_delete_selected_device` → `delete_device` sans `try`), `:723-724` (`_create_new_database` : `mkdir`/`write_text`), `:803-808` (`_switch_database` : `save_app_config()` puis `DeviceDatabase(...)` dont `load_error` n'est jamais lu), `:556-560` (`_persist` : `save_app_config()` hors du `try`)
- **Constat** : plusieurs écritures disque sont faites sans gestion d'`OSError` (disque plein, dossier en lecture seule, fichier verrouillé par un antivirus) et l'ouverture d'une base illisible via « Ouvrir… » crée silencieusement un `.bak` puis affiche une liste vide (cf. C-03).
- **Scénario de défaillance** : « Ouvrir… » sur un `devices.json` d'une version plus récente → aucune erreur affichée, liste vide, puis « Enregistrer » écrase la base commune.
- **Correction proposée** : après `DeviceDatabase(Path(file_path))`, si `db.load_error` : `QMessageBox.critical` et ne pas basculer ; encapsuler `write_text`, `delete_device`, `save_app_config` dans `try/except OSError` avec message ; faire remonter `save_app_config` dans le `try` de `_persist`.
- **Confiance** : Probable (par lecture)

### [C-12] Un DICOM sans date est daté du jour de l'analyse, sans signalement
- **Sévérité** : Majeur
- **Fichier** : `src/cq_tdm/core/qc_history.py:124-129` (`dicom_date_to_iso`), `src/cq_tdm/gui/main_window.py:2618` (`_current_run_for_history`), `src/cq_tdm/reports/pdf_report.py:531` (`generate_report` : « Contrôle de qualité interne trimestriel du … »), `:1489-1496` (`generate_report_filename`)
- **Constat** : `dicom_date_to_iso("")` renvoie `date.today()`. Le registre (historique, PDF, nom de fichier) reçoit donc une date de contrôle fausse mais plausible dès que `StudyDate`/`SeriesDate`/… sont vides (séries anonymisées, dont les images de référence ANSM).
- **Scénario de défaillance** : série acquise le 15/09 et analysée le 01/10 : `run_date = 2026-10-01`, PDF « Contrôle … du 01/10/2026 », courbe de tendance décalée ; un ré-export le lendemain crée un nouveau `run_id` identique (UID) mais à une autre date.
- **Correction proposée** : `dicom_date_to_iso` renvoie `""` quand la date est inutilisable ; `QCRun.run_date=""` affiché « date inconnue » ; dans `_export_pdf`, si la date est vide, demander la date du contrôle (QDateEdit) avant de générer le rapport et l'enregistrer dans le run.
- **Confiance** : Confirmé (comportement codé et testé dans `test_dicom_date_to_iso`)

### [C-13] Ré-export d'une même série : l'action corrective et les notes antérieures sont effacées
- **Sévérité** : Majeur
- **Fichier** : `src/cq_tdm/core/device_database.py:257-263` (`add_run` : remplacement intégral), `src/cq_tdm/gui/main_window.py:2604-2641` (`_current_run_for_history` : nouvel objet avec `corrective_action=""`)
- **Constat** : le run est identifié par `SeriesInstanceUID` et remplacé en bloc. Les champs saisis après coup (`corrective_action_date`, `corrective_action`, `pdf_path` relié, `notes` d'un export précédent) sont perdus lors d'un nouvel export de la même série, par exemple après « Charger la série DICOM » depuis l'historique (tooltip `history_panel.py:151-153` qui annonce le remplacement).
- **Scénario de défaillance** : contrôle NC le 01/09 → action corrective saisie le 10/09 → le physicien recharge la série pour refaire le PDF avec le logo → l'action corrective disparaît du registre.
- **Correction proposée** : dans `add_run`, si un run existant a le même `run_id`, recopier `corrective_action_date`, `corrective_action` (et proposer de conserver `notes`) sur le nouveau run avant remplacement ; ou faire confirmer le remplacement dans `_export_pdf` en listant ce qui sera perdu.
- **Confiance** : Probable (par lecture ; sémantique de remplacement confirmée par `test_database_persists_runs_and_replaces_same_series`)

### [C-14] Publication PyPI déclenchable à la main « pour test », sans contrôle de version ni tests
- **Sévérité** : Majeur
- **Fichier** : `.github/workflows/publish.yml:6` (`workflow_dispatch`), `:29-37` ; `.github/workflows/build.yml` (aucune étape `pytest`)
- **Constat** : un déclenchement manuel publie sur le vrai index PyPI ; rien ne vérifie que `cq_tdm.__version__` correspond au tag de la release ni que les tests passent. `build.yml` compile les exécutables sans lancer la suite.
- **Scénario de défaillance** : « Run workflow » depuis une branche de travail avec `__version__ = "0.7.0"` déjà publié → échec PyPI (version existante) dans le meilleur cas, publication d'un code non testé dans le pire ; une release taguée `v0.8.0` avec `__version__` oublié à `0.7.0` publie la mauvaise version.
- **Correction proposée** : retirer `workflow_dispatch` ou le router vers TestPyPI (`repository-url: https://test.pypi.org/legacy/`) ; ajouter une étape `python -c "import cq_tdm, os; assert 'v'+cq_tdm.__version__ == os.environ['GITHUB_REF_NAME']"` ; faire dépendre `publish` et `build` du job CI de C-08 (`needs: test`).
- **Confiance** : Confirmé (par lecture des workflows)

### [C-15] `AppConfig.load` n'applique aucune validation de type aux valeurs lues
- **Sévérité** : Mineur
- **Fichier** : `src/cq_tdm/core/app_config.py:124` (`load`), `src/cq_tdm/gui/main_window.py:932` (`ReportSettingsDialog._load_settings` : `int(config.report_logo_scale * 100)`), `src/cq_tdm/reports/pdf_report.py:559` (`text_width * self.logo_scale`)
- **Constat** : `cls(**{k: v ...})` accepte `report_logo_scale: "0.4"` ou `theme: 5` sans erreur ; l'erreur surgit plus tard (`TypeError` dans la boîte de configuration des rapports, logo ignoré dans le PDF via `except Exception: pass`).
- **Scénario de défaillance** : `settings.json` édité à la main (`"report_logo_scale": "40 %"`) → « Erreur inattendue : TypeError » à l'ouverture de Configuration › Rapports.
- **Correction proposée** : dans `load`, convertir et borner chaque champ (`float(...)`, `0.1 <= scale <= 1.0`, `theme in ("dark", "light")`) ; en cas d'échec, utiliser la valeur par défaut du champ et journaliser.
- **Confiance** : Probable

### [C-16] `QCRun.date` remplace silencieusement une date malformée par aujourd'hui
- **Sévérité** : Mineur
- **Fichier** : `src/cq_tdm/core/qc_history.py:94-100` (`QCRun.date`), `:102-103` (`date_fr`)
- **Constat** : un `run_date` corrompu (`"2026-13-01"`, `""`) est affiché et trié comme un contrôle d'aujourd'hui dans le tableau, la courbe (`trend_chart.py:223`) et le PDF.
- **Scénario de défaillance** : fichier édité à la main ou C-12 : un vieux contrôle apparaît comme le plus récent, « dernier : 01/10/2026 » dans le résumé.
- **Correction proposée** : `date` renvoie `None` (ou `date.min`) et `date_fr` renvoie « date inconnue » ; les tris utilisent `run_date` brut (déjà le cas dans `add_run`).
- **Confiance** : Confirmé (par lecture)

### [C-17] `dicom_folder_rel` enregistré avec le séparateur natif : non portable entre postes Windows et Linux partageant la base
- **Sévérité** : Mineur
- **Fichier** : `src/cq_tdm/core/dicom_locator.py:66-71` (`relative_to_database`), `:92` (`candidate_folders` : `db_path.parent / dicom_folder_rel`)
- **Constat** : `os.path.relpath` produit `..\\data\\2026\\S1` sous Windows ; relu sous Linux, `Path("..\\data\\2026\\S1")` est un seul composant et ne résout rien (l'inverse fonctionne par tolérance de Windows).
- **Scénario de défaillance** : base commune sur NAS, poste Windows enregistre, poste Linux clique « Charger la série DICOM » → « Images DICOM introuvables » alors que le chemin relatif est bon.
- **Correction proposée** : stocker en notation POSIX (`Path(rel).as_posix()`) et relire avec `PurePosixPath(rel).parts` ; même traitement pour `folder_relocations`.
- **Confiance** : Probable

### [C-18] Recherche de série : `QProgressDialog` non modale + `processEvents()` → réentrance possible
- **Sévérité** : Mineur
- **Fichier** : `src/cq_tdm/gui/main_window.py:2883-2896` (`_search_run_folder`)
- **Constat** : `QProgressDialog(..., self)` est `NonModal` par défaut (vérifié : `windowModality() == NonModal`) et `visit()` appelle `QApplication.processEvents()` à chaque dossier ; la fenêtre principale reste cliquable pendant le parcours.
- **Scénario de défaillance** : pendant la recherche, l'utilisateur clique à nouveau « Charger la série DICOM » ou « Ouvrir un dossier » → deuxième chargement imbriqué, `_current_series` modifié sous les pieds de la recherche, deux boîtes de progression.
- **Correction proposée** : `progress.setWindowModality(Qt.WindowModality.ApplicationModal)` et désactiver les boutons de la fenêtre pendant la recherche ; à terme déplacer `find_series_folder` dans un `QThread`/`QRunnable` avec signal de progression.
- **Confiance** : Confirmé (modalité vérifiée)

### [C-19] Texte utilisateur injecté dans du rich text Qt sans échappement
- **Sévérité** : Mineur
- **Fichier** : `src/cq_tdm/gui/history_panel.py:340-369` (`_show_details` : `run.notes`, `run.pdf_path`, `run.dicom_folder`, `run.artifacts_description`, `run.kernel`), `src/cq_tdm/gui/main_window.py:3387-3425` (`_update_install_summary` : établissement, localisation, n° de série)
- **Constat** : ces chaînes sont concaténées dans du HTML (`"<br>".join`, `setText(f"{saved}<br>…")`) ; Qt détecte le rich text et interprète `<`, `>`, `&`.
- **Scénario de défaillance** : note « écart <2 HU sur 12h » → tout ce qui suit `<2` disparaît de la boîte de détail ; établissement « Clinique A&B » affiché « A&B » ou tronqué selon le parseur.
- **Correction proposée** : `html.escape()` systématique (déjà utilisé dans `_format_artifact_html`, `main_window.py:3170-3171`) ou `setTextFormat(Qt.TextFormat.PlainText)` quand aucun balisage n'est nécessaire.
- **Confiance** : Probable

### [C-20] Export CSV : `open()` sans gestion d'erreur
- **Sévérité** : Mineur
- **Fichier** : `src/cq_tdm/gui/history_panel.py:377` (`_export_csv`)
- **Constat** : `PermissionError` (fichier déjà ouvert dans Excel sous Windows, dossier en lecture seule) remonte jusqu'à `sys.excepthook` → boîte « Erreur inattendue » avec traceback.
- **Scénario de défaillance** : l'utilisateur exporte deux fois vers `historique_cq.csv` en gardant le premier ouvert dans Excel.
- **Correction proposée** : `try/except OSError` → `QMessageBox.warning(self, "Export CSV", f"Impossible d'écrire le fichier :\n{e}")`.
- **Confiance** : Probable

### [C-21] `load_dicom_folder` : fichiers cachés/DICOMDIR tentés, et chemin de fichier accepté
- **Sévérité** : Mineur
- **Fichier** : `src/cq_tdm/core/dicom_loader.py:345-347` (`exists()` sans `is_dir()`), `:353-359` (liste d'extensions ignorées), `:362-369`
- **Constat** : `.DS_Store`, `Thumbs.db`, `DICOMDIR`, `*.bak`, `desktop.ini` sont ouverts par pydicom et leur échec devient la « Première erreur » affichée (`main_window.py:1809`), masquant la vraie cause (par ex. C-07). Un chemin de fichier lève `NotADirectoryError` (reproduit) alors que la docstring n'annonce que `FileNotFoundError`.
- **Scénario de défaillance** : dossier macOS → « 1 fichier n'a pas pu être lu. Première erreur : .DS_Store: File is missing DICOM File Meta Information ».
- **Correction proposée** : ignorer les noms commençant par `.`, `DICOMDIR`, et partager la liste `_SKIP_SUFFIXES` avec `dicom_locator.py:20` (actuellement dupliquée et différente) ; `if not folder_path.is_dir(): raise NotADirectoryError(...)` ; dans la GUI, afficher l'erreur la plus fréquente plutôt que la première.
- **Confiance** : Confirmé (NotADirectoryError reproduit) / Probable pour les fichiers cachés

### [C-22] `_geometry_for_analysis` modifie `self._geometry_mismatch` comme effet de bord, partagé entre analyses UH et SPB
- **Sévérité** : Mineur
- **Fichier** : `src/cq_tdm/gui/main_window.py:2549-2563` (`_geometry_for_analysis`), `:2590-2602` (`_format_roi_geometry_html`)
- **Constat** : un accesseur qui remet un drapeau à `False` puis le positionne ; l'analyse SPB (1 s plus tard) écrase la valeur calculée pour l'analyse UH. Le message « recalculées : format d'image différent » dépend donc de l'ordre des timers.
- **Scénario de défaillance** : série hétérogène (C-04) où la coupe UH ne correspond pas à la géométrie figée mais la coupe de début de plage SPB si : le message disparaît après la fin de l'analyse SPB.
- **Correction proposée** : faire retourner `(geometry, mismatch)` et stocker le résultat dans `WaterPhantomResults`/`NPSResult` (par ex. `geometry_mismatch: bool`) plutôt que dans la fenêtre.
- **Confiance** : Confirmé (par lecture)

### [C-23] État partiellement mis à jour si le chargement d'un dossier échoue en cours de route
- **Sévérité** : Mineur
- **Fichier** : `src/cq_tdm/gui/main_window.py:1814-1864` (`_load_dicom_folder`), `:2815-2822` (`_load_run_series`)
- **Constat** : `self._current_series` et `self._current_folder` sont affectés avant les étapes susceptibles d'échouer (`set_image`, `_try_auto_detect_device`, `detect_phantom` du mode debug) ; en cas d'exception, `_current_image`, les overlays et l'historique restent ceux de l'ancienne série. `_load_run_series` compare ensuite `self._current_image.series_instance_uid` (ancienne série) à `run.series_uid` et affiche un second message trompeur « Série différente ».
- **Scénario de défaillance** : dossier dont la coupe médiane est corrompue en pixels (exception dans `set_image`) → message d'erreur puis fenêtre dans un état mixte.
- **Correction proposée** : construire tout l'état dans des variables locales et ne l'affecter qu'à la fin ; faire retourner `bool` à `_load_dicom_folder` et l'utiliser dans `_load_run_series` avant toute comparaison.
- **Confiance** : Probable

### [C-24] Figures matplotlib non fermées en cas d'exception
- **Sévérité** : Mineur
- **Fichier** : `src/cq_tdm/core/trend_chart.py:219-298` (`render_trend_chart`), `src/cq_tdm/reports/pdf_report.py:133-311` (`_generate_nps_plot`, `_generate_hu_roi_image`, `_generate_nps_roi_image`, `_generate_artifact_image`), `src/cq_tdm/gui/main_window.py:3097-3143` (`_generate_nps_plot`)
- **Constat** : `plt.close(fig)` n'est pas dans un `finally` ; les appelants attrapent les exceptions (`pdf_report.py:843,853,947,1347`) et continuent, la figure reste ouverte (avertissement « More than 20 figures » puis mémoire).
- **Scénario de défaillance** : police FreeType défaillante (cas documenté dans `main.py:10-18`) → chaque rafraîchissement du graphique de tendance fuit une figure.
- **Correction proposée** : `try: … finally: plt.close(fig)`, ou utiliser `matplotlib.figure.Figure` + `FigureCanvasAgg` sans passer par `pyplot` (pas d'état global).
- **Confiance** : Confirmé (par lecture)

### [C-25] Exceptions avalées sans trace ; aucun journal applicatif
- **Sévérité** : Mineur
- **Fichier** : `src/cq_tdm/gui/main_window.py:2306-2312` (`_run_debounced_hu_analysis`), `:2351-2356` (`_run_debounced_nps_analysis`), `:1862-1864`, `:2083-2085` ; `src/cq_tdm/reports/pdf_report.py:572-573,843-844,853-854,947-948,1347-1348` (`except Exception: pass`) ; `src/cq_tdm/core/dicom_locator.py:34-35`
- **Constat** : `except Exception as e` → message de barre d'état éphémère (`showMessage` sans timeout mais remplacé au prochain message) ou `pass`. Le seul journal est celui des exceptions non attrapées (`main.py:132-143`). Aucun `logging`.
- **Scénario de défaillance** : « Erreur analyse SPB: No valid ROIs could be processed. » disparaît ; un rapport sans image d'artéfact ni logo est produit sans que personne ne sache pourquoi.
- **Correction proposée** : configurer `logging` dans `main.py` (handler fichier `RotatingFileHandler` à côté de `cq_tdm.log`, niveau INFO, DEBUG via `--verbose`) ; remplacer chaque `except … pass` par `logger.exception(...)` ; faire apparaître les erreurs d'analyse dans le panneau résultats (déjà prévu : `_get_empty_results_html`).
- **Confiance** : Confirmé (par lecture)

### [C-26] Ouverture de `run.pdf_path` sans contrôle et `is_file()` sur chemin réseau à chaque sélection
- **Sévérité** : Mineur
- **Fichier** : `src/cq_tdm/gui/history_panel.py:278` (`_on_selection_changed`), `:299-301` (`_open_pdf`), `src/cq_tdm/gui/image_viewer.py:1196-1198` (`_open_dicom_folder`)
- **Constat** : `Path(run.pdf_path).is_file()` est évalué à chaque changement de ligne ; sur un partage SMB injoignable, cet appel peut bloquer plusieurs secondes (timeout réseau) et geler l'interface. `QDesktopServices.openUrl` est appelé sur un chemin lu dans un fichier JSON partagé/modifiable sans vérifier qu'il s'agit d'un `.pdf` (sous Windows, un `.exe`/`.bat` serait exécuté).
- **Scénario de défaillance** : historique de 30 contrôles dont les PDF sont sur un NAS éteint → chaque clic dans le tableau fige la fenêtre.
- **Correction proposée** : ne tester l'existence qu'au clic sur le bouton (ou en cache avec expiration) ; refuser l'ouverture si `Path(p).suffix.lower() != ".pdf"`.
- **Confiance** : Probable

### [C-27] `AppConfig.save` non atomique ; un seul `.bak` pour la base
- **Sévérité** : Mineur
- **Fichier** : `src/cq_tdm/core/app_config.py:139-143` (`save`), `src/cq_tdm/core/device_database.py:197-203` (`_backup_unreadable_file`)
- **Constat** : `settings.json` est ouvert en `"w"` puis écrit : une coupure pendant l'écriture laisse un fichier vide → au prochain démarrage, `load` renomme en `.bak` et repart avec les défauts (chemin de base personnalisé perdu, l'utilisateur voit une base vide — cf. C-02). `_backup_unreadable_file` écrase toujours le même `.bak`.
- **Scénario de défaillance** : arrêt brutal pendant `_toggle_theme` → chemin `device_database_path` perdu → base locale vide affichée.
- **Correction proposée** : factoriser l'écriture atomique de `DeviceDatabase._save` dans `core/utils.py` (`atomic_write_json(path, data)`) et l'utiliser dans `AppConfig.save` et `_write_crash_log` ; horodater les sauvegardes.
- **Confiance** : Confirmé (par lecture)

### [C-28] `"version": 2` écrit mais jamais lu ; aucune migration explicite ni garde-fou contre un fichier plus récent
- **Sévérité** : Mineur
- **Fichier** : `src/cq_tdm/core/device_database.py:184,207-211` (`_load`, `_save`), `:70-75` (`from_dict` ignore les clés inconnues)
- **Constat** : un fichier écrit par une version future (champs renommés, `version: 3`) est chargé en ignorant silencieusement les clés inconnues puis réécrit en `version: 2` → perte de données au premier enregistrement, sans message.
- **Scénario de défaillance** : deux postes avec des versions différentes de CQ TDM sur la même base (cas réel avec une base réseau).
- **Correction proposée** : lire `version` ; si `> SCHEMA_VERSION`, positionner `load_error`/mode lecture seule ; conserver les clés inconnues (`extra: dict`) et les réécrire telles quelles ; ajouter une table de migrations `1 → 2 → …`.
- **Confiance** : Confirmé (par lecture)

### [C-29] Le chemin par défaut de la base est « épinglé » en absolu dans `settings.json` dès la première sauvegarde
- **Sévérité** : Mineur
- **Fichier** : `src/cq_tdm/gui/main_window.py:556-560` (`DeviceManagerDialog._persist`)
- **Constat** : `config.device_database_path = str(self.device_db.db_path)` même quand la base est celle par défaut (`device_database_path == ""`). Le profil utilisateur contient alors un chemin absolu (`C:\Users\x\AppData\Roaming\cq_tdm\devices.json`).
- **Scénario de défaillance** : profil migré vers un autre compte/poste ou dossier `cq_tdm` déplacé → CQ TDM pointe sur un chemin inexistant → base vide (C-02).
- **Correction proposée** : ne renseigner `device_database_path` que si `db_path != DeviceDatabase.default_path()` ; exposer `default_path()` comme méthode de classe.
- **Confiance** : Confirmé (par lecture)

### [C-30] PDF : cellules de `Table` en chaînes brutes, sans retour à la ligne
- **Sévérité** : Mineur
- **Fichier** : `src/cq_tdm/reports/pdf_report.py:724-743` (`_build_equipment_section`), `:773-791` (`_build_acquisition_section`), `:965-969` (`_build_artifact_section` : description libre), `:1001-1021` (`_build_history_section`)
- **Constat** : reportlab ne replie pas une `str` dans une cellule ; seuls `series_instance_uid` et `dicom_folder` sont enveloppés dans `Paragraph` (`:807-808`). Un établissement long, un algorithme « iDose4 niveau 3, reconstruction itérative hybride… » ou une description d'artéfact de deux lignes débordent de la colonne et sont rognés en bord de page.
- **Scénario de défaillance** : description d'artéfact de 150 caractères → texte coupé sur le PDF archivé au registre.
- **Correction proposée** : helper `_cell(text)` → `Paragraph(html.escape(text), style)` pour toute valeur libre ; `colWidths` en fraction de `doc.width`.
- **Confiance** : Probable

### [C-31] Tests GUI : fixture `qapp` maison, et appels réels à `get_app_config()` qui écrivent dans `~/.config/cq_tdm`
- **Sévérité** : Mineur
- **Fichier** : `tests/test_history_home_view.py:255-261` (`config`), `tests/test_device_manager.py:97-107` ; `src/cq_tdm/gui/theme.py:458` (`theme_colors`), `src/cq_tdm/gui/history_panel.py:39-40` (`_palette`), `src/cq_tdm/gui/image_viewer.py:483,972,1105`
- **Constat** : les tests patchent `cq_tdm.gui.main_window.get_app_config` mais `theme.py`, `history_panel.py` et `image_viewer.py` importent la fonction depuis `core` ; `AppConfig.load()` réel est donc appelé et **crée `settings.json` dans le vrai dossier de configuration** (observé après `pytest` : `~/.config/cq_tdm/settings.json` créé). La fixture `qapp` définie localement masque celle de pytest-qt (dépendance déclarée mais inutilisée : pas de `qtbot`).
- **Scénario de défaillance** : sur le poste d'un développeur, la suite modifie son thème/chemin de base ; en CI, un dossier `$HOME/.config` en lecture seule fait tomber des tests sans rapport.
- **Correction proposée** : fixture `autouse` dans `conftest.py` : `monkeypatch.setattr(cq_tdm.core.app_config, "_app_config", AppConfig(...))` (le singleton, pas les ré-exports) et `monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))` ; utiliser `qapp`/`qtbot` de pytest-qt.
- **Confiance** : Confirmé (fichier créé observé)

### [C-32] `test_nps_validation.py` : scripts de génération de figures déguisés en tests, dépendants de données externes
- **Sévérité** : Mineur
- **Fichier** : `tests/test_nps_validation.py:393-411` (`test_reference_data_exists`), `:627-790` et `:793-1007` (`generate_combined_*`), `:1010-1021` (`TestCombinedValidation`), `:271` (`color` inutilisé, F841)
- **Constat** : les « tests » écrivent des PNG, `generate_combined_validation_figure` saute une série absente avec un `print` et retourne quand même un chemin (le test passe avec 0 série) ; `matplotlib.pyplot` est importé au niveau module (backend choisi avant tout `matplotlib.use`). Aucun test unitaire des briques `detrend_roi`, `compute_nps_2d`, `radial_average`, `fit_nps_polynomial`, `NPSROIConfig.from_dict/to_dict`.
- **Scénario de défaillance** : CI `nps-validation.yml` « verte » avec un zip incomplet ; régression dans `radial_average` non détectée sans `test_data/`.
- **Correction proposée** : déplacer les deux générateurs dans `scripts/make_validation_figures.py` ; marquer la classe `@pytest.mark.ansm_data` ; ajouter des tests unitaires synthétiques (bruit blanc gaussien : NPS plat, `fit_nps_polynomial` ≥ 0, round-trip JSON coin/centre).
- **Confiance** : Confirmé (par lecture et exécution)

### [C-33] `pyproject.toml` : dépendances sans borne supérieure, décodeurs absents, pas de matrice Python
- **Sévérité** : Mineur
- **Fichier** : `pyproject.toml:12-27`, `:6` (`requires-python = ">=3.10"`), `.github/workflows/build.yml:20` (3.12 uniquement)
- **Constat** : `numpy>=1.24`, `pydicom>=2.4`, `scipy>=1.11`, `matplotlib>=3.8` sans `<` : le code gère déjà une rupture (`np.trapz`, `nps.py:21`) et un contournement (`main.py:10-18`) ; la prochaine rupture (pydicom 4, numpy 3) arrivera chez l'utilisateur `pip install cq-tdm`. `pyinstaller` n'est pas dans `[dev]`. Python 3.10 annoncé mais jamais testé.
- **Scénario de défaillance** : `pipx install cq-tdm` sur une machine neuve récupère un pydicom 4 incompatible → crash au chargement.
- **Correction proposée** : bornes majeures (`numpy>=1.24,<3`, `pydicom>=2.4,<4`, `PySide6-Essentials>=6.6,<7`, …), `pydicom[pixeldata]` (C-07), `pyinstaller` dans un extra `build`, matrice CI 3.10/3.12/3.13 (C-08), et un `constraints.txt` figé pour les exécutables.
- **Confiance** : Confirmé (par lecture)

### [C-34] `build.yml` : aucune étape de test, actions épinglées par tag majeur, dépendances système non figées
- **Sévérité** : Mineur
- **Fichier** : `.github/workflows/build.yml:15,18,55,61` (`@v7`), `:68` (`softprops/action-gh-release@v3`), `:49` (`choco install innosetup`), `.github/workflows/nps-validation.yml:63` (`peter-evans/create-pull-request@v8`)
- **Constat** : les tags majeurs sont mobiles (supply chain) et les versions `@v7`/`@v8` n'ont pas pu être vérifiées depuis cette session ; `innosetup` et `pyinstaller` ne sont pas versionnés → une build de release peut changer sans modification du dépôt. Le smoke test `--check-deps` est une bonne idée mais ne couvre pas `trend_chart`/`dicom_locator`/`matplotlib.dates` (voir C-47).
- **Scénario de défaillance** : nouvelle version majeure de PyInstaller publiée la veille d'une release → exécutable cassé sans changement de code.
- **Correction proposée** : épingler par SHA (`uses: actions/checkout@<sha> # v7.x`), `pip install pyinstaller==6.x`, `choco install innosetup --version=6.x`, `needs: test`.
- **Confiance** : Probable (versions non vérifiables ici)

### [C-35] `nps-validation.yml` : téléchargement sans `--fail`, figures générées depuis `main` et non depuis le tag
- **Sévérité** : Mineur
- **Fichier** : `.github/workflows/nps-validation.yml:19` (`ref: main`), `:36-38` (`curl -L -o … ` sans `--fail`), `:44`
- **Constat** : un 404 sur l'asset `test-data` produit un `test_data.zip` HTML, l'échec survient au `unzip` avec un message obscur ; déclenché par une release, le workflow valide le code de `main` (potentiellement en avance sur le tag) et le PR « Update NPS validation figures » porte la version `__version__` de `main`.
- **Scénario de défaillance** : figure README annotée « v0.8.0 » alors que la release validée est 0.7.0.
- **Correction proposée** : `curl --fail -L …` ; `ref: ${{ github.event.release.tag_name || 'main' }}` ; échouer si une série est absente (cf. C-32).
- **Confiance** : Confirmé (par lecture)

### [C-36] `install-windows.bat` : Python 3.14 imposé, installation `pip` globale, chemins non échappés
- **Sévérité** : Mineur
- **Fichier** : `installers/install-windows.bat:40,52` (Python 3.14), `:94` (`pip install --upgrade cq-tdm` sans `--user`), `:151,164,171` (chemins dans des chaînes PowerShell entre apostrophes)
- **Constat** : le script installe la toute dernière version de Python ; les roues binaires (PySide6, scipy, numpy, Pillow) ne sont pas garanties pour une version aussi récente → pip tente une compilation et échoue. Sans `--user`, un Python « all users » demande des droits administrateur. Un profil contenant une apostrophe (`C:\Users\O'Brien`) casse les commandes PowerShell. `winget` est appelé sans vérifier qu'il n'est pas le stub du Store.
- **Scénario de défaillance** : poste neuf le jour de sortie d'une version de Python : « Failed to install CQ TDM ».
- **Correction proposée** : viser `Python.Python.3.12` (ou 3.13) ; `pip install --user --upgrade cq-tdm` ; échapper les apostrophes (`'%SCRIPT_PATH:'=''%'`) ou passer par un script `.ps1` avec paramètres ; vérifier `winget` via `winget source list`.
- **Confiance** : Probable

### [C-37] `install-linux.sh` : PEP 668, lecture interactive incompatible avec `curl | bash`, aucune vérification de version Python
- **Sévérité** : Mineur
- **Fichier** : `installers/install-linux.sh:41` (`python3 -m pip install --user pipx`), `:138` (`read -p`), `:25-26` (version affichée mais non contrôlée), absence de `set -euo pipefail`
- **Constat** : sur Debian 12 / Ubuntu ≥ 23.04, `pip install --user` est refusé (`externally-managed-environment`) → le script s'arrête avant d'avoir proposé `apt install pipx`. Exécuté via `curl … | bash`, `read -p` lit la suite du script sur stdin. Python 3.8 passe le contrôle « Python 3 trouvé » et échoue plus tard sur `requires-python`.
- **Scénario de défaillance** : Ubuntu 24.04 vierge → « [ERROR] Failed to install pipx ».
- **Correction proposée** : tester `python3 -c "import sys; sys.exit(sys.version_info < (3,10))"` ; proposer `sudo apt install pipx` / `pip install --user --break-system-packages pipx` ; `read -r -p … < /dev/tty` ; `set -euo pipefail`.
- **Confiance** : Probable

### [C-38] `cq-tdm.iss` : la version est lue à la 3e ligne de `__init__.py`
- **Sévérité** : Mineur
- **Fichier** : `installers/cq-tdm.iss:10-17` (`ParseVersion`), `src/cq_tdm/__init__.py:3`
- **Constat** : trois `FileRead` successifs puis extraction entre guillemets : l'ajout d'une ligne (docstring multi-ligne, import, `__all__`) au-dessus de `__version__` donne une version vide ou fausse dans l'installateur, sans erreur de build.
- **Scénario de défaillance** : installateur `CQ_TDM_Setup_Windows.exe` publié avec `AppVersion` vide → Windows « Ajout/suppression de programmes » affiche une version vide et les mises à jour ne se détectent plus.
- **Correction proposée** : générer la version côté workflow (`python -c "import cq_tdm; print(cq_tdm.__version__)"` → `iscc /DMyAppVersion=…`) ou boucler sur les lignes jusqu'à trouver `__version__`.
- **Confiance** : Confirmé (par lecture)

### [C-39] Toutes les opérations longues s'exécutent dans le thread GUI
- **Sévérité** : Mineur
- **Fichier** : `src/cq_tdm/gui/main_window.py:1800-1801` (`_load_dicom_folder` : `showMessage` puis chargement bloquant), `:2052-2075` (`generate_pdf_report`), `:2338-2340` (`analyze_nps`)
- **Constat** : il n'y a aucun `QThread`, donc pas de bug de concurrence (point positif), mais `load_dicom_folder` d'une série de 300 coupes 1024² ou la génération d'un PDF (4 figures matplotlib + graphiques de tendance) gèlent la fenêtre plusieurs secondes ; le message « Chargement du dossier… » n'est même pas repeint. Sous Windows, l'OS affiche « Ne répond pas ».
- **Scénario de défaillance** : série hélicoïdale complète de 600 images → fenêtre blanche 10-20 s, l'utilisateur clique plusieurs fois.
- **Correction proposée** : `QThreadPool` + `QRunnable` (ou `concurrent.futures` + `QTimer` de sondage) pour le chargement, avec `QProgressDialog` modale et annulation ; désactiver les actions pendant l'opération ; garder les analyses (rapides) sur le thread GUI.
- **Confiance** : Confirmé (par lecture)

### [C-40] Les imports « paresseux » de `core/__init__.py` sont contournés dès l'import de la fenêtre principale
- **Sévérité** : Suggestion
- **Fichier** : `src/cq_tdm/core/__init__.py:3-59`, `src/cq_tdm/gui/main_window.py:46-61`, `src/cq_tdm/reports/pdf_report.py:14-18`
- **Constat** : `from ..core import DicomImage, …` au niveau module déclenche `__getattr__` → `dicom_loader` → numpy + pydicom + PIL au démarrage (mesuré : après `import cq_tdm.gui.main_window`, `numpy`, `pydicom`, `PIL` sont chargés ; `scipy`, `matplotlib`, `reportlab` restent différés). Le mécanisme ne protège donc que partiellement et complique le typage (ruff F401 ×7 sur les ré-exports).
- **Correction proposée** : soit assumer les imports directs (plus simple, typage statique), soit utiliser `if TYPE_CHECKING:` pour les annotations et importer les fonctions dans les méthodes qui les appellent ; ajouter `__all__` dans `core/__init__.py` et `reports/__init__.py`.
- **Confiance** : Confirmé (mesuré)

### [C-41] Code et état morts
- **Sévérité** : Suggestion
- **Fichier** : `src/cq_tdm/gui/main_window.py:1226-1233, 3447-3454, 3499-3506` (`_saved_hospital_name` … `_saved_ref_nps_freq` : écrits, jamais lus), `:1967-1971` (`_format_date`), `:16` (`QGridLayout`) ; `src/cq_tdm/core/dicom_loader.py:153-165` (`get_3d_array`, `sort_by_instance`) ; `src/cq_tdm/gui/image_viewer.py:739-747` (`set_rois` « legacy ») ; `src/cq_tdm/core/water_phantom.py:105-120,296-324` et `src/cq_tdm/core/nps.py:156-165,710-728` (`to_dict`, `format_*_text` jamais appelés) ; `cq_tdm.spec:27` (`PySide6.QtPrintSupport` non utilisé)
- **Constat** : vérifié par `grep` : aucune lecture de ces symboles hors de leur définition.
- **Correction proposée** : supprimer ; si `format_*_text` servent de sortie CLI, ajouter une commande `cq-tdm --analyze <dossier>` qui les utilise et un test.
- **Confiance** : Confirmé (grep)

### [C-42] `QLineEdit` cachés utilisés comme « miroir mémoire » de l'installation, avec debounce sur des champs que l'utilisateur ne peut plus éditer
- **Sévérité** : Suggestion
- **Fichier** : `src/cq_tdm/gui/main_window.py:1356-1374`, `:1735-1745` (`_on_field_changed`), `:2358-2378` (`_on_reference_field_changed`, `_run_debounced_reference_update`), `:2021-2024, 2652-2655, 3193-3194, 3222-3223, 3394-3397` (lecture `parse_float_fr(self._edit_ref_noise.text())`)
- **Constat** : depuis le déplacement du formulaire dans `DeviceManagerDialog`, ces widgets ne sont jamais affichés ; les valeurs de référence sont relues en re-parsant du texte formaté (`format_fr` → `parse_float_fr`, perte de précision : `0.2634` → « 0,263 » → `0.263`). La valeur de référence effectivement utilisée pour le verdict n'est donc plus exactement celle enregistrée dans `devices.json`.
- **Correction proposée** : supprimer les `QLineEdit` cachés et le timer `_ref_debounce_timer` ; lire `self._current_device.reference_noise`/`reference_nps_freq` directement (`None` si pas d'installation) via deux propriétés `_ref_noise`/`_ref_nps`.
- **Confiance** : Confirmé (par lecture)

### [C-43] Gardes `hasattr(self, …)` qui compensent un ordre d'initialisation fragile
- **Sévérité** : Suggestion
- **Fichier** : `src/cq_tdm/gui/main_window.py:1769, 3343, 3360, 3381`, `src/cq_tdm/gui/image_viewer.py:1202, 1256, 1268, 1277, 1302`
- **Constat** : `_update_install_summary` est appelé pendant `_setup_ui` avant que tous les widgets existent, d'où les gardes ; `getattr(self, "_current_image", None)` (`:2514, 3326`) idem. Toute nouvelle dépendance entre widgets nécessite un garde supplémentaire.
- **Correction proposée** : créer tous les widgets dans `_setup_ui` sans appeler les méthodes de rafraîchissement, puis un unique `_refresh_all()` en fin de `__init__` ; supprimer les gardes.
- **Confiance** : Confirmé (par lecture)

### [C-44] Fonctions trop longues et modules trop gros
- **Sévérité** : Suggestion
- **Fichier** : `src/cq_tdm/gui/main_window.py` (3600 l. ; six classes : `DeviceManagerDialog` 91-811, `ImageInfoDialog`, `ReportSettingsDialog`, `NotesEditorDialog`, `CorrectiveActionDialog`, `MainWindow` 1165-3600) ; `DeviceManagerDialog._setup_ui` (136-367, 230 l.), `MainWindow._export_pdf` (1973-2085), `_load_device_config` (3427-3506), `_format_results_html` + 5 `_format_*_html` (2919-3243) ; `src/cq_tdm/gui/image_viewer.py` (`ImageViewerWidget.__init__` 938-1099, `ArtifactInspectionDialog._setup_ui` 1686-1851 avec feuilles de style inline répétées) ; `src/cq_tdm/reports/pdf_report.py` (`generate_report` 506-674) ; `src/cq_tdm/core/nps.py` (`analyze_nps` 509-707)
- **Constat** : le rendu HTML des résultats, la construction du `QCRun`, et le choix de l'image de rapport sont mêlés à la logique de fenêtre, ce qui empêche de les tester sans Qt.
- **Correction proposée** : `gui/device_manager_dialog.py`, `gui/dialogs.py`, `gui/results_html.py` (fonctions pures `render_results_html(results, nps, artifact, refs, theme) -> str`, testables), `gui/artifact_dialog.py` ; dans `_export_pdf`, extraire `_build_report_inputs()` ; dans `pdf_report`, une fonction par section déjà présente — ne reste qu'à sortir le logo (`546-573`) dans `_build_logo()`.
- **Confiance** : Confirmé (par lecture)

### [C-45] Typage, nommage et configuration ruff incohérents
- **Sévérité** : Suggestion
- **Fichier** : `src/cq_tdm/core/nps.py:295` (`num_bins: int = None`), `src/cq_tdm/gui/image_viewer.py:412` (`color: QColor = None`), `src/cq_tdm/core/dicom_locator.py:17` (`typing.Callable`, UP035), mélange `Optional[X]`/`X | None` (UP045 ×58), `pyproject.toml:52-57`
- **Constat** : la règle N802 est inapplicable aux surcharges Qt (`mousePressEvent`, `paintEvent`…) et E501 n'est pas respectée (160 cas) ; la configuration ne reflète donc pas la pratique et ne peut pas être imposée en CI telle quelle.
- **Correction proposée** : `[tool.ruff.lint.per-file-ignores] "src/cq_tdm/gui/*" = ["N802"]` ou `pep8-naming.extend-ignore-names = ["*Event", "isChecked", "setChecked"]` ; passer `ruff check --fix` + `ruff format` en un commit dédié ; `Optional` → `X | None` partout ; `num_bins: int | None = None`, `color: QColor | None = None`.
- **Confiance** : Confirmé (ruff)

### [C-46] PDF : compteur de pages manuel au lieu de `doc.page`
- **Sévérité** : Suggestion
- **Fichier** : `src/cq_tdm/reports/pdf_report.py:660-674` (`generate_report`), `:341-352` (`_draw_footer`), `:355-377` (`_draw_header` ignore `doc`)
- **Constat** : `page_counter` incrémenté dans les callbacks suppose un seul passage ; `canvas_obj.getPageNumber()`/`doc.page` fournissent la valeur sûre, et permettent « Page n / N » avec un `canvasmaker` à deux passes.
- **Correction proposée** : utiliser `canvas_obj.getPageNumber()` ; supprimer `page_counter`.
- **Confiance** : Confirmé (par lecture)

### [C-47] Le smoke test `--check-deps` ne couvre pas tous les modules chargés paresseusement
- **Sévérité** : Suggestion
- **Fichier** : `src/cq_tdm/main.py:93-110` (`_check_dependencies`), `cq_tdm.spec:20-45` et `installers/cq_tdm_installer.spec:27-55` (`hiddenimports`), `.github/workflows/build.yml:32-35,43-46`
- **Constat** : `cq_tdm.core.trend_chart` (→ `matplotlib.dates`, `dateutil`), `cq_tdm.core.dicom_locator`, `cq_tdm.core.qc_history`, `cq_tdm.gui.history_panel`, `PIL.PngImagePlugin` ne sont pas importés par le smoke test ; `scipy.ndimage` est importé dans des fonctions (`dicom_loader.py:416,438`, `nps.py:315`) donc détecté statiquement, mais la liste d'exclusions scipy (`spec:98-110`) repose sur les imports internes d'une version donnée de scipy.
- **Correction proposée** : dériver la liste de `_check_dependencies` par `pkgutil.walk_packages(cq_tdm.__path__)` + les modules tiers ; exécuter aussi `--check-deps` dans la CI de C-08 sur la version non gelée ; envisager `collect_submodules("scipy.ndimage")` plutôt que des exclusions manuelles.
- **Confiance** : Probable

## 4. Tests : couverture, manques, tests à ajouter

**État** : 70 tests passent sans données externes ; ils couvrent bien `qc_history` (critères, modèle, round-trip), `roi_geometry`, `dicom_locator`, la détection synthétique du fantôme, une partie de `device_database`, deux dialogues GUI et la génération d'un PDF complet. Les 22 tests de `test_nps_validation.py` et 10 de `TestAnsmSeries` dépendent de `test_data/` (absent du dépôt, gitignoré, fourni par un asset de release).

**Non testé ou très peu** : `nps.py` (briques unitaires : `detrend_roi`, `compute_nps_2d`, `radial_average`, `fit_nps_polynomial`, `NPSROIConfig` JSON, `skipped_rois`, `roi_warnings`) ; `water_phantom.analyze_water_phantom` (seuils `water_ct_ncg`, `uniformity`, `measure_roi` hors image) ; `dicom_loader.load_dicom_file` avec pixels réels (`RescaleSlope`, `PixelSpacing` absent/nul, `SliceLocation` absent → `ImagePositionPatient`, multi-frame rejeté, kernel multi-valué) et `load_dicom_folder` (tri, `load_errors`, fichiers non DICOM) ; `device_database` (corruption, `.bak`, `load_error`, `delete_device`, relecture concurrente) ; `app_config` (tout) ; `main.py` (`--version`, `--check-deps`, `_write_crash_log`) ; `trend_chart` (1 test) ; `history_panel` (CSV, sélection, hit-test) ; `MainWindow._export_pdf`/`_record_run_in_history`/`_load_run_series`/`_try_auto_detect_device` ; `image_viewer` (fenêtrage, `MarkedSlider`, clamp des coupes, `set_slice_count`) ; `pdf_report` (texte long, logo portrait, historique > 10, notes markdown).

**Tests à ajouter (concrets)** :

1. `tests/test_device_database.py` :
   - `test_bad_run_entry_does_not_hide_other_devices` (C-03) ; `test_unreadable_file_sets_load_error_and_keeps_bak_history`.
   - `test_two_instances_do_not_lose_each_other_runs` (C-01) ; `test_save_refused_when_parent_dir_missing` (C-02).
   - `test_empty_dicom_identity_is_refused_or_unique` (C-05) ; `test_add_run_unknown_device_raises_lookup_error` (C-10).
   - `test_default_path_unwritable_does_not_raise` (C-09) ; `test_newer_schema_version_is_read_only` (C-28).
2. `tests/test_dicom_loader.py` (fichiers écrits avec pydicom dans `tmp_path`, 64×64 px) :
   - `test_hu_conversion_uses_rescale`, `test_missing_pixel_spacing_is_rejected_or_derived` (C-06), `test_zero_pixel_spacing_is_rejected`, `test_slice_order_from_image_position_when_slice_location_missing`, `test_multiframe_and_rgb_are_skipped_with_reason`, `test_localizer_and_other_series_are_excluded` (C-04), `test_hidden_files_are_ignored` (C-21), `test_folder_path_that_is_a_file_raises_not_a_directory`.
3. `tests/test_nps_unit.py` : `test_detrend_removes_quadratic_background`, `test_white_noise_nps_is_flat_and_total_power_matches_variance`, `test_radial_average_is_rotation_invariant`, `test_fit_is_non_negative_and_falls_back_with_few_points`, `test_roi_config_json_round_trip_corner_vs_centre`, `test_rois_clipped_by_border_are_reported_in_skipped_rois`, `test_empty_roi_positions_raise_value_error`.
4. `tests/test_water_phantom.py` : seuils ±7/±25 sur image synthétique décalée, `measure_roi` hors image → `ValueError`, géométrie figée vs recalculée.
5. `tests/test_qc_history.py` (compléter) : `test_malformed_run_date_is_not_today` (C-16), `test_missing_dicom_date_is_flagged_not_today` (C-12), `test_rerecording_same_series_keeps_corrective_action` (C-13).
6. `tests/test_app_config.py` : round-trip, fichier corrompu → `.bak` + défauts, types invalides → défauts (C-15), écriture atomique (C-27).
7. `tests/test_main_window_export.py` (offscreen, `monkeypatch` de `QFileDialog.getSaveFileName` et `QDesktopServices.openUrl`) : export PDF enregistre un run avec `pdf_path` et `ref_*`, ré-export remplace sans dupliquer, `_load_run_series` avec dossier déplacé (relocation apprise), `_try_auto_detect_device` sur identité vide.
8. `tests/test_history_panel.py` : `set_runs` + `set_current_run` → `rowCount`, boutons activés selon sélection, `_run_at` retrouve un point du graphique, export CSV avec `PermissionError` géré (C-20).
9. `tests/test_pdf_report.py` : description d'artéfact longue et établissement long ne dépassent pas la largeur (C-30), logo portrait plafonné à 6 cm, historique de 12 runs → 10 affichés + courbes, notes markdown (titres, listes, gras).
10. `tests/test_main.py` : `--version` et `--check-deps` via `subprocess`, `_write_crash_log` roule à 1 Mo.
11. `conftest.py` : fixture `autouse` isolant `AppConfig` et `XDG_CONFIG_HOME` (C-31) ; `test_data_dir` → `pytest.skip` (C-08).

## 5. Packaging, CI, installeurs

- **pyproject.toml** : version dynamique via `cq_tdm.__version__` correcte ; dépendances minimales sans bornes (C-33) ; décodeurs d'images compressées absents (C-07) ; `pytest-qt` déclaré mais non utilisé (C-31) ; `ruff` configuré mais inapplicable en l'état (C-45) ; `package-data = assets/*` embarque aussi `create_icon.py` (inoffensif, à exclure).
- **cq_tdm.spec / installers/cq_tdm_installer.spec** : duplication intégrale des `hiddenimports`/`excludes` entre les deux fichiers → factoriser dans un `pyinstaller_common.py` importé par les deux ; `PySide6.QtPrintSupport` inutile ; exclusions scipy fragiles (C-47) ; `console=False` avec `disable_windowed_traceback=False` est cohérent avec le hook `sys.excepthook` (bon point).
- **build.yml** : pas de tests avant build, épinglage par tag majeur (C-34), `softprops/action-gh-release` reçoit `GITHUB_TOKEN` avec `contents: write` (correct) ; le job Windows seul produit les binaires — pas de build Linux (AppImage) alors que `install-linux.sh` passe par PyPI, cohérent.
- **publish.yml** : trusted publishing bien configuré (`id-token: write`, `environment: pypi`) ; `workflow_dispatch` dangereux (C-14) ; pas de vérification tag ↔ version.
- **nps-validation.yml** : `curl` sans `--fail`, `ref: main` (C-35) ; PR automatique bien pensée ; nécessite `tests/output` créé à la main alors que la fixture le crée déjà.
- **install-windows.bat** : C-36 ; bonne idée du `cmd /k` pour garder la console ; recherche de Python exhaustive mais limitée aux chemins par défaut.
- **install-linux.sh** : C-37 ; icône téléchargée depuis `main` (une version d'icône peut ne pas correspondre à la version installée) ; `gio set … trusted` correct pour GNOME.
- **cq-tdm.iss** : C-38 ; `PrivilegesRequired=lowest` + `{autopf}` cohérents ; langues FR/EN ; AppId fixe (bon pour les mises à jour).
- **.gitignore** : `test_data/`, `*.pdf` sauf `references/`, `*.zip` : cohérent avec les workflows ; `src/cq_tdm.egg-info` présent localement et correctement ignoré.

## 6. Points positifs (court)

- Critères ANSM centralisés (`qc_history.py`) et partagés GUI/PDF/historique, avec test de cohérence `test_pdf_overall_status_matches_history_criteria`.
- Écriture atomique de `devices.json` et sauvegarde `.bak` d'un fichier illisible ; références figées sur chaque run (`ref_noise`, `ref_nps_freq`) pour conserver le verdict historique.
- Hook `sys.excepthook` avec journal et boîte de dialogue : indispensable pour un exécutable fenêtré.
- `dicom_locator` : vérification systématique du `SeriesInstanceUID` avant de réutiliser un dossier, relocalisations apprises, tests clairs.
- Détection du fantôme robuste (ajustement de cercle itératif, tests synthétiques avec bulle/table/décentrage).
- Commentaires explicatifs de qualité sur les choix non évidents (ordre d'import FreeType, `RevolutionTime` vs `ExposureTime`, décalage d'un demi-pixel des ROI).
- Smoke test `--check-deps` sur les exécutables gelés ; versions d'actions récentes ; trusted publishing PyPI.

## 7. Plan d'action suggéré pour Claude Code local

1. **(S)** `tests/conftest.py` : `assert` → `pytest.skip` ; fixture autouse d'isolation `AppConfig`/`XDG_CONFIG_HOME` ; utiliser `qapp` de pytest-qt. — C-08, C-31
2. **(M)** Nouveau `.github/workflows/ci.yml` (ruff + pytest, matrice OS/Python, `libegl1`) ; `needs: test` dans `build.yml`/`publish.yml` ; retirer/rediriger `workflow_dispatch` de `publish.yml` ; vérification tag ↔ `__version__` ; `curl --fail`. — C-08, C-14, C-34, C-35
3. **(S)** `ruff check --fix` + `ruff format` + ajustement de la config (N802 Qt, E501) en un commit séparé ; supprimer le code mort. — C-45, C-41
4. **(L)** `DeviceDatabase` : chargement tolérant run par run avec `load_warnings` ; distinction fichier absent / dossier inaccessible → mode lecture seule ; `reload()` + fusion par `device_id`/`run_id` et verrou de fichier avant chaque `_save` ; tmp unique ; `.bak` horodatés ; lecture de `version` ; `DeviceNotFoundError` ; `mkdir` protégé ; tests associés. — C-01, C-02, C-03, C-09, C-10, C-27, C-28
5. **(M)** `device_database.generate_id`/`save_device`/`_image_device_id`/`_try_auto_detect_device` : refuser ou rendre unique une identité DICOM vide ; `_persist` n'épingle plus le chemin par défaut. — C-05, C-29
6. **(M)** `dicom_loader` : filtrage par série/SOP class/ImageType, homogénéité matrice/pixel, `PixelSpacing` absent ou nul rejeté (ou dérivé), fichiers cachés ignorés, `is_dir()` ; gardes `pixel_size_mm <= 0` dans `roi_geometry` et `nps` ; tests synthétiques pydicom. — C-04, C-06, C-21
7. **(S)** `pyproject.toml` : bornes majeures, `pydicom[pixeldata]`, extra `build` avec `pyinstaller` ; `hiddenimports` des deux `.spec` + message dédié « série compressée ». — C-07, C-33
8. **(M)** Registre : `dicom_date_to_iso` → `""` + saisie de la date à l'export si absente ; `QCRun.date` sans repli sur aujourd'hui ; `add_run` conserve `corrective_action*` lors d'un remplacement. — C-12, C-13, C-16
9. **(M)** `DeviceManagerDialog` : gestion `OSError` sur `write_text`/`delete_device`/`save_app_config` ; refus de basculer sur une base avec `load_error`. — C-11
10. **(S)** `history_panel`/`main_window` : `html.escape` des textes libres, `try/except OSError` sur le CSV, test d'existence du PDF au clic seulement + contrôle d'extension. — C-19, C-20, C-26
11. **(S)** `_search_run_folder` : progression modale ; `_load_dicom_folder` retourne `bool` et n'affecte l'état qu'en fin ; `_geometry_mismatch` porté par les résultats. — C-18, C-22, C-23
12. **(S)** `logging` applicatif + `try/finally` autour des figures matplotlib ; `AppConfig.save` atomique via helper partagé ; validation des types dans `AppConfig.load`. — C-24, C-25, C-27, C-15
13. **(M)** Refactor GUI : supprimer les `QLineEdit` cachés (lire `self._current_device`), scinder `main_window.py` en modules, extraire `results_html.py` pur et testable, `_refresh_all()` sans `hasattr`. — C-42, C-43, C-44, C-40
14. **(M)** Thread de chargement (`QThreadPool`) avec progression et annulation. — C-39
15. **(S)** PDF : `Paragraph` pour les cellules libres, `getPageNumber()`. — C-30, C-46
16. **(S)** Installeurs : Python 3.12/3.13 et `--user` dans le `.bat`, PEP 668 + `read < /dev/tty` + contrôle de version dans le `.sh`, version passée à `iscc` par le workflow. — C-36, C-37, C-38
17. **(M)** Tests unitaires listés en section 4 (NPS, water_phantom, dicom_loader, app_config, main, pdf_report, panneau historique) ; déplacer les générateurs de figures hors de `tests/`. — C-32, C-47
