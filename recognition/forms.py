"""表单定义

原 PyQt5 程序用 ``QLineEdit`` / ``QComboBox`` / ``QDateEdit`` 收集输入，逐条手写
SQL 校验；这里改为 Django Form，把“非空校验、日期解析、权限选项、密码强度”
统一交给框架处理，视图只负责业务逻辑。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import List, Tuple

from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import LoginLog, Material, Profile
from .utils import local_today

# 浏览器摄像头抓拍得到的是 dataURL，例如 data:image/jpeg;base64,xxxx
DATA_URL_RE = re.compile(r"^data:image/(png|jpe?g|webp);base64,[A-Za-z0-9+/=\s]+$", re.I)

TEXT_INPUT = {"class": "input"}


class DateInput(forms.DateInput):
    input_type = "date"


# ==========================================================================
# 认证相关（对应 login_ui / register_ui / addUser_ui）
# ==========================================================================
class LoginForm(forms.Form):
    """登录表单（旧 QLoginWin）。"""

    username = forms.CharField(
        label="用户名",
        max_length=150,
        widget=forms.TextInput(attrs={"placeholder": "请输入用户名", **TEXT_INPUT}),
    )
    password = forms.CharField(
        label="密　码",
        widget=forms.PasswordInput(attrs={"placeholder": "请输入密码", **TEXT_INPUT}),
    )

    def clean_username(self):
        return self.cleaned_data["username"].strip()


class RegisterForm(UserCreationForm):
    """自助注册（旧 QRegisterWin）。

    原程序只收 用户名+密码 且明文入库；这里改用 ``UserCreationForm``，
    获得二次确认与 Django 密码强度校验，密码以 PBKDF2 哈希存储。
    注册出来的账号固定为普通用户（与原程序写死 permission='user' 一致）。
    """

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "用户名"
        self.fields["username"].widget.attrs.update({"placeholder": "请输入用户名", **TEXT_INPUT})
        self.fields["password1"].label = "密　码"
        self.fields["password1"].widget.attrs.update({"placeholder": "请输入密码", **TEXT_INPUT})
        self.fields["password2"].label = "确认密码"
        self.fields["password2"].widget.attrs.update({"placeholder": "请再次输入密码", **TEXT_INPUT})


class AdminUserCreateForm(RegisterForm):
    """管理员添加用户（旧 QAddUserWin，可指定权限）。"""

    permission = forms.ChoiceField(
        label="权　限",
        choices=Profile.PERMISSION_CHOICES,
        initial=Profile.USER,
        widget=forms.Select(attrs=TEXT_INPUT),
    )

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            Profile.objects.update_or_create(
                user=user, defaults={"permission": self.cleaned_data["permission"]}
            )
        return user


# ==========================================================================
# 识别相关（对应 identify_ui / from_ui）
# ==========================================================================
@lru_cache(maxsize=1)
def _sample_choices() -> Tuple[Tuple[str, Tuple[Tuple[str, str], ...]], ...]:
    """扫描原项目 image/ 目录，生成“示例图片”下拉选项。

    原桌面程序只能“上传本地文件”或“打开摄像头”；浏览器里摄像头需要授权，
    因此额外提供示例图片，方便直接体验识别流程。
    """
    root = Path(settings.MATERIAL_SAMPLE_DIR)
    if not root.is_dir():
        return ()

    per_class = max(1, int(settings.MATERIAL_SAMPLE_PER_CLASS))
    groups: List[Tuple[str, Tuple[Tuple[str, str], ...]]] = []
    for class_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        files = sorted(
            (f for f in class_dir.iterdir() if f.is_file()),
            key=lambda f: (len(f.stem), f.stem),
        )[:per_class]
        if not files:
            continue
        items = tuple(
            (f"{class_dir.name}/{f.name}", f.name) for f in files
        )
        label = settings.MATERIAL_CLASS_LABELS.get(class_dir.name, class_dir.name)
        groups.append((f"{class_dir.name}（{label}）", items))
    return tuple(groups)


def sample_value_set() -> set:
    """所有合法的示例图片相对路径，用于防止路径穿越。"""
    return {value for _label, items in _sample_choices() for value, _name in items}


class IdentifyForm(forms.Form):
    """识别表单：三种图片来源任选其一。

    1. ``image``   —— 上传本地图片（旧 pushButton_openfile）
    2. ``capture`` —— 浏览器摄像头抓拍的 dataURL（旧 pushButton_opencap + 识别）
    3. ``sample``  —— 示例图片（新增，取自原项目 image/ 目录）
    """

    image = forms.FileField(
        label="上传图片",
        required=False,
        widget=forms.ClearableFileInput(
            attrs={"accept": "image/*", "class": "input-file", "id": "id_image"}
        ),
    )
    capture = forms.CharField(required=False, widget=forms.HiddenInput)
    sample = forms.ChoiceField(label="示例图片", required=False, widget=forms.Select)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = _sample_choices()
        self.fields["sample"].choices = [("", "— 选择示例图片 —")] + list(choices)
        if not choices:
            self.fields["sample"].widget.attrs["disabled"] = "disabled"
            self.fields["sample"].help_text = (
                f"未找到示例图片目录：{settings.MATERIAL_SAMPLE_DIR}"
            )

    def clean_capture(self):
        raw = (self.cleaned_data.get("capture") or "").strip()
        if raw and not DATA_URL_RE.match(raw):
            raise forms.ValidationError("摄像头抓拍数据格式不正确，请重新拍摄。")
        return raw

    def clean_image(self):
        upload = self.cleaned_data.get("image")
        if upload and upload.size > settings.FILE_UPLOAD_MAX_MEMORY_SIZE:
            raise forms.ValidationError("图片过大（上限 10 MB），请压缩后重试。")
        return upload

    def clean_sample(self):
        value = self.cleaned_data.get("sample") or ""
        # 只接受下拉框里列出过的相对路径，天然杜绝 ../../ 路径穿越
        if value and value not in sample_value_set():
            raise forms.ValidationError("示例图片不存在，请重新选择。")
        return value

    def clean(self):
        cleaned = super().clean()
        if not (cleaned.get("image") or cleaned.get("capture") or cleaned.get("sample")):
            raise forms.ValidationError("请先上传图片、使用摄像头拍摄，或选择一张示例图片。")
        return cleaned


class FeedbackForm(forms.Form):
    """反馈错误（旧 QFromWin）。

    原程序是一个自由文本框，用户可以随便填；这里改为固定下拉，
    保证写回数据库的“正确结果”一定是模型支持的 9 个类别之一。
    """

    true_name = forms.ChoiceField(label="正确结果", choices=(), widget=forms.Select(attrs=TEXT_INPUT))

    def __init__(self, *args, predicted: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["true_name"].choices = [
            (name, f"{name}（{settings.MATERIAL_CLASS_LABELS.get(name, name)}）")
            for name in settings.MATERIAL_CLASS_NAMES
        ]
        if predicted:
            self.fields["true_name"].initial = predicted


# ==========================================================================
# 查询筛选（对应 log_ui / userLog_ui / user_ui 的搜索栏）
# ==========================================================================
class DateRangeMixin(forms.Form):
    """原程序的两个 QDateEdit 默认都是“今天”，这里保持同样的初始值。"""

    start_date = forms.DateField(
        label="起始时间", required=False, widget=DateInput(attrs=TEXT_INPUT)
    )
    end_date = forms.DateField(
        label="结束时间", required=False, widget=DateInput(attrs=TEXT_INPUT)
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["start_date"].initial = local_today()
            self.fields["end_date"].initial = local_today()

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and start > end:
            raise forms.ValidationError("起始时间不能晚于结束时间。")
        return cleaned


class LogFilterForm(DateRangeMixin):
    """识别日志筛选，字段对应原“用户 / 操作 / 材料 / 时间”。"""

    username = forms.CharField(label="用户", required=False, widget=forms.TextInput(attrs=TEXT_INPUT))
    action = forms.ChoiceField(
        label="操作",
        required=False,
        choices=[("", "全部"), ("identify", "识别"), ("feedback", "反馈")],
        widget=forms.Select(attrs=TEXT_INPUT),
    )
    material = forms.CharField(label="材料", required=False, widget=forms.TextInput(attrs=TEXT_INPUT))

    def clean_username(self):
        return (self.cleaned_data.get("username") or "").strip()

    def clean_material(self):
        return (self.cleaned_data.get("material") or "").strip()


class LoginLogFilterForm(DateRangeMixin):
    """登录日志筛选，字段对应原“用户 / 操作 / 时间”。"""

    username = forms.CharField(label="用户", required=False, widget=forms.TextInput(attrs=TEXT_INPUT))
    action = forms.ChoiceField(
        label="操作",
        required=False,
        choices=[("", "全部")] + list(LoginLog.ACTION_CHOICES),
        widget=forms.Select(attrs=TEXT_INPUT),
    )

    def clean_username(self):
        return (self.cleaned_data.get("username") or "").strip()


class UserFilterForm(forms.Form):
    """用户管理筛选（旧 user_ui：ID / 用户 / 权限[All|Admin|User]）。"""

    user_id = forms.CharField(label="ID", required=False, widget=forms.TextInput(attrs=TEXT_INPUT))
    username = forms.CharField(label="用户", required=False, widget=forms.TextInput(attrs=TEXT_INPUT))
    permission = forms.ChoiceField(
        label="权限",
        required=False,
        choices=[("", "全部")] + Profile.PERMISSION_CHOICES,
        widget=forms.Select(attrs=TEXT_INPUT),
    )

    def clean_user_id(self):
        return (self.cleaned_data.get("user_id") or "").strip()

    def clean_username(self):
        return (self.cleaned_data.get("username") or "").strip()


class UserDeleteForm(forms.Form):
    """删除用户（旧 QDeleteUserWin：先选“用户名 / ID”，再填值）。"""

    FIELD_USERNAME = "username"
    FIELD_ID = "user_id"
    FIELD_CHOICES = [(FIELD_USERNAME, "用户名"), (FIELD_ID, "ID")]

    field = forms.ChoiceField(label="按", choices=FIELD_CHOICES, widget=forms.Select(attrs=TEXT_INPUT))
    value = forms.CharField(label="值", max_length=150, widget=forms.TextInput(attrs=TEXT_INPUT))

    def clean(self):
        cleaned = super().clean()
        field, value = cleaned.get("field"), (cleaned.get("value") or "").strip()
        if not value:
            raise forms.ValidationError("请输入要删除的用户信息！")
        if field == self.FIELD_ID and not value.isdigit():
            raise forms.ValidationError("ID 必须是数字。")

        if field == self.FIELD_ID:
            target = User.objects.filter(pk=int(value)).first()
        else:
            target = User.objects.filter(username=value).first()

        if target is None:
            label = "用户 ID" if field == self.FIELD_ID else "用户名"
            raise forms.ValidationError(f"该{label}不存在！")
        cleaned["target"] = target
        return cleaned


# ==========================================================================
# 材料管理（对应 update_ui）
# ==========================================================================
class MaterialUpdateForm(forms.Form):
    """修改材料的产地/库存（旧 QUpdateWin，两项都为空时不提交）。"""

    material = forms.ModelChoiceField(
        label="材　料", queryset=Material.objects.all(), widget=forms.Select(attrs=TEXT_INPUT)
    )
    location = forms.CharField(
        label="产　地", required=False, max_length=255, widget=forms.TextInput(attrs=TEXT_INPUT)
    )
    count = forms.CharField(
        label="库　存", required=False, max_length=255, widget=forms.TextInput(attrs=TEXT_INPUT)
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].label_from_instance = lambda obj: f"{obj.material_id} - {obj.name}（{obj.label}）"

    def clean(self):
        cleaned = super().clean()
        location = (cleaned.get("location") or "").strip()
        count = (cleaned.get("count") or "").strip()
        if not location and not count:
            raise forms.ValidationError("产地和库存至少要填写一项。")
        cleaned["location"], cleaned["count"] = location, count
        return cleaned

    def save(self) -> Material:
        material: Material = self.cleaned_data["material"]
        # 与原逻辑一致：只更新填写了的字段
        if self.cleaned_data["count"]:
            material.count = self.cleaned_data["count"]
        if self.cleaned_data["location"]:
            material.location = self.cleaned_data["location"]
        material.save(update_fields=["count", "location"])
        return material
