# Revues du 2026-10-01 — CQ TDM 0.7.0 (commit 0c60fae)

Trois revues indépendantes, menées en parallèle sur le code tel quel, **sans aucune modification du code source**. Elles sont rédigées pour être données à Claude Code (ou à un relecteur) afin d'appliquer les corrections une par une.

| Rapport | Périmètre | Constats |
|---|---|---|
| [`REVUE_CODE.md`](REVUE_CODE.md) | Bugs, robustesse, persistance (`devices.json`), DICOM atypiques, tests, packaging, CI | 47 (3 critiques, 11 majeurs, 25 mineurs, 8 suggestions), préfixe `C-` |
| [`REVUE_UX_UI.md`](REVUE_UX_UI.md) | Parcours utilisateur, libellés, disposition, visionneuse, formulaires, historique, accessibilité, rapport PDF | 55 (1 bloquant, 26 majeurs, 24 mineurs, 4 améliorations), préfixe `U-` |
| [`REVUE_PHYSIQUE_MATHS.md`](REVUE_PHYSIQUE_MATHS.md) | Conversion UH, détection du fantôme, géométrie des ROI, bruit, SPB/NPS, fréquence moyenne, statuts ANSM, validation | 23 (2 critiques, 4 majeurs, 13 mineurs, 4 suggestions), préfixe `P-` |

Chaque rapport se termine par un **plan d'action ordonné** (section « Plan d'action suggéré pour Claude Code local ») avec une taille S/M/L par tâche.

## Ordre de traitement conseillé

1. Les constats qui peuvent produire un **résultat ou un statut de conformité faux** : `U-01` (PDF « CONFORME » sans inspection des artéfacts ni références), `P-01` (fréquence moyenne sur le fit polynomial), `P-02` (définition de la magnitude du bruit), `C-01` à `C-03` (base `devices.json` partagée).
2. Les constats de **chargement DICOM** (`C-04` à `C-06`) et de **retour utilisateur** (`U-02` à `U-06`).
3. La mise en place d'une **CI test + lint** (`C-08`, `C-14`) avant d'enchaîner les corrections mineures, pour les sécuriser.

Attention : les corrections `P-01` à `P-05` **changent les valeurs calculées** ; elles imposent une nouvelle validation contre les références ANSM, un changement de version majeure et le ré-établissement des valeurs de référence des installations.

## Annexes

Les scripts utilisés par les agents pour confirmer les constats sont dans `annexes/`. Dans les rapports, un chemin `scratchpad/<code|ux|physique>/...` correspond à `annexes/<code|ux|physique>/...`. Certains scripts contiennent des chemins absolus de la session d'analyse (dossier de travail temporaire) à adapter avant exécution.

- `annexes/code/` : sondes DICOM atypiques, base partagée, caractères spéciaux dans le PDF.
- `annexes/ux/` : script de captures hors écran (`capture.py`, `QT_QPA_PLATFORM=offscreen`), calcul des contrastes WCAG (`contrast.py`), bases `devices_*.json` factices pour les scénarios. Les 56 captures PNG et les PDF de démonstration ne sont pas inclus.
- `annexes/physique/` : tests synthétiques du SPB (bruit blanc, Parseval, fréquence moyenne analytique, balayage du degré et du domaine du polynôme), statuts de l'eau, avec leurs sorties `*_out.txt`.

## Conditions de l'analyse

Python 3.11, numpy 2.4, scipy 1.17, pydicom 3.0, PySide6 6.11, reportlab 5.0, matplotlib 3.11, hors écran (Linux). Les données `test_data/` de validation ANSM n'étaient pas disponibles : les tests `test_nps_validation.py` sont en erreur ou ignorés dans cet environnement, et les vérifications numériques reposent sur des images synthétiques.
