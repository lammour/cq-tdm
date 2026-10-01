"""Numerical checks of cq_tdm.core.nps against theory (white noise, Parseval, CT-like noise)."""
import sys, warnings
import numpy as np
from numpy.polynomial import Polynomial
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from synth import *  # noqa
from cq_tdm.core import nps as N
from cq_tdm.core.nps import (detrend_roi, compute_nps_2d, radial_average,
                             fit_nps_polynomial, analyze_nps)

np.set_printoptions(precision=4, suppress=True, linewidth=140)
rng = np.random.default_rng(42)


def hdr(t):
    print("\n" + "=" * 100 + f"\n{t}\n" + "=" * 100)


# ---------------------------------------------------------------------------
hdr("A. BRUIT BLANC GAUSSIEN : NPS 2D attendu = sigma^2 * dx * dy, Parseval")
sigma, dx, Nn = 10.0, 0.5, 64
expected = sigma ** 2 * dx * dx
nps_sum = None; var_meas = []; var_detr = []
n_roi = 400
for _ in range(n_roi):
    roi = rng.normal(0, sigma, (Nn, Nn))
    var_meas.append(roi.var())
    d = detrend_roi(roi)
    var_detr.append(d.var())
    n2, fx, fy = compute_nps_2d(d, dx)
    nps_sum = n2 if nps_sum is None else nps_sum + n2
nps2d = nps_sum / n_roi
df = fx[1] - fx[0]
print(f"attendu NPS2D plat = sigma^2*dx^2 = {expected:.4f} HU^2.mm^2")
print(f"moyenne NPS2D (tous bins)         = {nps2d.mean():.4f}  (ecart {100*(nps2d.mean()/expected-1):+.3f} %)")
c = Nn // 2
print(f"NPS2D au DC (apres detrend ordre 2) = {nps2d[c, c]:.4g} ; bins voisins (c,c+1)={nps2d[c, c+1]:.3f} (c,c+2)={nps2d[c, c+2]:.3f} (c+1,c+1)={nps2d[c+1,c+1]:.3f}")
mask_hi = np.hypot(*np.meshgrid(fx, fy)) > 0.15
print(f"moyenne NPS2D hors basses freq (|f|>0.15) = {nps2d[mask_hi].mean():.4f} (ecart {100*(nps2d[mask_hi].mean()/expected-1):+.3f} %)")
par = nps2d.sum() * df * df
print(f"Parseval: sum(NPS2D)*df^2 = {par:.3f}  vs var ROI brute {np.mean(var_meas):.3f}  vs var ROI detrendee {np.mean(var_detr):.3f}  (sigma^2={sigma**2})")
print(f"  -> perte de variance due au detrend ordre 2: {100*(1-np.mean(var_detr)/np.mean(var_meas)):.3f} % (attendu ~ 6/4096 = {100*6/4096:.3f} %)")
print(f"  -> df = {df:.5f} mm^-1 ; attendu 1/(N*dx) = {1/(Nn*dx):.5f}")

nr, fr = radial_average(nps2d, fx, fy, dx)
nyq = 1 / (2 * dx)
print(f"radial: {len(fr)} bins, fr[0..3]={fr[:4]}, fr[-1]={fr[-1]:.4f} (= {fr[-1]/nyq:.4f} x Nyquist), pas={fr[1]-fr[0]:.5f}")
print("NPS radial brut (bins 0..44):")
print(nr)
print(f"NPS radial moyen sur f<=Nyquist (hors bin 0)= {nr[(fr <= nyq) & (fr > 0)].mean():.4f} (attendu {expected})")
print(f"NPS radial a f = Nyquist..1.375 Nyquist      = {nr[fr > nyq]}")
fit = fit_nps_polynomial(fr, nr, 11)
print(f"f_moy brut [0,1.375Nyq] = {fmean_numeric(fr, nr):.4f} ; f_moy brut [0,Nyq] = {fmean_numeric(fr, nr, nyq):.4f} ; attendu bruit blanc (plat jusqu'a Nyq) = Nyq/2 = {nyq/2:.4f}")
print(f"f_moy FIT  [0,1.375Nyq] = {fmean_numeric(fr, fit):.4f} ; f_moy FIT [0,Nyq] = {fmean_numeric(fr, fit, nyq):.4f}")
print(f"fit min = {fit.min():.3f}, fit[0]={fit[0]:.3f}, fit max = {fit.max():.3f} ; residu RMS fit-brut sur f<=Nyq = {np.sqrt(np.mean((fit-nr)[fr<=nyq]**2)):.3f}")
tnp = np.sum(nr) * (fr[1] - fr[0])
print(f"'total_noise_power' du code = sum(nps_radial)*df = {tnp:.3f} (unite reelle HU^2.mm) ; integrale polaire 2*pi*int f NPS1D df (0..Nyq) = {np.trapezoid(2*np.pi*fr[fr<=nyq]*nr[fr<=nyq], fr[fr<=nyq]):.2f} ; sigma^2 = {sigma**2} ; pi/4*sigma^2 (disque inscrit) = {np.pi/4*sigma**2:.2f}")

# ---------------------------------------------------------------------------
hdr("B. BRUIT CT-LIKE S(f) = f exp(-(f/f0)^2) : f_moy analytique vs analyze_nps")
results_B = {}
for (f0, dx, matrix, sigma) in [(0.30, 0.5, 512, 10.0), (0.20, 0.5, 512, 10.0), (0.40, 0.5, 512, 10.0),
                                 (0.30, 0.7, 512, 10.0), (0.30, 0.25, 1024, 10.0)]:
    fa = analytic_fmean_ct_like(f0)
    series = make_series(10, lambda r, s: ct_like_noise(r, s, dx, sigma, f0), pixel_mm=dx, matrix=matrix,
                         radius_px=200.0 * (matrix / 512), seed=int(f0 * 100 + dx * 10))
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        res = analyze_nps(series)
        nwarn = len([x for x in w if 'RankWarning' in str(x.category) or 'Rank' in str(x.message)])
    nyq = 1 / (2 * dx)
    fr, nr, nf = res.frequencies_radial, res.nps_radial, res.nps_radial_fit
    f_raw_full = fmean_numeric(fr, nr)
    f_raw_nyq = fmean_numeric(fr, nr, nyq)
    f_fit_nyq = fmean_numeric(fr, nf, nyq)
    # 2D "true" (Cartesian) centroid of the radial frequency
    FX, FY = np.meshgrid(res.frequencies_x, res.frequencies_y)
    FR = np.hypot(FX, FY)
    f2d = (FR * res.nps_2d).sum() / res.nps_2d.sum()
    # Parseval on the 2D averaged spectrum
    df2 = res.frequencies_x[1] - res.frequencies_x[0]
    var2d = res.nps_2d.sum() * df2 ** 2
    # variance measured in the ROIs (mean over slices of the ROI variance)
    roi_vars = []
    for img in series.images:
        for p in res.roi_config.rois:
            h = p.side_square // 2
            r = img.pixel_array[p.y - h:p.y + h, p.x - h:p.x + h]
            roi_vars.append(r.var())
    print(f"f0={f0} dx={dx} matrix={matrix} ROI={res.roi_size}px coupes={res.num_slices} rankwarn={nwarn}")
    print(f"   f_moy analytique = {fa:.4f} | code (fit, 0..1.375Nyq) = {res.mean_frequency:.4f} ({100*(res.mean_frequency/fa-1):+.2f} %)"
          f" | brut 0..1.375Nyq = {f_raw_full:.4f} ({100*(f_raw_full/fa-1):+.2f} %) | brut 0..Nyq = {f_raw_nyq:.4f} ({100*(f_raw_nyq/fa-1):+.2f} %)"
          f" | fit 0..Nyq = {f_fit_nyq:.4f} ({100*(f_fit_nyq/fa-1):+.2f} %) | centroide 2D cartesien = {f2d:.4f}")
    print(f"   Parseval 2D: sum(NPS2D)df^2 = {var2d:.2f} vs var ROI = {np.mean(roi_vars):.2f} (sigma^2 = {sigma**2}) ; "
          f"fit min = {nf.min():.3f} ; part de puissance du fit au-dela de Nyquist = {100*np.trapezoid(nf[fr>=nyq], fr[fr>=nyq])/np.trapezoid(nf, fr):.2f} % ; "
          f"part brute au-dela de Nyquist = {100*np.trapezoid(nr[fr>=nyq], fr[fr>=nyq])/np.trapezoid(nr, fr):.2f} %")
    print(f"   pic brut a f = {fr[np.argmax(nr)]:.4f}, pic fit a f = {fr[np.argmax(nf)]:.4f} ; analytique pic = f0/sqrt(2) = {f0/np.sqrt(2):.4f}")
    results_B[(f0, dx, matrix)] = (res, series)

# ---------------------------------------------------------------------------
hdr("C. AJUSTEMENT POLYNOMIAL DEGRE 11 : conditionnement, domaine, degre")
res, series = results_B[(0.30, 0.5, 512)]
fr, nr = res.frequencies_radial, res.nps_radial
nyq = 1.0
for dxx in [0.2, 0.5, 1.0]:
    frs = fr * (0.5 / dxx)  # same spectrum sampled with another pixel size -> other frequency range
    V = np.vander(frs, 12)
    print(f"dx={dxx}: f max={frs.max():.3f} mm^-1, cond(Vandermonde brute)={np.linalg.cond(V):.3e}, "
          f"cond(Vandermonde colonnes normalisees, comme np.polyfit)={np.linalg.cond(V/np.sqrt((V*V).sum(0))):.3e}, "
          f"cond(Vandermonde domaine [-1,1])={np.linalg.cond(np.vander(2*frs/frs.max()-1, 12)):.3e}")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        c = np.polyfit(frs, nr, 11)
        fit_a = np.polyval(c, frs)
        print(f"   np.polyfit warnings: {[str(x.message)[:60] for x in w]}")
    fit_b = Polynomial.fit(frs, nr, 11)(frs)
    print(f"   max |polyfit - Polynomial.fit(domaine scale)| = {np.max(np.abs(fit_a-fit_b)):.3e} (echelle NPS max {nr.max():.1f})")
print("Sensibilite de f_moy au degre du polynome (meme spectre brut, f en 0..1.375 Nyq):")
fa = analytic_fmean_ct_like(0.30)
for deg in [5, 7, 9, 11, 13, 15, 17]:
    fit = fit_nps_polynomial(fr, nr, deg)
    print(f"   degre {deg:2d}: f_moy = {fmean_numeric(fr, fit):.4f} ({100*(fmean_numeric(fr, fit)/fa-1):+.2f} % vs analytique), "
          f"f_moy(0..Nyq) = {fmean_numeric(fr, fit, nyq):.4f}, min fit = {fit.min():.2f}, RMS residu = {np.sqrt(np.mean((fit-nr)**2)):.3f}")
print(f"   brut      : f_moy = {fmean_numeric(fr, nr):.4f} ({100*(fmean_numeric(fr, nr)/fa-1):+.2f} %)")
# clipping effect: how much negative area is removed
c = np.polyfit(fr, nr, 11); raw_fit = np.polyval(c, fr)
print(f"   zone negative du fit degre 11 avant clipping: min = {raw_fit.min():.3f}, aire negative = {np.trapezoid(np.minimum(raw_fit,0), fr):.3f} vs aire totale {np.trapezoid(nr, fr):.2f}")

# ---------------------------------------------------------------------------
hdr("D. AXE DE FREQUENCE RADIAL SELON LA TAILLE DE ROI (pas code vs 1/(N dx))")
dx = 0.5
print(" N   num_bins  r_pixels  pas_code     1/(N dx)   ratio   erreur % sur l'axe (et donc sur f_moy)")
for Nn in [32, 40, 48, 56, 64, 72, 80, 88, 96, 104, 112, 120, 128]:
    dummy = np.ones((Nn, Nn))
    fx = np.fft.fftshift(np.fft.fftfreq(Nn)) / dx
    nr_, fr_ = radial_average(dummy, fx, fx, dx)
    step = fr_[1] - fr_[0]
    nb = len(fr_); rp = int(Nn // 2 * 1.375)
    print(f"{Nn:3d}   {nb:3d}      {rp:3d}     {step:.6f}   {1/(Nn*dx):.6f}   {step/(1/(Nn*dx)):.4f}   {100*(step*(Nn*dx)-1):+.3f}")
# Direct measurement on CT-like series with ROI 56 and 72 vs 64
res64, series = results_B[(0.30, 0.5, 512)]
fa = analytic_fmean_ct_like(0.30)
for rs in [40, 48, 56, 64, 72, 88, 96, 128]:
    r = analyze_nps(series, roi_size=rs)
    print(f"ROI {rs:3d} px: f_moy code = {r.mean_frequency:.4f} ({100*(r.mean_frequency/fa-1):+.2f} % vs analytique {fa:.4f}) ; brut 0..Nyq = {fmean_numeric(r.frequencies_radial, r.nps_radial, 1.0):.4f}")

# ---------------------------------------------------------------------------
hdr("E. MOYENNE RADIALE : 37 angles (0 et 360 doublon), interpolation, zeros hors matrice")
# exact radial average by fine annular binning on an analytic anisotropic 2D NPS
Nn, dx = 64, 0.5
fx = np.fft.fftshift(np.fft.fftfreq(Nn)) / dx
FX, FY = np.meshgrid(fx, fx)
for name, S in [("isotrope f exp(-(f/0.3)^2)", np.hypot(FX, FY) * np.exp(-(np.hypot(FX, FY) / 0.3) ** 2)),
                ("anisotrope (fx/0.4, fy/0.25)", np.hypot(FX, FY) * np.exp(-(FX / 0.4) ** 2 - (FY / 0.25) ** 2))]:
    nr_, fr_ = radial_average(S, fx, fx, dx)
    # reference: dense angular sampling (3600 angles, no duplicate) with same bilinear interpolation
    from scipy import ndimage
    th = np.deg2rad(np.arange(0, 360, 0.1))
    prof = []
    for t in th:
        rv = np.linspace(0, int(Nn // 2 * 1.375), len(fr_))
        prof.append(ndimage.map_coordinates(S, [Nn // 2 + rv * np.sin(t), Nn // 2 + rv * np.cos(t)], order=1, mode='constant', cval=0))
    dense = np.mean(prof, axis=0)
    # and the "no zeros outside" variant (nan outside, nanmean)
    prof2 = []
    for t in th:
        rv = np.linspace(0, int(Nn // 2 * 1.375), len(fr_))
        p = ndimage.map_coordinates(S, [Nn // 2 + rv * np.sin(t), Nn // 2 + rv * np.cos(t)], order=1, mode='constant', cval=np.nan)
        prof2.append(p)
    dense_nan = np.nanmean(np.array(prof2), axis=0)
    nyq = 1.0
    print(f"{name}: f_moy code(37 angles) = {fmean_numeric(fr_, nr_):.5f} ; 3600 angles = {fmean_numeric(fr_, dense):.5f} "
          f"({100*(fmean_numeric(fr_, nr_)/fmean_numeric(fr_, dense)-1):+.3f} %) ; sans zeros hors matrice = {fmean_numeric(fr_, dense_nan):.5f} "
          f"({100*(fmean_numeric(fr_, dense_nan)/fmean_numeric(fr_, dense)-1):+.3f} %) ; limite 0..Nyq = {fmean_numeric(fr_, dense, nyq):.5f}")
    print(f"   max ecart relatif code vs 3600 angles sur f<=Nyq: {100*np.max(np.abs(nr_-dense)[(fr_<=nyq)&(fr_>0)]/dense[(fr_<=nyq)&(fr_>0)]):.3f} %")

# white-noise flat spectrum: what the >Nyquist tail does to f_mean
S = np.full((Nn, Nn), 25.0)
nr_, fr_ = radial_average(S, fx, fx, dx)
print(f"spectre plat 25: NPS radial code au-dela de Nyquist = {nr_[fr_ > 1.0]}")
print(f"   f_moy code (0..1.375Nyq, zeros hors matrice) = {fmean_numeric(fr_, nr_):.4f} vs theorie 1D plat 0..Nyq = 0.5")

# ---------------------------------------------------------------------------
hdr("F. EFFET DU DETRENDING ORDRE 2 vs SOUSTRACTION DE LA MOYENNE sur f_moy")
res, series = results_B[(0.30, 0.5, 512)]
fa = analytic_fmean_ct_like(0.30)
nps_d = None; nps_m = None; cnt = 0
for img in series.images:
    for p in res.roi_config.rois:
        h = p.side_square // 2
        r = img.pixel_array[p.y - h:p.y + h, p.x - h:p.x + h]
        a, fx, fy = compute_nps_2d(detrend_roi(r), 0.5)
        b, _, _ = compute_nps_2d(r - r.mean(), 0.5)
        nps_d = a if nps_d is None else nps_d + a
        nps_m = b if nps_m is None else nps_m + b
        cnt += 1
nps_d /= cnt; nps_m /= cnt
nrd, frr = radial_average(nps_d, fx, fy, 0.5)
nrm, _ = radial_average(nps_m, fx, fy, 0.5)
print(f"f_moy brut 0..Nyq: detrend ordre 2 = {fmean_numeric(frr, nrd, 1.0):.4f} ; moyenne seule = {fmean_numeric(frr, nrm, 1.0):.4f} ; analytique = {fa:.4f}")
print(f"f_moy fit code   : detrend ordre 2 = {fmean_numeric(frr, fit_nps_polynomial(frr, nrd)):.4f} ; moyenne seule = {fmean_numeric(frr, fit_nps_polynomial(frr, nrm)):.4f}")
print(f"premiers bins (f=0, 0.031, 0.0625, 0.094): detrend {nrd[:4]} ; moyenne seule {nrm[:4]}")
print(f"puissance totale 1D: detrend {np.trapezoid(nrd, frr):.3f} ; moyenne seule {np.trapezoid(nrm, frr):.3f} ({100*(np.trapezoid(nrd, frr)/np.trapezoid(nrm, frr)-1):+.2f} %)")

# ---------------------------------------------------------------------------
hdr("G. SENSIBILITE DE f_moy A UN DECALAGE D'UN DEMI-BIN DE L'AXE DE FREQUENCE (hypothese pour l'ecart ANSM)")
for (f0, dx, matrix), (res, _) in results_B.items():
    fr, nf = res.frequencies_radial, res.nps_radial_fit
    half = (fr[1] - fr[0]) / 2
    print(f"f0={f0} dx={dx}: f_moy = {res.mean_frequency:.4f}, demi-bin = {half:.4f} -> decalage relatif = {100*half/res.mean_frequency:+.2f} %")

# ---------------------------------------------------------------------------
hdr("H. CAS LIMITES : image constante, nombre de coupes, 1024, ROI impaire")
series_c = make_series(3, lambda r, s: np.zeros(s), seed=1)
rc = analyze_nps(series_c, slice_range=(0, 2))
print(f"image constante: f_moy = {rc.mean_frequency}, NPS max = {rc.nps_2d.max()}, total = {rc.total_noise_power}")
res, series = results_B[(0.30, 0.5, 512)]
for sr in [(0, 0), (0, 2), (0, 8), (0, 9)]:
    r = analyze_nps(series, slice_range=sr)
    print(f"slice_range={sr}: coupes={r.num_slices}, f_moy={r.mean_frequency:.4f}")
try:
    r = analyze_nps(series, roi_size=63)
    print(f"ROI 63 px (impaire): f_moy={r.mean_frequency:.4f}, bins={len(r.frequencies_radial)}, fmax={r.frequencies_radial[-1]:.4f}, pas={r.frequencies_radial[1]-r.frequencies_radial[0]:.5f} vs 1/(63 dx)={1/(63*0.5):.5f}")
except Exception as e:
    print("ROI 63 px:", type(e).__name__, e)
# series with fewer than 10 slices and default call
try:
    analyze_nps(make_series(5, lambda r, s: np.zeros(s)))
except ValueError as e:
    print("5 coupes, appel par defaut ->", e)
# default slice selection for 21 and 20 slices
for n in (20, 21):
    s = make_series(n, lambda r, s: np.zeros(s))
    start = (n - 10) // 2
    print(f"{n} coupes: coupes SPB par defaut = indices {start}..{start+9} (centre {(start+start+9)/2}), coupe centrale = {(n-1)//2}")
print("\nFIN t1")
