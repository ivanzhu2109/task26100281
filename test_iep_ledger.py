import unittest
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from zoneinfo import ZoneInfo

from iep_ledger import Ledger, MandateRecord, SessionRecord

NY = ZoneInfo("America/New_York")
EDT = timezone(timedelta(hours=-4))
EST = timezone(timedelta(hours=-5))
UTC = timezone.utc


def mandate(record_id, recorded_at, student, service, start, end, weekly, voided=False):
    return MandateRecord(
        id=record_id,
        recorded_at=recorded_at,
        voided=voided,
        student=student,
        service=service,
        start_date=start,
        end_date=end,
        weekly_minutes=weekly,
    )


def session(record_id, recorded_at, student, service, start, end, voided=False):
    return SessionRecord(
        id=record_id,
        recorded_at=recorded_at,
        voided=voided,
        student=student,
        service=service,
        start=start,
        end=end,
    )


class CrossMonthWeekTest(unittest.TestCase):
    """场景一:授权跨 3/4 月,会话全在 4 月,3 月仍按工作日比例计入。"""

    def test_minutes_allocated_by_weekdays_not_by_session_month(self):
        ledger = Ledger()
        t0 = datetime(2025, 4, 10, tzinfo=UTC)
        ledger.record_mandate(
            mandate("m1", t0, "alice", "speech",
                    date(2025, 3, 31), date(2025, 4, 7), 100)
        )
        ledger.record_session(
            session("s1", t0, "alice", "speech",
                    datetime(2025, 4, 2, 10, 0, tzinfo=EDT),
                    datetime(2025, 4, 2, 10, 50, tzinfo=EDT))
        )
        as_of = datetime(2025, 4, 11, tzinfo=UTC)

        march = ledger.report("2025-03", as_of)
        self.assertEqual(march[("alice", "speech")].required, Fraction(20))
        self.assertEqual(march[("alice", "speech")].credited, Fraction(10))
        self.assertTrue(march[("alice", "speech")].shortfall)

        april = ledger.report("2025-04", as_of)
        self.assertEqual(april[("alice", "speech")].required, Fraction(80))
        self.assertEqual(april[("alice", "speech")].credited, Fraction(40))
        self.assertTrue(april[("alice", "speech")].shortfall)


class MidWeekMandateChangeTest(unittest.TestCase):
    """场景二:同周授权变更、重叠会话、跨边界会话、85% 临界。"""

    def setUp(self):
        self.ledger = Ledger()
        t0 = datetime(2025, 4, 10, tzinfo=UTC)
        # bob/speech 同一周两段授权:周一至周三 100/周,周四至周五 100/周
        self.ledger.record_mandate(
            mandate("m1", t0, "bob", "speech",
                    date(2025, 3, 31), date(2025, 4, 3), 100)
        )
        self.ledger.record_mandate(
            mandate("m2", t0, "bob", "speech",
                    date(2025, 4, 3), date(2025, 4, 7), 100)
        )
        # 重叠会话:并集 90 分钟,超出片段一 R=60 的部分不得挪用
        self.ledger.record_session(
            session("s1", t0, "bob", "speech",
                    datetime(2025, 3, 31, 10, 0, tzinfo=EDT),
                    datetime(2025, 3, 31, 11, 0, tzinfo=EDT))
        )
        self.ledger.record_session(
            session("s2", t0, "bob", "speech",
                    datetime(2025, 3, 31, 10, 30, tzinfo=EDT),
                    datetime(2025, 3, 31, 11, 30, tzinfo=EDT))
        )
        # 跨生效边界的会话:周三 23:30 至周四 00:30,各计 30 分钟
        self.ledger.record_session(
            session("s3", t0, "bob", "speech",
                    datetime(2025, 4, 2, 23, 30, tzinfo=EDT),
                    datetime(2025, 4, 3, 0, 30, tzinfo=EDT))
        )
        # carol:恰好 85%;dave:少一秒
        for student, seconds in (("carol", 85 * 60), ("dave", 85 * 60 - 1)):
            self.ledger.record_mandate(
                mandate(f"m-{student}", t0, student, "ot",
                        date(2025, 3, 31), date(2025, 4, 7), 100)
            )
            start = datetime(2025, 4, 1, 9, 0, tzinfo=EDT)
            self.ledger.record_session(
                session(f"s-{student}", t0, student, "ot",
                        start, start + timedelta(seconds=seconds))
            )
        self.as_of = datetime(2025, 4, 11, tzinfo=UTC)

    def test_overlap_counted_once_and_no_cross_segment_credit(self):
        april = self.ledger.report("2025-04", self.as_of)
        line = april[("bob", "speech")]
        # 片段一:R=60,L=90+30=120,C=60(超额 60 分钟作废)
        # 片段二:R=40,L=30,C=30(不得用片段一超额部分补足)
        # 4 月工作日:片段一 2/5... 即 2/3,片段二 2/2
        self.assertEqual(line.required, Fraction(60) * Fraction(2, 3) + Fraction(40))
        self.assertEqual(line.credited, Fraction(60) * Fraction(2, 3) + Fraction(30))
        self.assertEqual(line.required, Fraction(80))
        self.assertEqual(line.credited, Fraction(70))
        # 70/80 = 87.5%,不标记不足
        self.assertFalse(line.shortfall)

    def test_march_slice_of_first_segment(self):
        march = self.ledger.report("2025-03", self.as_of)
        line = march[("bob", "speech")]
        self.assertEqual(line.required, Fraction(20))
        self.assertEqual(line.credited, Fraction(20))
        self.assertFalse(line.shortfall)

    def test_exactly_85_percent_not_flagged_but_one_second_less_is(self):
        april = self.ledger.report("2025-04", self.as_of)
        carol = april[("carol", "ot")]
        self.assertEqual(carol.required, Fraction(80))
        self.assertEqual(carol.credited, Fraction(68))
        self.assertFalse(carol.shortfall)

        dave = april[("dave", "ot")]
        self.assertEqual(dave.required, Fraction(80))
        self.assertEqual(dave.credited, Fraction(85 * 60 - 1, 60) * Fraction(4, 5))
        self.assertTrue(dave.shortfall)


class DstRevisionOutOfOrderTest(unittest.TestCase):
    """场景三:跨夏令时回拨的会话,乱序修订与作废,多切面查询。"""

    T1 = datetime(2025, 11, 3, 12, 0, tzinfo=UTC)
    T2 = datetime(2025, 11, 4, 12, 0, tzinfo=UTC)
    T3 = datetime(2025, 11, 5, 12, 0, tzinfo=UTC)

    def setUp(self):
        self.ledger = Ledger()
        t0 = datetime(2025, 11, 1, 12, 0, tzinfo=UTC)
        for service in ("pt", "ot"):
            self.ledger.record_mandate(
                mandate(f"m-{service}", t0, "erin", service,
                        date(2025, 10, 27), date(2025, 11, 3), 240)
            )
        # 会话:2025-11-02 00:30 EDT 至 03:30 EST,真实经过 4 小时(240 分钟)
        start = datetime(2025, 11, 2, 0, 30, tzinfo=EDT)
        end = datetime(2025, 11, 2, 3, 30, tzinfo=EST)
        v1 = session("s1", self.T1, "erin", "pt", start, end)
        v2 = session("s1", self.T2, "erin", "ot", start, end)
        v3 = session("s1", self.T3, "erin", "ot", start, end, voided=True)
        # 乱序加入:作废版最先,初版其次,修订版最后
        self.ledger.record_session(v3)
        self.ledger.record_session(v1)
        self.ledger.record_session(v2)

    def test_slice_after_first_record(self):
        report = self.ledger.report("2025-10", self.T1 + timedelta(hours=1))
        pt = report[("erin", "pt")]
        # 真实经过 240 分钟(非钟面 180 分钟),足额
        self.assertEqual(pt.required, Fraction(240))
        self.assertEqual(pt.credited, Fraction(240))
        self.assertFalse(pt.shortfall)
        ot = report[("erin", "ot")]
        self.assertEqual(ot.credited, Fraction(0))
        self.assertTrue(ot.shortfall)

    def test_slice_after_revision_moves_service(self):
        report = self.ledger.report("2025-10", self.T2 + timedelta(hours=1))
        pt = report[("erin", "pt")]
        self.assertEqual(pt.credited, Fraction(0))
        self.assertTrue(pt.shortfall)
        ot = report[("erin", "ot")]
        self.assertEqual(ot.credited, Fraction(240))
        self.assertFalse(ot.shortfall)

    def test_slice_after_void(self):
        report = self.ledger.report("2025-10", self.T3 + timedelta(hours=1))
        for service in ("pt", "ot"):
            line = report[("erin", service)]
            self.assertEqual(line.required, Fraction(240))
            self.assertEqual(line.credited, Fraction(0))
            self.assertTrue(line.shortfall)

    def test_later_revisions_do_not_change_old_slices(self):
        # 全部修订加入后,旧切面结果不变
        self.test_slice_after_first_record()
        self.test_slice_after_revision_moves_service()


if __name__ == "__main__":
    unittest.main()
