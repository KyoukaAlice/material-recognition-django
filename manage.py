#!/usr/bin/env python
"""Django 命令行入口（材料识别系统）。"""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "无法导入 Django，请确认已安装依赖并激活虚拟环境：\n"
            r"  .\.venv\Scripts\activate" "\n"
            "  python -m pip install -r requirements.txt"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
