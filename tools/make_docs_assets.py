"""生成 GitHub Pages 展示页所需的图片素材（放到 docs/images/）。

素材来源全部是真实的：
  * 训练曲线 / 混淆矩阵 / 类别分布 —— 原项目 train.py 的实际输出
  * 识别效果图 —— 用 best_model.pth 现场推理真实样本生成
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import django

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.conf import settings  # noqa: E402

from recognition.ml import predict_image, render_prediction_chart  # noqa: E402

DOCS_IMG = BASE_DIR / "docs" / "images"
DOCS_IMG.mkdir(parents=True, exist_ok=True)
LEGACY = Path(settings.LEGACY_DIR)

# 1) 直接把训练过程图表复制过来
training_assets = {
    "loss_curve.png": "训练损失曲线",
    "accuracy_curve.png": "训练准确率曲线",
    "confusion_matrix.png": "混淆矩阵",
    "class_distribution.png": "数据集类别分布",
}
for name, desc in training_assets.items():
    src = LEGACY / name
    if src.is_file():
        shutil.copy(src, DOCS_IMG / name)
        print(f"[OK]   {name:<26} {desc}  ({src.stat().st_size / 1024:.0f} KB)")
    else:
        print(f"[MISS] {name:<26} 源文件不存在: {src}")

# 2) 现场推理真实样本，生成识别效果图 + 原图
demo_samples = [
    ("copper_pipes", "13.jpg"),
    ("glass_wool", "14.jpg"),
]
for class_name, filename in demo_samples:
    src = LEGACY / "image" / class_name / filename
    if not src.is_file():
        print(f"[MISS] 样本不存在: {src}")
        continue

    result = predict_image(src)
    png = render_prediction_chart(src, result)
    out = DOCS_IMG / f"demo_{class_name}.png"
    out.write_bytes(png)
    print(f"[OK]   {out.name:<26} 预测={result['pred_class']} "
          f"置信度={result['confidence']} ({len(png) / 1024:.0f} KB)")

    # 原图单独存一份，展示页里做对比
    from PIL import Image

    with Image.open(src) as im:
        im.convert("RGB").resize((320, 320)).save(
            DOCS_IMG / f"sample_{class_name}.jpg", "JPEG", quality=88
        )
    print(f"[OK]   sample_{class_name}.jpg{'':<12} 原图缩略图")

print(f"\n素材目录: {DOCS_IMG}")
for f in sorted(DOCS_IMG.iterdir()):
    print(f"  {f.name:<28} {f.stat().st_size / 1024:>8.1f} KB")
