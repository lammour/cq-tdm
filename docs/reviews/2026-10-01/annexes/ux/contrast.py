def lum(hexc):
    hexc = hexc.lstrip('#')
    r, g, b = [int(hexc[i:i+2], 16)/255 for i in (0, 2, 4)]
    def f(c): return c/12.92 if c <= 0.03928 else ((c+0.055)/1.055)**2.4
    return 0.2126*f(r)+0.7152*f(g)+0.0722*f(b)
def ratio(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi+0.05)/(lo+0.05)
pairs = [
 # (label, fg, bg)
 ("Dark: text #e0e0e0 / bg #2b2b2b", "#e0e0e0", "#2b2b2b"),
 ("Dark: text_secondary #aaaaaa / bg #2b2b2b", "#aaaaaa", "#2b2b2b"),
 ("Dark: pending #888888 / bg #2b2b2b", "#888888", "#2b2b2b"),
 ("Dark: #888 (summary/labels) / window #2d2d2d", "#888888", "#2d2d2d"),
 ("Dark: #666 (hint welcome) / #1e1e1e", "#666666", "#1e1e1e"),
 ("Dark: #666 (db path label DeviceManager) / window #2d2d2d", "#666666", "#2d2d2d"),
 ("Dark: section_title #4fc3f7 / bg #2b2b2b", "#4fc3f7", "#2b2b2b"),
 ("Dark: th #aaaaaa / bg #2b2b2b", "#aaaaaa", "#2b2b2b"),
 ("Dark: ok #4caf50 / bg #2b2b2b", "#4caf50", "#2b2b2b"),
 ("Dark: nc #ff9800 / bg #2b2b2b", "#ff9800", "#2b2b2b"),
 ("Dark: ncg #f44336 / bg #2b2b2b", "#f44336", "#2b2b2b"),
 ("Dark: warning_text #ffcc80 / warning_bg #4a3000", "#ffcc80", "#4a3000"),
 ("Dark: warning_title #ff9800 / warning_bg #4a3000", "#ff9800", "#4a3000"),
 ("Dark: accent button white / #2a82da", "#ffffff", "#2a82da"),
 ("Dark: accent link #2a82da / bg #2b2b2b", "#2a82da", "#2b2b2b"),
 ("Dark: accent link #2a82da / window #2d2d2d", "#2a82da", "#2d2d2d"),
 ("Dark: disabled button text pending #888888 / border #444444", "#888888", "#444444"),
 ("Dark: palette disabled #7f7f7f / window #2d2d2d", "#7f7f7f", "#2d2d2d"),
 ("Dark: palette text #dcdcdc / base #1e1e1e", "#dcdcdc", "#1e1e1e"),
 ("Dark: overlay title #ccc / rgba(30,30,30) ~#1e1e1e", "#cccccc", "#1e1e1e"),
 ("Dark: overlay UH label #ffcc00 / #1e1e1e", "#ffcc00", "#1e1e1e"),
 ("Dark: overlay SPB label #00cc00 / #1e1e1e", "#00cc00", "#1e1e1e"),
 ("Dark: info overlay #dddddd / #1e1e1e", "#dddddd", "#1e1e1e"),
 ("Dark: history ok #66bb6a / #2b2b2b", "#66bb6a", "#2b2b2b"),
 ("Dark: history nc #ffa726 / #2b2b2b", "#ffa726", "#2b2b2b"),
 ("Dark: history ncg #ef5350 / #2b2b2b", "#ef5350", "#2b2b2b"),
 ("Dark: chart muted #aaaaaa / #2b2b2b", "#aaaaaa", "#2b2b2b"),
 ("Dark: NPS plot ticks #888 / #2b2b2b", "#888888", "#2b2b2b"),
 ("Dark: NPS plot axis labels #aaa / #2b2b2b", "#aaaaaa", "#2b2b2b"),
 ("Dark: artifact dialog info #aaa / #1e1e1e", "#aaaaaa", "#1e1e1e"),
 ("Dark: ToggleSwitch ON green #4caf50 vs OFF #888888 (not text)", "#4caf50", "#888888"),
 ("Dark: white on btn_absent #2e7d32", "#ffffff", "#2e7d32"),
 ("Dark: white on btn_present #d84315", "#ffffff", "#d84315"),
 ("Light: text #222222 / bg #ffffff", "#222222", "#ffffff"),
 ("Light: text_secondary #555555 / bg #ffffff", "#555555", "#ffffff"),
 ("Light: pending #999999 / bg #ffffff", "#999999", "#ffffff"),
 ("Light: #888 (summary/labels) / #ffffff", "#888888", "#ffffff"),
 ("Light: #888 / Fusion light window ~#efefef", "#888888", "#efefef"),
 ("Light: #666 (hint) / #f0f0f0", "#666666", "#f0f0f0"),
 ("Light: section_title #1565c0 / #ffffff", "#1565c0", "#ffffff"),
 ("Light: th #666666 / #ffffff", "#666666", "#ffffff"),
 ("Light: ok #4caf50 / #ffffff (results html, same colors as dark!)", "#4caf50", "#ffffff"),
 ("Light: nc #ff9800 / #ffffff (results html)", "#ff9800", "#ffffff"),
 ("Light: ncg #f44336 / #ffffff (results html)", "#f44336", "#ffffff"),
 ("Light: warning_text #bf360c / warning_bg #fff3e0", "#bf360c", "#fff3e0"),
 ("Light: warning_title #e65100 / warning_bg #fff3e0", "#e65100", "#fff3e0"),
 ("Light: accent button white / #1565c0", "#ffffff", "#1565c0"),
 ("Light: history ok #2e7d32 / #ffffff", "#2e7d32", "#ffffff"),
 ("Light: history nc #ef6c00 / #ffffff", "#ef6c00", "#ffffff"),
 ("Light: history ncg #c62828 / #ffffff", "#c62828", "#ffffff"),
 ("Light: overlay title #ccc / rgba(240,240,240) #f0f0f0  (BUG?)", "#cccccc", "#f0f0f0"),
 ("Light: overlay UH label #ffcc00 / #f0f0f0", "#ffcc00", "#f0f0f0"),
 ("Light: overlay SPB label #00cc00 / #f0f0f0", "#00cc00", "#f0f0f0"),
 ("Light: NPS plot (dark-only styling) ticks #888 / #2b2b2b", "#888888", "#2b2b2b"),
 ("PDF: ok green (0,0.5,0) #008000 / white", "#008000", "#ffffff"),
 ("PDF: NC orange reportlab #ffa500 / white", "#ffa500", "#ffffff"),
 ("PDF: NCG red #ff0000 / white", "#ff0000", "#ffffff"),
 ("PDF: white on orange badge #ffa500", "#ffffff", "#ffa500"),
 ("PDF: white on green badge (0,0.6,0) #009900", "#ffffff", "#009900"),
 ("PDF: footer grey #808080 / white", "#808080", "#ffffff"),
 ("PDF: header grey #666666 / white", "#666666", "#ffffff"),
 ("PDF: title (0.2,0.2,0.3) #33334d / white", "#33334d", "#ffffff"),
 ("PDF: subtitle (0.4,0.4,0.5) #666680 / white", "#666680", "#ffffff"),
]
print(f"{'Couple':<70} {'Ratio':>6}  AA(normal>=4.5)  AA(large>=3)")
for label, fg, bg in pairs:
    r = ratio(fg, bg)
    print(f"{label:<70} {r:6.2f}  {'OUI' if r>=4.5 else 'NON':<15} {'OUI' if r>=3 else 'NON'}")
