"""
Tests for services/parser.py
Run: python tests/test_parser.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("BOT_TOKEN", "fake")
os.environ.setdefault("ADMIN_ID", "123")

from services.parser import parse_rows

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


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
SPLITE_ONE = "\n".join([
    "braba7x.ff1\tPortuguese\tYouTube\thttps://youtube.com/shorts/gs0\tpublished\t07.04.2026\t19 357\t1,00\t$19,4\tSite\tPENDING\tBrookhaven\tJohn",
    "braba7x.ff1\tPortuguese\tInstagram\thttps://www.instagram.com/reel/abc\tpublished\t07.04.2026\t666\t0,50\t$0,3\tSite\tPENDING\tBrookhaven\tJohn",
    "braba7x.ff1\tPortuguese\tTIkTok\thttps://vt.tiktok.com/abc\tpublished\t07.04.2026\t238\t0,50\t$0,1\tSite\tPENDING\tBrookhaven\tJohn",
])

SPLITE_MULTI = "\n".join([
    "Blogger\tLanguage\tPlatform\tLink\tStatus\tDate\tViews\tRate\tPrice\tPayMethod\tPayStatus\tContent\tManager",
    "\t\t\tNEW WEEK 31.03.2026 - 06.04.2026\t\t\t\t\t\t\t\t\t",
    "blodynes\tSpanish\tYouTube\thttps://youtube.com/shorts/a1\tpublished\t31.03.2026\t5 849\t1,00\t$5,8\tSite\tPENDING\tETfB\tJohn",
    "blodynes\tSpanish\tTIkTok\thttps://vt.tiktok.com/abc\tpublished\t05.04.2026\t1 275\t0,50\t$0,6\tSite\tPENDING\tETfB\tJohn",
    "ef3jota\tSpanish\tYouTube\thttps://youtube.com/shorts/b1\tpublished\t29.03.2026\t1 914\t1,00\t$1,9\tSite\tPENDING\tETfB\tJohn",
    "ef3jota\tSpanish\tYouTube\thttps://youtube.com/shorts/b2\tpublished\t01.04.2026\t1 791\t1,00\t$1,8\tSite\tPENDING\tBF\tJohn",
])

AMMM2_EMPTY_PRICE = "\n".join([
    "mimiyae\tOld\tEnglish\tYouTube\thttps://youtube.com/shorts/598\tpublished\t31.3.2026\t1 839\t\tSite\tUNPAID\t\tAdopt Me\tOverlay\tJohn",
    "fusiiio_\tOld\tFrench\tTIkTok\thttps://vm.tiktok.com/abc\tpublished\t31.3.2026\t342\t\tSite\tUNPAID\t\tAdopt Me\tOverlay\tJohn",
])

BLOCKED = "\n".join([
    "justdp09\tEnglish\tYouTube\thttps://youtube.com/shorts/xxx\tblocked\t31.03.2026\t4 810\t1,00\t$4,8\tCrypto\tPAID\tETfB\tTony",
    "justdp09\tEnglish\tYouTube\thttps://youtube.com/shorts/yyy\tpublished\t31.03.2026\t12 000\t1,00\t$12,0\tCrypto\tPAID\tETfB\tTony",
])

NO_NAME = "\tEnglish\tYouTube\thttps://youtube.com/shorts/aaa\tpublished\t01.04.2026\t5000\t1,00\t$5,0\tSite\tPENDING\tETfB\tJohn"

MULTI_GAME = "\n".join([
    "ef3jota\tSpanish\tYouTube\thttps://youtube.com/shorts/b1\tpublished\t29.03.2026\t1 914\t1,00\t$1,9\tSite\tPENDING\tETfB\tJohn",
    "ef3jota\tSpanish\tYouTube\thttps://youtube.com/shorts/b2\tpublished\t01.04.2026\t1 791\t1,00\t$1,8\tSite\tPENDING\tBF\tJohn",
    "ef3jota\tSpanish\tTIkTok\thttps://vt.tiktok.com/b3\tpublished\t01.04.2026\t440\t0,50\t$0,2\tSite\tPENDING\tBF\tJohn",
])

# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
print("\n--- SPLite: один блогер ---")
r = parse_rows(SPLITE_ONE, "ru")
check("mode=splite",              r.mode == "splite")
check("1 blogger",                len(r.bloggers) == 1)
check("3 videos",                 r.bloggers[0].video_count == 3)
check("total=$19,8",              r.bloggers[0].total_price == "$19,8")
check("game=Brookhaven",          r.bloggers[0].games == ["Brookhaven"])
check("no errors",                not r.bloggers[0].has_errors)
check("YouTube Shorts detected",  r.bloggers[0].rows[0].platform == "YouTube Shorts")
check("TikTok normalised",        r.bloggers[0].rows[2].platform == "TikTok")
check("method=site",              r.bloggers[0].pay_method_type == "site")

print("\n--- SPLite: несколько блогеров + мусор ---")
r2 = parse_rows(SPLITE_MULTI, "ru")
check("2 bloggers",               len(r2.bloggers) == 2)
check("blodynes first",           r2.bloggers[0].blogger == "blodynes")
check("blodynes 2 videos",        r2.bloggers[0].video_count == 2)
check("ef3jota 2 games",          set(r2.bloggers[1].games) == {"ETfB", "BF"})
check("no critical errors",       not r2.critical_errors)

print("\n--- AM/MM2: пустые цены → ERR ---")
r3 = parse_rows(AMMM2_EMPTY_PRICE, "ru")
check("mode=ammm2",               r3.mode == "ammm2")
check("2 bloggers",               len(r3.bloggers) == 2)
check("mimiyae has_errors",       r3.bloggers[0].has_errors)
check("price=ERR:ПУСТО",         r3.bloggers[0].rows[0].price == "ERR:ПУСТО")
check("total=$0,0 (no prices)",   r3.bloggers[0].total_price == "$0,0")

print("\n--- blocked включается ---")
r4 = parse_rows(BLOCKED, "ru")
check("2 videos incl. blocked",   r4.bloggers[0].video_count == 2)
check("Crypto→usdt-trc20",        r4.bloggers[0].pay_method_type == "usdt-trc20")

print("\n--- критическая ошибка: нет имени ---")
r5 = parse_rows(NO_NAME, "ru")
check("critical error raised",    len(r5.critical_errors) > 0)
check("no bloggers parsed",       len(r5.bloggers) == 0)

print("\n--- несколько игр у одного блогера ---")
r6 = parse_rows(MULTI_GAME, "ru")
b = r6.bloggers[0]
check("3 videos",                 b.video_count == 3)
check("2 unique games",           len(b.games) == 2)
check("games order preserved",    b.games[0] == "ETfB" and b.games[1] == "BF")
check("total=$3,9",               b.total_price == "$3,9")

print("\n--- английский язык ---")
r7 = parse_rows(AMMM2_EMPTY_PRICE, "en")
check("ERR:EMPTY in english",     r7.bloggers[0].rows[0].price == "ERR:EMPTY")

print("\n--- views formatting ---")
r8 = parse_rows(SPLITE_ONE, "ru")
check("views display '19 357'",   r8.bloggers[0].rows[0].views_raw == "19 357")
check("views int 19357",          r8.bloggers[0].rows[0].views == 19357)

# --------------------------------------------------------------------------- #
print(f"\n{'='*40}")
print(f"PASSED: {PASS}  FAILED: {FAIL}")
if FAIL == 0:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)