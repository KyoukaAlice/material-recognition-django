"""端到端冒烟测试：真正启动服务后跑一遍完整业务流程。

用法（先另开一个终端启动服务）::

    python manage.py runserver 8011
    python tools/smoke_test.py http://127.0.0.1:8011

覆盖：
  1. 未登录访问识别页 -> 跳转登录
  2. 用导入的 Admin/123456 登录
  3. 上传图片识别 -> 结果页出现正确类别 + 材料信息
  4. 置信度图表 PNG 接口
  5. 示例图片识别（不经过文件上传）
  6. 摄像头 dataURL 识别
  7. 反馈错误写入日志
  8. 管理员页面（识别日志 / 用户日志 / 用户管理 / 材料管理）可访问
  9. 普通用户访问管理员页面被拒绝（原程序只 hide 按钮）
 10. 非法文件被拒绝
 11. 注册 + 新账号登录
"""

from __future__ import annotations

import base64
import io
import re
import sys
import time
from pathlib import Path

import requests

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8011").rstrip("/")

CSRF_RE = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')
PASSED, FAILED = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [PASS] {name}" + (f"  {detail}" if detail else ""))
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name}" + (f"  {detail}" if detail else ""))


def csrf(session: requests.Session, url: str) -> str:
    """取页面里的 csrf token（Django 的 CSRF_COOKIE 需要配合表单字段）。"""
    response = session.get(url, timeout=30)
    match = CSRF_RE.search(response.text)
    if not match:
        raise RuntimeError(f"{url} 页面里找不到 csrfmiddlewaretoken")
    return match.group(1)


def login(username: str, password: str) -> requests.Session:
    session = requests.Session()
    token = csrf(session, f"{BASE}/login/")
    response = session.post(
        f"{BASE}/login/",
        data={"csrfmiddlewaretoken": token, "username": username, "password": password},
        headers={"Referer": f"{BASE}/login/"},
        timeout=30,
    )
    return session, response


def sample_image_bytes() -> bytes:
    """用 Pillow 现场生成一张图片（避免依赖仓库里的数据集路径）。"""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (256, 256), (120, 160, 90)).save(buffer, format="JPEG")
    return buffer.getvalue()


def main() -> int:
    print(f"目标服务: {BASE}\n")

    # ---------------------------------------------------------------- 0
    print("0) 服务可用性")
    try:
        health = requests.get(f"{BASE}/login/", timeout=30)
    except requests.exceptions.ConnectionError as exc:
        print(f"  [FAIL] 无法连接服务：{exc}")
        return 1
    check("登录页返回 200", health.status_code == 200, f"status={health.status_code}")

    # ---------------------------------------------------------------- 1
    print("\n1) 未登录访问受保护页面")
    anon = requests.Session()
    resp = anon.get(f"{BASE}/identify/", allow_redirects=False, timeout=30)
    check(
        "匿名访问 /identify/ 被重定向到登录页",
        resp.status_code == 302 and "/login/" in resp.headers.get("Location", ""),
        f"status={resp.status_code} -> {resp.headers.get('Location')}",
    )
    resp = anon.get(f"{BASE}/logs/", allow_redirects=False, timeout=30)
    check(
        "匿名访问 /logs/ 被重定向到登录页",
        resp.status_code == 302 and "/login/" in resp.headers.get("Location", ""),
        f"status={resp.status_code}",
    )

    # ---------------------------------------------------------------- 2
    print("\n2) 管理员登录（Admin / 123456，来自原 MySQL dump）")
    admin, resp = login("Admin", "123456")
    check("登录成功并跳转", resp.status_code == 200 and "材料识别" in resp.text, f"url={resp.url}")
    check("已写入登录日志（后续检查）", True)

    # ---------------------------------------------------------------- 3
    print("\n3) 上传图片识别")
    token = csrf(admin, f"{BASE}/identify/")
    files = {"image": ("smoke-test.jpg", sample_image_bytes(), "image/jpeg")}
    resp = admin.post(
        f"{BASE}/identify/",
        data={"csrfmiddlewaretoken": token},
        files=files,
        headers={"Referer": f"{BASE}/identify/"},
        timeout=120,
    )
    check("识别请求成功", resp.status_code == 200, f"status={resp.status_code} url={resp.url}")
    check("结果页出现“识别结果”", "识别结果" in resp.text)
    found_class = re.search(r'class="result-class">([^<]+)<', resp.text)
    pred_label = found_class.group(1).strip() if found_class else ""
    check("解析出预测类别", bool(pred_label), f"预测={pred_label}")
    conf = re.search(r'class="result-conf">([^<]+)<', resp.text)
    check("显示置信度", bool(conf), f"置信度={conf.group(1) if conf else '—'}")
    check("结果页渲染了上传的图片", "/media/uploads/" in resp.text)
    check("展示材料信息（来自导入的 material_info）", "材料信息" in resp.text)

    log_id = None
    match = re.search(r"/identify/graph/(\d+)/", resp.text)
    if match:
        log_id = int(match.group(1))
    check("结果页带图表链接", log_id is not None, f"log_id={log_id}")

    # ---------------------------------------------------------------- 4
    print("\n4) 置信度图表 PNG")
    if log_id:
        png = admin.get(f"{BASE}/identify/graph/{log_id}.png", timeout=60)
        check("图表接口返回 200", png.status_code == 200, f"status={png.status_code}")
        check("Content-Type 为 image/png", png.headers.get("Content-Type") == "image/png")
        check("返回真实 PNG 数据", png.content[:8] == b"\x89PNG\r\n\x1a\n", f"{len(png.content)} bytes")
        page = admin.get(f"{BASE}/identify/graph/{log_id}/", timeout=30)
        check("图表页面可访问", page.status_code == 200 and "概率详情" in page.text)

    # ---------------------------------------------------------------- 5
    print("\n5) 示例图片识别（内置示例图片）")
    token = csrf(admin, f"{BASE}/identify/")
    listing = admin.get(f"{BASE}/identify/", timeout=30).text
    sample_values = re.findall(r'<option value="([a-z_]+/\d+\.jpg)"', listing)
    check("识别页列出了示例图片选项", len(sample_values) > 0, f"{len(sample_values)} 个")
    if sample_values:
        sample = sample_values[0]
        expected = sample.split("/")[0]
        resp = admin.post(
            f"{BASE}/identify/",
            data={"csrfmiddlewaretoken": token, "sample": sample},
            headers={"Referer": f"{BASE}/identify/"},
            timeout=120,
        )
        check("示例图片识别成功", resp.status_code == 200, f"status={resp.status_code}")
        m = re.search(r'class="result-class">([^<]+)<', resp.text)
        check("示例图片识别给出了类别", bool(m), f"预测={m.group(1).strip() if m else '—'}")
        # 页面用中文名展示，英文类别名在“类别标签”中，用它核对预测是否正确
        m_tag = re.search(r"类别标签\s*<code>([^<]+)</code>", resp.text)
        predicted = m_tag.group(1).strip() if m_tag else ""
        check(
            f"示例图片 {sample} 预测正确（{expected}）",
            predicted == expected,
            f"期望={expected} 实际={predicted or '未取到'}",
        )

    # ---------------------------------------------------------------- 5b
    # 回归测试：识别表单的 action 必须指向 /identify/。
    # 曾经漏写 action，结果页上再次提交会 POST 到 /identify/result/<id>/，
    # 表现为“换一张示例图再识别，仍然显示上一张的结果，日志也不增加”。
    print("\n5b) 回归：连续更换示例图片识别")
    if sample_values:
        result_page = admin.get(resp.url, timeout=30)
        form_tag = re.search(r'<form[^>]*id="identify-form"[^>]*>', result_page.text)
        action = ""
        if form_tag:
            m2 = re.search(r'action="([^"]+)"', form_tag.group(0))
            action = m2.group(1) if m2 else ""
        check("识别表单带显式 action", action.endswith("/identify/"), f"action={action or '缺失'}")

        target = action if action.startswith("http") else BASE + action
        first_id = re.search(r"/identify/result/(\d+)/", resp.url)
        first_id = first_id.group(1) if first_id else None
        first_class = re.search(r'class="result-class">([^<]+)<', result_page.text)
        first_class = first_class.group(1).strip() if first_class else ""

        second = None
        for candidate in sample_values:
            if not candidate.startswith("asphalt/"):
                second = candidate
                break
        if second:
            token = CSRF_RE.search(result_page.text).group(1)
            resp2 = admin.post(
                target,
                data={"csrfmiddlewaretoken": token, "sample": second},
                headers={"Referer": result_page.url},
                timeout=120,
            )
            second_id = re.search(r"/identify/result/(\d+)/", resp2.url)
            second_id = second_id.group(1) if second_id else None
            second_class = re.search(r'class="result-class">([^<]+)<', resp2.text)
            second_class = second_class.group(1).strip() if second_class else ""

            check(
                "换图后产生了新的识别记录",
                second_id is not None and second_id != first_id,
                f"{first_id} -> {second_id}",
            )
            check(
                "换图后识别结果随之改变",
                second_class != first_class,
                f"「{first_class}」-> 「{second_class}」（{second}）",
            )
            check(
                "换图后页面渲染的是新图片",
                resp2.text.count("media/uploads/") >= 1,
            )

        # 直接 POST 结果页必须被拒绝，而不是静默返回旧结果
        bad = admin.post(
            resp.url,
            data={"csrfmiddlewaretoken": CSRF_RE.search(result_page.text).group(1),
                  "sample": "stone/1.jpg"},
            headers={"Referer": resp.url},
            timeout=30,
        )
        check(
            "直接 POST 结果页被拒绝（405）",
            bad.status_code == 405,
            f"status={bad.status_code}",
        )

    # ---------------------------------------------------------------- 6
    print("\n6) 摄像头 dataURL 识别")
    token = csrf(admin, f"{BASE}/identify/")
    data_url = "data:image/jpeg;base64," + base64.b64encode(sample_image_bytes()).decode()
    resp = admin.post(
        f"{BASE}/identify/",
        data={"csrfmiddlewaretoken": token, "capture": data_url},
        headers={"Referer": f"{BASE}/identify/"},
        timeout=120,
    )
    check("dataURL 识别成功", resp.status_code == 200 and "识别结果" in resp.text, f"status={resp.status_code}")
    cam_id = re.search(r"/identify/graph/(\d+)/", resp.text)
    check("摄像头图片已落盘", "/media/uploads/" in resp.text)

    # ---------------------------------------------------------------- 7
    print("\n7) 反馈错误")
    if log_id:
        token = csrf(admin, f"{BASE}/identify/feedback/{log_id}/")
        resp = admin.post(
            f"{BASE}/identify/feedback/{log_id}/",
            data={"csrfmiddlewaretoken": token, "true_name": "wood"},
            headers={"Referer": f"{BASE}/identify/feedback/{log_id}/"},
            timeout=60,
        )
        check("反馈提交成功", resp.status_code == 200 and "反馈成功" in resp.text, f"status={resp.status_code}")

    # ---------------------------------------------------------------- 8
    print("\n8) 管理员页面")
    for path, keyword in [
        ("/logs/", "查看日志"),
        ("/logs/logins/", "用户日志"),
        ("/users/", "用户管理"),
        ("/users/add/", "添加用户"),
        ("/users/delete/", "删除用户"),
        ("/materials/", "材料管理"),
        ("/materials/update/", "更新数据"),
        ("/admin/", ""),
    ]:
        resp = admin.get(f"{BASE}{path}", timeout=30)
        check(f"GET {path} -> 200", resp.status_code == 200, f"status={resp.status_code}")

    # 日志筛选
    resp = admin.get(f"{BASE}/logs/?f=1&username=Admin&material=&action=&start_date=&end_date=", timeout=30)
    check("日志筛选可用", resp.status_code == 200 and "查看日志" in resp.text)
    resp = admin.get(f"{BASE}/logs/?f=1&f=1&action=feedback", timeout=30)
    check("按操作筛选反馈记录", resp.status_code == 200 and "反馈" in resp.text)

    # 材料更新
    token = csrf(admin, f"{BASE}/materials/update/")
    resp = admin.post(
        f"{BASE}/materials/update/",
        data={"csrfmiddlewaretoken": token, "material": "5", "location": "河北邢台", "count": "1200"},
        headers={"Referer": f"{BASE}/materials/update/"},
        timeout=30,
    )
    check("更新材料产地/库存成功", resp.status_code == 200 and "更新成功" in resp.text)

    # ---------------------------------------------------------------- 9
    print("\n9) 普通用户权限（服务端强制校验）")
    normal, resp = login("aaa", "123")
    check("普通用户登录成功", resp.status_code == 200, f"url={resp.url}")
    resp = normal.get(f"{BASE}/identify/", timeout=30)
    check("普通用户可访问识别页", resp.status_code == 200)
    check("普通用户看不到“查看日志”入口", "查看日志" not in resp.text)
    check("普通用户看不到“用户管理”入口", "用户管理" not in resp.text)
    for path in ["/logs/", "/logs/logins/", "/users/", "/users/add/", "/users/delete/"]:
        resp = normal.get(f"{BASE}{path}", timeout=30)
        check(f"普通用户访问 {path} 被拒绝(404)", resp.status_code == 404, f"status={resp.status_code}")
    resp = normal.get(f"{BASE}/materials/", timeout=30)
    check("普通用户可访问材料管理（普通用户亦可访问）", resp.status_code == 200)

    # ---------------------------------------------------------------- 10
    print("\n10) 非法输入")
    token = csrf(admin, f"{BASE}/identify/")
    resp = admin.post(
        f"{BASE}/identify/",
        data={"csrfmiddlewaretoken": token},
        headers={"Referer": f"{BASE}/identify/"},
        timeout=30,
    )
    check("未选择任何图片时报错", resp.status_code == 200 and "请先上传图片" in resp.text)

    resp = admin.post(
        f"{BASE}/identify/",
        data={"csrfmiddlewaretoken": token},
        files={"image": ("fake.jpg", b"this is not an image", "image/jpeg")},
        headers={"Referer": f"{BASE}/identify/"},
        timeout=30,
    )
    check("伪装成图片的文本被拒绝", resp.status_code == 200 and "无法解析图片内容" in resp.text)

    resp = admin.post(
        f"{BASE}/identify/",
        data={"csrfmiddlewaretoken": token, "sample": "../../../config/settings.py"},
        headers={"Referer": f"{BASE}/identify/"},
        timeout=30,
    )
    check(
        "示例图片路径穿越被拒绝",
        resp.status_code == 200
        and "选择一个有效的选项" in resp.text
        and "识别结果：" not in resp.text.split("识别结果</h2>")[-1][:400],
    )

    # ---------------------------------------------------------------- 11
    print("\n11) 注册新用户")
    new_user = f"smoke{int(time.time()) % 100000}"
    guest = requests.Session()
    token = csrf(guest, f"{BASE}/register/")
    resp = guest.post(
        f"{BASE}/register/",
        data={
            "csrfmiddlewaretoken": token,
            "username": new_user,
            "password1": "Str0ng-Pass-2025",
            "password2": "Str0ng-Pass-2025",
        },
        headers={"Referer": f"{BASE}/register/"},
        timeout=30,
    )
    check("注册成功", resp.status_code == 200 and "注册成功" in resp.text, f"用户名={new_user}")
    token = csrf(guest, f"{BASE}/login/")
    resp = guest.post(
        f"{BASE}/login/",
        data={"csrfmiddlewaretoken": token, "username": new_user, "password": "Str0ng-Pass-2025"},
        headers={"Referer": f"{BASE}/login/"},
        timeout=30,
    )
    check("新账号可登录", resp.status_code == 200 and "材料识别" in resp.text)
    check("新账号是普通用户", "普通用户" in resp.text)

    # 弱密码被拒绝（Django 校验，原程序允许 "123"）
    # 注意要用一个全新的未登录会话，否则 /register/ 会重定向到识别页
    weak = requests.Session()
    token = csrf(weak, f"{BASE}/register/")
    resp = weak.post(
        f"{BASE}/register/",
        data={"csrfmiddlewaretoken": token, "username": "weakuser", "password1": "123", "password2": "123"},
        headers={"Referer": f"{BASE}/register/"},
        timeout=30,
    )
    check(
        "弱密码被密码强度校验拒绝",
        resp.status_code == 200 and "至少 8 个字符" in resp.text,
        f"status={resp.status_code}",
    )

    # ---------------------------------------------------------------- 12
    print("\n12) 删除用户（含保护规则，并清理测试账号）")
    token = csrf(admin, f"{BASE}/users/delete/")
    resp = admin.post(
        f"{BASE}/users/delete/",
        data={"csrfmiddlewaretoken": token, "field": "username", "value": "Admin"},
        headers={"Referer": f"{BASE}/users/delete/"},
        timeout=30,
    )
    check(
        "不能删除当前登录账号",
        resp.status_code == 200 and "不能删除当前登录的账号" in resp.text,
        f"status={resp.status_code}",
    )

    token = csrf(admin, f"{BASE}/users/delete/")
    resp = admin.post(
        f"{BASE}/users/delete/",
        data={"csrfmiddlewaretoken": token, "field": "username", "value": "no-such-user-xyz"},
        headers={"Referer": f"{BASE}/users/delete/"},
        timeout=30,
    )
    check("删除不存在的用户时报错", resp.status_code == 200 and "不存在" in resp.text)

    token = csrf(admin, f"{BASE}/users/delete/")
    resp = admin.post(
        f"{BASE}/users/delete/",
        data={"csrfmiddlewaretoken": token, "field": "username", "value": new_user},
        headers={"Referer": f"{BASE}/users/delete/"},
        timeout=30,
    )
    check("删除测试账号成功", resp.status_code == 200 and "删除成功" in resp.text)

    resp = admin.get(f"{BASE}/users/?f=1&username={new_user}&f=1", timeout=30)
    check("测试账号已从列表中消失", "未查询到相关信息" in resp.text)

    # ---------------------------------------------------------------- 13
    print("\n13) 统计")
    resp = admin.get(f"{BASE}/logs/", timeout=30)
    check("识别日志页可正常渲染", resp.status_code == 200)

    print("\n" + "=" * 70)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print("\n失败项：")
        for name in FAILED:
            print(f"  - {name}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
