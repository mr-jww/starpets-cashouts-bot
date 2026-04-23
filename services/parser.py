"""
Parser for rows copy-pasted from Google Sheets tracker.

Auto-detects table type by column count:
  splite  — 13 cols:
    Blogger | Language | Platform | Link | Status | Date | Views |
    Rate | Price | PayMethod | PayStatus | Content | Manager

  ammm2   — 15 cols:
    Blogger | New/Old | Language | Platform | Link | Status | Date |
    Views | Price | PayMethod | PayStatus | PayDate |
    IntType(AM/MM2) | IntType | Manager

Returns ParseResult with one BloggerResult per unique blogger.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional

ERR = {
    "ru": "ERR:ПУСТО",
    "en": "ERR:EMPTY",
}

# --------------------------------------------------------------------------- #
# Platform normalisation
# --------------------------------------------------------------------------- #
_PLATFORM_MAP = {
    "youtube":   "YouTube",
    "instagram": "Instagram",
    "tiktok":    "TikTok",
    "tik tok":   "TikTok",
    "facebook":  "Facebook",
}


def _normalise_platform(raw: str, link: str, lang: str) -> str:
    if not raw or not raw.strip():
        return ERR[lang]
    key = raw.strip().lower()
    platform = _PLATFORM_MAP.get(key, raw.strip())
    if platform == "YouTube" and "shorts" in link.lower():
        return "YouTube Shorts"
    return platform


# --------------------------------------------------------------------------- #
# Payment method normalisation
# --------------------------------------------------------------------------- #
_METHOD_MAP = {
    "site":       "site",
    "paypal":     "paypal",
    "usdt":       "usdt-trc20",
    "usdt-trc20": "usdt-trc20",
    "crypto":     "usdt-trc20",
}


def _normalise_method(raw: str) -> str:
    return _METHOD_MAP.get(raw.strip().lower(), raw.strip().lower())


# --------------------------------------------------------------------------- #
# Number helpers
# --------------------------------------------------------------------------- #
def _parse_views(raw: str) -> Optional[int]:
    cleaned = raw.strip().replace(" ", "").replace(",", "").replace("\u202f", "")
    try:
        return int(cleaned)
    except ValueError:
        return None


def _format_views(views: int) -> str:
    return f"{views:,}".replace(",", " ")


# --------------------------------------------------------------------------- #
# Junk line detection
# --------------------------------------------------------------------------- #
def _is_junk_line(parts: list[str]) -> bool:
    if not any(p.strip() for p in parts):
        return True
    joined = "\t".join(parts).strip()
    if re.match(r"new\s+week", joined, re.IGNORECASE):
        return True
    if parts[0].strip().lower() == "blogger":
        return True
    return False


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #
@dataclass
class VideoRow:
    blogger:      str
    platform:     str
    link:         str
    date:         str
    views_raw:    str
    views:        Optional[int]
    price:        str
    pay_method:   str
    game:         str
    mode:         str
    has_error:    bool = False
    error_fields: list[str] = field(default_factory=list)


@dataclass
class BloggerResult:
    blogger: str
    rows:    list[VideoRow] = field(default_factory=list)
    mode:    str = "splite"

    @property
    def video_count(self) -> int:
        return len(self.rows)

    @property
    def games(self) -> list[str]:
        seen: list[str] = []
        for r in self.rows:
            if r.game and r.game not in seen and not r.game.startswith("ERR:"):
                seen.append(r.game)
        return seen

    @property
    def total_price(self) -> str:
        total = 0.0
        for row in self.rows:
            if row.price and not row.price.startswith("ERR:"):
                try:
                    val = row.price.replace("$", "").replace(",", ".").strip()
                    total += float(val)
                except ValueError:
                    pass
        return "$" + f"{total:.1f}".replace(".", ",")

    @property
    def has_errors(self) -> bool:
        return any(r.has_error for r in self.rows)

    @property
    def pay_method_type(self) -> str:
        if not self.rows:
            return ""
        methods = [r.pay_method.strip().lower() for r in self.rows if r.pay_method]
        if not methods:
            return ""
        return max(set(methods), key=methods.count)


@dataclass
class ParseResult:
    bloggers:        list[BloggerResult] = field(default_factory=list)
    critical_errors: list[str] = field(default_factory=list)
    mode:            str = "splite"


# --------------------------------------------------------------------------- #
# Mode detection
# --------------------------------------------------------------------------- #
def _detect_mode(parts: list[str]) -> str:
    return "ammm2" if len(parts) >= 15 else "splite"


# --------------------------------------------------------------------------- #
# Row parsers
# --------------------------------------------------------------------------- #
def _parse_splite_row(parts: list[str], lang: str) -> VideoRow:
    err           = ERR[lang]
    blogger       = parts[0].strip()
    platform      = _normalise_platform(parts[2].strip(), parts[3].strip(), lang)
    link          = parts[3].strip()
    date          = parts[5].strip() or err
    views_str     = parts[6].strip()
    price         = parts[8].strip() or err
    pay_method    = _normalise_method(parts[9].strip())
    game          = parts[11].strip() or err

    views         = _parse_views(views_str) if views_str else None
    views_display = _format_views(views) if views is not None else err

    error_fields = []
    if date == err:          error_fields.append("date")
    if views_display == err: error_fields.append("views")
    if price == err:         error_fields.append("price")
    if platform == err:      error_fields.append("platform")
    if game == err:          error_fields.append("game")

    return VideoRow(
        blogger=blogger, platform=platform, link=link,
        date=date, views_raw=views_display, views=views,
        price=price, pay_method=pay_method, game=game,
        mode="splite", has_error=bool(error_fields),
        error_fields=error_fields,
    )


def _parse_ammm2_row(parts: list[str], lang: str) -> VideoRow:
    err           = ERR[lang]
    blogger       = parts[0].strip()
    platform      = _normalise_platform(parts[3].strip(), parts[4].strip(), lang)
    link          = parts[4].strip()
    date          = parts[6].strip() or err
    views_str     = parts[7].strip()
    price         = parts[8].strip() or err
    pay_method    = _normalise_method(parts[9].strip())
    game          = parts[12].strip() or err

    views         = _parse_views(views_str) if views_str else None
    views_display = _format_views(views) if views is not None else err

    error_fields = []
    if date == err:          error_fields.append("date")
    if views_display == err: error_fields.append("views")
    if price == err:         error_fields.append("price")
    if platform == err:      error_fields.append("platform")
    if game == err:          error_fields.append("game")

    return VideoRow(
        blogger=blogger, platform=platform, link=link,
        date=date, views_raw=views_display, views=views,
        price=price, pay_method=pay_method, game=game,
        mode="ammm2", has_error=bool(error_fields),
        error_fields=error_fields,
    )


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def parse_rows(text: str, lang: str = "ru") -> ParseResult:
    result = ParseResult()
    lines = text.strip().splitlines()
    detected_mode: Optional[str] = None
    blogger_map: dict[str, BloggerResult] = {}

    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue

        parts = line.split("\t") if "\t" in line else re.split(r"  +", line)
        parts = [p.strip() for p in parts]

        if _is_junk_line(parts):
            continue

        if detected_mode is None:
            detected_mode = _detect_mode(parts)
            result.mode = detected_mode

        mode = detected_mode
        min_cols = 13 if mode == "splite" else 15

        if len(parts) < min_cols:
            result.critical_errors.append(
                f"Строка {line_no}: недостаточно столбцов ({len(parts)} из {min_cols})"
                if lang == "ru" else
                f"Line {line_no}: not enough columns ({len(parts)} of {min_cols})"
            )
            continue

        blogger_name = parts[0].strip()
        if not blogger_name:
            result.critical_errors.append(
                f"Строка {line_no}: отсутствует имя блогера — строка пропущена"
                if lang == "ru" else
                f"Line {line_no}: blogger name is missing — row skipped"
            )
            continue

        row = (
            _parse_splite_row(parts, lang)
            if mode == "splite"
            else _parse_ammm2_row(parts, lang)
        )

        if blogger_name not in blogger_map:
            blogger_map[blogger_name] = BloggerResult(
                blogger=blogger_name, mode=mode
            )
        blogger_map[blogger_name].rows.append(row)

    result.bloggers = list(blogger_map.values())
    return result