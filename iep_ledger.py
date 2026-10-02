"""IEP 服务履约核算库(纯标准库,单进程,内存存储)。

口径:按周履约、按工作日归属月份。校区时区固定 America/New_York,
周从当地周一零点开始,工作日为周一至周五(不考虑节假日)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from fractions import Fraction
from zoneinfo import ZoneInfo

CAMPUS_TZ_NAME = "America/New_York"

_CAMPUS_TZ = ZoneInfo(CAMPUS_TZ_NAME)
_SHORTFALL_THRESHOLD = Fraction(85, 100)


@dataclass(frozen=True)
class MandateRecord:
    """授权修订:同一 id 的多版按 recorded_at 取不晚于 as_of 的最后一版。"""

    id: str
    recorded_at: datetime  # 带 UTC 偏移
    voided: bool
    student: str
    service: str
    start_date: date  # 校区当地日期,含
    end_date: date  # 校区当地日期,不含
    weekly_minutes: int


@dataclass(frozen=True)
class SessionRecord:
    """会话修订:起止时间均带 UTC 偏移。"""

    id: str
    recorded_at: datetime  # 带 UTC 偏移
    voided: bool
    student: str
    service: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class ReportLine:
    """某学生与服务在报告月份的核算结果(精确有理数,不提前取整)。"""

    required: Fraction
    credited: Fraction
    shortfall: bool


class Ledger:
    def __init__(self) -> None:
        self._mandates: list[MandateRecord] = []
        self._sessions: list[SessionRecord] = []

    def record_mandate(self, record: MandateRecord) -> None:
        self._mandates.append(record)

    def record_session(self, record: SessionRecord) -> None:
        self._sessions.append(record)

    def report(self, month: str, as_of: datetime) -> dict[tuple[str, str], ReportLine]:
        month_start, month_end = _month_range(month)
        mandates = _effective_records(self._mandates, as_of)
        sessions = _effective_records(self._sessions, as_of)

        required: dict[tuple[str, str], Fraction] = {}
        credited: dict[tuple[str, str], Fraction] = {}

        for mandate in mandates:
            for seg_start, seg_end, weekdays in _segments(mandate):
                if weekdays == 0:
                    continue
                req = Fraction(mandate.weekly_minutes * weekdays, 5)
                seg_start_dt = datetime.combine(seg_start, time.min, _CAMPUS_TZ)
                seg_end_dt = datetime.combine(seg_end, time.min, _CAMPUS_TZ)
                actual = _overlap_minutes(
                    sessions, mandate.student, mandate.service,
                    seg_start_dt, seg_end_dt,
                )
                credit = min(req, actual)
                in_month = _weekdays_between(
                    max(seg_start, month_start), min(seg_end, month_end)
                )
                if in_month == 0:
                    continue
                key = (mandate.student, mandate.service)
                share = Fraction(in_month, weekdays)
                required[key] = required.get(key, Fraction(0)) + req * share
                credited[key] = credited.get(key, Fraction(0)) + credit * share

        report: dict[tuple[str, str], ReportLine] = {}
        for key in required:
            req = required[key]
            credit = credited[key]
            shortfall = req > 0 and credit < req * _SHORTFALL_THRESHOLD
            report[key] = ReportLine(required=req, credited=credit, shortfall=shortfall)
        return report


def _effective_records(records, as_of: datetime):
    """按 id 取 recorded_at 不晚于 as_of 的最后一版;作废版视为不存在。"""
    latest = {}
    for record in records:
        if record.recorded_at > as_of:
            continue
        current = latest.get(record.id)
        if current is None or record.recorded_at > current.recorded_at:
            latest[record.id] = record
    return [record for record in latest.values() if not record.voided]


def _month_range(month: str) -> tuple[date, date]:
    year, mon = (int(part) for part in month.split("-"))
    start = date(year, mon, 1)
    if mon == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, mon + 1, 1)
    return start, end


def _weekdays_between(start: date, end: date) -> int:
    """[start, end) 内周一至周五的天数。"""
    count = 0
    day = start
    while day < end:
        if day.weekday() < 5:
            count += 1
        day += timedelta(days=1)
    return count


def _segments(mandate: MandateRecord):
    """每个当地周与授权的交集:(起始日期, 结束日期, 工作日数)。"""
    monday = mandate.start_date - timedelta(days=mandate.start_date.weekday())
    while monday < mandate.end_date:
        week_end = monday + timedelta(days=7)
        seg_start = max(monday, mandate.start_date)
        seg_end = min(week_end, mandate.end_date)
        if seg_start < seg_end:
            yield seg_start, seg_end, _weekdays_between(seg_start, seg_end)
        monday = week_end


def _overlap_minutes(
    sessions, student: str, service: str,
    seg_start: datetime, seg_end: datetime,
) -> Fraction:
    """片段内有效会话的真实经过分钟数,重叠部分只算一次。"""
    intervals = []
    for session in sessions:
        if session.student != student or session.service != service:
            continue
        lo = max(session.start, seg_start)
        hi = min(session.end, seg_end)
        if lo < hi:
            intervals.append((lo, hi))
    intervals.sort()
    total = Fraction(0)
    cur_lo = cur_hi = None
    for lo, hi in intervals:
        if cur_hi is not None and lo <= cur_hi:
            if hi > cur_hi:
                cur_hi = hi
            continue
        if cur_hi is not None:
            total += _elapsed_minutes(cur_lo, cur_hi)
        cur_lo, cur_hi = lo, hi
    if cur_hi is not None:
        total += _elapsed_minutes(cur_lo, cur_hi)
    return total


def _elapsed_minutes(start: datetime, end: datetime) -> Fraction:
    """真实经过分钟数(按绝对时间差,不用当地钟面)。"""
    delta = end - start
    seconds = (
        delta.days * 86400
        + delta.seconds
        + Fraction(delta.microseconds, 1_000_000)
    )
    return seconds / 60
