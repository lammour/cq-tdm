"""Trend chart of one QC metric over time, shared by the GUI and the PDF report.

Pure matplotlib (Agg): returns PNG bytes plus the pixel position of every
plotted point so a GUI can hit-test clicks on the image.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from io import BytesIO

from .qc_history import METRICS, PENDING, QCRun, evaluate_run, tolerance_band

DARK_PALETTE = dict(
    bg="#2b2b2b", fg="#e0e0e0", muted="#aaaaaa", grid="#555555",
    ok="#66bb6a", nc="#ffa726", ncg="#ef5350", pending="#888888",
    band="#2e7d32", ref="#4fc3f7", current="#ce93d8", line="#4fc3f7",
)
LIGHT_PALETTE = dict(
    bg="#ffffff", fg="#222222", muted="#666666", grid="#dddddd",
    ok="#2e7d32", nc="#ef6c00", ncg="#c62828", pending="#9e9e9e",
    band="#a5d6a7", ref="#1565c0", current="#6a1b9a", line="#1565c0",
)


@dataclass
class TrendChart:
    png: bytes
    # (x_px, y_px, run) in image coordinates, origin top-left
    hits: list[tuple[float, float, QCRun]]


def render_trend_chart(
    runs: list[QCRun],
    metric: str,
    ref_noise: float | None,
    ref_nps: float | None,
    *,
    current: QCRun | None = None,
    selected: QCRun | None = None,
    palette: dict = DARK_PALETTE,
    width_px: int = 560,
    height_px: int = 260,
    dpi: int = 100,
    title: str | None = None,
) -> TrendChart:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    pal = palette
    label = dict(METRICS)[metric]
    fig, ax = plt.subplots(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    fig.patch.set_facecolor(pal["bg"])
    ax.set_facecolor(pal["bg"])

    ordered = sorted(runs, key=lambda r: r.date)
    plotted: list[tuple[date, float, QCRun, str]] = [
        (r.date, getattr(r, metric), r, evaluate_run(r)[metric])
        for r in ordered if getattr(r, metric) is not None
    ]

    band = tolerance_band(metric, ref_noise, ref_nps)
    if metric == "water_ct":
        ax.axhline(25, color=pal["ncg"], linestyle=":", linewidth=0.8)
        ax.axhline(-25, color=pal["ncg"], linestyle=":", linewidth=0.8)
    if band is not None:
        lo, hi, ref = band
        ax.axhspan(lo, hi, color=pal["band"], alpha=0.18, linewidth=0)
        ax.axhline(lo, color=pal["band"], linewidth=0.8, alpha=0.6)
        ax.axhline(hi, color=pal["band"], linewidth=0.8, alpha=0.6)
        if ref is not None:
            ax.axhline(ref, color=pal["ref"], linestyle="--", linewidth=1, alpha=0.8, label="Référence")

    if plotted:
        ax.plot([p[0] for p in plotted], [p[1] for p in plotted],
                color=pal["line"], linewidth=1.2, alpha=0.7, zorder=2)
        for x, y, _run, st in plotted:
            ax.plot(x, y, "o", color=pal.get(st, pal[PENDING]), markersize=6, zorder=3)

    if current is not None and getattr(current, metric) is not None:
        st = evaluate_run(current)[metric]
        y = getattr(current, metric)
        ax.plot(current.date, y, marker="*", markersize=13, linestyle="none",
                color=pal.get(st, pal[PENDING]), markeredgecolor=pal["current"],
                markeredgewidth=1.2, zorder=4, label="Mesure en cours")
        plotted.append((current.date, y, current, st))

    for x, y, run, _st in plotted:
        if run is selected:
            ax.plot(x, y, "o", markersize=14, markerfacecolor="none",
                    markeredgecolor=pal["fg"], markeredgewidth=1.5, zorder=5)

    if not plotted:
        ax.text(0.5, 0.5, "Aucune donnée", transform=ax.transAxes, ha="center",
                va="center", color=pal["muted"], fontsize=10)
    else:
        # Keep a sensible time window: matplotlib stretches a single date over years
        first = min(p[0] for p in plotted)
        last = max(p[0] for p in plotted)
        if (last - first).days < 180:
            pad = timedelta(days=max(45, (180 - (last - first).days) // 2))
            ax.set_xlim(first - pad, last + pad)
        else:
            span = last - first
            ax.set_xlim(first - span * 0.05, last + span * 0.05)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%y"))
        fig.autofmt_xdate(rotation=0, ha="center")

    ax.tick_params(colors=pal["muted"], labelsize=8)
    for side in ("bottom", "left"):
        ax.spines[side].set_color(pal["grid"])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, alpha=0.25, color=pal["grid"])
    ax.set_ylabel(label, color=pal["muted"], fontsize=8)
    if title:
        ax.set_title(title, color=pal["fg"], fontsize=9)
    if band is not None and band[2] is not None or current is not None:
        ax.legend(loc="best", fontsize=7, facecolor=pal["bg"], edgecolor=pal["grid"],
                  labelcolor=pal["fg"])
    fig.tight_layout(pad=0.6)

    fig_h = fig.get_size_inches()[1] * dpi
    hits = []
    for x, y, run, _st in plotted:
        px, py = ax.transData.transform((mdates.date2num(x), y))
        hits.append((px, fig_h - py, run))

    buf = BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return TrendChart(png=buf.getvalue(), hits=hits)
