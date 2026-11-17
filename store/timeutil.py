"""时间约定：内部一律使用 UTC ISO 8601 定长字符串。

`YYYY-MM-DDTHH:MM:SSZ` 的字典序等于时间序，因此 SQL 里可以直接用
`<= >= >` 比较，不需要 SQLite 的日期函数。所有入口在写入前归一化。
"""

from __future__ import annotations

from datetime import datetime, timezone

_ISO = "%Y-%m-%dT%H:%M:%SZ"


def parse_ts(value: str | datetime) -> datetime:
    """解析时间；无时区按 UTC 处理。"""
    if isinstance(value, datetime):
        dt = value
    else:
        if not isinstance(value, str):
            raise TypeError(f"time must be str or datetime, got {type(value)!r}")
        s = value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def norm_ts(value: str | datetime) -> str:
    """归一化为 `YYYY-MM-DDTHH:MM:SSZ`。"""
    return parse_ts(value).strftime(_ISO)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime(_ISO)


def is_valid_ts(value: str) -> bool:
    try:
        norm_ts(value)
        return True
    except (TypeError, ValueError):
        return False
