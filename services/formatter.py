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




def _method_label(method_type: str, lang: str) -> str:
    return _METHOD_LABELS.get(method_type.lower(), {}).get(lang, method_type)


def _header(result: BloggerResult, lang: str) -> str:
    games_str = ", ".join(result.games) if result.games else "?"
    if lang == "ru":
        return (
            f"{result.total_price_display} для {result.blogger} "
            f"за {result.video_count} видео по {games_str}:"
        )
    return (
        f"{result.total_price_display} for {result.blogger} "
        f"for {result.video_count} videos on {games_str}:"
    )


def _item(row, lang: str) -> str:
    views_label = "пр." if lang == "ru" else "views"
    return f"- {row.platform} ({row.date} - {row.views_raw} {views_label} - {row.price})"


def _footer(method_type: str, address: str, lang: str) -> str:
    return f"{_method_label(method_type, lang)} – {address}"



_MIN_PAYOUT = {
    "paypal":     50.0,
    "usdt-trc20": 10.0,
}


def payout_warning(method_type: str, amount_str: str, lang: str) -> str:
    """Return warning string if amount is below minimum, else empty string."""
    minimum = _MIN_PAYOUT.get(method_type)
    if not minimum:
        return ""
    # Parse amount like "$12,3" or "$12.3"
    try:
        clean = amount_str.lstrip("$").replace(",", ".").strip()
        amount = float(clean)
    except (ValueError, AttributeError):
        return ""
    if amount < minimum:
        label = "PayPal" if method_type == "paypal" else "USDT-TRC20"
        if lang == "ru":
            return f"⚠️ Минимальная выплата {label}: ${minimum:.0f} (сейчас ${amount:.1f})"
        else:
            return f"⚠️ Minimum payout for {label}: ${minimum:.0f} (current ${amount:.1f})"
    return ""


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