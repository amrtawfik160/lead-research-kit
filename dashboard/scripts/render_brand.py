#!/usr/bin/env python3
"""Render Veinore mark, lockups, favicons and the Open Graph image.

Brand geometry only. No workspace data and no secrets.
"""
from __future__ import annotations

from pathlib import Path

from fontTools.misc.transform import Transform
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
BRAND = PUBLIC / "brand"
FONT = Path("/usr/share/fonts/truetype/macos/Inter-SemiBold.ttf")
FONT_MED = Path("/usr/share/fonts/truetype/macos/Inter-Medium.ttf")

DARK = "#141518"
LIGHT = "#f2f3f5"
MUTED = "#8a8f98"
BG = "#08090a"
MARK = "M6.5 7.25 16 25.75 25.5 7.25"


def word_path(text: str, size: float, x: float, baseline: float, tracking: float = -0.045) -> tuple[str, float]:
    font = TTFont(FONT)
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap()
    units = font["head"].unitsPerEm
    scale = size / units
    paths: list[str] = []
    cursor = x
    for char in text:
        name = cmap[ord(char)]
        pen = SVGPathPen(glyph_set)
        glyph_set[name].draw(TransformPen(pen, Transform(scale, 0, 0, -scale, cursor, baseline)))
        commands = pen.getCommands()
        if commands:
            paths.append(commands)
        cursor += glyph_set[name].width * scale + size * tracking
    font.close()
    return " ".join(paths), cursor


def svg_lockup(fill: str) -> str:
    path, end = word_path("Veinore", 20, 40, 23)
    width = round(end + 6, 2)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 32" fill="none" role="img" aria-label="Veinore">
  <path d="{MARK}" stroke="{fill}" stroke-width="3.75" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="{path}" fill="{fill}"/>
</svg>
"""


def svg_mark() -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" fill="none" role="img" aria-label="Veinore">
  <path d="{MARK}" stroke="currentColor" stroke-width="3.75" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""


def draw_mark(draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float], color: tuple[int, int, int, int], width: int) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    left = (x0 + w * 0.20, y0 + h * 0.22)
    bottom = (x0 + w * 0.50, y0 + h * 0.80)
    right = (x0 + w * 0.80, y0 + h * 0.22)
    draw.line([left, bottom, right], fill=color, width=width, joint="curve")
    radius = width / 2
    for point in (left, bottom, right):
        draw.ellipse((point[0] - radius, point[1] - radius, point[0] + radius, point[1] + radius), fill=color)


def icon(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (8, 9, 10, 255))
    draw = ImageDraw.Draw(image)
    pad = size * 0.16
    stroke = max(2, round(size * 0.092))
    draw_mark(draw, (pad, pad, size - pad, size - pad), (242, 243, 245, 255), stroke)
    return image


def lockup_png(dark_bg: bool, scale: int = 4) -> Image.Image:
    font = ImageFont.truetype(FONT, 20 * scale)
    width = int(40 * scale + font.getlength("Veinore") + 8 * scale)
    image = Image.new("RGBA", (width, 32 * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    ink = (242, 243, 245, 255) if dark_bg else (20, 21, 24, 255)
    stroke = int(round(3.75 * scale))
    draw_mark(draw, (0, 0, 32 * scale, 32 * scale), ink, stroke)
    draw.text((40 * scale, 3.2 * scale), "Veinore", font=font, fill=ink)
    return image


def og_image() -> Image.Image:
    image = Image.new("RGB", (1200, 630), (8, 9, 10))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((72, 214, 200, 342), radius=28, fill=(16, 17, 18))
    draw_mark(draw, (88, 230, 184, 326), (242, 243, 245, 255), 14)
    title = ImageFont.truetype(FONT, 84)
    sub = ImageFont.truetype(FONT_MED, 32)
    draw.text((232, 214), "Veinore", font=title, fill=(242, 243, 245))
    draw.text((236, 318), "Evidence-first lead research", font=sub, fill=(138, 143, 152))
    return image


def main() -> None:
    BRAND.mkdir(parents=True, exist_ok=True)
    (BRAND / "mark.svg").write_text(svg_mark(), encoding="utf-8")
    (BRAND / "logo-light.svg").write_text(svg_lockup(DARK), encoding="utf-8")
    (BRAND / "logo-dark.svg").write_text(svg_lockup(LIGHT), encoding="utf-8")
    icon(16).save(PUBLIC / "favicon-16.png")
    icon(32).save(PUBLIC / "favicon-32.png")
    icon(180).save(PUBLIC / "apple-touch-icon.png")
    icon(512).save(PUBLIC / "icon-512.png")
    og_image().save(PUBLIC / "og.png", quality=95)
    lockup_png(False).save(BRAND / "logo-light.png")
    lockup_png(True).save(BRAND / "logo-dark.png")
    print("wrote brand assets")


if __name__ == "__main__":
    main()
