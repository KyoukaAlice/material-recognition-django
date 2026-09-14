"""自检命令：确认模型、依赖与数据库都就绪。

    python manage.py check_model
    python manage.py check_model --image D:\\path\\to\\test.jpg
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from recognition.ml import (
    ChartUnavailable,
    MaterialClassifier,
    ModelNotAvailable,
    predict_image,
    render_prediction_chart,
)


class Command(BaseCommand):
    help = "检查模型与推理链路是否正常"

    def add_arguments(self, parser):
        parser.add_argument("--image", help="可选：对该图片跑一次真实推理")

    def handle(self, *args, **options):
        ok = True

        # 1) 依赖
        import django
        import torch
        import torchvision

        self.stdout.write("依赖版本：")
        self.stdout.write(f"  Django     : {django.get_version()}")
        self.stdout.write(f"  torch      : {torch.__version__}")
        self.stdout.write(f"  torchvision: {torchvision.__version__}")
        self.stdout.write(f"  CUDA 可用  : {torch.cuda.is_available()}")

        # 2) 模型文件
        model_path = Path(settings.MATERIAL_MODEL_PATH)
        self.stdout.write("")
        self.stdout.write(f"模型路径：{model_path}")
        if not model_path.is_file():
            self.stderr.write(self.style.ERROR(f"  模型文件不存在（大小写/路径请核对）"))
            raise CommandError("模型文件缺失，可设置环境变量 MATERIAL_MODEL_PATH 指定")
        self.stdout.write(
            f"  文件大小：{model_path.stat().st_size / 1024 / 1024:.1f} MB"
        )
        self.stdout.write(f"  类别数  ：{len(settings.MATERIAL_CLASS_NAMES)}")
        self.stdout.write(f"  类别    ：{', '.join(settings.MATERIAL_CLASS_NAMES)}")

        # 3) 加载模型
        self.stdout.write("")
        try:
            classifier = MaterialClassifier.instance()
            classifier.load()
        except ModelNotAvailable as exc:
            raise CommandError(f"模型加载失败：{exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"模型加载成功，推理设备：{classifier.device}"))

        # 4) 数据库
        from recognition.models import IdentifyLog, LoginLog, Material
        from django.contrib.auth.models import User

        self.stdout.write("")
        self.stdout.write("数据库统计：")
        self.stdout.write(f"  用户 {User.objects.count()} / 材料 {Material.objects.count()} "
                          f"/ 识别日志 {IdentifyLog.objects.count()} / 登录日志 {LoginLog.objects.count()}")
        if Material.objects.count() == 0:
            self.stdout.write(
                self.style.WARNING("  材料表为空，建议执行：python manage.py import_legacy_data")
            )

        # 5) 可选：真实推理
        if options["image"]:
            image = Path(options["image"])
            if not image.is_file():
                raise CommandError(f"图片不存在：{image}")
            self.stdout.write("")
            self.stdout.write(f"正在识别：{image}")
            result = predict_image(image)
            self.stdout.write(f"  预测类别：{result['pred_class']}")
            self.stdout.write(f"  置信度  ：{result['confidence']}")
            self.stdout.write("  全部类别：")
            for name, value in result["all_confidences"].items():
                self.stdout.write(f"    {name:<14} {value}")

            try:
                png = render_prediction_chart(image, result)
                self.stdout.write(self.style.SUCCESS(f"  图表渲染成功：{len(png) / 1024:.1f} KB PNG"))
            except ChartUnavailable as exc:
                ok = False
                self.stderr.write(self.style.ERROR(f"  图表渲染失败：{exc}"))

        self.stdout.write("")
        if ok:
            self.stdout.write(self.style.SUCCESS("自检通过。"))
        else:
            raise CommandError("自检未通过，请查看上面的错误信息。")
