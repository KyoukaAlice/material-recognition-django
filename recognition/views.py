"""视图

原 PyQt5 的 12 个窗口 -> URL 路由：

旧类（main.py）        新视图                        URL
--------------------  ---------------------------  ----------------------------------
QLoginWin             login_view / logout_view     /login/  /logout/
QRegisterWin          register_view                /register/
QIdentifyWin          identify / identify_result   /identify/  /identify/result/<id>/
QGraphWin             graph_view / graph_png       /identify/graph/<id>/
QFromWin              feedback                     /identify/feedback/<id>/
QLogWin               log_list                     /logs/
QUserLogWin           login_log_list               /logs/logins/
QUserWin              user_list                    /users/
QAddUserWin           user_create                  /users/add/
QDeleteUserWin        user_delete                  /users/delete/
QMaterialWin          material_list                /materials/
QUpdateWin            material_update              /materials/update/

权限：原程序只把“查看日志 / 用户管理”两个按钮 hide() 掉，任何人构造请求都能绕过。
这里用 ``admin_required`` 在服务端强制执行。
"""

from __future__ import annotations

import base64
import io
import logging
from functools import wraps
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from .forms import (
    AdminUserCreateForm,
    FeedbackForm,
    IdentifyForm,
    LogFilterForm,
    LoginForm,
    LoginLogFilterForm,
    MaterialUpdateForm,
    RegisterForm,
    UserDeleteForm,
    UserFilterForm,
)
from .ml import (
    ChartUnavailable,
    ModelNotAvailable,
    open_image,
    predict_image,
    render_prediction_chart,
)
from .models import IdentifyLog, LoginLog, Material, Profile, is_admin_user
from .utils import local_today

logger = logging.getLogger(__name__)


# ==========================================================================
# 通用工具
# ==========================================================================
def admin_required(view_func):
    """仅管理员可访问；未登录先跳登录页，已登录但非管理员返回 403。"""

    @wraps(view_func)
    def _check(request, *args, **kwargs):
        if not is_admin_user(request.user):
            logger.warning("非管理员 %s 尝试访问 %s", request.user, request.path)
            raise Http404("页面不存在或无权访问。")  # 不泄露“这里有个管理页”
        return view_func(request, *args, **kwargs)

    return login_required(_check)


def _paginate(request, queryset, per_page=None):
    paginator = Paginator(queryset, per_page or settings.PAGE_SIZE)
    return paginator.get_page(request.GET.get("page"))


def _filters_applied(request) -> bool:
    """筛选表单是否被提交过。

    原程序打开日志页时显示全部记录，只是把两个日期框预填成“今天”；
    所以只有真正点过“查询日志”才应用筛选条件。
    """
    return request.GET.get("f") == "1"


def _decode_data_url(raw: str) -> bytes:
    """把浏览器摄像头抓拍的 dataURL 解成字节。"""
    _, _, payload = raw.partition(",")
    try:
        data = base64.b64decode(payload, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("摄像头图片解码失败，请重新拍摄。") from exc
    if not data:
        raise ValueError("摄像头图片为空，请重新拍摄。")
    if len(data) > settings.FILE_UPLOAD_MAX_MEMORY_SIZE:
        raise ValueError("摄像头图片过大，请降低分辨率后重试。")
    return data


def _resolve_source(cleaned: dict) -> tuple:
    """按“上传 > 摄像头 > 示例图片”的优先级取出原始图片字节。"""
    upload = cleaned.get("image")
    if upload is not None:
        return upload.read(), upload.name

    if cleaned.get("capture"):
        return _decode_data_url(cleaned["capture"]), "camera-capture.jpg"

    sample = cleaned.get("sample")
    if sample:
        root = Path(settings.MATERIAL_SAMPLE_DIR).resolve()
        target = (root / sample).resolve()
        # 表单已校验取值在选项内，这里再做一次路径归属检查作为双保险
        if root not in target.parents or not target.is_file():
            raise ValueError("示例图片不存在。")
        return target.read_bytes(), target.name

    raise ValueError("没有可用的图片来源。")


def _normalize_and_store(raw: bytes) -> tuple:
    """校验并规范化图片，落盘后返回 ``(相对路径, JPEG字节)``。

    原程序直接把用户选的路径交给模型，文件的真实格式、方向、体积都不受控。
    这里统一走 PIL：确认是图片 -> 按 EXIF 摆正 -> 限制长边 -> 转存 JPEG，
    既保证推理输入一致，也避免把畸形文件留在服务器上。
    """
    try:
        image = open_image(raw)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    max_side = 1600
    if max(image.size) > max_side:
        image.thumbnail((max_side, max_side))

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90, optimize=True)
    data = buffer.getvalue()

    relative = f"uploads/{local_today():%Y/%m/%d}/{uuid4().hex}.jpg"
    stored = default_storage.save(relative, ContentFile(data))
    return stored, data


# ==========================================================================
# 认证：登录 / 登出 / 注册
# ==========================================================================
def login_view(request):
    """登录（旧 QLoginWin.login_main）。"""
    if request.user.is_authenticated:
        return redirect("recognition:identify")

    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            user = authenticate(
                request,
                username=form.cleaned_data["username"],
                password=form.cleaned_data["password"],
            )
            if user is not None and user.is_active:
                auth_login(request, user)
                # 原程序登录成功后往 login_info 写一条 action='login'
                LoginLog.objects.create(
                    username=user.username, action=LoginLog.ACTION_LOGIN
                )
                messages.success(request, f"欢迎回来，{user.username}！")
                return redirect(_safe_next(request) or "recognition:identify")
            form.add_error(None, "账号或密码输入错误，请检查输入是否正确")
    else:
        form = LoginForm()

    return render(request, "recognition/login.html", {"form": form})


def _safe_next(request):
    """校验 ?next= 参数，避免开放重定向。"""
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return None


@login_required
@require_POST
def logout_view(request):
    """登出（旧 QIdentifyWin.logout，会写一条 action='logout'）。"""
    LoginLog.objects.create(username=request.user.username, action=LoginLog.ACTION_LOGOUT)
    auth_logout(request)
    messages.success(request, "已退出登录。")
    return redirect("recognition:login")


def register_view(request):
    """自助注册（旧 QRegisterWin.register_main，权限固定为普通用户）。"""
    if request.user.is_authenticated:
        return redirect("recognition:identify")

    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            LoginLog.objects.create(
                username=user.username, action=LoginLog.ACTION_REGISTER
            )
            messages.success(request, "注册成功，请使用新账号登录。")
            return redirect("recognition:login")
    else:
        form = RegisterForm()

    return render(request, "recognition/register.html", {"form": form})


# ==========================================================================
# 材料识别（旧 QIdentifyWin）
# ==========================================================================
def _identify_context(request, form, log=None):
    material = Material.for_class(log.material_name) if log else None
    return {
        "form": form,
        "log": log,
        "material": material,
        "confidence_rows": log.confidence_rows if log else [],
        "feedback_form": FeedbackForm(predicted=log.material_name) if log else None,
    }


@login_required
def identify(request):
    """识别页：GET 显示表单，POST 识别成功后跳到结果页（PRG，刷新不会重复识别）。"""
    if request.method == "POST":
        form = IdentifyForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                raw, _original_name = _resolve_source(form.cleaned_data)
                img_path, image_bytes = _normalize_and_store(raw)
                # 注意：这里用的是已经落盘、被规范化过的字节，而不是用户原始文件
                result = predict_image(io.BytesIO(image_bytes))
            except ModelNotAvailable as exc:
                logger.error("模型不可用: %s", exc)
                messages.error(request, f"模型不可用：{exc}")
            except ValueError as exc:
                messages.error(request, str(exc))
            except Exception as exc:  # noqa: BLE001
                logger.exception("识别失败")
                messages.error(request, f"识别失败：{exc}")
            else:
                log = IdentifyLog.objects.create(
                    username=request.user.username,
                    action=IdentifyLog.ACTION_IDENTIFY,
                    material_name=result["pred_class"],
                    # 旧程序这里也写预测类别，但列名叫“实际材料”，语义不对；
                    # 识别记录不再臆造真实标签，留给“反馈错误”填写。
                    material_truename="",
                    img_path=img_path,
                    confidence=result["confidence"],
                    confidences=result["all_confidences"],
                )
                messages.success(request, "识别成功！")
                # 旧程序在这里弹 QMessageBox；Web 版改用消息条 + 结果页
                return redirect("recognition:identify_result", pk=log.pk)
    else:
        form = IdentifyForm()

    return render(request, "recognition/identify.html", _identify_context(request, form))


@login_required
@require_GET
def identify_result(request, pk):
    """识别结果（旧 QIdentifyWin 右侧的 结果/置信度/材料信息 三个控件）。

    只接受 GET：识别页的表单 action 指向 /identify/，如果哪天又被改成直接
    POST 到本页，会立刻返回 405 而不是悄悄渲染旧结果。
    """
    log = get_object_or_404(IdentifyLog, pk=pk)
    return render(
        request,
        "recognition/identify.html",
        _identify_context(request, IdentifyForm(), log=log),
    )


@login_required
def feedback(request, pk):
    """反馈错误（旧 QFromWin.feedback）。

    原实现是 ``DELETE 最新一行 + INSERT 一行``：并发时会把别人的记录删掉。
    这里保留识别记录，另存一条 action='feedback' 的记录并用外键指回去。
    """
    log = get_object_or_404(IdentifyLog, pk=pk)

    if request.method == "POST":
        form = FeedbackForm(request.POST, predicted=log.material_name)
        if form.is_valid():
            true_name = form.cleaned_data["true_name"]
            IdentifyLog.objects.create(
                username=request.user.username,
                action=IdentifyLog.ACTION_FEEDBACK,
                material_name=log.material_name,
                material_truename=true_name,
                img_path=log.img_path,
                confidence=log.confidence,
                confidences=log.confidences,
                corrects=log,
            )
            messages.success(
                request,
                f"反馈成功！已记录：预测 {log.material_name} -> 实际 {true_name}",
            )
            return redirect("recognition:identify_result", pk=log.pk)
    else:
        form = FeedbackForm(predicted=log.material_name)

    return render(
        request,
        "recognition/feedback.html",
        {"form": form, "log": log, "material": Material.for_class(log.material_name)},
    )


# ==========================================================================
# 图表（旧 QGraphWin + predict.visualize_prediction -> graph.png）
# ==========================================================================
@login_required
@require_GET
def graph_view(request, pk):
    """图表展示窗口。"""
    log = get_object_or_404(IdentifyLog, pk=pk)
    return render(
        request,
        "recognition/graph.html",
        {"log": log, "confidence_rows": log.confidence_rows},
    )


@login_required
@require_GET
def graph_png(request, pk):
    """服务端实时渲染 PNG。

    原程序把图固定写成 ``graph.png``（多用户会互相覆盖），这里按记录 id 出图。
    """
    log = get_object_or_404(IdentifyLog, pk=pk)

    if not log.img_path:
        raise Http404("该记录没有关联图片。")
    try:
        if not default_storage.exists(log.img_path):
            raise Http404("原图已被删除，无法生成图表。")
        with default_storage.open(log.img_path, "rb") as handle:
            source = handle.read()
    except Http404:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取图片失败 %s: %s", log.img_path, exc)
        raise Http404("读取原图失败。")

    prediction = {
        "pred_class": log.material_name,
        "confidence": log.confidence or "—",
        "all_confidences": log.confidences or {},
    }

    try:
        png = render_prediction_chart(source, prediction)
    except ChartUnavailable as exc:
        raise Http404(str(exc))

    response = HttpResponse(png, content_type="image/png")
    response["Cache-Control"] = "private, max-age=3600"
    if request.GET.get("download"):
        response["Content-Disposition"] = f'attachment; filename="graph-{log.pk}.png"'
    return response


# ==========================================================================
# 日志（旧 QLogWin / QUserLogWin，仅管理员）
# ==========================================================================
@admin_required
def log_list(request):
    """材料识别日志。"""
    form = LogFilterForm(request.GET or None)
    queryset = IdentifyLog.objects.all()
    applied = _filters_applied(request)

    if applied and form.is_valid():
        cd = form.cleaned_data
        if cd.get("username"):
            queryset = queryset.filter(username__icontains=cd["username"])
        if cd.get("action"):
            queryset = queryset.filter(action=cd["action"])
        if cd.get("material"):
            queryset = queryset.filter(
                Q(material_name__icontains=cd["material"])
                | Q(material_truename__icontains=cd["material"])
            )
        if cd.get("start_date"):
            queryset = queryset.filter(datetime__date__gte=cd["start_date"])
        if cd.get("end_date"):
            queryset = queryset.filter(datetime__date__lte=cd["end_date"])

    page_obj = _paginate(request, queryset)
    return render(
        request,
        "recognition/log_list.html",
        {"form": form, "page_obj": page_obj, "applied": applied, "total": queryset.count()},
    )


@admin_required
def login_log_list(request):
    """用户（登录）日志。"""
    form = LoginLogFilterForm(request.GET or None)
    queryset = LoginLog.objects.all()
    applied = _filters_applied(request)

    if applied and form.is_valid():
        cd = form.cleaned_data
        if cd.get("username"):
            queryset = queryset.filter(username__icontains=cd["username"])
        if cd.get("action"):
            queryset = queryset.filter(action=cd["action"])
        if cd.get("start_date"):
            queryset = queryset.filter(datetime__date__gte=cd["start_date"])
        if cd.get("end_date"):
            queryset = queryset.filter(datetime__date__lte=cd["end_date"])

    page_obj = _paginate(request, queryset)
    return render(
        request,
        "recognition/login_log_list.html",
        {"form": form, "page_obj": page_obj, "applied": applied, "total": queryset.count()},
    )


# ==========================================================================
# 用户管理（旧 QUserWin / QAddUserWin / QDeleteUserWin，仅管理员）
# ==========================================================================
@admin_required
def user_list(request):
    """用户列表（旧 QUserWin.show_table / search_info）。"""
    from django.contrib.auth.models import User

    form = UserFilterForm(request.GET or None)
    queryset = User.objects.select_related("profile").order_by("id")
    applied = _filters_applied(request)

    if applied and form.is_valid():
        cd = form.cleaned_data
        if cd.get("user_id"):
            # 旧代码对 int 列做 LIKE，这里按精确 ID 处理，非数字直接查不到
            queryset = (
                queryset.filter(pk=int(cd["user_id"])) if cd["user_id"].isdigit() else queryset.none()
            )
        if cd.get("username"):
            queryset = queryset.filter(username__icontains=cd["username"])
        if cd.get("permission"):
            queryset = queryset.filter(profile__permission=cd["permission"])

    page_obj = _paginate(request, queryset, per_page=15)
    return render(
        request,
        "recognition/user_list.html",
        {
            "form": form,
            "page_obj": page_obj,
            "applied": applied,
            "total": queryset.count(),
            "delete_form": UserDeleteForm(),
        },
    )


@admin_required
def user_create(request):
    """添加用户（旧 QAddUserWin.add_user）。"""
    if request.method == "POST":
        form = AdminUserCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            LoginLog.objects.create(
                username=request.user.username, action=LoginLog.ACTION_ADDUSER
            )
            messages.success(
                request,
                f"用户 {user.username} 添加成功（权限：{user.profile.get_permission_display()}）！",
            )
            return redirect("recognition:user_list")
    else:
        form = AdminUserCreateForm()

    return render(
        request, "recognition/user_form.html", {"form": form, "title": "添加用户"}
    )


@admin_required
def user_delete(request):
    """删除用户（旧 QDeleteUserWin.delete_user）。"""
    if request.method == "POST":
        form = UserDeleteForm(request.POST)
        if form.is_valid():
            target = form.cleaned_data["target"]

            if target.pk == request.user.pk:
                messages.error(request, "不能删除当前登录的账号。")
            elif is_admin_user(target) and not _other_admin_exists(target):
                # 原程序没有这层保护，删掉最后一个管理员就没人能进管理页了
                messages.error(request, "至少需要保留一个管理员账号，无法删除。")
            else:
                username = target.username
                target.delete()
                messages.success(request, f"用户 {username} 删除成功！")
                return redirect("recognition:user_list")
    else:
        initial = {}
        username = request.GET.get("username")
        if username:
            initial = {"field": UserDeleteForm.FIELD_USERNAME, "value": username}
        form = UserDeleteForm(initial=initial)

    return render(
        request, "recognition/user_delete.html", {"form": form, "title": "删除用户"}
    )


def _other_admin_exists(target) -> bool:
    from django.contrib.auth.models import User

    return (
        User.objects.exclude(pk=target.pk)
        .filter(Q(is_superuser=True) | Q(profile__permission=Profile.ADMIN))
        .exists()
    )


# ==========================================================================
# 材料管理（旧 QMaterialWin / QUpdateWin）
# ==========================================================================
@login_required
def material_list(request):
    """材料列表。

    旧 QMaterialWin.show_table 需要 material_name LEFT JOIN material_location
    LEFT JOIN material_count 三表联查，现在就是一次 ``Material.objects.all()``。
    """
    queryset = Material.objects.all()
    page_obj = _paginate(request, queryset, per_page=15)
    return render(
        request,
        "recognition/material_list.html",
        {"page_obj": page_obj, "total": queryset.count()},
    )


@login_required
def material_update(request):
    """修改材料的产地/库存（旧 QUpdateWin.update_info）。"""
    initial = {}
    material_id = request.GET.get("material")
    if material_id and material_id.isdigit():
        material = Material.objects.filter(pk=int(material_id)).first()
        if material:
            initial["material"] = material

    if request.method == "POST":
        form = MaterialUpdateForm(request.POST)
        if form.is_valid():
            material = form.save()
            messages.success(request, f"材料 {material.name} 的数据更新成功！")
            return redirect("recognition:material_list")
    else:
        form = MaterialUpdateForm(initial=initial)

    return render(request, "recognition/material_update.html", {"form": form})
