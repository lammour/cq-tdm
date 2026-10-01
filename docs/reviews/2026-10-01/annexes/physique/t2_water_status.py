"""Checks of water_phantom / roi_geometry / qc_history against the ANSM decision."""
import sys
import numpy as np
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from synth import *  # noqa
from cq_tdm.core.water_phantom import calculate_rois, analyze_water_phantom, measure_roi, create_circular_mask
from cq_tdm.core.roi_geometry import ROIGeometry
from cq_tdm.core.dicom_loader import detect_phantom
from cq_tdm.core import qc_history as Q
from cq_tdm.core.utils import format_fr
from cq_tdm.core.nps import analyze_nps

np.set_printoptions(precision=4, suppress=True, linewidth=140)


def hdr(t):
    print("\n" + "=" * 100 + f"\n{t}\n" + "=" * 100)


hdr("A. GEOMETRIE DES ROI UH vs decision 9.1.7 (tailles, position du bord externe)")
for D_mm, dx in [(200, 0.5), (200, 0.469), (160, 0.4), (300, 0.7), (160, 1.172), (200, 0.234)]:
    D_px = D_mm / dx
    g = ROIGeometry.from_phantom(D_px, dx, 512, 512)
    R = D_px / 2
    gap_mm = (R - g.peripheral_distance - g.peripheral_radius) * dx
    cen_mm = 2 * g.central_radius * dx
    per_mm = 2 * g.peripheral_radius * dx
    n_pix = int(np.pi * g.peripheral_radius ** 2)
    overlap = (g.central_radius + g.peripheral_radius) > g.peripheral_distance
    print(f"D={D_mm}mm dx={dx}: centrale {cen_mm:.1f} mm ({100*cen_mm/D_mm:.1f} % ; cible 40 %) ; periph {per_mm:.1f} mm ({100*per_mm/D_mm:.1f} % <=10 %) ~{n_pix} px (>=100) ; "
          f"bord externe a {gap_mm:.2f} mm de la paroi (10-15) ; chevauchement centre/periph: {overlap} ; NPS ROI {g.nps_roi_size} px = {g.nps_roi_size*dx:.1f} mm ({100*g.nps_roi_size*dx/D_mm:.1f} % D)")

hdr("B. FANTOME SYNTHETIQUE : moyenne, uniformite, bruit (valeurs attendues analytiques)")
rng = np.random.default_rng(3)
sigma, mean_hu, cup = 8.0, 2.0, 5.0  # cupping: +5 HU at the inner wall
noise = white_noise(rng, (512, 512), sigma)
img = make_image(noise, pixel_mm=0.5, radius_px=200.0, center=(262.3, 249.6), water_mean=mean_hu, cupping=cup)
geo = detect_phantom(img)
print(f"detection: centre=({geo.center_row:.2f},{geo.center_col:.2f}) attendu (262.3,249.6) ; rayon={geo.radius:.2f} px attendu 200 ; points={geo.num_edge_points}")
rois = calculate_rois(img)
res = analyze_water_phantom(img, rois)
rc = rois.central.radius
exp_central = mean_hu + cup * (rc / 200.0) ** 2 / 2  # <r^2> over a disc = r^2/2
d = rois.geometry.peripheral_distance; rp = rois.geometry.peripheral_radius
exp_periph = mean_hu + cup * (d ** 2 + rp ** 2 / 2) / 200.0 ** 2
print(f"ROI centrale r={rc}px: mesure {res.central.mean_hu:.3f} HU, attendu {exp_central:.3f} (+/- {sigma/np.sqrt(res.central.num_pixels):.3f} stat) ; n={res.central.num_pixels} px")
print(f"ROI periph  r={rp}px d={d}px: moyennes {[round(m.mean_hu,2) for m in res.peripheral]} attendu {exp_periph:.3f} (+/- {sigma/np.sqrt(res.top.num_pixels):.3f})")
print(f"uniformite code = {res.uniformity:.3f} (max |periph - centre|) attendu ~{exp_periph-exp_central:.3f}")
print(f"bruit code (std ROI centrale, ddof=0) = {res.noise:.4f} ; sigma vrai = {sigma} ; ddof=1 donnerait x{np.sqrt(res.central.num_pixels/(res.central.num_pixels-1)):.6f}")
print(f"nombre CT eau = {res.water_ct_number:.3f} -> statut {Q.water_ct_status(res.water_ct_number)}")
# mask inclusivity
m = create_circular_mask((512, 512), (256, 256), 80)
print(f"masque r=80: {m.sum()} px vs pi r^2 = {np.pi*80**2:.0f} (bord inclus '<=' -> +{m.sum()-np.pi*80**2:.0f} px, {100*(m.sum()/(np.pi*80**2)-1):.2f} %)")

hdr("C. BRUIT : ROI centrale 40 % sur 1 coupe vs ROI SPB sur 10 coupes (definition ANSM 9.1.7.2)")
# 10 slices, white noise sigma=8 + cupping 5 HU + ring-like low-frequency artefact
series = make_series(10, lambda r, s: white_noise(r, s, sigma), pixel_mm=0.5, seed=7, water_mean=0.0, cupping=cup)
stds_central = []
for im in series.images:
    rr = calculate_rois(im)
    stds_central.append(analyze_water_phantom(im, rr).noise)
stds_central = np.array(stds_central)
resn = analyze_nps(series)
roi_stds = []
for im in series.images:
    for p in resn.roi_config.rois:
        h = p.side_square // 2
        roi_stds.append(im.pixel_array[p.y - h:p.y + h, p.x - h:p.x + h].std())
roi_stds = np.array(roi_stds)
print(f"sigma vrai = {sigma} ; cupping {cup} HU au bord")
print(f"std ROI centrale (1 coupe, coupe 5) = {stds_central[5]:.4f} ; sur les 10 coupes: moy {stds_central.mean():.4f}, min {stds_central.min():.4f}, max {stds_central.max():.4f}, etendue {100*(stds_central.max()-stds_central.min())/stds_central.mean():.2f} %")
print(f"std des 80 ROI SPB 64px: moy {roi_stds.mean():.4f}, ecart-type entre ROI {roi_stds.std():.4f} ; moyenne quadratique {np.sqrt((roi_stds**2).mean()):.4f}")
# Correlated noise: slice to slice variability
series2 = make_series(10, lambda r, s: ct_like_noise(r, s, 0.5, sigma, 0.3), pixel_mm=0.5, seed=8)
sc = np.array([analyze_water_phantom(im, calculate_rois(im)).noise for im in series2.images])
print(f"bruit correle (f0=0.3): std ROI centrale par coupe = {sc} ; etendue {100*(sc.max()-sc.min())/sc.mean():.2f} % ; moyenne {sc.mean():.4f}")
resn2 = analyze_nps(series2)
roi_stds2 = np.array([im.pixel_array[p.y - p.side_square//2:p.y + p.side_square//2, p.x - p.side_square//2:p.x + p.side_square//2].std()
                      for im in series2.images for p in resn2.roi_config.rois])
print(f"   std moyen des 80 ROI SPB = {roi_stds2.mean():.4f} ; sqrt(AUC NPS2D) = {np.sqrt(resn2.nps_2d.sum()*(resn2.frequencies_x[1]-resn2.frequencies_x[0])**2):.4f}")

hdr("D. STATUTS : bornes, inclusivite, arrondis d'affichage")
for v in [6.95, 7.0, 7.0000001, 7.04, 7.05, 24.96, 25.0, 25.04, -25.0, -7.0]:
    print(f"nombre CT {v:+.7f} -> affiche '{format_fr(v, 1, sign=True)} HU' -> statut {Q.water_ct_status(v)} ; analyze: acceptable={abs(v)<=7} ncg={abs(v)>25}")
print("decision: NCG si CT <= -25 ou 25 <= CT (borne 25 incluse dans NCG) ; NC si 7 < |CT| <= 25 ; conforme si |CT| <= 7")
for v in [6.96, 7.0, 7.04]:
    print(f"uniformite {v} -> affiche '{format_fr(v,1)}' -> {Q.uniformity_status(v)}")
for ref, val in [(1.5, 1.7), (1.5, 1.70000001), (1.5, 1.705), (3.0, 3.3), (3.0, 3.304), (3.0, 3.305), (3.0, 2.7), (2.0, 2.2)]:
    lo, hi = Q.noise_bounds(ref)
    print(f"bruit ref={ref} val={val} ecart={val-ref:+.5f} affiche '{format_fr(val-ref,2,sign=True)}' bornes [{lo:+.3f},{hi:+.3f}] -> {Q.noise_status(val, ref)}")
for ref, val in [(0.3, 0.33), (0.3, 0.3300004), (0.3, 0.3305), (0.3, 0.27), (0.3, 0.2695)]:
    print(f"SPB ref={ref} val={val} ecart={100*(val-ref)/ref:+.3f} % affiche '{format_fr(100*(val-ref)/ref,1,sign=True)} %' -> {Q.nps_status(val, ref)}")
print("bandes de tolerance:", Q.tolerance_band('noise', 2.0, None), Q.tolerance_band('noise', 1.0, None), Q.tolerance_band('nps_freq', None, 0.3), Q.tolerance_band('water_ct', None, None), Q.tolerance_band('uniformity', None, None))
# reference rounding on save (GUI re-parses the 2-decimal text)
for ref in [2.0049, 2.005, 0.26278]:
    print(f"reference {ref} -> champ texte '{format_fr(ref, 2)}' / '{format_fr(ref, 3)}' -> reparse {float(format_fr(ref,2).replace(',','.'))} / {float(format_fr(ref,3).replace(',','.'))}")

hdr("E. DETECTION : robustesse au bruit eleve et a une paroi faible")
for sig, wall in [(8, 120), (25, 120), (40, 120), (60, 120), (25, 60), (25, 40), (25, -100)]:
    rng = np.random.default_rng(11)
    im = make_image(white_noise(rng, (512, 512), sig), pixel_mm=0.5, radius_px=200.0, wall_hu=wall)
    g = detect_phantom(im)
    print(f"sigma={sig} paroi={wall} HU: rayon={g.radius:.2f} (attendu 200) centre=({g.center_row:.2f},{g.center_col:.2f}) points={g.num_edge_points}")
# effect of a radius error on the ROI edge position
for err_px in [-4, -2, 0, 2, 4]:
    g = ROIGeometry.from_phantom(400 + err_px, 0.5, 512, 512)
    gap = (200 - g.peripheral_distance - g.peripheral_radius) * 0.5
    print(f"erreur rayon {err_px/2:+.1f} px -> bord externe ROI periph a {gap:.2f} mm de la vraie paroi (10-15 requis) ; ROI centrale r={g.central_radius}")
print("\nFIN t2")
