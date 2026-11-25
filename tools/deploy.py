#!/usr/bin/env python3
"""deploy.py — 把 .deb 推到 CardputerZero 上安装，并抓屏验证（作者自用的小工具）。

需要三个环境变量：
    CZ_HOST   设备地址，例如 192.168.1.50
    CZ_USER   登录用户
    CZ_PW     登录密码（同时也当 sudo 密码用）

用法：
    python tools/deploy.py install         # 安装 dist/ 里最新的 deb
    python tools/deploy.py install --run   # 安装后跑一次并抓屏
    python tools/deploy.py keytest         # 用 uinput 注入按键做端到端验证
    python tools/deploy.py shot --out x.png
    python tools/deploy.py stop            # 收尾，把桌面拉回来
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import paramiko

APP = Path(__file__).resolve().parent.parent
SHOTS = APP / "shots"
KEYINJECT = Path(os.environ.get("CZ_KEYINJECT", APP.parent.parent / "scripts" / "keyinject.py"))

HOST = os.environ.get("CZ_HOST", "")
USER = os.environ.get("CZ_USER", "")
PASS = os.environ.get("CZ_PW")
APP = "/usr/share/APPLaunch/share/qigua"
FB_DUMP = r"""
import mmap, os, base64, sys
fd = os.open("/dev/fb0", os.O_RDWR)
m = mmap.mmap(fd, 108800, mmap.MAP_SHARED, mmap.PROT_READ)
sys.stdout.write(base64.b64encode(m[:108800]).decode())
m.close(); os.close(fd)
"""


def connect():
    missing = [k for k, v in (("CZ_HOST", HOST), ("CZ_USER", USER), ("CZ_PW", PASS)) if not v]
    if missing:
        sys.exit("缺少环境变量：" + ", ".join(missing))
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASS, timeout=15, look_for_keys=False, allow_agent=False)
    return c


def run(c, cmd: str, timeout: int = 120, sudo: bool = False):
    if sudo:
        cmd = "echo '%s' | sudo -S -p '' bash -c %s" % (PASS, _q(cmd))
    _i, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    return out, err


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def put(c, local: str, remote: str):
    sftp = c.open_sftp()
    sftp.put(local, remote)
    sftp.close()


def shot(c, out: str) -> None:
    """从设备抓 320×170 的 fb 内容存成 PNG。"""
    from PIL import Image
    run(c, "cat > /tmp/_fbdump.py <<'EOF'\n" + FB_DUMP + "\nEOF")
    out_b64, err = run(c, "python3 /tmp/_fbdump.py")
    if not out_b64.strip():
        sys.exit(f"fb dump failed: {err[:200]}")
    import base64
    raw = base64.b64decode(out_b64.strip())
    img = Image.new("RGB", (320, 170))
    px = img.load()
    for y in range(170):
        for x in range(320):
            i = (y * 320 + x) * 2
            v = raw[i] | (raw[i + 1] << 8)
            r = (v >> 11) & 0x1F
            g = (v >> 5) & 0x3F
            b = v & 0x1F
            px[x, y] = (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    img.save(out)
    print("shot ->", out)


def cmd_install(c, deb: str, do_run: bool) -> None:
    remote = "/tmp/" + os.path.basename(deb)
    put(c, deb, remote)
    out, err = run(c, f"dpkg-deb -I {remote} | head -20")
    print(out.strip())
    out, err = run(c, f"dpkg -i {remote}", sudo=True, timeout=180)
    print(out.strip()[-1500:])
    if err.strip():
        print("[stderr]", err.strip()[:600])
    out, _ = run(c, f"ls -l /usr/share/APPLaunch/applications/qigua.desktop "
                    f"/usr/share/APPLaunch/bin/qigua {APP}/main.py")
    print(out.strip())

    # 重启 launcher 让它重新读菜单
    run(c, "systemctl --user restart APPLaunch")
    time.sleep(1.5)
    out, _ = run(c, "systemctl --user is-active APPLaunch")
    print("APPLaunch:", out.strip())

    if do_run:
        cmd_run(c)


def cmd_run(c, seconds: int = 12) -> None:
    """停掉 launcher，前台跑若干秒抓一帧，进程超时退出后恢复桌面。"""
    run(c, "systemctl --user stop APPLaunch")
    time.sleep(0.6)

    chan = c.get_transport().open_session()
    chan.settimeout(None)
    chan.exec_command(f"cd {APP} && exec timeout {seconds} python3 main.py -v 2>&1")
    time.sleep(4.5)
    shot(c, str(SHOTS / "device-clock.png"))
    time.sleep(0.3)

    out = b""
    chan.shutdown_read()
    try:
        while True:
            chunk = chan.recv(4096)
            if not chunk:
                break
            out += chunk
    except Exception:                             # noqa: BLE001
        pass
    chan.close()
    text = out.decode("utf-8", "replace")
    print("--- app log ---")
    print(text.strip()[:1200])


def cmd_stop(c) -> None:
    run(c, "pkill -f 'python3 main.py' || true")
    time.sleep(0.3)
    run(c, "systemctl --user start APPLaunch")
    time.sleep(1.0)
    out, _ = run(c, "systemctl --user is-active APPLaunch")
    print("APPLaunch:", out.strip())


def cmd_keytest(c, keys: list[str]) -> None:
    """用 uinput 注入按键，验证 6 起卦 / 翻页 的整条链路。"""
    # 1) 虚拟键盘（serve 模式，fifo 喂键）
    put(c, str(KEYINJECT), f"/home/{USER}/keyinject.py")
    run(c, "systemctl stop keyinject 2>/dev/null; systemctl reset-failed keyinject 2>/dev/null", sudo=True)
    out, err = run(c, f"systemd-run --unit=keyinject --collect /usr/bin/python3 "
                      f"/home/{USER}/keyinject.py --serve /tmp/keycmd", sudo=True)
    print("keyinject:", (out or err).strip()[:200])
    time.sleep(2.0)
    run(c, "chmod 666 /tmp/keycmd 2>/dev/null", sudo=True)
    vpath, _ = run(c, "for f in /sys/class/input/event*/device/name; do "
                      "if grep -q keyinject $f 2>/dev/null; then echo /dev/input/$(basename $(dirname $(dirname $f))); fi; done")
    vpath = vpath.strip().splitlines()[-1] if vpath.strip() else ""
    print("virtual keyboard:", vpath or "(not found)")
    if not vpath:
        return

    # 2) 停桌面，带双键盘启动应用
    run(c, "systemctl --user stop APPLaunch")
    time.sleep(0.6)
    chan = c.get_transport().open_session()
    chan.exec_command(
        f"cd {APP} && exec env CZ_KBD=/dev/input/cardputer-zero-internal:{vpath} "
        f"timeout 30 python3 main.py -v 2>&1")
    time.sleep(3.5)
    shot(c, str(SHOTS / "k0-clock.png"))

    # 3) 逐键注入并抓屏
    for i, key in enumerate(keys, 1):
        out, err = run(c, f"echo {key} >> /tmp/keycmd")
        if err.strip():
            print(f"inject {key} err:", err.strip()[:200])
        time.sleep(1.6)
        shot(c, str(SHOTS / f"k{i}-{key}.png"))

    time.sleep(0.4)
    chan.shutdown_read()
    out = b""
    try:
        while True:
            chunk = chan.recv(8192)
            if not chunk:
                break
            out += chunk
    except Exception:                             # noqa: BLE001
        pass
    chan.close()
    print("--- app log ---")
    print(out.decode("utf-8", "replace").strip()[:1500])

    run(c, "systemctl stop keyinject 2>/dev/null", sudo=True)
    run(c, "systemctl --user start APPLaunch")
    time.sleep(1.0)
    out, _ = run(c, "systemctl --user is-active APPLaunch")
    print("APPLaunch:", out.strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["install", "run", "shot", "stop", "keytest"])
    ap.add_argument("--deb", default=None)
    ap.add_argument("--run", action="store_true", help="install 后立即跑一次并抓屏")
    ap.add_argument("--out", default="shots/qigua/device.png")
    ap.add_argument("--keys", nargs="*", default=[], help="key 动作要注入的键")
    args = ap.parse_args()

    c = connect()
    try:
        if args.action == "install":
            if args.deb:
                deb = args.deb
            else:
                cands = sorted(Path("dist").glob("qigua_*_all.deb"), key=lambda p: p.stat().st_mtime)
                deb = str(cands[-1]) if cands else ""
            if not deb or not os.path.exists(deb):
                sys.exit("找不到 deb，先跑 python build_deb.py")
            cmd_install(c, deb, args.run)
        elif args.action == "run":
            cmd_run(c)
        elif args.action == "shot":
            shot(c, args.out)
        elif args.action == "stop":
            cmd_stop(c)
        elif args.action == "keytest":
            cmd_keytest(c, args.keys or ["6", "right", "right"])
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
