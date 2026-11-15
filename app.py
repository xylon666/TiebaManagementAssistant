"""吧务管理工具 · HTTP 服务（仅依赖标准库 + aiotieba）"""

from __future__ import annotations

import json
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import config as cfg_mod
import paths
from store import Store
from tieba_service import Runner, ServiceError, TiebaService

WEB_DIR = paths.WEB_DIR
DATA_DIR = paths.DATA_DIR

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}

store = Store(DATA_DIR / "bawu.db")
runner = Runner()
service = TiebaService(runner, cfg_mod.load)


class Server(ThreadingHTTPServer):
    # Windows 下 SO_REUSEADDR 允许两个进程绑同一端口，这里必须关掉，
    # 否则重复启动会「静默抢同一个端口」，端口顺延逻辑永远不触发
    allow_reuse_address = False
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TiebaBawu/1.0"

    # ---------- 基础 ----------

    def log_message(self, fmt: str, *args) -> None:  # 静音默认访问日志
        return

    def _json(self, payload: dict, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            raise ServiceError("请求体格式错误")

    def _serve_static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else unquote(path.lstrip("/"))
        target = (WEB_DIR / rel).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            self.send_error(404, "Not Found")
            return
        raw = target.read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type", MIME.get(target.suffix.lower(), "application/octet-stream")
        )
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    # ---------- 路由 ----------

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            self._serve_static(path)
            return
        try:
            if path == "/api/bootstrap":
                self._json(self._bootstrap())
            elif path == "/api/status":
                self._json({"ok": True, **self._status()})
            elif path == "/api/logs":
                self._json({"ok": True, "logs": store.list_logs(limit=100)})
            elif path == "/api/targets":
                self._json({"ok": True, "targets": store.list_targets()})
            elif path == "/api/blocks":
                qs = parse_qs(urlparse(self.path).query)
                fname = cfg_mod.load()["fname"]
                try:
                    page = int((qs.get("page") or ["1"])[0])
                except ValueError:
                    page = 1
                keyword = (qs.get("keyword") or [""])[0]
                all_pages = (qs.get("all") or ["0"])[0] in ("1", "true", "yes")
                self._json(
                    {
                        "ok": True,
                        **service.blocks(
                            fname, keyword, max(1, page), all_pages=all_pages
                        ),
                    }
                )
            elif path == "/api/appeals":
                qs = parse_qs(urlparse(self.path).query)
                fname = cfg_mod.load()["fname"]
                keyword = (qs.get("keyword") or [""])[0]
                self._json({"ok": True, **service.appeals(fname, keyword)})
            elif path == "/api/diagnose":
                fname = cfg_mod.load()["fname"]
                self._json({"ok": True, "diagnosis": service.diagnose(fname)})
            else:
                self._json({"ok": False, "error": f"未知接口 {path}"}, 404)
        except ServiceError as exc:
            self._json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            traceback.print_exc()
            self._json({"ok": False, "error": f"服务端异常：{exc}"}, 500)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path == "/api/resolve":
                self._json(self._resolve(body))
            elif path == "/api/ban":
                self._json(self._ban(body))
            elif path == "/api/unban":
                self._json(self._unban(body))
            elif path == "/api/ban_batch":
                self._json(self._ban_batch(body))
            elif path == "/api/unban_batch":
                self._json(self._unban_batch(body))
            elif path == "/api/appeals/handle":
                self._json(self._handle_appeals(body))
            elif path == "/api/settings":
                self._json(self._save_settings(body))
            elif path == "/api/logs/clear":
                store.clear_logs()
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": f"未知接口 {path}"}, 404)
        except ServiceError as exc:
            self._json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            traceback.print_exc()
            self._json({"ok": False, "error": f"服务端异常：{exc}"}, 500)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/targets/"):
                key = unquote(path[len("/api/targets/"):])
                store.delete_target(key)
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": f"未知接口 {path}"}, 404)
        except Exception as exc:
            self._json({"ok": False, "error": f"服务端异常：{exc}"}, 500)

    # ---------- 业务 ----------

    def _status(self) -> dict:
        cfg = cfg_mod.load()
        status = service.status()
        return {
            "config": cfg_mod.public_view(cfg),
            "login": status["login"],
            "self": status["self"],
            "error": status["error"],
        }

    def _bootstrap(self) -> dict:
        cfg = cfg_mod.load()
        return {
            "ok": True,
            "config": cfg_mod.public_view(cfg),
            "status": service.status(),
            "targets": store.list_targets(limit=20),
            "last_target": store.last_target(),
            "logs": store.list_logs(limit=30),
        }

    def _resolve(self, body: dict) -> dict:
        cfg = cfg_mod.load()
        fname = (body.get("fname") or cfg["fname"]).strip() or cfg["fname"]
        query = body.get("query")
        info = service.resolve(query, fname)
        store.remember_target(
            user_id=info["user_id"],
            user_name=info.get("user_name", ""),
            nick_name=info.get("nick_name", ""),
            portrait=info.get("portrait", ""),
            avatar=info.get("avatar", ""),
            forum_level=info.get("forum_level") or 0,
        )
        return {
            "ok": True,
            "user": info,
            **self._sync_fname(info),
            "targets": store.list_targets(limit=20),
            "last_target": store.last_target(),
        }

    def _ban(self, body: dict) -> dict:
        cfg = cfg_mod.load()
        fname = (body.get("fname") or cfg["fname"]).strip() or cfg["fname"]
        query = body.get("query")
        day = body.get("day", cfg["default_day"])
        reason = (body.get("reason") or cfg["default_reason"] or "").strip()

        result = service.ban(query, day, reason, fname)
        info = result["user"]
        store.remember_target(
            user_id=info["user_id"],
            user_name=info.get("user_name", ""),
            nick_name=info.get("nick_name", ""),
            portrait=info.get("portrait", ""),
            avatar=info.get("avatar", ""),
            forum_level=info.get("forum_level") or 0,
            day=int(day),
            reason=reason,
        )
        store.add_log(
            action="ban",
            fname=info.get("fname") or fname,
            user_id=info["user_id"],
            user_name=info.get("user_name", ""),
            nick_name=info.get("nick_name", ""),
            avatar=info.get("avatar", ""),
            ok=result["ok"],
            message=result["message"]
            + (f"（{result['route']}）" if result.get("route") else ""),
            day=int(day),
            reason=reason,
        )
        return {
            "ok": True,
            "success": result["ok"],
            "message": result["message"],
            "route": result.get("route", ""),
            "attempts": result.get("attempts", []),
            "user": info,
            **self._sync_fname(info),
            "logs": store.list_logs(limit=30),
            "targets": store.list_targets(limit=20),
            "last_target": store.last_target(),
        }

    def _unban(self, body: dict) -> dict:
        cfg = cfg_mod.load()
        fname = (body.get("fname") or cfg["fname"]).strip() or cfg["fname"]
        query = body.get("query")

        result = service.unban(query, fname)
        info = result["user"]
        store.add_log(
            action="unban",
            fname=info.get("fname") or fname,
            user_id=info["user_id"],
            user_name=info.get("user_name", ""),
            nick_name=info.get("nick_name", ""),
            avatar=info.get("avatar", ""),
            ok=result["ok"],
            message=result["message"],
        )
        return {
            "ok": True,
            "success": result["ok"],
            "message": result["message"],
            "route": result.get("route", ""),
            "attempts": result.get("attempts", []),
            "user": info,
            **self._sync_fname(info),
            "logs": store.list_logs(limit=30),
        }

    def _ban_batch(self, body: dict) -> dict:
        cfg = cfg_mod.load()
        fname = (body.get("fname") or cfg["fname"]).strip() or cfg["fname"]
        day = body.get("day", cfg["default_day"])
        reason = (body.get("reason") or cfg["default_reason"] or "").strip()

        result = service.ban_many(body.get("users"), day, reason, fname)
        for item in result["results"]:
            store.add_log(
                action="ban",
                fname=result["fname"],
                user_id=item["user_id"],
                user_name=item.get("user_name", ""),
                nick_name=item.get("name", ""),
                avatar="",
                ok=item["ok"],
                message=item["message"]
                + (f"（{item['route']}）" if item.get("route") else ""),
                day=int(day),
                reason=reason,
            )
        return {
            "ok": True,
            "fname": result["fname"],
            "results": result["results"],
            "success_count": sum(1 for r in result["results"] if r["ok"]),
            "fail_count": sum(1 for r in result["results"] if not r["ok"]),
            "logs": store.list_logs(limit=30),
        }

    def _unban_batch(self, body: dict) -> dict:
        cfg = cfg_mod.load()
        fname = (body.get("fname") or cfg["fname"]).strip() or cfg["fname"]

        result = service.unban_many(body.get("users"), fname)
        for item in result["results"]:
            store.add_log(
                action="unban",
                fname=result["fname"],
                user_id=item["user_id"],
                user_name=item.get("user_name", ""),
                nick_name=item.get("name", ""),
                avatar="",
                ok=item["ok"],
                message=item["message"],
            )
        return {
            "ok": True,
            "fname": result["fname"],
            "results": result["results"],
            "success_count": sum(1 for r in result["results"] if r["ok"]),
            "fail_count": sum(1 for r in result["results"] if not r["ok"]),
            "logs": store.list_logs(limit=30),
        }

    def _handle_appeals(self, body: dict) -> dict:
        cfg = cfg_mod.load()
        fname = (body.get("fname") or cfg["fname"]).strip() or cfg["fname"]
        refuse = bool(body.get("refuse"))

        result = service.handle_appeals(body.get("items"), refuse, fname)
        for item in result["results"]:
            store.add_log(
                action="appeal_refuse" if refuse else "appeal_pass",
                fname=result["fname"],
                user_id=item["user_id"],
                user_name=item.get("user_name", ""),
                nick_name=item.get("name", ""),
                avatar="",
                ok=item["ok"],
                message=item["message"],
                day=item.get("punish_day") or None,
                reason=item.get("appeal_reason", ""),
            )
        return {
            "ok": True,
            "fname": result["fname"],
            "refuse": result["refuse"],
            "results": result["results"],
            "success_count": result["success_count"],
            "fail_count": result["fail_count"],
            "hint": result.get("hint", ""),
            "logs": store.list_logs(limit=30),
        }

    @staticmethod
    def _sync_fname(info: dict) -> dict:
        """吧名解析结果与配置不一致时（例如「QQ飞车吧」其实是个同名空吧），自动纠正并落库。"""
        resolved = (info.get("fname") or "").strip()
        requested = (info.get("requested_fname") or "").strip()
        if resolved and requested and resolved != requested:
            cfg_mod.save({"fname": resolved})
            return {
                "fname_corrected": {"from": requested, "to": resolved},
                "config": cfg_mod.public_view(cfg_mod.load()),
            }
        return {}

    def _save_settings(self, body: dict) -> dict:
        patch = {}
        if "BDUSS" in body:
            bduss = str(body.get("BDUSS") or "").strip()
            if bduss and len(bduss) != 192:
                raise ServiceError(
                    f"BDUSS 长度应为 192 个字符，当前是 {len(bduss)} 个，"
                    "大概是复制时漏掉了一段，请重新从浏览器 Cookie 中完整复制"
                )
            patch["BDUSS"] = bduss
        if "STOKEN" in body:
            patch["STOKEN"] = str(body.get("STOKEN") or "").strip()
        if "fname" in body:
            fname = str(body.get("fname") or "").strip()
            if not fname:
                raise ServiceError(
                    "请填写目标贴吧的名字，例如「QQ飞车」——带不带「吧」都行，程序会自动校正"
                )
            # 不能自动补「吧」：贴吧里「QQ飞车」和「QQ飞车吧」是两个不同的吧
            patch["fname"] = fname
        if "default_day" in body:
            patch["default_day"] = body.get("default_day")
        if "default_reason" in body:
            patch["default_reason"] = str(body.get("default_reason") or "").strip()

        cfg = cfg_mod.save(patch)
        return {
            "ok": True,
            "config": cfg_mod.public_view(cfg),
            "status": service.status(),
        }


def _existing_instance_url(host: str, port: int) -> str | None:
    """检测本机是否已经跑着一个本程序实例（避免重复启动开两份）。"""
    if host not in ("127.0.0.1", "localhost"):
        return None
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://{host}:{port}/api/status", timeout=1.2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if isinstance(data, dict) and data.get("ok") and "login" in data:
        return f"http://{host}:{port}/"
    return None


def _bind_server(host: str, port: int) -> tuple[Server, int]:
    """端口被占用时顺延找空闲端口，避免二次启动直接报错退出。"""
    last_error: Exception | None = None
    for candidate in [port] + [port + i for i in range(1, 11)]:
        try:
            return Server((host, candidate), Handler), candidate
        except OSError as exc:
            last_error = exc
    raise last_error or OSError("无法绑定端口")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        except Exception:
            pass

    cfg = cfg_mod.load()
    host, port = cfg["host"], int(cfg["port"])

    running = _existing_instance_url(host, port)
    if running:
        print(f"程序已经在运行：{running}")
        print("已为你打开页面，本窗口可以关闭。")
        if "--no-browser" not in sys.argv:
            webbrowser.open(running)
        return

    try:
        httpd, actual_port = _bind_server(host, port)
    except OSError as exc:
        print(f"[x] 端口 {port} 及后续 10 个端口都被占用：{exc}")
        input("按回车键退出…")
        sys.exit(1)

    url = f"http://{host}:{actual_port}/"
    print("=" * 52)
    print("  贴吧吧务管理工具")
    print(f"  地址：{url}")
    print(f"  配置：{paths.CONFIG_PATH}")
    print(f"  数据库：{DATA_DIR / 'bawu.db'}")
    if not (cfg.get("BDUSS") or "").strip():
        print("  [!] 还没有配置 BDUSS，浏览器打开后点右上角「设置」填写")
    if actual_port != port:
        print(f"  [!] 端口 {port} 被占用，已改用 {actual_port}")
    print("  关闭本窗口即退出程序")
    print("=" * 52)

    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    finally:
        httpd.server_close()
        service.close()


if __name__ == "__main__":
    main()
