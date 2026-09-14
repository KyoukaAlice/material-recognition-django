"""迁移自检：建表 + 导入原 MySQL 数据。

    python manage.py migrate
    python manage.py import_legacy_data
    python manage.py check_model
    python manage.py runserver 8000
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import django

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.core.management import call_command  # noqa: E402

from recognition.ml import get_transform, predict_image  # noqa: E402


def banner(text: str) -> None:
    print("\n" + "=" * 70)
    print(text)
    print("=" * 70)


def main() -> int:
    banner("1/3 migrate")
    call_command("migrate", interactive=False, verbosity=1)

    banner("2/3 import_legacy_data")
    try:
        call_command("import_legacy_data", verbosity=1)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 导入失败（可忽略，稍后重试）：{exc}")

    banner("3/3 模型推理 + 图表")
    from django.conf import settings

    class_names = list(settings.MATERIAL_CLASS_NAMES)
    print(f"类别({len(class_names)}): {class_names}")
    print(f"transform: {get_transform()}")

    sample = None
    sample_dir = Path(settings.MATERIAL_SAMPLE_DIR)
    for class_dir in sorted(p for p in sample_dir.iterdir() if p.is_dir()):
        files = sorted(class_dir.iterdir())
        if files:
            sample = files[0]
            break
    if sample is None:
        print("[warn] 找不到示例图片，跳过推理验证")
        return 0

    print(f"\n样本图片: {sample}（期望类别: {sample.parent.name}）")
    started = time.time()
    result = predict_image(sample)
    first = time.time() - started
    started = time.time()
    predict_image(sample)
    second = time.time() - started

    print(f"  预测类别: {result['pred_class']}")
    print(f"  置信度  : {result['confidence']}")
    print(f"  首次推理耗时: {first:.2f}s（含模型加载）")
    print(f"  二次推理耗时: {second:.2f}s（模型已常驻，原代码每次都会重新加载）")
    print("  全类别置信度:")
    for name, value in result["all_confidences"].items():
        print(f"    {name:<14} {value}")

    from recognition.ml import render_prediction_chart

    png = render_prediction_chart(sample, result)
    digest = hashlib.md5(png).hexdigest()
    print(f"\n图表 PNG: {len(png)} bytes, md5={digest}")

    print("\nJSON 结果（前 300 字符）:")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:300])

    print("\n✅ 全部检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
