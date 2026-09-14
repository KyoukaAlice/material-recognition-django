"""批量识别文件夹里的图片。

对应原 ``predict.py`` 的批量分支（``batch_predict`` + ``save_results``）::

    python manage.py predict_folder /path/to/images --out predictions.csv
    python manage.py predict_folder /path/to/images --write-log --username admin

按 Ctrl+C 可随时中断。
"""

from __future__ import annotations

import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from recognition.ml import ModelNotAvailable, batch_predict, save_results
from recognition.models import IdentifyLog


class Command(BaseCommand):
    help = "批量识别一个文件夹中的图片，并可选写入识别日志"

    def add_arguments(self, parser):
        parser.add_argument("folder", help="图片文件夹路径")
        parser.add_argument("--out", default="predictions.csv", help="CSV 输出路径")
        parser.add_argument("--no-csv", action="store_true", help="不写 CSV，只打印结果")
        parser.add_argument("--write-log", action="store_true", help="把每条结果写入识别日志表")
        parser.add_argument("--username", default="cli", help="写入日志时记录的用户名")

    def handle(self, *args, **options):
        folder = Path(options["folder"])
        if not folder.is_dir():
            raise CommandError(f"文件夹不存在：{folder}")

        self.stdout.write(f"开始批量识别：{folder}")
        try:
            results = batch_predict(folder)
        except ModelNotAvailable as exc:
            raise CommandError(f"模型不可用：{exc}") from exc
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        if not results:
            raise CommandError("没有产生任何识别结果。")

        for res in results:
            self.stdout.write(
                f"  {res['filename']:<32} -> {res['pred_class']:<14} {res['confidence']}"
            )

        values = [float(r["confidence"].rstrip("%")) for r in results]
        self.stdout.write("")
        self.stdout.write(f"总图片数  : {len(results)}")
        self.stdout.write(f"最高置信度: {max(values):.2f}%")
        self.stdout.write(f"最低置信度: {min(values):.2f}%")
        average = sum(values) / len(values)
        self.stdout.write(f"平均置信度: {average:.2f}%")

        if not options["no_csv"]:
            output = save_results(results, options["out"])
            self.stdout.write(self.style.SUCCESS(f"详细结果已保存至: {output}"))

        if options["write_log"]:
            username = options["username"]
            created = 0
            for res in results:
                IdentifyLog.objects.create(
                    username=username,
                    action=IdentifyLog.ACTION_IDENTIFY,
                    material_name=res["pred_class"],
                    material_truename="",
                    img_path="",  # 命令行批量识别不复制图片，仅记录结果
                    confidence=res["confidence"],
                    confidences=res["all_confidences"],
                )
                created += 1
            self.stdout.write(self.style.SUCCESS(f"已写入识别日志 {created} 条（用户 {username}）"))
