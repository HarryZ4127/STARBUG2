#!/usr/bin/env python3
"""Render the ISA trade-study page (results/starbug_isa_study.html) from the model's JSON output:
results/digest.json (summarize.py), ~/starbug/width_vs_ports.json, ~/starbug/unroll_sensitivity.json."""
import html
import json
import math
import os

H = os.path.expanduser(os.environ.get("STARBUG_HOME", "~/starbug"))  # WSL/Linux work dir: traces, ELFs, JSON
RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
D = json.load(open(f"{RES}/digest.json"))
WP = json.load(open(f"{H}/width_vs_ports.json"))
US = json.load(open(f"{H}/unroll_sensitivity.json"))

SB1 = "SB1 today: 4-wide, 1 mem op (lane 0), no predictor"
RV = "RV32GC baseline: 1-wide, gshare"
G = D["group_geomean"]
GROUPS = ["float DSP (team Embench suite)", "integer DSP / ML", "ALU / control"]
GSHORT = {"float DSP (team Embench suite)": "Float DSP", "integer DSP / ML": "Integer DSP / ML",
          "ALU / control": "ALU / control"}
GCLS = {"float DSP (team Embench suite)": "s1", "integer DSP / ML": "s2", "ALU / control": "s3"}
SHORT = {
    "Bench": "",
    "+ gshare predictor (as RV32GC)": "+ gshare branch predictor",
    "+ static BTFN prediction": "+ static branch prediction (BTFN)",
    "+ zero-overhead loops": "+ zero-overhead loops",
    "+ 1 LS + 1 load-only, 2 interleaved banks": "+ load-only 2nd path, 2 interleaved banks",
    "+ 1 LS + 1 load-only, conflict-free": "+ load-only 2nd path, no bank conflicts",
    "+ 2 LS, 2 interleaved banks": "+ 2nd full LS path, 2 interleaved banks",
    "+ 2 LS, conflict-free": "+ 2nd full LS path, no bank conflicts",
    "8-wide, 1 mem op": "8-wide, 1 mem op",
    "8-wide, 1 LS + 1 load-only, 2 banks": "8-wide, load-only 2nd path, 2 banks",
    "8-wide, 2 LS, 2 banks": "8-wide, 2 LS paths, 2 banks",
    "4-wide, 1 LS + 1 load-only, 2 banks, gshare, ZOL": "4-wide combined: load-only path + 2 banks + gshare + ZOL",
    "8-wide, 2 LS, 2 banks, gshare, ZOL": "8-wide combined: 2 LS + 2 banks + gshare + ZOL",
    "ceiling: 16-wide, 4 mem ops any lane, conflict-free, gshare, ZOL": "Ceiling (16-wide, 4 mem ops, no limits)",
}
SECTIONS = [
    ("Branches", ["+ gshare predictor (as RV32GC)", "+ static BTFN prediction", "+ zero-overhead loops"]),
    ("Second memory path (4-wide)", ["+ 1 LS + 1 load-only, 2 interleaved banks", "+ 1 LS + 1 load-only, conflict-free",
                                     "+ 2 LS, 2 interleaved banks", "+ 2 LS, conflict-free"]),
    ("8-wide", ["8-wide, 1 mem op", "8-wide, 1 LS + 1 load-only, 2 banks", "8-wide, 2 LS, 2 banks"]),
    ("Combined", ["4-wide, 1 LS + 1 load-only, 2 banks, gshare, ZOL", "8-wide, 2 LS, 2 banks, gshare, ZOL"]),
    ("Reference", ["ceiling: 16-wide, 4 mem ops any lane, conflict-free, gshare, ZOL"]),
]
BENCH_LABEL = {
    "fir_f32_taps256_n128": "FIR f32, 256 taps, 128 samples", "fir_f32_taps256_n1": "FIR f32, 256 taps, 1 sample",
    "lms_f32_taps256_n128": "LMS f32, 256 taps, 128 samples", "lms_f32_taps256_n1": "LMS f32, 256 taps, 1 sample",
    "biquad_cascade_df2T_f32_sos3_n128": "Biquad cascade, 3 stages, 128", "biquad_cascade_df2T_f32_sos3_n1": "Biquad cascade, 3 stages, 1",
    "dct4_2048_f32": "DCT-IV, 2048", "dct4_512_f32": "DCT-IV, 512", "rfft2048_f32": "Real FFT, 2048", "rfft512_f32": "Real FFT, 512",
    "matmult-int": "Integer matrix multiply", "edn": "EDN (FIR, IIR, lattice, DCT)", "dwconv3x3_i8": "Depthwise conv 3×3, int8",
    "fir_q15": "FIR Q15, 32 taps", "crc32": "CRC-32 (table)", "nettle-sha256": "SHA-256",
}
e = html.escape


def x(v):
    return f"{v:.2f}×"


def pct(v):
    return f"{(v - 1) * 100:+.0f}%"


# ---------------------------------------------------------------- chart 1: dot plot
def dotplot():
    rows = []
    for title, names in SECTIONS:
        rows.append(("head", title))
        rows += [("row", n) for n in names]
    lo, hi = 0.9, 2.0
    L, R, top, rh = 300, 690, 34, 26
    W = R + 20
    Hh = top + rh * len(rows) + 30

    def sx(v):
        return L + (min(max(v, lo), hi) - lo) / (hi - lo) * (R - L)

    out = [f'<svg viewBox="0 0 {W} {Hh}" role="img" aria-label="Geomean speedup over SB1 today by configuration and workload group">']
    for t in (1.0, 1.2, 1.4, 1.6, 1.8, 2.0):
        out.append(f'<line x1="{sx(t):.1f}" x2="{sx(t):.1f}" y1="{top - 8}" y2="{Hh - 26}" class="{"axis1" if t == 1.0 else "grid"}"/>')
        out.append(f'<text x="{sx(t):.1f}" y="{Hh - 10}" class="tick" text-anchor="middle">{t:.1f}×</text>')
    out.append(f'<text x="{sx(1.0) + 4:.1f}" y="{top - 14}" class="note">SB1 today = 1.0×</text>')
    y = top
    for kind, val in rows:
        if kind == "head":
            out.append(f'<text x="0" y="{y + 17}" class="rowhead">{e(val)}</text>')
            y += rh
            continue
        allv = G["all"][val]
        out.append(f'<line x1="{sx(1.0):.1f}" x2="{sx(allv):.1f}" y1="{y + 13}" y2="{y + 13}" class="bar"/>')
        out.append(f'<text x="12" y="{y + 17}" class="rowlabel">{e(SHORT[val])}</text>')
        for g in GROUPS:
            v = G[g][val]
            out.append(f'<circle cx="{sx(v):.1f}" cy="{y + 13}" r="5" class="dot {GCLS[g]}"><title>{e(GSHORT[g])}: {v:.2f}×</title></circle>')
        out.append(f'<text x="{min(sx(max(G[g][val] for g in GROUPS)) + 10, R - 4):.1f}" y="{y + 17}" class="val">{allv:.2f}×</text>')
        y += rh
    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------- chart 2: validation scatter (log-log)
def scatter():
    pts = [(r["rtl"], r["model"], r["run"], r["benchmark"]) for r in D["validation"]]
    lo, hi = 2.0, 6.0  # log10 cycles
    S, P = 300, 46
    W = S + P + 20

    def s(v):
        return P + (math.log10(v) - lo) / (hi - lo) * S

    def sy(v):
        return 10 + S - (math.log10(v) - lo) / (hi - lo) * S

    cls = {"scalar on RV32GC": "s3", "hand-VLIW on RV32GC": "s2", "hand-VLIW on Starbug": "s1"}
    out = [f'<svg viewBox="0 0 {W} {S + 56}" role="img" aria-label="Model cycles versus RTL cycles">']
    for k in range(2, 7):
        v = 10 ** k
        lab = {2: "100", 3: "1k", 4: "10k", 5: "100k", 6: "1M"}[k]
        out.append(f'<line x1="{s(v):.1f}" x2="{s(v):.1f}" y1="10" y2="{10 + S}" class="grid"/>')
        out.append(f'<line x1="{P}" x2="{P + S}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" class="grid"/>')
        out.append(f'<text x="{s(v):.1f}" y="{S + 26}" class="tick" text-anchor="middle">{lab}</text>')
        out.append(f'<text x="{P - 6}" y="{sy(v) + 4:.1f}" class="tick" text-anchor="end">{lab}</text>')
    out.append(f'<line x1="{s(100):.1f}" y1="{sy(100):.1f}" x2="{s(1e6):.1f}" y2="{sy(1e6):.1f}" class="axis1"/>')
    for rtl, mod, run, b in pts:
        out.append(f'<circle cx="{s(rtl):.1f}" cy="{sy(mod):.1f}" r="4" class="dot {cls[run]}"><title>{e(b)}, {e(run)}: model {mod:,} vs RTL {rtl:,} ({mod / rtl - 1:+.1%})</title></circle>')
    out.append(f'<text x="{P + S / 2}" y="{S + 46}" class="tick" text-anchor="middle">RTL cycles (Questa)</text>')
    out.append(f'<text x="12" y="{10 + S / 2}" class="tick" text-anchor="middle" transform="rotate(-90 12 {10 + S / 2})">Model cycles</text>')
    out.append("</svg>")
    return "\n".join(out)


def heat(v, lo=1.0, hi=2.0):
    a = max(0.0, min(1.0, (v - lo) / (hi - lo)))
    return f'style="--h:{a * 70:.0f}%"'


# ---------------------------------------------------------------- tables
def width_ports_table():
    wp = D["width_vs_ports"]
    rows = []
    for w in (4, 8, 16):
        cells = "".join(f'<td class="num heat" {heat(wp[f"w{w}_m{m}"], 1.0, 1.7)}>{wp[f"w{w}_m{m}"]:.2f}×</td>' for m in (1, 2, 4))
        rows.append(f"<tr><th scope=\"row\">{w} lanes</th>{cells}</tr>")
    return ('<table class="grid3"><thead><tr><th></th><th scope="col">1 mem op / bundle</th><th scope="col">2</th>'
            '<th scope="col">4</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table>")


def bench_table():
    cols = [("+ gshare predictor (as RV32GC)", "gshare"), ("+ zero-overhead loops", "ZOL"),
            ("+ 1 LS + 1 load-only, 2 interleaved banks", "LS+LD, banks"),
            ("+ 1 LS + 1 load-only, conflict-free", "LS+LD, free"),
            ("+ 2 LS, 2 interleaved banks", "2 LS, banks"), ("+ 2 LS, conflict-free", "2 LS, free"),
            ("8-wide, 2 LS, 2 banks", "8-wide 2 LS"),
            ("4-wide, 1 LS + 1 load-only, 2 banks, gshare, ZOL", "4-wide combined"),
            ("ceiling: 16-wide, 4 mem ops any lane, conflict-free, gshare, ZOL", "Ceiling")]
    head = "".join(f'<th scope="col">{e(c)}</th>' for _, c in cols)
    body = []
    rows = {r["benchmark"]: r for r in D["per_benchmark"]}
    for g in GROUPS:
        body.append(f'<tr class="grp"><th colspan="{len(cols) + 3}" scope="colgroup">{e(GSHORT[g])}</th></tr>')
        for b, r in rows.items():
            if r["group"] != g:
                continue
            cells = "".join(f'<td class="num heat" {heat(r[n], 1.0, 2.2)}>{r[n]:.2f}</td>' for n, _ in cols)
            body.append(f'<tr><th scope="row">{e(BENCH_LABEL[b])}</th><td class="num">{r["sb1_cycles"]:,}</td>'
                        f'<td class="num">{r["sb1_over_rv32gc_scalar"]:.2f}</td>{cells}</tr>')
    return ('<table class="bench"><thead><tr><th scope="col">Kernel</th><th scope="col">SB1 cycles</th>'
            '<th scope="col">SB1 vs RV32GC</th>' + head + "</tr></thead><tbody>" + "".join(body) + "</tbody></table>")


def validation_table():
    rows = {}
    for r in D["validation"]:
        rows.setdefault(r["benchmark"], {})[r["run"]] = r
    runs = ["scalar on RV32GC", "hand-VLIW on RV32GC", "hand-VLIW on Starbug"]
    body = []
    for b, rr in rows.items():
        cells = []
        for run in runs:
            r = rr[run]
            sev = "ok" if abs(r["error"]) <= 0.05 else ("warn" if abs(r["error"]) <= 0.10 else "bad")
            cells.append(f'<td class="num">{r["model"]:,}</td><td class="num">{r["rtl"]:,}</td>'
                         f'<td class="num"><span class="chip {sev}">{r["error"]:+.1%}</span></td>')
        body.append(f'<tr><th scope="row">{e(BENCH_LABEL[b])}</th>{"".join(cells)}</tr>')
    head2 = "".join('<th scope="col">Model</th><th scope="col">RTL</th><th scope="col">Error</th>' for _ in runs)
    head1 = "".join(f'<th colspan="3" scope="colgroup">{e(r)}</th>' for r in runs)
    return (f'<table class="val"><thead><tr><th></th>{head1}</tr><tr><th scope="col">Kernel</th>{head2}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


def unroll_table():
    pick = ["+ gshare predictor (as RV32GC)", "+ zero-overhead loops", "+ 1 LS + 1 load-only, conflict-free"]
    body = []
    for b, r in US.items():
        gain = r["scalar"]["sb1_cycles"] / r["unroll"]["sb1_cycles"]
        cells = "".join(f'<td class="num">{r["scalar"][n]:.2f}</td><td class="num">{r["unroll"][n]:.2f}</td>' for n in pick)
        body.append(f'<tr><th scope="row">{e(BENCH_LABEL[b])}</th><td class="num heat" {heat(gain, 1.0, 2.6)}>{gain:.2f}×</td>{cells}</tr>')
    head1 = "".join(f'<th colspan="2" scope="colgroup">{e(SHORT[n])}</th>' for n in pick)
    head2 = "".join('<th scope="col">-O3</th><th scope="col">unrolled</th>' for _ in pick)
    return (f'<table class="val"><thead><tr><th></th><th>Unrolling alone</th>{head1}</tr>'
            f'<tr><th scope="col">Kernel</th><th scope="col">SB1, -O3 → unrolled</th>{head2}</tr></thead><tbody>{"".join(body)}</tbody></table>')


def header_table():
    h = D["headers"]
    keys = list(h[SB1]["code_bytes"])
    rows = []
    for k in keys:
        a, b = h[SB1], h["8-wide, 2 LS, 2 banks"]
        rows.append(f'<tr><th scope="row">{e(k)}</th><td class="num">{a["code_bytes"][k]:,} B</td>'
                    f'<td class="num">{a["crossing_per_1000_bundles"][k]:.0f}</td>'
                    f'<td class="num">{b["code_bytes"][k]:,} B</td><td class="num">{b["crossing_per_1000_bundles"][k]:.0f}</td></tr>')
    return ('<table class="val"><thead><tr><th></th><th colspan="2" scope="colgroup">4-wide (SB1 schedule)</th>'
            '<th colspan="2" scope="colgroup">8-wide, 2 LS</th></tr><tr><th scope="col">Bundle header</th>'
            '<th scope="col">Hot-code size</th><th scope="col">Bundles straddling a 64 B line, per 1000</th>'
            '<th scope="col">Hot-code size</th><th scope="col">Straddling, per 1000</th></tr></thead><tbody>'
            + "".join(rows) + "</tbody></table>")


# ---------------------------------------------------------------- numbers quoted in the text
ga = G["all"]
n = lambda k: ga[k]
LSLD_B, LSLD_F = n("+ 1 LS + 1 load-only, 2 interleaved banks"), n("+ 1 LS + 1 load-only, conflict-free")
LS2_B, LS2_F = n("+ 2 LS, 2 interleaved banks"), n("+ 2 LS, conflict-free")
W8_1, W8_2B = n("8-wide, 1 mem op"), n("8-wide, 2 LS, 2 banks")
GS, BT, ZOL = n("+ gshare predictor (as RV32GC)"), n("+ static BTFN prediction"), n("+ zero-overhead loops")
COMB4, COMB8, CEIL = n("4-wide, 1 LS + 1 load-only, 2 banks, gshare, ZOL"), n("8-wide, 2 LS, 2 banks, gshare, ZOL"), n("ceiling: 16-wide, 4 mem ops any lane, conflict-free, gshare, ZOL")
VS = D["validation_summary"]
SBRV = D["starbug_vs_scalar_geomean"]
hd = D["headers"][SB1]
k16, k32 = "16-bit hint (SB1)", "32-bit header, 4 ops/header (P-bit chaining)"
code_growth = hd["code_bytes"][k32] / hd["code_bytes"][k16] - 1
pred_q15 = D["predication"]["fir_q15"]["share_of_sb1_cycles"]
pred_other = max(v["share_of_sb1_cycles"] for b, v in D["predication"].items() if b != "fir_q15")
unroll_gains = [US[b]["scalar"]["sb1_cycles"] / US[b]["unroll"]["sb1_cycles"] for b in US]
UNROLL_SET = ["fir_f32_taps256_n128", "fir_f32_taps256_n1", "lms_f32_taps256_n128", "lms_f32_taps256_n1", "edn", "matmult-int", "fir_q15"]
unroll_big = [US[b]["scalar"]["sb1_cycles"] / US[b]["unroll"]["sb1_cycles"] for b in UNROLL_SET]
wp = D["width_vs_ports"]
sbrv_all = D["sb1_over_rv32gc_geomean"]["all"]
mm = {b: r for b, r in ((r["benchmark"], r) for r in D["per_benchmark"])}

CSS = r"""
/* Layout: one reading column (~68ch) with wide tables and charts allowed to scroll in their own boxes. */
:root {
  --bg: #f4f6f9; --surface: #ffffff; --ink: #161e2c; --muted: #566174; --rule: #d6dce5;
  --accent: #2c56c9; --s1: #2c56c9; --s2: #0b8584; --s3: #9a3d8f;
  --ok: #1d7a46; --warn: #a3650a; --bad: #b3261e;
  --display: "Source Serif 4", Georgia, "Times New Roman", serif;
  --body: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Consolas, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0f131a; --surface: #161c26; --ink: #e5e9f0; --muted: #9aa4b5; --rule: #2a3342;
  --accent: #82a2ff; --s1: #82a2ff; --s2: #3cc3c1; --s3: #d488cb;
  --ok: #5fcf8e; --warn: #e3a64b; --bad: #ff8a80; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #0f131a; --surface: #161c26; --ink: #e5e9f0; --muted: #9aa4b5; --rule: #2a3342;
  --accent: #82a2ff; --s1: #82a2ff; --s2: #3cc3c1; --s3: #d488cb;
  --ok: #5fcf8e; --warn: #e3a64b; --bad: #ff8a80; color-scheme: dark }
body { background: var(--bg); color: var(--ink); font: 15px/1.6 var(--body); }
.wrap { max-width: 1060px; margin: 0 auto; padding-inline: 20px; padding-block: 36px 64px; }
.col { max-width: 70ch; }
header { display: grid; gap: 6px; padding-bottom: 22px; border-bottom: 1px solid var(--rule); margin-bottom: 28px; }
.eyebrow { font: 600 12px/1.3 var(--mono); letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }
h1 { font: 600 clamp(30px, 4.6vw, 44px)/1.12 var(--display); margin: 0; text-wrap: balance; letter-spacing: -.01em; }
.lede { font-size: 17px; color: var(--muted); margin: 6px 0 0; max-width: 70ch; }
h2 { font: 600 24px/1.25 var(--display); margin: 44px 0 10px; text-wrap: balance; }
h3 { font: 600 15px/1.35 var(--body); margin: 26px 0 6px; }
p, li { max-width: 70ch; }
ol.findings { padding-left: 1.3em; display: grid; gap: 12px; margin: 0; }
ol.findings li::marker { font: 600 14px var(--mono); color: var(--accent); }
ol.findings b { font-weight: 600; }
.num, td.num { font-family: var(--mono); font-variant-numeric: tabular-nums; }
.figure { background: var(--surface); border: 1px solid var(--rule); border-radius: 6px; padding: 18px 18px 10px; margin: 18px 0 6px; }
.scroll { overflow-x: auto; }
.figure svg { display: block; width: 100%; min-width: 600px; height: auto; }
.figure.small svg { min-width: 320px; max-width: 420px; }
.caption { font-size: 13px; color: var(--muted); margin: 8px 0 0; max-width: 80ch; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 13px; color: var(--muted); margin: 0 0 8px; }
.legend span::before { content: ""; display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; vertical-align: -1px; background: currentColor; }
.legend .s1 { color: var(--s1); } .legend .s2 { color: var(--s2); } .legend .s3 { color: var(--s3); }
.legend .bar::before { border-radius: 2px; width: 16px; height: 4px; vertical-align: 2px; background: var(--muted); }
.legend .lbl { color: var(--muted); }
svg text { fill: var(--ink); font-family: var(--body); }
svg .tick { fill: var(--muted); font: 11px var(--mono); }
svg .note { fill: var(--muted); font: 11px var(--body); }
svg .rowhead { font: 600 12px var(--body); letter-spacing: .04em; text-transform: uppercase; fill: var(--muted); }
svg .rowlabel { font-size: 13px; }
svg .val { font: 600 12px var(--mono); }
svg .grid { stroke: var(--rule); stroke-width: 1; }
svg .axis1 { stroke: var(--muted); stroke-width: 1.2; stroke-dasharray: 3 3; }
svg .bar { stroke: var(--muted); stroke-width: 3; stroke-linecap: round; opacity: .45; }
svg .dot { stroke: var(--surface); stroke-width: 1.5; }
svg .s1 { fill: var(--s1); } svg .s2 { fill: var(--s2); } svg .s3 { fill: var(--s3); }
table { border-collapse: collapse; font-size: 13px; width: 100%; }
th, td { padding: 6px 9px; text-align: left; border-bottom: 1px solid var(--rule); vertical-align: top; }
thead th { font-weight: 600; color: var(--muted); font-size: 12px; border-bottom: 1px solid var(--muted); vertical-align: bottom; }
td.num, thead th:not(:first-child) { text-align: right; }
tbody th { font-weight: 500; white-space: nowrap; }
tr.grp th { font: 600 11px/1.3 var(--mono); text-transform: uppercase; letter-spacing: .08em; color: var(--muted); padding-top: 14px; }
td.heat { background: color-mix(in oklab, var(--accent) var(--h), var(--surface)); }
.tablebox { background: var(--surface); border: 1px solid var(--rule); border-radius: 6px; padding: 6px 10px; margin: 16px 0 6px; overflow-x: auto; }
table.grid3 { max-width: 520px; }
table.grid3 td, table.grid3 th { padding: 10px 14px; }
.chip { display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 12px; border: 1px solid currentColor; }
.chip.ok { color: var(--ok); } .chip.warn { color: var(--warn); } .chip.bad { color: var(--bad); }
.callout { border-left: 3px solid var(--accent); padding: 4px 0 4px 16px; margin: 18px 0; max-width: 70ch; }
.qa { display: grid; gap: 14px; }
.qa div { max-width: 72ch; }
.qa .q { font-weight: 600; }
code, pre { font-family: var(--mono); font-size: 12.5px; }
pre { background: var(--surface); border: 1px solid var(--rule); border-radius: 6px; padding: 12px 14px; overflow-x: auto; line-height: 1.5; }
ul.tight { padding-left: 1.2em; display: grid; gap: 6px; }
a { color: var(--accent); }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (max-width: 560px) { body { font-size: 14.5px; } .wrap { padding-inline: 16px; } h2 { font-size: 21px; } }
"""

page = f"""<title>Starbug ISA Trade Study</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,600&display=swap">
<style>{CSS}</style>
<div class="wrap">
<header>
  <div class="eyebrow">Starbug 2 · ELEC 494 · 13 October 2026</div>
  <h1>Starbug ISA Trade Study</h1>
  <p class="lede">How much each proposed ISA change buys on 16 kernels: the team's 10 Embench DSP kernels plus integer DSP, ML and control code. Every number comes from MPACT instruction traces replayed through a timing model checked against the RTL.</p>
</header>

<section class="col">
<h2>What the numbers say</h2>
<ol class="findings">
  <li><b>A second memory path is the only change that moves the needle, and only if the two accesses don't collide.</b>
    Over today's Starbug (geomean, all 16 kernels): a load-only second path gives {pct(LSLD_F)}, a second full load/store path {pct(LS2_F)},
    when every paired access lands in a different bank. With plain two-bank word interleaving and the data where the linker put it, the same paths give {pct(LSLD_B)} and {pct(LS2_B)}. Bank conflicts take about half the gain.</li>
  <li><b>8-wide is not worth it with one or two memory ports.</b> 8-wide with one memory op per bundle: {pct(W8_1)}.
    8-wide with two load/store paths: {pct(W8_2B)}, about the same as 4-wide with two paths ({pct(LS2_B)}). Width only starts to matter at four memory ops per bundle
    ({x(wp["w4_m4"])} at 4-wide vs {x(wp["w8_m4"])} at 8-wide).</li>
  <li><b>Branch features are small once the compiler unrolls.</b> gshare predictor {pct(GS)}, static backward-taken prediction {pct(BT)},
    zero-overhead loops {pct(ZOL)}. Without unrolling they are worth up to 1.7× on tight loops, which is where the earlier "up to 4×" estimate comes from.</li>
  <li><b>The compiler is the largest lever.</b> Unrolling plus splitting reductions over several accumulators makes the same Starbug run
    {min(unroll_big):.1f}–{max(unroll_big):.1f}× faster on FIR, LMS, EDN, matrix multiply and Q15 FIR. That is more than any hardware change above.</li>
  <li><b>The 32-bit header costs no cycles; predication buys almost none.</b> The header grows the hot code by {code_growth:.0%}.
    About {hd["crossing_per_1000_bundles"][k32] / 10:.0f}% of bundles would straddle a 64-byte I-cache line, which today's fetch logic can't issue as a bundle.
    creg/z predication has something to remove only in the Q15 FIR's saturation ({pred_q15:.1%} of cycles); GCC has already made every other kernel branch-free.</li>
  <li><b>Today's baseline comparison is unequal.</b> The <code>starbug</code> config runs without the branch predictor the <code>rv32gc</code> baseline has
    (<code>BPRED_SUPPORTED = 0</code> vs gshare), so the 1.40× in the weekly report understates the VLIW part.</li>
</ol>
</section>

<h2>Speedup over today's Starbug</h2>
<p class="col">Each row adds one change to SB1 today (4-wide, one memory op per bundle in lane 0, no branch predictor), with the same unrolled code scheduled for that machine. Dots are the geomean of each workload group; the bar and number are the geomean over all 16 kernels.</p>
<div class="figure scroll">
  <div class="legend"><span class="s1">Float DSP (10)</span><span class="s2">Integer DSP / ML (4)</span><span class="s3">ALU / control (2)</span><span class="bar lbl">All 16</span></div>
  {dotplot()}
</div>
<p class="caption">"2 interleaved banks" means even and odd words in separate banks, data left where the linker placed it; two accesses to the same bank in one bundle cost one extra cycle. "No bank conflicts" is the best case: data placed by the compiler, or true dual-ported memory. SB1 today, running the unrolled code, is {sbrv_all:.2f}× faster than the RV32GC baseline running the plain -O3 build.</p>

<h3>Issue width against memory ops per bundle</h3>
<p class="col">Geomean speedup over SB1 today, any lane may hold a memory op, no bank conflicts, branches unchanged. The columns separate what extra lanes buy from what extra memory ports buy.</p>
<div class="tablebox">{width_ports_table()}</div>

<h2>Every kernel</h2>
<p class="col">Speedup over SB1 today for each configuration. "SB1 vs RV32GC" is today's Starbug against the RV32GC baseline running the plain -O3 build. Darker cells are larger speedups.</p>
<div class="tablebox">{bench_table()}</div>
<p class="caption">The 128-sample biquad is bound by its feedback recursion and gains nothing from extra ports. CRC-32 is a serial chain of table lookups. Depthwise conv and FIR stream two arrays in lockstep, so their paired loads keep a fixed bank relationship and, with the linker's placement, many pairs collide every iteration: depthwise conv drops from 1.40× to 1.03×, FIR from 1.63× to 1.24×.</p>

<h2>Answers to the open questions in the 10.09 report</h2>
<div class="qa">
  <div><p class="q">One load/store path plus one load-only path, instead of two full paths?</p>
  <p>Yes. It captures {(LSLD_F - 1) / (LS2_F - 1):.0%} of the two-full-path gain ({pct(LSLD_F)} vs {pct(LS2_F)}). The gap is in kernels that store inside the loop: LMS ({mm["lms_f32_taps256_n128"]["+ 1 LS + 1 load-only, conflict-free"]:.2f}× vs {mm["lms_f32_taps256_n128"]["+ 2 LS, conflict-free"]:.2f}×) and the in-place DCT/FFT butterflies. It also avoids two stores in one cycle, which removes the store–store and load–store hazards between the paths.</p></div>
  <div><p class="q">Should the compiler place data in the interleaved region, or should hardware handle bank conflicts?</p>
  <p>Hardware conflict stalls alone keep roughly half the benefit ({pct(LSLD_B)} instead of {pct(LSLD_F)} for the load-only path). The other half needs placement: put the two streams a kernel reads together (samples and coefficients, input and weights) in opposite banks, or give each its own memory region, as DSPs with separate X/Y memories do. Plan for both: stall on a conflict so the hardware is correct, and have the compiler or linker separate the hot arrays.</p></div>
  <div><p class="q">Is the marginal target of ≥1.2× over current Starbug the right level?</p>
  <p>It is reachable but not automatic. Load-only path with naive interleaving: {x(LSLD_B)}, a miss. Same with gshare and zero-overhead loops: {x(COMB4)}. Load-only path with conflict-free placement: {x(LSLD_F)}. The ideal target should name the data-placement work, because that decides which side of 1.2× the design lands on.</p></div>
  <div><p class="q">P-bit chaining or a second header word for 8-wide?</p>
  <p>On this suite it doesn't matter for size: 8-wide bundles average under four ops, so both encodings produce the same bytes. Given that 8-wide gains about 1% here, the 8-wide encoding can stay unresolved while the second memory path goes first.</p></div>
</div>

<h2>Header encoding and fetch</h2>
<div class="tablebox">{header_table()}</div>
<p class="caption col">Hot code is every basic block the benchmarks execute in their timed region, laid out contiguously. A straddling bundle is one whose bytes cross a 64-byte I-cache line. Today's RTL refuses to issue those as bundles and runs them as scalar code. With the 32-bit header there is no scalar fallback, so the assembler has to align them or the fetch logic has to read across lines; the "continuous fetching" item on the goal list covers this. If each straddle cost one cycle, that would be {hd["crossing_cycle_share"][k32]:.1%} of 4-wide cycles.</p>

<h2>The compiler effect</h2>
<p class="col">The same configurations scheduled from the plain -O3 build instead of the unrolled one. "Unrolling alone" is SB1's speedup from the unrolled build; the other columns are speedups over SB1 on the same build.</p>
<div class="tablebox">{unroll_table()}</div>

<h2>How the model was built and checked</h2>
<div class="col">
<ul class="tight">
  <li><b>Traces.</b> MPACT <code>rv32g_sim</code>, patched to log every retired PC and data address and to stub the 16550 UART, so the cvw bare-metal ELFs run unchanged. All 42 runs pass their own self-checks (SNR or verify).</li>
  <li><b>Code.</b> The team's 10 Embench DSP kernels (<code>cvw lm/fp examples/C/embench_starbug</code>), Embench-IoT crc32, nettle-sha256, matmult-int and edn, plus an int8 depthwise conv and a Q15 FIR written for this study. All are built with GCC 10.2, <code>-O3</code>, rv32imafc. The unrolled build adds Leo's VLIW-seed flags and <code>-fvariable-expansion-in-unroller</code>.</li>
  <li><b>Timing.</b> In-order, lock-step bundles. Load, multiply and FP results are ready after 2 cycles, ALU results after 1 (Wally's <code>LoadStallD</code>, <code>MDUStallD</code>, <code>FPUStallD</code>). A taken branch or misprediction costs 2 cycles. 16 KB 4-way I and D caches, 18-cycle line fill. Lanes follow the RTL: memory, branches and CSRs in lane 0 only.</li>
  <li><b>Scheduling.</b> A list scheduler packs each basic block into bundles for the configuration under test. Within a block, memory ops are treated as independent when their traced addresses never overlap. No software pipelining across iterations: unrolling stands in for it.</li>
</ul>
<div class="callout">Against Leo's Questa runs, the model's median error is {VS["hand-VLIW on Starbug"]["median_abs_error"]:.1%} for hand-written VLIW code on Starbug and {VS["hand-VLIW on RV32GC"]["median_abs_error"]:.1%} on RV32GC ({VS["hand-VLIW on Starbug"]["within_10pct"]} and {VS["hand-VLIW on RV32GC"]["within_10pct"]} of 10 kernels within 10%). The model puts Starbug at {SBRV["model"]:.2f}× over scalar where the RTL measured {SBRV["rtl"]:.2f}×.</div>
</div>
<div class="figure small scroll">
  <div class="legend"><span class="s1">Hand-VLIW on Starbug</span><span class="s2">Hand-VLIW on RV32GC</span><span class="s3">Scalar on RV32GC</span></div>
  {scatter()}
</div>
<p class="caption">Each dot is one kernel and one run; on the dashed line, model equals RTL. The scalar runs differ most because the RTL ELFs came from a newer GCC; the hand-written VLIW kernels are the same code in both.</p>
<div class="tablebox">{validation_table()}</div>

<h2>Limits</h2>
<div class="col">
<ul class="tight">
  <li>The scheduler stands in for a compiler that doesn't exist yet. Hand scheduling or modulo scheduling could find more parallelism; the ceiling row ({x(CEIL)}) shows how much this code holds when resources stop binding.</li>
  <li>Cycle counts only. Area and clock are not modelled: a second memory path or 8 lanes may cost frequency that the 50 MHz target has to absorb.</li>
  <li>Bank conflicts use word interleaving and the addresses the linker produced. A layout tuned per kernel would land between the "2 banks" and "no conflicts" rows.</li>
  <li>The earlier estimates in the weekly report (2.7–3.1× for a second load path, up to 4× for low-overhead branches) are not reproduced here. They were measured against different baselines; numbers from the two studies shouldn't be mixed in the DFD.</li>
</ul>
</div>

<h2>Reproduce</h2>
<pre>cd STARBUG2/mpact_run   # this repo; $STARBUG_HOME defaults to ~/starbug
python3 trace_patch.py &amp;&amp; (cd ~/starbug/rvr-starbug &amp;&amp; ~/bin/bazel build -c opt //riscv:rv32g_sim)
bash build_embench_dsp.sh &amp;&amp; bash build_extra.sh &amp;&amp; bash run_traces.sh
python3 validate.py &amp;&amp; python3 sweep_isa.py &amp;&amp; python3 width_vs_ports.py &amp;&amp; python3 unroll_sensitivity.py
python3 summarize.py &amp;&amp; python3 gen_report.py</pre>
<p class="caption">Machine configurations live in <code>vliw_model.py</code> (<code>CONFIGS</code>); adding a row there and rerunning the last three commands adds it to this page.</p>
</div>
"""
open(f"{RES}/starbug_isa_study.html", "w", encoding="utf-8").write(page)
print("wrote", f"{RES}/starbug_isa_study.html", len(page))
