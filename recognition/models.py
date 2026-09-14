"""数据模型

对应 ``materials_management.sql`` 中的 6 张表：

旧表（MySQL）        新模型（Django ORM）      说明
------------------  ------------------------  ----------------------------------
user_info           auth.User + Profile       密码改为 Django 哈希存储
login_info          LoginLog                  登录/登出/注册/加用户
log_info            IdentifyLog               识别与反馈日志
material_name       Material.name             四张物料表按 material_id 合并成
material_info       Material.info             一张 Material 表，查询不再需要
material_location   Material.location         原来的三重 LEFT JOIN
material_count      Material.count
"""

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


class Profile(models.Model):
    """用户权限：等价于旧表 user_info.permission 字段。"""

    ADMIN = "admin"
    USER = "user"
    PERMISSION_CHOICES = [(ADMIN, "管理员"), (USER, "普通用户")]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name="用户",
    )
    permission = models.CharField(
        "权限", max_length=16, choices=PERMISSION_CHOICES, default=USER
    )

    class Meta:
        verbose_name = "用户权限"
        verbose_name_plural = "用户权限"

    def __str__(self) -> str:
        return f"{self.user.username}（{self.get_permission_display()}）"

    @property
    def is_admin(self) -> bool:
        return self.permission == self.ADMIN


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_profile(sender, instance, created, **kwargs):  # noqa: ARG001
    """新建用户时自动补一条 Profile，超级用户默认管理员。"""
    default = Profile.ADMIN if instance.is_superuser else Profile.USER
    Profile.objects.get_or_create(user=instance, defaults={"permission": default})


def is_admin_user(user) -> bool:
    """统一的“是否管理员”判断。"""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, "profile", None)
    return bool(profile and profile.is_admin)


class LoginLog(models.Model):
    """登录日志（旧表 login_info）。"""

    ACTION_LOGIN = "login"
    ACTION_LOGOUT = "logout"
    ACTION_REGISTER = "register"
    ACTION_ADDUSER = "adduser"
    ACTION_CHOICES = [
        (ACTION_LOGIN, "登录"),
        (ACTION_LOGOUT, "登出"),
        (ACTION_REGISTER, "注册"),
        (ACTION_ADDUSER, "添加用户"),
    ]

    username = models.CharField("用户名", max_length=150, db_index=True)
    # 字段名沿用旧表的 action / datetime 列名，方便与 SQL 数据对照
    action = models.CharField(
        "操作", max_length=32, choices=ACTION_CHOICES, default=ACTION_LOGIN, db_index=True
    )
    datetime = models.DateTimeField("时间", default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-datetime", "-id"]
        verbose_name = "登录日志"
        verbose_name_plural = "登录日志"

    def __str__(self) -> str:
        return f"{self.username} {self.action} @ {self.datetime:%Y-%m-%d %H:%M:%S}"


class IdentifyLog(models.Model):
    """材料识别/反馈日志（旧表 log_info）。"""

    ACTION_IDENTIFY = "identify"
    ACTION_FEEDBACK = "feedback"
    ACTION_CHOICES = [
        (ACTION_IDENTIFY, "识别"),
        (ACTION_FEEDBACK, "反馈"),
    ]

    username = models.CharField("操作用户", max_length=150, db_index=True)
    action = models.CharField(
        "操作", max_length=32, choices=ACTION_CHOICES, default=ACTION_IDENTIFY, db_index=True
    )
    datetime = models.DateTimeField("时间", default=timezone.now, db_index=True)
    material_name = models.CharField("识别材料", max_length=64, blank=True, default="")
    material_truename = models.CharField("实际材料", max_length=64, blank=True, default="")
    img_path = models.CharField("图片路径", max_length=255, blank=True, default="")

    # 以下两列是新增的：旧程序把置信度只放在内存里，因此“查看详情”只能对
    # 最后一次识别生效、且 graph.png 会被并发用户互相覆盖。落库后任何一条
    # 历史记录都能重新查看置信度图表。
    confidence = models.CharField("置信度", max_length=16, blank=True, default="")
    confidences = models.JSONField("全类别置信度", default=dict, blank=True)

    # 旧程序用 “DELETE 最新一行 + INSERT 一行” 表示反馈，并发时会误删他人记录。
    # 这里改为新增一行并用外键指向被纠正的识别记录，识别历史保持完整。
    corrects = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="corrections",
        verbose_name="反馈对应的识别记录",
    )

    class Meta:
        ordering = ["-datetime", "-id"]
        verbose_name = "材料识别日志"
        verbose_name_plural = "材料识别日志"
        indexes = [models.Index(fields=["action", "-datetime"])]

    def __str__(self) -> str:
        return f"{self.username} {self.action} {self.material_name} @ {self.datetime:%Y-%m-%d %H:%M:%S}"

    @property
    def img_url(self):
        """图片可访问 URL；文件不存在（如导入的历史路径）时返回 None。"""
        if not self.img_path:
            return None
        try:
            if default_storage.exists(self.img_path):
                return default_storage.url(self.img_path)
        except Exception:  # noqa: BLE001 - 存储后端异常不应导致页面 500
            return None
        return None

    @property
    def material_label(self) -> str:
        return settings.MATERIAL_CLASS_LABELS.get(self.material_name, self.material_name)

    @property
    def truename_label(self) -> str:
        return settings.MATERIAL_CLASS_LABELS.get(
            self.material_truename, self.material_truename
        )

    @property
    def confidence_rows(self):
        """``[(类别, 中文名, 浮点置信度)]``，按置信度降序，供模板画条形图。"""
        rows = []
        for name, raw in (self.confidences or {}).items():
            try:
                value = float(str(raw).rstrip("%"))
            except (TypeError, ValueError):
                continue
            rows.append((name, settings.MATERIAL_CLASS_LABELS.get(name, name), value))
        rows.sort(key=lambda item: item[2], reverse=True)
        return rows


class Material(models.Model):
    """物料主数据。旧库 4 张表（name/info/location/count）按 material_id 合并。"""

    # 保留 material_id 这个主键名与取值（1~9），AutoField 便于后台新增
    material_id = models.AutoField(primary_key=True, verbose_name="材料编号")
    name = models.CharField(
        "材料名称", max_length=64, unique=True, db_column="material_name"
    )
    info = models.TextField("材料简介", blank=True, default="", db_column="material_info")
    location = models.CharField(
        "产地", max_length=255, blank=True, default="", db_column="material_location"
    )
    # 旧库该列是 varchar，且程序允许写入任意文本，这里保持 CharField 不改语义
    count = models.CharField(
        "库存", max_length=255, blank=True, default="", db_column="material_count"
    )

    class Meta:
        ordering = ["material_id"]
        verbose_name = "材料"
        verbose_name_plural = "材料"

    def __str__(self) -> str:
        return f"{self.material_id}-{self.name}"

    @property
    def label(self) -> str:
        return settings.MATERIAL_CLASS_LABELS.get(self.name, self.name)

    @classmethod
    def for_class(cls, class_name: str):
        """按模型预测出的类别名取材料记录（旧 main.py 的两步 SELECT）。"""
        if not class_name:
            return None
        return cls.objects.filter(name=class_name).first()
