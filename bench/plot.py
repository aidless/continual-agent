"""最小 SVG 折线图（标准库，不引第三方绘图依赖）。"""
from __future__ import annotations

from pathlib import Path

W, H, PAD = 900, 420, 60
COLORS = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed"]


def line_chart(path: Path, series: list[tuple[str, list[float], list[float]]],
               title: str, ylabel: str, ymax: float | None = None) -> None:
    xs = [x for _, sx, _ in series for x in sx]
    ys = [y for _, _, sy in series for y in sy]
    if not xs:
        return
    x0, x1 = min(xs), max(xs) or 1
    y0, y1 = 0.0, ymax or (max(ys) * 1.15 if max(ys) > 0 else 1.0)
    def px(x): return PAD + (x - x0) / max(1e-9, (x1 - x0)) * (W - 2 * PAD)
    def py(y): return H - PAD - (y - y0) / max(1e-9, (y1 - y0)) * (H - 2 * PAD)

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
             f'<rect width="{W}" height="{H}" fill="#fff"/>',
             f'<text x="{PAD}" y="30" font-size="18" font-family="sans-serif">{title}</text>',
             f'<text x="18" y="{H/2}" font-size="12" font-family="sans-serif" transform="rotate(-90 18 {H/2})">{ylabel}</text>',
             f'<line x1="{PAD}" y1="{H-PAD}" x2="{W-PAD}" y2="{H-PAD}" stroke="#888"/>',
             f'<line x1="{PAD}" y1="{PAD}" x2="{PAD}" y2="{H-PAD}" stroke="#888"/>']
    for i, (label, sx, sy) in enumerate(series):
        color = COLORS[i % len(COLORS)]
        pts = " ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in zip(sx, sy))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"/>')
        for x, y in zip(sx, sy):
            parts.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="3" fill="{color}"/>')
        ly = PAD + 18 * i
        parts.append(f'<text x="{W-PAD-180}" y="{ly}" font-size="12" fill="{color}" font-family="sans-serif">{label}</text>')
    parts.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n")
