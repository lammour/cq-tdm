"""Diagnostic P-01 / P-05 : fréquence moyenne brute vs ajustée sur la banque ANSM.

À lancer en local, depuis la racine du dépôt, avec test_data/ présent :
    python docs/reviews/2026-10-01/annexes/physique/ansm_raw_vs_fit.py

Pour chaque série : f_moy telle que calculée par CQ TDM (fit degré 11 sur
[0 ; 1,375 f_Nyq]), f_moy sur le spectre brut (même domaine, puis 0–f_Nyq),
f_moy sur un fit restreint à 0–f_Nyq, et l'écart de chacune à la référence
iQMetrix. Teste aussi, à partir des colonnes raw/fit de NPS1D.csv, quelle
règle (brut ou fit, bornes) reproduit l'« Average Frequency » d'iQMetrix.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

from conftest import SERIES_INFO, TEST_DATA_DIR  # noqa: E402
from test_nps_validation import (  # noqa: E402
    find_slice_range_by_location,
    load_roi_config,
    parse_nps_1d_csv,
    parse_nps_results,
)

from cq_tdm.core.dicom_loader import load_dicom_folder  # noqa: E402
from cq_tdm.core.nps import analyze_nps, fit_nps_polynomial  # noqa: E402

_trapz = getattr(np, "trapezoid", None) or np.trapz


def centroid(f: np.ndarray, s: np.ndarray, fmax: float | None = None) -> float:
    if fmax is not None:
        m = f <= fmax + 1e-12
        f, s = f[m], s[m]
    s = np.maximum(s, 0)
    return float(_trapz(f * s, f) / _trapz(s, f))


def pct(a: float, ref: float) -> str:
    return f"{(a - ref) / ref * 100:+6.2f} %"


def main() -> None:
    print(f"test_data : {TEST_DATA_DIR}\n")
    header = (
        f"{'série':8} {'réf':>7} | {'fit 1.375Nq':>11} {'brut 1.375Nq':>12} "
        f"{'brut 0-Nq':>10} {'fit 0-Nq':>9} | écarts à la réf (même ordre)"
    )
    for mode in ("ROI de référence (JSON)", "ROI automatiques"):
        print(f"=== {mode} ===")
        print(header)
        for name, info in SERIES_INFO.items():
            sdir = TEST_DATA_DIR / name
            ddir = sdir / info["dicom_subdir"]
            cfg = load_roi_config(sdir)
            ref = parse_nps_results(ddir / "NPS_Results.txt")
            series = load_dicom_folder(ddir)
            rng = find_slice_range_by_location(series, cfg.slice_start_mm, cfg.slice_end_mm)
            kw = dict(roi_size=64, slice_range=rng)
            if mode.startswith("ROI de réf"):
                kw["roi_positions"] = cfg.rois
            r = analyze_nps(series, **kw)
            f, raw, fit = r.frequencies_radial, r.nps_radial, r.nps_radial_fit
            nyq = 0.5 / r.pixel_size_mm
            m = f <= nyq + 1e-12
            fit_nq = fit_nps_polynomial(f[m], raw[m], degree=11)
            vals = [
                r.mean_frequency,              # code actuel (fit, 0-1.375 Nq)
                centroid(f, raw),              # brut, même domaine
                centroid(f, raw, nyq),         # brut, 0-Nq
                centroid(f[m], fit_nq),        # fit restreint 0-Nq
            ]
            print(
                f"{name:8} {ref.average_frequency:7.4f} | "
                + " ".join(f"{v:11.4f}" for v in vals)
                + " | "
                + "  ".join(pct(v, ref.average_frequency) for v in vals)
                + f"   (pixel {r.pixel_size_mm:.4f} mm, JSON {cfg.pixel_size:.4f} mm, Nq {nyq:.3f})"
            )
        print()

    print("=== Règle iQMetrix : f_av recalculée depuis NPS1D.csv (colonnes raw / fit) ===")
    print(f"{'série':8} {'réf':>7} | {'raw tout':>9} {'fit tout':>9} {'raw 0-Nq':>9} {'fit 0-Nq':>9} | f_max csv")
    for name, info in SERIES_INFO.items():
        ddir = TEST_DATA_DIR / name / info["dicom_subdir"]
        ref = parse_nps_results(ddir / "NPS_Results.txt")
        d = parse_nps_1d_csv(ddir / "NPS1D.csv")
        cfg = load_roi_config(TEST_DATA_DIR / name)
        nyq = 0.5 / cfg.pixel_size
        vals = [
            centroid(d.frequencies, d.nps_1d_raw),
            centroid(d.frequencies, d.nps_1d_fit),
            centroid(d.frequencies, d.nps_1d_raw, nyq),
            centroid(d.frequencies, d.nps_1d_fit, nyq),
        ]
        print(
            f"{name:8} {ref.average_frequency:7.4f} | "
            + " ".join(f"{v:9.4f}" for v in vals)
            + " | "
            + "  ".join(pct(v, ref.average_frequency) for v in vals)
            + f"   f_max {d.frequencies[-1]:.3f} (Nq {nyq:.3f})"
        )


if __name__ == "__main__":
    main()
