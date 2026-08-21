"""Rendu des cards stats (PIL) — réplique du layout Statbot en 1280×708.

- Rendu en 2x puis downscale (anti-aliasing gratuit sur les coins arrondis et le texte).
- Polices DejaVu embarquées par matplotlib (rendu identique en local et sur Railway).
- Emojis : DejaVu n'a aucun glyphe emoji (d'où les carrés vides "tofu" de
  l'ancienne version). Chaque emoji est donc rendu comme une petite image
  Twemoji téléchargée depuis le CDN jsDelivr au premier usage puis cachée en
  mémoire pour toute la vie du process. Les icônes de section (trophée,
  haut-parleur, graphique, manette) sont ces mêmes images passées en
  silhouette grise pour matcher le style monochrome de la référence.
  Sans réseau, l'emoji est simplement omis — jamais de carré vide.
"""

import io
import os
from dataclasses import dataclass
from typing import Optional

import aiohttp
import emoji as emoji_lib
import matplotlib  # uniquement pour ses polices DejaVu embarquées
from PIL import Image, ImageDraw, ImageFont, ImageOps

SCALE = 2
CARD_W, CARD_H = 1280, 708

# Couleurs relevées sur la card de référence
PAGE_BG = (33, 34, 38, 255)
BLOCK_BG = (46, 48, 53, 255)
ROW_DARK = (35, 36, 40, 255)     # segment label (1j, Message…) et pastilles de noms
ROW_LIGHT = (56, 58, 64, 255)    # segment valeur
BADGE_LABEL_BG = (64, 66, 72, 255)
INK = (255, 255, 255, 255)
INK_SOFT = (185, 187, 190, 255)
INK_MUTED = (150, 152, 158, 255)
ICON_TINT = (181, 186, 193, 255)
SERIES_MESSAGES = (62, 196, 109, 255)  # vert « Message »
SERIES_VOICE = (236, 95, 163, 255)     # rose « Vocale »

ICON_TROPHY = "🏆"
ICON_VOICE = "🔊"
ICON_CHART = "📈"
ICON_GAME = "🎮"
_ICON_EMOJIS = (ICON_TROPHY, ICON_VOICE, ICON_CHART, ICON_GAME)

TWEMOJI_BASE = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72/"
_EMOJI_CACHE: dict[str, Optional[Image.Image]] = {}


def _s(n: float) -> int:
    return round(n * SCALE)


_FONT_CACHE: dict = {}
_FONT_FILES = {
    "regular": "DejaVuSans.ttf",
    "bold": "DejaVuSans-Bold.ttf",
    "italic": "DejaVuSans-Oblique.ttf",
}


def _font(style: str, size: int) -> ImageFont.FreeTypeFont:
    key = (style, size)
    if key not in _FONT_CACHE:
        path = os.path.join(matplotlib.get_data_path(), "fonts", "ttf", _FONT_FILES[style])
        _FONT_CACHE[key] = ImageFont.truetype(path, _s(size))
    return _FONT_CACHE[key]


# ---------------------------------------------------------------------------
# Emojis (Twemoji)
# ---------------------------------------------------------------------------

def collect_emojis(*texts: str) -> set[str]:
    """Tous les emojis présents dans les textes donnés + les icônes de section."""
    chars = set(_ICON_EMOJIS)
    for text in texts:
        if text:
            chars.update(span["emoji"] for span in emoji_lib.emoji_list(text))
    return chars


def _twemoji_codes(char: str) -> list[str]:
    # Convention twemoji : le sélecteur de variation FE0F est absent du nom de
    # fichier dans la plupart des cas — on tente sans, puis avec.
    full = "-".join(f"{ord(c):x}" for c in char)
    without_vs = "-".join(f"{ord(c):x}" for c in char if ord(c) != 0xFE0F)
    return [full] if full == without_vs else [without_vs, full]


async def fetch_emoji_images(chars: set[str]) -> dict[str, Image.Image]:
    """Télécharge (et cache en mémoire) les PNG Twemoji des emojis demandés.
    Un emoji introuvable ou un échec réseau est caché comme None : il sera
    simplement omis du rendu."""
    missing = [c for c in chars if c not in _EMOJI_CACHE]
    if missing:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for char in missing:
                image = None
                for code in _twemoji_codes(char):
                    try:
                        async with session.get(f"{TWEMOJI_BASE}{code}.png") as resp:
                            if resp.status == 200:
                                data = await resp.read()
                                image = Image.open(io.BytesIO(data)).convert("RGBA")
                                break
                    except (aiohttp.ClientError, OSError):
                        pass
                _EMOJI_CACHE[char] = image
    return {c: _EMOJI_CACHE[c] for c in chars if _EMOJI_CACHE.get(c) is not None}


def _segments(text: str) -> list[tuple[str, str]]:
    """Découpe un texte en [("text", ...) | ("emoji", ...)]."""
    spans = emoji_lib.emoji_list(text)
    out, pos = [], 0
    for span in spans:
        if span["match_start"] > pos:
            out.append(("text", text[pos:span["match_start"]]))
        out.append(("emoji", span["emoji"]))
        pos = span["match_end"]
    if pos < len(text):
        out.append(("text", text[pos:]))
    return out


def _rich_width(draw, text, font, emoji_map, emoji_size) -> float:
    width = 0.0
    for kind, val in _segments(text):
        if kind == "emoji":
            if val in emoji_map:
                width += emoji_size + _s(2)
        else:
            width += draw.textlength(val, font=font)
    return width


def _truncate_rich(draw, text, font, emoji_map, emoji_size, max_width) -> str:
    if _rich_width(draw, text, font, emoji_map, emoji_size) <= max_width:
        return text
    ellipsis_w = draw.textlength("…", font=font)
    out, width = "", 0.0
    for kind, val in _segments(text):
        if kind == "emoji":
            piece = (emoji_size + _s(2)) if val in emoji_map else 0
            if width + piece + ellipsis_w > max_width:
                return out + "…"
            out += val
            width += piece
        else:
            for ch in val:
                ch_w = draw.textlength(ch, font=font)
                if width + ch_w + ellipsis_w > max_width:
                    return out + "…"
                out += ch
                width += ch_w
    return out


def draw_rich_text(canvas, draw, x, y_center, text, font, fill, emoji_map, emoji_size) -> float:
    """Dessine texte + emojis inline à partir de x, centrés verticalement sur
    y_center. Renvoie le x de fin."""
    for kind, val in _segments(text):
        if kind == "emoji":
            image = emoji_map.get(val)
            if image is None:
                continue
            icon = image.resize((emoji_size, emoji_size), Image.LANCZOS)
            canvas.alpha_composite(icon, (int(x), int(y_center - emoji_size / 2)))
            x += emoji_size + _s(2)
        else:
            draw.text((x, y_center), val, font=font, fill=fill, anchor="lm")
            x += draw.textlength(val, font=font)
    return x


def _silhouette(image: Image.Image, size: int, tint=ICON_TINT) -> Image.Image:
    """Icône monochrome : l'alpha du twemoji sert de pochoir, rempli en gris clair."""
    icon = image.resize((size, size), Image.LANCZOS)
    solid = Image.new("RGBA", (size, size), tint)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(solid, (0, 0), icon.split()[3])
    return out


def _circle_crop(image: Image.Image, diameter: int) -> Image.Image:
    fitted = ImageOps.fit(image.convert("RGBA"), (diameter, diameter), Image.LANCZOS)
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter, diameter), fill=255)
    out = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    out.paste(fitted, (0, 0), mask)
    return out


# ---------------------------------------------------------------------------
# Graphique sparkline (pur PIL — pas d'axes, pas de grille)
# ---------------------------------------------------------------------------

def _catmull_rom(points: list[tuple[float, float]], samples: int = 16) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    pts = [points[0]] + points + [points[-1]]
    out = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for step in range(samples):
            t = step / samples
            t2, t3 = t * t, t * t * t
            x = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x, y))
    out.append(points[-1])
    return out


def _draw_sparkline(canvas, box, series) -> None:
    """series = [(valeurs, couleur), ...] — chaque série est normalisée
    indépendamment sur la hauteur du cadre (style Statbot, pas d'axes) et
    dessinée dans l'ordre (la dernière passe au-dessus)."""
    x0, y0, x1, y1 = box
    usable_h = (y1 - y0) - _s(8)
    for values, color in series:
        vals = list(values)
        if not vals:
            continue
        if len(vals) == 1:
            vals = vals * 2
        peak = max(vals)
        if peak <= 0:
            peak = 1
        n = len(vals)
        pts = [(x0 + (x1 - x0) * i / (n - 1), y1 - usable_h * (v / peak)) for i, v in enumerate(vals)]
        smooth = [(px, min(max(py, y0), y1)) for px, py in _catmull_rom(pts)]
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        odraw.polygon([(x0, y1)] + smooth + [(x1, y1)], fill=color[:3] + (90,))
        odraw.line(smooth, fill=color, width=_s(3), joint="curve")
        canvas.alpha_composite(overlay)


# ---------------------------------------------------------------------------
# Données de la card
# ---------------------------------------------------------------------------

@dataclass
class CardData:
    avatar: Optional[Image.Image]
    placeholder_glyph: str            # dessiné dans le cercle si avatar absent
    title: str
    title_suffix: str                 # gris, après le titre (ex. le pseudo)
    subtitle: str                     # ligne 2 du header
    badges: list                      # [(label, valeur)] × 2
    rank_title: str                   # "Classement serveur" / "Top membres"
    rank_rows: list                   # [(label, valeur)] × 2
    messages_rows: list               # [(label, valeur, unité)] × 3
    voice_rows: list                  # [(label, valeur, unité)] × 3
    top_title: str                    # "Top des salons et applications" / "Top des membres"
    top_rows: list                    # [(icone: "text"|"voice"|"game", nom, valeur, unité)] × 3
    graph_messages: list
    graph_voice: list
    period_label: str                 # "14 derniers jours"
    rank_icon: str = ICON_TROPHY
    timezone_label: str = "Europe/Paris"
    brand_text: str = "Propulsé par ScoobyBot"
    brand_icon: Optional[Image.Image] = None


# ---------------------------------------------------------------------------
# Blocs
# ---------------------------------------------------------------------------

def _block_title(canvas, draw, x, y, w, title, icon, emoji_map) -> None:
    draw.text((_s(x + 18), _s(y + 34)), title, font=_font("bold", 24), fill=INK, anchor="lm")
    if icon == "#":
        draw.text((_s(x + w - 18), _s(y + 34)), "#", font=_font("bold", 30), fill=ICON_TINT, anchor="rm")
    elif icon and emoji_map.get(icon):
        size = _s(30)
        canvas.alpha_composite(
            _silhouette(emoji_map[icon], size),
            (_s(x + w - 18) - size, _s(y + 34) - size // 2),
        )


def _draw_header(canvas, draw, data: CardData, emoji_map) -> None:
    d = _s(92)
    if data.avatar is not None:
        canvas.alpha_composite(_circle_crop(data.avatar, d), (_s(20), _s(12)))
    else:
        draw.ellipse((_s(20), _s(12), _s(20) + d, _s(12) + d), fill=BLOCK_BG)
        draw.text((_s(20) + d // 2, _s(12) + d // 2), data.placeholder_glyph,
                  font=_font("bold", 40), fill=ICON_TINT, anchor="mm")

    badge_left = _draw_badges(canvas, draw, data.badges)

    x = _s(132)
    title_font = _font("bold", 40)
    suffix_font = _font("regular", 26)
    max_w = badge_left - x - _s(20)

    suffix_w = draw.textlength(data.title_suffix, font=suffix_font) + _s(12) if data.title_suffix else 0
    title = _truncate_rich(draw, data.title, title_font, emoji_map, _s(46), max_w - suffix_w)
    end_x = draw_rich_text(canvas, draw, x, _s(46), title, title_font, INK, emoji_map, _s(46))
    if data.title_suffix:
        draw.text((end_x + _s(12), _s(52)), data.title_suffix, font=suffix_font, fill=INK_MUTED, anchor="lm")

    sub_font = _font("bold", 22)
    subtitle = _truncate_rich(draw, data.subtitle, sub_font, emoji_map, _s(26), max_w)
    draw_rich_text(canvas, draw, x, _s(90), subtitle, sub_font, INK_SOFT, emoji_map, _s(26))


def _draw_badges(canvas, draw, badges) -> int:
    """Groupes badge (label au-dessus, valeur en dessous), alignés à droite.
    Renvoie le x (scalé) du bord gauche occupé, pour borner le titre."""
    label_font = _font("bold", 19)
    value_font = _font("bold", 25)
    right = _s(1260)
    for label, value in reversed(badges):
        label_w = draw.textlength(label, font=label_font) + _s(28)
        value_w = draw.textlength(value, font=value_font) + _s(36)
        group_w = max(label_w, value_w)
        gx = right - group_w
        lx = gx + (group_w - label_w) / 2
        draw.rounded_rectangle((lx, _s(20), lx + label_w, _s(52)), radius=_s(9), fill=BADGE_LABEL_BG)
        draw.text((lx + label_w / 2, _s(36)), label, font=label_font, fill=INK, anchor="mm")
        draw.rounded_rectangle((gx, _s(58), gx + group_w, _s(106)), radius=_s(11), fill=BLOCK_BG)
        draw.text((gx + group_w / 2, _s(82)), value, font=value_font, fill=INK, anchor="mm")
        right = gx - _s(22)
    return int(right)


def _draw_rank_block(canvas, draw, x, y, w, h, data: CardData, emoji_map) -> None:
    draw.rounded_rectangle((_s(x), _s(y), _s(x + w), _s(y + h)), radius=_s(14), fill=BLOCK_BG)
    _block_title(canvas, draw, x, y, w, data.rank_title, data.rank_icon, emoji_map)

    pad = _s(16)
    label_w = _s(150)
    row_h = _s(76)
    row_y = _s(y + 66)
    value_font = _font("bold", 25)
    for label, value in data.rank_rows:
        left, right = _s(x) + pad, _s(x + w) - pad
        draw.rounded_rectangle((left, row_y, right, row_y + row_h), radius=_s(11), fill=ROW_LIGHT)
        draw.rounded_rectangle((left, row_y, left + label_w, row_y + row_h), radius=_s(11), fill=ROW_DARK)
        draw.text((left + label_w / 2, row_y + row_h / 2), label, font=_font("bold", 25), fill=INK, anchor="mm")

        avail = right - (left + label_w) - _s(24)
        shown = _truncate_rich(draw, value, value_font, emoji_map, _s(28), avail)
        shown_w = _rich_width(draw, shown, value_font, emoji_map, _s(28))
        vx = left + label_w + (right - left - label_w - shown_w) / 2
        draw_rich_text(canvas, draw, vx, row_y + row_h / 2, shown, value_font, INK, emoji_map, _s(28))
        row_y += row_h + _s(14)


def _draw_stat_block(canvas, draw, x, y, w, h, title, icon, rows, emoji_map) -> None:
    draw.rounded_rectangle((_s(x), _s(y), _s(x + w), _s(y + h)), radius=_s(14), fill=BLOCK_BG)
    _block_title(canvas, draw, x, y, w, title, icon, emoji_map)

    pad = _s(16)
    label_w = _s(70)
    row_h = _s(52)
    row_y = _s(y + 64)
    for label, value, unit in rows:
        left, right = _s(x) + pad, _s(x + w) - pad
        draw.rounded_rectangle((left, row_y, right, row_y + row_h), radius=_s(9), fill=ROW_LIGHT)
        draw.rounded_rectangle((left, row_y, left + label_w, row_y + row_h), radius=_s(9), fill=ROW_DARK)
        draw.text((left + label_w / 2, row_y + row_h / 2), label, font=_font("bold", 20), fill=INK, anchor="mm")
        vx = left + label_w + _s(16)
        draw.text((vx, row_y + row_h / 2), value, font=_font("bold", 24), fill=INK, anchor="lm")
        vx += draw.textlength(value, font=_font("bold", 24)) + _s(8)
        draw.text((vx, row_y + row_h / 2), unit, font=_font("italic", 19), fill=INK_SOFT, anchor="lm")
        row_y += row_h + _s(8)


def _draw_top_block(canvas, draw, x, y, w, h, data: CardData, emoji_map) -> None:
    draw.rounded_rectangle((_s(x), _s(y), _s(x + w), _s(y + h)), radius=_s(14), fill=BLOCK_BG)
    _block_title(canvas, draw, x, y, w, data.top_title, ICON_CHART, emoji_map)

    row_h = _s(50)
    row_y = _s(y + 68)
    icon_cx = _s(x + 33)
    pill_x = _s(x + 60)
    pill_w = _s(310)
    name_font = _font("bold", 22)
    for icon_kind, name, value, unit in data.top_rows:
        row_cy = row_y + row_h / 2
        if icon_kind == "text":
            draw.text((icon_cx, row_cy), "#", font=_font("bold", 26), fill=ICON_TINT, anchor="mm")
        else:
            icon_emoji = ICON_VOICE if icon_kind == "voice" else ICON_GAME
            if emoji_map.get(icon_emoji):
                size = _s(26)
                canvas.alpha_composite(
                    _silhouette(emoji_map[icon_emoji], size),
                    (int(icon_cx - size / 2), int(row_cy - size / 2)),
                )

        draw.rounded_rectangle((pill_x, row_y, pill_x + pill_w, row_y + row_h), radius=_s(9), fill=ROW_DARK)
        if name:
            shown = _truncate_rich(draw, name, name_font, emoji_map, _s(26), pill_w - _s(28))
            draw_rich_text(canvas, draw, pill_x + _s(14), row_cy, shown, name_font, INK, emoji_map, _s(26))
        if value:
            vx = pill_x + pill_w + _s(18)
            draw.text((vx, row_cy), value, font=_font("bold", 22), fill=INK, anchor="lm")
            vx += draw.textlength(value, font=_font("bold", 22)) + _s(8)
            draw.text((vx, row_cy), unit, font=_font("italic", 19), fill=INK_SOFT, anchor="lm")
        row_y += row_h + _s(14)


def _draw_graph_block(canvas, draw, x, y, w, h, data: CardData, emoji_map) -> None:
    draw.rounded_rectangle((_s(x), _s(y), _s(x + w), _s(y + h)), radius=_s(14), fill=BLOCK_BG)
    draw.text((_s(x + 18), _s(y + 34)), "Graphiques", font=_font("bold", 24), fill=INK, anchor="lm")

    # Légende, alignée à droite du titre
    legend_font = _font("bold", 20)
    dot = _s(13)
    items = [("Message", SERIES_MESSAGES), ("Vocale", SERIES_VOICE)]
    total = sum(dot + _s(8) + draw.textlength(label, font=legend_font) for label, _ in items) + _s(20)
    lx = _s(x + w - 18) - total
    ly = _s(y + 34)
    for label, color in items:
        draw.ellipse((lx, ly - dot / 2, lx + dot, ly + dot / 2), fill=color)
        lx += dot + _s(8)
        draw.text((lx, ly), label, font=legend_font, fill=INK, anchor="lm")
        lx += draw.textlength(label, font=legend_font) + _s(20)

    box = (_s(x + 16), _s(y + 64), _s(x + w - 16), _s(y + h - 18))
    _draw_sparkline(canvas, box, [
        (data.graph_voice, SERIES_VOICE),
        (data.graph_messages, SERIES_MESSAGES),
    ])


def _draw_footer(canvas, draw, data: CardData) -> None:
    y = _s(676)
    bold = _font("bold", 21)
    regular = _font("regular", 21)

    x = _s(24)
    parts = [
        ("Période d'analyse: ", bold, INK),
        (data.period_label, regular, INK_SOFT),
        (" — ", regular, INK_MUTED),
        ("Fuseau horaire: ", bold, INK),
        (data.timezone_label, regular, INK_SOFT),
    ]
    for text, font, fill in parts:
        draw.text((x, y), text, font=font, fill=fill, anchor="lm")
        x += draw.textlength(text, font=font)

    brand_font = _font("bold", 21)
    text_w = draw.textlength(data.brand_text, font=brand_font)
    bx = _s(1256) - text_w
    draw.text((bx, y), data.brand_text, font=brand_font, fill=INK, anchor="lm")
    if data.brand_icon is not None:
        icon_d = _s(30)
        canvas.alpha_composite(
            _circle_crop(data.brand_icon, icon_d),
            (int(bx - icon_d - _s(10)), int(y - icon_d / 2)),
        )


# ---------------------------------------------------------------------------
# Assemblage
# ---------------------------------------------------------------------------

def render_card(data: CardData, emoji_map: dict) -> Image.Image:
    canvas = Image.new("RGBA", (_s(CARD_W), _s(CARD_H)), PAGE_BG)
    draw = ImageDraw.Draw(canvas)

    _draw_header(canvas, draw, data, emoji_map)

    col_w = (1240 - 2 * 16) / 3
    xs = [20 + i * (col_w + 16) for i in range(3)]
    _draw_rank_block(canvas, draw, xs[0], 132, col_w, 245, data, emoji_map)
    _draw_stat_block(canvas, draw, xs[1], 132, col_w, 245, "Messages", "#", data.messages_rows, emoji_map)
    _draw_stat_block(canvas, draw, xs[2], 132, col_w, 245, "Activité vocale", ICON_VOICE, data.voice_rows, emoji_map)

    _draw_top_block(canvas, draw, 20, 393, 600, 250, data, emoji_map)
    _draw_graph_block(canvas, draw, 636, 393, 624, 250, data, emoji_map)

    _draw_footer(canvas, draw, data)

    return canvas.convert("RGB").resize((CARD_W, CARD_H), Image.LANCZOS)


def render_slots_card(
    *,
    reels: list[str],
    balance: float,
    bet: int,
    status: str,
    emoji_map: dict,
    payout: float | None = None,
    net: float | None = None,
) -> Image.Image:
    """Rendu de la machine à sous dans le même langage visuel que les stats."""
    canvas = Image.new("RGBA", (_s(CARD_W), _s(CARD_H)), PAGE_BG)
    draw = ImageDraw.Draw(canvas)

    draw.text(
        (_s(48), _s(48)),
        "SCOOBY SLOTS",
        font=_font("bold", 34),
        fill=INK,
        anchor="lm",
    )
    draw.text(
        (_s(1232), _s(48)),
        f"Solde : {_format_card_coins(balance)} coins",
        font=_font("bold", 25),
        fill=INK_SOFT,
        anchor="rm",
    )

    panel_x, panel_y, panel_w, panel_h = 76, 116, 1128, 444
    draw.rounded_rectangle(
        (_s(panel_x), _s(panel_y), _s(panel_x + panel_w), _s(panel_y + panel_h)),
        radius=_s(24),
        fill=BLOCK_BG,
    )
    draw.text(
        (_s(112), _s(151)),
        "MACHINE À SOUS",
        font=_font("bold", 22),
        fill=INK_MUTED,
        anchor="lm",
    )
    draw.text(
        (_s(1168), _s(151)),
        f"Mise : {_format_card_coins(bet)} coin{'s' if bet != 1 else ''}",
        font=_font("bold", 22),
        fill=INK_SOFT,
        anchor="rm",
    )

    cell_w, cell_h, gap = 292, 270, 22
    total_w = 3 * cell_w + 2 * gap
    first_x = panel_x + (panel_w - total_w) / 2
    reel_top = 178
    reel_font = _font("bold", 92)
    emoji_size = _s(106)
    for index, symbol in enumerate(reels):
        x = first_x + index * (cell_w + gap)
        draw.rounded_rectangle(
            (_s(x), _s(reel_top), _s(x + cell_w), _s(reel_top + cell_h)),
            radius=_s(18),
            fill=ROW_DARK,
            outline=BADGE_LABEL_BG,
            width=_s(2),
        )
        center_y = reel_top + cell_h / 2
        width = _rich_width(draw, symbol, reel_font, emoji_map, emoji_size)
        if width:
            draw_rich_text(
                canvas,
                draw,
                _s(x + cell_w / 2) - width / 2,
                _s(center_y),
                symbol,
                reel_font,
                INK,
                emoji_map,
                emoji_size,
            )
        else:
            draw.text(
                (_s(x + cell_w / 2), _s(center_y)),
                symbol,
                font=reel_font,
                fill=INK,
                anchor="mm",
            )

    # Table de paiement : lignes de gains des triples, comblent l'espace du bas
    draw.line(
        (_s(panel_x + 26), _s(494), _s(panel_x + panel_w - 26), _s(494)),
        fill=BADGE_LABEL_BG,
        width=_s(2),
    )
    paytable = [
        ("PAIRE", "1,6×", False),
        ("TRIPLE", "4×", False),
        ("💎", "10×", True),
        ("7️⃣", "30×", True),
    ]
    cell_pad = _s(28)
    emoji_font = _font("bold", 30)
    label_font = _font("bold", 22)
    col_w = (panel_w - 2 * cell_pad) / len(paytable)
    emoji_size = _s(36)
    for i, (glyph, value, is_emoji) in enumerate(paytable):
        cx = panel_x + cell_pad + col_w * (i + 0.5)
        if is_emoji and glyph in emoji_map:
            gfx = emoji_map[glyph].resize((emoji_size, emoji_size), Image.LANCZOS)
            icon_w = emoji_size + _s(2)
            val_w = draw.textlength(value, font=label_font)
            total_w = icon_w + val_w
            gx = _s(cx) - total_w // 2
            canvas.alpha_composite(gfx, (int(gx), int(_s(520) - emoji_size / 2)))
            draw.text(
                (gx + icon_w, _s(520)),
                value, font=label_font, fill=INK_MUTED, anchor="lm",
            )
        elif is_emoji:
            draw.text(
                (_s(cx), _s(520)),
                f"{glyph} {value}",
                font=label_font, fill=INK_MUTED, anchor="mm",
            )
        else:
            draw.text(
                (_s(cx), _s(520)),
                f"{glyph} {value}",
                font=label_font, fill=INK_MUTED, anchor="mm",
            )

    status_fill = INK_SOFT
    if net is not None:
        status_fill = SERIES_MESSAGES if net > 0 else (226, 82, 96, 255)
    status_font = _font("bold", 29)
    status_emoji_size = _s(31)
    status_width = _rich_width(draw, status, status_font, emoji_map, status_emoji_size)
    draw_rich_text(
        canvas,
        draw,
        _s(CARD_W / 2) - status_width / 2,
        _s(604),
        status,
        status_font,
        status_fill,
        emoji_map,
        status_emoji_size,
    )

    if payout is not None:
        draw.text(
            (_s(CARD_W / 2), _s(646)),
            f"Retour brut : {_format_card_coins(payout)} coins  •  Nouveau solde : {_format_card_coins(balance)} coins",
            font=_font("regular", 21),
            fill=INK_MUTED,
            anchor="mm",
        )

    brand_font = _font("bold", 21)
    brand_text = "Propulsé par ScoobyBot"
    brand_w = draw.textlength(brand_text, font=brand_font)
    draw.text(
        (_s(1256) - brand_w, _s(678)),
        brand_text,
        font=brand_font,
        fill=INK_MUTED,
        anchor="lm",
    )

    return canvas.convert("RGB").resize((CARD_W, CARD_H), Image.LANCZOS)


_ROULETTE_ORDER = (
    0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10,
    5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26,
)
_RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
_ROULETTE_RED = (198, 47, 57, 255)
_ROULETTE_BLACK = (18, 19, 21, 255)
_ROULETTE_GREEN = (39, 139, 82, 255)


def _roulette_color(n: int) -> tuple:
    if n == 0:
        return _ROULETTE_GREEN
    return _ROULETTE_RED if n in _RED_NUMBERS else _ROULETTE_BLACK


def _roulette_number_position(cx: float, cy: float, radius: float, angle_deg: float) -> tuple[float, float]:
    import math
    rad = math.radians(angle_deg)
    return cx + radius * math.cos(rad), cy + radius * math.sin(rad)


def render_roulette_card(
    *,
    result_num: int,
    color_label: str,
    balance: float,
    bet: int,
    bet_label: str,
    status: str,
    emoji_map: dict,
    payout: float | None = None,
    net: float | None = None,
) -> Image.Image:
    """Card roulette européenne — roue stylisée + mise + résultat, style stats/slots."""
    import math
    canvas = Image.new("RGBA", (_s(CARD_W), _s(CARD_H)), PAGE_BG)
    draw = ImageDraw.Draw(canvas)

    draw.text((_s(48), _s(48)), "SCOOBY ROULETTE", font=_font("bold", 34), fill=INK, anchor="lm")
    draw.text(
        (_s(1232), _s(48)),
        f"Solde : {_format_card_coins(balance)} coins",
        font=_font("bold", 25), fill=INK_SOFT, anchor="rm",
    )

    panel_x, panel_y, panel_w, panel_h = 76, 116, 1128, 444
    draw.rounded_rectangle(
        (_s(panel_x), _s(panel_y), _s(panel_x + panel_w), _s(panel_y + panel_h)),
        radius=_s(24), fill=BLOCK_BG,
    )
    draw.text((_s(112), _s(151)), "ROULEETTE EUROPÉENNE", font=_font("bold", 22), fill=INK_MUTED, anchor="lm")
    draw.text(
        (_s(1168), _s(151)),
        f"Mise : {_format_card_coins(bet)} coins — {bet_label}",
        font=_font("bold", 22), fill=INK_SOFT, anchor="rm",
    )

    # Roue stylisée : ring de 37 secteurs colorés + numéros
    cx, cy = 640, 350
    r_out, r_in = 185, 128
    n = len(_ROULETTE_ORDER)
    num_font = _font("bold", 18)
    for i, number in enumerate(_ROULETTE_ORDER):
        a0 = i * 360 / n
        a1 = (i + 1) * 360 / n
        fill = _roulette_color(number)
        outline = (255, 255, 255, 255) if number == result_num else (0, 0, 0, 0)
        draw.pieslice(
            (_s(cx - r_out), _s(cy - r_out), _s(cx + r_out), _s(cy + r_out)),
            a0, a1, fill=fill, outline=outline, width=_s(2),
        )
        mid = (a0 + a1) / 2
        px, py = _roulette_number_position(cx, cy, (r_in + r_out) / 2, mid)
        draw.text((_s(px), _s(py)), str(number), font=num_font, fill=INK, anchor="mm")

    # Moyeu avec le résultat
    hub_r = 120
    draw.ellipse((_s(cx - hub_r), _s(cy - hub_r), _s(cx + hub_r), _s(cy + hub_r)), fill=ROW_DARK, outline=BADGE_LABEL_BG, width=_s(2))
    result_fill = _roulette_color(result_num)
    draw.ellipse((_s(cx - 46), _s(cy - 46), _s(cx + 46), _s(cy + 46)), fill=result_fill, outline=(255, 255, 255, 255), width=_s(3))
    draw.text((_s(cx), _s(cy - 20)), str(result_num), font=_font("bold", 40), fill=INK, anchor="mm")
    draw.text((_s(cx), _s(cy + 26)), color_label.upper(), font=_font("bold", 18), fill=INK, anchor="mm")

    # Status / gain-perte
    status_fill = INK_SOFT
    if net is not None:
        status_fill = SERIES_MESSAGES if net > 0 else (226, 82, 96, 255)
    status_font = _font("bold", 29)
    status_emoji_size = _s(31)
    status_width = _rich_width(draw, status, status_font, emoji_map, status_emoji_size)
    draw_rich_text(
        canvas, draw,
        _s(CARD_W / 2) - status_width / 2, _s(604),
        status, status_font, status_fill, emoji_map, status_emoji_size,
    )

    if payout is not None:
        draw.text(
            (_s(CARD_W / 2), _s(646)),
            f"Retour brut : {_format_card_coins(payout)} coins  •  Nouveau solde : {_format_card_coins(balance)} coins",
            font=_font("regular", 21), fill=INK_MUTED, anchor="mm",
        )

    brand_font = _font("bold", 21)
    brand_text = "Propulsé par ScoobyBot"
    brand_w = draw.textlength(brand_text, font=brand_font)
    draw.text((_s(1256) - brand_w, _s(678)), brand_text, font=brand_font, fill=INK_MUTED, anchor="lm")

    return canvas.convert("RGB").resize((CARD_W, CARD_H), Image.LANCZOS)


def _format_card_coins(amount: float) -> str:
    return f"{amount:.2f}".rstrip("0").rstrip(".")


def to_discord_file(image: Image.Image, filename: str):
    import discord  # import tardif : évite de charger discord.py pour un usage hors-bot éventuel

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    return discord.File(buf, filename=filename)
