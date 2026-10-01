"""Sweep: polynomial-fit bias of f_mean vs pixel size / spectrum shape; beyond-Nyquist handling for sharp kernels."""
import sys
import numpy as np
from scipy import ndimage
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from synth import *  # noqa
from cq_tdm.core.nps import detrend_roi, compute_nps_2d, radial_average, fit_nps_polynomial

np.set_printoptions(precision=4, suppress=True, linewidth=140)


def pipeline(dx, f0, roi=64, n_slices=10, n_rois=8, seed=0, grid=512, deg=11):
    """Replicates analyze_nps on ROI patches cut from CT-like noise fields (no phantom)."""
    rng = np.random.default_rng(seed)
    nps_sum = None; cnt = 0
    for s in range(n_slices):
        field = ct_like_noise(rng, (grid, grid), dx, 10.0, f0)
        for k in range(n_rois):
            r0 = rng.integers(0, grid - roi); c0 = rng.integers(0, grid - roi)
            patch = field[r0:r0 + roi, c0:c0 + roi]
            a, fx, fy = compute_nps_2d(detrend_roi(patch), dx)
            nps_sum = a if nps_sum is None else nps_sum + a
            cnt += 1
    nps2d = nps_sum / cnt
    nr, fr = radial_average(nps2d, fx, fy, dx)
    nf = fit_nps_polynomial(fr, nr, deg)
    nyq = 1 / (2 * dx)
    return dict(fr=fr, nr=nr, nf=nf, nyq=nyq, nps2d=nps2d, fx=fx,
                f_code=fmean_numeric(fr, nf), f_raw=fmean_numeric(fr, nr),
                f_raw_nyq=fmean_numeric(fr, nr, nyq), f_fit_nyq=fmean_numeric(fr, nf, nyq))


def radial_nanmean(nps2d, dx):
    """Same 37-angle profile method but ignoring samples outside the matrix (NaN) instead of zeros."""
    n = nps2d.shape[0]; nyq = 1 / (2 * dx)
    nb = int(n // 2 * 1.375) + 1; rp = int(n // 2 * 1.375)
    prof = []
    for t in np.deg2rad(np.arange(0, 361, 10)):
        rv = np.linspace(0, rp, nb)
        prof.append(ndimage.map_coordinates(nps2d, [n // 2 + rv * np.sin(t), n // 2 + rv * np.cos(t)], order=1, mode='constant', cval=np.nan))
    return np.nanmean(np.array(prof), axis=0), np.linspace(0, nyq * 1.375, nb)


print("=" * 110)
print("I. BIAIS DU FIT DEGRE 11 SELON LA TAILLE DE PIXEL (spectre f exp(-(f/f0)^2), ROI 64 px, 10 coupes x 8 ROI)")
print("=" * 110)
print(" dx(mm) f0   Nyq   fmax   f_analyt  f_brut(0..1.375Nyq)  f_code(fit)   ecart fit vs brut   ecart code vs analyt   pic brut  pic fit")
for f0 in [0.2, 0.3, 0.45]:
    for dx in [0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6, 0.8, 1.0]:
        p = pipeline(dx, f0)
        fa = analytic_fmean_ct_like(f0)
        print(f" {dx:4.2f}  {f0:.2f}  {p['nyq']:.3f} {p['fr'][-1]:.3f}   {fa:.4f}    {p['f_raw']:.4f}             {p['f_code']:.4f}      "
              f"{100*(p['f_code']/p['f_raw']-1):+6.2f} %           {100*(p['f_code']/fa-1):+6.2f} %        "
              f"{p['fr'][np.argmax(p['nr'])]:.3f}    {p['fr'][np.argmax(p['nf'])]:.3f}")

print("\nMeme chose avec ROI 128 px (matrice 1024 / pixel fin) :")
for f0 in [0.2, 0.3]:
    for dx in [0.2, 0.25, 0.3, 0.5]:
        p = pipeline(dx, f0, roi=128)
        fa = analytic_fmean_ct_like(f0)
        print(f" dx={dx:4.2f} f0={f0}: fmax={p['fr'][-1]:.3f} f_analyt={fa:.4f} brut={p['f_raw']:.4f} code(fit)={p['f_code']:.4f} "
              f"-> fit vs brut {100*(p['f_code']/p['f_raw']-1):+.2f} % ; code vs analyt {100*(p['f_code']/fa-1):+.2f} %")

print("\nAlternatives d'ajustement sur le cas dx=0.25, f0=0.3, ROI 128 (le pire) :")
p = pipeline(0.25, 0.3, roi=128)
fr, nr, nyq = p['fr'], p['nr'], p['nyq']
from numpy.polynomial import Polynomial
m = fr <= nyq
for name, fit in [("degre 11 sur 0..1.375Nyq (code)", p['nf']),
                  ("degre 11 sur 0..Nyq seulement", np.maximum(Polynomial.fit(fr[m], nr[m], 11)(fr[m]), 0)),
                  ("degre 11 sur 0..1.2 mm-1 (support utile)", None)]:
    if fit is None:
        mm = fr <= 1.2
        fit = np.maximum(Polynomial.fit(fr[mm], nr[mm], 11)(fr[mm], ), 0)
        print(f"   {name}: f_moy = {fmean_numeric(fr[mm], fit):.4f} (brut meme domaine {fmean_numeric(fr[mm], nr[mm]):.4f})")
    elif len(fit) == len(fr):
        print(f"   {name}: f_moy = {fmean_numeric(fr, fit):.4f} (brut {fmean_numeric(fr, nr):.4f})")
    else:
        print(f"   {name}: f_moy = {fmean_numeric(fr[m], fit):.4f} (brut {fmean_numeric(fr[m], nr[m]):.4f})")
print("   brut vs fit autour du pic (bins 5..15):")
print("   f   ", fr[5:16]); print("   brut", nr[5:16]); print("   fit ", p['nf'][5:16])

print("\n" + "=" * 110)
print("II. NOYAU DUR (puissance a Nyquist) : zeros hors matrice vs NaN vs limitation a Nyquist")
print("=" * 110)
for f0, dx in [(0.6, 0.573), (0.8, 0.5), (0.45, 0.7), (0.3, 0.5)]:
    p = pipeline(dx, f0)
    fa = analytic_fmean_ct_like(f0)
    nr_nan, fr_nan = radial_nanmean(p['nps2d'], dx)
    nyq = p['nyq']
    # fraction of power at/beyond Nyquist in the true spectrum (analytic, radial 1D)
    ff = np.linspace(0, 5, 20001); S = ct_like_spectrum(ff, f0)
    frac = np.trapezoid(S[ff >= nyq], ff[ff >= nyq]) / np.trapezoid(S, ff)
    print(f"f0={f0} dx={dx} Nyq={nyq:.3f}: puissance 1D analytique au-dela de Nyquist = {100*frac:.1f} % ; f_analyt(0..inf)={fa:.4f} f_analyt(0..Nyq)={fmean_numeric(ff, S, nyq):.4f}")
    print(f"   code (zeros, fit 0..1.375Nyq) = {p['f_code']:.4f} ({100*(p['f_code']/fa-1):+.2f} %) ; brut zeros = {p['f_raw']:.4f} ; "
          f"NaN-mean brut 0..1.375Nyq = {fmean_numeric(fr_nan, nr_nan):.4f} ({100*(fmean_numeric(fr_nan, nr_nan)/fa-1):+.2f} %) ; "
          f"brut 0..Nyq = {p['f_raw_nyq']:.4f} ({100*(p['f_raw_nyq']/fa-1):+.2f} %) ; NaN-mean fit = {fmean_numeric(fr_nan, fit_nps_polynomial(fr_nan, nr_nan)):.4f}")
    print(f"   NPS radial au-dela de Nyquist: zeros {p['nr'][p['fr']>nyq][:6]} ; NaN-mean {nr_nan[fr_nan>nyq][:6]}")

print("\n" + "=" * 110)
print("III. REPRODUCTIBILITE STATISTIQUE de f_moy (10 coupes x 8 ROI 64 px) : ecart-type entre realisations")
print("=" * 110)
for f0, dx in [(0.3, 0.5), (0.3, 0.25)]:
    vals = [pipeline(dx, f0, seed=s)['f_code'] for s in range(12)]
    raws = [pipeline(dx, f0, seed=s)['f_raw'] for s in range(12)]
    print(f"f0={f0} dx={dx}: f_code moy {np.mean(vals):.4f} sd {np.std(vals):.4f} ({100*np.std(vals)/np.mean(vals):.2f} %) ; brut moy {np.mean(raws):.4f} sd {np.std(raws):.4f} ({100*np.std(raws)/np.mean(raws):.2f} %)")
print("FIN t3")
