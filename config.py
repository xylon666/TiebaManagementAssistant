"""配置读写：config.json 为主，允许用环境变量覆盖敏感项。"""

from __future__ import annotations

import json
import os
from typing import Any

from paths import CONFIG_PATH

DEFAULTS: dict[str, Any] = {
    # 贴吧登录凭证：BDUSS 必填，STOKEN 一般留空即可
    "BDUSS": "",
    "STOKEN": "",
    # 目标贴吧（首次运行由用户在设置里填写）
    "fname": "",
    # 默认封禁天数
    "default_day": 10,
    # 默认封禁理由
    "default_reason": "违反吧规",
    # 服务监听地址（仅本机）
    "host": "127.0.0.1",
    "port": 8765,
}

# 环境变量覆盖，便于不想把凭证落盘的用户
ENV_MAP = {
    "BDUSS": "TIEBA_BDUSS",
    "STOKEN": "TIEBA_STOKEN",
    "fname": "TIEBA_FNAME",
}


def _coerce(key: str, value: Any) -> Any:
    if key in ("default_day", "port"):
        try:
            return int(value)
        except (TypeError, ValueError):
            return DEFAULTS[key]
    return value


def load() -> dict[str, Any]:
    """读取当前配置（配置文件 + 环境变量）。"""
    cfg: dict[str, Any] = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text("utf-8"))
        except Exception:
            data = None
        if isinstance(data, dict):
            for key, value in data.items():
                if key in DEFAULTS:
                    cfg[key] = _coerce(key, value)

    for key, env_name in ENV_MAP.items():
        env_value = os.environ.get(env_name)
        if env_value:
            cfg[key] = env_value

    cfg["default_day"] = max(1, min(int(cfg["default_day"] or 10), 9999))
    return cfg


def save(patch: dict[str, Any]) -> dict[str, Any]:
    """增量保存配置（只接受已知字段）。"""
    cfg = load()
    for key, value in patch.items():
        if key in DEFAULTS and value is not None:
            cfg[key] = _coerce(key, value)
    cfg["default_day"] = max(1, min(int(cfg["default_day"] or 10), 9999))
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return cfg


def public_view(cfg: dict[str, Any]) -> dict[str, Any]:
    """返回给前端的配置视图：凭证打码，只暴露是否已配置。"""
    bduss = (cfg.get("BDUSS") or "").strip()
    stoken = (cfg.get("STOKEN") or "").strip()
    return {
        "fname": cfg["fname"],
        "default_day": cfg["default_day"],
        "default_reason": cfg["default_reason"],
        "configured": bool(bduss),
        "bduss_tail": f"...{bduss[-6:]}" if len(bduss) > 6 else ("已配置" if bduss else ""),
        "has_stoken": bool(stoken),
    }
