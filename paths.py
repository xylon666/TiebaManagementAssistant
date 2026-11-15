"""运行目录 / 资源目录的统一解析。

源码运行与 PyInstaller 打包后（sys.frozen）行为保持一致：

- RES_DIR：只读资源（web/ 静态文件）。打包后在 sys._MEIPASS 里。
- APP_DIR：可写目录（config.json、data/bawu.db）。打包后优先用 exe 同目录，
  实现「绿色便携」；若该目录不可写（例如放在 Program Files），回落到
  %APPDATA%\\TiebaBawu。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))


def _resource_dir() -> Path:
    if FROZEN:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return Path(__file__).resolve().parent


def _writable_dir() -> Path:
    if not FROZEN:
        return Path(__file__).resolve().parent

    exe_dir = Path(sys.executable).resolve().parent
    try:
        probe = exe_dir / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return exe_dir
    except OSError:
        base = os.environ.get("APPDATA") or str(Path.home())
        target = Path(base) / "TiebaBawu"
        target.mkdir(parents=True, exist_ok=True)
        return target


RES_DIR = _resource_dir()
APP_DIR = _writable_dir()
WEB_DIR = RES_DIR / "web"
DATA_DIR = APP_DIR / "data"
CONFIG_PATH = APP_DIR / "config.json"
