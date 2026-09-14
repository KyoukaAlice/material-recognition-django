"""带命名空间的 URL，统一使用 ``{% url 'recognition:xxx' %}`` 反向解析。"""

from django.urls import path

from . import views

app_name = "recognition"

urlpatterns = [
    # 首页 = 识别页（未登录会被 login_required 弹到登录页）
    path("", views.identify, name="home"),
    # 认证
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("register/", views.register_view, name="register"),
    # 识别 / 结果 / 图表 / 反馈
    path("identify/", views.identify, name="identify"),
    path("identify/result/<int:pk>/", views.identify_result, name="identify_result"),
    path("identify/graph/<int:pk>/", views.graph_view, name="graph"),
    path("identify/graph/<int:pk>.png", views.graph_png, name="graph_png"),
    path("identify/feedback/<int:pk>/", views.feedback, name="feedback"),
    # 日志（管理员）
    path("logs/", views.log_list, name="log_list"),
    path("logs/logins/", views.login_log_list, name="login_log_list"),
    # 用户管理（管理员）
    path("users/", views.user_list, name="user_list"),
    path("users/add/", views.user_create, name="user_create"),
    path("users/delete/", views.user_delete, name="user_delete"),
    # 材料管理
    path("materials/", views.material_list, name="material_list"),
    path("materials/update/", views.material_update, name="material_update"),
]
