# Revue physique et mathématiques — CQ TDM (date 2026-10-01, commit 0c60fae, version 0.7.0)

Périmètre : `src/cq_tdm/core/{nps,water_phantom,roi_geometry,dicom_loader,qc_history,trend_chart}.py`, les parties numériques de `reports/pdf_report.py`, `gui/main_window.py`, `gui/image_viewer.py`, les tests `tests/test_nps_validation.py`, `test_phantom_detection.py`, `test_roi_geometry.py`, les figures `docs/*.png`, le README. Références : décision ANSM du 18/12/2025 (annexe A, points 3.2.2, 5.1, 6.2, 6.26, 6.31, 6.32, 8, 9.1.7 ; annexe C) et guide d'application v1 du 23/12/2025 (§4 « Logiciels », tableau §9).

Toutes les vérifications numériques ont été exécutées (numpy 2.4.6, scipy 1.17.1) avec les scripts du scratchpad `physique/` (`t1_nps_core.py`, `t2_water_status.py`, `t3_fit_sweep.py`, `t3b_nanmean.py`, sorties `t*_out.txt`). Aucun fichier du dépôt n'a été modifié. Les images de référence ANSM ne sont pas dans le dépôt (`test_data/` ignoré par git, téléchargées par la CI depuis une release GitHub `test-data`) : les constats sur la validation s'appuient sur `docs/nps_validation_combined.png` (v0.7.0) et sur le code des tests.

---

## 1. Résumé exécutif

La chaîne de calcul est globalement juste dans ses fondamentaux : conversion UH correcte, détection sub-pixel du bord interne robuste, géométrie des ROI UH conforme à la décision, normalisation du SPB 2D exacte (NPS = Δx·Δy/(Nx·Ny)·|FFT|², vérifiée par Parseval à 0,1 % près), axes de fréquence en mm⁻¹ corrects pour les ROI de 64 et 128 px, statuts de conformité fidèles aux formules de la décision.

Constats : **2 critiques, 4 majeurs, 13 mineurs, 4 suggestions**.

Les points qui peuvent changer un résultat ou un statut :
1. **[P-01]** La fréquence moyenne est calculée sur l'ajustement polynomial de degré 11 étendu jusqu'à 1,375 × Nyquist : dès que le pixel est fin (≤ 0,4 mm : tête, DFOV ≤ 200 mm, matrices 1024) et le noyau mou, le polynôme ne suit plus un spectre concentré sur une fraction du domaine et **surestime f_moy de +10 % à +50 %** (spectre brut : +0,3 à +3 %). Les séries ANSM (pixel 0,47–0,67 mm) ne révèlent pas ce défaut.
2. **[P-02]** La magnitude du bruit est l'écart-type de la ROI centrale (40 % du diamètre) sur **une seule coupe**, alors que la décision (9.1.7.2) demande de déterminer le bruit « sur l'ensemble des 10 coupes » avec les ROI du SPB — et c'est ce que rapporte iQMetrix (« Noise (HU) »). Une valeur de référence venant d'un autre outil n'est pas comparable.
3. **[P-03]** L'axe de fréquence radial est faux de +0,6 à +1,85 % pour les tailles de ROI 40, 56, 72, 88, 104 et 120 px, toutes atteignables par la règle « 15 % du diamètre arrondi à 8 px ».
4. **[P-04]** Au-delà de Nyquist, les échantillons hors matrice sont remplacés par des zéros puis moyennés : pour un noyau dur (puissance à Nyquist), f_moy est sous-estimée de 4,5 à 7,6 % par rapport à une moyenne sans zéros — c'est vraisemblablement l'origine du −3 % sur la série 4 de l'ANSM.
5. **[P-05]** La validation ANSM accepte 10 % d'écart (= la tolérance réglementaire) ; l'écart constaté est systématique (+2,5 à +6,4 % sur 4 séries, −3 % sur la 5ᵉ), inexpliqué, et aucune grandeur absolue (intensité du pic, √AUC, bruit) n'est comparée aux références.

---

## 2. Exigences de la décision ANSM et du guide

| Grandeur | Définition réglementaire | Tolérance | ROI / coupes requises | Référence |
|---|---|---|---|---|
| Nombre CT de l'eau (exactitude, mode conventionnel) | Nombre CT **moyen** des pixels de la ROI centrale | Conforme si −7 ≤ CT ≤ 7 UH ; NC si 7 ≤ \|CT\| ≤ 25 (guide) / 7 < \|CT\| ≤ 25 (décision) ; **NCG si CT ≤ −25 ou 25 ≤ CT** (arrêt + signalement ANSM/ARS sous 2 jours ouvrés) | ROI circulaire au centre de l'image du fantôme, Ø = 40 % du Ø du fantôme, dans la **coupe reconstruite centrale** | A-9.1.7.2, A-9.1.7.3 ; guide tableau §9 |
| Nombre CT de l'eau (stabilité, mode spectral) | Δ(CT) = CT_i − CT_ref (ROI centrale) | mêmes seuils (±7 NC, ±25 NCG) | idem | A-9.1.7.3 |
| Uniformité | Différence maximale entre le CT moyen de la ROI centrale et le CT moyen de **chacune** des 4 ROI périphériques : −7 ≤ MAX(CT_centrale − CT_périph) ≤ 7, c.-à-d. max \|Δ\| ≤ 7 | ±7 UH (pas de NCG) | 4 ROI circulaires aux positions cardinales ; Ø ≤ 10 % du Ø fantôme et ≥ 100 pixels ; même taille ; **bord externe entre 10 et 15 mm du bord interne** du fantôme ; pas de chevauchement avec la centrale ; **positions et tailles identiques d'un contrôle à l'autre** ; coupe centrale | A-9.1.7.2, A-9.1.7.3 |
| Bruit (magnitude) | Écart-type s des nombres CT des pixels d'une ROI (6.2) ; « déterminer le SPB **et le bruit** sur l'ensemble des 10 coupes » au moyen des ROI du logiciel SPB | MIN(−0,2 ; −0,1·B_ref) ≤ B_i − B_ref ≤ MAX(0,2 ; 0,1·B_ref) (stabilité) | ROI « en nombre et de taille suffisants », définies par l'exploitant et consignées au registre (3.2.2) ; **10 coupes centrées sur la coupe centrale** | A-6.2, A-9.1.7.2, A-9.1.7.3, A-3.2.2 |
| SPB / fréquence moyenne | « Intensité du bruit en fonction de la fréquence spatiale » (6.32) ; méthode non précisée ; **logiciel à valider sur la banque d'images de référence ANSM** (résultats « équivalents ») | −10 % ≤ (f_i − f_ref)/f_ref ≤ 10 % (stabilité) | idem bruit ; relever la fréquence moyenne de la puissance du bruit | A-6.32, A-9.1.7.2, A-9.1.7.3 ; guide §4 |
| Artéfacts | Recherche visuelle sur toutes les coupes, fenêtre centrée 0 UH, largeur 80 UH | aucun artéfact cliniquement gênant | toutes les coupes | A-9.1.7.2 |
| Acquisition | Fantôme d'eau Ø ≥ 16 cm centré ; ≥ 10 coupes ; images axiales DICOM non compressées (ou décompressées sans perte) ; DFOV = Ø fantôme + 2 cm ; modulation désactivée | — | — | A-4, A-5.1, A-8, A-9.1.7.2 |
| Valeurs de référence | Fixées au CQE initial ; nouvelle référence après intervention ou modification de protocole | — | — | A-8 |
| Registre des opérations | Position et taille des ROI du SPB ; résultats liés aux images DICOM archivées ; état de conformité ; actions correctives | — | — | A-3.2.2 ; guide §3.2 |
| Périodicité | CQI trimestriel (semestriel pour le per-opératoire) | — | — | Annexe C-2.2 |

---

## 3. Chaîne de calcul implémentée

```
DICOM (pydicom) ──► HU = pixel.astype(float64)·RescaleSlope + RescaleIntercept       [dicom_loader.py:214-221]
   │  tri par (SliceLocation | IPP[z], InstanceNumber)                                 [dicom_loader.py:159-161]
   │  pixel_size_mm = PixelSpacing[0] (pixels supposés carrés)                        [dicom_loader.py:77-80]
   ▼
Détection du fantôme (coupe UH / coupe médiane de la plage SPB)                        [dicom_loader.py:531-568]
   centroïde de la plus grande composante |HU|<100 → 72 rayons, 1er franchissement
   |HU|≥50 UH sur 3 échantillons (lissage 3 px), interpolation linéaire → cercle de
   Kasa robuste (rejet > max(2 px, 3·médiane)) ×2 passes → (centre, rayon interne) sub-pixel
   ▼
Géométrie des ROI (figée par installation à partir du 1er contrôle)                     [roi_geometry.py:66-100]
   r_centrale = int(0,40·D/2) ; r_périph = max(int(0,10·D/2), ceil(√(100/π))=6)
   d_périph  = round(R − 12,5 mm/Δx − r_périph)   (bord externe à 12,5 mm de la paroi)
   côté SPB  = clip(round(0,15·D/8)·8, 32, 128) ; offsets SPB 0,455·R (cardinal), 0,367·R/axe (diagonal)
   ▼
Nombre CT / uniformité / bruit (1 coupe « UH », défaut = coupe médiane)                [water_phantom.py:235-293]
   CT_eau = mean(ROI centrale) ; uniformité = max_k |mean(ROI_k) − CT_eau| ;
   bruit = std(ROI centrale, ddof=0)                                                   ← P-02
   statuts : |CT|≤7 ok, ≤25 NC, >25 NCG ; uniformité ≤7                               [qc_history.py:146-166]
   ▼
SPB : 10 coupes (défaut (N−10)//2 … +9) × 8 ROI carrées                                [nps.py:498-706]
   ROI_i(x,y) − P₂(x,y)   (polynôme 2D d'ordre 2, 6 coefficients, lstsq)              [nps.py:230-257]
   NPS₂D,i = Δx²/(N²)·|fftshift(FFT2(ROI_i − P₂))|²        (HU²·mm²)                  [nps.py:260-288]
   NPS₂D = ⟨NPS₂D,i⟩ sur 8 ROI × 10 coupes (moyenne des spectres)                      [nps.py:583-643]
   NPS₁D(f_k) = moyenne de 37 profils (θ = 0°,10°,…,360°) interpolés bilinéairement,
       r_k = k px, k = 0…int(0,6875·N) ; hors matrice → 0                              [nps.py:290-370]
       axe affiché : f_k = linspace(0, 1,375·f_Nyq, int(0,6875·N)+1)                   ← P-03
   fit = max(polyfit(f, NPS₁D, 11), 0) sur tout [0 ; 1,375·f_Nyq]                        [nps.py:373-406]
   f_moy = ∫ f·fit df / ∫ fit df  (trapèzes, 0 → 1,375·f_Nyq)                           [nps.py:656-665] ← P-01
   ▼
Statuts de stabilité : B_i − B_ref ∈ [min(−0,2;−0,1B_ref) ; max(0,2;0,1B_ref)] ;
                       f_i ∈ [0,9 f_ref ; 1,1 f_ref]  (bornes incluses, ε = 1e-9)       [qc_history.py:136-166]
   ▼
PDF (valeurs arrondies : CT 1 déc., σ 2 déc., f 3 déc., écart % 1 déc.) ; graphe de tendance
```

Formules codées (vérifiées) :
- `compute_nps_2d` (nps.py:273-287) : `nps_2d = |fftshift(fft2(roi))|² · Δx² / (rows·cols)` ; `freq = fftshift(fftfreq(N))/Δx` → unités HU²·mm² et mm⁻¹ correctes ; Σ NPS₂D·Δf² = σ² (Parseval, §6 test A).
- `radial_average` (nps.py:290-370) : 37 angles, `r_values = linspace(0, int(N/2·1,375), int(N/2·1,375)+1)` (pas exact de 1 px), `map_coordinates(order=1, mode='constant', cval=0)`, `nps_r = mean(profils)`.
- `fit_nps_polynomial` (nps.py:373-406) : `np.polyfit(f, nps, 11)` sur l'abscisse en mm⁻¹ non normalisée, puis `np.maximum(fit, 0)`.
- `analyze_nps` (nps.py:656-665) : `total_power = trapz(fit, f)`, `mean_frequency = trapz(f·fit, f)/total_power`.

---

## 4. Tableau de conformité exigence ↔ implémentation

| Exigence | Implémentation | Verdict | Constat |
|---|---|---|---|
| CT eau = moyenne ROI centrale Ø 40 %, coupe centrale | `mean(ROI centrale)`, r = int(0,4·D/2) ; coupe médiane par défaut, modifiable et mémorisée par installation | Conforme (réserve sur la coupe mémorisée) | P-18 |
| Seuils CT eau ±7 / ±25, NCG | `|v|≤7 → ok ; ≤25 → NC ; >25 → NCG` | Écart mineur : borne 25 exclue du NCG ; incohérence valeur affichée/statut | P-08 |
| Stabilité CT eau (mode spectral) | non implémentée (exactitude seulement) | Non vérifiable / hors périmètre déclaré | P-22 |
| Uniformité = max \|CT_c − CT_p\| sur 4 ROI cardinales | `max(abs(m − water_ct))` | Conforme | — |
| ROI périph. Ø ≤ 10 % D, ≥ 100 px, même taille | `int(0,10·D/2)`, min r = 6 px (113 px) | Conforme (8,8–10 % ; 113–5541 px testés) | — |
| Bord externe entre 10 et 15 mm de la paroi interne | 12,5 mm visé ; 12,0–12,8 mm obtenus ; ±2 px d'erreur de rayon → 11,5–13,5 mm | Conforme | — |
| Positions/tailles identiques d'un contrôle à l'autre | géométrie figée par installation, seul le centre est redétecté ; invalidée si matrice/pixel changent | Conforme (bonne conception) | P-06 (réinitialisation) |
| Bruit = écart-type d'une ROI, « sur l'ensemble des 10 coupes » avec les ROI du SPB | écart-type de la ROI centrale 40 % sur la seule coupe UH | **Écart** | P-02 |
| Critère bruit MIN/MAX(0,2 ; 10 %) | `noise_bounds` exact, bornes incluses | Conforme | — |
| SPB sur 10 coupes centrées sur la coupe centrale | défaut `(N−10)//2 … +9` (centre 9,5 pour N = 20 ou 21) | Conforme | — |
| Fréquence moyenne, tolérance ±10 % | `0,9·f_ref ≤ f ≤ 1,1·f_ref` | Conforme (formule) ; **valeur de f biaisée dans certains cas** | P-01, P-03, P-04 |
| Logiciel SPB validé sur la banque ANSM (résultats « équivalents ») | tests pytest, tolérance 10 %, écart +2,5…+6,4 % / −3 % | **Écart méthodologique** (tolérance trop lâche, biais inexpliqué, pas de validation absolue) | P-05 |
| Position/taille des ROI SPB au registre | tableau des ROI (px et mm) dans le PDF, export JSON | Conforme | — |
| Images axiales DICOM non compressées | transfert syntaxes compressées → erreur signalée ; Enhanced CT multi-frame rejeté | Conforme (limitation) | P-16 |
| Conversion UH | `float64·slope + intercept` ; PixelRepresentation via pydicom | Conforme | P-16 (PixelPadding, pixels non carrés) |
| ≥ 10 coupes | `ValueError` si < 10 en appel par défaut ; avertissement PDF si plage < 10 | Conforme | — |

---

## 5. Constats

### [P-01] Fréquence moyenne calculée sur un polynôme de degré 11 ajusté jusqu'à 1,375 × Nyquist : biais de +10 à +50 % dès que le pixel est fin ou le noyau mou
- **Sévérité** : Critique (change un résultat et peut changer un statut de conformité)
- **Fichier** : `src/cq_tdm/core/nps.py:373-406` (`fit_nps_polynomial`), `src/cq_tdm/core/nps.py:656-665` (`analyze_nps`, calcul de `mean_frequency`)
- **Attendu** : f_moy = ∫₀^{f_max} f·NPS₁D(f) df / ∫₀^{f_max} NPS₁D(f) df, estimée sur une courbe qui reproduit fidèlement le spectre mesuré. Un polynôme de degré 11 ne peut pas être à la fois une bosse étroite et nul sur un long intervalle : lorsque le support utile du spectre (≈ 0–0,8 mm⁻¹ pour un noyau standard) ne couvre qu'une fraction du domaine d'ajustement [0 ; 1,375·f_Nyq] (= 0–2,75 mm⁻¹ à 0,25 mm/px, 0–1,96 mm⁻¹ à 0,35 mm/px), l'ajustement oscille, est tronqué à 0 (`np.maximum`) et laisse des lobes positifs parasites à haute fréquence qui pèsent lourd dans ∫ f·NPS df. Pour mémoire, une fraction p de puissance parasite placée en f_t déplace f_moy de ≈ p·(f_t − f_moy) : 1 % à 1,9 mm⁻¹ pour f_moy = 0,27 → +6 %.
- **Implémenté** :
  ```python
  coeffs = np.polyfit(frequencies, nps_values, degree)   # nps.py:401, degré 11, f ∈ [0, 1,375 f_Nyq]
  nps_fit = np.maximum(nps_fit, 0)                         # nps.py:405
  total_power = _trapezoid(nps_radial_fit, freq_radial)    # nps.py:661
  mean_frequency = _trapezoid(freq_radial * nps_radial_fit, freq_radial) / total_power   # nps.py:663
  ```
- **Effet** : bruit synthétique de SPB connu S(f) = f·exp(−(f/f₀)²) (forme FBP, f_moy analytique = √π/2·f₀), 10 coupes × 8 ROI :

  | pixel (mm) | f₀ | f_moy brute | f_moy code (fit) | fit vs brut | configuration réelle correspondante |
  |---|---|---|---|---|---|
  | 0,50 | 0,30 | 0,2720 | 0,2728 | **+0,3 %** | séries ANSM (0,47–0,67 mm) : défaut invisible |
  | 0,40 | 0,20 | 0,1892 | 0,2101 | **+11 %** | tête, DFOV 205 mm, noyau mou (f_moy ≈ 0,18) |
  | 0,35 | 0,20 | 0,1923 | 0,2350 | **+22 %** | tête, DFOV 180 mm |
  | 0,30 | 0,30 | 0,2805 | 0,2934 | **+4,6 %** | DFOV 154 mm |
  | 0,25 | 0,30 (ROI 128) | 0,2716 | 0,2983 | **+9,8 %** | matrice 1024, DFOV 256 mm |
  | 0,25 | 0,20 (ROI 128) | 0,1852 | 0,2388 | **+29 %** | matrice 1024, noyau mou |
  | 0,20 | 0,30 (ROI 128) | 0,2744 | 0,3456 | **+26 %** | matrice 1024, DFOV 205 mm |

  Le biais dépend de la forme du spectre et de la réalisation du bruit (reproductibilité à 0,25 mm/px : 1,4 % sur le fit contre 0,5 % sur le brut, §6 test III) : il ne se compense donc **pas** entre contrôle de référence et contrôle périodique, et une dérive réelle de texture est amplifiée ou masquée de façon imprévisible. Une installation « tête » avec matrice 512 et DFOV ≤ 200 mm, ou toute matrice 1024, est concernée. Le PDF ne trace que le spectre brut (`pdf_report.py:138`) : l'utilisateur ne peut pas voir que le fit a échoué.
- **Preuve** : `t3_fit_sweep.py` section I et « Alternatives » : pour dx = 0,25 / f₀ = 0,3 / ROI 128, fit sur [0 ; 1,375·f_Nyq] → 0,2983 ; fit restreint à [0 ; f_Nyq] → 0,2715 ; brut → 0,2716 ; part de puissance du fit au-delà de Nyquist 0,78 % alors que le brut y est à 0,00 %. Degrés 9–17 équivalents (±0,3 %) quand le domaine est adapté (`t1` C), ce qui montre que le degré n'est pas le problème mais le domaine.
- **Correction proposée** : (a) calculer f_moy sur le **spectre brut** (ou sur un fit restreint au support utile : [0 ; f_Nyq] ou jusqu'à la dernière fréquence où NPS > 1 % du max), et n'utiliser le polynôme que pour l'affichage ; (b) à défaut, ajuster sur [0 ; f_Nyq] avec `numpy.polynomial.Polynomial.fit` (domaine mis à l'échelle) et intégrer sur ce même domaine ; (c) tracer le fit dans le PDF et émettre un avertissement si |f_moy(fit) − f_moy(brut)| / f_moy(brut) > 2 %. Il faut au préalable déterminer la règle exacte d'iQMetrix (brut ou fit ? borne d'intégration ?) à partir de `NPS1D.csv` (colonnes raw/fit) et de `Average Frequency` de `NPS_Results.txt` (voir plan, étape 1) pour rester « équivalent » sur la banque ANSM. Tout changement ici modifie les valeurs de f_moy → nouvelle validation ANSM et version majeure.
- **Confiance** : Confirmé (numériquement) ; la règle iQMetrix reste À vérifier.

### [P-02] Magnitude du bruit : écart-type de la ROI centrale (40 % D) sur une seule coupe, au lieu des ROI du SPB sur les 10 coupes
- **Sévérité** : Critique (change la valeur rapportée et le statut de stabilité si B_ref provient d'une autre méthode)
- **Fichier** : `src/cq_tdm/core/water_phantom.py:269` (`analyze_water_phantom`, `noise = central.std_hu`), `src/cq_tdm/gui/main_window.py:2296,2626` (`noise=self._current_results.noise` enregistré dans `QCRun`), `src/cq_tdm/reports/pdf_report.py:1259` (« Écart-type central »)
- **Attendu** : décision A-9.1.7.2, « pour l'analyse du SPB et du bruit : sélectionner 10 coupes… placer des ROI en nombre et de taille suffisants… déterminer le SPB **et le bruit** sur l'ensemble des 10 coupes ». A-6.2 : bruit = écart-type des nombres CT des pixels d'une ROI. iQMetrix-CT rapporte dans `NPS_Results.txt` « Noise (HU) » (S1 : 7,629) et « Square Root AUC NPS 2D » (7,554), c'est-à-dire l'écart-type des ROI du SPB sur les coupes analysées. B_ref, fixée au CQE initial par l'organisme externe, est donc selon toute vraisemblance définie ainsi.
- **Implémenté** : `noise = central.std_hu` (ROI Ø 40 % D, ≈ 20 000 px, coupe UH unique) ; les écarts-types des 80 ROI SPB sont pourtant déjà calculés (`nps.py:598-599`, `roi_stats`) mais ne servent qu'aux avertissements.
- **Effet** : (1) différence de définition : la ROI 40 % intègre les non-uniformités basse fréquence (cupping, anneaux, ombre de table) que les ROI de 64 px n'intègrent pas ; (2) fluctuation coupe à coupe de 1,1 % (bruit blanc) à 2,3 % (bruit corrélé) sur la coupe unique, contre ≈ 0,4 % sur 10 coupes ; (3) le libellé « Écart-type central » du PDF ne correspond pas à la grandeur du registre. Avec une tolérance de max(0,2 UH ; 10 %), 2–5 % d'écart de définition suffisent à faire basculer un statut près de la borne ; à faible bruit (σ ≈ 2 UH, tolérance 0,2 UH = 10 %) le risque est le même.
- **Preuve** : `t2_water_status.py` section C : σ vrai 8,0 ; ROI centrale coupe 5 = 8,067 ; sur 10 coupes min 7,979 / max 8,067 (étendue 1,1 %) ; bruit corrélé : étendue 2,3 % ; moyenne des 80 ROI SPB = 8,004 (±0,10 entre ROI) ; √AUC(NPS₂D) = 7,967 (−0,5 % : détrending). Le cupping parabolique de 5 UH n'ajoute que +0,04 % sur σ (effet (2) dominant pour ce cas ; les anneaux/bandes réels font plus).
- **Correction proposée** : dans `analyze_nps`, calculer `noise = mean(std_i)` (ou la moyenne quadratique √mean(std_i²), à choisir et documenter — l'écart entre les deux est < 0,01 % ici) sur les ROI SPB valides des 10 coupes et l'exposer dans `NPSResult.noise` ; enregistrer cette valeur dans `QCRun.noise` et l'utiliser pour `noise_status` ; garder l'écart-type de la ROI centrale à titre d'information (« écart-type ROI centrale, coupe UH »). Changement de résultat → version majeure et note dans l'historique (les anciennes références ne sont plus comparables : prévoir une migration ou un marquage).
- **Confiance** : Confirmé (implémentation) ; Probable (définition de B_ref par les organismes externes).

### [P-03] Axe de fréquence radial faux de +0,6 à +1,85 % pour les ROI de 40, 56, 72, 88, 104 et 120 px
- **Sévérité** : Majeur (biais mesurable sur la valeur absolue de f_moy ; se compense entre deux contrôles seulement si la géométrie reste figée)
- **Fichier** : `src/cq_tdm/core/nps.py:327-335` (`radial_average`, `freq_max = nyquist*1.375`, `num_bins = int(fft_size//2*1.375)+1`, `freq_r = np.linspace(0, freq_max, num_bins)`) et `:344` (`r_pixels = int(fft_size//2*1.375)`)
- **Attendu** : les profils sont échantillonnés à r_k = k pixels (k = 0 … r_pixels) ; la fréquence du k-ième échantillon est exactement f_k = k/(N·Δx). L'étiquette `linspace(0, 1,375·f_Nyq, r_pixels+1)` a pour pas 1,375·f_Nyq/int(0,6875·N) = (0,6875·N/int(0,6875·N))·1/(N·Δx), égal à 1/(N·Δx) seulement si 0,6875·N est entier (N multiple de 16).
- **Implémenté** : étiquettes `freq_r` indépendantes de `r_values` ; `nps_roi_size = round(0,15·D/8)·8` (`roi_geometry.py:86`) produit 40, 56, 72, 88, 104, 120 px pour des diamètres réels (ex. fantôme 20 cm, DFOV 280 mm → 366 px → 56 px).
- **Effet** : f_moy multipliée par 1,0185 (40 px), 1,0132 (56), 1,0102 (72), 1,0083 (88), 1,0070 (104), 1,0061 (120). Sur la série synthétique f₀ = 0,3, ROI 56 : +4,4 % vs analytique contre +2,3 % en 64 px (la différence contient aussi la fuite spectrale, P-06). Si l'exploitant réinitialise la géométrie ou si une référence a été établie avec un autre logiciel (sans ce biais), l'erreur apparaît directement dans le rapport f_i/f_ref.
- **Preuve** : `t1_nps_core.py` section D (tableau pas code / 1/(N·Δx)) et mesures ROI 40 → 128 px.
- **Correction proposée** :
  ```python
  r_pixels = int(fft_size // 2 * 1.375)
  num_bins = r_pixels + 1
  freq_r = np.arange(num_bins) / (fft_size * pixel_size_mm)   # exact pour tout N
  ```
  (identique à l'actuel pour N = 64/128 : aucune incidence sur la validation ANSM).
- **Confiance** : Confirmé.

### [P-04] Au-delà de Nyquist, les échantillons hors matrice valent 0 et sont moyennés : sous-estimation de f_moy pour les noyaux durs
- **Sévérité** : Majeur (biais de −4,5 à −7,6 % par rapport à une moyenne sans zéros ; convention à aligner sur la référence)
- **Fichier** : `src/cq_tdm/core/nps.py:366` (`map_coordinates(..., mode='constant', cval=0)`), `:344-348` (profils jusqu'à 1,375·N/2 px, 37 angles)
- **Attendu** : pour r > N/2 px, seuls les angles proches des diagonales ont des données (les coins du carré de fréquences) ; une moyenne radiale cohérente ne doit porter que sur les échantillons existants (ou se limiter à f_Nyq). Avec des zéros, NPS₁D(f > f_Nyq) décroît artificiellement : pour un spectre plat de 25, la courbe code vaut 13,5 → 0 entre 1,0 et 1,375·f_Nyq alors que la valeur réelle dans les coins est 25.
- **Implémenté** : `cval=0` puis `np.mean(radial_profiles)`.
- **Effet** : négligeable quand le spectre est nul avant Nyquist (noyaux standard : +0,01 %), mais pour un noyau dur ayant 8–21 % de sa puissance 1D au-delà de Nyquist (f₀ = 0,45–0,8 pour Δx = 0,5–0,7 mm) : f_moy code = 0,4807 / 0,5948 / 0,3730 contre 0,5097 / 0,6436 / 0,3904 en moyenne sans zéros (−5,7 %, −7,6 %, −4,5 %). La figure `docs/nps_validation_combined.png` (série 4, noyau plus dur) montre exactement ce comportement : la courbe ANSM reste à 3–5 HU²·mm² entre 0,9 et 1,2 mm⁻¹ alors que CQ TDM tombe à 0, et c'est la seule série où CQ TDM est **sous** la référence (−2,95 %). Deux installations contrôlées avec des noyaux différents ne sont donc pas comparées à iQMetrix avec le même biais.
- **Preuve** : `t3b_nanmean.py` (II-bis) et `t1_nps_core.py` E (spectre plat).
- **Correction proposée** : `cval=np.nan` + `np.nanmean(..., axis=0)` et suppression des bins entièrement NaN (f > ≈ 1,28·f_Nyq), **ou** limiter la moyenne radiale et l'intégration à [0 ; f_Nyq] (physiquement plus propre, mais à confronter à la convention iQMetrix sur la série 4 : comparer bin à bin la colonne raw de `NPS1D.csv` au-delà de Nyquist).
- **Confiance** : Confirmé (effet) ; Probable (explication de la série 4).

### [P-05] Validation contre la banque ANSM : tolérance d'acceptation égale à la tolérance réglementaire, biais systématique non expliqué, aucune validation des grandeurs absolues
- **Sévérité** : Majeur (non-conformité méthodologique au guide §4 « résultats équivalents aux résultats de référence »)
- **Fichier** : `tests/test_nps_validation.py:474,550` (`assert err <= 10`), `:597-617` (forme : normalisation par le max puis `correlation > 0.7`), `:442,507,582,676,843` (`roi_size=64` imposé), `tests/conftest.py:30-51` (fréquences de référence) ; `docs/nps_validation_combined.png`
- **Attendu** : un logiciel « équivalent » doit reproduire la référence à une fraction de la tolérance du test (de l'ordre de 1–3 % sur f_moy, comme la reproductibilité statistique de 0,3 %), et sa normalisation doit être vérifiée en absolu (« Peak Intensity (HU²·mm²) », « Square Root AUC NPS 2D (HU) », « Noise (HU) » sont fournis par l'ANSM pour cela).
- **Implémenté** : seuil 10 % sur f_moy (= tolérance de stabilité) ; test de forme normalisé (insensible à un facteur d'échelle, corrélation > 0,7 quasi non discriminante : deux bosses quelconques dépassent 0,9) ; `ReferenceNPSResults.peak_intensity`, `.noise`, `.sqrt_auc_nps_2d` sont parsés mais jamais comparés.
- **Effet** : résultats v0.7.0 (ROI automatiques) : S1 +4,67 %, S2 +2,49 %, S3a +5,01 %, S3b +6,43 %, S4 −2,95 % (moyenne des écarts absolus 4,3 %). Le biais est **systématiquement positif** sur 4 séries et dépend de la série : il consomme jusqu'à 2/3 de la tolérance de stabilité et n'est pas constant, donc ne se compense pas entre contrôles si la référence vient d'iQMetrix. Sur les courbes, le spectre CQ TDM apparaît décalé vers la droite d'environ un demi-bin (≈ 0,015 mm⁻¹) : un décalage d'un demi-bin de l'axe radial donne exactement +5 à +9,5 % sur f_moy (§6 test G), ce qui est l'ordre de grandeur observé. Hypothèses à tester sur les données locales, par ordre de vraisemblance : (i) convention de centrage/binning radial d'iQMetrix différente (d'un demi-bin) ; (ii) détrending différent (ordre 2 → +1,8 % par rapport à une simple soustraction de moyenne, §6 test F) ; (iii) PixelSpacing DICOM ≠ `pixel_size`/DFOV du JSON ANSM (un écart de 4,7 % sur le pixel donne 4,7 % sur f) ; (iv) règle de calcul de f_av (brut/fit, bornes). Un calcul avec les ROI de référence (test `test_nps_with_reference_rois`) donne-t-il le même biais ? (les figures ne montrent que les ROI automatiques).
- **Preuve** : figures `docs/*.png` ; `t1` G (demi-bin) ; `t1` F (détrending) ; lecture des tests.
- **Correction proposée** : (1) resserrer l'acceptation à ≤ 2 % sur f_moy (ou documenter une correction) ; (2) ajouter : RMS relatif de la colonne raw de `NPS1D.csv` vs `nps_radial` bin à bin (< 5 % sur 0,05–0,8 mm⁻¹), `peak_intensity` ±5 %, `sqrt_auc_nps_2d` ±2 % (√(Σ NPS₂D·Δf²)), `noise` ±2 % (après P-02) ; (3) tracer raw ANSM vs raw CQ TDM et estimer le décalage par corrélation croisée ; (4) assertion `abs(series.images[0].pixel_size_mm − ref_config.pixel_size) < 1e-3` ; (5) tester aussi la taille de ROI issue de la règle (sans `roi_size=64`).
- **Confiance** : Confirmé (méthode de validation) ; À vérifier (origine du biais).

### [P-06] f_moy dépend de la taille de ROI (fuite spectrale + détrending) et la règle « 15 % / 8 px » bascule entre 56 et 64 px pour un fantôme de 400 px
- **Sévérité** : Majeur (biais de +2,3 % à 64 px, +4,4 % à 56 px, +8,1 % à 40 px par rapport à la valeur analytique ; saut de ≈ 2 % si la géométrie change)
- **Fichier** : `src/cq_tdm/core/roi_geometry.py:86-87` (`nps_roi_size`), `:29-32` (constantes), `src/cq_tdm/core/nps.py:230-257` (`detrend_roi`)
- **Attendu** : le périodogramme d'une ROI de N px convolue le vrai SPB par un noyau sinc² de largeur ≈ 1/(N·Δx) et le détrending d'ordre 2 supprime les premiers bins (bin 1 : −59 %, bin 2 : −7 %, §6 test A) : estimateur biaisé vers les hautes fréquences, d'autant plus que la ROI est petite en mm. C'est inhérent à la méthode (Dolly et al. 2016 ; Friedman et al. 2013 ; IEC 62220-1 recommande des ROI ≥ 128 px pour cette raison) ; il faut donc que la ROI soit strictement identique d'un contrôle à l'autre — ce que fait le gel de la géométrie — et que la règle de taille ne soit pas instable.
- **Implémenté** : `int(round(D·0,15/8))·8` ; pour D = 400 px (fantôme 20 cm à 0,5 mm/px, DFOV 256 mm), 0,15·D/8 = 7,5 : D détecté 399,6 → 56 px, D détecté 400,1 → 64 px. Observé dans `t1` B : la série synthétique de rayon 200 px a reçu 56 px. `test_roi_geometry.py:135` admet d'ailleurs « 56 ou 64 ». Le commentaire « a ±1 px diameter jitter cannot change the size » (`roi_geometry.py:30`) est faux au voisinage des demi-entiers.
- **Effet** : f_moy ROI 56 = 0,2776 vs ROI 64 = 0,2719 (+2,1 %, dont +1,3 % d'axe P-03) ; ROI 128 = 0,2676. Une réinitialisation de la géométrie (`main_window.py:618-635`) ou une nouvelle installation avec un diamètre détecté différent de ±0,5 px change f_moy de 1–2 % sans que la machine ait changé ; la validation ANSM impose 64 px et ne couvre pas la règle.
- **Preuve** : `t1` D ; `t1` B (ROI = 56 px) ; `t1` F (détrending +1,8 %).
- **Correction proposée** : (a) choisir la taille en **mm** (ex. 30 mm arrondis au multiple de 8 px le plus proche, mais à partir du pixel et non du diamètre détecté), ce qui rend la taille indépendante de la détection ; (b) imposer N multiple de 16 (32, 48, 64, 80, 96, 112, 128) pour supprimer aussi P-03 ; (c) interdire ou avertir fortement lors d'une réinitialisation de géométrie quand une référence f_ref existe (nouvelle référence obligatoire, décision A-8).
- **Confiance** : Confirmé.

### [P-07] Validation ANSM : coupes et biais avec ROI de référence non montrés ; `find_slice_range_by_location` peut utiliser jusqu'à ≈ 20 coupes
- **Sévérité** : Mineur (statistique) — regroupé avec P-05 pour l'action
- **Fichier** : `tests/test_nps_validation.py:124-165`
- **Attendu** : 10 coupes centrées (décision) ; comparaison à iQMetrix sur les mêmes coupes (section start/stop du JSON).
- **Implémenté** : plage par positions z du JSON ; repli sur ≈ 20 coupes centrales si pas de recouvrement.
- **Effet** : statistique seulement (sd(f_moy) ≈ 0,3 % pour 80 ROI).
- **Preuve** : lecture ; `t3` III.
- **Correction proposée** : journaliser le nombre de coupes réellement utilisées par série dans la figure ; vérifier qu'il vaut celui d'iQMetrix.
- **Confiance** : Confirmé.

### [P-08] Bornes de statut : 25 UH exclu du NCG, et incohérences entre valeur affichée (arrondie) et statut calculé
- **Sévérité** : Mineur (cas limites, mais un rapport peut afficher « +25,0 HU — NON CONFORME » là où la décision dit NCG, ou « +7,0 HU — NON CONFORME »)
- **Fichier** : `src/cq_tdm/core/qc_history.py:146-148` (`water_ct_status`), `src/cq_tdm/core/water_phantom.py:273-274`, `:278`, `src/cq_tdm/core/qc_history.py:155-166` (`_EPS = 1e-9`), `src/cq_tdm/reports/pdf_report.py:1170,1231,1259,1271,1315` (arrondis 1/2/3 décimales)
- **Attendu** : décision 9.1.7.3 : NCG si CT ≤ −25 ou 25 ≤ CT (borne incluse) ; conforme si −7 ≤ CT ≤ 7. Le lecteur du rapport juge sur la valeur imprimée.
- **Implémenté** : `v <= 25 → NC`, `> 25 → NCG` ; comparaison en pleine précision puis affichage arrondi.
- **Effet** (`t2` D) : 25,000 → NC (décision : NCG) ; 24,96 → affiché « +25,0 » et NC ; 7,04 → affiché « +7,0 » et NC ; bruit ref 3,0, écart +0,304 → affiché « +0,30 » (borne +0,30) et NC ; SPB +10,00004 % → affiché « +10,0 % » et NC. `_EPS = 1e-9` est sans effet pratique.
- **Preuve** : `t2_water_status.py` D.
- **Correction proposée** : `NCG if v >= 25` ; pour la cohérence affichage/statut, soit comparer la valeur arrondie à la résolution affichée (règle à documenter), soit afficher une décimale de plus que la résolution du critère ; unifier `analyze_water_phantom` (booléens) et `qc_history` (une seule fonction).
- **Confiance** : Confirmé.

### [P-09] Pondération angulaire : 37 angles de 0° à 360°, 0° compté deux fois ; échantillonnage angulaire grossier
- **Sévérité** : Mineur
- **Fichier** : `src/cq_tdm/core/nps.py:348` (`theta_deg = np.arange(0, 361, 10)`)
- **Attendu** : angles équirépartis sans doublon (0…350) ou moyenne de tous les pixels par couronne.
- **Implémenté** : 37 profils dont deux identiques (θ = 0 et 360) → poids 2/37 pour la direction +x.
- **Effet** : +0,02 % sur f_moy pour un SPB isotrope ; +0,9 % pour un SPB fortement anisotrope (bandes passantes 0,4/0,25) avec des erreurs locales jusqu'à 11 % sur NPS₁D (3,6 % près de Nyquist même en isotrope).
- **Preuve** : `t1` E.
- **Correction proposée** : `np.arange(0, 360, 10)` si l'on veut rester fidèle à iQMetrix (à vérifier : si iQMetrix fait 0:10:360, garder tel quel et documenter), ou moyenne par couronnes sur tous les pixels (plus précise).
- **Confiance** : Confirmé.

### [P-10] `total_noise_power` et `average_nps` : grandeurs sans signification physique et unité fausse
- **Sévérité** : Mineur (non affichées dans la GUI ni le PDF ; `format_nps_results_text` n'est appelé nulle part)
- **Fichier** : `src/cq_tdm/core/nps.py:668-670`, `:720-721` (« Puissance totale: … HU² »)
- **Attendu** : la variance est ∫∫ NPS₂D dfx dfy = 2π ∫ f·NPS₁D(f) df (coordonnées polaires), en HU² ; ∫ NPS₁D df est en HU²·mm.
- **Implémenté** : `np.sum(nps_radial)·df` (inclut la queue > Nyquist) étiqueté HU² ; `average_nps = mean(nps_radial)` sur 45 bins dont 13 au-delà de Nyquist.
- **Effet** : bruit blanc σ = 10 : code 26,5 « HU² » ; intégrale polaire 0→Nyq = 77,8 ≈ π/4·σ² (disque inscrit) ; Σ NPS₂D·Δf² = 99,9 = σ².
- **Preuve** : `t1` A.
- **Correction proposée** : exposer `sqrt_auc_nps_2d = sqrt(Σ NPS₂D·Δf²)` (grandeur iQMetrix, HU) et supprimer/renommer les deux autres.
- **Confiance** : Confirmé.

### [P-11] Valeurs de référence relues depuis le champ texte arrondi (σ 2 décimales, f 3 décimales)
- **Sévérité** : Mineur
- **Fichier** : `src/cq_tdm/gui/main_window.py:540-544` (`reference_noise = parse_float_fr(text)`), `:2687` (`setText(format_fr(noise, 2))`)
- **Attendu** : B_ref et f_ref stockées en pleine précision (telles qu'enregistrées dans `QCRun.noise`/`nps_freq`).
- **Implémenté** : la référence choisie depuis un contrôle est d'abord affectée en pleine précision (`:2691`) puis, à la sauvegarde, remplacée par la relecture du texte à 2/3 décimales.
- **Effet** : bornes décalées de ≤ 0,005 UH / ≤ 0,0005 mm⁻¹ (ex. 2,0049 → 2,00) ; incohérence entre `run.ref_noise` et la valeur réellement utilisée ; négligeable devant 0,2 UH / 10 %.
- **Preuve** : `t2` D (fin).
- **Correction proposée** : ne relire le champ que s'il a été modifié par l'utilisateur ; sinon conserver la valeur numérique.
- **Confiance** : Confirmé.

### [P-12] ROI SPB de côté impair : toutes les ROI sont rejetées (`ValueError`)
- **Sévérité** : Mineur (robustesse : import JSON ou géométrie figée modifiée)
- **Fichier** : `src/cq_tdm/core/nps.py:188-199` (`extract_roi_for_nps`, `half_size = roi_size // 2`, tranche `[c − half, c + half)`), `:587-590`
- **Attendu** : extraction de `roi_size` pixels quelle que soit la parité.
- **Implémenté** : 2·(N//2) = N − 1 pixels pour N impair → `shape != roi_size` → ROI ignorée → « No valid ROIs could be processed ».
- **Effet** : plantage sur un JSON avec `side_square` impair (`t1` H : ROI 63 → ValueError).
- **Correction proposée** : `row_end = row_start + roi_size` ; même chose pour les colonnes.
- **Confiance** : Confirmé.

### [P-13] Détection du fantôme : échec silencieux à très fort bruit et sur paroi peu dense
- **Sévérité** : Mineur (la GUI affiche les ROI, l'utilisateur peut voir l'erreur ; mais aucun indicateur de qualité n'est consigné)
- **Fichier** : `src/cq_tdm/core/dicom_loader.py:387-392` (seuils 100/50 UH, 3 échantillons), `:414-425`, `:531-568`
- **Attendu** : détection du bord interne pour σ jusqu'à ≈ 50 UH (coupes fines, faible dose) et parois de 40 à 150 UH ; signal explicite quand la détection est douteuse.
- **Implémenté** : seuils fixes ; `num_edge_points` existe mais n'est pas exploité pour avertir.
- **Effet** (`t2` E) : σ = 40 UH → rayon 199,6 (OK, 57 points) ; σ = 60 UH → rayon 72,6 px au lieu de 200 avec 56 points « valides » (géométrie fausse, ROI mal placées) ; paroi à 40 UH → rayon 206,2 (bord externe) → ROI périphériques à 9,5 mm de la vraie paroi (< 10 mm) ; paroi −100 UH (polyéthylène) OK.
- **Correction proposée** : seuil adaptatif (ex. 50 UH ou 3·σ_eau mesuré au centre, le plus grand) ; avertissement si `num_edge_points < 60` ou si le rayon s'écarte de plus de 2 % de la géométrie figée ; refuser de figer une géométrie issue d'un repli (`num_edge_points == 0`).
- **Confiance** : Confirmé.

### [P-14] Bins de basse fréquence déprimés par le détrending d'ordre 2 (méthode à documenter)
- **Sévérité** : Mineur (conforme à la pratique iQMetrix/Solomon, mais c'est un choix qui pèse +1,8 % sur f_moy et explique une partie possible de l'écart ANSM)
- **Fichier** : `src/cq_tdm/core/nps.py:230-257` (`detrend_roi`)
- **Attendu** : soustraction d'un polynôme 2D d'ordre 2 par ROI (Greffier et al. 2022 ; Solomon/Samei) — c'est ce qui est fait. Parseval : perte de variance 6/4096 = 0,15 %.
- **Implémenté** : lstsq sur 6 monômes, coordonnées en pixels non centrées (conditionnement acceptable pour N ≤ 128).
- **Effet** (`t1` A, F) : bruit blanc : bin radial 1 à 41 % de sa valeur, bin 2 à 93 % ; variance −0,12 % ; bruit CT-like : f_moy brut 0,2772 (ordre 2) vs 0,2722 (moyenne seule) : +1,8 % ; puissance 1D −2,2 %.
- **Correction proposée** : aucune (conserver), mais documenter dans le README et dans le PDF (« SPB : soustraction d'un polynôme 2D d'ordre 2 par ROI, méthode iQMetrix-CT ») et centrer/normaliser les coordonnées dans la matrice de conception pour les ROI de 128 px (hygiène numérique).
- **Confiance** : Confirmé.

### [P-15] Graphe de tendance : la bande de tolérance est tracée autour des références actuelles alors que chaque point est jugé avec ses références figées
- **Sévérité** : Mineur (présentation)
- **Fichier** : `src/cq_tdm/core/trend_chart.py:60-75` (`evaluate_run(r)` vs `tolerance_band(metric, ref_noise, ref_nps)`)
- **Attendu** : après un changement de référence (décision A-8), les anciens points gardent leur verdict (bien) ; la bande devrait être segmentée par période de référence ou légendée « référence actuelle ».
- **Implémenté** : une seule bande ; points colorés avec leur statut figé → un point peut être vert hors bande (ou l'inverse).
- **Effet** : lecture ambiguë ; aucune incidence sur les statuts.
- **Correction proposée** : tracer la bande par segments (par `ref_noise`/`ref_nps_freq` des runs) ou l'indiquer dans la légende.
- **Confiance** : Confirmé.

### [P-16] Lecture DICOM : hypothèses silencieuses (pixels carrés, PixelSpacing absent → 1 mm, PixelPaddingValue, Enhanced CT)
- **Sévérité** : Mineur
- **Fichier** : `src/cq_tdm/core/dicom_loader.py:77-80` (`pixel_size_mm = pixel_spacing[0]`), `:271-275` (`PixelSpacing` par défaut `[1.0, 1.0]`), `:214-221` (`_apply_modality_lut`), `:245-248` (multi-frame rejeté)
- **Attendu** : PixelSpacing = [espacement ligne, espacement colonne] ; un fichier sans PixelSpacing doit être refusé (sinon toutes les fréquences et les tailles de ROI en mm sont fausses) ; PixelPaddingValue (0028,0120) marque les pixels hors reconstruction (valeur extrême après rescale) ; `RescaleType` devrait valoir « HU ». L'overflow int16 est évité par le passage en float64 (correct).
- **Implémenté** : repli silencieux à 1,0 mm ; seul `pixel_spacing[0]` est utilisé pour les deux axes ; pas de masque de padding (sans conséquence sur les ROI, qui restent dans l'eau ; peut perturber la composante « eau » initiale si la valeur de padding tombe dans |HU| < 100, improbable).
- **Effet** : erreur grossière mais détectable (ROI visiblement fausses) ; `ROIGeometry.matches` compare les pixels à 0,5 % : un changement de DFOV de 0,4 % passe inaperçu (effet 0,4 % sur f, négligeable).
- **Correction proposée** : lever une erreur si PixelSpacing manque ; vérifier l'égalité des deux espacements à 0,1 % ; journaliser `RescaleType`.
- **Confiance** : Confirmé.

### [P-17] Coupe UH mémorisée par installation réutilisée sur une série de longueur différente
- **Sévérité** : Mineur (la décision impose la coupe **centrale**)
- **Fichier** : `src/cq_tdm/gui/main_window.py:1729-1732`, `src/cq_tdm/gui/image_viewer.py:1584-1594` (`_clamp_slice`)
- **Implémenté** : l'index de coupe sauvegardé (ex. 10 pour 21 coupes) est rappliqué tel quel (borné) à une série de 15 ou 40 coupes.
- **Effet** : mesure hors coupe centrale, sans avertissement.
- **Correction proposée** : mémoriser la position relative (ou la position z) et avertir si l'index rappelé n'est pas la coupe médiane ± 1.
- **Confiance** : Confirmé.

### [P-18] Nombre CT de l'eau : conforme ; réserves de précision (rayon tronqué, masque inclusif)
- **Sévérité** : Mineur (information)
- **Fichier** : `src/cq_tdm/core/roi_geometry.py:77`, `src/cq_tdm/core/water_phantom.py:197,227-228`
- **Implémenté** : r = int(0,4·D/2) (troncature : 39,6–40,0 % du diamètre) ; masque `distance <= r` (aire −0,13 % vs πr²) ; `np.std(ddof=0)` (facteur 1,00003 pour 20 000 px).
- **Effet** : `t2` B : moyenne mesurée 2,458 vs 2,390 attendu ± 0,057 (1,2 σ, cohérent) ; uniformité 2,72 vs 2,68 attendu ; σ 8,0001 vs 8,0.
- **Correction proposée** : aucune nécessaire ; éventuellement `round` au lieu de `int`.
- **Confiance** : Confirmé.

### [P-19] Rapport PDF : seul le SPB brut est tracé alors que f_moy est calculée sur le fit
- **Sévérité** : Mineur (lié à P-01)
- **Fichier** : `src/cq_tdm/reports/pdf_report.py:135-141`
- **Correction proposée** : tracer les deux courbes (brut en trait fin, fit en trait épais), limiter l'axe à f_Nyq ou marquer f_Nyq, et indiquer f_moy brute et f_moy fit.
- **Confiance** : Confirmé.

### [P-20] Conditionnement de `np.polyfit` à l'ordre 11 : acceptable en double précision (pas de bug), à sécuriser
- **Sévérité** : Suggestion
- **Fichier** : `src/cq_tdm/core/nps.py:401`
- **Preuve** : cond(Vandermonde brute) 1e8–6e9 selon le pixel, mais `np.polyfit` normalise les colonnes → cond 7,6e7 constant ; écart avec `Polynomial.fit` (domaine [−1, 1], cond 6,9e3) ≤ 1e-9 ; aucun `RankWarning` sur 0,2–1,0 mm. Zone négative avant clipping : min −0,8 pour un max de 164, aire −0,04 %.
- **Correction proposée** : `Polynomial.fit(f, nps, 11)` (domaine mis à l'échelle) et capturer `np.exceptions.RankWarning` ; le vrai problème est le domaine (P-01).
- **Confiance** : Confirmé.

### [P-21] Fréquence moyenne : définition à expliciter dans le rapport (1D radial, bornes, brut/fit)
- **Sévérité** : Suggestion
- **Fichier** : `src/cq_tdm/reports/pdf_report.py:1297-1345`, README §SPB
- **Attendu** : la décision ne définit pas f_moy ; l'exploitant doit consigner la méthode (registre). Le centroïde 2D cartésien (0,349) diffère du centroïde 1D radial (0,278) de 25 % (`t1` B) : la convention doit être écrite.
- **Correction proposée** : ajouter dans le PDF une ligne « Méthode : SPB 2D (Δx²/N²·|FFT|², polynôme 2D d'ordre 2 soustrait), moyenne radiale 1D, f_moy = ∫f·SPB/∫SPB sur [0 ; …], ajustement polynomial de degré 11 (affichage) ».
- **Confiance** : Confirmé.

### [P-22] Mode spectral : test de stabilité du nombre CT non implémenté
- **Sévérité** : Suggestion (hors périmètre déclaré du logiciel, mais exigé par la décision pour les installations utilisant le mode spectral)
- **Fichier** : `src/cq_tdm/core/qc_history.py:146-148` (pas de référence CT)
- **Correction proposée** : ajouter `ref_water_ct` et `water_ct_stability_status(Δ)` avec les mêmes seuils ±7/±25, sélectionnable par protocole.
- **Confiance** : Confirmé.

### [P-23] Sélection des coupes SPB par défaut pour un nombre impair de coupes
- **Sévérité** : Suggestion
- **Fichier** : `src/cq_tdm/core/nps.py:554`, `src/cq_tdm/gui/image_viewer.py:1388-1393`
- **Implémenté** : pour 21 coupes, SPB = indices 5–14 (centre 9,5) et coupe UH = 10 : la fenêtre est décalée d'une demi-coupe (inévitable avec 10 coupes), cohérent entre GUI et cœur.
- **Correction proposée** : aucune ; documenter.
- **Confiance** : Confirmé.

---

## 6. Résultats des vérifications numériques

Scripts : `scratchpad/physique/t1_nps_core.py`, `t2_water_status.py`, `t3_fit_sweep.py`, `t3b_nanmean.py`. Bruit synthétique généré indépendamment du code testé (filtrage spectral de bruit blanc sur une grille 512²/1024², fantôme d'eau synthétique avec paroi 120 UH et air).

### 6.1 Normalisation et Parseval (bruit blanc σ = 10 UH, Δx = 0,5 mm, 400 ROI 64 px)

| Test | Attendu | Obtenu | Écart | Verdict |
|---|---|---|---|---|
| NPS₂D plat = σ²·Δx² | 25,000 HU²·mm² | 25,004 (bins \|f\| > 0,15) ; 24,966 (tous bins) | +0,015 % / −0,14 % | OK |
| Parseval Σ NPS₂D·Δf² | 100 (σ²) | 99,86 | −0,12 % (détrending, attendu −0,15 %) | OK |
| Δf = 1/(N·Δx) | 0,03125 | 0,03125 | 0 | OK |
| NPS₂D(0,0) après détrending | ≈ 0 | 2e-25 | — | OK |
| Bins radiaux 1, 2, 3 (détrending) | 25 | 10,29 ; 23,17 ; 24,34 | −59 % ; −7 % ; −3 % | Méthode (P-14) |
| NPS₁D moyen 0 < f ≤ f_Nyq | 25 | 24,29 | −2,8 % (bins 1-3 déprimés) | Méthode |
| NPS₁D pour f_Nyq < f ≤ 1,375 f_Nyq | 25 (coins) | 13,6 → 0 | zéros hors matrice | P-04 |
| f_moy brute, plat 0→f_Nyq | 0,500 | 0,517 (0–f_Nyq) ; 0,573 (0–1,375 f_Nyq) | +3,4 % / +14,6 % | P-04, P-14 |
| `total_noise_power` | σ² = 100 (si HU²) | 26,5 | — | P-10 |
| 2π∫f·NPS₁D df (0→f_Nyq) | π/4·σ² = 78,5 | 77,8 | −0,9 % | OK (cohérent) |

### 6.2 Bruit CT-like S(f) = f·exp(−(f/f₀)²), f_moy analytique = √π/2·f₀ (`analyze_nps` complet, fantôme synthétique, 10 coupes)

| Cas | Attendu | Obtenu (code, fit) | Brut 0–f_Nyq | Écart code | Verdict |
|---|---|---|---|---|---|
| f₀ = 0,3, Δx = 0,5, ROI 56 | 0,2659 | 0,2776 | 0,2772 | +4,4 % (dont +1,3 % axe P-03, +1,8 % détrending, reste fuite spectrale) | Biais de méthode + P-03 |
| f₀ = 0,3, Δx = 0,5, ROI 64 | 0,2659 | 0,2719 | 0,2718 | +2,3 % | Biais de méthode (ROI 32 mm) |
| f₀ = 0,3, Δx = 0,5, ROI 128 | 0,2659 | 0,2676 | 0,2673 | +0,6 % | OK |
| f₀ = 0,2, Δx = 0,5, ROI 56 | 0,1772 | 0,1911 | 0,1880 | +7,8 % | P-01 (+1,6 % fit) + P-03 + méthode |
| f₀ = 0,4, Δx = 0,5, ROI 56 | 0,3545 | 0,3625 | 0,3606 | +2,3 % | — |
| f₀ = 0,3, Δx = 0,7, ROI 56 | 0,2659 | 0,2702 | 0,2683 | +1,6 % | — |
| f₀ = 0,3, Δx = 0,25, 1024, ROI 120 | 0,2659 | **0,3016** | 0,2731 | **+13,5 %** (brut +2,7 %) | **P-01** |
| Parseval 2D (tous cas) | 100 | 98,7–100,1 | ≤ 1,3 % | OK |
| Pic brut vs analytique f₀/√2 | 0,2121 | 0,2171 (Δf = 0,031) | < 1 bin | OK |
| Reproductibilité f_moy (12 réalisations) Δx = 0,5 | — | sd 0,27 % (fit et brut) | — | OK |
| Reproductibilité Δx = 0,25 | — | sd 1,44 % (fit) vs 0,53 % (brut) | fit ×3 plus bruité | P-01 |

### 6.3 Biais du fit selon le pixel (`t3`, ROI 64 px sauf mention) — extrait

| Δx (mm) | f₀ | f_brut | f_code | fit vs brut |
|---|---|---|---|---|
| 1,00 | 0,30 | 0,2525 | 0,2526 | +0,03 % |
| 0,60 | 0,30 | 0,2700 | 0,2703 | +0,10 % |
| 0,50 | 0,20 | 0,1854 | 0,1883 | +1,6 % |
| 0,40 | 0,20 | 0,1892 | 0,2101 | **+11,1 %** |
| 0,35 | 0,20 | 0,1923 | 0,2350 | **+22,2 %** |
| 0,30 | 0,30 | 0,2805 | 0,2934 | **+4,6 %** |
| 0,25 | 0,30 | 0,2859 | 0,3314 | **+15,9 %** |
| 0,25 | 0,30 (ROI 128) | 0,2716 | 0,2983 | **+9,8 %** |
| 0,20 | 0,30 (ROI 128) | 0,2744 | 0,3456 | **+25,9 %** |
| 0,25 | 0,30 (ROI 128), fit restreint à 0–f_Nyq | 0,2716 | 0,2715 | 0,0 % |

### 6.4 Axe de fréquence radial (`t1` D)

| N (px) | pas code (mm⁻¹, Δx = 0,5) | 1/(N·Δx) | erreur |
|---|---|---|---|
| 32, 48, 64, 80, 96, 112, 128 | = 1/(N·Δx) | — | 0 |
| 40 | 0,050926 | 0,050000 | **+1,85 %** |
| 56 | 0,036184 | 0,035714 | **+1,32 %** |
| 72 | 0,028061 | 0,027778 | +1,02 % |
| 88 | 0,022917 | 0,022727 | +0,83 % |
| 104 | 0,019366 | 0,019231 | +0,70 % |
| 120 | 0,016768 | 0,016667 | +0,61 % |

### 6.5 Au-delà de Nyquist : zéros (code) vs moyenne sans zéros vs 0–f_Nyq (`t3b`)

| f₀ / Δx | puissance 1D > f_Nyq | analytique 0–∞ | analytique 0–f_Nyq | code | NaN-mean (fit) | brut 0–f_Nyq | code vs NaN-mean |
|---|---|---|---|---|---|---|---|
| 0,6 / 0,573 (≈ S4) | 12,1 % | 0,5317 | 0,4609 | 0,4807 | 0,5097 | 0,4606 | **−5,7 %** |
| 0,8 / 0,5 | 21,0 % | 0,7090 | 0,5626 | 0,5948 | 0,6436 | 0,5613 | **−7,6 %** |
| 0,45 / 0,7 | 8,0 % | 0,3988 | 0,3604 | 0,3730 | 0,3904 | 0,3606 | **−4,5 %** |
| 0,3 / 0,5 | 0,0 % | 0,2659 | 0,2659 | 0,2728 | 0,2728 | 0,2718 | 0,0 % |

### 6.6 Moyenne radiale 37 angles (`t1` E)

| SPB | f_moy code | 3600 angles | écart | erreur locale max (f ≤ f_Nyq) |
|---|---|---|---|---|
| isotrope f·exp(−(f/0,3)²) | 0,26599 | 0,26595 | +0,02 % | 3,6 % (bord Nyquist) |
| anisotrope (0,4 / 0,25) | 0,29424 | 0,29157 | +0,91 % | 11,4 % |

### 6.7 Polynôme : conditionnement et degré (`t1` C)

| Test | Résultat | Verdict |
|---|---|---|
| cond(Vandermonde) brute, Δx = 0,2 / 0,5 / 1,0 | 5,9e9 / 9,6e7 / 2,0e9 ; après normalisation interne de polyfit : 7,6e7 ; domaine [−1,1] : 6,9e3 | Acceptable (double précision) |
| max \|polyfit − Polynomial.fit\| | ≤ 1,1e-9 (sur des valeurs ≈ 160) | OK |
| RankWarning | aucun | OK |
| f_moy vs degré 9/11/13/15/17 (Δx = 0,5) | 0,2775 / 0,2776 / 0,2784 / 0,2780 / 0,2777 | stable (±0,3 %) quand le domaine convient |
| degré 5 / 7 | 0,2821 / 0,2851 | +1,6 / +2,7 % |
| aire négative avant clipping (degré 11) | −0,025 sur 58,6 (0,04 %) | négligeable |

### 6.8 Fantôme, ROI UH, bruit, statuts (`t2`)

| Test | Attendu | Obtenu | Verdict |
|---|---|---|---|
| Centre détecté (262,3 ; 249,6), rayon 200 px, σ = 8 | — | (262,41 ; 249,58), 199,59 px, 72 rayons | OK (0,1 px, 0,4 px) |
| Rayon, σ = 25 / 40 / 60 UH | 200 | 199,7 / 199,6 / **72,6** | P-13 à σ ≥ 60 |
| Rayon, paroi 60 / 40 / −100 UH | 200 | 201,1 / **206,2** / 199,9 | P-13 paroi ≤ 40 UH |
| ROI centrale Ø (D = 160–300 mm, Δx 0,23–1,17) | 40 % | 39,6–40,0 % | OK |
| ROI périph. Ø, pixels | ≤ 10 %, ≥ 100 | 8,8–10,0 %, 113–5541 px | OK |
| Bord externe ROI périph. | 10–15 mm | 12,0–12,8 mm ; ±2 px de rayon → 11,5–13,5 mm | OK |
| Chevauchement centrale/périph. | non | non (tous cas) | OK |
| Moyenne ROI centrale (cupping 5 UH) | 2,390 ± 0,057 | 2,458 | OK |
| Uniformité | ≈ 2,68 | 2,72 | OK |
| σ ROI centrale, ddof | 8,0 | 8,0001 (ddof = 1 : ×1,00003) | OK |
| σ ROI centrale 1 coupe vs 10 coupes (bruit corrélé) | — | étendue 2,3 % entre coupes ; moyenne ROI SPB 7,981 ; √AUC 7,967 | P-02 |
| `water_ct_status(25,0)` | NCG (décision) | NC | P-08 |
| 24,96 / 7,04 affichés | « +25,0 » / « +7,0 » | NC / NC | P-08 |
| `noise_bounds(1,5)` ; (3,0) | ±0,2 ; ±0,3 | ±0,2 ; ±0,3 ; bornes incluses | OK |
| `nps_bounds(0,3)` | [0,27 ; 0,33] | [0,27 ; 0,33] | OK |
| Référence 2,0049 relue | 2,0049 | 2,00 | P-11 |
| Image constante | f_moy 0, NPS 0 | 0, 0 | OK |
| ROI 63 px | fonctionne | ValueError | P-12 |

---

## 7. Tests à ajouter

Tests synthétiques (sans données ANSM), prêts à implémenter dans `tests/test_nps_synthetic.py` :

1. **Bruit blanc, normalisation** : 200 ROI 64×64 de N(0, σ = 10), Δx = 0,5 → `compute_nps_2d(detrend_roi(roi))` moyenné ; attendu : moyenne des bins |f| > 0,15 mm⁻¹ = 25,0 ± 0,3 HU²·mm² ; `freq_x[1]−freq_x[0] == 1/(64·0,5)` ; `nps_2d[32,32] < 1e-12`.
2. **Parseval** : même jeu : `abs(nps_2d.sum()·Δf² − var(roi détrendue)) / var < 1e-3` ; et `sqrt(Σ NPS₂D Δf²)` vs σ dans [0,995 ; 1,0] (perte détrending 0,15 %).
3. **Axe radial pour tout N** : pour N ∈ {32,40,48,56,64,72,80,88,96,104,112,120,128} : `freq_r[1]−freq_r[0] == 1/(N·Δx)` à 1e-12 (échoue aujourd'hui pour 40/56/72/88/104/120 → P-03).
4. **Fréquence moyenne analytique** : série synthétique (helper `ct_like_noise` du scratchpad) f₀ = 0,3, Δx = 0,5, ROI 64 : f_moy brute 0–f_Nyq = 0,2659·(1 + biais de méthode) ; fixer l'attendu par tolérance ≤ 3 % et surtout **invariance** : f_moy(fit) / f_moy(brut) ∈ [0,99 ; 1,01] pour Δx ∈ {0,25 ; 0,35 ; 0,5 ; 0,7} et f₀ ∈ {0,2 ; 0,3} (échoue aujourd'hui pour Δx ≤ 0,4 → P-01).
5. **Invariance par translation/rotation de la ROI** : même champ de bruit, ROI décalées de (+7, −11) px et spectre tourné de 90° (`np.rot90` des ROI) : f_moy identique à 0,5 % près ; spectre 2D transposé = rot90 du spectre 2D (vérifie l'ordre (row, col) des axes).
6. **Isotropie** : SPB 2D analytique isotrope injecté dans `radial_average` : NPS₁D(f) = S(f) à 1 % sur 0,05 ≤ f ≤ 0,9·f_Nyq ; anisotrope : écart avec une moyenne par couronnes < 1 %.
7. **Au-delà de Nyquist** : spectre plat 25 → bins f > f_Nyq doivent valoir 25 (moyenne sans zéros) ou être exclus ; aujourd'hui 13,5→0 (P-04).
8. **Image constante / ROI impaire / 1 coupe** : f_moy = 0 et aucun NaN ; ROI 63 px extrait 63×63 (P-12) ; `slice_range=(k,k)` fonctionne.
9. **Bruit (P-02)** : série 10 coupes N(0, 8) : `NPSResult.noise` = 8,0 ± 0,05 et égal à `mean(std des ROI SPB)` ; coupe unique à σ = 8,3 ne doit pas changer la valeur de plus de 0,3 %.
10. **Statuts** : `water_ct_status(25.0) == NCG`, `(-25.0) == NCG`, `(7.0) == OK`, `(7.0 + 1e-9)` cohérent avec l'affichage ; `noise_status(1.7, 1.5) == OK` ; `noise_status(3.3, 3.0) == OK` ; `nps_status(0.33, 0.3) == OK` ; `nps_status(0.3306, 0.3) == NC`.
11. **Géométrie** : pour D_px ∈ {399,5 ; 400,1}, `nps_roi_size` identique (échoue aujourd'hui : 56 vs 64 → P-06) ; pour D = 160–300 mm, Δx = 0,23–1,2 : bord externe périph. ∈ [11,5 ; 13,5] mm, périph. ≥ 100 px, Ø ≤ 10 % D.
12. **Détection** : fantôme synthétique σ = 50 UH, paroi 120 : rayon ± 1 px ; paroi 40 UH : rayon ± 1,5 px (échoue aujourd'hui, P-13) ; `num_edge_points ≥ 60`.
13. **Validation ANSM (quand `test_data/` est présent)** : écart f_moy ≤ 2 % ; RMS relatif brut vs `NPS1D.csv` (0,05–0,8 mm⁻¹) ≤ 5 % ; `peak_intensity` ±5 % ; `sqrt_auc_nps_2d` = √(Σ NPS₂D Δf²) ±2 % ; `noise` ±2 % ; `pixel_size_mm == ref_config.pixel_size` ; décalage par corrélation croisée brut/brut < 0,25 bin.

---

## 8. Points positifs

- Normalisation 2D du SPB exacte (Δx²/N²·|FFT|²), axes en mm⁻¹ corrects, moyenne des spectres (pas spectre de la moyenne), Parseval respecté à 0,1 %.
- Détrending polynomial 2D d'ordre 2 par ROI, conforme à la méthode iQMetrix/Solomon ; 8 ROI non chevauchantes × 10 coupes → reproductibilité statistique de f_moy ≈ 0,3 %.
- Détection du bord **interne** par ajustement de cercle robuste et sub-pixel (0,1 px sur le centre, 0,4 px sur le rayon ; insensible à bulle, table, décentrage, bruit jusqu'à 40 UH), conforme à la lettre de la décision (10–15 mm du bord interne).
- Géométrie des ROI figée par installation (positions et tailles identiques d'un contrôle à l'autre), invalidée si matrice/pixel changent, enregistrée dans chaque `QCRun` : excellente réponse à l'exigence A-9.1.7.2 et au registre A-3.2.2.
- Formules des critères (±7/±25 UH, MIN/MAX(0,2 ; 10 %), ±10 %) fidèles, partagées entre GUI, PDF et historique ; références figées dans chaque contrôle ; conversion UH en float64 (pas d'overflow int16).
- Export JSON compatible iQMetrix (coin supérieur gauche), cohérent avec l'extraction interne (aller-retour exact pour les côtés pairs).

---

## 9. Plan d'action suggéré pour Claude Code local

| # | Action | Constats | Taille | Change les résultats ? |
|---|---|---|---|---|
| 1 | Diagnostiquer le biais ANSM sur `test_data/` : (a) recalculer f_av à partir des colonnes raw et fit de `NPS1D.csv` et retrouver la valeur de `NPS_Results.txt` (→ règle brut/fit et bornes d'iQMetrix) ; (b) corrélation croisée raw CQ TDM vs raw ANSM (décalage en bins) ; (c) comparer `PixelSpacing` DICOM et `pixel_size` JSON ; (d) refaire le test avec détrending « moyenne seule » et avec 36 angles pour voir lequel rapproche de la référence ; (e) série 4 : comparer les bins > f_Nyq | P-05, P-04, P-14, P-09 | M | non (analyse) |
| 2 | Corriger l'axe de fréquence radial (`freq_r = arange/(N·Δx)`) | P-03 | S | oui pour N ∉ multiples de 16 (pas sur la validation ANSM) |
| 3 | Fréquence moyenne : calculer sur le brut ou sur un fit restreint au support, en fonction du résultat de l'étape 1 ; tracer brut + fit dans le PDF ; avertissement si fit ≠ brut > 2 % | P-01, P-19, P-20 | M | **oui** → nouvelle validation ANSM, version majeure |
| 4 | Bruit = moyenne des écarts-types des ROI SPB sur les 10 coupes, exposé dans `NPSResult`, enregistré dans `QCRun`, libellé PDF « Bruit (ROI SPB, 10 coupes) » ; garder σ ROI centrale en information ; migration/marquage des anciennes références | P-02 | M | **oui** → version majeure, références à ré-établir |
| 5 | Au-delà de Nyquist : NaN + nanmean (ou limitation à f_Nyq), selon la convention établie à l'étape 1 | P-04 | S | oui pour les noyaux durs → validation ANSM (série 4) |
| 6 | Règle de taille des ROI SPB : taille en mm depuis le pixel (indépendante du diamètre détecté), multiples de 16 px ; avertissement/blocage de la réinitialisation de géométrie quand une référence existe | P-06 | S/M | oui si la taille change pour une installation (seulement lors d'une nouvelle référence) |
| 7 | Durcir la validation ANSM (≤ 2 %, grandeurs absolues, bin à bin, pixel size, taille de ROI issue de la règle) et mettre à jour les figures | P-05, P-07 | M | non |
| 8 | Bornes de statut : `>= 25` → NCG ; cohérence valeur affichée/statut (règle à documenter) ; unifier `analyze_water_phantom`/`qc_history` | P-08 | S | cas limites seulement |
| 9 | Tests synthétiques du §7 (1–12) dans la CI (sans données ANSM) | tous | M | non |
| 10 | Robustesse : ROI impaire, PixelSpacing absent → erreur, pixels non carrés, seuil de détection adaptatif + avertissement `num_edge_points`, coupe UH relative, références non arrondies | P-12, P-16, P-13, P-17, P-11 | S chacun | non |
| 11 | Documentation de la méthode (README + bloc « Méthode » dans le PDF) et suppression/renommage de `total_noise_power`/`average_nps` (→ `sqrt_auc_nps_2d`) | P-21, P-10, P-14 | S | non |
| 12 | Tendance : bande de tolérance par période de référence ; option mode spectral (stabilité CT) | P-15, P-22 | S/M | non |

Les étapes 3, 4, 5 et 6 modifient des valeurs mesurées : les regrouper dans une même version majeure, régénérer la validation ANSM, et prévenir les utilisateurs que les références (B_ref, f_ref) établies avec les versions ≤ 0.7 doivent être ré-établies ou converties (A-8 : nouvelle valeur de référence au contrôle externe suivant).
