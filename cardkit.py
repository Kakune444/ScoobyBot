"""Moteur de layout pour les cards stats (PIL) — un seul PNG par commande,
au lieu d'un embed + des graphiques envoyés séparément.

Rendu en 2x puis downscale (anti-aliasing gratuit sur les coins arrondis et
le texte), palette reprise du skill dataviz (references/palette.md).

Aucun emoji Unicode n'est dessiné dans l'image : DejaVu Sans (la police
embarquée par matplotlib, réutilisée ici pour un rendu identique partout)
ne contient pas les glyphes emoji modernes et les afficherait en tofu. Les
émojis restent réservés au texte de message Discord (rendu nativement par
le client), jamais à l'intérieur du PNG.
"""

import io
import os
from dataclasses import dataclass
from typing import Callable, Optional

import matplotlib

matplotlib.use("Agg")  # doit précéder l'import de pyplot : pas de serveur d'affichage sur Railway
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw, ImageFont, ImageOps

SCALE = 2  # facteur de sur-échantillonnage

PAGE_BG_HEX = "#0d0d0d"
CARD_SURFACE_HEX = "#1a1a19"
CHIP_FILL_HEX = "#383835"
GRIDLINE_HEX = "#2c2c2a"
BASELINE_HEX = "#383835"
INK_PRIMARY_HEX = "#ffffff"
INK_SECONDARY_HEX = "#c3c2b7"
INK_MUTED_HEX = "#898781"
SERIES_MESSAGES_HEX = "#3987e5"
SERIES_VOICE_HEX = "#d95926"


def _hex_to_rgba(value: str, alpha: int = 255) -> tuple:
    value = value.lstrip("#")
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return (r, g, b, alpha)


PAGE_BG = _hex_to_rgba(PAGE_BG_HEX)
CARD_SURFACE = _hex_to_rgba(CARD_SURFACE_HEX)
CHIP_FILL = _hex_to_rgba(CHIP_FILL_HEX)
INK_PRIMARY = _hex_to_rgba(INK_PRIMARY_HEX)
INK_SECONDARY = _hex_to_rgba(INK_SECONDARY_HEX)
INK_MUTED = _hex_to_rgba(INK_MUTED_HEX)
CARD_BORDER = (255, 255, 255, 26)  # rgba(255,255,255,0.10)

CANVAS_WIDTH = 1480
OUTER_PAD = 48
GAP = 24
CONTENT_WIDTH = CANVAS_WIDTH - 2 * OUTER_PAD


def _s(n: float) -> int:
    return round(n * SCALE)


def split_width(width: float, n: int, gap: float = GAP) -> float:
    """Largeur (logique) de n panneaux égaux côte à côte sur `width`."""
    return (width - gap * (n - 1)) / n


_FONT_CACHE: dict = {}


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    key = (bold, size)
    if key not in _FONT_CACHE:
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        path = os.path.join(matplotlib.get_data_path(), "fonts", "ttf", name)
        _FONT_CACHE[key] = ImageFont.truetype(path, _s(size))
    return _FONT_CACHE[key]


def _truncate(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "…"
    while text and draw.textlength(text + ellipsis, font=font) > max_width:
        text = text[:-1]
    return (text + ellipsis) if text else ellipsis


def _card_surface(width: float, height: float, radius: float = 24, border: bool = True):
    img = Image.new("RGBA", (_s(width), _s(height)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r = _s(radius)
    draw.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=r, fill=CARD_SURFACE)
    if border:
        draw.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=r, outline=CARD_BORDER, width=max(1, _s(1)))
    return img, draw


def _round_and_border(image: Image.Image, radius: float = 24) -> Image.Image:
    r = _s(radius)
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=r, fill=255)
    out = Image.new("RGBA", image.size, (0, 0, 0, 0))
    out.paste(image, (0, 0), mask)
    draw = ImageDraw.Draw(out)
    draw.rounded_rectangle((0, 0, out.width - 1, out.height - 1), radius=r, outline=CARD_BORDER, width=max(1, _s(1)))
    return out


def _circle_crop(image: Image.Image, diameter: int) -> Image.Image:
    fitted = ImageOps.fit(image.convert("RGBA"), (diameter, diameter), Image.LANCZOS)
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter, diameter), fill=255)
    out = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    out.paste(fitted, (0, 0), mask)
    return out


# ---------------------------------------------------------------------------
# Blocs de layout
# ---------------------------------------------------------------------------

def render_header(width: float, icon_image: Optional[Image.Image], title: str, subtitle_lines: list[str]) -> Image.Image:
    icon_d = 112
    pad = 32
    text_h = 44 + 12 + len(subtitle_lines) * 28
    height = max(icon_d, text_h) + pad * 2
    img, draw = _card_surface(width, height)

    icon_box = _s(icon_d)
    icon_x = _s(pad)
    icon_y = (img.height - icon_box) // 2
    if icon_image is not None:
        avatar = _circle_crop(icon_image, icon_box)
        img.alpha_composite(avatar, (icon_x, icon_y))
    else:
        draw.ellipse((icon_x, icon_y, icon_x + icon_box, icon_y + icon_box), fill=CHIP_FILL)
        letter = (title[:1] or "?").upper()
        draw.text((icon_x + icon_box // 2, icon_y + icon_box // 2), letter, font=_font(True, 48), fill=INK_PRIMARY, anchor="mm")

    text_x = icon_x + icon_box + _s(pad)
    text_top = (img.height - _s(text_h)) // 2
    draw.text((text_x, text_top), title, font=_font(True, 44), fill=INK_PRIMARY, anchor="la")
    y = text_top + _s(44 + 12)
    for line in subtitle_lines:
        draw.text((text_x, y), line, font=_font(False, 22), fill=INK_MUTED, anchor="la")
        y += _s(28)
    return img


@dataclass
class StatTile:
    label: str
    value: str
    caption: str
    secondary: str = ""


def render_stat_row(width: float, tiles: list[StatTile], height: float = 170) -> Image.Image:
    n = len(tiles)
    tile_w = split_width(width, n)
    img = Image.new("RGBA", (_s(width), _s(height)), (0, 0, 0, 0))
    x = 0.0
    pad = 24
    for tile in tiles:
        card, draw = _card_surface(tile_w, height)
        draw.text((_s(pad), _s(pad)), tile.label.upper(), font=_font(True, 20), fill=INK_SECONDARY, anchor="la")
        draw.text((_s(pad), _s(64)), tile.value, font=_font(True, 56), fill=INK_PRIMARY, anchor="la")
        draw.text((_s(pad), _s(128)), tile.caption, font=_font(False, 18), fill=INK_MUTED, anchor="la")
        if tile.secondary:
            draw.text((_s(pad), _s(150)), tile.secondary, font=_font(False, 19), fill=INK_SECONDARY, anchor="la")
        img.alpha_composite(card, (_s(x), 0))
        x += tile_w + GAP
    return img


def render_ranking_card(width: float, title: str, entries: list[tuple], empty_text: str = "Pas encore de données", max_rows: int = 10) -> Image.Image:
    entries = entries[:max_rows]
    header_h = 56
    row_h = 40
    pad = 24
    n_rows = max(len(entries), 1)
    height = header_h + n_rows * row_h + pad
    img, draw = _card_surface(width, height)
    draw.text((_s(pad), _s(pad)), title.upper(), font=_font(True, 22), fill=INK_SECONDARY, anchor="la")

    if not entries:
        cy = _s(header_h) + _s(row_h) // 2
        draw.text((img.width // 2, cy), empty_text, font=_font(False, 20), fill=INK_MUTED, anchor="mm")
        return img

    rank_font = _font(True, 20)
    name_font = _font(False, 22)
    value_font = _font(True, 22)
    for i, (name, value) in enumerate(entries, start=1):
        row_cy = _s(header_h) + _s(row_h) * (i - 1) + _s(row_h) // 2
        draw.text((_s(pad), row_cy), str(i), font=rank_font, fill=INK_MUTED, anchor="lm")
        name_x = _s(pad + 36)
        value_w = draw.textlength(value, font=value_font)
        max_name_w = img.width - name_x - _s(pad) - int(value_w) - _s(16)
        shown_name = _truncate(name, name_font, max_name_w, draw)
        draw.text((name_x, row_cy), shown_name, font=name_font, fill=INK_PRIMARY, anchor="lm")
        draw.text((img.width - _s(pad), row_cy), value, font=value_font, fill=INK_SECONDARY, anchor="rm")
    return img


def render_chip_row(width: float, title: str, chips: list[str], empty_text: str = "Pas encore de données") -> Image.Image:
    pad = 24
    header_h = 40
    chip_h = 40
    chip_gap = 12
    line_gap = 12

    probe = Image.new("RGBA", (10, 10))
    pdraw = ImageDraw.Draw(probe)
    font = _font(False, 22)
    max_line_w = _s(width) - 2 * _s(pad)

    lines: list[list[tuple]] = [[]]
    line_w = 0
    for chip in chips:
        chip_w = int(pdraw.textlength(chip, font=font)) + _s(28)
        if lines[-1] and line_w + chip_w > max_line_w:
            lines.append([])
            line_w = 0
        lines[-1].append((chip, chip_w))
        line_w += chip_w + _s(chip_gap)

    n_lines = len(lines) if chips else 1
    height = header_h + pad + n_lines * chip_h + max(0, n_lines - 1) * line_gap
    img, draw = _card_surface(width, height)
    draw.text((_s(pad), _s(pad)), title.upper(), font=_font(True, 22), fill=INK_SECONDARY, anchor="la")

    if not chips:
        cy = _s(header_h + pad) + _s(chip_h) // 2
        draw.text((img.width // 2, cy), empty_text, font=_font(False, 20), fill=INK_MUTED, anchor="mm")
        return img

    y = _s(header_h + pad)
    for line in lines:
        x = _s(pad)
        for chip, chip_w in line:
            draw.rounded_rectangle((x, y, x + chip_w, y + _s(chip_h)), radius=_s(chip_h / 2), fill=CHIP_FILL)
            draw.text((x + chip_w / 2, y + _s(chip_h) / 2), chip, font=font, fill=INK_PRIMARY, anchor="mm")
            x += chip_w + _s(chip_gap)
        y += _s(chip_h + line_gap)
    return img


def render_empty_panel(width: float, height: float, text: str = "Pas encore de données sur cette période") -> Image.Image:
    img, draw = _card_surface(width, height)
    draw.text((img.width // 2, img.height // 2), text, font=_font(False, 22), fill=INK_MUTED, anchor="mm")
    return img


def _style_axes(ax) -> None:
    ax.set_facecolor(CARD_SURFACE_HEX)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color(BASELINE_HEX)
    ax.tick_params(colors=INK_MUTED_HEX, labelsize=9)
    ax.set_axisbelow(True)
    ax.xaxis.grid(False)


def wrap_chart(width: float, height: float, draw_fn: Callable, radius: float = 24, nrows: int = 1, sharex: bool = False) -> Image.Image:
    """draw_fn(fig, axes) : trace le contenu (bar/line + titre) sur une liste d'axes déjà stylés
    (toujours une liste, même pour nrows=1, pour que les tracés multi-panneaux — ex. petits
    multiples messages/vocal, jamais un double axe Y — utilisent la même signature)."""
    dpi = 100 * SCALE
    fig, axes = plt.subplots(nrows, 1, figsize=(width / 100, height / 100), dpi=dpi, sharex=sharex)
    fig.patch.set_facecolor(CARD_SURFACE_HEX)
    axes_list = list(axes) if nrows > 1 else [axes]
    for ax in axes_list:
        _style_axes(ax)
    draw_fn(fig, axes_list)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=CARD_SURFACE_HEX)
    plt.close(fig)
    buf.seek(0)
    chart = Image.open(buf).convert("RGBA")
    target = (_s(width), _s(height))
    if chart.size != target:
        chart = chart.resize(target, Image.LANCZOS)
    return _round_and_border(chart, radius)


def hrow(panels: list[Image.Image]) -> Image.Image:
    """Empile des panneaux déjà rendus côte à côte, alignés en haut."""
    gap = _s(GAP)
    width = sum(p.width for p in panels) + gap * (len(panels) - 1)
    height = max(p.height for p in panels)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    x = 0
    for p in panels:
        img.alpha_composite(p, (x, 0))
        x += p.width + gap
    return img


def compose_card(header: Image.Image, body_blocks: list[Image.Image], footer_text: str) -> Image.Image:
    pad = _s(OUTER_PAD)
    gap = _s(GAP)
    footer_h = _s(48)
    content_w = header.width
    total_h = pad * 2 + header.height + gap + sum(b.height + gap for b in body_blocks) + footer_h
    total_w = content_w + pad * 2

    canvas = Image.new("RGBA", (total_w, total_h), PAGE_BG)
    canvas.alpha_composite(header, (pad, pad))
    y = pad + header.height + gap
    for block in body_blocks:
        canvas.alpha_composite(block, (pad, y))
        y += block.height + gap

    draw = ImageDraw.Draw(canvas)
    draw.text((total_w // 2, total_h - footer_h // 2), footer_text, font=_font(False, 20), fill=INK_MUTED, anchor="mm")

    final_size = (total_w // SCALE, total_h // SCALE)
    return canvas.convert("RGB").resize(final_size, Image.LANCZOS)


def to_discord_file(image: Image.Image, filename: str):
    import discord  # import tardif : évite de charger discord.py pour un usage hors-bot éventuel

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    return discord.File(buf, filename=filename)
