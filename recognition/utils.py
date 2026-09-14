"""小工具函数。"""

from __future__ import annotations

import datetime

from django.utils import timezone


def local_today() -> datetime.date:
    """当前本地日期。

    注意：本工程 ``USE_TZ = False``（为了与原库的本地时间字符串保持一致），
    此时 ``django.utils.timezone.localdate()`` 会抛
    ``ValueError: localtime() cannot be applied to a naive datetime``。
    这里做一层兼容，两种设置下都能拿到正确的本地日期。
    """
    now = timezone.now()
    if timezone.is_aware(now):
        now = timezone.localtime(now)
    return now.date()
