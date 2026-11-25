#!/usr/bin/env python3
"""check_store.py — 上架前自检（不需要 dpkg、不需要联网、Windows 也能跑）。

对照官方两份文档逐条查：
  * 《应用开发规范》 https://cardputer.cc/#/documents/cp0-dev
  * 《应用提交指南》 https://cardputer.cc/#/documents/cp0-submit
  * app-builder.json schema（AppBuilder/docs/APP_BUILDER_JSON.md）

用法：
    python tools/check_store.py [--deb dist/qigua_0.6.0_all.deb]
退出码 0 = 可以提交；1 = 有硬性问题。
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import re
import sys
import tarfile
from pathlib import Path

from PIL import Image

APP = Path(__file__).resolve().parent.parent
CATEGORIES = {
    "System Tools", "Development", "Hardware & IoT", "Security", "Radio & Comms",
    "AI", "Creative & Office", "Media", "Games", "Emulators", "Education",
    "Lifestyle", "Other",
}
PERMISSION_KEYS = {"camera", "microphone", "imu", "network",
                   "additional_hardware", "background_service", "external_display"}
PLACEHOLDER_HINTS = ("todo", "your name", "m5stack", "template", "example")

errors: list[str] = []
warns: list[str] = []


def ok(name: str, cond: bool, detail: str = "", hard: bool = True) -> bool:
    tag = "ok  " if cond else ("FAIL" if hard else "warn")
    print(f"{tag} {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        (errors if hard else warns).append(f"{name}: {detail}")
    return cond


# ---------------------------------------------------------------- deb 读取

def read_ar(path: Path) -> dict[str, bytes]:
    data = path.read_bytes()
    if data[:8] != b"!<arch>\n":
        raise ValueError("not an ar archive")
    out, off = {}, 8
    while off + 60 <= len(data):
        hdr = data[off:off + 60]
        name = hdr[0:16].decode("ascii", "replace").strip()
        size = int(hdr[48:58].decode("ascii").strip())
        body = data[off + 60:off + 60 + size]
        out[name] = body
        off += 60 + size + (size % 2)
    return out


def control_fields(ctrl_tar_gz: bytes) -> dict[str, str]:
    with tarfile.open(fileobj=io.BytesIO(ctrl_tar_gz), mode="r:gz") as tar:
        for m in tar.getmembers():
            if m.name.lstrip("./") == "control":
                text = tar.extractfile(m).read().decode("utf-8", "replace")
                fields = {}
                for line in text.splitlines():
                    if ":" in line and not line.startswith(" "):
                        k, v = line.split(":", 1)
                        fields[k.strip()] = v.strip()
                return fields
    return {}


def data_entries(data_tar_gz: bytes) -> dict[str, tuple[int, bytes | None]]:
    """路径 -> (mode, 内容或 None)。"""
    out: dict[str, tuple[int, bytes | None]] = {}
    with tarfile.open(fileobj=io.BytesIO(data_tar_gz), mode="r:gz") as tar:
        for m in tar.getmembers():
            content = None
            if m.isfile():
                content = tar.extractfile(m).read()
            out[m.name.lstrip("./")] = (m.mode, content)
    return out


# ---------------------------------------------------------------- 检查项

def check_manifest() -> dict:
    p = APP / "app-builder.json"
    if not ok("app-builder.json 存在", p.exists()):
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:                      # noqa: BLE001
        ok("app-builder.json 可解析", False, str(exc))
        return {}
    ok("app-builder.json 可解析", True)

    for key in ("package_name", "version", "app_name", "bin_name"):
        ok(f"顶层 {key}", bool(str(raw.get(key, "")).strip()), repr(raw.get(key)))

    pkg = str(raw.get("package_name", ""))
    ok("package_name 合法（小写/数字/.+-）", bool(re.fullmatch(r"[a-z0-9][a-z0-9.+-]*", pkg)), pkg)

    store = raw.get("store") or {}
    ok("有 store 段", bool(store))

    summary = store.get("summary", "")
    ok("store.summary 非空且 ≤80 字符", 0 < len(summary) <= 80, f"{len(summary)} 字符")

    cats = store.get("categories") or []
    ok("categories 1~2 个且都在枚举内", 1 <= len(cats) <= 2 and set(cats) <= CATEGORIES, str(cats))

    shots = store.get("screenshots") or []
    ok("screenshots 1~6 张", 1 <= len(shots) <= 6, f"{len(shots)} 张")
    for s in shots:
        f = APP / s
        if not ok(f"截图存在 {s}", f.exists()):
            continue
        with Image.open(f) as im:
            ok(f"截图尺寸 {s} = 320×170", im.size == (320, 170), str(im.size))
            ok(f"截图格式 {s} PNG", im.format == "PNG", str(im.format))

    icon = store.get("icon", "")
    ip = APP / icon
    if ok("icon 存在", bool(icon) and ip.exists(), icon):
        with Image.open(ip) as im:
            w, h = im.size
            ok("icon 为正方形", w == h, f"{w}×{h}")
            ok("icon 边长 128~512", 128 <= w <= 512, f"{w}px")
            ok("icon 为 PNG", im.format == "PNG", str(im.format))
            ok("icon 无圆角（四角不透明）", _corners_opaque(im), "四角应不透明")

    lic = store.get("license", "")
    ok("license 是 SPDX 标识", bool(re.fullmatch(r"[A-Za-z0-9.+-]+", lic)), lic)

    code = store.get("share_code", "")
    ok("share_code 正好 4 位 A-Z0-9", bool(re.fullmatch(r"[A-Z0-9]{4}", code)), code)
    ok("share_code 不是占位符", code.upper() not in {"TEMP", "TODO", "0000"}, code)

    perms = store.get("permissions") or {}
    ok("permissions 恰好 7 项",
       set(perms) == PERMISSION_KEYS and all(isinstance(v, bool) for v in perms.values()),
       f"{sorted(perms)}")

    author = store.get("author") or {}
    name = str(author.get("display_name", ""))
    ok("author.display_name 非空", bool(name), name)
    low = name.lower()
    ok("author 不是模板占位", not any(h in low for h in PLACEHOLDER_HINTS), name)

    blob = json.dumps(raw, ensure_ascii=False).lower()
    ok("元数据里没有 TODO 占位", "todo" not in blob)
    repo = str(store.get("source_repo", ""))
    ok("source_repo 不指向模板仓库", "CardputerZero/Template" not in repo, repo or "(未填)")

    # 版本一致性
    ver = str(raw.get("version", ""))
    vf = (APP / "VERSION").read_text(encoding="utf-8").strip() if (APP / "VERSION").exists() else ""
    ok("version 与 VERSION 文件一致", ver == vf, f"{ver} vs {vf}")
    src = (APP / "src" / "main.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', src)
    ok("version 与 main.__version__ 一致", bool(m) and m.group(1) == ver,
       f"{m.group(1) if m else '?'} vs {ver}")
    return raw


def _corners_opaque(im: Image.Image) -> bool:
    if im.mode not in ("RGBA", "LA"):
        return True
    a = im.convert("RGBA").getchannel("A")
    w, h = im.size
    for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if a.getpixel(xy) < 250:
            return False
    return True


def check_deb(deb: Path, raw: dict) -> None:
    if not ok("deb 存在", deb.exists(), str(deb)):
        return

    parts = read_ar(deb)
    ok("deb 含 debian-binary/control.tar.gz/data.tar.gz",
       {"debian-binary", "control.tar.gz", "data.tar.gz"} <= set(parts))

    ctrl = control_fields(parts["control.tar.gz"])
    ok("control: Package 与 package_name 一致",
       ctrl.get("Package") == raw.get("package_name"), ctrl.get("Package", ""))
    ok("control: Version 与元数据一致",
       ctrl.get("Version") == raw.get("version"), ctrl.get("Version", ""))
    ok("control: Architecture", bool(ctrl.get("Architecture")), ctrl.get("Architecture", ""))
    mt = ctrl.get("Maintainer", "")
    ok("control: Maintainer 已替换（非模板默认）",
       bool(mt) and not any(h in mt.lower() for h in ("your name", "m5stack", "example")), mt)

    entries = data_entries(parts["data.tar.gz"])
    desktops = [p for p in entries if p.endswith(".desktop")]
    ok("包内含 .desktop", bool(desktops), str(desktops[:2]))

    for d in desktops:
        text = (entries[d][1] or b"").decode("utf-8", "replace")
        ok(f"{os.path.basename(d)} 有 [Desktop Entry]", "[Desktop Entry]" in text)
        name_m = re.search(r"^Name=(.+)$", text, re.M)
        exec_m = re.search(r"^Exec=(.+)$", text, re.M)
        icon_m = re.search(r"^Icon=(.+)$", text, re.M)
        ok(f"{os.path.basename(d)} Name/Exec 非空",
           bool(name_m and name_m.group(1).strip()) and bool(exec_m and exec_m.group(1).strip()),
           f"{name_m.group(1) if name_m else '?'} / {exec_m.group(1) if exec_m else '?'}")
        if icon_m:
            icon_ref = icon_m.group(1).strip()
            target = "usr/share/APPLaunch/" + icon_ref.lstrip("/")
            ok("desktop 的 Icon 在包内存在", target in entries, icon_ref)
        if exec_m:
            bin_path = exec_m.group(1).strip().lstrip("/")
            ok("desktop 的 Exec 在包内存在且可执行",
               bin_path in entries and (entries[bin_path][0] & 0o111) != 0, bin_path)

    ok("包内含 app 主程序.py", any(p.endswith("/main.py") for p in entries),
       str([p for p in entries if p.endswith('main.py')][:2]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deb", default=None)
    args = ap.parse_args()

    print("== 元数据 ==")
    raw = check_manifest()

    print("\n== 安装包 ==")
    deb = Path(args.deb) if args.deb else None
    if deb is None:
        cands = sorted((APP / "dist").glob("qigua_*_all.deb"),
                       key=lambda p: p.stat().st_mtime) if (APP / "dist").is_dir() else []
        deb = cands[-1] if cands else APP / "dist" / "qigua_missing.deb"
    check_deb(deb, raw)

    print()
    if warns:
        print("提醒：" + "；".join(warns))
    if errors:
        print(f"❌ {len(errors)} 项不通过：")
        for e in errors:
            print("   -", e)
        return 1
    print("✅ 全部通过，可以交给 czdev publish / dev.cardputer.cc")
    return 0


if __name__ == "__main__":
    sys.exit(main())
