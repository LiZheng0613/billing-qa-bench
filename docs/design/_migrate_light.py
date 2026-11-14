import pathlib
import re

ROOT = pathlib.Path(r"D:\project\batch_test\frontend\src")
SKIP = {"LoginView.vue", "AppShell.vue"}

MAP = {
    # text
    "text-slate-100": "text-slate-900",
    "text-slate-200": "text-slate-800",
    "text-slate-300": "text-slate-700",
    "text-slate-400": "text-slate-600",
    "text-slate-600": "text-slate-500",
    "text-slate-700": "text-slate-300",
    # surfaces
    "bg-slate-950/70": "bg-white/85",
    "bg-slate-950/60": "bg-white",
    "bg-slate-950/50": "bg-slate-50/80",
    "bg-slate-950/40": "bg-slate-50",
    "bg-slate-950/20": "bg-slate-50",
    "bg-slate-950": "bg-slate-50",
    "bg-slate-900/70": "bg-white",
    "bg-slate-900/60": "bg-white",
    "bg-slate-900/50": "bg-white",
    "bg-slate-900/40": "bg-slate-50",
    "bg-slate-900/30": "bg-slate-50",
    "bg-slate-900": "bg-white",
    "bg-slate-800/60": "bg-slate-100",
    "bg-slate-800/50": "bg-slate-100",
    "bg-slate-800/40": "bg-slate-100/70",
    "bg-slate-800/30": "bg-slate-100/70",
    "bg-slate-800": "bg-slate-100",
    "bg-slate-700/20": "bg-slate-100",
    # borders
    "border-slate-800/80": "border-slate-200",
    "border-slate-800/70": "border-slate-200",
    "border-slate-800/60": "border-slate-200",
    "border-slate-800/50": "border-slate-200",
    "border-slate-800": "border-slate-200",
    "border-slate-700": "border-slate-300",
    "border-slate-600/40": "border-slate-300",
    "border-slate-600": "border-slate-300",
    # accent text
    "text-indigo-200": "text-indigo-700",
    "text-indigo-300": "text-indigo-600",
    "text-indigo-400": "text-indigo-600",
    "text-emerald-200": "text-emerald-700",
    "text-emerald-300": "text-emerald-700",
    "text-emerald-400": "text-emerald-600",
    "text-rose-200": "text-rose-700",
    "text-rose-300": "text-rose-600",
    "text-rose-400": "text-rose-600",
    "text-amber-200": "text-amber-700",
    "text-amber-300": "text-amber-700",
    "text-amber-400": "text-amber-600",
    "text-sky-200": "text-sky-700",
    "text-sky-300": "text-sky-700",
    "text-sky-400": "text-sky-600",
    "text-cyan-300": "text-cyan-700",
    "text-cyan-400": "text-cyan-600",
    "text-violet-300": "text-violet-700",
    "text-fuchsia-300": "text-fuchsia-700",
    "text-orange-300": "text-orange-700",
    # accent surfaces
    "bg-indigo-600/20": "bg-indigo-50",
    "bg-indigo-500/20": "bg-indigo-50",
    "bg-indigo-500/15": "bg-indigo-50",
    "bg-indigo-500/10": "bg-indigo-50",
    "bg-indigo-500/5": "bg-indigo-50/60",
    "bg-indigo-400/20": "bg-indigo-50",
    "bg-indigo-500/80": "bg-indigo-500",
    "bg-emerald-500/15": "bg-emerald-50",
    "bg-emerald-500/10": "bg-emerald-50",
    "bg-emerald-500/5": "bg-emerald-50/60",
    "bg-rose-500/15": "bg-rose-50",
    "bg-rose-500/10": "bg-rose-50",
    "bg-rose-500/5": "bg-rose-50/60",
    "bg-amber-500/15": "bg-amber-50",
    "bg-amber-500/10": "bg-amber-50",
    "bg-amber-500/5": "bg-amber-50/60",
    "bg-sky-500/10": "bg-sky-50",
    # accent borders
    "border-indigo-500/40": "border-indigo-200",
    "border-indigo-500/30": "border-indigo-200",
    "border-indigo-500/20": "border-indigo-100",
    "border-emerald-500/30": "border-emerald-200",
    "border-emerald-500/25": "border-emerald-200",
    "border-rose-500/40": "border-rose-200",
    "border-rose-500/30": "border-rose-200",
    "border-amber-500/40": "border-amber-200",
    "border-amber-500/30": "border-amber-200",
    "border-sky-500/40": "border-sky-200",
    "border-sky-500/30": "border-sky-200",
    # gradient / ring / shadow
    "to-cyan-400": "to-indigo-600",
    "ring-indigo-500/30": "ring-indigo-200",
    "shadow-indigo-500/30": "shadow-indigo-500/25",
}

KEYS = sorted(MAP, key=len, reverse=True)
PATTERN = re.compile("|".join(re.escape(k) for k in KEYS))

changed = []
for path in ROOT.rglob("*.vue"):
    if path.name in SKIP:
        continue
    text = path.read_text(encoding="utf-8")
    original = text
    text = text.replace("bg-slate-950/80", "bg-slate-900/40")
    text = PATTERN.sub(lambda m: MAP[m.group(0)], text)
    if text != original:
        path.write_text(text, encoding="utf-8")
        changed.append(path.name)

print("changed:", len(changed))
print("\n".join(changed))
