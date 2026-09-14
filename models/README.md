# 模型权重目录

请把训练好的 `best_model.pth`（ResNet50，9 类建筑材料，约 90 MB）放到本目录下：

```
material_recognition_django/
└── models/
    └── best_model.pth
```

程序会按以下顺序自动查找权重，找到即用：

1. 环境变量 `MATERIAL_MODEL_PATH` 指定的路径
2. `models/best_model.pth`（本目录）
3. 同级目录 `pytorch_model/best_model.pth`（原 PyQt5 工程目录）

## 获取权重

- 仓库 Release 页面下载 `best_model.pth` 后放入本目录
- 或使用自己训练得到的权重（`manage.py` 同级执行原 `train.py` 亦可）

放置完成后可执行自检确认：

```bash
python manage.py check_model
```
