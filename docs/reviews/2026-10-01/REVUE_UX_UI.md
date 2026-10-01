# Revue UX/UI — CQ TDM (date 2026-10-01, commit 0c60fae)

Version évaluée : CQ TDM 0.7.0 (`src/cq_tdm/__init__.py`), PySide6 6.11.2, exécution hors écran (`QT_QPA_PLATFORM=offscreen`).
Toutes les localisations sont de la forme `chemin:ligne` relatives à la racine du dépôt `/home/user/cq-tdm`. Aucun fichier du dépôt n'a été modifié.

## 1. Résumé exécutif

CQ TDM est un logiciel métier déjà mûr : le parcours « charger → reconnaître l'installation → analyser → inspecter → exporter » est largement guidé (bouton d'accueil accentué, sélecteur d'installation qui change d'état, résumé sous le sélecteur avec liens d'action, curseur de coupe à marqueurs glissables, historique avec tendance et bande de tolérance), la terminologie réglementaire est globalement respectée et le rapport PDF reprend les éléments du registre des opérations. L'effort de guidage « que faire maintenant » (`primary_button_style`, liens « définir les valeurs actuelles comme références », « mémoriser ces coupes ») est la grande force du produit.

Les cinq problèmes les plus pénalisants pour un physicien ou un manipulateur :
1. **Un rapport peut être exporté et affiché « CONFORME » alors que l'inspection des artéfacts n'a pas été faite ou que les valeurs de référence manquent** (`pdf_report.py:696-740`, `main_window.py:1973-1993`) : risque de fausse conformité dans un document réglementaire.
2. **Les boutons standard des dialogues sont en anglais** (« OK / Cancel / Yes / No », boîtes de fichiers) car aucun `QTranslator` Qt n'est installé (`main.py:206-210`) ; la confirmation de suppression d'une installation se répond par « Yes/No ».
3. **Saisies perdues sans avertissement** dans « Gestion des installations » (fermeture par la croix, changement de ligne dans la liste : `main_window.py:394-411`) et **suppression d'une installation sans dire que ses contrôles enregistrés disparaissent** (`main_window.py:673-691`).
4. **L'interface gèle sans retour visuel** pendant le chargement DICOM, le calcul SPB et la génération PDF (tout est synchrone dans le thread GUI ; pas de curseur d'attente, pas de barre de progression : `main_window.py:1797-1864, 2327-2356, 2003-2085`).
5. **Lisibilité à 1366×768 et en thème clair** : fenêtres secondaires plus hautes que l'écran (960×780 et 1200×900), overlay d'informations qui recouvre la ROI 12h, libellés de ROI qui se chevauchent, textes #666/#888 sous les seuils WCAG, overlay « Afficher/Cacher » illisible en thème clair, graphique SPB toujours sombre.

## 2. Méthode

- **Lecture intégrale** : `README.md`, `docs/screenshot.png`, `src/cq_tdm/main.py` (249 l.), `gui/main_window.py` (3600 l.), `gui/image_viewer.py` (1938 l.), `gui/history_panel.py` (446 l.), `gui/theme.py` (88 l.), `core/trend_chart.py` (135 l.), `reports/pdf_report.py` (1586 l.), `installers/*` (4 fichiers), `core/app_config.py`, extraits de `core/qc_history.py`, `core/device_database.py`, `core/dicom_loader.py`, `core/utils.py`, `tests/conftest.py`, `tests/test_history_home_view.py`, `tests/test_phantom_detection.py`.
- **Textes réglementaires** : `decision_ansm.txt` (sections 2.1 périodicités, 3.2 registre des opérations, 9.1.7 fantôme d'eau et critères), `guide_application.txt` (survol).
- **Vérification dynamique réalisée** (l'installation `pip install -e ".[dev]"` était prête) : script `scratchpad/ux/capture.py` qui instancie `MainWindow` hors écran avec une configuration en mémoire (jamais `~/.config`), une base d'installations factice (1 installation, 8 contrôles dont 3 NC) et une série synthétique de 24 coupes (fantôme d'eau 512×512, 0,469 mm/px). 5 scénarios × captures : état vide, accueil avec historique, série chargée non reconnue, série chargée reconnue (+ dialogues d'artéfacts, d'installation, d'informations image, coupes modifiées, W/L 80/0), thème clair ; tailles 900×600 (minimum), 1366×728 (poste 1366×768 avec barre des tâches) et 1600×1000. 56 captures PNG dans `scratchpad/ux/`.
- **PDF** : deux rapports générés avec `generate_pdf_report` (cas complet avec historique et notes ; cas NC sans référence, sans inspection d'artéfacts, champs vides), convertis avec `pdftoppm -r 60` (`pdf_ok-1..7.png`, `pdf_nc-1..5.png`) et `pdftotext -layout`.
- **Contrastes** : ratios WCAG 2 calculés par `scratchpad/ux/contrast.py` sur les couples de `theme.py`, des feuilles de style inline, de `trend_chart.py` et de `pdf_report.py` (tableau en §6).
- **Limites** : pas de vraie série DICOM (`test_data/` absent du dépôt) ; pas de test sur Windows ni en HiDPI réel ; les captures hors écran n'exécutent pas les timers (le graphique de tendance a été redessiné à la main) ; les boîtes `QMessageBox` statiques ont été remplacées par un stub pour éviter le blocage modal (leurs boutons « Yes/No » sur les captures sont ceux du stub, mais le constat U-02 est confirmé par les `QDialogButtonBox` réels des dialogues Notes et Configuration des rapports, qui affichent « OK / Cancel »).

## 3. Parcours utilisateur

Flux réel observé dans le code, confronté aux 7 étapes du README.

| Étape README | Ce que fait réellement l'interface | Frictions observées |
|---|---|---|
| 1. Lancer : l'historique de la dernière installation est affiché | `MainWindow.__init__` sélectionne `_default_device()` et ouvre l'onglet « Historique » (`main_window.py:1240-1242`). Accueil = écran « Aucune image chargée » + bouton accentué « Ouvrir un dossier DICOM... » (`image_viewer.py:977-993`). | **Premier lancement sans aucune installation** : l'historique affiche « Sélectionnez une installation ci-dessus pour afficher l'historique de ses contrôles » (`main_window.py:2514`) alors que la liste déroulante ne contient que « Aucune installation sélectionnée » ; rien n'indique qu'il faut d'abord charger une série puis créer l'installation. Les contrôles de coupes (« Coupe UH », « Coupes SPB », boutons ↓) sont actifs sans image (capture `empty_02`). À 900×600 le résumé de l'historique est tronqué (pas de retour à la ligne, `history_panel.py:98`). |
| 2. Charger une série DICOM (installation reconnue automatiquement) | Menu Fichier, Ctrl+O, bouton d'accueil, glisser-déposer (`main_window.py:1746-1795`). Chargement synchrone (`:1800-1801`), analyse UH après 300 ms et SPB après 1 s (debounce). Reconnaissance par identité DICOM (`:3495-3531`). | **Aucun retour pendant le chargement** (barre de statut « Chargement du dossier: … » mise à jour avant un appel bloquant, pas de curseur d'attente, pas de progression). Le dialogue de dossier s'ouvre toujours sur le répertoire courant (`:1749-1753`, troisième argument `""`) : le dernier dossier n'est pas mémorisé. Fichiers partiellement illisibles ignorés en silence (`:1803-1811`, seul le cas « aucun fichier » avertit). Échec de détection du fantôme non signalé (`PhantomGeometry.num_edge_points == 0` jamais lu par la GUI). |
| 3. Choisir les coupes sous le curseur ; « Réinit. coupes » | Marqueurs UH (triangle jaune) et SPB (bande verte) glissables (`image_viewer.py:99-444`), spinboxes + boutons « ↓ » (`:1316-1370`), ligne de résumé « Coupes UH 14 · SPB 8–17 ≠ enregistrées (…) — mémoriser ces coupes » (`main_window.py:3403-3425`). | Bon retour visuel. Mais la touche **R** (« Réinitialiser la vue ») remet aussi les coupes d'analyse au centre de la série (`image_viewer.py:1463-1489`) → relance une analyse sur d'autres coupes que celles enregistrées, à l'insu de l'utilisateur. Les ROI UH ne sont dessinées que sur la coupe UH (`image_viewer.py:645-648`) : en feuilletant, elles « disparaissent » sans explication. Boutons « ↓ » sans texte. |
| 4. « Nouvelle installation » / « Modifier… » → fenêtre des installations | `DeviceManagerDialog` (`main_window.py:92-811`) : liste à gauche, formulaire long à droite (6 champs identité, 2 références, 5 champs registre, sections lecture seule, 3 boutons d'action enfouis), bouton unique « Enregistrer les modifications ». | Fenêtre de 960×780 par défaut (`:118-119`), plus haute qu'un écran 1366×768 utile. Pas de bouton Fermer/Annuler, pas de détection de modifications non enregistrées ; changer de ligne dans la liste écrase la saisie (`:394-411`). Aucun champ marqué obligatoire, date de mise en service en texte libre. Le chemin de la base est écrit en #666/11 px (ratio 2,4:1, `:151-153`). |
| 5. Vérifier les artéfacts, ajouter une observation | `ArtifactInspectionDialog` modal 1200×900 (`image_viewer.py:1664-1938`) à W 80 / L 0 figé, deux gros boutons vert/orange ; `NotesEditorDialog` (« Notes ») markdown simplifié. | Dialogue plus haut qu'un écran 1366×768 ; navigation entre coupes uniquement par le curseur (pas de molette, pas de flèches, les raccourcis ←/→ de la fenêtre principale ne s'appliquent pas au dialogue) alors que l'ANSM demande d'inspecter « l'ensemble des coupes » ; pas de bouton Annuler visible ; le bouton « Présence d'artéfacts » désactivé reste orange (pas de règle `:disabled`). L'observation s'appelle tour à tour « observation » (bouton), « Notes » (titre du dialogue et section PDF). |
| 6. « Enregistrer les résultats et exporter le PDF » | `_export_pdf` (`main_window.py:1973-2085`) : avertit seulement si aucune image, aucune analyse, ou installation non enregistrée ; dialogue de fichier ; génération synchrone ; ouverture du PDF ; ajout à l'historique. | **Aucun contrôle de complétude** (artéfacts non inspectés, références absentes) : le PDF peut porter le bandeau « CONFORME ». Pas de retour pendant la génération (plusieurs secondes : 4 figures matplotlib). Le succès n'est signalé que dans la barre de statut, message permanent (`:2077`). Libellés divergents entre menu (« Exporter rapport PDF... ») et bouton. |
| 7. Onglet « Historique » | `HistoryPanel` : tableau 9 colonnes, graphique cliquable, 6 boutons d'action, export CSV. | Boutons tronqués sous 1366 px ; double-clic « Détail du contrôle » non découvrable ; CSV avec décimales à point et séparateur « ; » (illisible comme nombres dans Excel FR) ; abréviations d'en-tête sans infobulle ni unité. |

## 4. Constats

### [U-01] Rapport PDF exportable « CONFORME » avec des tests non réalisés
- **Sévérité** : Bloquant
- **Axe** : PDF / Erreurs
- **Localisation** : `src/cq_tdm/reports/pdf_report.py:696-740` (`_compute_overall_status`), `src/cq_tdm/gui/main_window.py:1973-1993` (`_export_pdf`), `pdf_report.py:1004-1008` (« Statut : Non inspecté »), `pdf_report.py:1444-1458` (« Bruit : … » sans statut)
- **Constat** : `_compute_overall_status` ignore `artifact_result is None` et les références absentes ; le bandeau de première page affiche « CONFORME » dès que nombre CT et uniformité passent. `_export_pdf` ne vérifie ni l'inspection des artéfacts ni les références. Le rapport NC de démonstration (`pdf_nc-1.png`, `pdf_nc-5.png`) montre « Bruit : 3,30 HU » sans verdict et « Statut : Non inspecté » en page 5, invisible depuis le résumé.
- **Impact** : document réglementaire (registre des opérations, 3.2.2 : « état de la conformité de chaque élément testé ») pouvant affirmer une conformité globale alors que deux des cinq tests (artéfacts, stabilité bruit/SPB) n'ont pas été évalués. Risque d'audit et de décision clinique erronée.
- **Proposition** : (1) avant l'export, un dialogue « Contrôle incomplet » listant les manques avec cases : « Inspection visuelle des artéfacts non effectuée », « Valeurs de référence non renseignées (bruit, SPB) », « Installation non enregistrée » ; boutons « Compléter » (par défaut) / « Exporter quand même ». (2) Dans le PDF, remplacer le bandeau par « CONTRÔLE INCOMPLET » (gris) quand un test manque, et écrire dans le résumé « Artéfacts : non inspectés — test non réalisé », « Bruit : 3,30 UH — non évalué (référence absente) ». (3) Dans le résumé de l'écran, afficher le même état global que le PDF (« Statut global : incomplet / conforme / non conforme »).

### [U-02] Boutons standard et boîtes de dialogue Qt en anglais
- **Sévérité** : Majeur
- **Axe** : Libellés / Conventions
- **Localisation** : `src/cq_tdm/main.py:206-210` (création de `QApplication`, aucun `QTranslator`) ; `main_window.py:921-924, 1082-1085, 1151-1152` (`QDialogButtonBox.Ok|Cancel`) ; `main_window.py:623-631, 659-662, 679-685`, `history_panel.py:324-328` (`QMessageBox.question` Yes/No) ; tous les `QFileDialog`.
- **Constat** : captures `empty_08_ReportSettings.png` et `empty_09_NotesEditor.png` : boutons « OK » et « Cancel ». Les questions destructives se répondent par « Yes / No ». Les boîtes de fichiers (quand le style non natif est utilisé) affichent « Open / Cancel / Look in ».
- **Impact** : rupture de langue sur les actions les plus sensibles (suppression, écrasement de références) ; utilisateurs manipulateurs non anglophones.
- **Proposition** : dans `main()`, charger la traduction Qt : `translator = QTranslator(); if translator.load(QLocale.system(), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)): app.translate…; app.installTranslator(translator)` (PySide6-Essentials embarque `qtbase_fr.qm`). En complément, remplacer les `Yes|No` par des boutons explicites : `box.addButton("Supprimer", ButtonRole.DestructiveRole)` / `addButton("Annuler", RejectRole)`, et `Ok|Cancel` par « Enregistrer » / « Annuler » via `button(StandardButton.Ok).setText("Enregistrer")`.

### [U-03] Saisies perdues sans avertissement dans « Gestion des installations »
- **Sévérité** : Majeur
- **Axe** : Formulaires / Erreurs
- **Localisation** : `src/cq_tdm/gui/main_window.py:92-120` (dialogue sans `closeEvent`/`reject` gardés), `:394-411` (`_on_device_selected` recharge le formulaire), `:356-359` (seul bouton « Enregistrer les modifications »), `:702-722, 802-811` (changement de base sans garde)
- **Constat** : aucune détection de formulaire modifié. Fermer la fenêtre par la croix, cliquer une autre installation dans la liste, « Ouvrir… » une autre base : la saisie en cours est perdue silencieusement. Il n'y a ni bouton « Fermer » ni « Annuler ».
- **Impact** : perte de temps et d'informations réglementaires (registre, références) ; incohérence avec la convention Qt (zone de boutons en bas à droite).
- **Proposition** : suivre un drapeau `_dirty` (connecter `textChanged` de tous les `QLineEdit`), activer « Enregistrer » seulement si `_dirty`, intercepter `closeEvent`/`reject` et le changement de ligne avec `QMessageBox` « Modifications non enregistrées — Enregistrer / Ne pas enregistrer / Annuler ». Ajouter un `QDialogButtonBox` « Enregistrer » (par défaut) + « Fermer » sous le formulaire, et afficher un astérisque dans le titre « Détails de l'installation * » quand `_dirty`.

### [U-04] Suppression d'une installation : l'historique disparaît sans le dire, sans sauvegarde
- **Sévérité** : Majeur
- **Axe** : Erreurs
- **Localisation** : `src/cq_tdm/gui/main_window.py:673-691` (`_delete_selected_device`), `src/cq_tdm/core/device_database.py:294-300` (`delete_device`)
- **Constat** : « Voulez-vous vraiment supprimer l'installation '…' ? » (Yes/No) ; les N contrôles enregistrés (`device.runs`) sont supprimés avec elle, sans mention ni copie de sauvegarde.
- **Impact** : perte irréversible d'un historique réglementaire (résultats « présentés jusqu'au contrôle de qualité externe suivant », 3.2.2) sur un simple « Yes ».
- **Proposition** : message « Supprimer l'installation « CHU Exemple – Discovery RT – N° inv. INV-042 » et ses 8 contrôles enregistrés ? Les rapports PDF et les images DICOM ne sont pas supprimés. Cette action est irréversible. » avec bouton destructif « Supprimer » (non par défaut) ; écrire une copie `devices.json.bak-AAAAMMJJ-HHMM` avant suppression (ou exiger la saisie du n° d'inventaire si `len(runs) > 0`).

### [U-05] L'interface gèle sans retour pendant le chargement, le calcul SPB et la génération du PDF
- **Sévérité** : Majeur
- **Axe** : Parcours / Erreurs
- **Localisation** : `src/cq_tdm/gui/main_window.py:1800-1801` (`load_dicom_folder` synchrone), `:2327-2349` (`analyze_nps`), `:2010-2076` (`generate_pdf_report` puis 4 figures matplotlib) ; aucun `setOverrideCursor`, `QProgressDialog` (sauf la recherche de série `:2883-2888`) ni `QThread` dans `src/cq_tdm/gui`.
- **Constat** : la barre de statut est mise à jour avant l'appel bloquant (« Chargement du dossier: … », « Analyse SPB en cours... », « Génération du rapport... ») mais la fenêtre ne se repeint pas tant que le calcul tourne ; sur une série de 233 coupes ou un poste lent l'application semble plantée (« Ne répond pas » sous Windows).
- **Impact** : clics répétés, fermeture forcée, doute sur le résultat.
- **Proposition** : a minima `QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)` + `processEvents()` autour des trois opérations et une `QProgressDialog` indéterminée (« Chargement de 233 fichiers DICOM… », « Calcul du spectre de puissance du bruit… », « Génération du rapport PDF… ») avec `setMinimumDuration(400)`. Mieux : `QThread`/`QRunnable` pour le chargement et le SPB, boutons d'analyse désactivés pendant l'exécution, puis message de fin dans la barre de statut avec délai.

### [U-06] Fenêtres secondaires plus hautes qu'un écran 1366×768
- **Sévérité** : Majeur
- **Axe** : Disposition
- **Localisation** : `src/cq_tdm/gui/main_window.py:118-119` (`setMinimumSize(860, 600)`, `resize(960, 780)`), `src/cq_tdm/gui/image_viewer.py:1680-1681` (`setMinimumSize(800, 600)`, `resize(1200, 900)`)
- **Constat** : la hauteur utile d'un poste 1366×768 est d'environ 700-730 px. « Gestion des installations » (780 px) et « Inspection des artéfacts » (900 px) dépassent l'écran : les boutons « Enregistrer les modifications », « Absence / Présence d'artéfacts » peuvent être hors écran ou la fenêtre est rognée par le gestionnaire de fenêtres.
- **Impact** : actions principales inaccessibles sur le matériel hospitalier courant.
- **Proposition** : dimensionner par rapport à l'écran : `geo = self.screen().availableGeometry(); self.resize(min(1200, int(geo.width()*0.9)), min(900, int(geo.height()*0.9)))`, et abaisser les minimums (`860×600` → `820×560` ; `800×600` → `700×520`). Mémoriser la taille des deux dialogues (voir U-13).

### [U-07] Touche « R » : réinitialise aussi les coupes d'analyse
- **Sévérité** : Majeur
- **Axe** : Visionneuse / Erreurs
- **Localisation** : `src/cq_tdm/gui/main_window.py:1457, 1515-1517` (`_reset_view` → `_reset_all`), `src/cq_tdm/gui/image_viewer.py:1463-1489` (`_reset_all` remet coupe UH et plage SPB au centre), dialogue des raccourcis `main_window.py:1543` (« R : Réinitialiser la vue (zoom 100%) »)
- **Constat** : « R » est annoncé comme un reset de vue (zoom) mais déplace la coupe UH et la plage SPB vers le centre de la série et relance l'analyse ; le zoom est en réalité « ajusté à la vue » et non 100 %.
- **Impact** : une pression accidentelle change les coupes de mesure (donc les résultats et l'écart aux coupes enregistrées) sans confirmation ; l'utilisateur peut exporter un contrôle sur de mauvaises coupes.
- **Proposition** : limiter « R » à zoom/pan/fenêtrage ; déplacer la remise à zéro des coupes derrière « ⟲ Réinit. coupes » (qui revient aux coupes enregistrées) et, s'il n'y a pas de coupes enregistrées, un item de menu « Coupes par défaut (centre de la série) ». Corriger le texte du raccourci : « R — Réinitialiser l'affichage (zoom ajusté, fenêtrage 400/40) ».

### [U-08] Échec de détection du fantôme invisible pour l'utilisateur
- **Sévérité** : Majeur
- **Axe** : Visionneuse / Erreurs
- **Localisation** : `src/cq_tdm/core/dicom_loader.py:402, 522-567` (`num_edge_points = 0` = géométrie de repli centrée sur l'image, rayon = diamètre de reconstruction) ; aucune lecture de `num_edge_points` dans `src/cq_tdm/gui/` ; le contour détecté n'est visible que via « Aide › Mode debug (centre/périmètre fantôme) » (`main_window.py:1277-1280`).
- **Constat** : si le fantôme n'est pas trouvé (fantôme très décentré, bulle, table), les ROI sont placées sur une géométrie par défaut et les résultats affichés normalement, sans avertissement. L'option qui permettrait de le voir est rangée dans « Aide » sous un nom de développeur.
- **Impact** : mesures faites hors de l'eau (paroi, air) prises pour des résultats valides.
- **Proposition** : propager un drapeau `fallback` dans `WaterPhantomResults`/`NPSResult` ; afficher dans « Résultats » un encart orange « Fantôme non détecté automatiquement : ROI placées au centre de l'image (Ø = champ de vue). Vérifiez la position des ROI sur l'image. » et un message de statut persistant ; dessiner automatiquement le contour détecté (magenta pointillé) tant que l'encart est affiché. Déplacer l'option dans « Affichage › Afficher le contour détecté du fantôme ».

### [U-09] Fichiers DICOM illisibles ignorés en silence
- **Sévérité** : Majeur
- **Axe** : Erreurs
- **Localisation** : `src/cq_tdm/gui/main_window.py:1803-1811` (avertissement seulement si `series.is_empty`), `:1851` (« N images chargées depuis … »)
- **Constat** : si 3 fichiers sur 20 échouent, la barre de statut dit « 17 images chargées » et rien d'autre. Le nombre de coupes compte pour le SPB (10 coupes exigées) et pour l'inspection « sur l'ensemble des coupes ».
- **Impact** : série incomplète analysée sans que l'opérateur le sache ; décalage entre coupes enregistrées et coupes chargées (voir aussi « Série incomplète » `:2824-2830`).
- **Proposition** : si `series.load_errors` n'est pas vide, `QMessageBox.warning` « 17 images chargées, 3 fichiers ignorés » avec `setDetailedText("\n".join(series.load_errors))`, et mention dans la barre de statut « 17 images chargées · 3 fichiers ignorés (détails : Affichage › Informations image) ».

### [U-10] Unités « HU » affichées alors que les libellés et la décision disent « UH »
- **Sévérité** : Majeur
- **Axe** : Libellés
- **Localisation** : `src/cq_tdm/gui/main_window.py:2976-3030` (« -0,8 HU », « ±7 HU (NCG: ±25 HU) »), `:3205-3216`, `:3155` (« W: 80, L: 0 UH »), `history_panel.py:35` (« Bruit σ »), `qc_history.py:28-31` (« (HU) »), `main_window.py:246, 3400` (« σ réf. 8,18 HU »), `pdf_report.py:155, 300, 764, 1207-1210, 1259-1270, 1302-1310, 1458`, `image_viewer.py:1723` (« 0 UH »), `trend_chart.py` via `METRICS`.
- **Constat** : mélange permanent : « Coupe UH », « ROIs UH », « Fenêtre … 80 UH » contre « -0,8 HU », « Bruit σ (HU) », « SPB (HU²·mm²) », et dans le PDF « Centre (L) = 0 UH » suivi de l'image titrée « (L=0, W=80 HU) » (`pdf_ok-6.png`). La décision ANSM utilise « UH ».
- **Impact** : document réglementaire incohérent ; charge de lecture.
- **Proposition** : adopter « UH » partout dans l'interface et le PDF (y compris « UH²·mm² » ou « UH²·mm² » pour le SPB), garder « HU » uniquement dans le code et le CSV (en-têtes `*_UH`). Centraliser dans `core/utils.py` une constante `UNIT_HU = "UH"`.

### [U-11] Overlay d'informations et libellés de ROI recouvrent les ROI
- **Sévérité** : Majeur
- **Axe** : Visionneuse
- **Localisation** : `src/cq_tdm/gui/image_viewer.py:1264-1272` (overlay haut-gauche, marge 15 px), `:772-801` (`_draw_roi` : libellé en `center_x + radius + 2`, `QGraphicsTextItem` non cosmétique), `main_window.py:2196-2229` (noms « C », « 12h »…), `:2246-2255` (« SPB1 »…)
- **Constat** : captures `loaded_04_results_1366x728.png` : le cartouche d'informations (7 lignes) couvre la ROI 12h et le haut du fantôme ; « SPB8 » est écrit sur le cercle 3h, « SPB1 »/« SPB3 » sur les carrés voisins, « C » à l'intérieur de SPB8 ; la police des libellés suit le zoom (≈ 7 px à 82 %). L'overlay bas-droit « Afficher/Cacher » masque l'angle inférieur droit.
- **Impact** : impossible de vérifier visuellement que les ROI sont dans l'eau (exigence de la méthode) sans masquer les infos ou zoomer.
- **Proposition** : (1) rendre les libellés cosmétiques : `text.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)` avec fond semi-transparent ; (2) placer le libellé **au centre** des ROI SPB (carrés) et des ROI périphériques, et « C » au centre de la ROI centrale ; (3) réduire le cartouche à 3 lignes (date · scanner · kV/mAs/filtre/épaisseur) avec le détail en infobulle, ou le positionner **sous** la zone image (barre d'information), et le replier automatiquement si le rectangle de l'image le chevauche ; (4) déplacer les interrupteurs UH/SPB/Infos dans la ligne « Réinit. W/L » (cases à cocher colorées) pour libérer l'image.

### [U-12] Troncatures à la taille minimale et répartition du splitter
- **Sévérité** : Majeur
- **Axe** : Disposition
- **Localisation** : `src/cq_tdm/gui/main_window.py:1171` (`setMinimumSize(900, 600)`), `:1428-1430` (`splitter.setSizes([700, 500])`, pas de `setStretchFactor`), `history_panel.py:144-169` (6 boutons sur une ligne), `:98-100` (résumé sans `setWordWrap`), `image_viewer.py:1321, 1338` (« Coupe UH: », « Coupes SPB: »)
- **Constat** : à 900×600 (`empty_01_default_size.png`) : « Coupe UH » devient « Coupe », les boutons de l'historique affichent « comme ré », « uvrir le PD », « er la série », « on correcti », le résumé est coupé. À 1366×728 la visionneuse n'obtient que 586 px : image à 82 % avec de larges bandes noires, panneau de droite à 760 px dont la moitié est vide.
- **Impact** : fenêtre inutilisable à sa taille minimale ; image trop petite sur l'écran cible.
- **Proposition** : `setMinimumSize(1100, 680)` ; `splitter.setStretchFactor(0, 3); setStretchFactor(1, 2)` ; remplacer la rangée de 6 boutons par une `QToolBar` à icônes + texte court avec menu « Plus… » (ou deux rangées) ; `self._summary.setWordWrap(True)` ; libellés « UH : » / « SPB : » avec infobulles.

### [U-13] Préférences non persistées : taille de fenêtre, splitter, dernier dossier, dernier dossier d'export
- **Sévérité** : Majeur
- **Axe** : Conventions / Parcours
- **Localisation** : `src/cq_tdm/main.py:240-243` (seul `--size` ajuste la fenêtre), `main_window.py:1749-1753` (`getExistingDirectory(self, "Ouvrir dossier DICOM", "")`), `:2003-2008` (export PDF dans le répertoire courant), `history_panel.py:374`, `core/app_config.py:11-29` (aucun champ de géométrie ni de dossiers)
- **Constat** : à chaque lancement : fenêtre 900×600, splitter 700/500, dialogues de fichiers ouverts sur le répertoire courant du processus (sous Windows : dossier de l'exécutable).
- **Impact** : navigation répétée jusqu'au dossier d'export PACS et au dossier des rapports à chaque contrôle ; fenêtre à redimensionner à chaque session.
- **Proposition** : `QSettings("CQ TDM", "CQ TDM")` : `saveGeometry()/restoreGeometry()` dans `closeEvent`/`__init__`, `splitter.saveState()`, `last_dicom_dir`, `last_report_dir` (ou ajouter ces champs à `AppConfig`). Ouvrir le dialogue d'export dans `last_report_dir` et proposer « Fichier › Dossiers récents ».

### [U-14] Inspection des artéfacts : navigation limitée au curseur, pas d'annulation visible
- **Sévérité** : Majeur
- **Axe** : Visionneuse / Parcours
- **Localisation** : `src/cq_tdm/gui/image_viewer.py:1695-1710` (slider seul), `:1712-1718` (`setDragMode(NoDrag)`, pas de `wheelEvent` de coupe), `:1735-1780` (deux boutons sans « Annuler »), `main_window.py:1439-1446` (raccourcis ←/→ attachés à la fenêtre principale, inactifs dans le dialogue)
- **Constat** : pour parcourir « l'ensemble des coupes » (9.1.7.2) l'utilisateur ne dispose que du curseur ; molette, flèches, Page↑/↓ sont inopérants ; aucune indication des coupes déjà vues ; aucun bouton « Annuler » (seule Échap ferme, non indiquée) ; pas de zoom pour examiner un artéfact fin.
- **Impact** : inspection fastidieuse ou bâclée ; test visuel pourtant décisif pour la conformité.
- **Proposition** : dans le dialogue, molette = coupe suivante/précédente, Ctrl+molette = zoom, ←/→, Page↑/↓, Début/Fin ; compteur « Coupes vues : 12/24 » et bouton « Absence d'artéfacts » grisé tant que toutes les coupes n'ont pas été affichées (ou avertissement) ; bouton « Annuler » (`RejectRole`) à gauche ; infobulle « Molette : coupe · Ctrl+molette : zoom ». Conserver W/L figé.

### [U-15] Export CSV illisible comme nombres dans Excel français
- **Sévérité** : Majeur
- **Axe** : Historique
- **Localisation** : `src/cq_tdm/gui/history_panel.py:371-392` (`delimiter=";"`, floats bruts `run.water_ct`, `run.noise`…)
- **Constat** : séparateur « ; » (convention FR) mais décimales avec point (« 8.21 ») : Excel/LibreOffice en locale française importent ces colonnes comme du texte.
- **Impact** : l'export CSV, seul moyen de retraiter l'historique, n'est pas exploitable sans manipulation.
- **Proposition** : écrire les nombres avec `format_fr(…)` (virgule) et en-têtes avec unités (« ct_eau_UH »), ou proposer deux options dans le dialogue d'enregistrement (« CSV Excel français (;) » / « CSV standard (,) »). Ajouter une ligne d'en-tête de contexte (installation, références en vigueur, version du logiciel).

### [U-16] Thème clair : overlay « Afficher/Cacher » illisible et graphique SPB sombre
- **Sévérité** : Majeur
- **Axe** : Accessibilité
- **Localisation** : `src/cq_tdm/gui/image_viewer.py:1120-1121` (titre `color: #ccc`), `:1137-1146` (« UH » `#ffcc00`, « SPB » `#00cc00`, « Infos » `#cccccc` sur fond `rgba(240,240,240,200)`), `main_window.py:3092-3146` (`_generate_nps_plot` couleurs fixes `#2b2b2b`/`#1e1e1e`), `:2941-2943` (`.ok #4caf50`, `.nc #ff9800` sur fond blanc)
- **Constat** : capture `light_04` : « Afficher/Cacher : », « UH », « Infos » quasi invisibles (ratios 1,3-1,9:1) ; `light_07` : bloc graphique sombre dans un panneau blanc ; statuts « Conforme » 2,8:1 et « Non conforme » 2,2:1 sur blanc.
- **Impact** : le thème clair, souvent choisi en salle éclairée, est partiellement inutilisable.
- **Proposition** : tirer toutes les couleurs de `theme_colors()` : ajouter `overlay_text`, `roi_uh`, `roi_spb`, `plot_bg`, `plot_fg`, `ok`, `nc`, `ncg` par thème (clair : `#1b5e20`, `#e65100`, `#b71c1c` ; sombre : valeurs actuelles). Passer la palette à `_generate_nps_plot` comme le fait `trend_chart.py`.

### [U-17] Textes secondaires sous les seuils de contraste (thème sombre)
- **Sévérité** : Majeur
- **Axe** : Accessibilité
- **Localisation** : `src/cq_tdm/gui/main_window.py:151-153` (chemin de base `#666`, 11 px), `:280-299, 304-335` (sections lecture seule `#888`), `:361` (statut `#888` 11 px), `:1352` (résumé d'installation `#888` 11 px), `history_panel.py:99`, `image_viewer.py:993-995` (`#666` sur `#1e1e1e`), `theme.py:44` (`pending #888888`), `main_window.py:2943` (`.ncg #f44336`)
- **Constat** : ratios calculés (§6) : `#666/#2d2d2d` = 2,4:1, `#666/#1e1e1e` = 2,9:1, `#888/#2d2d2d` = 3,9:1, `#f44336/#2b2b2b` = 3,85:1 (texte gras 12 px, donc « normal »), bouton accent blanc sur `#2a82da` = 3,96:1. Le chemin de la base de données (information de traçabilité) est illisible (`home_09`).
- **Impact** : informations clés (références, coupes, chemin de la base) difficiles à lire pour tous, impossibles pour une vue baissée.
- **Proposition** : utiliser `text_secondary` (`#aaaaaa`, 6,1:1) à la place de `#888`/`#666` ; porter `pending` à `#9e9e9e` (sombre) et `#757575` (clair) ; NCG `#ff6659` ; accent sombre `#1e6fc4` (blanc dessus = 5,0:1) ; taille minimale 12 px pour tout texte informatif.

### [U-18] Boîtes de messages génériques et messages d'exception bruts
- **Sévérité** : Majeur
- **Axe** : Erreurs
- **Localisation** : `src/cq_tdm/gui/main_window.py:1811, 1976, 1980, 2092, 2120, 2444` (titre « Attention »), `:1864, 2085, 2113, 2194` (titre « Erreur », corps `f"…:\n{e}"`), `:1863, 2084, 2312, 2356` (barre de statut « Erreur: {e} » permanente), `:2492-2500` (titre « Base de données des appareils »)
- **Constat** : titres non informatifs (« Attention », « Erreur ») ; corps constitué du texte de l'exception Python (souvent en anglais : « Folder not found », erreurs pydicom) ; aucune action proposée ; le message d'erreur reste dans la barre de statut jusqu'au prochain événement.
- **Impact** : l'utilisateur ne sait pas quoi faire ; capture d'écran inexploitable pour le support.
- **Proposition** : titre = contexte (« Chargement DICOM », « Export du rapport »), texte = cause en français + action (« Le dossier ne contient aucun fichier DICOM lisible. Vérifiez qu'il s'agit du dossier de la série exportée (fichiers .dcm ou sans extension). »), `setDetailedText(traceback)` pour les détails techniques, bouton « Ouvrir le journal » (voir U-41). Donner un délai (8 s) aux messages de statut d'erreur et les préfixer d'un « ⚠ ».

### [U-19] Premier lancement sans installation : état vide non guidant
- **Sévérité** : Majeur
- **Axe** : Parcours
- **Localisation** : `src/cq_tdm/gui/main_window.py:2510-2521` (`_refresh_history` : placeholder « Sélectionnez une installation ci-dessus… » même si la base est vide), `:64-67` (libellés), `image_viewer.py:1316-1370` (contrôles de coupes actifs sans image)
- **Constat** : capture `empty_02` : la liste déroulante ne contient que « Aucune installation sélectionnée », l'historique demande pourtant d'en sélectionner une ; aucune trace des 7 étapes du README ; les spinboxes « Coupe UH 1 », « Coupes SPB 1 → 1 » et les boutons « ↓ » sont actifs sans image.
- **Impact** : premier contact déroutant ; les étapes implicites (« il faut charger une série avant de créer l'installation ») ne sont pas dites.
- **Proposition** : si `self._device_db.get_all_devices()` est vide : placeholder « Aucune installation enregistrée. Chargez une série DICOM du fantôme d'eau (Ctrl+O) puis cliquez sur « Nouvelle installation » : l'installation est identifiée à partir des informations DICOM. » ; désactiver `slice_controls_widget` tant que `_current_series is None` ; ajouter en tête du panneau droit une liste d'étapes compacte (voir U-20).

### [U-20] Absence d'indicateur de progression du contrôle (liste de contrôle des 7 étapes)
- **Sévérité** : Majeur
- **Axe** : Parcours
- **Localisation** : `src/cq_tdm/gui/main_window.py:1326-1430` (`_setup_ui`, panneau droit), `:3374-3425` (`_update_install_summary` porte déjà l'état « références / coupes »)
- **Constat** : l'état d'avancement est dispersé : résumé sous le sélecteur (références, coupes), ligne « Inspection visuelle : Non effectuée » enfouie en bas des résultats, « ✓ » dans le texte du bouton d'observation, bouton d'export activé dès qu'une analyse existe.
- **Impact** : oublis (artéfacts, références, enregistrement de l'installation) découverts après l'export, ou jamais (U-01).
- **Proposition** : widget « Avancement du contrôle » sous le titre « Résultats d'analyse » : `☑ Série chargée (24 coupes) · ☑ Installation reconnue · ☐ Valeurs de référence · ☑ Coupes UH 12 / SPB 8–17 · ☐ Artéfacts inspectés · ☐ Observation (facultatif) · ☐ Rapport enregistré`, chaque élément cliquable vers l'action correspondante ; le bouton d'export passe en style accentué quand tout est coché et affiche « Enregistrer le contrôle et exporter le PDF (2 étapes manquantes) » sinon.

### [U-21] Libellés divergents pour la même action (export) et hiérarchie des boutons
- **Sévérité** : Mineur
- **Axe** : Libellés / Conventions
- **Localisation** : `src/cq_tdm/gui/main_window.py:1252` (« Exporter rapport PDF... », Ctrl+E), `:1423` (« Enregistrer les résultats et exporter le PDF »), `:1555` (dialogue des raccourcis), `:2003` (titre de dialogue « Exporter rapport PDF »), `:1412-1425` (trois boutons pleine largeur de même poids)
- **Constat** : le menu promet un simple export, le bouton promet un enregistrement ; aucun des trois boutons du bas n'est mis en avant alors que `primary_button_style` existe.
- **Impact** : doute sur la différence entre les deux commandes ; action finale non repérable.
- **Proposition** : un seul libellé « Enregistrer le contrôle et exporter le PDF… » (menu et bouton, Ctrl+S en plus de Ctrl+E) ; appliquer `primary_button_style()` au bouton d'export quand il est activé ; garder « Inspection visuelle des artéfacts » et « Observation… » en boutons standard.

### [U-22] Ponctuation française : espace avant « : », points de suspension, pluriel de ROI
- **Sévérité** : Mineur
- **Axe** : Libellés
- **Localisation** : voir le tableau §5 (`image_viewer.py:949, 1026, 1041, 1071, 1321, 1338, 1699` ; `main_window.py:160-168, 1250-1270, 1253-1254, 1800, 1863, 2077, 2084, 2110-2113, 2191-2194, 2465-2467, 3059-3061` ; `history_panel.py:35`)
- **Constat** : « Coupe: », « W: », « L: », « Zoom: », « Coupe UH: », « Chargement du dossier: », « Erreur: », « Rapport exporté: » sans espace (insécable) avant les deux-points ; « ... » (trois points) dans les menus et la fenêtre des installations contre « … » ailleurs ; « ROIs » (pluriel anglais) dans les menus, les raccourcis et les résultats ; « Artéfacts: Présence détectée (NC) ».
- **Impact** : impression de traduction approximative dans un logiciel réglementaire.
- **Proposition** : règle unique : « libellé&nbsp;: », « … » (U+2026), « ROI » invariable, « Non conforme » sans trait d'union (sauf « non-conformité »), « artéfact » avec accent partout (déjà le cas).

### [U-23] Fenêtre « Gestion des installations » : formulaire long, actions enfouies, champs non qualifiés
- **Sévérité** : Majeur
- **Axe** : Formulaires
- **Localisation** : `src/cq_tdm/gui/main_window.py:199-362` (`_setup_ui` du dialogue), `:224-226` (date de mise en service en `QLineEdit`), `:256, 316, 337` (trois boutons d'action dans le défilement), `:243-253` (références sans unité dans le libellé)
- **Constat** : 13 champs + 4 sections lecture seule défilent dans une seule colonne ; les actions « Définir les valeurs actuelles comme références », « Mémoriser les coupes actuelles », « Réinitialiser la géométrie des ROI » sont au milieu du défilement ; rien n'indique quels champs sont nécessaires au registre ni le format de la date ; les unités ne figurent que dans le placeholder (disparaît à la saisie).
- **Impact** : saisie incomplète (PDF avec « Non renseigné »), formats de date hétérogènes, actions importantes manquées.
- **Proposition** : `QTabWidget` à trois onglets « Identification », « Registre des opérations », « Références et ROI » (le dernier regroupant références, coupes, géométrie et leurs boutons) ; `QDateEdit` (format `dd/MM/yyyy`, popup calendrier) pour la mise en service ; libellés avec unités « Bruit de référence σ (UH) : », « Fréquence moyenne SPB de référence (mm⁻¹) : » ; astérisque et infobulle « Requis dans le rapport » sur Établissement, Installation, N° de série, N° d'inventaire, Fantôme (marque/modèle/n° série) ; surligner en orange les champs requis vides à l'enregistrement sans bloquer.

### [U-24] Message de confirmation des références : la valeur remplacée n'est pas montrée
- **Sévérité** : Mineur
- **Axe** : Erreurs / Formulaires
- **Localisation** : `src/cq_tdm/gui/main_window.py:2671-2680` (`_confirm_and_set_references`), `:652-662` (`_set_references_from_analysis`), `history_panel.py:145-147`
- **Constat** : « Définir σ = 3,30 HU et f SPB = 0,310 c/mm (analyse en cours) comme valeurs de référence de cette installation ? » — pas de rappel des références actuelles (8,18 / 0,262) ni des conséquences (géométrie des ROI figée à nouveau, verdicts futurs).
- **Impact** : écrasement par erreur d'une référence établie lors d'un contrôle validé.
- **Proposition** : « Remplacer les valeurs de référence de « CHU Exemple – Discovery RT » ? σ : 8,18 → 3,30 UH · f SPB : 0,262 → 0,310 mm⁻¹ (contrôle du 15/09/2026). La géométrie des ROI de ce contrôle sera figée et les prochains contrôles seront jugés par rapport à ces valeurs. » Boutons « Remplacer » / « Annuler ». Quand aucune référence n'existe : « Définir … comme références ».

### [U-25] Bouton « Présence d'artéfacts » désactivé indiscernable ; boutons d'inspection codés uniquement par couleur
- **Sévérité** : Mineur
- **Axe** : Accessibilité
- **Localisation** : `src/cq_tdm/gui/image_viewer.py:1758-1780` (feuille de style sans `:disabled`), `:1876-1885` (`setEnabled(False)`), `:1735-1757`
- **Constat** : capture `loaded_09` : après le clic, le bouton reste orange plein. Vert/orange seuls distinguent « Absence »/« Présence » (texte présent, donc acceptable), mais l'ordre « Absence » à gauche en vert invite au clic réflexe.
- **Impact** : état incompréhensible (« pourquoi le bouton ne répond plus ? »).
- **Proposition** : ajouter `QPushButton:disabled { background-color: #555; color: #999; }` ; après « Présence », masquer ce bouton et afficher le champ de description avec « Confirmer » ; préfixer les libellés d'icônes/symboles (« ✓ Absence d'artéfacts », « ⚠ Présence d'artéfacts ») et rendre « Absence » non-défaut (`setAutoDefault(False)`) pour éviter la validation par Entrée.

### [U-26] ROI UH invisibles hors de la coupe UH, sans explication
- **Sévérité** : Mineur
- **Axe** : Visionneuse
- **Localisation** : `src/cq_tdm/gui/image_viewer.py:641-656` (`_update_roi_visibility`), capture `loaded_10_slices_modified.png`
- **Constat** : en feuilletant les coupes, les cercles jaune/cyan disparaissent dès que la coupe affichée n'est pas la coupe UH ; l'interrupteur « UH » reste sur « on ».
- **Impact** : l'utilisateur croit avoir perdu les ROI ou cherche la panne.
- **Proposition** : dessiner les ROI UH en pointillé atténué (alpha 40 %) sur les autres coupes avec le libellé « ROI UH (coupe 12) », ou afficher dans le coin de l'image « ROI UH mesurées sur la coupe 12 — touche H » ; même traitement pour la plage SPB (« coupes 8–17 »).

### [U-27] Molette = zoom ; pas de défilement des coupes à la molette
- **Sévérité** : Mineur
- **Axe** : Visionneuse
- **Localisation** : `src/cq_tdm/gui/image_viewer.py:860-880` (`wheelEvent`), dialogue des raccourcis `main_window.py:1567-1570`
- **Constat** : dans toutes les consoles et visionneuses DICOM, la molette fait défiler les coupes ; ici elle zoome (facteur 1,15 par cran) et le zoom peut atteindre 5000 %.
- **Impact** : réflexe contrarié, zooms accidentels ; feuilletage uniquement au clavier ou au curseur.
- **Proposition** : molette = coupe précédente/suivante ; Ctrl+molette = zoom ; conserver F/R. Mettre à jour le dialogue des raccourcis et l'infobulle de la visionneuse.

### [U-28] Raccourcis mono-lettre non découvrables, aucun mnémonique, pas de raccourci dans les menus d'action
- **Sévérité** : Mineur
- **Axe** : Accessibilité / Conventions
- **Localisation** : `src/cq_tdm/gui/main_window.py:1439-1463` (`QShortcut` H, N, F, R, U, S, I, A), `:1249-1282` (menus avec `&` mais aucune action ne porte de `&`), boutons sans `&` (`:1412-1425`, `history_panel.py:145-163`), `image_viewer.py:33-96` (`ToggleSwitch` sans `focusPolicy` ni gestion clavier)
- **Constat** : les raccourcis n'apparaissent que dans « Aide › Raccourcis clavier » ; les actions de menu n'ont ni mnémonique ni raccourci affiché pour « Gestion des installations… » ; les interrupteurs UH/SPB/Infos ne sont pas atteignables au clavier ; aucun `setTabOrder` explicite.
- **Impact** : utilisateurs clavier et lecteurs d'écran pénalisés ; raccourcis mono-lettre risqués si un champ texte est un jour ajouté à la fenêtre.
- **Proposition** : ajouter les actions mono-lettre au menu « Affichage » avec leur raccourci visible (`QAction("Aller à la coupe UH", shortcut="H")`) ; mnémoniques « &Ouvrir un dossier DICOM… », « &Gestion des installations… », « &Enregistrer le contrôle… » ; `ToggleSwitch.setFocusPolicy(Qt.FocusPolicy.StrongFocus)` + `keyPressEvent` (Espace) + dessin du focus ; `setTabOrder` sélecteur → Modifier → onglets → boutons.

### [U-29] Aide, raccourcis et « À propos » dans des `QMessageBox` non redimensionnables
- **Sévérité** : Mineur
- **Axe** : Disposition / Erreurs
- **Localisation** : `src/cq_tdm/gui/main_window.py:1529-1580` (`_show_shortcuts`), `:1582-1618` (`_show_help`), `:2412-2439` (`_show_about`)
- **Constat** : captures `empty_04/05/06` : colonnes de 400 px, 550 px de haut, sans défilement ni recherche ; l'aide décrit 5 étapes (pas 7), parle de « Renseigner l'installation : nom, localisation, valeurs de référence » sans citer le registre ni le bouton « Nouvelle installation », et « R » y est mal décrit (U-07). « À propos » n'offre ni lien vers le dépôt/les issues ni versions Python/Qt utiles au support.
- **Impact** : aide peu consultable ; incohérente avec l'interface.
- **Proposition** : `QDialog` avec `QTextBrowser` (liens cliquables, 640×520, redimensionnable, mémorisée) ; aligner le texte sur les 7 étapes du README et sur U-20 ; « À propos » : version, Python, PySide6, lien « Signaler un problème (GitHub) », « Ouvrir le dossier des journaux ».

### [U-30] Barre de statut : messages permanents, sans icône, sans distinction erreur/succès
- **Sévérité** : Mineur
- **Axe** : Erreurs
- **Localisation** : `src/cq_tdm/gui/main_window.py:1437` (« Prêt »), `:1800, 1851, 2077, 2110, 2191, 2312, 2356, 2465-2467, 3528-3530` (sans délai), `:2322` (« Modification de la plage SPB... » reste affiché si le debounce est annulé), `:2260-2262` (ROI SPB ignorées, 8 s seulement)
- **Constat** : « Rapport exporté: /chemin/très/long.pdf · contrôle ajouté à l'historique » reste affiché pendant toute la session ; les erreurs ont la même apparence que les succès ; « Artéfacts: Présence détectée (NC) » suggère une détection automatique.
- **Impact** : information périmée prise pour l'état courant ; alertes manquées.
- **Proposition** : délais systématiques (succès 6 s, erreur 12 s), préfixes « ✓ » / « ⚠ », `QLabel` permanent à droite (`addPermanentWidget`) pour l'état durable (« CHU Exemple · Discovery RT · 24 coupes · coupes UH 12 / SPB 8–17 »), et reformuler : « Artéfacts : présence déclarée (non conforme) » / « absence déclarée (conforme) ».

### [U-31] Vocabulaire « appareil » / « équipement » / « installation » / « scanner »
- **Sévérité** : Mineur
- **Axe** : Libellés
- **Localisation** : `README.md` (« Base de données des appareils »), `main_window.py:2493` (titre « Base de données des appareils »), `device_database.py:147` (« Équipement inconnu »), `pdf_report.py:391-396` (« [Nom de l'équipement] », « [Localisation de l'équipement] »), `main_window.py:398-401` (« scanner »), dialogue des installations (« Installation : » pour le nom)
- **Constat** : quatre termes pour la même entité ; dans le formulaire, « Installation : » est à la fois le titre de l'entité et le libellé du champ « nom ».
- **Impact** : hésitation sur ce qu'il faut saisir ; incohérence avec la décision (« installation de tomodensitométrie », « tomodensitomètre »).
- **Proposition** : « installation » pour l'entité enregistrée, « tomodensitomètre » pour l'appareil physique ; champ « Nom de l'installation : » ; « Base de données des installations » ; « Installation sans nom » au lieu de « Équipement inconnu ».

### [U-32] Historique : abréviations d'en-tête sans infobulle ni unité, détail non découvrable
- **Sévérité** : Mineur
- **Axe** : Historique
- **Localisation** : `src/cq_tdm/gui/history_panel.py:35` (`_COLUMNS`), `:105-117` (table), `:116` (double-clic → `_show_details`), `:332-369`
- **Constat** : « CT eau », « Unif. », « Bruit σ », « f SPB » sans unités ; aucune infobulle d'en-tête ; le détail d'un contrôle (notes, action corrective, chemins) n'est accessible que par double-clic ; les notes n'apparaissent qu'en infobulle de cellule.
- **Impact** : lecture incertaine pour un manipulateur ; informations du registre cachées.
- **Proposition** : `setHorizontalHeaderItem(i, QTableWidgetItem(label))` + `setToolTip("Nombre CT moyen de la ROI centrale (UH)")` etc. ; en-têtes « CT eau (UH) », « Unif. (UH) », « σ (UH) », « f SPB (mm⁻¹) » ; bouton « Détails… » (ou panneau latéral) ; colonne « Action corrective » (icône ✓ avec date) et « Obs. » (icône note).

### [U-33] Graphique de tendance : axe des dates ambigu, couleur seule pour le statut
- **Sévérité** : Mineur
- **Axe** : Historique / Accessibilité
- **Localisation** : `src/cq_tdm/core/trend_chart.py:109` (`DateFormatter("%m/%y")`), `:80-81` (marqueurs « o » colorés), `:121-123` (légende seulement s'il y a une référence), `history_panel.py:138` (hauteur min 170 px)
- **Constat** : « 11/24 » se lit « 11 novembre » en français ; conforme/NC/NCG ne diffèrent que par la couleur (vert/orange/rouge) ; sans référence saisie, aucune légende ni bande.
- **Impact** : lecture des dates erronée ; daltonisme (deutéranopie : vert/orange proches).
- **Proposition** : `DateFormatter("%b %Y")` avec locale française (« nov. 2024 ») ou « %m/%Y » ; marqueurs par statut : rond (conforme), triangle (NC), carré (NCG) ; légende toujours affichée (« Conforme · Non conforme · Référence · Tolérance ») ; texte « Aucune valeur de référence : bande de tolérance non tracée » à la place de la bande.

### [U-34] Résultats : encadré SPB ne dit pas combien de ROI ont été ignorées ; avertissements tronqués
- **Sévérité** : Mineur
- **Axe** : Visionneuse / Erreurs
- **Localisation** : `src/cq_tdm/gui/main_window.py:3041, 3077-3079` (« Nombre de ROIs : 8 » = `len(roi_config.rois)`), `:2231-2262` (ROI ignorées en rouge, message 8 s), `:3062-3075` (5 premiers avertissements)
- **Constat** : une ROI SPB hors image est marquée « (ignorée) » en rouge sur l'image et dans la barre de statut pendant 8 s ; le tableau des résultats continue d'afficher 8 ROI.
- **Impact** : le rapport et l'écran ne disent pas que la mesure SPB a été faite sur 7 ROI.
- **Proposition** : ligne « ROI mesurées : 7 / 8 (SPB4 hors image — recentrer le fantôme) » en orange dans « Résultats » et dans le PDF ; conserver le rouge sur l'image.

### [U-35] Rapport PDF : pagination sans total, version et date absentes des pieds de page
- **Sévérité** : Majeur
- **Axe** : PDF
- **Localisation** : `src/cq_tdm/reports/pdf_report.py:349-360` (`_draw_footer` « Page N »), `:643-647` (« Rapport généré par CQ TDM v0.7.0 » uniquement en fin de corps), `:363-387` (en-tête absent de la page 1)
- **Constat** : « Page 3 » sans « / 7 » ; la version du logiciel figure en dernière ligne du corps (en page 7, après l'historique) ; la date d'édition n'est qu'en page 1.
- **Impact** : un auditeur ne peut vérifier qu'un rapport imprimé est complet ni identifier la version sur une page isolée.
- **Proposition** : pied de page « CQ TDM 0.7.0 · contrôle du 15/09/2026 · édité le 01/10/2026 16:02 · page 3 / 7 » sur toutes les pages (deux passes ReportLab ou `canvasmaker` NumberedCanvas) ; conserver l'en-tête établissement / installation / date sur les pages 2+.

### [U-36] Rapport PDF : 7 pages dont 4 à moitié vides
- **Sévérité** : Majeur
- **Axe** : PDF
- **Localisation** : `src/cq_tdm/reports/pdf_report.py:603-640` (chaque section dans `KeepTogether`), `:856-903` (images ROI 7,5 cm), `:1105` (graphique SPB 14 × 9,3 cm), `:1012-1015` (image artéfacts 12 × 12 cm)
- **Constat** : captures `pdf_ok-2`, `-4`, `-5`, `-6` : pages à 40-60 % vides car la section suivante, insécable, ne tient pas dans le reste.
- **Impact** : impression et archivage alourdis, lecture hachée ; 7 pages pour 1 contrôle × 4 contrôles/an × N scanners.
- **Proposition** : `KeepTogether` seulement sur titre + premier tableau de chaque section ; images ROI 6,5 cm ; graphique SPB 11 × 7 cm à côté des valeurs ; image artéfacts 9 cm ; viser 4 pages (1 : identité + résumé + paramètres ; 2 : ROI + nombre CT + uniformité ; 3 : bruit + SPB ; 4 : artéfacts + historique + notes + signatures).

### [U-37] Rapport PDF : résumé sans valeurs de référence ni critères, statuts hétérogènes
- **Sévérité** : Majeur
- **Axe** : PDF
- **Localisation** : `src/cq_tdm/reports/pdf_report.py:1422-1498` (`_build_summary_section` : lignes « Test : valeur → STATUT », « — » si pas de référence), `:1198-1230` (« Valeur attendue : 0 HU », « Critère NC : ±7 HU », « Critère NCG : ±25 HU »)
- **Constat** : « Bruit (stabilité) : 3,30 HU → NON CONFORME » sans la référence 8,18 ni le critère [−0,82 ; +0,82] ; « Bruit : 3,30 HU » sans statut quand la référence manque ; le vocabulaire « Critère NC / Critère NCG » ne reprend pas celui de la décision (« critère d'acceptabilité », « non-conformité grave »).
- **Impact** : l'auditeur doit feuilleter pour reconstituer chaque verdict ; ambiguïté « test non fait » vs « test conforme ».
- **Proposition** : tableau « Résumé des tests » à 5 colonnes : Test · Valeur mesurée · Référence / valeur attendue · Critère d'acceptabilité · Conformité (« Conforme », « Non conforme », « Non-conformité grave », « Non évalué — référence absente », « Non réalisé ») ; formulations ANSM : « −7 UH ≤ nombre CT ≤ +7 UH ; non-conformité grave au-delà de ±25 UH ».

### [U-38] Rapport PDF : coupes utilisées absentes, ROI non numérotées sur les images, couleurs différentes de l'écran
- **Sévérité** : Majeur
- **Axe** : PDF
- **Localisation** : `src/cq_tdm/reports/pdf_report.py:905-957` (tableau des ROI sans numéro de coupe), `:1341-1344` (« Nombre de coupes analysées : 10 » sans plage), `:176-230` (ROI UH toutes jaunes, sans libellé), `:233-283` (ROI SPB vertes sans numéro)
- **Constat** : le rapport ne dit pas sur quelle coupe (n°, position z) ont été mesurés nombre CT/uniformité/bruit ni quelles coupes composent le SPB, alors que l'historique les stocke (`QCRun.hu_slice_index`, `nps_start_slice`) ; le tableau nomme « SPB 1 (haut-gauche) » mais l'image n'est pas annotée ; l'écran distingue centrale (jaune) et périphériques (cyan), le PDF non.
- **Impact** : reproductibilité d'un contrôle à l'autre (exigence « positions et tailles identiques ») et vérification par l'auditeur affaiblies.
- **Proposition** : ligne « Coupe UH : 12 / 24 (z = +5,0 mm) · Coupes SPB : 8–17 (10 coupes) » dans « Paramètres d'acquisition » et sous les images ; annoter les images (`ax.text`) « C », « 12h », « 3h »…, « 1 »…« 8 » ; reprendre les couleurs de l'écran ; ajouter un rappel « Géométrie figée depuis le contrôle du … » déjà présent dans le texte (ok).

### [U-39] Rapport PDF : bloc de validation absent, type de contrôle figé « trimestriel »
- **Sévérité** : Majeur
- **Axe** : PDF
- **Localisation** : `src/cq_tdm/reports/pdf_report.py:572-575` (« Contrôle de qualité interne trimestriel du … »), `:1501-1544` (`generate_report_filename` « CQI-trimestriel_… »), aucune zone opérateur/validation dans `generate_report`
- **Constat** : la décision prévoit aussi un contrôle semestriel (per-opératoire), un contrôle avant mise en service et des contrôles après intervention (2.1, 3.2.1) ; le rapport ne porte ni « contrôle réalisé par », ni « validé par (physicien médical) », ni date/signature, attendus pour tout rapport de CQ interne versé au registre.
- **Impact** : document non attribuable ; libellé faux pour une partie des installations.
- **Proposition** : champ « Type de contrôle » dans le dialogue d'export (« Trimestriel » par défaut, « Semestriel (per-opératoire) », « Avant mise en service », « Après intervention / évolution logicielle », « Autre : … ») reporté en titre et dans le nom de fichier ; champs « Réalisé par » et « Validé par » mémorisés par installation (`AppConfig.last_operator`) ; bloc final « Réalisé par : ____ Date : ____ Signature : ____ / Validé par (physicien médical) : ____ ».

### [U-40] Rapport PDF : notes avec balises brutes, graphiques de tendance illisibles, couleurs orange trop claires
- **Sévérité** : Majeur
- **Axe** : PDF
- **Localisation** : `src/cq_tdm/reports/pdf_report.py:1127-1138` (`flush_list` : `Table([[f"• {item}"]])` avec chaînes brutes contenant `<b>`), `:1096-1110` (graphiques 640×400 px rendus à 8,3 × 5,2 cm, étiquettes de dates qui se chevauchent), `:123-131, 495-498` (`colors.orange` = #FFA500, 1,97:1 sur blanc), `:320-346` (badge blanc sur orange 1,97:1)
- **Constat** : capture `pdf_ok-7` : « • <b>À surveiller</b> : bruit proche de la limite » imprimé tel quel ; axes des graphiques « 11/2401/2503/25… » collés ; bandeau et verdicts NC en orange clair peu lisibles, invisibles en impression noir et blanc.
- **Impact** : rapport perçu comme bogué ; graphiques inutilisables ; non-conformités peu visibles.
- **Proposition** : `Paragraph(f"• {item}", style)` dans `flush_list` ; graphiques à 15 cm de large empilés (ou `fig.autofmt_xdate(rotation=30)` et `width_px=900`) ; orange foncé `Color(0.80, 0.33, 0)` pour NC, badge à texte blanc sur fond `#b45309` ; ajouter un symbole textuel (« ✗ NON CONFORME », « ✓ CONFORME ») pour l'impression monochrome.

### [U-41] Journal `cq_tdm.log` et dossier de configuration inaccessibles depuis l'interface
- **Sévérité** : Mineur
- **Axe** : Erreurs
- **Localisation** : `src/cq_tdm/main.py:126-191` (journal écrit, chemin cité dans la boîte d'erreur), `main_window.py:1273-1282` (menu Aide sans entrée), README « Signaler un problème »
- **Constat** : le README demande de joindre `cq_tdm.log` ; aucune commande n'ouvre le fichier ni le dossier (`%LOCALAPPDATA%\cq_tdm\`, chemin que l'utilisateur doit taper).
- **Impact** : signalements sans journal ; support ralenti.
- **Proposition** : « Aide › Ouvrir le dossier des journaux et réglages » (`QDesktopServices.openUrl(QUrl.fromLocalFile(AppConfig.config_dir()))`), « Aide › Signaler un problème… » (URL issues), et dans la boîte « Erreur inattendue » un bouton « Ouvrir le dossier du journal ».

### [U-42] Changement de thème différé au prochain lancement
- **Sévérité** : Mineur
- **Axe** : Parcours
- **Localisation** : `src/cq_tdm/gui/main_window.py:2376-2386` (`_toggle_theme`), couleurs figées à la construction (`image_viewer.py:518-521, 968-970, 1100-1102`, `main_window.py:1387-1396`)
- **Constat** : « Le changement de thème sera appliqué au prochain lancement de l'application. »
- **Impact** : friction mineure ; l'utilisateur ne voit pas le résultat.
- **Proposition** : proposer « Redémarrer maintenant » dans la boîte (`QProcess.startDetached(sys.executable, sys.argv)` puis `close()`), ou à terme recharger les feuilles de style via une méthode `apply_theme()` sur chaque widget.

### [U-43] Fenêtre « Configuration des rapports » : échelle du logo inexpliquée, options d'export ailleurs
- **Sévérité** : Mineur
- **Axe** : Formulaires
- **Localisation** : `src/cq_tdm/gui/main_window.py:888-901` (« Échelle : 40 % »), `:874` (« Supprimer »), `:904-913` (« Contenu : » avec une seule case), `:1270` (menu « Rapports... »)
- **Constat** : « Échelle : 40 % » (de quoi ?) ; « Supprimer » retire la sélection du logo (pas le fichier) ; l'option « Inclure l'historique des contrôles » est globale alors qu'elle se décide souvent à l'export.
- **Impact** : réglage à tâtons ; option oubliée.
- **Proposition** : « Largeur du logo : 40 % de la largeur de page » avec aperçu à l'échelle ; bouton « Retirer » ; déplacer « Inclure l'historique » et le futur « Type de contrôle » dans un dialogue d'options d'export affiché avant le choix du fichier (case mémorisée) ; titre de menu « Configuration › Rapports PDF… ».

### [U-44] Dialogue d'observation : titre « Notes », markdown sans aperçu, bouton « ✓ » ambigu
- **Sévérité** : Mineur
- **Axe** : Formulaires / Libellés
- **Localisation** : `src/cq_tdm/gui/main_window.py:992` (titre « Notes »), `:1004-1016` (consignes markdown), `:1025-1049` (boutons « G », « I », « H », « • » 30 px), `:2469-2478` (« Ajouter une observation ✓ »), `pdf_report.py:1120` (section « Notes »), `history_panel.py:244-245` (notes en infobulle)
- **Constat** : « observation » (bouton, README, décision 3.2.2 n'emploie ni l'un ni l'autre) devient « Notes » dans le dialogue et le PDF ; la syntaxe markdown doit être tapée ; le « ✓ » signale la présence d'un texte mais le libellé reste « Ajouter ».
- **Impact** : incohérence et apprentissage inutile pour une zone de commentaires.
- **Proposition** : titre « Observations du contrôle » partout ; `QTextEdit` riche minimal (gras/italique/liste via `QTextEdit` natif, export HTML → ReportLab) ou conserver le markdown avec un aperçu ; libellé dynamique « Ajouter une observation… » / « Modifier l'observation (3 lignes)… ».

### [U-45] Boutons « ↓ » sans texte et « ⟲ Réinit. Zoom » dont l'action est « ajuster »
- **Sévérité** : Mineur
- **Axe** : Libellés / Visionneuse
- **Localisation** : `src/cq_tdm/gui/image_viewer.py:1329-1332, 1355-1358` (« ↓ » 24 px, infobulles « Définir à la position courante », « Centrer sur la position courante (10 coupes) »), `:1063-1066` (« ⟲ Réinit. Zoom », infobulle « Ajuster l'image à la vue »), `:1018-1020` (« ⟲ Réinit. W/L », « (Tissus mous) »)
- **Constat** : icônes typographiques sans libellé ; « Réinit. Zoom » exécute un ajustement, pas un retour à 100 % ; « W/L » et « Tissus mous » sans valeurs.
- **Impact** : découvrabilité faible ; incohérence avec « R » et « F ».
- **Proposition** : « ↓ Coupe affichée » / « ↓ Centrer ici (10 coupes) » (ou `QToolButton` avec `setToolButtonStyle(TextBesideIcon)`) ; « Ajuster » (F) et « 100 % » ; « Fenêtre 400 / 40 » avec un menu de préréglages « Eau (200/0) », « Artéfacts ANSM (80/0) », « Tissus mous (400/40) ».

### [U-46] Sélecteur d'installation : libellé « N°inv. » collé, liste tronquée dans la fenêtre des installations
- **Sévérité** : Mineur
- **Axe** : Libellés / Disposition
- **Localisation** : `src/cq_tdm/core/device_database.py:139-140` (« N°inv. {n} »), `src/cq_tdm/gui/main_window.py:181-183` (`QListWidget` sans élision), `:366-392` (« (image chargée) » ajouté au libellé)
- **Constat** : « CHU Exemple - GE MEDICAL SYSTEMS Discovery RT - N°inv. INV-042 » ; dans la liste (capture `home_09`) l'entrée dépasse et une barre horizontale apparaît ; « - » (trait d'union) en guise de séparateur.
- **Impact** : lecture pénible dès que l'établissement a un nom long.
- **Proposition** : « CHU Exemple · Discovery RT · n° inv. INV-042 » ; `QListWidget.setTextElideMode(Qt.TextElideMode.ElideMiddle)` + infobulle complète ; afficher « (image chargée) » comme icône ou texte secondaire.

### [U-47] Boîte « Informations image » : « N/A » anglais, monospace, pas de copie
- **Sévérité** : Mineur
- **Axe** : Libellés
- **Localisation** : `src/cq_tdm/gui/main_window.py:814-841` (`ImageInfoDialog`), `:1636-1662` (« N/A » ×8), `pdf_report.py:752-756, 808-822` (« N/A » dans le PDF)
- **Constat** : « Pitch : N/A », « FOV : N/A » ; la boîte est une étiquette monospace sélectionnable sans bouton « Copier ».
- **Impact** : anglicisme dans un rapport réglementaire ; copie pénible vers un ticket.
- **Proposition** : « — » ou « non renseigné dans les DICOM » ; `QTextBrowser` avec tableau à deux colonnes et bouton « Copier » ; dans le PDF « — ».

### [U-48] Titre de fenêtre statique, pas de récents, pas de « Fermer la série »
- **Sévérité** : Mineur
- **Axe** : Conventions
- **Localisation** : `src/cq_tdm/gui/main_window.py:1170` (`setWindowTitle("CQ TDM")`), `:1249-1256` (menu Fichier)
- **Constat** : le titre ne reflète ni l'installation ni la série chargée ; aucune liste de dossiers récents ; impossible de revenir à l'écran d'accueil sans relancer.
- **Impact** : confusion entre deux fenêtres/deux séries ; navigation plus longue.
- **Proposition** : `setWindowTitle(f"{nom_installation} — {dossier} — CQ TDM")` ; sous-menu « Dossiers récents » (5 entrées, `AppConfig.recent_folders`) ; « Fichier › Fermer la série » qui remet l'état initial.

### [U-49] Scripts d'installation en anglais et publication « Luis »
- **Sévérité** : Mineur
- **Axe** : Conventions
- **Localisation** : `installers/install-windows.bat:11-13, 150-166` (« CQ TDM Installer for Windows », « Create Desktop shortcut? (Y/N) »), `installers/install-linux.sh:4-6, 143-176`, `installers/cq-tdm.iss:21` (`MyAppPublisher "Luis"`), `install-linux.sh:88-98` (icône téléchargée depuis GitHub alors que `assets/icon.png` est installé avec le paquet)
- **Constat** : les deux scripts « sans droits administrateur » s'adressent en anglais à un public francophone ; « Luis » apparaît comme éditeur dans « Ajout/Suppression de programmes » ; l'installation Linux échoue à poser l'icône hors réseau.
- **Impact** : expérience d'installation en décalage avec le reste du produit.
- **Proposition** : messages en français (« Installation de CQ TDM », « Créer un raccourci sur le Bureau ? (o/N) ») ; `MyAppPublisher "Luis Ammour"` ; icône : `python3 -c "import cq_tdm, pathlib; print(pathlib.Path(cq_tdm.__file__).parent/'assets'/'icon.png')"`.

### [U-50] Dialogue « Action corrective » : date par défaut = aujourd'hui, pas de lien avec la non-conformité
- **Sévérité** : Mineur
- **Axe** : Formulaires
- **Localisation** : `src/cq_tdm/gui/main_window.py:1122-1159` (`CorrectiveActionDialog`), `:1137-1140` (`QDate.currentDate()`), `history_panel.py:155-159` (bouton actif pour tout contrôle enregistré)
- **Constat** : la case « Une action corrective a été réalisée » est décochée par défaut même pour un contrôle NC ; la date par défaut est celle du jour ; aucun rappel des tests non conformes du contrôle ; le champ « Nature » est une ligne unique.
- **Impact** : saisie d'une date erronée ; action corrective sans mention de ce qu'elle corrige.
- **Proposition** : rappel en tête « Contrôle du 08/04/2025 — non conforme : bruit 9,27 UH (réf. 8,18) » ; case cochée par défaut si le contrôle est NC ; date vide tant que la case n'est pas cochée (`setSpecialValueText`) ; `QPlainTextEdit` 3 lignes ; libellé « Nature de l'action : » ; proposer « Nouveau contrôle conforme le … » comme suggestion.

### [U-51] Bouton « Ajouter une observation » actif sans série, texte perdu au chargement
- **Sévérité** : Mineur
- **Axe** : Parcours / Erreurs
- **Localisation** : `src/cq_tdm/gui/main_window.py:1418-1420` (toujours activé), `:1824-1826` (`_user_notes = ""` à chaque chargement)
- **Constat** : une observation saisie avant de charger (ou avant de recharger) la série est effacée sans avertissement.
- **Impact** : perte de saisie.
- **Proposition** : désactiver le bouton tant que `_current_series is None` ; au chargement d'une nouvelle série, si `_user_notes` n'est pas vide, demander « Conserver l'observation en cours pour la nouvelle série ? ».

### [U-52] Deux séparateurs de base de données : fenêtre « Déplacer... », « Nouvelle... » dans l'écran d'édition d'installation
- **Sévérité** : Amélioration
- **Axe** : Disposition
- **Localisation** : `src/cq_tdm/gui/main_window.py:146-173` (section « Base de données » en tête de la liste), `:702-811`
- **Constat** : les opérations d'administration (ouvrir/créer/déplacer le fichier `devices.json`) occupent le haut de la colonne de gauche à chaque ouverture de « Modifier… », juste au-dessus de la liste des installations.
- **Impact** : risque de changer de base en voulant modifier une installation ; bruit visuel.
- **Proposition** : déplacer ces trois actions dans « Configuration › Base de données… » (dialogue dédié avec chemin complet copiable, « Ouvrir le dossier », « Créer », « Déplacer », dernière modification) et n'afficher ici qu'une ligne « Base : devices.json (réseau) » avec infobulle.

### [U-53] Marqueurs du curseur de coupe : 130 px de hauteur réservée
- **Sévérité** : Amélioration
- **Axe** : Disposition
- **Localisation** : `src/cq_tdm/gui/image_viewer.py:125` (`setMinimumHeight(90)`), `:947` (`setContentsMargins(5, 38, 5, 5)`), `main_window.py:1306-1320` (ligne de spinboxes sous le curseur)
- **Constat** : le bloc curseur + libellés UH/SPB + spinboxes occupe ~170 px avant l'image sur un écran de 728 px utiles.
- **Impact** : image plus petite.
- **Proposition** : dessiner UH (triangle) et SPB (bande) **sous** la rainure sur deux rangées de 10 px, ramener la hauteur à 56 px ; passer les spinboxes dans la même ligne que « Coupe : » à droite du compteur « 12 / 24 ».

### [U-54] Statuts courts « NC » / « NCG » dans le tableau et les résultats
- **Sévérité** : Amélioration
- **Axe** : Libellés
- **Localisation** : `src/cq_tdm/core/qc_history.py:23-24` (`STATUS_LABEL`, `STATUS_SHORT`), `main_window.py:2979-2983` (« ✗ NCG »), `history_panel.py:230`
- **Constat** : « NC » / « NCG » sans légende ; la décision écrit « non-conforme grave ».
- **Impact** : abréviations opaques pour un nouvel utilisateur ; infobulle absente.
- **Proposition** : garder « NC » / « NCG » dans les cellules étroites avec infobulle « Non conforme — remise en conformité dès que possible » / « Non-conformité grave — arrêt de l'exploitation et signalement ANSM/ARS sous 2 jours ouvrés » ; en toutes lettres dans « Résultats » et le PDF.

### [U-55] Icônes et HiDPI : tailles de police en pixels dans les feuilles de style
- **Sévérité** : Amélioration
- **Axe** : Disposition / Accessibilité
- **Localisation** : `src/cq_tdm/gui/main_window.py:153, 361, 1329, 1352, 1382, 1394, 1009-1011`, `image_viewer.py:981, 994, 1743, 1766`… (`font-size: 11px/12px/14px/18px`), `main.py:206-210` (pas de `setAttribute(HighDpiScaleFactorRoundingPolicy)`)
- **Constat** : Qt 6 gère l'échelle d'écran, mais les tailles en px ignorent la taille de police système choisie par l'utilisateur (accessibilité Windows) ; aucune politique d'arrondi HiDPI explicite (125 % → flou possible).
- **Impact** : textes trop petits pour les utilisateurs ayant agrandi la police système.
- **Proposition** : `QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)` avant la création de l'application ; tailles relatives (`font.setPointSizeF(base * 0.9)`) à partir de `QApplication.font()`.

## 5. Tableau des libellés à corriger

| Fichier:ligne | Texte actuel | Texte proposé | Motif |
|---|---|---|---|
| `image_viewer.py:949`, `:1699` | `Coupe:` | `Coupe :` (espace insécable) | Typographie FR |
| `image_viewer.py:1026`, `:1041` | `W:` / `L:` | `Largeur (W) :` / `Centre (L) :` ou `W :`/`L :` + infobulle | Typographie, compréhension |
| `image_viewer.py:1071` | `Zoom:` | `Zoom :` | Typographie |
| `image_viewer.py:1321` | `Coupe UH:` | `Coupe UH :` | Typographie |
| `image_viewer.py:1338` | `Coupes SPB:` | `Coupes SPB :` | Typographie |
| `image_viewer.py:1018` | `⟲ Réinit. W/L` | `Fenêtre 400/40` (menu de préréglages) | Action réelle, valeurs visibles |
| `image_viewer.py:1019` | `Réinitialiser fenêtrage (Tissus mous)` | `Revenir au fenêtrage tissus mous (W 400 / L 40)` | Précision |
| `image_viewer.py:1063` | `⟲ Réinit. Zoom` | `Ajuster à la vue (F)` | Le bouton ajuste, ne « réinitialise » pas |
| `image_viewer.py:1120` | `Afficher/Cacher :` | `Afficher :` | Concision |
| `image_viewer.py:1329-1331` | `↓` / `Définir à la position courante` | `↓ Coupe affichée` / `Prendre la coupe affichée comme coupe UH` | Bouton sans texte |
| `image_viewer.py:1355-1357` | `↓` / `Centrer sur la position courante (10 coupes)` | `↓ Centrer ici` / `Centrer les 10 coupes SPB sur la coupe affichée` | Idem |
| `image_viewer.py:984` | `Ouvrir un dossier DICOM...` | `Ouvrir un dossier DICOM…` | Points de suspension |
| `image_viewer.py:1723` | `Fenêtre artéfacts ANSM : Centre (L) = 0 UH, Largeur (W) = 80 UH` | `Fenêtre imposée par l'ANSM : centre 0 UH, largeur 80 UH (non modifiable)` | Dire pourquoi la molette/W-L ne répondent pas |
| `image_viewer.py:1735` | `Absence d'artéfacts` | `✓ Aucun artéfact cliniquement gênant` | Reprend le critère ANSM |
| `image_viewer.py:1758` | `Présence d'artéfacts` | `⚠ Artéfact(s) observé(s)…` | Annonce l'étape de description |
| `image_viewer.py:1795-1797` | `Décrivez les artéfacts observés (type, localisation, sévérité...)` | `Type (anneau, bande, strie…), localisation, coupes concernées, gêne clinique` | Guide la saisie du registre |
| `main_window.py:64` | `Aucune installation sélectionnée` | `Aucune installation` (base vide : `Aucune installation enregistrée`) | État vide |
| `main_window.py:65` | `Installation inconnue` | `Installation non enregistrée` | Cohérent avec le résumé (`:3384`) |
| `main_window.py:160-168` | `Ouvrir...` / `Nouvelle...` / `Déplacer...` | `Ouvrir…` / `Créer…` / `Déplacer…` | Points de suspension ; « Nouvelle » seul est ambigu |
| `main_window.py:186` | `Nouvelle installation depuis l'image chargée` | `Créer l'installation de la série chargée` | Verbe d'action, « image » vs « série » |
| `main_window.py:191` | `Supprimer l'installation` | `Supprimer l'installation…` | Ouvre une confirmation |
| `main_window.py:203` | `Détails de l'installation :` | `Installation sélectionnée` | Titre, pas un libellé de champ |
| `main_window.py:222` | `Installation :` | `Nom de l'installation :` | Confusion titre/champ |
| `main_window.py:226` | `Mise en service :` (texte libre) | `Date de mise en service (jj/mm/aaaa) :` + `QDateEdit` | Format |
| `main_window.py:230` | `N° série :` | `N° de série :` | Français |
| `main_window.py:234` | `N° inventaire :` | `N° d'inventaire :` | Français |
| `main_window.py:248` | `Bruit réf. (σ) :` | `Bruit de référence σ (UH) :` | Unité dans le libellé |
| `main_window.py:253` | `Fréq. SPB réf. :` | `Fréquence moyenne SPB de référence (mm⁻¹) :` | Unité, lisibilité |
| `main_window.py:246, 251` | `(HU)`, `(cycles/mm)` | `(UH)`, `(mm⁻¹)` ou `(cycles/mm)` partout | Cohérence U-10 |
| `main_window.py:256` | `Définir les valeurs actuelles comme références` | `Prendre l'analyse en cours comme référence…` | Verbe + ellipse (confirmation) |
| `main_window.py:277` | `Identification DICOM :` | `Identité DICOM (lecture seule)` | Dire que c'est non modifiable |
| `main_window.py:316` | `Mémoriser les coupes actuelles` | `Enregistrer les coupes choisies dans la visionneuse` | Clarté |
| `main_window.py:337` | `Réinitialiser la géométrie des ROI` | `Oublier la géométrie figée des ROI…` | Reprend le texte de confirmation |
| `main_window.py:356` | `Enregistrer les modifications` | `Enregistrer` + bouton `Fermer` | Convention Qt |
| `main_window.py:699` | `Aucune base de données` | `Base non encore créée (sera créée à la première installation enregistrée)` | Explication |
| `main_window.py:705, 717, 743` | `JSON Files (*.json)` | `Base d'installations CQ TDM (*.json)` | Anglais, sens |
| `main_window.py:819` | `Informations image` | `Informations de la série DICOM` | Portée réelle |
| `main_window.py:849`, `:1270` | `Configuration des rapports` / `Rapports...` | `Options des rapports PDF` / `Rapports PDF…` | Cohérence |
| `main_window.py:874` | `Supprimer` (logo) | `Retirer` | Ne supprime pas le fichier |
| `main_window.py:890` | `Échelle :` | `Largeur du logo (% de la page) :` | Sens de la valeur |
| `main_window.py:904` | `Contenu :` | `Sections facultatives :` | Précision |
| `main_window.py:992`, `pdf_report.py:1120` | `Notes` | `Observations du contrôle` | Cohérence avec le bouton |
| `main_window.py:1004` | `Rédigez vos notes ci-dessous. Formatage markdown simplifié supporté :` | `Observations reprises dans le rapport. Mise en forme : ` | « supporté » = anglicisme |
| `main_window.py:1134` | `Une action corrective a été réalisée` | `Une action corrective a été réalisée après ce contrôle` | Contexte |
| `main_window.py:1145` | `Nature :` | `Nature de l'action :` | Précision |
| `main_window.py:1250` | `Ouvrir un dossier DICOM...` | `&Ouvrir un dossier DICOM…` | Mnémonique, ellipse |
| `main_window.py:1252` | `Exporter rapport PDF...` | `&Enregistrer le contrôle et exporter le PDF…` | Même action que le bouton |
| `main_window.py:1253-1254` | `Exporter positions ROIs SPB (JSON)...` / `… ROIs UH (JSON)...` | `Exporter les positions des ROI SPB (JSON, IQMetrix-CT)…` / `… ROI UH …` | « ROI » invariable |
| `main_window.py:1260` | `Informations image...` | `Informations de la série…` | Portée |
| `main_window.py:1262` | `Thème clair` | `Thème clair (redémarrage requis)` | Attente |
| `main_window.py:1269` | `Gestion des installations...` | `&Installations…` | Concision, mnémonique |
| `main_window.py:1277` | `Mode debug (centre/périmètre fantôme)` | `Afficher le contour détecté du fantôme` (menu Affichage) | Vocabulaire utilisateur |
| `main_window.py:1314` | `⟲ Réinit. coupes` | `Coupes enregistrées` | Dit où l'on revient |
| `main_window.py:1328` | `Installation` | `Installation contrôlée` | Contexte |
| `main_window.py:1412` | `Inspection visuelle des artéfacts` | `Inspecter les artéfacts (A)…` | Verbe, raccourci visible |
| `main_window.py:1418` | `Ajouter une observation` | `Ajouter une observation…` / `Modifier l'observation…` | Ellipse, état |
| `main_window.py:1423` | `Enregistrer les résultats et exporter le PDF` | `Enregistrer le contrôle et exporter le PDF…` | « contrôle » = terme de l'historique |
| `main_window.py:1437` | `Prêt` | `Prêt — ouvrez une série DICOM (Ctrl+O)` | Guidage |
| `main_window.py:1543` | `Réinitialiser la vue (zoom 100%)` | `Réinitialiser l'affichage (zoom ajusté, fenêtre 400/40)` | Exactitude |
| `main_window.py:1544-1545` | `Afficher/Masquer ROIs UH (jaune)` | `Afficher/masquer les ROI UH` | Pluriel, casse |
| `main_window.py:1749` | `Ouvrir dossier DICOM` | `Ouvrir le dossier de la série DICOM` | Français |
| `main_window.py:1800` | `Chargement du dossier: {folder}` | `Chargement de {dossier}…` | Typographie |
| `main_window.py:1804, 1806` | `Aucun fichier DICOM trouvé dans ce dossier.` | `Ce dossier ne contient aucun fichier DICOM lisible. Sélectionnez le dossier de la série exportée (fichiers .dcm ou sans extension).` | Action |
| `main_window.py:1811, 1976, 1980, 2092, 2120, 2444` | `Attention` | Titre contextuel : `Export du rapport`, `Export des ROI`, `Inspection des artéfacts` | Titre informatif |
| `main_window.py:1851` | `{n} images chargées depuis {dossier}` | `{n} coupes chargées — {dossier}` | « coupes » = vocabulaire de l'app |
| `main_window.py:1863-1864` | `Erreur: {e}` / `Impossible de charger le dossier DICOM:\n{e}` | `⚠ Chargement impossible` / `Impossible de lire la série DICOM.\n\nDossier : …` + détails | Typographie, détails séparés |
| `main_window.py:1986-1991` | `L'installation n'est pas enregistrée : le rapport PDF sera exporté mais le contrôle ne sera pas ajouté à l'historique…` | garder, boutons `Enregistrer l'installation` / `Exporter sans historique` / `Annuler` | Actions explicites |
| `main_window.py:2003, 2006` | `Exporter rapport PDF` / `PDF Files (*.pdf)` | `Enregistrer le rapport PDF` / `Rapport PDF (*.pdf)` | Anglais |
| `main_window.py:2010` | `Génération du rapport...` | `Génération du rapport PDF…` | Ellipse |
| `main_window.py:2077` | `Rapport exporté: {path}{msg}` | `✓ Rapport enregistré : {nom}.pdf · contrôle ajouté à l'historique` | Typographie, concision |
| `main_window.py:2110, 2191` | `ROIs SPB exportées: …` | `✓ Positions des ROI SPB exportées : …` | Pluriel, typographie |
| `main_window.py:2262` | `SPB : {n} ROI hors image ignorée(s) — recentrer le fantôme` | `⚠ SPB : {n} ROI hors image ignorée(s) — fantôme à recentrer ou champ de vue trop petit` | Cause |
| `main_window.py:2322` | `Modification de la plage SPB...` | `Plage SPB modifiée — recalcul dans 1 s…` | Dit ce qui va se passer |
| `main_window.py:2384-2385` | `Le changement de thème sera appliqué au prochain lancement de l'application.` | `Le thème sera appliqué au prochain lancement.` + bouton `Redémarrer maintenant` | Action |
| `main_window.py:2465-2467` | `Artéfacts: Présence détectée (NC)` / `Artéfacts: Absence confirmée (Conforme)` | `Artéfacts : présence déclarée — non conforme` / `Artéfacts : absence déclarée — conforme` | « détectée » suggère un automatisme |
| `main_window.py:2476` | `Ajouter une observation ✓` | `Modifier l'observation…` | État |
| `main_window.py:2486-2487` | `Aucune analyse effectuée` / `Ouvrez un dossier DICOM pour lancer l'analyse` | garder ; ajouter `(Ctrl+O ou glisser-déposer)` | Raccourci |
| `main_window.py:2493` | `Base de données des appareils` | `Base de données des installations` | Vocabulaire |
| `main_window.py:2514` | `Sélectionnez une installation ci-dessus pour afficher l'historique de ses contrôles` | base vide : `Aucune installation enregistrée — chargez une série puis « Nouvelle installation »` | État vide |
| `main_window.py:2976` | `±7 HU (NCG: ±25 HU)` | `−7 à +7 UH (non-conformité grave au-delà de ±25 UH)` | ANSM, typographie |
| `main_window.py:2978-2983` | `✗ NCG` / `✗ NC` | `✗ Non-conformité grave` / `✗ Non conforme` | Lisibilité |
| `main_window.py:2995` | `Écart max C-P` | `Écart max. centre – périphérie` | Abréviation opaque |
| `main_window.py:3004` | `Écart-type central` | `Bruit σ (écart-type ROI centrale)` | Terme ANSM « bruit » |
| `main_window.py:3010-3013` | `Moyenne` / `Écart-type` (en-têtes) | `Moyenne (UH)` / `σ (UH)` | Unités |
| `main_window.py:3059` | `Nombre de ROIs` | `ROI mesurées` (`7 / 8`) | Pluriel, information |
| `main_window.py:3060` | `Taille ROI` | `Taille des ROI` | Français |
| `main_window.py:3061, 3233` | `cycles/mm` | `mm⁻¹` (ou `cycles/mm` partout, mais pas « c/mm ») | `c/mm` (`:3400, 2677`) non standard |
| `main_window.py:3120` | `f_moy: {x} c/mm` (légende du graphique) | `Fréquence moyenne : {x} mm⁻¹` | Notation de code |
| `main_window.py:3155` | `W: 80, L: 0 UH` | `Largeur 80 UH, centre 0 UH` | Typographie |
| `main_window.py:3197, 3226` | `Renseigner σ de référence` / `Renseigner fréquence de référence` | `Non évalué — définir la valeur de référence (Modifier…)` | Dit où aller |
| `main_window.py:3384` | `Installation non enregistrée` | `Installation non enregistrée — cliquez sur « Nouvelle installation »` | Action |
| `main_window.py:3400-3402` | `σ réf. 8,18 HU · f SPB réf. 0,262 c/mm` | `Références : σ 8,18 UH · f SPB 0,262 mm⁻¹` | Unités |
| `history_panel.py:35` | `CT eau`, `Unif.`, `Bruit σ`, `f SPB` | `CT eau (UH)`, `Unif. (UH)`, `σ (UH)`, `f SPB (mm⁻¹)` + infobulles | Unités |
| `history_panel.py:145` | `Définir comme référence` | `Prendre comme référence…` | Ellipse (confirmation) |
| `history_panel.py:155` | `Action corrective…` | `Action corrective…` (ok) ; infobulle : ajouter `Obligatoire après une non-conformité (registre, 3.2.2)` | Guidage |
| `history_panel.py:160` | `Supprimer` | `Supprimer…` | Ellipse |
| `history_panel.py:162` | `Exporter CSV` | `Exporter en CSV…` | Ellipse |
| `history_panel.py:250` | `Aucun contrôle enregistré pour cette installation` | `Aucun contrôle enregistré : le premier sera ajouté à l'export du rapport` | Guidage |
| `history_panel.py:326-327` | `Supprimer le contrôle du … de l'historique ?\nLe rapport PDF n'est pas supprimé.` | garder + `Cette action est irréversible.` + bouton `Supprimer` | Destructif |
| `history_panel.py:374` | `Exporter l'historique` / `historique_cq.csv` | `Exporter l'historique en CSV` / `historique_CQ_{installation}_{date}.csv` | Nom parlant |
| `main.py:183-186` | `Erreur inattendue` / `…Merci de joindre ce fichier à tout signalement de problème.` | garder + bouton `Ouvrir le dossier du journal` | Action |
| `pdf_report.py:103-120` | `NON-CONFORMITÉ GRAVE` / `NON CONFORME` | garder ; ajouter symboles `✗` / `✓` | Monochrome |
| `pdf_report.py:155, 300, 764…` | `HU` | `UH` | Cohérence |
| `pdf_report.py:300` | `Inspection artéfacts (L=0, W=80 HU)` | `Fenêtre d'inspection : centre 0 UH, largeur 80 UH` | Cohérence avec la ligne au-dessus |
| `pdf_report.py:572-573` | `Contrôle de qualité interne trimestriel du …` | `Contrôle de qualité interne {type} du …` | U-39 |
| `pdf_report.py:748-750, 808-822` | `Non renseigné` / `N/A` | `Non renseigné` / `— (absent des DICOM)` | Anglais |
| `pdf_report.py:966` | `Inspection des Artéfacts` | `Inspection visuelle des artéfacts` | Casse FR, terme |
| `pdf_report.py:1004-1007` | `Statut : Non inspecté` | `Résultat : TEST NON RÉALISÉ — inspection visuelle à effectuer` (en orange) | Visibilité |
| `pdf_report.py:1207-1210` | `Valeur attendue : 0 HU` / `Critère NC : ±7 HU` / `Critère NCG : ±25 HU` | `Critère d'acceptabilité : −7 UH ≤ nombre CT ≤ +7 UH` / `Non-conformité grave : nombre CT ≤ −25 UH ou ≥ +25 UH` | Vocabulaire ANSM |
| `pdf_report.py:1246-1249` | `12h (Haut)` … | `12 h (haut)` … | Typographie |
| `pdf_report.py:1253` | `Écart vs Centre (HU)` | `Écart par rapport au centre (UH)` | Anglicisme |
| `pdf_report.py:1340` | `Spectre de Puissance du Bruit (SPB)` | `Spectre de puissance du bruit (SPB)` | Casse FR (idem `:3053` GUI, `:159` titre figure) |
| `pdf_report.py:1444-1445` | `Bruit : 3,30 HU` (sans statut) | `Bruit : 3,30 UH — non évalué (valeur de référence absente)` | U-01 |
| `pdf_report.py:1108-1110` | `Zone verte : tolérance ANSM autour de la valeur de référence actuelle ; points orange : hors tolérance.` | `Bande verte : tolérance ANSM autour de la référence actuelle ; points orange : non conformes ; points rouges : non-conformité grave.` | Complet |

## 6. Accessibilité : contrastes calculés et synthèse clavier

Ratios WCAG 2.1 (texte normal : AA ≥ 4,5:1 ; texte large/gras ≥ 18 px ou ≥ 14 px gras : AA ≥ 3:1). Les textes concernés font 11-13 px, donc « normal ». Calcul : `scratchpad/ux/contrast.py`.

| Couple (usage, fichier:ligne) | Ratio | AA normal | AA large |
|---|---|---|---|
| **Thème sombre** | | | |
| `#e0e0e0` sur `#2b2b2b` (texte principal, `theme.py:36`) | 10,73 | OUI | OUI |
| `#aaaaaa` sur `#2b2b2b` (`text_secondary`, `th`) | 6,09 | OUI | OUI |
| `#888888` sur `#2b2b2b` (`pending`, `theme.py:44`) | 3,99 | NON | OUI |
| `#888888` sur `#2d2d2d` (résumé d'installation, sections lecture seule, `main_window.py:1352, 280-335`) | 3,89 | NON | OUI |
| `#666666` sur `#1e1e1e` (indice d'accueil, `image_viewer.py:994`) | 2,90 | NON | NON |
| `#666666` sur `#2d2d2d` (chemin de la base, `main_window.py:153`) | 2,40 | NON | NON |
| `#4fc3f7` sur `#2b2b2b` (titres de section) | 7,07 | OUI | OUI |
| `#4caf50` sur `#2b2b2b` (`.ok`, `main_window.py:2941`) | 5,09 | OUI | OUI |
| `#ff9800` sur `#2b2b2b` (`.nc`) | 6,57 | OUI | OUI |
| `#f44336` sur `#2b2b2b` (`.ncg`) | 3,85 | NON | OUI |
| `#ffcc80` sur `#4a3000` (texte d'avertissement) | 8,29 | OUI | OUI |
| blanc sur `#2a82da` (bouton accentué, `theme.py:49`) | 3,96 | NON | OUI (gras 12-14 px : limite) |
| `#2a82da` sur `#2d2d2d` (liens « définir… », « mémoriser… ») | 3,48 | NON | OUI |
| `#888888` sur `#444444` (bouton accentué désactivé) | 2,75 | NON | NON |
| `#7f7f7f` sur `#2d2d2d` (texte désactivé de la palette, `main.py:50`) | 3,44 | NON | OUI |
| `#dddddd` sur `#1e1e1e` (cartouche d'informations) | 12,27 | OUI | OUI |
| `#ffcc00` / `#00cc00` sur `#1e1e1e` (UH / SPB, overlay) | 11,03 / 7,65 | OUI | OUI |
| `#66bb6a` / `#ffa726` / `#ef5350` sur `#2b2b2b` (historique ok/nc/ncg) | 5,99 / 7,29 / 4,06 | OUI / OUI / NON | OUI |
| `#888888` sur `#2b2b2b` (graduations du graphique SPB, `main_window.py:3128`) | 3,99 | NON | OUI |
| blanc sur `#2e7d32` / `#d84315` (boutons d'artéfacts) | 5,13 / 4,44 | OUI / NON | OUI |
| **Thème clair** | | | |
| `#222222` sur blanc | 15,91 | OUI | OUI |
| `#999999` sur blanc (`pending`, `theme.py:24`) | 2,85 | NON | NON |
| `#888888` sur blanc / sur `#efefef` (mêmes libellés qu'en sombre, codés en dur) | 3,54 / 3,08 | NON | OUI |
| `#4caf50` / `#ff9800` / `#f44336` sur blanc (`.ok/.nc/.ncg` — mêmes couleurs qu'en sombre) | 2,78 / 2,16 / 3,68 | NON | NON / NON / OUI |
| `#e65100` sur `#fff3e0` (titre d'avertissement) | 3,46 | NON | OUI |
| `#cccccc` sur `#f0f0f0` (titre « Afficher/Cacher », `image_viewer.py:1121`) | 1,41 | NON | NON |
| `#ffcc00` / `#00cc00` / `#cccccc` sur `#f0f0f0` (UH / SPB / Infos) | 1,33 / 1,91 / 1,41 | NON | NON |
| `#2e7d32` / `#ef6c00` / `#c62828` sur blanc (historique) | 5,13 / 3,08 / 5,62 | OUI / NON / OUI | OUI |
| **PDF (fond blanc)** | | | |
| vert `(0,0.5,0)` | 5,14 | OUI | OUI |
| orange ReportLab `#ffa500` (NC : textes `:495-498`, badge `:123-131`) | 1,97 | NON | NON |
| rouge `#ff0000` (NCG) | 4,00 | NON | OUI |
| blanc sur vert `(0,0.6,0)` (badge CONFORME) | 3,78 | NON | OUI |
| gris pied de page `#808080` | 3,95 | NON | OUI |

**Synthèse clavier** : menus avec mnémoniques (F, A, C, A — « Affichage » et « Aide » partagent « A ») ; aucune action de menu ni aucun bouton avec mnémonique ; raccourcis globaux Ctrl+O/E/I/Q, F1, ←/→/Début/Fin/Page, H/N/F/R/U/S/I/A non affichés dans les menus ; `ToggleSwitch` non focusable ; graphique de tendance non accessible au clavier (sélection par clic uniquement, mais le tableau est focusable) ; dialogues modaux : Échap ferme (non indiqué), Entrée valide le bouton par défaut (dans l'inspection des artéfacts, le bouton « Absence d'artéfacts » est `autoDefault` : **Entrée déclare l'absence d'artéfacts**, à neutraliser) ; focus visible : rectangle Fusion sur les boutons, pas sur les éléments de l'overlay. Daltonisme : verdicts toujours accompagnés d'un texte (bon) ; points du graphique de tendance et bande de tolérance codés par la couleur seule (U-33) ; ROI mesurées/ignorées vert/rouge avec texte « (ignorée) » (bon).

## 7. Rapport PDF : constats spécifiques et maquette proposée

Constats détaillés : U-01 (conformité sans tests), U-35 (pagination/version), U-36 (pages vides), U-37 (résumé), U-38 (coupes et ROI), U-39 (validation, type de contrôle), U-40 (notes, graphiques, couleurs), U-10 (UH/HU), U-47 (N/A). Points positifs : en-tête des pages 2+ avec établissement, installation (n° d'inventaire) et date ; sections du registre (fantôme, protocole clinique d'origine, algorithme de reconstruction, UID de série et dossier d'archive, tableau des positions/tailles de ROI en px et mm avec origine de la géométrie figée, actions correctives, historique avec statuts gelés) ; actions ANSM reprises mot pour mot (« Remise en conformité dès que possible », « Arrêt de l'exploitation et signalement… »).

Éléments manquants ou mal mis en évidence pour un auditeur :
- l'état de chaque test en première page avec référence et critère (U-37) ; l'état « non réalisé » visible dès la page 1 (U-01) ;
- la personne ayant réalisé et validé le contrôle, et une zone de signature (U-39) ;
- les numéros de coupes (et positions z) utilisés (U-38) ;
- le type de contrôle (trimestriel / semestriel / avant mise en service / après intervention) (U-39) ;
- la date de définition des valeurs de référence et le contrôle dont elles proviennent (le texte « figées depuis le contrôle du … » existe pour la géométrie, pas pour σ et f SPB) ;
- une pagination « n / N » et la version sur chaque page (U-35) ;
- les éléments 3.2.1 du registre (domaines d'utilisation clinique, modes d'acquisition/reconstruction en routine, modulation, épaisseurs les plus utilisée/la plus fine, rapports d'intervention) ne sont pas gérés par l'application : hors périmètre actuel, mais une section « Informations relatives à l'installation (registre 3.2.1) » saisie une fois par installation et imprimée en annexe donnerait un rapport autoportant.

Maquette textuelle de la structure proposée (4 pages pour un contrôle standard) :

```
[Page 1 — garde + verdict]
Logo | CHU Exemple · Imagerie 2
RAPPORT DE CONTRÔLE DE QUALITÉ INTERNE — TOMODENSITOMÈTRE
Contrôle trimestriel du 15/09/2026 · Décision ANSM du 18/12/2025, point 9.1.7
Installation : GE Discovery RT · n° de série 123456 · n° d'inventaire INV-042 · mise en service 12/03/2021
Réalisé par : ______________   Validé par (physicien médical) : ______________   Édité le 01/10/2026 16:02

┌──────────────────────────────────────────────────────────────────────────────┐
│  ✗  NON CONFORME — remise en conformité dès que possible                      │
└──────────────────────────────────────────────────────────────────────────────┘

Résumé des tests
Test                         Mesure           Référence / attendu   Critère d'acceptabilité        Conformité
Nombre CT de l'eau           −0,8 UH          0 UH                  −7 ≤ CT ≤ +7 UH (grave ±25)     ✓ Conforme
Uniformité                   0,2 UH           —                     écart max ≤ 7 UH                ✓ Conforme
Bruit (stabilité)            3,30 UH          8,18 UH (réf. 10/03/2023)  −0,82 ≤ Δ ≤ +0,82 UH       ✗ Non conforme
Fréquence moyenne SPB        0,310 mm⁻¹       0,262 mm⁻¹            |Δ| ≤ 10 %                      ✗ Non conforme
Artéfacts (visuel)           aucun observé    —                     aucun artéfact gênant           ✓ Conforme
Observations : Fantôme centré au laser. À surveiller : bruit proche de la limite.

Protocole de contrôle (registre 3.2.2) : CQ FANTOME EAU 120kV · issu de « Abdomen standard »
120 kV · 350 mA · 23 mAs · 1,00 s/tour · hélicoïdal pitch 0,938 · collimation 64 × 0,625 mm · foyer 1,2
STANDARD / ASiR-V 40 % · coupe 5,0 mm · 512 × 512 · 0,469 mm/px · FOV 240 mm · IDSV affiché 12,34 mGy (BODY32)
Coupes utilisées : UH 12 / 24 (z = +5,0 mm) · SPB 8–17 (10 coupes)
Fantôme : PTW Fantôme d'eau 20 cm, n° PH-77 · Série DICOM : 1.2.826…9999.1 · Archive : /data/CQ/2026-09-15/S3

[Page 2 — ROI, nombre CT, uniformité]
Visualisation des ROI (images annotées C, 12 h, 3 h, 6 h, 9 h / SPB 1-8, mêmes couleurs que l'écran)
Tableau positions/tailles (px, mm) + origine (« figées depuis le contrôle du … »)
Nombre CT de l'eau — tableau + verdict ; Uniformité — tableau des 4 ROI + verdict

[Page 3 — bruit et SPB]
Bruit — mesure, référence (date), écart, critère, verdict, action
SPB — coupes, ROI mesurées (8/8), taille, fréquence moyenne, référence, écart, critère, verdict ; courbe 11 × 7 cm ; avertissements

[Page 4 — artéfacts, historique, validation]
Inspection visuelle des artéfacts — image 9 cm (fenêtre 0/80 UH), déclaration, description, verdict
Historique (facultatif) — tableau 10 derniers contrôles, actions correctives, 2 graphiques 15 cm empilés
Bloc de validation — Réalisé par / date / signature ; Validé par / date / signature
Pied de page sur toutes les pages : « CQ TDM 0.7.0 · contrôle du 15/09/2026 · page n / N »
```

## 8. Points positifs

- Guidage par l'état : bouton d'accueil accentué, sélecteur « Installation inconnue » + bouton « Nouvelle installation » mis en avant, liens d'action dans le résumé (« définir les valeurs actuelles comme références », « mémoriser ces coupes »), résumé « Coupes … ≠ enregistrées » en orange.
- Curseur de coupe à marqueurs glissables (triangle UH, bande SPB) avec curseurs de souris adaptés ; raccourcis H/N pour y revenir.
- Historique riche : ligne « En cours » comparée aux références, bande de tolérance ANSM, clic sur un point → ligne, infobulle au survol, statuts gelés à la date du contrôle, relocalisation des séries DICOM vérifiée par UID.
- Messages de confirmation déjà présents sur les actions sensibles (références, géométrie, suppression) et explication des conséquences (géométrie des ROI).
- Gestion des erreurs globales : journal borné, boîte « Erreur inattendue » avec détails, base de données corrompue sauvegardée en `.bak`.
- Rapport PDF structuré par test avec les formulations ANSM, les éléments du registre, les UID/dossiers d'archive et les positions de ROI.
- Thème sombre cohérent, texte principal à 10,7:1 ; infobulles stylées corrigées pour le thème.
- Installateur Inno Setup en français, sans droits administrateur ; exécutable portable ; glisser-déposer de dossier.

## 9. Plan d'action suggéré pour Claude Code local

1. **Garde-fou d'export et état global** — U-01, U-20 — **M** — *gain rapide* pour la partie dialogue de complétude (`_export_pdf`) ; état « incomplet » dans le PDF ensuite.
2. **Traduction Qt + boutons explicites** — U-02 — **S** — *gain rapide* (10 lignes dans `main.py`, puis remplacement des `Yes|No`/`Ok|Cancel`).
3. **Fenêtre des installations : drapeau de modification, Enregistrer/Fermer, confirmation de perte, suppression explicite avec sauvegarde** — U-03, U-04, U-24 — **M**.
4. **Retour d'activité** : curseur d'attente + `QProgressDialog` autour du chargement, du SPB et du PDF ; délais et préfixes dans la barre de statut — U-05, U-30 — **S/M** — *gain rapide* pour le curseur d'attente.
5. **Tailles d'écran** : dimensions relatives à l'écran pour les deux dialogues, minimum de la fenêtre principale, stretch du splitter, barre d'outils de l'historique, retour à la ligne du résumé — U-06, U-12, U-53 — **M**.
6. **Persistance** : géométrie, splitter, derniers dossiers (DICOM, rapports), titre de fenêtre, dossiers récents — U-13, U-48 — **S** — *gain rapide*.
7. **Sécurité des mesures** : touche R limitée à l'affichage ; avertissement de détection de fantôme en repli ; fichiers ignorés signalés ; ROI SPB ignorées dans les résultats — U-07, U-08, U-09, U-34 — **M**.
8. **Unités et libellés** : « UH » partout, espaces insécables, « … », « ROI » invariable, tableau §5 — U-10, U-22, U-31, U-47, U-54 — **M** — *gain rapide* (recherche/remplacement guidé par le tableau).
9. **Visionneuse** : libellés de ROI cosmétiques et centrés, cartouche réduit/replié, interrupteurs hors image, ROI UH atténuées hors coupe UH, molette = coupes — U-11, U-26, U-27, U-45 — **M/L**.
10. **Inspection des artéfacts** : navigation clavier/molette, compteur de coupes vues, Annuler, style `:disabled`, Entrée neutralisée — U-14, U-25 — **M**.
11. **Thèmes et contrastes** : couleurs des overlays, du graphique SPB et des statuts tirées de `theme_colors()` ; remplacement de `#666/#888` ; accent plus foncé — U-16, U-17, U-55 — **M**.
12. **Historique** : CSV en virgule décimale, en-têtes avec unités et infobulles, bouton Détails, dates « mois année », marqueurs par statut — U-15, U-32, U-33 — **S/M** — *gain rapide* pour le CSV.
13. **PDF — structure** : pied de page n/N + version, sections sécables, résumé en tableau avec référence/critère/« non réalisé », coupes utilisées, ROI annotées, bloc de validation, type de contrôle — U-35 à U-39 — **L**.
14. **PDF — rendu** : `Paragraph` dans les listes des notes, graphiques de tendance lisibles, orange foncé et symboles — U-40 — **S** — *gain rapide*.
15. **Formulaires** : onglets de la fenêtre des installations, `QDateEdit`, champs requis, dialogue d'action corrective contextualisé, observations renommées et protégées, options d'export — U-23, U-43, U-44, U-50, U-51, U-52 — **M**.
16. **Aide et support** : aide/raccourcis/À propos dans un `QTextBrowser` alignés sur les 7 étapes, accès au journal, lien issues, premier lancement guidé — U-19, U-28, U-29, U-41, U-42 — **S/M**.
17. **Installation** : scripts en français, éditeur, icône locale — U-49 — **S** — *gain rapide*.

Captures et artefacts de la revue : `scratchpad/ux/*.png` (56 captures), `scratchpad/ux/demo_report.pdf`, `scratchpad/ux/demo_report_nc.pdf`, `scratchpad/ux/pdf_ok-*.png`, `scratchpad/ux/pdf_nc-*.png`, scripts `capture.py` et `contrast.py`.
