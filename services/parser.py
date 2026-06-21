"""
Parser for rows copy-pasted from Google Sheets tracker.

Auto-detects table type by column count:
  splite  - 13 cols (14 with Comment, ignored)
  ammm2   - 15 cols (16 with Comments, ignored)
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional

ERR = {"ru": "ERR:ПУСТО", "en": "ERR:EMPTY"}

_PLATFORM_MAP = {
    "youtube": "YouTube", "instagram": "Instagram",
    "tiktok": "TikTok", "tik tok": "TikTok", "facebook": "Facebook",
}

_METHOD_MAP = {
    "site": "site", "paypal": "paypal",
    "usdt": "usdt-trc20", "usdt-trc20": "usdt-trc20", "crypto": "usdt-trc20",
}

_CURRENCY_SYMBOLS = ["$", "€", "₽"]

_ERR_DESCRIPTIONS = {
    "date":     {"ru": "отсутствует дата",         "en": "missing date"},
    "views":    {"ru": "отсутствуют просмотры",    "en": "missing views"},
    "price":    {"ru": "отсутствует цена",         "en": "missing price"},
    "platform": {"ru": "отсутствует платформа",    "en": "missing platform"},
    "game":     {"ru": "отсутствует игра",         "en": "missing game"},
    "method":   {"ru": "отсутствует метод оплаты", "en": "missing payment method"},
}


def _normalise_platform(raw: str, link: str, lang: str) -> str:
    if not raw or not raw.strip():
        return ERR[lang]
    key = raw.strip().lower()
    platform = _PLATFORM_MAP.get(key, raw.strip())
    if platform == "YouTube" and "shorts" in link.lower():
        return "YouTube Shorts"
    return platform


def _normalise_method(raw: str) -> str:
    if not raw or not raw.strip():
        return ""
    return _METHOD_MAP.get(raw.strip().lower(), raw.strip().lower())


def _detect_currency(price_raw: str) -> str:
    for sym in _CURRENCY_SYMBOLS:
        if sym in price_raw:
            return sym
    return "$"


def _parse_views(raw: str) -> Optional[int]:
    cleaned = raw.strip()
    for ch in ("\u00a0", "\u202f", "\u2009", "\u0020", " ", "\xa0", ",", "."):
        cleaned = cleaned.replace(ch, "")
    try:
        return int(cleaned)
    except ValueError:
        return None


def _format_views(views: int) -> str:
    return f"{views:,}".replace(",", " ")


def _is_junk_line(parts: list[str]) -> bool:
    if not any(p.strip() for p in parts):
        return True
    joined = "\t".join(parts).strip()
    if re.match(r"new\s+week", joined, re.IGNORECASE):
        return True
    if parts[0].strip().lower() == "blogger":
        return True
    return False


@dataclass
class VideoRow:
    blogger:       str
    platform:      str
    link:          str
    date:          str
    views_raw:     str
    views:         Optional[int]
    price:         str
    currency:      str
    pay_method:    str
    pay_status:    str
    game:          str
    mode:          str
    manager:       str = ""
    has_error:     bool = False
    error_fields:  list[str] = field(default_factory=list)
    error_details: list[str] = field(default_factory=list)


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
    def currency(self) -> str:
        currencies = [r.currency for r in self.rows if r.currency]
        if not currencies:
            return "$"
        return max(set(currencies), key=currencies.count)

    @property
    def total_price(self) -> str:
        total = 0.0
        sym = self.currency
        for row in self.rows:
            if row.price and not row.price.startswith("ERR:"):
                try:
                    val = row.price.replace(sym, "").replace(",", ".").strip()
                    total += float(val)
                except ValueError:
                    pass
        return sym + f"{total:.1f}".replace(".", ",")

    @property
    def total_price_display(self) -> str:
        raw = self.total_price
        return f"⚠️ERROR:{raw}" if self.has_errors else raw

    @property
    def has_errors(self) -> bool:
        return any(r.has_error for r in self.rows)

    @property
    def pay_method_type(self) -> str:
        methods = [r.pay_method for r in self.rows if r.pay_method]
        if not methods:
            return ""
        return max(set(methods), key=methods.count)

    def error_summary(self, lang: str) -> str:
        lines = []
        for i, row in enumerate(self.rows, 1):
            if row.has_error:
                platform = row.platform if not row.platform.startswith("ERR:") else "?"
                date = row.date if not row.date.startswith("ERR:") else "?"
                details = ", ".join(row.error_details)
                if lang == "ru":
                    lines.append(f"  Строка {i} ({platform}, {date}): {details}")
                else:
                    lines.append(f"  Row {i} ({platform}, {date}): {details}")
        return "\n".join(lines)


@dataclass
class ParseResult:
    bloggers:        list[BloggerResult] = field(default_factory=list)
    critical_errors: list[str] = field(default_factory=list)
    mode:            str = "splite"

    @property
    def bloggers_with_errors(self) -> list[BloggerResult]:
        return [b for b in self.bloggers if b.has_errors]


def _detect_mode(parts: list[str]) -> str:
    """Detect table format by the position of the video link.

    The SaB (splite) table keeps the link in column 4 (index 3). The AM/MM2
    (ammm2) table has an extra New/Old column up front, which moves the link to
    column 5 (index 4). Detecting by the link position is robust to an empty
    New/Old cell and to trailing empty columns being trimmed on copy — unlike a
    plain column count, which can silently misread one table as the other.
    """
    for i, p in enumerate(parts[:7]):
        if "http" in p.lower():
            if i == 4:
                return "ammm2"
            if i == 3:
                return "splite"
            break
    # No link found in the expected place — fall back to the column count.
    return "ammm2" if len(parts) >= 15 else "splite"


def _build_errors(date, views_display, price, platform, game, pay_method, err, lang):
    ef, ed = [], []
    for fname, val in [("date", date), ("views", views_display),
                       ("price", price), ("platform", platform), ("game", game)]:
        if val == err:
            ef.append(fname)
            ed.append(_ERR_DESCRIPTIONS[fname][lang])
    if not pay_method:
        ef.append("method")
        ed.append(_ERR_DESCRIPTIONS["method"][lang])
    return ef, ed


def _parse_splite_row(parts: list[str], lang: str) -> VideoRow:
    err = ERR[lang]
    blogger  = parts[0].strip()
    platform = _normalise_platform(parts[2].strip(), parts[3].strip(), lang)
    link     = parts[3].strip()
    date     = parts[5].strip() or err
    views_s  = parts[6].strip()
    price_r  = parts[8].strip()
    price    = price_r or err
    currency = _detect_currency(price_r) if price_r else "$"
    method     = _normalise_method(parts[9].strip())
    pay_status = parts[10].strip().upper() if len(parts) > 10 else ""
    game       = parts[11].strip() or err
    views      = _parse_views(views_s) if views_s else None
    vdisp      = _format_views(views) if views is not None else err
    manager    = parts[12].strip() if len(parts) > 12 else ""
    ef, ed     = _build_errors(date, vdisp, price, platform, game, method, err, lang)
    return VideoRow(
        blogger=blogger, platform=platform, link=link, date=date,
        views_raw=vdisp, views=views, price=price, currency=currency,
        pay_method=method, pay_status=pay_status, game=game, mode="splite", manager=manager,
        has_error=bool(ef), error_fields=ef, error_details=ed,
    )


def _parse_ammm2_row(parts: list[str], lang: str) -> VideoRow:
    err = ERR[lang]
    blogger  = parts[0].strip()
    platform = _normalise_platform(parts[3].strip(), parts[4].strip(), lang)
    link     = parts[4].strip()
    date     = parts[6].strip() or err
    views_s  = parts[7].strip()
    price_r  = parts[8].strip()
    price    = price_r or err
    currency = _detect_currency(price_r) if price_r else "$"
    method     = _normalise_method(parts[9].strip())
    pay_status = parts[10].strip().upper() if len(parts) > 10 else ""
    game       = parts[12].strip() or err
    views      = _parse_views(views_s) if views_s else None
    vdisp      = _format_views(views) if views is not None else err
    manager    = parts[14].strip() if len(parts) > 14 else ""
    ef, ed     = _build_errors(date, vdisp, price, platform, game, method, err, lang)
    return VideoRow(
        blogger=blogger, platform=platform, link=link, date=date,
        views_raw=vdisp, views=views, price=price, currency=currency,
        pay_method=method, pay_status=pay_status, game=game, mode="ammm2", manager=manager,
        has_error=bool(ef), error_fields=ef, error_details=ed,
    )



def _split_row(line: str) -> list[str]:
    """
    Split a spreadsheet row preserving empty cells.
    Tab-separated: split by tab (empty cells = empty strings naturally).
    Space-separated: 4+ spaces = separator + empty cell, 2-3 spaces = separator.
    """
    if "\t" in line:
        return [p.strip() for p in line.split("\t")]
    # Normalize space runs: 4+ spaces -> double tab (empty cell), 2-3 -> single tab
    normalized = re.sub(r" {4,}", "\t\t", line)
    normalized = re.sub(r" {2,3}", "\t", normalized)
    return [p.strip() for p in normalized.split("\t")]


_STATUS_WORDS = ("unpaid", "paid", "pending")
_AMOUNT_SIGN_RE = re.compile(r"[$€₽]\s?\d")


def looks_like_lost_tabs(text: str) -> bool:
    """
    Detect rows where the column separators were lost during copy-paste.

    Telegram Web and the mobile apps replace clipboard tabs with single
    spaces, which collapses a whole spreadsheet row into one column (see
    _split_row). The row still carries the signature of real payout data:
    a link, a money amount and a payout-status word. When all three appear
    in a line that _split_row could not break into columns, tabs were lost.
    """
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        # If the splitter already finds columns, separators are intact — skip.
        if len(_split_row(line)) >= 3:
            continue
        low = s.lower()
        has_link   = "http" in low
        has_amount = bool(_AMOUNT_SIGN_RE.search(s))
        has_status = any(w in low for w in _STATUS_WORDS)
        if has_link and has_amount and has_status:
            return True
    return False


def parse_rows(text: str, lang: str = "ru") -> ParseResult:
    result = ParseResult()
    text = text.replace("\r", "")
    lines = text.strip().splitlines()
    detected_mode: Optional[str] = None
    blogger_map: dict[str, BloggerResult] = {}

    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        parts = _split_row(line)
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
                f"Строка {line_no}: отсутствует имя блогера"
                if lang == "ru" else
                f"Line {line_no}: blogger name is missing"
            )
            continue
        row = _parse_splite_row(parts, lang) if mode == "splite" else _parse_ammm2_row(parts, lang)
        if blogger_name not in blogger_map:
            blogger_map[blogger_name] = BloggerResult(blogger=blogger_name, mode=mode)
        blogger_map[blogger_name].rows.append(row)

    result.bloggers = list(blogger_map.values())
    return result