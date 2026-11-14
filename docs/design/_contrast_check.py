"""Design-batch-2 token contrast self-check (WCAG 2.1 relative luminance).

Run:  python docs/design/_contrast_check.py
Pass criteria: body/UI text >= 4.5:1, large text >= 3:1, non-text border >= 1.4:1 (informational).
"""

from __future__ import annotations


def srgb_to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * srgb_to_linear(r) + 0.7152 * srgb_to_linear(g) + 0.0722 * srgb_to_linear(b)


def ratio(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return round((hi + 0.05) / (lo + 0.05), 2)


CHECKS: list[tuple[str, str, str, str, float]] = [
    # (version, role, fg, bg, threshold)
    ("V4", "正文 on 卡片", "#0F172A", "#FFFFFF", 4.5),
    ("V4", "次要文字 on 页面", "#475569", "#F8FAFC", 4.5),
    ("V4", "弱化标签 on 卡片", "#64748B", "#FFFFFF", 4.5),
    ("V4", "主按钮文字", "#FFFFFF", "#1E40AF", 4.5),
    ("V4", "链接 on 页面", "#1E40AF", "#F8FAFC", 4.5),
    ("V4", "通过胶囊", "#047857", "#ECFDF5", 4.5),
    ("V4", "失败胶囊", "#B91C1C", "#FEF2F2", 4.5),
    ("V4", "超限胶囊", "#B45309", "#FEF3C7", 4.5),
    ("V4", "强调数字 on 卡片", "#B45309", "#FFFFFF", 4.5),
    ("V5", "正文 on 白", "#111827", "#FFFFFF", 4.5),
    ("V5", "次要文字 on 白", "#6B7280", "#FFFFFF", 4.5),
    ("V5", "强调绿 on 白", "#15803D", "#FFFFFF", 4.5),
    ("V5", "主按钮文字", "#FFFFFF", "#15803D", 4.5),
    ("V5", "失败红 on 白", "#B91C1C", "#FFFFFF", 4.5),
    ("V5", "超限琥珀 on 白", "#B45309", "#FFFFFF", 4.5),
    ("V5", "表头灰 on 白", "#6B7280", "#FFFFFF", 4.5),
    ("V6", "正文 on 卡片", "#0B1220", "#FFFFFF", 4.5),
    ("V6", "次要文字 on 页面", "#52606F", "#F6F7F9", 4.5),
    ("V6", "主按钮文字", "#FFFFFF", "#0F172A", 4.5),
    ("V6", "金色强调 on 白", "#A16207", "#FFFFFF", 4.5),
    ("V6", "金色强调 on 浅金", "#92400E", "#FFFBEB", 4.5),
    ("V6", "通过胶囊", "#047857", "#ECFDF5", 4.5),
    ("V6", "失败胶囊", "#B91C1C", "#FEF2F2", 4.5),
    ("V7", "正文 on 卡片", "#111827", "#FFFFFF", 4.5),
    ("V7", "次要文字 on 卡片", "#6B7280", "#FFFFFF", 4.5),
    ("V7", "次要文字 on 页面", "#5F6675", "#F5F5F7", 4.5),
    ("V7", "主按钮文字", "#FFFFFF", "#4F46E5", 4.5),
    ("V7", "浅底强调文字", "#4338CA", "#EEF2FF", 4.5),
    ("V7", "通过胶囊", "#047857", "#ECFDF5", 4.5),
    ("V7", "失败胶囊", "#B91C1C", "#FEF2F2", 4.5),
    ("V7", "超限胶囊", "#B45309", "#FEF3C7", 4.5),
]

failures = [row for row in CHECKS if ratio(row[2], row[3]) < row[4]]
width = max(len(f"{v} · {role}") for v, role, *_ in CHECKS)

print(f"{'token'.ljust(width)}  {'fg':<8} {'bg':<8} {'ratio':>6}  result")
print("-" * (width + 36))
for version, role, fg, bg, threshold in CHECKS:
    value = ratio(fg, bg)
    ok = "PASS" if value >= threshold else f"FAIL (< {threshold})"
    print(f"{f'{version} · {role}'.ljust(width)}  {fg:<8} {bg:<8} {value:>6}  {ok}")

print()
print(f"checked={len(CHECKS)} failed={len(failures)}")
if failures:
    raise SystemExit(1)
