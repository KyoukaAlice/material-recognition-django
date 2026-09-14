"""清空日志，并把记录编号重置为从 1 开始。

Django 的自增主键在 SQLite 下由 ``sqlite_sequence`` 维护，删掉数据并不会让编号
回到 1（下一条会接着上次的最大值继续）。本命令在清空数据后同步重置自增计数器，
因此适合在演示或答辩前把编号恢复成从 1 递增。

用法::

    python manage.py reset_logs                    # 清空识别日志与登录日志，编号都从 1 开始
    python manage.py reset_logs --identify-only    # 只清识别日志
    python manage.py reset_logs --login-only       # 只清登录日志
    python manage.py reset_logs --reset-only       # 不删数据，仅重置计数器
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection

from recognition.models import IdentifyLog, LoginLog


class Command(BaseCommand):
    help = "清空识别日志 / 登录日志，并将自增编号重置为从 1 开始"

    def add_arguments(self, parser):
        parser.add_argument("--identify-only", action="store_true", help="只处理识别日志")
        parser.add_argument("--login-only", action="store_true", help="只处理登录日志")
        parser.add_argument(
            "--reset-only", action="store_true", help="只重置自增计数器，不删除任何数据"
        )

    def handle(self, *args, **options):
        if options["identify_only"] and options["login_only"]:
            self.stderr.write(self.style.ERROR("--identify-only 与 --login-only 不能同时使用"))
            return

        targets = []
        if not options["login_only"]:
            targets.append(("识别日志", IdentifyLog))
        if not options["identify_only"]:
            targets.append(("登录日志", LoginLog))

        for label, model in targets:
            table = model._meta.db_table

            if options["reset_only"]:
                deleted = 0
            else:
                deleted = model.objects.all().delete()[0]

            reset_ok = self._reset_sequence(table)
            self.stdout.write(
                f"{label}：删除 {deleted} 条，编号重置 {'成功' if reset_ok else '跳过'}（表 {table}）"
            )

        if not options["reset_only"]:
            self.stdout.write(self.style.SUCCESS("完成，下一次新增记录的编号将从 1 开始。"))

    # ------------------------------------------------------------------
    def _reset_sequence(self, table: str) -> bool:
        """把指定表的自增计数器归零。"""
        vendor = connection.vendor
        with connection.cursor() as cursor:
            if vendor == "sqlite":
                # SQLite 使用 AUTOINCREMENT 时计数保存在 sqlite_sequence
                cursor.execute("DELETE FROM sqlite_sequence WHERE name = %s", [table])
                return True
            if vendor == "mysql":
                cursor.execute(f"ALTER TABLE `{table}` AUTO_INCREMENT = 1")
                return True
            if vendor == "postgresql":
                cursor.execute(
                    "SELECT setval(pg_get_serial_sequence(%s, 'id'), 1, false)", [table]
                )
                return True
        self.stderr.write(
            self.style.WARNING(f"  （{vendor} 不支持自动重置自增计数器，请手动处理）")
        )
        return False
