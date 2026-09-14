"""把原 MySQL 数据导入 Django。

用法::

    python manage.py import_legacy_data
    python manage.py import_legacy_data --flush          # 先清空业务数据再导入
    python manage.py import_legacy_data --dry-run        # 只解析、不写库
    python manage.py import_legacy_data --sql D:\\path\\materials_management.sql

原程序的数据都在 MySQL 的 ``materials_management`` 库里，仓库里有 Navicat 导出的
``materials_management.sql``。本命令直接解析这份 dump（不依赖 MySQL 客户端），
把 6 张表映射到 Django 模型：

    user_info        -> auth.User + Profile（密码用 set_password 哈希存储）
    login_info       -> LoginLog
    log_info         -> IdentifyLog
    material_name    -> Material.name
    material_info    -> Material.info
    material_location-> Material.location
    material_count   -> Material.count
"""

from __future__ import annotations

import re
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from recognition.models import IdentifyLog, LoginLog, Material, Profile

# MySQL dump 里的反斜杠转义
ESCAPES = {
    "0": "\0",
    "b": "\b",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "Z": "\x1a",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "%": "%",
    "_": "_",
}

INSERT_HEAD_RE = re.compile(r"INSERT\s+INTO\s+`?(\w+)`?\s+VALUES\s*", re.I)
DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S")


# ==========================================================================
# SQL dump 解析
# ==========================================================================
def _parse_tuple(text: str, pos: int):
    """从 ``text[pos] == '('`` 开始解析一个 VALUES 元组，返回 (值列表, 新位置)。"""
    assert text[pos] == "("
    pos += 1
    values = []
    chars = []
    in_string = False

    while pos < len(text):
        ch = text[pos]

        if in_string:
            if ch == "\\":  # 转义序列
                nxt = text[pos + 1] if pos + 1 < len(text) else ""
                chars.append(ESCAPES.get(nxt, nxt))
                pos += 2
                continue
            if ch == "'":
                if pos + 1 < len(text) and text[pos + 1] == "'":  # '' 表示一个单引号
                    chars.append("'")
                    pos += 2
                    continue
                in_string = False
                values.append("".join(chars))
                chars = []
                pos += 1
                continue
            chars.append(ch)
            pos += 1
            continue

        if ch == "'":
            in_string = True
            pos += 1
            continue
        if ch in " \t\r\n,":
            pos += 1
            continue
        if ch == ")":
            return values, pos + 1
        if ch == ";":  # 空元组 / 语句提前结束
            break

        # 裸 token：NULL、数字
        start = pos
        while pos < len(text) and text[pos] not in ",);":
            pos += 1
        token = text[start:pos].strip()
        values.append(None if token.upper() == "NULL" else token)

    raise ValueError("SQL 元组解析失败（引号未闭合？）")


def iter_inserts(sql_text: str):
    """遍历 dump 里所有 ``INSERT INTO ... VALUES (...)``，产出 (表名, 行列表)。"""
    pos = 0
    while True:
        match = INSERT_HEAD_RE.search(sql_text, pos)
        if not match:
            return
        table = match.group(1)
        pos = match.end()
        rows = []
        while True:
            while pos < len(sql_text) and sql_text[pos] in " \t\r\n,":
                pos += 1
            if pos >= len(sql_text):
                break
            if sql_text[pos] == ";":
                pos += 1
                break
            if sql_text[pos] != "(":
                break
            values, pos = _parse_tuple(sql_text, pos)
            rows.append(values)
        yield table, rows


def _parse_datetime(value):
    if not value:
        return None
    text = str(value).strip()
    for fmt in DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


# ==========================================================================
# 命令
# ==========================================================================
class Command(BaseCommand):
    help = "从原项目的 materials_management.sql 导入用户、物料与日志数据"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 同一个原图可能被多条日志引用，缓存避免重复复制
        self._image_cache = {}
        # 判重跳过的条数（命令可重复执行）
        self._skipped_identify = 0
        self._skipped_login = 0

    def add_arguments(self, parser):
        parser.add_argument(
            "--sql",
            default=str(Path(settings.LEGACY_DIR) / "materials_management.sql"),
            help="MySQL 导出文件路径",
        )
        parser.add_argument("--flush", action="store_true", help="导入前清空日志与物料数据")
        parser.add_argument("--dry-run", action="store_true", help="只解析并打印统计，不写数据库")
        parser.add_argument("--no-images", action="store_true", help="不映射历史图片路径")
        parser.add_argument(
            "--skip-existing-users",
            action="store_true",
            help="用户已存在时不重置密码（默认会按 dump 里的明文密码重设）",
        )

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        sql_path = Path(options["sql"])
        if not sql_path.is_file():
            raise CommandError(f"找不到 SQL 文件：{sql_path}")

        self.stdout.write(f"解析 SQL 文件：{sql_path}")
        text = sql_path.read_text(encoding="utf-8-sig", errors="replace")

        tables = OrderedDict()
        try:
            for table, rows in iter_inserts(text):
                tables.setdefault(table, []).extend(rows)
        except ValueError as exc:
            raise CommandError(f"SQL 解析失败：{exc}") from exc

        for name in sorted(tables):
            self.stdout.write(f"  - {name}: {len(tables[name])} 行")

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("--dry-run：未写入数据库。"))
            return

        self._image_cache = {}
        copy_images = not options["no_images"]
        with transaction.atomic():
            if options["flush"]:
                counts = (
                    IdentifyLog.objects.all().delete()[0],
                    LoginLog.objects.all().delete()[0],
                    Material.objects.all().delete()[0],
                )
                self.stdout.write(
                    self.style.WARNING(
                        f"--flush：已清空 识别日志 {counts[0]} 条 / 登录日志 {counts[1]} 条 / 材料 {counts[2]} 条"
                    )
                )

            n_material = self._import_materials(tables)
            n_user, n_profile = self._import_users(tables, options)
            n_log = self._import_identify_logs(tables, copy_images)
            n_login = self._import_login_logs(tables)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("导入完成："))
        self.stdout.write(f"  材料 Material      : {n_material}")
        self.stdout.write(f"  用户 User          : {n_user}（权限档案 {n_profile}）")
        self.stdout.write(f"  识别日志 IdentifyLog: {n_log}")
        self.stdout.write(f"  登录日志 LoginLog   : {n_login}")
        if self._skipped_identify or self._skipped_login:
            self.stdout.write(
                f"  （判重跳过：识别日志 {self._skipped_identify} 条 / "
                f"登录日志 {self._skipped_login} 条，命令可安全重复执行）"
            )
        self.stdout.write("")
        self.stdout.write("默认管理员账号：Admin / 123456（来自 dump，请尽快修改密码）")

    # ------------------------------------------------------------------
    def _import_materials(self, tables) -> int:
        """4 张物料表按 material_id 合并成一个 Material。"""
        by_id = {}

        def slot(material_id):
            return by_id.setdefault(material_id, {"name": "", "info": "", "location": "", "count": ""})

        for row in tables.get("material_name", []):
            if len(row) >= 2:
                slot(str(row[0]))["name"] = row[1] or ""
        for row in tables.get("material_info", []):
            if len(row) >= 2:
                slot(str(row[0]))["info"] = row[1] or ""
        for row in tables.get("material_location", []):
            if len(row) >= 2:
                slot(str(row[0]))["location"] = row[1] or ""
        for row in tables.get("material_count", []):
            if len(row) >= 2:
                slot(str(row[0]))["count"] = row[1] or ""

        created = 0
        for material_id, fields in by_id.items():
            if not fields["name"]:
                continue  # 没有名称的记录无法与模型类别对应，跳过
            Material.objects.update_or_create(
                material_id=int(material_id),
                defaults={
                    "name": fields["name"],
                    "info": fields["info"],
                    "location": fields["location"],
                    "count": fields["count"],
                },
            )
            created += 1

        # 补全 dump 里缺失、但模型类别需要的材料（避免识别后查不到材料信息）
        for name in settings.MATERIAL_CLASS_NAMES:
            if not Material.objects.filter(name=name).exists():
                Material.objects.create(name=name)
                self.stdout.write(f"  + 补充缺失材料：{name}")
                created += 1
        return created

    # ------------------------------------------------------------------
    def _import_users(self, tables, options) -> tuple:
        created = updated = profiles = 0
        for row in tables.get("user_info", []):
            if len(row) < 4:
                continue
            _uid, username, password, permission = row[0], row[1], row[2], row[3]
            if not username:
                continue

            user = User.objects.filter(username=username).first()
            if user is None:
                user = User(username=username)
                if password:
                    user.set_password(password)  # 明文 -> PBKDF2 哈希
                user.save()
                created += 1
            else:
                if password and not options["skip_existing_users"]:
                    user.set_password(password)
                    user.save(update_fields=["password"])
                updated += 1

            # 旧程序的 'admin'/'user' 只映射到本应用的 Profile，
            # 不授予 Django admin 站点权限（那需要 createsuperuser）
            perm = Profile.ADMIN if str(permission).strip().lower() == Profile.ADMIN else Profile.USER
            Profile.objects.update_or_create(user=user, defaults={"permission": perm})
            profiles += 1

        return created + updated, profiles

    # ------------------------------------------------------------------
    def _import_login_logs(self, tables) -> int:
        count = skipped = 0
        for row in tables.get("login_info", []):
            if len(row) < 3:
                continue
            username, action, when = row[0], row[1], _parse_datetime(row[2])
            if not username or not when:
                continue
            action = action or "login"
            # 让命令可以重复执行：同一条日志不重复写入
            if LoginLog.objects.filter(
                username=username, action=action, datetime=when
            ).exists():
                skipped += 1
                continue
            LoginLog.objects.create(username=username, action=action, datetime=when)
            count += 1
        self._skipped_login = skipped
        return count

    # ------------------------------------------------------------------
    def _import_identify_logs(self, tables, copy_images: bool) -> int:
        count = skipped = 0
        for row in tables.get("log_info", []):
            if len(row) < 6:
                continue
            username, action, when, material, truename, img_path = row[:6]
            when = _parse_datetime(when)
            if not username or not when:
                continue
            action = action or IdentifyLog.ACTION_IDENTIFY
            # 让命令可以重复执行：按“用户+操作+时间+材料”判重
            if IdentifyLog.objects.filter(
                username=username, action=action, datetime=when, material_name=material or ""
            ).exists():
                skipped += 1
                continue
            stored_path = self._map_image(img_path, copy_images) if copy_images else ""
            IdentifyLog.objects.create(
                username=username,
                action=action,
                datetime=when,
                material_name=material or "",
                material_truename=truename or "",
                img_path=stored_path,
                # 旧表没有置信度列，历史记录无法还原概率分布
                confidence="",
                confidences={},
            )
            count += 1
        self._skipped_identify = skipped
        return count

    # ------------------------------------------------------------------
    def _map_image(self, legacy_path, copy_images: bool) -> str:
        """把旧的 Windows 绝对路径映射到 media/ 下的文件。

        dump 里的路径形如
        ``D:/Python files/pytorch_model/dataset/val/copper_pipes/16.jpg``，
        而仓库里的 ``pytorch_model/image/<类别>/<文件名>`` 保存了完整数据集，
        因此按“类别 + 文件名”找回原图并复制到 media/legacy/ 下。
        """
        if not legacy_path:
            return ""
        if legacy_path in self._image_cache:
            return self._image_cache[legacy_path]

        normalized = str(legacy_path).replace("\\", "/")
        parts = [p for p in normalized.split("/") if p]
        result = ""
        if len(parts) >= 2 and copy_images:
            class_name, filename = parts[-2], parts[-1]
            candidates = [
                Path(normalized),  # 本机恰好存在原路径
                Path(settings.MATERIAL_SAMPLE_DIR) / class_name / filename,
                Path(settings.LEGACY_DIR) / "dataset" / "train" / class_name / filename,
                Path(settings.LEGACY_DIR) / "dataset" / "val" / class_name / filename,
                Path(settings.LEGACY_DIR) / "dataset" / "test" / class_name / filename,
            ]
            source = next((p for p in candidates if p.is_file()), None)
            if source is not None:
                relative = f"legacy/{class_name}/{filename}"
                try:
                    if not default_storage.exists(relative):
                        with source.open("rb") as handle:
                            default_storage.save(relative, File(handle))
                    result = relative
                except Exception as exc:  # noqa: BLE001
                    self.stderr.write(f"  图片复制失败 {source}: {exc}")

        self._image_cache[legacy_path] = result
        return result
