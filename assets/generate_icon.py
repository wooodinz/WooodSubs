"""Generates a crisp 32x32 pixel-art WooodSubs Windows .ico icon:
a chunky pixel wooden log with concentric growth rings, a tiny green sprout,
and glowing retro sound-wave arcs radiating out of the cut face.
"""
from pathlib import Path
from PIL import Image


def build_icon(out_path: Path, preview_path: Path | None = None) -> None:
    w, h = 32, 32
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = img.load()

    def put(x: int, y: int, col: tuple[int, int, int, int]) -> None:
        if 0 <= x < w and 0 <= y < h:
            px[x, y] = col

    # Palette
    border = (52, 60, 78, 255)
    border_hi = (74, 86, 110, 255)
    bg_top = (28, 33, 45, 255)
    bg_mid = (22, 26, 36, 255)
    bg_bot = (17, 20, 28, 255)
    shadow = (11, 13, 19, 255)

    outline = (36, 19, 10, 255)
    bark_dark = (88, 48, 25, 255)
    bark_mid = (138, 78, 40, 255)
    bark_hi = (178, 108, 56, 255)
    bark_rim = (208, 138, 82, 255)

    cut_cream = (247, 224, 174, 255)
    cut_mid = (218, 174, 110, 255)
    cut_ring = (168, 115, 62, 255)
    cut_core = (120, 72, 34, 255)

    leaf_dk = (44, 148, 78, 255)
    leaf_lt = (110, 232, 134, 255)
    stem = (72, 116, 46, 255)

    # 1. Rounded pixel badge background (radius ~5 px)
    corner_cut = {
        (0, 0), (1, 0), (2, 0), (3, 0),
        (0, 1), (1, 1),
        (0, 2),
        (0, 3),
    }

    def is_inside(x: int, y: int) -> bool:
        mx = min(x, w - 1 - x)
        my = min(y, h - 1 - y)
        return (mx, my) not in corner_cut

    for y in range(h):
        for x in range(w):
            if not is_inside(x, y):
                continue
            # Check if border pixel (4-neighbor outside)
            is_edge = any(
                not (0 <= x + dx < w and 0 <= y + dy < h and is_inside(x + dx, y + dy))
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))
            )
            if is_edge:
                put(x, y, border_hi if y <= 3 else border)
            else:
                if y < 10:
                    put(x, y, bg_top)
                elif y < 22:
                    put(x, y, bg_mid)
                else:
                    put(x, y, bg_bot)

    # Subtle circular ambient glow behind the sound waves on the right side
    glow_cx, glow_cy = 21.5, 17.0
    for y in range(4, 29):
        for x in range(12, 30):
            if not is_inside(x, y):
                continue
            d2 = (x - glow_cx) ** 2 + (y - glow_cy) ** 2
            if d2 <= 54:
                r, g, b, a = px[x, y]
                put(x, y, (min(255, r + 7), min(255, g + 14), min(255, b + 22), a))

    # 2. Ground shadow beneath the log
    for x in range(4, 18):
        put(x, 24, shadow)
    for x in range(5, 17):
        put(x, 25, shadow)

    # 3. Pixel Wooden Log body (left x=3..15, center y=17, half-height=5 -> y=12..22)
    for y in range(12, 23):
        dy = y - 17
        x_start = 3 if abs(dy) <= 3 else (4 if abs(dy) == 4 else 5)
        x_end = 15
        for x in range(x_start, x_end + 1):
            if y in (12, 22) or x == x_start:
                put(x, y, outline)
            elif y == 13:
                put(x, y, bark_rim)
            elif y in (14, 15):
                put(x, y, bark_hi)
            elif y in (16, 17, 18):
                put(x, y, bark_mid)
            else:
                put(x, y, bark_dark)

    # Extra crisp left-corner outline pixels
    put(4, 13, outline)
    put(4, 21, outline)

    # Pixel wood bark texture lines & knots
    for x, y in ((6, 14), (7, 14), (10, 15), (11, 15), (12, 15)):
        put(x, y, bark_mid)
    for x, y in ((5, 17), (6, 17), (9, 18), (10, 18), (11, 18), (7, 19), (8, 19)):
        put(x, y, bark_dark)
    for x, y in ((6, 16), (7, 16), (9, 16), (11, 17)):
        put(x, y, bark_hi)
    put(8, 15, bark_rim)
    put(12, 20, bark_mid)

    # 4. Oval Cut Wood Face on the right end of the log (center cx=15.5, cy=17)
    oval_pixels = [
        (0, 12), (1, 12),
        (-1, 13), (0, 13), (1, 13), (2, 13),
        (-2, 14), (-1, 14), (0, 14), (1, 14), (2, 14), (3, 14),
        (-2, 15), (-1, 15), (0, 15), (1, 15), (2, 15), (3, 15),
        (-2, 16), (-1, 16), (0, 16), (1, 16), (2, 16), (3, 16),
        (-2, 17), (-1, 17), (0, 17), (1, 17), (2, 17), (3, 17),
        (-2, 18), (-1, 18), (0, 18), (1, 18), (2, 18), (3, 18),
        (-2, 19), (-1, 19), (0, 19), (1, 19), (2, 19), (3, 19),
        (-2, 20), (-1, 20), (0, 20), (1, 20), (2, 20), (3, 20),
        (-1, 21), (0, 21), (1, 21), (2, 21),
        (0, 22), (1, 22),
    ]
    oval_set = {(15 + dx, y) for dx, y in oval_pixels}
    for x, y in oval_set:
        is_rim = any((x + dx, y + dy) not in oval_set for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)))
        if is_rim:
            put(x, y, outline)
        else:
            nd = (((x - 15.5) / 2.2) ** 2 + ((y - 17.0) / 3.8) ** 2) ** 0.5
            if nd < 0.28:
                put(x, y, cut_core)
            elif nd < 0.55:
                put(x, y, cut_mid)
            elif nd < 0.78:
                put(x, y, cut_ring)
            else:
                put(x, y, cut_cream)

    put(15, 13, cut_cream)
    put(16, 13, cut_cream)

    # 5. Cute pixel sprout growing from top of log (around x=8..9, y=8..11)
    put(8, 11, stem)
    put(8, 10, stem)
    for x, y in ((5, 9), (6, 9), (7, 9), (6, 8), (7, 8)):
        put(x, y, leaf_lt if (x, y) in ((6, 8), (7, 8), (5, 9)) else leaf_dk)
    for x, y in ((9, 9), (10, 9), (9, 8), (10, 8), (11, 8)):
        put(x, y, leaf_lt if (x, y) in ((9, 8), (10, 8), (11, 8)) else leaf_dk)
    put(8, 12, outline)

    # 6. Three crisp symmetric pixel sound-wave arcs with clean dark gaps
    waves = [
        # (radius, max_dy, bright_color, shade_color)
        (4.6, 3, (255, 214, 102, 255), (218, 152, 48, 255)),   # inner: warm gold
        (7.8, 6, (104, 242, 200, 255), (48, 178, 146, 255)),   # middle: mint
        (11.1, 8, (94, 200, 248, 255), (44, 132, 204, 255)),   # outer: electric blue
    ]
    cx, cy = 16.5, 17.0
    for rad, max_dy, col_hi, col_lo in waves:
        for dy in range(-max_dy, max_dy + 1):
            val = rad * rad - dy * dy
            if val <= 0:
                continue
            dx = val ** 0.5
            x = int(round(cx + dx))
            y = int(round(cy + dy))
            if x >= 29 or x <= 19:
                continue
            put(x, y, col_hi)
            if abs(dy) <= max_dy - 1 and x - 1 >= 20:
                put(x - 1, y, col_lo if abs(dy) >= max_dy - 2 else col_hi)

    # Crisp pixel music note (♪) floating above the sound waves
    note_hi = (255, 232, 138, 255)
    note_lo = (232, 172, 62, 255)
    for x, y, c in (
        (21, 5, note_hi), (22, 5, note_hi), (23, 6, note_lo),  # flag
        (21, 6, note_hi), (21, 7, note_hi), (21, 8, note_lo),  # stem
        (19, 8, note_hi), (20, 8, note_hi),                    # note head top
        (19, 9, note_lo), (20, 9, note_lo),                    # note head bottom
    ):
        put(x, y, c)

    sizes = [256, 128, 64, 48, 32, 16]
    images = [img.resize((s, s), Image.Resampling.NEAREST) for s in sizes]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        out_path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[1:],
    )
    if preview_path is not None:
        images[0].save(preview_path, format="PNG")
    print(f"Pixel-art WooodSubs icon written to {out_path}")


if __name__ == "__main__":
    assets_dir = Path(__file__).resolve().parent
    build_icon(assets_dir / "wooodsubs.ico", assets_dir / "wooodsubs_icon_preview.png")
