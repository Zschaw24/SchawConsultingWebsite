"""Consignment tag label rendering.

Produces the physical tags that go on inventory: a header block with the item
number, a condition/box band, the listed date, a QR code, and the shop's URL
bar down the right edge.

Two outputs from one layout:
  * ``render_png``  - a raster preview you can eyeball before feeding a roll.
  * ``render_zpl``  - ZPL II for the thermal printer.

Geometry lives in :class:`Stock` (physical) and :class:`Layout` (fractions of
the label face), so re-targeting a different label size is a one-line change.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field, replace

import qrcode
from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------
# Physical stock
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Stock:
    """A roll of die-cut labels, described in the orientation the tag READS.

    ``width_mm`` / ``height_mm`` are the printed face as a person holds it:
    portrait, number at the top. On the roll the label feeds on its side, so
    the renderer rotates by ``feed_rotation`` on the way out to the printer.
    """

    width_mm: float = 30.0
    height_mm: float = 46.0
    dpi: int = 203
    #: Content is laid out portrait but feeds landscape; 90 puts the top of the
    #: tag at the leading edge. Use 270 if the roll comes off the other way.
    feed_rotation: int = 90
    #: Thermal darkness, 0-30. The sample prints looked light in the solid bar.
    darkness: int = 22

    @property
    def px_per_mm(self) -> float:
        return self.dpi / 25.4

    @property
    def width_px(self) -> int:
        return round(self.width_mm * self.px_per_mm)

    @property
    def height_px(self) -> int:
        return round(self.height_mm * self.px_per_mm)


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Layout:
    """Element geometry as fractions of the label face.

    Values transcribed from the marked-up mockup: the header block and the
    condition band run the full content width, the URL bar is flush to the
    right edge and runs to the bottom, and the QR sits hard in the bottom-left
    corner. The open middle is the tagline's.
    """

    margin: float = 0.045

    # Header block: glyph cell | logo + No. + item number
    header_bottom: float = 0.262
    glyph_cell_width: float = 0.295
    #: Fraction of the right header cell reserved for the logo + "No.";
    #: the item number gets the rest, so the two can never overlap.
    logo_zone_width: float = 0.42

    # Condition / box band, then the listed line
    band_bottom: float = 0.330
    listed_bottom: float = 0.392

    # URL bar down the right edge, flush bottom
    bar_left: float = 0.790
    bar_top: float = 0.408

    # QR hard into the bottom-left corner. Sized so its modules land on whole
    # printer dots - at 203dpi this box gives 4 dots per module, which is the
    # difference between a code that scans first time and one that doesn't.
    qr_top: float = 0.650
    qr_right: float = 0.540
    #: The QR sits on its own inset, tighter than the label's margin, so it
    #: reaches the corner and can be torn off in one piece. Watch the die-cut's
    #: corner radius - too small and the arc clips the bottom-left finder
    #: pattern, which is the one thing a scanner cannot recover from.
    qr_margin: float = 0.012

    # Tagline centred in what's left
    tagline_top: float = 0.425
    tagline_bottom: float = 0.625

    # Rules
    rule_heavy: float = 0.010
    rule_light: float = 0.006


#: Condition -> the glyph stamped in the header's left cell.
CONDITION_GLYPHS = {
    "COLLECTIBLE": "T",
    "VERY GOOD": "\u0393",
    "GOOD": "\u2020",
}


@dataclass(frozen=True)
class Tag:
    """The variable content of one tag."""

    number: int
    condition: str
    box: int
    listed: str
    qr_url: str
    code: str
    glyph: str = "T"

    @classmethod
    def build(cls, number: int, condition: str, box: int, listed: str,
              base_url: str = "https://maineconsignment.com/i") -> "Tag":
        condition = condition.upper()
        code = f"{box:02d}-{number:03d}"
        return cls(
            number=number,
            condition=condition,
            box=box,
            listed=listed,
            qr_url=f"{base_url}/{code}",
            code=code,
            glyph=CONDITION_GLYPHS.get(condition, "T"),
        )


# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------

# Only the item number and "No." are set bold. Everything else on the tag is
# regular weight, so the number is the one thing that reads from across a room.
_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
_MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
_SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_SANS_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _font(path: str, px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, max(px, 1))


def _fit(draw: ImageDraw.ImageDraw, text: str, path: str, box_w: int, box_h: int,
         start: int) -> ImageFont.FreeTypeFont:
    """Largest font at ``path`` that fits ``text`` inside ``box_w`` x ``box_h``."""
    size = start
    while size > 4:
        f = _font(path, size)
        l, t, r, b = draw.textbbox((0, 0), text, font=f)
        if r - l <= box_w and b - t <= box_h:
            return f
        size -= 1
    return _font(path, 5)


def _text(draw: ImageDraw.ImageDraw, xy, text, font, fill=0, anchor="la") -> None:
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


# --------------------------------------------------------------------------
# Logo
# --------------------------------------------------------------------------


def _draw_roundel(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float) -> None:
    """The shop mark: a pine and a sun over water, in a ring."""
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=0, width=max(1, round(r * 0.07)))
    inner = r * 0.78
    # water: two strokes across the lower third
    for i, dy in enumerate((0.34, 0.56)):
        y = cy + inner * dy
        half = inner * (0.80 - i * 0.22)
        draw.line([cx - half, y, cx + half, y], fill=0, width=max(1, round(r * 0.07)))
    # pine
    peak = cy - inner * 0.62
    base = cy + inner * 0.18
    half_w = inner * 0.42
    draw.polygon([(cx - inner * 0.16, peak), (cx - half_w, base), (cx + half_w * 0.34, base)], fill=0)
    # sun
    draw.ellipse(
        [cx + inner * 0.30, cy - inner * 0.52, cx + inner * 0.72, cy - inner * 0.10], fill=0
    )


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------


def render_png(tag: Tag, stock: Stock = Stock(), layout: Layout = Layout(),
               scale: int = 1) -> Image.Image:
    """Render one tag, portrait, as a 1-bit-style greyscale image."""
    W = stock.width_px * scale
    H = stock.height_px * scale
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)

    m = round(layout.margin * W)
    x0, x1 = m, W - m
    cw = x1 - x0

    heavy = max(1, round(layout.rule_heavy * W))
    light = max(1, round(layout.rule_light * W))

    # ---- header block ----------------------------------------------------
    hb_top = m
    hb_bot = round(layout.header_bottom * H)
    draw_box = [x0, hb_top, x1, hb_bot]
    d.line([x0, hb_top, x1, hb_top], fill=0, width=heavy)
    d.rectangle(draw_box, outline=0, width=light)

    glyph_x1 = x0 + round(layout.glyph_cell_width * cw)
    d.line([glyph_x1, hb_top, glyph_x1, hb_bot], fill=0, width=light)

    # condition glyph, centred in its cell
    gf = _fit(d, tag.glyph, _MONO, round((glyph_x1 - x0) * 0.66),
              round((hb_bot - hb_top) * 0.62), round(H * 0.16))
    _text(d, ((x0 + glyph_x1) // 2, (hb_top + hb_bot) // 2), tag.glyph, gf, anchor="mm")

    # The right cell splits: logo + "No." on the left, item number on the right.
    rw = x1 - glyph_x1
    hh = hb_bot - hb_top
    num_zone_x0 = glyph_x1 + round(layout.logo_zone_width * rw)

    # logo roundel, top of the logo zone
    lr = hh * 0.28
    _draw_roundel(d, glyph_x1 + rw * 0.21, hb_top + hh * 0.34, lr)

    # "No." on the baseline, under the logo
    nof = _font(_MONO_BOLD, max(6, round(H * 0.042)))
    _text(d, (glyph_x1 + round(rw * 0.07), hb_bot - round(hh * 0.13)), "No.", nof, anchor="ls")

    # item number, right-aligned and dominant, confined to its own zone
    num = str(tag.number)
    num_pad = round(cw * 0.022)
    nf = _fit(d, num, _SANS_BOLD, (x1 - num_pad) - num_zone_x0, round(hh * 0.82),
              round(H * 0.22))
    _text(d, (x1 - num_pad, hb_bot - round(hh * 0.09)), num, nf, anchor="rs")

    # ---- condition / box band -------------------------------------------
    band_bot = round(layout.band_bottom * H)
    d.line([x0, band_bot, x1, band_bot], fill=0, width=heavy)
    bf = _fit(d, f"{tag.condition}  BOX {tag.box}", _MONO, round(cw * 0.94),
              round((band_bot - hb_bot) * 0.62), round(H * 0.060))
    band_mid = (hb_bot + band_bot) // 2
    pad = round(cw * 0.02)
    _text(d, (x0 + pad, band_mid), tag.condition, bf, anchor="lm")
    _text(d, (x1 - pad, band_mid), f"BOX {tag.box}", bf, anchor="rm")

    # ---- listed line -----------------------------------------------------
    listed_bot = round(layout.listed_bottom * H)
    lf = _font(_SANS, max(7, round(H * 0.040)))
    _text(d, (x0 + pad, (band_bot + listed_bot) // 2), f"Listed {tag.listed}", lf, anchor="lm")

    # ---- URL bar: flush right, flush bottom ------------------------------
    bar_x0 = round(layout.bar_left * W)
    bar_y0 = round(layout.bar_top * H)
    d.rectangle([bar_x0, bar_y0, x1, H - m], fill=0)
    url = "MAINECONSIGNMENT.COM"
    bar_w = x1 - bar_x0
    bar_h = (H - m) - bar_y0
    uf = _fit(d, url, _SANS, round(bar_h * 0.90), round(bar_w * 0.62), round(W * 0.10))
    strip = Image.new("L", (round(bar_h * 0.94), round(bar_w * 0.70)), 0)
    sd = ImageDraw.Draw(strip)
    sd.text((strip.width // 2, strip.height // 2), url, font=uf, fill=255, anchor="mm")
    img.paste(strip.rotate(90, expand=True),
              (bar_x0 + (bar_w - strip.height) // 2, bar_y0 + (bar_h - strip.width) // 2))

    # ---- QR, hard into the bottom-left corner ----------------------------
    qm = max(1, round(layout.qr_margin * W))
    qr_y0 = round(layout.qr_top * H)
    qr_x1 = round(layout.qr_right * W)
    qr_side = min(qr_x1 - qm, (H - qm) - qr_y0)
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=10, border=0)
    qr.add_data(tag.qr_url)
    qr.make(fit=True)
    qimg = qr.make_image(fill_color="black", back_color="white").convert("L")
    modules = qimg.size[0] // 10
    box_px = max(1, qr_side // modules)
    qimg = qimg.resize((box_px * modules, box_px * modules), Image.NEAREST)
    img.paste(qimg, (qm, H - qm - qimg.size[1]))

    # ---- code string, reading up the side of the QR ----------------------
    cf = _font(_MONO, max(6, round(H * 0.034)))
    cl, ct, cr, cb = d.textbbox((0, 0), tag.code, font=cf)
    cstrip = Image.new("L", (cr - cl + 4, cb - ct + 4), 255)
    ImageDraw.Draw(cstrip).text((2 - cl, 2 - ct), tag.code, font=cf, fill=0)
    cstrip = cstrip.rotate(90, expand=True)
    img.paste(cstrip, (qm + qimg.size[0] + round(W * 0.018),
                       H - qm - cstrip.size[1]))

    # ---- tagline, centred in the open middle -----------------------------
    tl_x1 = bar_x0 - round(W * 0.03)
    tl_y0, tl_y1 = round(layout.tagline_top * H), round(layout.tagline_bottom * H)
    tf = _fit(d, "something to", _SANS, round((tl_x1 - x0) * 0.94),
              round((tl_y1 - tl_y0) * 0.30), round(H * 0.075))
    cx = (x0 + tl_x1) // 2
    lines = ["Have", "something to", "sell?"]
    lh = (tl_y1 - tl_y0) / len(lines)
    for i, line in enumerate(lines):
        _text(d, (cx, round(tl_y0 + lh * (i + 0.5))), line, tf, anchor="mm")

    return img


def binarize(img: Image.Image, threshold: int = 128) -> Image.Image:
    """Collapse to pure black and pure white.

    A thermal head has no greys: every pixel either burns or it doesn't. Doing
    this before anyone looks at a preview means the mottled edges that
    anti-aliasing produces show up on screen instead of on the roll.
    """
    return img.point(lambda p: 0 if p < threshold else 255, mode="L")


def render_tag(tag: Tag, stock: Stock = Stock(), layout: Layout = Layout(),
               zoom: int = 6) -> Image.Image:
    """One tag exactly as the printer will burn it, magnified for inspection.

    Rendered at native printer resolution and thresholded first, then blown up
    with nearest-neighbour - so what you see is the real dot pattern, not a
    smoothed idealisation of it.
    """
    img = binarize(render_png(tag, stock, layout, scale=1))
    return img.resize((img.width * zoom, img.height * zoom), Image.NEAREST)


def render_preview(tags, stock: Stock = Stock(), layout: Layout = Layout(),
                   scale: int = 4, cols: int = 3, pad: int = 24) -> Image.Image:
    """Lay several tags side by side, each at true printer resolution."""
    imgs = [render_tag(t, stock, layout, zoom=scale) for t in tags]
    w, h = imgs[0].size
    rows = (len(imgs) + cols - 1) // cols
    sheet = Image.new("L", (cols * w + (cols + 1) * pad, rows * h + (rows + 1) * pad), 210)
    for i, im in enumerate(imgs):
        r, c = divmod(i, cols)
        sheet.paste(im, (pad + c * (w + pad), pad + r * (h + pad)))
    return sheet


# --------------------------------------------------------------------------
# ZPL
# --------------------------------------------------------------------------


def render_zpl(tag: Tag, stock: Stock = Stock(), layout: Layout = Layout()) -> str:
    """ZPL II for one tag, as a rotated raster of the same layout.

    Rasterising rather than emitting native ZPL elements keeps the printed tag
    byte-identical to the preview - there is no second layout to drift.
    """
    img = render_png(tag, stock, layout, scale=1)
    img = img.rotate(stock.feed_rotation, expand=True)
    bitmap = img.point(lambda p: 0 if p < 128 else 255, mode="1")

    w, h = bitmap.size
    row_bytes = (w + 7) // 8
    packed = bytearray()
    px = bitmap.load()
    for y in range(h):
        bits = 0
        nbits = 0
        for x in range(w):
            bits = (bits << 1) | (0 if px[x, y] else 1)  # 1 = black
            nbits += 1
            if nbits == 8:
                packed.append(bits)
                bits = nbits = 0
        if nbits:
            packed.append(bits << (8 - nbits))

    hexdata = packed.hex().upper()
    total = row_bytes * h
    return (
        "^XA"
        f"^MD{stock.darkness}"
        f"^PW{w}"
        f"^LL{h}"
        "^LH0,0"
        "^LT0"
        f"^GFA,{total},{total},{row_bytes},{hexdata}"
        "^FS"
        "^PQ1"
        "^XZ"
    )


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
