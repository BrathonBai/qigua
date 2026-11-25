#!/usr/bin/env python3
"""build_deb.py — 在任意平台（含 Windows）把 qigua 打成 .deb。

.deb 只是 ar 归档：debian-binary + control.tar.gz + data.tar.gz，
所以不需要 dpkg-deb，也不需要交叉编译（Architecture: all，纯 Python）。

用法：
    python build_deb.py            # -> dist/qigua_<version>_all.deb
"""

from __future__ import annotations

import gzip
import io
import os
import shutil
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
ASSETS = ROOT / "assets"
DIST = ROOT / "dist"

APP_ID = "qigua"
APP_NAME = "起卦"
APP_DESC = "梅花易数 · 时空起卦：数字时钟 + 农历干支 + 一键成卦"
MAINTAINER = "fiddling.work <noreply@fiddling.work>"
SHARE = f"usr/share/APPLaunch/share/{APP_ID}"

PY_FILES = ["main.py", "astro.py", "gua.py", "net.py", "cz.py", "ui.py"]

LAUNCHER = """#!/bin/sh
# 起卦 — 启动器（退出后把桌面拉回来）
/usr/bin/python3 /usr/share/APPLaunch/share/qigua/main.py "$@"
rc=$?
if ! systemctl --user is-active --quiet APPLaunch 2>/dev/null; then
    systemctl --user start APPLaunch >/dev/null 2>&1 || true
fi
exit $rc
"""

DESKTOP = f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
Comment={APP_DESC}
Exec=/usr/share/APPLaunch/bin/{APP_ID}
Terminal=false
Sysplause=true
Icon=share/images/{APP_ID}.png
Categories=Utility;
StartupNotify=false
"""


def version() -> str:
    v = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    return v or "0.1.0"


def _add(tar: tarfile.TarFile, name: str, data: bytes | None = None,
         mode: int = 0o644, src: Path | None = None, mtime: float | None = None) -> None:
    """往 tar 里加一个文件（uid/gid=0，root 属主）。"""
    info = tarfile.TarInfo(name)
    info.mode = mode
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mtime = int(mtime or time.time())
    if data is not None:
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    else:
        assert src is not None
        payload = src.read_bytes()
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))


def _add_dir(tar: tarfile.TarFile, name: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mtime = int(time.time())
    tar.addfile(info)


def _tar_gz(entries: list[tuple], compress: bool = True) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for e in entries:
            if e[0] == "dir":
                _add_dir(tar, e[1])
            else:
                payload = e[2] if len(e) > 2 else None
                mode = e[3] if len(e) > 3 else 0o644
                if isinstance(payload, Path):
                    _add(tar, e[1], None, mode, src=payload)
                else:
                    _add(tar, e[1], payload or b"", mode)
    raw = buf.getvalue()
    if not compress:
        return raw
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", compresslevel=9, mtime=0) as gz:
        gz.write(raw)
    return out.getvalue()


def build() -> Path:
    ver = version()
    DIST.mkdir(exist_ok=True)

    # ---- data.tar.gz
    entries: list[tuple] = [
        ("dir", "usr"),
        ("dir", "usr/share"),
        ("dir", "usr/share/APPLaunch"),
        ("dir", "usr/share/APPLaunch/share"),
        ("dir", SHARE),
        ("dir", f"{SHARE}/assets"),
        ("dir", "usr/share/APPLaunch/bin"),
        ("dir", "usr/share/APPLaunch/applications"),
        ("dir", "usr/share/APPLaunch/share/images"),
    ]
    for name in PY_FILES:
        src = SRC / name
        if not src.exists():
            raise SystemExit(f"missing {src}")
        entries.append(("file", f"{SHARE}/{name}", src, 0o644))
    icon = ASSETS / "icon.png"
    entries.append(("file", f"{SHARE}/assets/icon.png", icon, 0o644))
    # launcher 会按尺寸找图标：<name>.png / _100 / _80，缺了就会显示成默认图
    for suffix in ("", "_100", "_80", "_256"):
        variant = ASSETS / f"icon{suffix}.png"
        if variant.exists():
            entries.append(("file", f"usr/share/APPLaunch/share/images/{APP_ID}{suffix}.png",
                            variant, 0o644))
    entries.append(("file", f"usr/share/APPLaunch/bin/{APP_ID}", LAUNCHER.encode("utf-8"), 0o755))
    entries.append(("file", f"usr/share/APPLaunch/applications/{APP_ID}.desktop",
                    DESKTOP.encode("utf-8"), 0o644))
    readme = ROOT / "README.md"
    if readme.exists():
        entries.append(("dir", "usr/share/doc"))
        entries.append(("dir", f"usr/share/doc/{APP_ID}"))
        entries.append(("file", f"usr/share/doc/{APP_ID}/README.md", readme, 0o644))

    data_tar = _tar_gz(entries)

    # ---- control.tar.gz
    control = (
        f"Package: {APP_ID}\n"
        f"Version: {ver}\n"
        "Architecture: all\n"
        f"Maintainer: {MAINTAINER}\n"
        "Depends: python3, python3-pil\n"
        "Section: misc\n"
        "Priority: optional\n"
        f"Description: {APP_DESC}\n"
        " 打开即显示数字时钟（NTP 校时）、农历与干支四柱，按 6 键以上卦。\n"
    )
    ctrl_entries = [
        ("file", "./control", control.encode("utf-8"), 0o644),
        ("file", "./md5sums", b"", 0o644),
    ]
    control_tar = _tar_gz(ctrl_entries)

    # ---- ar 归档
    out = DIST / f"{APP_ID}_{ver}_all.deb"
    with open(out, "wb") as f:
        f.write(b"!<arch>\n")

        def member(name: str, payload: bytes, mode: int = 0o100644) -> None:
            header = (
                name.ljust(16)
                + str(int(time.time())).ljust(12)
                + "0".ljust(6)
                + "0".ljust(6)
                + f"{mode:o}".ljust(8)
                + str(len(payload)).ljust(10)
                + "`\n"
            )
            f.write(header.encode("ascii"))
            f.write(payload)
            if len(payload) % 2:
                f.write(b"\n")

        member("debian-binary", b"2.0\n")
        member("control.tar.gz", control_tar)
        member("data.tar.gz", data_tar)

    print(f"OK {out}  ({out.stat().st_size/1024:.1f} KB)")
    print(f"install: sudo apt install -y --reinstall ./{out.name}")
    return out


if __name__ == "__main__":
    build()
