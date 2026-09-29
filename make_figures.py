"""Regenerate docs/*.svg and results/*.csv."""
import os
import re
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from plcsim.scenarios import normal, pump1_fails, storm, L

plt.rcParams.update({"svg.fonttype": "none", "svg.hashsalt": "fixed", "font.family": "DejaVu Sans", "font.size": 9,
                     "axes.spines.top": False, "axes.spines.right": False})
NAVY, GOLD, RED, TEAL, GREY = "#1F3864", "#C9971C", "#B3261E", "#2E7D6B", "#777777"


def compact_svg(path):
    s = open(path).read()
    s = re.sub(r"<metadata>.*?</metadata>\s*", "", s, flags=re.S)
    s = re.sub(r"-?\d+\.\d{2,}", lambda m: f"{float(m.group()):.1f}".rstrip("0").rstrip("."), s)
    s = re.sub(r"\n\s+", "\n", s)
    open(path, "w").write(s)


os.makedirs("docs", exist_ok=True)
os.makedirs("results", exist_ok=True)

cases = [("Normal day: 8 h, lead pump chosen by run hours", normal(8.0), 60.0, (0.0, 2.2), 8, 80),
         ("Pump 1 contactor fails to close", pump1_fails(), 60.0, (0.6, 2.2), 1, 8),
         ("Storm inflow of 190 m3/h against 180 m3/h capacity", storm(), 60.0, (0.5, 4.1), 3.5, 80)]
fig = plt.figure(figsize=(8.6, 10.0))
gs = fig.add_gridspec(6, 1, height_ratios=[3, 0.9, 3, 0.9, 3, 0.9], hspace=1.05)
for k, (title, s, unit, ylim, hrs, step) in enumerate(cases):
    a = fig.add_subplot(gs[2 * k])
    b = fig.add_subplot(gs[2 * k + 1], sharex=a)
    lg = s.log[::step]
    t = [x[0] / 3600.0 for x in lg]
    a.plot(t, [x[1] for x in lg], color=NAVY, lw=1.6)
    for name, lab, c in (("LEAD_ON", "lead on", TEAL), ("LAG_ON", "lag on", GOLD), ("HIGH", "high", RED), ("OFF", "all off", GREY)):
        if ylim[0] <= L[name] <= ylim[1]:
            a.axhline(L[name], color=c, lw=0.8, ls="--")
            a.text(t[-1], L[name], f" {lab}", color=c, fontsize=7.5, va="center")
    a.set_ylim(*ylim)
    a.set_ylabel("Level (m)")
    a.set_title(title, loc="left", fontsize=10, fontweight="bold")
    plt.setp(a.get_xticklabels(), visible=False)
    for col, y0, c in ((2, 0.55, NAVY), (3, 0.05, GOLD)):
        spans, on = [], None
        for x in s.log[::4]:
            run = x[col] > 0.5
            if run and on is None:
                on = x[0] / 3600.0
            if not run and on is not None:
                spans.append((on, x[0] / 3600.0 - on))
                on = None
        if on is not None:
            spans.append((on, s.log[-1][0] / 3600.0 - on))
        b.broken_barh(spans, (y0, 0.4), color=c)
    b.set_yticks([])
    b.set_ylim(0, 1)
    b.spines["left"].set_visible(False)
    b.text(-0.005, 0.75, "P1", transform=b.transAxes, ha="right", va="center", fontsize=8, color=NAVY)
    b.text(-0.005, 0.2, "P2", transform=b.transAxes, ha="right", va="center", fontsize=8, color=GOLD)
    if k == 1:
        a.annotate("fault latched,\nstandby starts", xy=(t[next(i for i, x in enumerate(lg) if x[4]["fault1"])], 1.8),
                   xytext=(0.15, 2.05), fontsize=7.5, color=RED, arrowprops=dict(arrowstyle="->", color=RED))
    if k == 2:
        i = next(i for i, x in enumerate(lg) if x[4]["alarm_hh"])
        a.annotate("high-high alarm latches", xy=(t[i], lg[i][1]), xytext=(0.3, 3.85), fontsize=7.5, color=RED,
                   arrowprops=dict(arrowstyle="->", color=RED))
    b.set_xlim(0, hrs)
    b.set_xlabel("Time (h)")
fig.savefig("docs/scenarios.svg", bbox_inches="tight")
compact_svg("docs/scenarios.svg")
plt.close(fig)

n = cases[0][1]
with open("results/normal_day.csv", "w") as fh:
    fh.write("pump,starts,run_hours\n")
    for k, p in enumerate(n.pumps, 1):
        fh.write(f"{k},{len(p.starts)},{p.hours:.2f}\n")
print("figures in docs/, tables in results/")
