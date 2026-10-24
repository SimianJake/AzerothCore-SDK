"""Dependency-free SVG plots of paths (top-down, world X/Y), for checking FindPath and travel() results."""

from __future__ import annotations

from collections.abc import Sequence

Point = tuple[float, float, float]


def paths_svg(layers: Sequence[tuple[str, Sequence[Point]]], *, markers: Sequence[tuple[str, Point]] = (),
              size: int = 800, title: str = "") -> str:
    """layers: (css colour, points) polylines; markers: (label, point). WoW's X grows north, Y grows west,
    so north is drawn up and west to the left."""
    pts = [p for _, layer in layers for p in layer] + [p for _, p in markers]
    if not pts:
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}"/>'
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    pad = 20.0
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    scale = (size - 2 * pad) / span

    def to_screen(p: Point) -> tuple[float, float]:
        # screen x: west to the left (larger Y further left); screen y: north up (larger X higher).
        return pad + (max(ys) - p[1]) * scale, pad + (max(xs) - p[0]) * scale

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">',
           f'<rect width="{size}" height="{size}" fill="#fafafa"/>']
    if title:
        out.append(f'<text x="{pad}" y="14" font-family="sans-serif" font-size="12">{title}</text>')
    for colour, layer in layers:
        if len(layer) >= 2:
            d = " ".join(f"{x:.1f},{y:.1f}" for x, y in map(to_screen, layer))
            out.append(f'<polyline points="{d}" fill="none" stroke="{colour}" stroke-width="2"/>')
    for label, p in markers:
        x, y = to_screen(p)
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="#d33"/>')
        out.append(f'<text x="{x + 7:.1f}" y="{y - 7:.1f}" font-family="sans-serif" font-size="12">{label}</text>')
    out.append(f'<text x="{pad}" y="{size - 6}" font-family="sans-serif" font-size="10" fill="#666">'
               f'north up, west left; {span:.0f} yd across</text>')
    out.append("</svg>")
    return "\n".join(out)
