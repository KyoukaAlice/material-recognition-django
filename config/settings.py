"""
材料识别系统 —— Django 配置

本工程由原 PyQt5 桌面程序（pytorch_model/）移植而来：
  * main.py + 12 个 *_ui.py  ->  recognition 应用（models/forms/views/templates）
  * predict.py               ->  recognition/ml.py
  * materials_management.sql ->  recognition/models.py + import_legacy_data 命令
"""

import os
from pathlib import Path

# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# 原项目目录（存放 best_model.pth / dataset/ / image/）。
# 默认取本工程的同级目录，可用环境变量 MATERIAL_LEGACY_DIR 覆盖。
LEGACY_DIR = Path(os.environ.get("MATERIAL_LEGACY_DIR", BASE_DIR.parent / "pytorch_model"))


def _first_existing(*candidates):
    """按顺序返回第一个存在的路径，都不存在则返回第一个（便于报错时提示）。"""
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)
    return Path(candidates[0])


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}


def _first_dir_with_images(*candidates):
    """返回第一个“确实装了图片”的示例图片目录。

    不能只看目录是否存在：仓库里自带的 ``sample_images/`` 默认是空目录，
    若它被优先选中，识别页的示例图片下拉框就会是空的。因此这里要求目录下
    至少有一个类别子目录且其中含有图片文件。
    """
    fallback = None
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if not path.is_dir():
            continue
        if fallback is None:
            fallback = path
        try:
            for sub in path.iterdir():
                if not sub.is_dir():
                    continue
                if any(
                    f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES
                    for f in sub.iterdir()
                ):
                    return path
        except OSError:
            continue
    if fallback is not None:
        return fallback
    return Path(candidates[0])


# 工程内自带的资源目录：从 GitHub clone 下来后，没有原 PyQt5 目录也能跑。
LOCAL_MODEL_DIR = BASE_DIR / "models"
LOCAL_SAMPLE_DIR = BASE_DIR / "sample_images"

# matplotlib 默认把字体/缓存写到用户主目录（如 C:\Users\xxx\.matplotlib）。
# 在只读或受限的环境里这一步会失败并回退到临时目录（拖慢导入速度），
# 因此显式指向工程内的可写目录。必须在 import matplotlib 之前设置。
os.environ.setdefault("MPLCONFIGDIR", str(BASE_DIR / ".cache" / "matplotlib"))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default):
    raw = os.environ.get(name)
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# --------------------------------------------------------------------------
# 安全
# --------------------------------------------------------------------------
# 生产环境务必通过环境变量注入；此处的默认值仅供本地开发。
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-only-insecure-key-change-me-before-deploying-0123456789abcdef",
)

DEBUG = _env_bool("DJANGO_DEBUG", True)

# 原程序是单机桌面应用，没有 Host 概念；Web 版必须显式声明。
# 用手机连同一局域网测试摄像头时，把本机内网 IP 加进 DJANGO_ALLOWED_HOSTS。
ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS", ["127.0.0.1", "localhost", "[::1]"])

# CSRF 信任来源（http:// 的局域网访问需要显式列出，否则表单提交会被拒绝）
CSRF_TRUSTED_ORIGINS = _env_list("DJANGO_CSRF_TRUSTED_ORIGINS", [])

# 认证跳转
LOGIN_URL = "recognition:login"
LOGIN_REDIRECT_URL = "recognition:identify"
LOGOUT_REDIRECT_URL = "recognition:login"

# --------------------------------------------------------------------------
# 应用
# --------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "recognition.apps.RecognitionConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "recognition.context_processors.site_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --------------------------------------------------------------------------
# 数据库
# --------------------------------------------------------------------------
# 原项目用 MySQL(materials_management)。Web 版默认改用 SQLite：零配置、可直接跑，
# 表结构由 Django ORM 管理（原 4 张物料表已合并为 1 张 recognition_material）。
# 若要回到 MySQL，取消下面的注释并装好 mysqlclient 即可。
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# DATABASES = {
#     "default": {
#         "ENGINE": "django.db.backends.mysql",
#         "NAME": os.environ.get("MYSQL_DATABASE", "materials_management"),
#         "USER": os.environ.get("MYSQL_USER", "root"),
#         "PASSWORD": os.environ.get("MYSQL_PASSWORD", ""),
#         "HOST": os.environ.get("MYSQL_HOST", "localhost"),
#         "PORT": os.environ.get("MYSQL_PORT", "3306"),
#         "OPTIONS": {"charset": "utf8mb4"},
#     }
# }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# 用户与密码
# --------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --------------------------------------------------------------------------
# 国际化
# --------------------------------------------------------------------------
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
# 原库中 datetime 存的是本地时间字符串（如 2025-04-11 19:13:56），
# 关闭 UTC 存储以保持日志时间与原数据一致。
USE_TZ = False
USE_I18N = True

# --------------------------------------------------------------------------
# 静态文件与媒体文件
# --------------------------------------------------------------------------
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# 上传限制：单张图片/表单最大 10 MB（浏览器抓拍帧走的是同一通道）
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

# --------------------------------------------------------------------------
# 深度学习模型（对应原 predict.py 顶部“用户配置区域”）
# --------------------------------------------------------------------------
# 模型权重查找顺序：
#   1) 环境变量 MATERIAL_MODEL_PATH
#   2) 本工程 models/best_model.pth      <- clone 仓库后把权重放这里
#   3) 同级目录 pytorch_model/best_model.pth
MATERIAL_MODEL_PATH = _first_existing(
    os.environ.get("MATERIAL_MODEL_PATH"),
    LOCAL_MODEL_DIR / "best_model.pth",
    LEGACY_DIR / "best_model.pth",
)

# 类别顺序必须与 best_model.pth 训练时一致（原 predict.py: CLASS_NAMES）
MATERIAL_CLASS_NAMES = [
    "asphalt",
    "brick",
    "cement",
    "copper_pipes",
    "glass_wool",
    "pvc",
    "rebar",
    "stone",
    "wood",
]

# 类别中文名（取自 materials_management.sql 中 material_info 的首行）
MATERIAL_CLASS_LABELS = {
    "asphalt": "沥青",
    "brick": "砖",
    "cement": "水泥",
    "copper_pipes": "铜管",
    "glass_wool": "玻璃棉",
    "pvc": "PVC",
    "rebar": "钢筋",
    "stone": "石材",
    "wood": "木材",
}

# 推理设备：auto / cpu / cuda
MATERIAL_MODEL_DEVICE = os.environ.get("MATERIAL_MODEL_DEVICE", "auto")

# 示例图片目录（识别页的“示例图片”快捷入口）
# 会跳过存在但没有图片的目录，因此空的 sample_images/ 不会遮蔽原数据集目录。
MATERIAL_SAMPLE_DIR = _first_dir_with_images(
    os.environ.get("MATERIAL_SAMPLE_DIR"),
    LOCAL_SAMPLE_DIR,
    LEGACY_DIR / "image",
)
MATERIAL_SAMPLE_PER_CLASS = int(os.environ.get("MATERIAL_SAMPLE_PER_CLASS", "3"))

# 列表页每页条数（原程序一次性把整表塞进 QTableWidget）
PAGE_SIZE = 20

# --------------------------------------------------------------------------
# 日志
# --------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "[{levelname}] {asctime} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "recognition": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
