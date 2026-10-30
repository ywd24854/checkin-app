#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""比赛打卡记录 · Windows 启动器（中文交互都放在这里，bat 只做环境检查）

用法：
    start.bat                  直接启动（默认 8000，自动打开浏览器）
    start.bat 8080             指定端口启动
    start.bat menu             打开菜单（装依赖 / 备份数据 / 查看地址）
    start.bat serve --no-browser
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
LOG_DIR = DATA_DIR / "logs"
BACKUP_DIR = BASE / "backups"
REQ_FILE = BASE / "requirements.txt"

HOST = os.getenv("HOST", "").strip() or "0.0.0.0"
DEFAULT_PORT = int(os.getenv("PORT", "").strip() or "8000")

# 模块名 -> pip 包名
MODULES = [
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("multipart", "python-multipart"),
    ("PIL", "pillow"),
    ("openpyxl", "openpyxl"),
]


# ---------------------------------------------------------------- 环境
def missing_packages() -> list[str]:
    import importlib

    missing = []
    for mod, pkg in MODULES:
        try:
            importlib.import_module(mod)
        except Exception:
            missing.append(pkg)
    return missing


def install_deps() -> bool:
    pkgs = missing_packages()
    if not pkgs:
        print("依赖已齐全，无需安装。")
        return True
    print("缺少依赖：" + "、".join(pkgs))
    if not REQ_FILE.exists():
        print(f"找不到 {REQ_FILE.name}，无法自动安装。")
        return False
    print("开始安装（首次需要联网，可能要几分钟）…")
    cmd = [sys.executable, "-m", "pip", "install", "-r", str(REQ_FILE)]
    try:
        rc = subprocess.call(cmd)
    except Exception as exc:  # noqa: BLE001
        print(f"安装失败：{exc}")
        return False
    if rc != 0:
        print("pip 安装返回失败。若服务器无外网，请在有网的机器上执行：")
        print("    pip download -r requirements.txt -d wheels")
        print("然后把 wheels 目录一起拷过去，改成：")
        print("    pip install --no-index --find-links=wheels -r requirements.txt")
        return False
    left = missing_packages()
    if left:
        print("安装后仍缺：" + "、".join(left))
        return False
    print("依赖安装完成。")
    return True


# ---------------------------------------------------------------- 端口 / 地址
def port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, port))
            return True
        except OSError:
            return False


def pick_port(preferred: int) -> int | None:
    if port_available(preferred):
        return preferred
    print(f"端口 {preferred} 已被别的程序占用。")
    for p in range(preferred + 1, preferred + 21):
        if port_available(p):
            print(f"自动改用 {p}。")
            return p
    print("附近 20 个端口都占用了，请手动指定，例如：start.bat 9000")
    return None


def lan_ips() -> list[str]:
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return ips


# ---------------------------------------------------------------- 日志
def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = LOG_DIR / f"server-{time.strftime('%Y%m%d')}.log"
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", "%H:%M:%S")

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    file_handler = logging.FileHandler(logfile, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    return logfile


def print_banner(port: int, logfile: Path):
    print()
    print("=" * 58)
    print("  比赛打卡记录 · 服务已启动")
    print("=" * 58)
    print(f"  本机访问：http://127.0.0.1:{port}")
    for ip in lan_ips():
        print(f"  局域网  ：http://{ip}:{port}   ← 队员手机连同一个 WiFi 用这个")
    print(f"  管理员  ：admin（首次密码见首次启动提示或 CHECKIN_ADMIN_PASSWORD）")
    print(f"  运行日志：{logfile}")
    print("-" * 58)
    print("  关闭：在这个窗口按 Ctrl + C，或直接关掉窗口")
    print("  首次对外访问若弹出 Windows 防火墙提示，请勾选「专用网络」并允许")
    print("=" * 58)
    print()


# ---------------------------------------------------------------- 启动
def serve(port: int, open_browser: bool = True) -> int:
    missing = missing_packages()
    if missing:
        print("依赖未安装：" + "、".join(missing))
        print("请先运行：start.bat menu  →  选 2 安装依赖")
        return 1

    port = pick_port(port)
    if port is None:
        return 1

    logfile = setup_logging()
    url = f"http://127.0.0.1:{port}"
    if open_browser:
        threading.Timer(1.8, lambda: webbrowser.open(url)).start()

    os.chdir(BASE)
    if str(BASE) not in sys.path:
        sys.path.insert(0, str(BASE))

    print_banner(port, logfile)
    try:
        import uvicorn

        uvicorn.run("app.main:app", host=HOST, port=port, log_config=None, access_log=True)
    except KeyboardInterrupt:
        print("\n已停止。")
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("launcher").exception("启动失败")
        print(f"\n启动失败：{exc}")
        print(f"详细日志：{logfile}")
        return 1
    return 0


# ---------------------------------------------------------------- 备份
def backup_data() -> Path | None:
    if not DATA_DIR.exists():
        print("还没有 data 目录，暂时没有可备份的数据。")
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"checkin-data-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in DATA_DIR.rglob("*"):
            if not p.is_file():
                continue
            if "logs" in p.relative_to(DATA_DIR).parts:
                continue
            zf.write(p, Path("data") / p.relative_to(DATA_DIR))
            count += 1
    size_mb = target.stat().st_size / 1024 / 1024
    print(f"已备份 {count} 个文件 → {target}（{size_mb:.1f} MB）")
    print("备份里含数据库、照片和 secret.key：secret.key 泄露可伪造登录态，请妥善保管。")
    return target


# ---------------------------------------------------------------- 菜单
def menu() -> int:
    while True:
        print()
        print("=" * 58)
        print("  比赛打卡记录 · 管理菜单")
        print("=" * 58)
        print("  1) 启动服务")
        print("  2) 安装 / 修复依赖")
        print("  3) 备份数据（数据库 + 照片）")
        print("  4) 查看本机与局域网访问地址")
        print("  5) 环境自检")
        print("  0) 退出")
        print("=" * 58)
        try:
            choice = input("请输入编号后回车：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if choice == "1":
            raw = input(f"端口（直接回车用 {DEFAULT_PORT}）：").strip()
            port = DEFAULT_PORT
            if raw:
                if not raw.isdigit() or not (1 <= int(raw) <= 65535):
                    print("端口要是 1~65535 的数字。")
                    continue
                port = int(raw)
            return serve(port, open_browser=True)
        elif choice == "2":
            install_deps()
        elif choice == "3":
            backup_data()
        elif choice == "4":
            print(f"本机：http://127.0.0.1:{DEFAULT_PORT}")
            ips = lan_ips()
            if ips:
                for ip in ips:
                    print(f"局域网：http://{ip}:{DEFAULT_PORT}")
            else:
                print("没拿到局域网 IP，可能是没连网或只有回环地址。")
        elif choice == "5":
            check_env()
        elif choice in ("0", "q", "exit"):
            return 0
        else:
            print("没有这个编号，请重新输入。")


def check_env() -> None:
    print(f"Python    ：{sys.version.split()[0]}（{sys.executable}）")
    print(f"工作目录  ：{BASE}")
    missing = missing_packages()
    print("依赖      ：" + ("齐全" if not missing else "缺少 " + "、".join(missing)))
    print("数据目录  ：" + ("存在" if DATA_DIR.exists() else "还没建（首次启动自动创建）"))
    db = DATA_DIR / "checkin.db"
    if db.exists():
        print(f"数据库    ：{db.name}（{db.stat().st_size / 1024:.0f} KB）")
    print(f"端口 {DEFAULT_PORT}：" + ("可用" if port_available(DEFAULT_PORT) else "被占用"))
    ips = lan_ips()
    print("局域网 IP ：" + ("、".join(ips) if ips else "未获取到"))


# ---------------------------------------------------------------- 入口
def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a]
    no_browser = "--no-browser" in args
    args = [a for a in args if a != "--no-browser"]

    if not args:
        return serve(DEFAULT_PORT, open_browser=not no_browser)

    head = args[0].lower()
    if head == "menu":
        return menu()
    if head == "backup":
        backup_data()
        return 0
    if head == "deps":
        return 0 if install_deps() else 1
    if head == "check":
        check_env()
        return 0
    if head == "serve":
        port = DEFAULT_PORT
        if len(args) > 1:
            if not args[1].isdigit():
                print(f"端口不合法：{args[1]}")
                return 2
            port = int(args[1])
        return serve(port, open_browser=not no_browser)
    if head.isdigit():
        return serve(int(head), open_browser=not no_browser)

    print(f"无法识别的参数：{args[0]}")
    print("可用：start.bat [端口] | start.bat menu | start.bat serve [端口] --no-browser")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
