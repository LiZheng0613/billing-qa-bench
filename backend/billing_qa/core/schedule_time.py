"""定时执行的时间计算（服务器本地时间，naive datetime）。

批量任务支持四种执行方式：

- ``IMMEDIATE``：立即执行
- ``SCHEDULED``：指定时间执行一次
- ``DAILY``：每天固定时刻执行
- ``WEEKLY``：每周固定几天、固定时刻执行（1=周一 … 7=周日）

所有时间都按服务器本地时间解释，与 ``billing_qa.db.base.now`` 保持一致。
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta

TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def normalize_local(value: datetime | None) -> datetime | None:
    """把带时区的时间转为服务器本地时间的 naive datetime。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def parse_time_text(value: str) -> time:
    """解析 ``HH:MM``，格式非法时抛 ``ValueError``。"""
    match = TIME_PATTERN.match((value or "").strip())
    if match is None:
        raise ValueError("时刻格式需为 HH:MM（24 小时制）")
    return time(int(match.group(1)), int(match.group(2)))


def normalize_weekdays(value: list[int] | None) -> list[int]:
    """去重并升序排列星期（1=周一 … 7=周日）。"""
    days = sorted({int(item) for item in (value or [])})
    if not days:
        raise ValueError("每周执行至少需要选择一个星期")
    invalid = [item for item in days if item < 1 or item > 7]
    if invalid:
        raise ValueError("星期取值为 1~7（1=周一 … 7=周日）")
    return days


def compute_next_run(
    *,
    schedule_type: str,
    schedule_time: str,
    weekdays: list[int] | None = None,
    base: datetime | None = None,
) -> datetime:
    """计算严格晚于 ``base`` 的下一次触发时间。"""
    current = base or datetime.now()
    at = parse_time_text(schedule_time)

    if schedule_type == "DAILY":
        candidate = datetime.combine(current.date(), at)
        if candidate <= current:
            candidate += timedelta(days=1)
        return candidate

    if schedule_type == "WEEKLY":
        days = normalize_weekdays(weekdays)
        for offset in range(0, 8):
            day = current.date() + timedelta(days=offset)
            # Python 的 isoweekday() 天然是 1=周一 … 7=周日
            if day.isoweekday() not in days:
                continue
            candidate = datetime.combine(day, at)
            if candidate > current:
                return candidate
        # 理论上不会走到这里，留给极端边界兜底
        return datetime.combine(current.date() + timedelta(days=7), at)

    raise ValueError(f"不支持的周期执行方式: {schedule_type}")
