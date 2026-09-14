"""项目级路由。"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("recognition.urls")),
]

if settings.DEBUG:
    # 开发环境下由 Django 直接提供上传图片（生产交给 Nginx 等静态服务器）
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
