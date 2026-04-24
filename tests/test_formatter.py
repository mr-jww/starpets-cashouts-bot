"""
Tests for services/formatter.py
Run: python tests/test_formatter.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("BOT_TOKEN", "fake")
os.environ.setdefault("ADMIN_ID", "123")

from services.parser import parse_rows
from services.formatter import format_oneline, format_multiline

PASS = 0
FAIL = 0


def check(label: str, condition: bool):
    global PASS, FAIL
    if condition:
        print(f"  PASS  {label}")
        PASS += 1
    else:
        print(f"  FAIL  {label}")
        FAIL += 1


SPLITE = "\n".join([
    "blodynes\tSpanish\tYouTube\thttps://youtube.com/shorts/a1\tpublished\t31.03.2026\t5 849\t1,00\t$5,8\tSite\tPENDING\tETfB\tJohn",
    "blodynes\tSpanish\tYouTube\thttps://youtube.com/shorts/a2\tpublished\t01.04.2026\t2 079\t1,00\t$2,1\tSite\tPENDING\tETfB\tJohn",
    "blodynes\tSpanish\tYouTube\thttps://youtube.com/shorts/a3\tpublished\t01.04.2026\t16 763\t1,00\t$16,8\tSite\tPENDING\tETfB\tJohn",
    "blodynes\tSpanish\tTIkTok\thttps://vt.tiktok.com/abc\tpublished\t05.04.2026\t1 275\t0,50\t$0,6\tSite\tPENDING\tETfB\tJohn",
])

AMMM2_ERR = "\n".join([
    "mimiyae\tOld\tEnglish\tYouTube\thttps://youtube.com/shorts/598\tpublished\t31.3.2026\t1 839\t\tSite\tUNPAID\t\tAdopt Me\tOverlay\tJohn",
])

print("\n--- RU oneline ---")
r = parse_rows(SPLITE, "ru")
b = r.bloggers[0]
out = format_oneline(b, "site", "69ac899df797df28426efbbd", "ru")
check("starts with total",        out.startswith("$25,3"))
check("contains blogger name",    "blodynes" in out)
check("за N видео по",            "за 4 видео по ETfB" in out)
check("Site – address",           "Site – 69ac899df797df28426efbbd" in out)
check("all on two lines",         out.count("\n") == 1)
check("пр. label",                "пр." in out)
check("no error notice",          "ОШИБКА" not in out)

print("\n--- RU multiline ---")
out_ml = format_multiline(b, "site", "69ac899df797df28426efbbd", "ru")
check("header on first line",     out_ml.split("\n")[0].startswith("$25,3"))
check("4 video lines",            out_ml.count("\n- ") == 4)
check("footer on last line",      out_ml.strip().split("\n")[-1].startswith("Site"))

print("\n--- EN oneline ---")
r_en = parse_rows(SPLITE, "en")
b_en = r_en.bloggers[0]
out_en = format_oneline(b_en, "site", "69ac899df797df28426efbbd", "en")
check("for N videos on",          "for 4 videos on ETfB" in out_en)
check("views label",              "views" in out_en)
check("no пр.",                   "пр." not in out_en)

print("\n--- ERR notice ---")
r_err = parse_rows(AMMM2_ERR, "ru")
b_err = r_err.bloggers[0]
out_err = format_oneline(b_err, "site", "abc123", "ru")
check("ЕСТЬ ОШИБКА prefix",       out_err.startswith("⚠ ЕСТЬ ОШИБКА"))
check("ERR:ПУСТО in body",        "ERR:ПУСТО" in out_err)

print("\n--- EN ERR notice ---")
r_err_en = parse_rows(AMMM2_ERR, "en")
b_err_en = r_err_en.bloggers[0]
out_err_en = format_oneline(b_err_en, "site", "abc123", "en")
check("HAS ERROR prefix",         out_err_en.startswith("⚠ HAS ERROR"))
check("ERR:EMPTY in body",        "ERR:EMPTY" in out_err_en)

print("\n--- USDT-TRC20 method ---")
out_usdt = format_oneline(b, "usdt-trc20", "TLBwE3pdG9UYedsZHUENewCYLdy7KKhGi3", "ru")
check("USDT-TRC20 label",         "USDT-TRC20 – TLBwE3" in out_usdt)

print("\n--- PayPal method ---")
out_pp = format_oneline(b, "paypal", "user@gmail.com", "ru")
check("PayPal label",             "PayPal – user@gmail.com" in out_pp)

print(f"\n{'='*40}")
print(f"PASSED: {PASS}  FAILED: {FAIL}")
if FAIL == 0:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)