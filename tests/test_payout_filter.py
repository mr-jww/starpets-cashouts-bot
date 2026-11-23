"""Regression tests: ambassador filter keeps only the selected ambassador's rows.

Run: python -m unittest tests.test_payout_filter
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "fake")
os.environ.setdefault("ADMIN_ID", "123")

from handlers import payout
from services.parser import parse_rows

_TAIL = "\tEnglish\tOld\tpublished"

# One WW blogger ("mixer") with integrations of two ambassadors (+ one row
# without an ambassador), a second blogger of Max only, a third of Rina only.
WW_MIXED = "\n".join([
    # A date, B week, C month, D blogger, E link, F ambassador, G game, H type, I platform,
    # J lang, K New/Old, L status, M views, N method, O price, P pay status
    "03.11.2026\t02.11.2026\t11\tmixer\thttps://youtube.com/shorts/m1\tMax\tMM2\tAdvertising insert\tYouTube" + _TAIL + "\t1 000\tSite\t$2,00\tUNPAID",
    "04.11.2026\t02.11.2026\t11\tmixer\thttps://vt.tiktok.com/m2\tRina\tAdopt Me\tOverlay\tTikTok" + _TAIL + "\t5 000\tSite\t$10,00\tUNPAID",
    "05.11.2026\t02.11.2026\t11\tmixer\thttps://vt.tiktok.com/m3\tMax\tMM2\tAdvertising insert\tTikTok" + _TAIL + "\t1 500\tSite\t$3,00\tPAID",
    "05.11.2026\t02.11.2026\t11\tmixer\thttps://vt.tiktok.com/m4\tRina\tAdopt Me\tOverlay\tTikTok" + _TAIL + "\t700\tSite\t$1,40\tPAID",
    "06.11.2026\t02.11.2026\t11\tmixer\thttps://vt.tiktok.com/m5\t\tMM2\tOverlay\tTikTok" + _TAIL + "\t500\tSite\t$1,00\tUNPAID",
    "06.11.2026\t02.11.2026\t11\tmaxonly\thttps://youtube.com/shorts/x1\tMax\tMM2\tAdvertising insert\tYouTube" + _TAIL + "\t2 000\tSite\t$4,00\tUNPAID",
    "07.11.2026\t02.11.2026\t11\trinaonly\thttps://youtube.com/shorts/r1\tRina\tAdopt Me\tOverlay\tYouTube" + _TAIL + "\t3 000\tSite\t$6,00\tUNPAID",
])

# Same scenario in the two older formats (manager in the last column).
SPLITE_MIXED = "\n".join([
    "mixer\tEnglish\tYouTube\thttps://youtube.com/shorts/s1\tpublished\t01.04.2026\t1 000\t1,00\t$2,0\tSite\tUNPAID\tETfB\tJohn",
    "mixer\tEnglish\tTIkTok\thttps://vt.tiktok.com/s2\tpublished\t02.04.2026\t5 000\t1,00\t$5,0\tSite\tUNPAID\tBF\tTony",
    "mixer\tEnglish\tTIkTok\thttps://vt.tiktok.com/s3\tpublished\t03.04.2026\t1 500\t1,00\t$1,5\tSite\tUNPAID\tETfB\tJohn",
])
AMMM2_MIXED = "\n".join([
    "mixer\tOld\tEnglish\tYouTube\thttps://youtube.com/shorts/a1\tpublished\t31.3.2026\t1 839\t$1,8\tSite\tUNPAID\t\tAdopt Me\tOverlay\tJohn",
    "mixer\tOld\tEnglish\tTIkTok\thttps://vm.tiktok.com/a2\tpublished\t31.3.2026\t9 000\t$9,0\tSite\tUNPAID\t\tMM2\tOverlay\tTony",
])


class FilterRowsUnitTests(unittest.TestCase):
    def test_mixed_blogger_keeps_only_selected_ambassador_rows(self):
        bloggers = parse_rows(WW_MIXED, "ru").bloggers
        kept, excluded = payout._filter_rows_by_manager(bloggers, "Max")
        self.assertEqual([b.blogger for b in kept], ["mixer", "maxonly"])
        mixer = kept[0]
        self.assertEqual(mixer.video_count, 2)
        self.assertEqual(mixer.games, ["MM2"])
        self.assertEqual(mixer.total_price, "$5,0")          # 2.00 + 3.00
        self.assertTrue(all(r.manager == "Max" for r in mixer.rows))
        self.assertEqual(excluded, 3)                         # 2 Rina + 1 empty

    def test_other_ambassador(self):
        kept, excluded = payout._filter_rows_by_manager(parse_rows(WW_MIXED, "ru").bloggers, "Rina")
        self.assertEqual([b.blogger for b in kept], ["mixer", "rinaonly"])
        self.assertEqual(kept[0].video_count, 2)
        self.assertEqual(kept[0].games, ["Adopt Me"])
        self.assertEqual(kept[0].total_price, "$11,4")       # 10.00 + 1.40
        self.assertEqual(excluded, 3)

    def test_original_parse_result_not_mutated(self):
        bloggers = parse_rows(WW_MIXED, "ru").bloggers
        payout._filter_rows_by_manager(bloggers, "Max")
        self.assertEqual(bloggers[0].video_count, 5)

    def test_no_match(self):
        kept, excluded = payout._filter_rows_by_manager(parse_rows(WW_MIXED, "ru").bloggers, "Nobody")
        self.assertEqual(kept, [])
        self.assertEqual(excluded, 0)

    def test_whole_name_match_still_applies(self):
        kept, _ = payout._filter_rows_by_manager(parse_rows(WW_MIXED, "ru").bloggers, "Ma")
        self.assertEqual(kept, [])

    def test_splite_and_ammm2_formats(self):
        kept, excluded = payout._filter_rows_by_manager(parse_rows(SPLITE_MIXED, "ru").bloggers, "John")
        self.assertEqual(kept[0].video_count, 2)
        self.assertEqual(kept[0].total_price, "$3,5")
        self.assertEqual(kept[0].games, ["ETfB"])
        self.assertEqual(excluded, 1)

        kept, excluded = payout._filter_rows_by_manager(parse_rows(AMMM2_MIXED, "ru").bloggers, "Tony")
        self.assertEqual(kept[0].video_count, 1)
        self.assertEqual(kept[0].total_price, "$9,0")
        self.assertEqual(kept[0].games, ["MM2"])
        self.assertEqual(excluded, 1)


def _update(text):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=123, first_name="Test"),
        message=message, effective_message=message, callback_query=None,
    )


def _user(**overrides):
    user = {
        "id": 1, "telegram_id": 123, "username": "tester", "lang": "ru",
        "manager_filter": "Max", "output_mode": "text", "default_fmt": "oneline",
        "include_paid": 0, "warn_paid": 1, "include_pending": 0, "warn_pending": 1,
        "include_no_method": 0, "method_from_table": 0, "payout_mode": 0,
    }
    user.update(overrides)
    return user


class PayoutEndToEndTests(unittest.IsolatedAsyncioTestCase):
    """Run the real payout_got_rows → _emit_payouts with the DB layer mocked."""

    async def _run(self, text, effective_filter, method_type="site", **user_overrides):
        update = _update(text)
        context = SimpleNamespace(
            user_data={"user": _user(**user_overrides), "effective_filter": effective_filter},
            args=[],
        )
        blogger_ids = {}

        async def get_blogger_by_name(name, manager_id):
            return {"id": blogger_ids.setdefault(name, len(blogger_ids) + 1), "name": name}

        async def get_primary_method(blogger_id):
            return {"id": 100 + blogger_id, "type": method_type, "address": f"addr{blogger_id}"}

        save_payout = AsyncMock()
        with patch.object(payout, "get_blogger_by_name", get_blogger_by_name), \
                patch.object(payout, "get_primary_method", get_primary_method), \
                patch.object(payout, "save_payout", save_payout), \
                patch.object(payout, "db_log", AsyncMock()), \
                patch.object(payout, "log_info"):
            await payout.payout_got_rows(update, context)
        texts = [c.args[0] for c in update.message.reply_text.await_args_list]
        return texts, save_payout, context

    def _saved(self, save_payout):
        return {
            # formatted_text starts with the amount and the blogger name
            c.kwargs["formatted_text"].split(" для ")[1].split(" ")[0]: c.kwargs
            for c in save_payout.await_args_list
        }

    async def test_filter_max_payout_only_has_max_rows(self):
        texts, save_payout, context = await self._run(WW_MIXED, "Max")
        saved = self._saved(save_payout)
        self.assertEqual(set(saved), {"mixer", "maxonly"})
        # mixer: Max rows are $2.00 UNPAID + $3.00 PAID; PAID is skipped by default
        self.assertEqual(saved["mixer"]["amount_raw"], "$2,0")
        self.assertEqual(saved["mixer"]["videos_count"], 1)
        self.assertEqual(saved["mixer"]["game"], "MM2")
        self.assertEqual(saved["maxonly"]["amount_raw"], "$4,0")
        joined = "\n".join(texts)
        # PAID warning counts only Max's PAID row, not Rina's
        self.assertIn("Строки со статусом PAID (1)", joined)
        self.assertIn("Исключено строк других амбассадоров", joined)
        self.assertNotIn("Adopt Me", saved["mixer"]["formatted_text"])

    async def test_filter_max_with_paid_included(self):
        _, save_payout, _ = await self._run(WW_MIXED, "Max", include_paid=1)
        saved = self._saved(save_payout)
        self.assertEqual(saved["mixer"]["amount_raw"], "$5,0")
        self.assertEqual(saved["mixer"]["videos_count"], 2)

    async def test_filter_rina(self):
        texts, save_payout, _ = await self._run(WW_MIXED, "Rina", include_paid=1)
        saved = self._saved(save_payout)
        self.assertEqual(set(saved), {"mixer", "rinaonly"})
        self.assertEqual(saved["mixer"]["amount_raw"], "$11,4")
        self.assertEqual(saved["mixer"]["game"], "Adopt Me")
        self.assertEqual(saved["rinaonly"]["amount_raw"], "$6,0")

    async def test_amb_all_keeps_every_row(self):
        texts, save_payout, _ = await self._run(WW_MIXED, None, include_paid=1)
        saved = self._saved(save_payout)
        self.assertEqual(set(saved), {"mixer", "maxonly", "rinaonly"})
        self.assertEqual(saved["mixer"]["amount_raw"], "$17,4")   # 2+10+3+1.4+1
        self.assertEqual(saved["mixer"]["videos_count"], 5)
        self.assertNotIn("Исключено строк", "\n".join(texts))

    async def test_no_rows_for_ambassador(self):
        texts, save_payout, _ = await self._run(WW_MIXED, "Nobody")
        save_payout.assert_not_awaited()
        self.assertIn("нет записей менеджера Nobody", texts[-1])

    async def test_below_minimum_warning_uses_filtered_total(self):
        # USDT minimum is $10: unfiltered mixer total ($17.4) is above it,
        # Max's share ($5.0) is below — the warning must reflect the filtered rows.
        texts, save_payout, _ = await self._run(WW_MIXED, "Max", method_type="usdt-trc20", include_paid=1)
        self.assertIn("Ниже минимума выплаты: 2", texts[-1])

    async def test_splite_and_ammm2_end_to_end(self):
        _, save_payout, _ = await self._run(SPLITE_MIXED, "John")
        saved = self._saved(save_payout)
        self.assertEqual(saved["mixer"]["amount_raw"], "$3,5")
        self.assertEqual(saved["mixer"]["videos_count"], 2)

        _, save_payout, _ = await self._run(AMMM2_MIXED, "Tony")
        saved = self._saved(save_payout)
        self.assertEqual(saved["mixer"]["amount_raw"], "$9,0")
        self.assertEqual(saved["mixer"]["game"], "MM2")

    async def test_ww_invalid_date_marks_payout_as_error(self):
        bad = WW_MIXED.splitlines()[0].replace("03.11.2026", "3.11", 1)
        texts, save_payout, _ = await self._run(bad, "Max")
        saved = self._saved(save_payout)
        self.assertTrue(saved["mixer"]["amount_raw"].startswith("⚠️ERROR:"))
        self.assertIn("некорректная дата «3.11»", "\n".join(texts))


if __name__ == "__main__":
    unittest.main()
