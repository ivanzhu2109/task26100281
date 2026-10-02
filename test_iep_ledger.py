"""IEP 履约核算库的 unittest 测试。"""

import unittest
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction

from iep_ledger import CAMPUS_TZ, Ledger, MandateRecord, SessionRecord

UTC = timezone.utc


def ts(y, mo, d, h=0, mi=0, s=0):
    """UTC 时间戳快捷构造。"""
    return datetime(y, mo, d, h, mi, s, tzinfo=UTC)


def local(y, mo, d, h=0, mi=0, s=0):
    """校区当地时间快捷构造。"""
    return datetime(y, mo, d, h, mi, s, tzinfo=CAMPUS_TZ)


def mandate(rid, recorded_at, student, service, start, end, weekly, voided=False):
    return MandateRecord(
        id=rid,
        recorded_at=recorded_at,
        voided=voided,
        student=student,
        service=service,
        start_date=start,
        end_date=end,
        weekly_minutes=weekly,
    )


def session(rid, recorded_at, student, service, start, end, voided=False):
    return SessionRecord(
        id=rid,
        recorded_at=recorded_at,
        voided=voided,
        student=student,
        service=service,
        start=start,
        end=end,
    )


class CrossMonthWeekTest(unittest.TestCase):
    """场景一:授权仅覆盖 2025-03-31 至 2025-04-07,会话全在 4 月。"""

    def setUp(self):
        self.ledger = Ledger()
        self.ledger.record_mandate(
            mandate(
                "m1",
                ts(2025, 3, 1),
                "alice",
                "speech",
                date(2025, 3, 31),
                date(2025, 4, 7),
                100,
            )
        )
        self.ledger.record_session(
            session(
                "s1",
                ts(2025, 4, 2),
                "alice",
                "speech",
                local(2025, 4, 1, 15, 0),
                local(2025, 4, 1, 15, 30),
            )
        )
        self.ledger.record_session(
            session(
                "s2",
                ts(2025, 4, 4),
                "alice",
                "speech",
                local(2025, 4, 3, 15, 0),
                local(2025, 4, 3, 15, 20),
            )
        )
        self.as_of = ts(2025, 5, 1)

    def test_march_gets_one_weekday_share(self):
        rows = self.ledger.report((2025, 3), self.as_of)
        row = rows[("alice", "speech")]
        self.assertEqual(row.required, Fraction(20))
        self.assertEqual(row.credited, Fraction(10))
        self.assertTrue(row.shortfall)

    def test_april_gets_four_weekday_share(self):
        rows = self.ledger.report((2025, 4), self.as_of)
        row = rows[("alice", "speech")]
        self.assertEqual(row.required, Fraction(80))
        self.assertEqual(row.credited, Fraction(40))
        self.assertTrue(row.shortfall)


class MidWeekMandateChangeTest(unittest.TestCase):
    """场景二:同一周内授权变更,重叠会话与跨生效边界会话。

    周 2025-05-05(周一)起:
    - m1 覆盖 05-05~05-07(周一至周二,2 个工作日),每周 100 分钟,R1=40
    - m2 覆盖 05-07~05-12(周三至周五,3 个工作日),每周 150 分钟,R2=90
    - s1 周一 10:00-11:00(60 分钟,落在片段 1)
    - s2/s3 周三 10:00-10:30 与 10:15-10:45(重叠,并集 45 分钟,片段 2)
    - s4 周二 23:00 至周三 00:30(跨边界:片段 1 得 60 分钟,片段 2 得 30 分钟)
    片段 1:L=120 超出 R1=40,超额 80 分钟不得挪给片段 2;
    片段 2:L=45+30=75(重叠不重复计),C2=min(90,75)=75。
    """

    def setUp(self):
        self.ledger = Ledger()
        t0 = ts(2025, 4, 1)
        self.ledger.record_mandate(
            mandate("m1", t0, "bob", "ot", date(2025, 5, 5), date(2025, 5, 7), 100)
        )
        self.ledger.record_mandate(
            mandate("m2", t0, "bob", "ot", date(2025, 5, 7), date(2025, 5, 12), 150)
        )
        self.ledger.record_mandate(
            mandate("m3", t0, "carol", "pt", date(2025, 5, 5), date(2025, 5, 10), 100)
        )
        self.ledger.record_mandate(
            mandate("m4", t0, "dave", "pt", date(2025, 5, 5), date(2025, 5, 10), 100)
        )
        self.ledger.record_session(
            session(
                "s1", ts(2025, 5, 6), "bob", "ot",
                local(2025, 5, 5, 10, 0), local(2025, 5, 5, 11, 0),
            )
        )
        self.ledger.record_session(
            session(
                "s2", ts(2025, 5, 8), "bob", "ot",
                local(2025, 5, 7, 10, 0), local(2025, 5, 7, 10, 30),
            )
        )
        self.ledger.record_session(
            session(
                "s3", ts(2025, 5, 8), "bob", "ot",
                local(2025, 5, 7, 10, 15), local(2025, 5, 7, 10, 45),
            )
        )
        self.ledger.record_session(
            session(
                "s4", ts(2025, 5, 8), "bob", "ot",
                local(2025, 5, 6, 23, 0), local(2025, 5, 7, 0, 30),
            )
        )
        # carol:恰好 85 分钟,恰好 85%
        self.ledger.record_session(
            session(
                "s5", ts(2025, 5, 6), "carol", "pt",
                local(2025, 5, 5, 9, 0), local(2025, 5, 5, 10, 25),
            )
        )
        # dave:85 分钟少 1 秒,低于 85%
        self.ledger.record_session(
            session(
                "s6", ts(2025, 5, 6), "dave", "pt",
                local(2025, 5, 5, 9, 0), local(2025, 5, 5, 10, 24, 59),
            )
        )
        self.rows = self.ledger.report((2025, 5), ts(2025, 6, 1))

    def test_excess_not_transferred_and_overlap_counted_once(self):
        row = self.rows[("bob", "ot")]
        self.assertEqual(row.required, Fraction(130))
        # 若超额可挪用或重叠重复计,计入都会变成 130
        self.assertEqual(row.credited, Fraction(115))
        self.assertFalse(row.shortfall)

    def test_exactly_85_percent_not_shortfall(self):
        row = self.rows[("carol", "pt")]
        self.assertEqual(row.required, Fraction(100))
        self.assertEqual(row.credited, Fraction(85))
        self.assertFalse(row.shortfall)

    def test_one_second_below_85_percent_is_shortfall(self):
        row = self.rows[("dave", "pt")]
        self.assertEqual(row.required, Fraction(100))
        self.assertEqual(row.credited, Fraction(5099, 60))
        self.assertTrue(row.shortfall)


class DstRevisionHistoryTest(unittest.TestCase):
    """场景三:跨夏令时回拨的会话,随后改服务、再作废,乱序加入。

    2025-11-02(周日)纽约 02:00 回拨到 01:00。会话 05:30Z~07:30Z,
    当地钟面 01:30 EDT 到 02:30 EST 看似 1 小时,真实经过 2 小时。
    该周日属于 10-27 起的一周,5 个工作日全在 10 月,故查 10 月报告。
    会话 v1 记为 music,t2 修订为 art,t3 作废;按乱序加入。
    """

    def setUp(self):
        self.ledger = Ledger()
        t0 = ts(2025, 10, 1)
        t1 = ts(2025, 11, 3)
        t2 = ts(2025, 11, 4)
        t3 = ts(2025, 11, 5)
        start = ts(2025, 11, 2, 5, 30)
        end = ts(2025, 11, 2, 7, 30)
        # 乱序加入:作废版最先,修订版最后
        self.ledger.record_session(
            session("sx", t3, "erin", "art", start, end, voided=True)
        )
        self.ledger.record_session(
            session("sx", t1, "erin", "music", start, end)
        )
        self.ledger.record_mandate(
            mandate(
                "mm", t0, "erin", "music",
                date(2025, 10, 27), date(2025, 11, 3), 120,
            )
        )
        self.ledger.record_session(
            session("sx", t2, "erin", "art", start, end)
        )
        self.as_of_v1 = ts(2025, 11, 3, 12)
        self.as_of_v2 = ts(2025, 11, 4, 12)
        self.as_of_v3 = ts(2025, 11, 6)

    def test_real_elapsed_minutes_across_dst_fallback(self):
        rows = self.ledger.report((2025, 10), self.as_of_v1)
        row = rows[("erin", "music")]
        self.assertEqual(row.required, Fraction(120))
        # 真实 120 分钟;若按当地钟面会错算成 60
        self.assertEqual(row.credited, Fraction(120))
        self.assertFalse(row.shortfall)
        self.assertNotIn(("erin", "art"), rows)

    def test_after_revision_session_moves_to_other_service(self):
        rows = self.ledger.report((2025, 10), self.as_of_v2)
        row = rows[("erin", "music")]
        self.assertEqual(row.required, Fraction(120))
        self.assertEqual(row.credited, Fraction(0))
        self.assertTrue(row.shortfall)
        # art 无授权片段,不出现在报告中
        self.assertNotIn(("erin", "art"), rows)

    def test_after_void_session_disappears(self):
        rows = self.ledger.report((2025, 10), self.as_of_v3)
        row = rows[("erin", "music")]
        self.assertEqual(row.required, Fraction(120))
        self.assertEqual(row.credited, Fraction(0))
        self.assertTrue(row.shortfall)

    def test_later_revisions_do_not_change_old_slices(self):
        rows = self.ledger.report((2025, 10), self.as_of_v1)
        self.assertEqual(rows[("erin", "music")].credited, Fraction(120))
        rows = self.ledger.report((2025, 10), self.as_of_v2)
        self.assertEqual(rows[("erin", "music")].credited, Fraction(0))


if __name__ == "__main__":
    unittest.main()
