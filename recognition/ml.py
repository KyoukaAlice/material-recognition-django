"""材料识别推理模块

由原 ``pytorch_model/predict.py`` 移植。函数名尽量与原文保持一致，便于对照：

原函数                      现在的位置
--------------------------  --------------------------------------------
`get_transform()`           ``get_transform()``
`load_model()`              ``MaterialClassifier.load()`` / ``load_model()``
`predict_single_image()`    ``predict_image()``（单张图片）
`batch_predict()`           ``batch_predict()``
`save_results()`            ``save_results()``
`visualize_prediction()`    ``render_prediction_chart()``（改为输出 PNG 字节）

相对原实现的主要改动：

1. **模型只加载一次**。原 ``main.py`` 每次点击“识别材料”都调用
   ``load_model()``，每次都重新读盘 90 MB 权重并重建 ResNet50；
   这里改为进程内线程安全单例，首次调用后常驻内存。
2. ``torch.no_grad()`` -> ``torch.inference_mode()``，并把输入图片尺寸
   归一化后再推理。
3. ``visualize_prediction()`` 原来返回 matplotlib ``Figure`` 对象、由调用方
   ``savefig('graph.png')`` 落盘成固定文件名（并发用户会互相覆盖）。这里
   直接返回 PNG 字节流，交给 Django 视图输出，不再写死文件名。
4. 配置项（模型路径 / 类别 / 设备）统一从 Django settings 读取，不再硬编码。
"""

from __future__ import annotations

import io
import logging
import os
import threading
from collections import OrderedDict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

import torch
import torch.nn as nn
from django.conf import settings
from PIL import Image, ImageOps
from torchvision import models, transforms

logger = logging.getLogger(__name__)

# matplotlib 只在需要画图时使用；缺失时不影响识别功能本身。
try:  # pragma: no cover - 取决于运行环境是否装了 matplotlib
    import matplotlib

    matplotlib.use("Agg")  # 服务器端无 GUI，必须用非交互后端
    from matplotlib import pyplot as plt
except Exception:  # noqa: BLE001
    plt = None

ImageSource = Union[str, bytes, bytearray, io.IOBase]

# 图片后缀白名单（原 predict.py: valid_extensions）
VALID_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")


class ModelNotAvailable(RuntimeError):
    """模型文件缺失或无法加载。"""


class ChartUnavailable(RuntimeError):
    """matplotlib 不可用，无法生成图表。"""


# ==========================================================================
# 图像预处理（原 predict.py: get_transform）
# ==========================================================================
def get_transform() -> transforms.Compose:
    """验证/推理用的图像预处理流水线。"""
    return transforms.Compose(
        [
            transforms.Resize(256),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ]
    )


def resolve_device(preference: Optional[str] = None) -> torch.device:
    """解析推理设备。``auto`` 优先 CUDA，否则 CPU（原 predict.py 同逻辑）。"""
    preference = (preference or settings.MATERIAL_MODEL_DEVICE or "auto").lower()
    if preference == "cpu":
        return torch.device("cpu")
    if preference == "cuda":
        if not torch.cuda.is_available():
            logger.warning("MATERIAL_MODEL_DEVICE=cuda 但当前环境无可用 CUDA，回退到 CPU")
            return torch.device("cpu")
        return torch.device("cuda")
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def open_image(source: ImageSource) -> Image.Image:
    """把多种来源统一打开成 RGB 图像。

    支持：文件路径、原始字节、文件对象（UploadedFile / BytesIO / 已打开的句柄）。
    """
    if isinstance(source, (bytes, bytearray)):
        handle = io.BytesIO(bytes(source))
    elif hasattr(source, "read"):  # UploadedFile、BytesIO、open(path, 'rb')
        handle = source
        if hasattr(handle, "seek"):
            handle.seek(0)
    else:
        path = str(source)
        if not os.path.exists(path):
            raise ValueError(f"无法打开图片文件: {path}")
        handle = path

    try:
        image = Image.open(handle)
        # 手机拍摄的照片常带 EXIF 方向信息，先按 EXIF 摆正再喂给模型
        image = ImageOps.exif_transpose(image)
        return image.convert("RGB")
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"无法解析图片内容（不是有效的图片文件）: {exc}") from exc


# ==========================================================================
# 模型
# ==========================================================================
class MaterialClassifier:
    """ResNet50 材料分类器：进程内单例，线程安全。"""

    _instance: Optional["MaterialClassifier"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        model_path: Union[str, os.PathLike],
        class_names: Sequence[str],
        device: Optional[torch.device] = None,
    ) -> None:
        self.model_path = str(model_path)
        self.class_names = list(class_names)
        self.device = device or resolve_device()
        self.transform = get_transform()
        # 同一个模型实例被多个请求线程共享，推理阶段加锁更稳妥
        # （torch 模块本身是线程安全的，这里主要保护“首次加载”与 matplotlib 之外的场景）
        self._predict_lock = threading.Lock()
        self._loaded = False
        self.model: Optional[nn.Module] = None

    # ---------------- 单例 ----------------
    @classmethod
    def instance(cls) -> "MaterialClassifier":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls.from_settings()
        return cls._instance

    @classmethod
    def from_settings(cls) -> "MaterialClassifier":
        return cls(
            model_path=settings.MATERIAL_MODEL_PATH,
            class_names=settings.MATERIAL_CLASS_NAMES,
            device=resolve_device(),
        )

    @classmethod
    def reset(cls) -> None:
        """清空单例缓存（测试用）。"""
        with cls._instance_lock:
            cls._instance = None

    # ---------------- 加载 ----------------
    def _build_model(self) -> nn.Module:
        try:
            # torchvision >= 0.13
            model = models.resnet50(weights=None)
        except TypeError:  # pragma: no cover - 兼容旧版 torchvision
            model = models.resnet50(pretrained=False)
        model.fc = nn.Linear(model.fc.in_features, len(self.class_names))
        return model

    def load(self) -> nn.Module:
        """加载权重（幂等，只真正执行一次）。"""
        if self._loaded and self.model is not None:
            return self.model

        with self._instance_lock:
            if self._loaded and self.model is not None:
                return self.model

            if not os.path.exists(self.model_path):
                raise ModelNotAvailable(
                    f"模型文件不存在：{self.model_path}。"
                    "请将 best_model.pth 放入项目的 models/ 目录，"
                    "或用环境变量 MATERIAL_MODEL_PATH 指定其完整路径。"
                )

            logger.info("正在加载模型 %s (device=%s)", self.model_path, self.device)
            model = self._build_model()

            try:
                state_dict = torch.load(self.model_path, map_location=self.device)
            except Exception as exc:  # noqa: BLE001
                raise ModelNotAvailable(f"模型文件损坏或版本不兼容: {exc}") from exc

            # 有些权重保存时带了 "module." / "model." 前缀（DataParallel 训练）
            if isinstance(state_dict, dict) and not any(
                k.startswith("fc.") for k in state_dict
            ):
                for prefix in ("module.", "model."):
                    if all(k.startswith(prefix) for k in state_dict):
                        state_dict = {k[len(prefix):]: v for k, v in state_dict.items()}
                        break

            try:
                model.load_state_dict(state_dict)
            except Exception as exc:  # noqa: BLE001
                raise ModelNotAvailable(
                    f"权重与模型结构不匹配（检查 MATERIAL_CLASS_NAMES 是否为 "
                    f"{len(self.class_names)} 类）: {exc}"
                ) from exc

            model = model.to(self.device)
            model.eval()

            if model.fc.out_features != len(self.class_names):
                raise ModelNotAvailable("模型输出类别数与 MATERIAL_CLASS_NAMES 不一致！")

            self.model = model
            self._loaded = True
            logger.info("模型加载完成：%d 个类别 %s", len(self.class_names), self.class_names)
            return model

    # ---------------- 推理 ----------------
    @torch.inference_mode()
    def predict(self, source: ImageSource) -> Dict[str, object]:
        """对单张图片推理，返回与原型一致的结果字典。

        返回字段：
          filename          文件名（若是路径则取 basename，否则为 ``<上传图片>``）
          pred_class        预测类别（英文，对应 MATERIAL_CLASS_NAMES）
          confidence        置信度字符串，如 ``"97.11%"``（与原格式一致）
          confidence_value  置信度浮点数，如 ``97.11``
          all_confidences   ``OrderedDict[类别 -> "97.11%"]``，按置信度降序
          confidence_values ``[(类别, 浮点数)]``，便于画图/排序
          top3              前三名 ``[(类别, 浮点数)]``
        """
        model = self.load()
        image = open_image(source)

        tensor = self.transform(image).unsqueeze(0).to(self.device)
        logits = model(tensor)
        probabilities = torch.nn.functional.softmax(logits[0], dim=0)

        probs = probabilities.detach().cpu().tolist()
        pairs: List[Tuple[str, float]] = sorted(
            ((name, float(p) * 100.0) for name, p in zip(self.class_names, probs)),
            key=lambda item: item[1],
            reverse=True,
        )
        best_class, best_value = pairs[0]

        if isinstance(source, (str, os.PathLike)):
            filename = os.path.basename(str(source))
        elif hasattr(source, "name") and getattr(source, "name"):
            filename = os.path.basename(str(source.name))
        else:
            filename = "<上传图片>"

        # 保持原代码 all_confidences 的插入顺序 = 类别顺序，取值格式化到 2 位小数
        formatted = OrderedDict(
            (name, f"{prob * 100.0:.2f}%") for name, prob in zip(self.class_names, probs)
        )

        return {
            "filename": filename,
            "pred_class": best_class,
            "confidence": f"{best_value:.2f}%",
            "confidence_value": round(best_value, 2),
            "all_confidences": OrderedDict(formatted),
            "confidence_values": pairs,
            "top3": pairs[:3],
        }

    @property
    def is_loaded(self) -> bool:
        return self._loaded


# ==========================================================================
# 兼容层：保留原 predict.py 的函数名
# ==========================================================================
def load_model() -> nn.Module:
    """加载（或复用）模型，返回 ``torch.nn.Module``。"""
    return MaterialClassifier.instance().load()


def predict_image(source: ImageSource) -> Dict[str, object]:
    """单张图片预测（原 ``predict_single_image``）。"""
    return MaterialClassifier.instance().predict(source)


def predict_single_image(model, img_path, transform=None) -> Dict[str, object]:
    """原签名的薄封装，仅为便于与旧代码对照。

    新的调用请直接用 :func:`predict_image`；``model`` / ``transform``
    参数会被忽略，统一复用进程内单例，避免每次请求重建网络。
    """
    return MaterialClassifier.instance().predict(img_path)


def batch_predict(folder_path: Union[str, os.PathLike]) -> List[Dict[str, object]]:
    """批量预测文件夹内的图片（原 ``batch_predict``）。"""
    classifier = MaterialClassifier.instance()
    folder = str(folder_path)
    if not os.path.isdir(folder):
        raise ValueError(f"目标文件夹不存在: {folder}")

    image_files = sorted(
        f for f in os.listdir(folder) if f.lower().endswith(VALID_EXTENSIONS)
    )
    if not image_files:
        raise ValueError("目标文件夹中没有有效的图片文件！")

    results: List[Dict[str, object]] = []
    for name in image_files:
        try:
            results.append(classifier.predict(os.path.join(folder, name)))
        except Exception as exc:  # noqa: BLE001
            logger.warning("处理文件 %s 时出错: %s", name, exc)
    return results


def save_results(results: Iterable[Dict[str, object]], output_file: str = "predictions.csv") -> str:
    """把批量预测结果写成 CSV（原 ``save_results``）。"""
    import csv

    results = list(results)
    if not results:
        raise ValueError("没有可保存的预测结果！")

    with open(output_file, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["文件名", "预测类别", "置信度", "全类别置信度"])
        for res in results:
            conf_str = ", ".join(f"{k}:{v}" for k, v in res["all_confidences"].items())
            writer.writerow([res["filename"], res["pred_class"], res["confidence"], conf_str])
    return os.path.abspath(output_file)


# ==========================================================================
# 可视化（原 visualize_prediction，改为输出 PNG 字节）
# ==========================================================================
# matplotlib 的 pyplot 不是线程安全的，多用户并发画图需要串行化
_CHART_LOCK = threading.Lock()


def render_prediction_chart(
    source: ImageSource,
    prediction: Dict[str, object],
    *,
    dpi: int = 130,
    figsize: Tuple[float, float] = (10.0, 5.0),
) -> bytes:
    """生成“输入图片 + 各类别置信度条形图”的 PNG（对应原 graph.png 的内容）。"""
    if plt is None:
        raise ChartUnavailable("未安装 matplotlib，无法生成图表。请执行 pip install matplotlib")

    image = open_image(source)
    # 反查图片真实解码顺序，避免 PIL 的懒加载对象在别的线程里出问题
    image.load()

    classes = list(prediction["all_confidences"].keys())
    values = [
        float(str(v).rstrip("%")) for v in prediction["all_confidences"].values()
    ]
    pred_class = str(prediction["pred_class"])
    bar_colors = ["#ff9f43" if c == pred_class else "#7fb3d5" for c in classes]

    with _CHART_LOCK:
        fig = plt.figure(figsize=figsize, dpi=dpi)

        ax1 = fig.add_subplot(1, 2, 1)
        ax1.imshow(image)
        ax1.axis("off")
        ax1.set_title("Input Image")

        ax2 = fig.add_subplot(1, 2, 2)
        ax2.barh(classes, values, color=bar_colors)
        ax2.invert_yaxis()  # 置信度最高的排在最上面
        ax2.set_xlim(0, 100)
        ax2.set_xlabel("Confidence (%)")
        ax2.set_title(f"Class Confidence Distribution\nTop-1: {pred_class} ({prediction['confidence']})")
        for y, value in enumerate(values):
            ax2.text(min(value + 1.0, 96.0), y, f"{value:.1f}%", va="center", fontsize=8)

        fig.tight_layout()
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", bbox_inches="tight")
        plt.close(fig)  # 原代码用 plt.close(fig) 关闭默认显示，这里同时释放内存

    return buffer.getvalue()
