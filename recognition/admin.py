"""Django Admin 后台注册。

原桌面程序没有后台管理界面，全靠自建窗口 + 手写 SQL；这里顺带把数据模型挂到
``/admin/``，超级用户可以直接增删改查。
"""

from django.contrib import admin

from .models import IdentifyLog, LoginLog, Material, Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "permission")
    list_filter = ("permission",)
    search_fields = ("user__username",)


@admin.register(LoginLog)
class LoginLogAdmin(admin.ModelAdmin):
    list_display = ("username", "action", "datetime")
    list_filter = ("action",)
    search_fields = ("username",)
    date_hierarchy = "datetime"


@admin.register(IdentifyLog)
class IdentifyLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "username",
        "action",
        "material_name",
        "material_truename",
        "confidence",
        "datetime",
    )
    list_filter = ("action", "material_name")
    search_fields = ("username", "material_name", "material_truename")
    date_hierarchy = "datetime"
    readonly_fields = ("confidences",)


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ("material_id", "name", "location", "count")
    search_fields = ("name", "location")
