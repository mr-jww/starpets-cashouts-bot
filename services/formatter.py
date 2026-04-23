"""
Formatter: produces payout message in two display formats.
Language: 'ru' | 'en'
"""

from __future__ import annotations
from services.parser import BloggerResult

_METHOD_LABELS: dict[str, dict[str, str]] = {
    "site":       {"ru": "Site",       "en": "Site"},
    "usdt-trc20": {"ru": "USDT-TRC20", "en": "USDT-TRC20"},
    "paypal":     {"ru": "PayPal",     "en": "PayPal"},
}

_NOTICE = {
    "ru": "⚠ ЕСТЬ ОШИБКА",
    "en": "⚠ HAS ERROR",
}


def _method_label(method_type: str, lang: str) -> str:
    return _METHOD_LABELS.get(method_type.lower(), {}).get(lang, method_type)


def _header(result: BloggerResult, lang: str) -> str:
    games_str = ", ".join(result.games) if result.games else "?"
    if lang == "ru":
        return (
            f"{result.total_price} для {result.blogger} "
            f"за {result.video_count} видео по {games_str}:"
        )
    return (
        f"{result.total_price} for {result.blogger} "
        f"for {result.video_count} videos on {games_str}:"
    )


def _item(row, lang: str) -> str:
    views_label = "пр." if lang == "ru" else "views"
    return f"- {row.platform} ({row.date} - {row.views_raw} {views_label} - {row.price})"


def _footer(method_type: str, address: str, lang: str) -> str:
    return f"{_method_label(method_type, lang)} – {address}"


def format_oneline(
    result: BloggerResult,
    method_type: str,
    address: str,
    lang: str = "ru",
) -> str:
    """
    All videos on one line, method on second line.

    $X для X за N видео по X: - Platform (...) - Platform (...)
    Method – address
    """
    header = _header(result, lang)
    items  = " ".join(_item(r, lang) for r in result.rows)
    footer = _footer(method_type, address, lang)
    body   = f"{header} {items}\n{footer}"
    if result.has_errors:
        return f"{_NOTICE[lang]}\n{body}"
    return body


def format_multiline(
    result: BloggerResult,
    method_type: str,
    address: str,
    lang: str = "ru",
) -> str:
    """
    Each video on its own line.

    $X для X за N видео по X:
    - Platform (...)
    - Platform (...)
    Method – address
    """
    header = _header(result, lang)
    items  = "\n".join(_item(r, lang) for r in result.rows)
    footer = _footer(method_type, address, lang)
    body   = f"{header}\n{items}\n{footer}"
    if result.has_errors:
        return f"{_NOTICE[lang]}\n{body}"
    return body


def both_formats(
    result: BloggerResult,
    method_type: str,
    address: str,
    lang: str = "ru",
) -> tuple[str, str]:
    """Returns (oneline, multiline)."""
    return (
        format_oneline(result, method_type, address, lang),
        format_multiline(result, method_type, address, lang),
    )