"""Sharp-kernel case: zeros outside the matrix vs NaN-mean vs 0..Nyquist (NaN bins dropped)."""
import sys, warnings
import numpy as np
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from synth import *  # noqa
from t3_fit_sweep import pipeline, radial_nanmean  # noqa (re-runs t3 prints once)
from cq_tdm.core.nps import fit_nps_polynomial

warnings.simplefilter("ignore")
print("\n" + "=" * 110)
print("II-bis. NOYAU DUR : zeros hors matrice (code) vs NaN-mean (bins NaN exclus) vs limitation a Nyquist")
print("=" * 110)
for f0, dx in [(0.6, 0.573), (0.8, 0.5), (0.45, 0.7), (0.3, 0.5)]:
    p = pipeline(dx, f0)
    fa = analytic_fmean_ct_like(f0)
    nr_nan, fr_nan = radial_nanmean(p['nps2d'], dx)
    ok = np.isfinite(nr_nan)
    nyq = p['nyq']
    ff = np.linspace(0, 5, 20001); S = ct_like_spectrum(ff, f0)
    fa_nyq = fmean_numeric(ff, S, nyq)
    f_nan_raw = fmean_numeric(fr_nan[ok], nr_nan[ok])
    f_nan_fit = fmean_numeric(fr_nan[ok], fit_nps_polynomial(fr_nan[ok], nr_nan[ok]))
    print(f"f0={f0} dx={dx} Nyq={nyq:.3f} (bins valides NaN-mean: {ok.sum()}/{len(ok)}, fmax valide {fr_nan[ok][-1]:.3f} = {fr_nan[ok][-1]/nyq:.3f} Nyq)")
    print(f"   analytique 0..inf = {fa:.4f} ; analytique 0..Nyq = {fa_nyq:.4f}")
    print(f"   code (zeros, fit)      = {p['f_code']:.4f}  ({100*(p['f_code']/fa-1):+.2f} % vs 0..inf ; {100*(p['f_code']/fa_nyq-1):+.2f} % vs 0..Nyq)")
    print(f"   NaN-mean brut          = {f_nan_raw:.4f}  ({100*(f_nan_raw/fa-1):+.2f} % vs 0..inf)")
    print(f"   NaN-mean fit           = {f_nan_fit:.4f}  ({100*(f_nan_fit/fa-1):+.2f} % vs 0..inf)")
    print(f"   brut limite 0..Nyq     = {p['f_raw_nyq']:.4f}  ({100*(p['f_raw_nyq']/fa_nyq-1):+.2f} % vs 0..Nyq)")
    print(f"   ecart code vs NaN-mean(fit) = {100*(p['f_code']/f_nan_fit-1):+.2f} %")
print("FIN t3b")
