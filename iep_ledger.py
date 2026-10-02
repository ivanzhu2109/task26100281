"""IEP 服务履约核算库。

口径:按周履约、按工作日归属月份。校区时区固定 America/New_York,
当地周从周一零点开始。仅使用标准库,单进程顺序调用,数据保存在内存。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from fractions import Fraction
from zoneinfo import ZoneInfo

CAMPUS_TZ = ZoneInfo("America/New_York")

SHORTFALL_RATIO = Fraction(85, 100)

_WEEKDAYS_PER_WEEK = 5


def _week_segments(start_date: date, end_date: date):
    """把授权日期区间切成与当地周(周一零点起)对齐的片段。

    区间含起点、不含终点;每片返回 (片段起始日期, 片段结束日期)。
    """
    monday = start_date - timedelta(days=start_date.weekday())
    while monday < end_date:
        seg_start = max(monday, start_date)
        seg_end = min(monday + timedelta(days=7), end_date)
        if seg_start < seg_end:
            yield seg_start, seg_end
        monday += timedelta(days=7)


def _count_weekdays(seg_start: date, seg_end: date, month: tuple[int, int] | None = None):
    """统计片段内工作日(周一至周五)数;给定 month 时只算落在该月的。"""
    count = 0
    day = seg_start
    while day < seg_end:
        if day.weekday() < _WEEKDAYS_PER_WEEK and (
            month is None or (day.year, day.month) == month
        ):
            count += 1
        day += timedelta(days=1)
    return count


def _local_midnight(day: date) -> datetime:
    """校区当地某日零点的绝对时刻。"""
    return datetime.combine(day, time.min, tzinfo=CAMPUS_TZ)


def _merge_intervals(sessions: list[SessionRecord]) -> list[tuple[datetime, datetime]]:
    """把会话起止区间合并为互不重叠的并集,重叠部分只算一次。"""
    merged: list[list[datetime]] = []
    for s in sorted(sessions, key=lambda r: (r.start, r.end)):
        if merged and s.start <= merged[-1][1]:
            if s.end > merged[-1][1]:
                merged[-1][1] = s.end
        else:
            merged.append([s.start, s.end])
    return [(a, b) for a, b in merged]


def _elapsed_minutes(start: datetime, end: datetime) -> Fraction:
    """真实经过时间折算的分钟数,用时间差而非当地钟面。"""
    delta = end - start
    micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    return Fraction(micros, 60_000_000)


@dataclass(frozen=True)
class MandateRecord:
    """授权修订:同一 id 的多次修订以 recorded_at 区分版本。"""

    id: str
    recorded_at: datetime
    voided: bool
    student: str
    service: str
    start_date: date
    end_date: date
    weekly_minutes: int


@dataclass(frozen=True)
class SessionRecord:
    """会话修订:同一 id 的多次修订以 recorded_at 区分版本。"""

    id: str
    recorded_at: datetime
    voided: bool
    student: str
    service: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class ReportRow:
    """某学生与某服务在报告月份的核算结果,分钟均为精确分数。"""

    required: Fraction
    credited: Fraction
    shortfall: bool


class Ledger:
    """履约核算账本:接收授权/会话修订,按历史切面出报告。"""

    def __init__(self) -> None:
        self._mandates: dict[str, list[MandateRecord]] = {}
        self._sessions: dict[str, list[SessionRecord]] = {}

    def record_mandate(self, record: MandateRecord) -> None:
        self._mandates.setdefault(record.id, []).append(record)

    def record_session(self, record: SessionRecord) -> None:
        self._sessions.setdefault(record.id, []).append(record)

    @staticmethod
    def _effective(records_by_id: dict[str, list], as_of: datetime) -> list:
        """按 id 取 recorded_at 不晚于 as_of 的最后一版;作废版视为不存在。

        与加入顺序无关,只比较记录时间。
        """
        selected = []
        for versions in records_by_id.values():
            visible = [v for v in versions if v.recorded_at <= as_of]
            if not visible:
                continue
            latest = max(visible, key=lambda v: v.recorded_at)
            if not latest.voided:
                selected.append(latest)
        return selected

    def report(
        self, month: tuple[int, int], as_of: datetime
    ) -> dict[tuple[str, str], ReportRow]:
        """返回 {(学生, 服务): ReportRow},分钟为精确分数,不提前取整。"""
        mandates = self._effective(self._mandates, as_of)
        sessions = self._effective(self._sessions, as_of)

        intervals_by_pair: dict[tuple[str, str], list[SessionRecord]] = {}
        for s in sessions:
            intervals_by_pair.setdefault((s.student, s.service), []).append(s)
        merged_by_pair = {
            pair: _merge_intervals(items) for pair, items in intervals_by_pair.items()
        }

        totals: dict[tuple[str, str], list[Fraction]] = {}
        for m in mandates:
            pair = (m.student, m.service)
            intervals = merged_by_pair.get(pair, [])
            for seg_start, seg_end in _week_segments(m.start_date, m.end_date):
                days = _count_weekdays(seg_start, seg_end)
                if days == 0:
                    continue
                required = Fraction(m.weekly_minutes * days, _WEEKDAYS_PER_WEEK)
                bound_start = _local_midnight(seg_start)
                bound_end = _local_midnight(seg_end)
                actual = Fraction(0)
                for iv_start, iv_end in intervals:
                    lo = max(iv_start, bound_start)
                    hi = min(iv_end, bound_end)
                    if hi > lo:
                        actual += _elapsed_minutes(lo, hi)
                credited = min(required, actual)
                month_days = _count_weekdays(seg_start, seg_end, month)
                if month_days == 0:
                    continue
                share = Fraction(month_days, days)
                slot = totals.setdefault(pair, [Fraction(0), Fraction(0)])
                slot[0] += required * share
                slot[1] += credited * share

        return {
            pair: ReportRow(
                required=req,
                credited=cred,
                shortfall=req > 0 and cred < req * SHORTFALL_RATIO,
            )
            for pair, (req, cred) in totals.items()
        }
