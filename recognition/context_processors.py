"""模板上下文：把导航/分页/模型状态等公共数据注入所有模板。"""

from __future__ import annotations

import os

import django
from django.conf import settings

from .models import is_admin_user


def site_context(request):
    """所有模板可用：``is_admin``、``querystring``、``model_status`` 等。"""
    # 分页链接需要保留筛选条件，这里预先去掉 page 参数
    params = request.GET.copy()
    params.pop("page", None)
    encoded = params.urlencode()
    querystring = f"{encoded}&" if encoded else ""

    model_path = str(settings.MATERIAL_MODEL_PATH)
    model_status = {
        "path": model_path,
        "filename": os.path.basename(model_path),
        "exists": os.path.exists(model_path),
        "device": settings.MATERIAL_MODEL_DEVICE,
        "class_count": len(settings.MATERIAL_CLASS_NAMES),
    }

    return {
        "site_name": "材料识别系统",
        "django_version": django.get_version(),
        "is_admin": is_admin_user(getattr(request, "user", None)),
        "querystring": querystring,
        "model_status": model_status,
        "class_labels": settings.MATERIAL_CLASS_LABELS,
    }
