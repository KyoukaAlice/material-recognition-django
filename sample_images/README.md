# 示例图片目录

识别页的「使用示例图片」下拉框会读取本目录，结构为「类别名/图片文件」：

```
sample_images/
├── asphalt/
│   ├── 1.jpg
│   └── 2.jpg
├── brick/
│   └── 1.jpg
└── ...
```

类别名需与 `config/settings.py` 中的 `MATERIAL_CLASS_NAMES` 一致。
目录为空时，识别页会自动隐藏示例图片选项，不影响上传与摄像头识别。

也可以通过环境变量 `MATERIAL_SAMPLE_DIR` 指向其他目录。
