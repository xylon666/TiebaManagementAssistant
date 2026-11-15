"""aiotieba 封装层：把异步客户端桥接到同步的 HTTP 线程里。"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Callable
from urllib.parse import urlencode

BAWU_TYPE_LABEL = {
    "ADMIN": "大吧主",
    "MANAGER": "小吧主",
    "VIDEO_EDITOR": "视频小编",
    "IMAGE_EDITOR": "图片小编",
    "JOURNAL_EDITOR": "吧刊小编",
    "JOURNAL_CHIEF_EDITOR": "吧刊主编",
    "VOICE_EDITOR": "语音小编",
    "MANAGER_ASSIST": "吧务",
}

GENDER_LABEL = {"MALE": "男", "FEMALE": "女"}

# 网页端 tbs 接口，返回 {"tbs": "...", "is_login": 1}
WEB_TBS_URL = "https://tieba.baidu.com/dc/common/tbs"


class ServiceError(Exception):
    """可以直接展示给用户的业务错误。"""


def enum_name(value: Any) -> str:
    if value is None:
        return ""
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    return str(value)


def parse_target(query: Any) -> str | int:
    """把用户输入规整成 aiotieba 认识的 id_（UID 或用户名/portrait）。"""
    text = str(query or "").strip()
    if not text:
        raise ServiceError("请输入用户名或 UID")
    if len(text) > 100:
        raise ServiceError("输入内容过长，请检查是否粘贴了多余内容")
    if text.isdigit():
        return int(text)
    return text


def avatar_url(portrait: str) -> str:
    if not portrait:
        return ""
    if portrait.startswith("http"):
        return portrait
    return f"https://himg.bdimg.com/sys/portrait/item/{portrait}"


class Runner:
    """独立线程跑一个 asyncio 事件循环，供同步代码投递协程。"""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, name="tieba-loop", daemon=True
        )
        self._started = False

    def start(self) -> None:
        if not self._started:
            self._started = True
            self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def call(self, coro, timeout: float = 90):
        self.start()
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout)


class TiebaService:
    def __init__(self, runner: Runner, get_config: Callable[[], dict[str, Any]]) -> None:
        self._runner = runner
        self._get_config = get_config
        self._client: Any = None
        self._key: tuple[str, str] | None = None
        self._forum_cache: dict[tuple[str, int], tuple[int, str]] = {}

    # ---------- 客户端生命周期 ----------

    async def _client_or_raise(self):
        cfg = self._get_config()
        bduss = (cfg.get("BDUSS") or "").strip()
        if not bduss:
            raise ServiceError("还没配置 BDUSS，请点右上角「设置」填入贴吧登录凭证")
        key = (bduss, (cfg.get("STOKEN") or "").strip())

        if self._client is not None and self._key != key:
            await self._close()
        if self._client is None:
            try:
                import aiotieba
            except ImportError as exc:  # pragma: no cover
                raise ServiceError(
                    "未安装 aiotieba，请先执行：pip install -r requirements.txt"
                ) from exc
            client = aiotieba.Client(BDUSS=key[0], STOKEN=key[1])
            await client.__aenter__()
            self._client = client
            self._key = key
        return self._client

    async def _close(self) -> None:
        client, self._client, self._key = self._client, None, None
        if client is not None:
            try:
                await client.__aexit__(None, None, None)
            except Exception:
                pass

    async def _reset_and_raise(self, exc: Exception):
        await self._close()
        raise ServiceError(f"贴吧请求失败：{exc or exc.__class__.__name__}") from exc

    # ---------- 贴吧解析 ----------

    async def _resolve_forum(self, client, fname: str) -> tuple[int, str]:
        """把吧名解析成 (fid, 展示名)。

        贴吧里「QQ飞车吧」和「QQ飞车」是两个不同的吧，前者可能只是个同名空吧，
        必须选对 fid 才不会被判成「无封禁权限」。优先选当前账号有吧务权限的那个。
        """
        key = (fname or "").strip()
        if not key:
            raise ServiceError("还没有设置目标贴吧")

        me = await client.get_self_info()
        uid = int(getattr(me, "user_id", 0) or 0)

        cache_key = (key, uid)
        if cache_key in self._forum_cache:
            return self._forum_cache[cache_key]

        candidates = [key]
        if key.endswith("吧") and len(key) > 2:
            candidates.append(key[:-1])
        elif not key.endswith("吧"):
            candidates.append(f"{key}吧")

        found: list[dict[str, Any]] = []
        seen: set[int] = set()
        for cand in candidates:
            try:
                forum = await client.get_forum(cand)
            except Exception:
                continue
            fid = int(getattr(forum, "fid", 0) or 0)
            if not fid or fid in seen:
                continue
            seen.add(fid)
            found.append(
                {
                    "fid": fid,
                    "name": cand,
                    "threads": int(getattr(forum, "thread_num", 0) or 0),
                }
            )

        if not found:
            raise ServiceError(f"没能找到「{key}」对应的贴吧")

        chosen = found[0]
        if len(found) > 1:
            if uid:
                for item in found:
                    try:
                        bawu = await client.get_bawu_info(item["fid"])
                    except Exception:
                        continue
                    if getattr(bawu, "err", None) is not None:
                        continue
                    if any(
                        int(getattr(u, "user_id", 0) or 0) == uid
                        for u in (getattr(bawu, "all", []) or [])
                    ):
                        chosen = item
                        break
                else:
                    chosen = max(found, key=lambda x: x["threads"])
            else:
                chosen = max(found, key=lambda x: x["threads"])

        result = (chosen["fid"], chosen["name"])
        self._forum_cache[cache_key] = result
        return result

    # ---------- 用户解析 ----------

    async def _resolve_user(self, client, id_: str | int) -> dict[str, Any]:
        user = await client.get_user_info(id_)
        user_id = int(getattr(user, "user_id", 0) or 0)
        if not user_id:
            err = getattr(user, "err", None)
            hint = f"（{err}）" if err else ""
            raise ServiceError(f"没找到这个用户：{id_}{hint}")
        portrait = getattr(user, "portrait", "") or ""
        avatar = avatar_url(portrait)
        nick = getattr(user, "show_name", "") or getattr(user, "nick_name_new", "") or ""
        return {
            "user_id": user_id,
            "user_name": getattr(user, "user_name", "") or "",
            "nick_name": nick,
            "portrait": portrait,
            "avatar": avatar,
            "gender": GENDER_LABEL.get(enum_name(getattr(user, "gender", None)), ""),
            "is_vip": bool(getattr(user, "is_vip", False)),
            "sign": (getattr(user, "sign", "") or "").strip(),
        }

    async def _resolve_full(self, query: Any, fname: str) -> dict[str, Any]:
        client = await self._client_or_raise()
        fid, forum_name = await self._resolve_forum(client, fname)
        id_ = parse_target(query)
        info = await self._resolve_user(client, id_)
        info["fid"] = fid

        try:
            forum = await client.get_user_forum_info(fid, info["user_id"])
            if forum is not None and getattr(forum, "err", None) is None:
                small = getattr(forum, "small_avatar", "") or ""
                info.update(
                    {
                        "forum_level": int(getattr(forum, "level", 0) or 0),
                        "forum_level_name": getattr(forum, "level_name", "") or "",
                        "is_follow": bool(getattr(forum, "is_follow", False)),
                        "bawu_type": enum_name(getattr(forum, "bawu_type", None)),
                        "follow_days": int(getattr(forum, "follow_days", 0) or 0),
                        "thread_num": int(getattr(forum, "thread_num", 0) or 0),
                        "day_post_num": int(getattr(forum, "day_post_num", 0) or 0),
                        "sign_days": int(getattr(forum, "sign_days", 0) or 0),
                    }
                )
                if small:
                    info["avatar"] = small if small.startswith("http") else f"https://{small.lstrip('/')}"
        except ServiceError:
            raise
        except Exception as exc:  # 吧内信息拿不到不影响主流程
            info["forum_error"] = str(exc) or exc.__class__.__name__

        info["bawu_label"] = BAWU_TYPE_LABEL.get(info.get("bawu_type", ""), "")
        info["fname"] = forum_name
        info["requested_fname"] = fname
        return info

    # ---------- 对外同步接口 ----------

    def close(self) -> None:
        """退出时优雅关闭连接。"""
        try:
            self._runner.call(self._close(), timeout=5)
        except Exception:
            pass

    def status(self) -> dict[str, Any]:
        async def _status():
            cfg = self._get_config()
            out: dict[str, Any] = {"login": False, "self": None, "error": ""}
            if not (cfg.get("BDUSS") or "").strip():
                out["error"] = "未配置 BDUSS"
                return out
            try:
                client = await self._client_or_raise()
                me = await client.get_self_info()
                if not int(getattr(me, "user_id", 0) or 0):
                    out["error"] = "BDUSS 已失效，请重新获取"
                    return out
                out["login"] = True
                out["self"] = {
                    "user_id": me.user_id,
                    "name": getattr(me, "show_name", "") or getattr(me, "nick_name_new", ""),
                    "user_name": getattr(me, "user_name", "") or "",
                    "avatar": avatar_url(getattr(me, "portrait", "") or ""),
                    "tieba_uid": int(getattr(me, "tieba_uid", 0) or 0),
                }
            except ServiceError as exc:
                out["error"] = str(exc)
            except Exception as exc:
                out["error"] = f"登录校验失败：{exc or exc.__class__.__name__}"
            return out

        return self._runner.call(_status(), timeout=45)

    def resolve(self, query: Any, fname: str) -> dict[str, Any]:
        async def _do():
            try:
                return await self._resolve_full(query, fname)
            except ServiceError:
                raise
            except Exception as exc:
                await self._reset_and_raise(exc)

        return self._runner.call(_do())

    # ---------- 封禁 / 解封 ----------

    async def _fetch_web_tbs(self, bduss: str) -> str:
        """吧务接口有时只认网页端 tbs，而 aiotieba 用的是 APP 端 tbs，两份不通用。"""
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(WEB_TBS_URL, cookies={"BDUSS": bduss}) as resp:
                data = await resp.json(content_type=None)
        if not isinstance(data, dict):
            raise ServiceError("获取网页端凭证失败")
        if not data.get("is_login"):
            raise ServiceError("BDUSS 在网页端未登录，可能已经过期")
        tbs = data.get("tbs") or ""
        if not tbs:
            raise ServiceError("获取网页端 tbs 失败")
        return tbs

    async def _call_with_tbs(self, client, tbs: str, factory):
        """临时把客户端 tbs 换成指定的那份，调用结束后还原。"""
        account = client.account
        old = account.tbs
        account.tbs = tbs
        try:
            return await factory()
        finally:
            account.tbs = old

    async def _ban_once(self, client, fid: int, target, day, reason, use_web_tbs: bool):
        if use_web_tbs:
            tbs = await self._fetch_web_tbs(client.account.BDUSS)
            res = await self._call_with_tbs(
                client, tbs, lambda: client.block(fid, target, day=day, reason=reason)
            )
        else:
            res = await client.block(fid, target, day=day, reason=reason)
        return bool(res), getattr(res, "err", None)

    async def _unban_once(self, client, fid: int, user_id, use_web_tbs: bool):
        if use_web_tbs:
            tbs = await self._fetch_web_tbs(client.account.BDUSS)
            res = await self._call_with_tbs(
                client, tbs, lambda: client.unblock(fid, user_id)
            )
        else:
            res = await client.unblock(fid, user_id)
        return bool(res), getattr(res, "err", None)

    async def _ban_pmc(self, client, info, day, reason):
        """网页后台封禁（/pmc/blockid）：需要目标在本吧的一条帖子作为定位。"""
        import aiohttp

        user_name = (info.get("user_name") or "").strip()
        if not user_name:
            raise ServiceError("该用户没有设置用户名，网页后台封禁接口用不了")

        fid = int(info.get("fid") or 0)
        if not fid:
            raise ServiceError("没能取到目标贴吧的 fid")

        posts = await client.get_user_posts(info["user_id"], 1, rn=30)
        same_forum, others = [], []
        for post in list(posts or []):
            pid = int(getattr(post, "pid", 0) or 0)
            if not pid:
                continue
            if int(getattr(post, "fid", 0) or 0) == fid:
                same_forum.append(pid)
            else:
                others.append(pid)
        candidates = same_forum or others
        if not candidates:
            raise ServiceError("该用户在本吧没有可定位的帖子，网页后台封禁接口用不了")

        tbs = await self._fetch_web_tbs(client.account.BDUSS)
        query = urlencode(
            [
                ("day", day),
                ("fid", fid),
                ("tbs", tbs),
                ("ie", "gbk"),
                ("user_name[]", user_name),
                ("pid[]", candidates[0]),
                ("reason", reason or ""),
            ],
            safe="[]",
        )
        url = f"https://tieba.baidu.com/pmc/blockid?{query}"
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                url, cookies={"BDUSS": client.account.BDUSS}
            ) as resp:
                data = await resp.json(content_type=None)
        if not isinstance(data, dict):
            return False, "网页后台返回了无法解析的内容"
        errno = int(data.get("errno") or 0)
        if errno == 0:
            return True, None
        return False, f"({errno}, {data.get('errmsg') or '未知错误'})"

    @staticmethod
    def _check_day(day: Any) -> int:
        try:
            value = int(day)
        except (TypeError, ValueError):
            raise ServiceError("封禁天数必须是数字")
        if not 1 <= value <= 9999:
            raise ServiceError("封禁天数需在 1 - 9999 之间")
        return value

    async def _try_ban(
        self,
        client,
        fid: int,
        target: Any,
        day: int,
        reason: str,
        info: dict | None = None,
    ) -> tuple[bool, Any, str, list[dict]]:
        """依次尝试三条封禁通道，返回（是否成功、最后一次错误、命中通道、失败记录）。"""
        attempts: list[dict] = []
        route = "客户端凭证"
        ok, err = await self._ban_once(client, fid, target, day, reason, False)
        if not ok:
            attempts.append({"route": "客户端凭证", "error": self._err_text(err)})
            # 退一步：换用网页端 tbs 再试一次，很多「无封禁权限」都出在这里
            try:
                ok, err = await self._ban_once(client, fid, target, day, reason, True)
                route = "网页凭证"
                if not ok:
                    attempts.append({"route": "网页凭证", "error": self._err_text(err)})
            except ServiceError as exc:
                attempts.append({"route": "网页凭证", "error": str(exc)})

        if not ok and info is not None:
            # 最后一条路：网页吧务后台（需要目标在本吧的一条帖子做定位）
            try:
                ok, err = await self._ban_pmc(client, info, day, reason)
                route = "网页后台"
                if not ok:
                    attempts.append({"route": "网页后台", "error": self._err_text(err)})
            except ServiceError as exc:
                attempts.append({"route": "网页后台", "error": str(exc)})
            except Exception as exc:
                attempts.append({"route": "网页后台", "error": str(exc) or "调用失败"})
        return ok, err, route, attempts

    async def _try_unban(
        self, client, fid: int, user_id: int
    ) -> tuple[bool, Any, str, list[dict]]:
        attempts: list[dict] = []
        # 解封走的是网页接口，先用网页端 tbs 更稳
        route = "网页凭证"
        try:
            ok, err = await self._unban_once(client, fid, user_id, True)
        except ServiceError as exc:
            attempts.append({"route": "网页凭证", "error": str(exc)})
            ok, err = False, None
        if not ok:
            if err is not None or not attempts:
                attempts.append({"route": "网页凭证", "error": self._err_text(err)})
            ok, err = await self._unban_once(client, fid, user_id, False)
            route = "客户端凭证"
            if not ok:
                attempts.append({"route": "客户端凭证", "error": self._err_text(err)})
        return ok, err, route, attempts

    def ban(
        self, query: Any, day: int, reason: str, fname: str
    ) -> dict[str, Any]:
        day = self._check_day(day)
        reason = (reason or "").strip()

        async def _do():
            try:
                client = await self._client_or_raise()
                info = await self._resolve_full(query, fname)
                # 传 portrait 可以省掉一次内部查询
                target = info.get("portrait") or info["user_id"]

                ok, err, route, attempts = await self._try_ban(
                    client, int(info["fid"]), target, day, reason, info
                )
                message = (
                    "封禁成功"
                    if ok
                    else self._explain(err, day, info.get("fname", fname), attempts)
                )
                return {
                    "ok": ok,
                    "message": message,
                    "route": route if ok else "",
                    "attempts": attempts,
                    "user": info,
                }
            except ServiceError:
                raise
            except Exception as exc:
                await self._reset_and_raise(exc)

        return self._runner.call(_do())

    def unban(self, query: Any, fname: str) -> dict[str, Any]:
        async def _do():
            try:
                client = await self._client_or_raise()
                info = await self._resolve_full(query, fname)
                ok, err, route, attempts = await self._try_unban(
                    client, int(info["fid"]), int(info["user_id"])
                )
                message = (
                    "解封成功"
                    if ok
                    else f"解封失败：{self._err_text(err) or '该用户可能未被封禁，或当前账号没有吧务权限'}"
                )
                return {
                    "ok": ok,
                    "message": message,
                    "route": route if ok else "",
                    "attempts": attempts,
                    "user": info,
                }
            except ServiceError:
                raise
            except Exception as exc:
                await self._reset_and_raise(exc)

        return self._runner.call(_do())

    # ---------- 批量操作（封禁中列表里的多选） ----------

    @staticmethod
    def _batch_items(users: Any) -> list[dict]:
        items = [
            u
            for u in (users or [])
            if isinstance(u, dict) and int(u.get("user_id") or 0) > 0
        ]
        if not items:
            raise ServiceError("请先勾选要处理的用户")
        if len(items) > 50:
            raise ServiceError("一次最多处理 50 个用户，请分批操作")
        return items

    def ban_many(
        self, users: Any, day: int, reason: str, fname: str
    ) -> dict[str, Any]:
        """对勾选的用户批量封禁：吧名只解析一次，逐个提交，互不中断。"""
        day = self._check_day(day)
        reason = (reason or "").strip()
        items = self._batch_items(users)

        async def _do():
            try:
                client = await self._client_or_raise()
                fid, resolved = await self._resolve_forum(client, fname)
                results = []
                for u in items:
                    uid = int(u.get("user_id") or 0)
                    name = (u.get("nick_name") or u.get("user_name") or str(uid)).strip()
                    try:
                        ok, err, route, attempts = await self._try_ban(
                            client, fid, u.get("portrait") or uid, day, reason, None
                        )
                        message = (
                            "封禁成功"
                            if ok
                            else self._explain(err, day, resolved, attempts)
                        )
                    except ServiceError as exc:
                        ok, route, message = False, "", str(exc)
                    except Exception as exc:
                        ok, route, message = False, "", f"封禁失败：{exc}"
                    results.append(
                        {
                            "user_id": uid,
                            "name": name,
                            "user_name": u.get("user_name") or "",
                            "ok": ok,
                            "route": route if ok else "",
                            "message": message,
                        }
                    )
                return {"fid": fid, "fname": resolved, "results": results}
            except ServiceError:
                raise
            except Exception as exc:
                await self._reset_and_raise(exc)

        return self._runner.call(_do(), timeout=40 + 15 * len(items))

    def unban_many(self, users: Any, fname: str) -> dict[str, Any]:
        items = self._batch_items(users)

        async def _do():
            try:
                client = await self._client_or_raise()
                fid, resolved = await self._resolve_forum(client, fname)
                results = []
                for u in items:
                    uid = int(u.get("user_id") or 0)
                    name = (u.get("nick_name") or u.get("user_name") or str(uid)).strip()
                    try:
                        ok, err, route, _ = await self._try_unban(client, fid, uid)
                        message = (
                            "解封成功"
                            if ok
                            else f"解封失败：{self._err_text(err) or '该用户可能未被封禁'}"
                        )
                    except ServiceError as exc:
                        ok, route, message = False, "", str(exc)
                    except Exception as exc:
                        ok, route, message = False, "", f"解封失败：{exc}"
                    results.append(
                        {
                            "user_id": uid,
                            "name": name,
                            "user_name": u.get("user_name") or "",
                            "ok": ok,
                            "route": route if ok else "",
                            "message": message,
                        }
                    )
                return {"fid": fid, "fname": resolved, "results": results}
            except ServiceError:
                raise
            except Exception as exc:
                await self._reset_and_raise(exc)

        return self._runner.call(_do(), timeout=40 + 12 * len(items))

    def diagnose(self, fname: str) -> dict[str, Any]:
        """排查用：当前账号在本吧到底是什么身份、凭证是否可用。"""

        async def _do():
            client = await self._client_or_raise()
            out: dict[str, Any] = {"fname": fname}
            me = await client.get_self_info()
            uid = int(getattr(me, "user_id", 0) or 0)
            out["logged_in"] = uid > 0
            out["self"] = {
                "user_id": uid,
                "name": getattr(me, "show_name", "") or getattr(me, "nick_name_new", ""),
                "user_name": getattr(me, "user_name", "") or "",
            }
            if not uid:
                err = getattr(me, "err", None)
                out["login_error"] = str(err or "BDUSS 无效或已过期")
                out["role"] = "未登录"
                out["fid"] = 0
                try:
                    await self._fetch_web_tbs(client.account.BDUSS)
                    out["web_tbs"] = "可用"
                except Exception as exc:
                    out["web_tbs"] = f"不可用（{exc}）"
                out["client_tbs"] = "未获取"
                return out
            try:
                fid, resolved_name = await self._resolve_forum(client, fname)
                out["fid"] = fid
                out["resolved_fname"] = resolved_name
                out["fid_corrected"] = resolved_name != fname
            except ServiceError as exc:
                out["fid"] = 0
                out["resolved_fname"] = fname
                out["fid_error"] = str(exc)

            role = "不在吧务团队中"
            fid = int(out.get("fid") or 0)
            if fid:
                try:
                    bawu = await client.get_bawu_info(fid)
                    if getattr(bawu, "err", None) is not None:
                        out["bawu_error"] = str(bawu.err)
                    groups = (
                        ("大吧主", getattr(bawu, "admin", [])),
                        ("职业吧主", getattr(bawu, "profess_admin", [])),
                        ("第四吧主", getattr(bawu, "fourth_admin", [])),
                        ("小吧主", getattr(bawu, "manager", [])),
                    )
                    for label, members in groups:
                        if any(
                            int(getattr(u, "user_id", 0) or 0) == uid for u in members
                        ):
                            role = label
                            break
                    out["bawu_total"] = len(getattr(bawu, "all", []) or [])
                except Exception as exc:
                    out["bawu_error"] = str(exc)
            out["role"] = role

            out["client_tbs"] = "已获取" if client.account.tbs else "未获取"
            try:
                await self._fetch_web_tbs(client.account.BDUSS)
                out["web_tbs"] = "可用"
            except ServiceError as exc:
                out["web_tbs"] = f"不可用（{exc}）"
            except Exception as exc:
                out["web_tbs"] = f"不可用（{exc}）"
            return out

        return self._runner.call(_do(), timeout=60)

    def blocks(
        self,
        fname: str,
        keyword: str = "",
        page: int = 1,
        all_pages: bool = False,
    ) -> dict[str, Any]:
        """读取吧务后台的封禁列表。all_pages=True 时把剩余页一次性拉完（供「全选」使用）。"""

        async def _do():
            try:
                client = await self._client_or_raise()
                fid, _ = await self._resolve_forum(client, fname)
                payload = self._blocks_payload(
                    await client.get_blocks(fid, keyword, 1 if all_pages else page)
                )
                if not all_pages:
                    return payload

                items = list(payload["items"])
                total_pages = int(payload["page"].get("total") or 1)
                start = int(payload["page"].get("current") or 1)
                for p in range(start + 1, total_pages + 1):
                    nxt = self._blocks_payload(
                        await client.get_blocks(fid, keyword, p)
                    )
                    items.extend(nxt["items"])
                # 同一用户可能翻页期间重复返回，按 uid 去重并保序
                seen: set[int] = set()
                uniq = []
                for it in items:
                    uid = int(it.get("user_id") or 0)
                    if not uid or uid in seen:
                        continue
                    seen.add(uid)
                    uniq.append(it)
                payload["items"] = uniq
                payload["all"] = True
                payload["page"]["current"] = max(1, page)
                return payload
            except ServiceError:
                raise
            except Exception as exc:
                await self._reset_and_raise(exc)

        return self._runner.call(_do(), timeout=180 if all_pages else 90)

    @staticmethod
    def _blocks_payload(res: Any) -> dict[str, Any]:
        if res is None:
            return {"items": [], "page": {}}
        if getattr(res, "err", None) is not None and not len(res):
            raise ServiceError(f"获取封禁列表失败：{res.err}")

        items = []
        for item in list(res):
            block_time = getattr(item, "block_time", None)
            items.append(
                {
                    "user_id": int(getattr(item, "user_id", 0) or 0),
                    "user_name": getattr(item, "user_name", "") or "",
                    "nick_name": getattr(item, "nick_name_new", "") or "",
                    "day": int(getattr(item, "day", 0) or 0),
                    "block_time": block_time.strftime("%Y-%m-%d %H:%M")
                    if hasattr(block_time, "strftime")
                    else "",
                }
            )
        page_info = getattr(res, "page", None)
        return {
            "items": items,
            "page": {
                "current": int(getattr(page_info, "current_page", 0) or 0),
                "total": int(getattr(page_info, "total_page", 0) or 0),
                "count": int(getattr(page_info, "total_count", 0) or 0),
            },
        }

    # ---------- 解封申诉 ----------

    # 贴吧接口对单次处理的申诉条数有上限，超了会被直接拒绝
    MAX_APPEAL_BATCH = 30

    @staticmethod
    def _fmt_ts(sec: Any) -> str:
        try:
            value = int(sec or 0)
        except (TypeError, ValueError):
            return ""
        if value <= 0:
            return ""
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(value))

    @classmethod
    def _appeals_payload(cls, res: Any) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for a in list(res or []):
            portrait = getattr(a, "portrait", "") or ""
            appeal_id = int(getattr(a, "appeal_id", 0) or 0)
            if not appeal_id:
                continue
            items.append(
                {
                    "appeal_id": appeal_id,
                    "user_id": int(getattr(a, "user_id", 0) or 0),
                    "user_name": getattr(a, "user_name", "") or "",
                    "nick_name": getattr(a, "nick_name", "") or "",
                    "avatar": avatar_url(portrait),
                    "appeal_reason": (getattr(a, "appeal_reason", "") or "").strip(),
                    "appeal_time": cls._fmt_ts(getattr(a, "appeal_time", 0)),
                    "punish_reason": (getattr(a, "punish_reason", "") or "").strip(),
                    "punish_day": int(getattr(a, "punish_day", 0) or 0),
                    "punish_time": cls._fmt_ts(getattr(a, "punish_time", 0)),
                    "op_name": getattr(a, "op_name", "") or "",
                }
            )
        return {"items": items, "has_more": bool(getattr(res, "has_more", False))}

    async def _appeals_page(self, client, fid: int, pn: int, keyword: str, use_web_tbs: bool):
        if use_web_tbs:
            tbs = await self._fetch_web_tbs(client.account.BDUSS)
            return await self._call_with_tbs(
                client,
                tbs,
                lambda: client.get_unblock_appeals(fid, pn, rn=10, query=keyword),
            )
        return await client.get_unblock_appeals(fid, pn, rn=10, query=keyword)

    def appeals(self, fname: str, keyword: str = "") -> dict[str, Any]:
        """读取吧务后台的解封申诉。has_more 时继续翻页，一次取完给前端。"""

        async def _do():
            try:
                client = await self._client_or_raise()
                fid, resolved = await self._resolve_forum(client, fname)
                keyword_text = (keyword or "").strip()

                items: list[dict[str, Any]] = []
                seen: set[int] = set()
                ok_mode: bool | None = None
                truncated = False
                page = 1
                while page <= 10:
                    res = None
                    errors: list[str] = []
                    modes = [ok_mode] if ok_mode is not None else [False, True]
                    for use_web in modes:
                        try:
                            res = await self._appeals_page(
                                client, fid, page, keyword_text, bool(use_web)
                            )
                            ok_mode = bool(use_web)
                            break
                        except Exception as exc:
                            errors.append(
                                f"{'网页' if use_web else 'APP'}端凭证：{exc}"
                            )
                    if res is None:
                        raise ServiceError(
                            "读取申诉列表失败："
                            + "；".join(errors)
                            + "。申诉处理需要吧务后台的「解封申诉」权限，"
                            "小吧主请先让吧主分配该权限。"
                        )

                    payload = self._appeals_payload(res)
                    rows = payload["items"]
                    for it in rows:
                        if it["appeal_id"] in seen:
                            continue
                        seen.add(it["appeal_id"])
                        items.append(it)
                    if not payload["has_more"] or not rows:
                        break
                    if page >= 10:
                        # 待处理申诉多到 100 条还没拉完，剩下的不静默丢弃，告诉前端
                        truncated = True
                        break
                    page += 1

                return {
                    "fid": fid,
                    "fname": resolved,
                    "items": items,
                    "count": len(items),
                    "truncated": truncated,
                }
            except ServiceError:
                raise
            except Exception as exc:
                await self._reset_and_raise(exc)

        return self._runner.call(_do(), timeout=120)

    async def _handle_appeals_once(
        self, client, fid: int, ids: list[int], refuse: bool, use_web_tbs: bool
    ) -> tuple[bool, Any]:
        """返回（是否成功, 错误）。这条接口不包异常，得自己兜住。"""
        try:
            if use_web_tbs:
                tbs = await self._fetch_web_tbs(client.account.BDUSS)
                res = await self._call_with_tbs(
                    client,
                    tbs,
                    lambda: client.handle_unblock_appeals(fid, ids, refuse=refuse),
                )
            else:
                res = await client.handle_unblock_appeals(fid, ids, refuse=refuse)
        except Exception as exc:
            return False, exc
        return bool(res), getattr(res, "err", None)

    async def _handle_appeals_try(
        self, client, fid: int, ids: list[int], refuse: bool
    ) -> tuple[bool, Any, str]:
        ok, err = await self._handle_appeals_once(client, fid, ids, refuse, False)
        if ok:
            return True, None, "APP 端凭证"

        text = self._err_text(err)
        # 权限不够、或者服务端已经给出业务错误（比如申诉不存在），换凭证也没用
        if self._is_perm_error(text) or not self._should_retry_with_web_tbs(text):
            return False, err, ""

        ok, err2 = await self._handle_appeals_once(client, fid, ids, refuse, True)
        if ok:
            return True, None, "网页端凭证"
        # 换凭证这一步自己就失败了（通常是网页端没登录），真实原因还是第一次那个
        if isinstance(err2, ServiceError):
            return False, err, ""
        return False, err2 or err, ""

    @staticmethod
    def _should_retry_with_web_tbs(text: str) -> bool:
        """只有可能是凭证/签名问题时才值得换网页端 tbs 再试一次。"""
        low = (text or "").lower()
        return any(k in low for k in ("tbs", "凭证", "签名", "登录", "token", "验证"))

    @staticmethod
    def _is_perm_error(text: str) -> bool:
        return any(k in text for k in ("权限", "3150003", "3020001", "无权"))

    @staticmethod
    def _appeal_hint(text: str) -> str:
        if not text:
            return ""
        if TiebaService._is_perm_error(text):
            return (
                "申诉处理需要吧务后台「解封申诉」这一项权限：大吧主默认具备，"
                "小吧主需要先由吧主在吧务后台分配该权限后才能操作。"
            )
        if "403008" in text or "解封失败" in text:
            return (
                "这条申诉可能已经被其他吧务处理过，或该用户已不在封禁名单里。"
                "点「刷新」重新读取申诉列表后再试。"
            )
        if "过期" in text:
            return "登录凭证可能已失效，请重新从浏览器复制 BDUSS 并在「设置」里保存。"
        return ""

    def handle_appeals(
        self, items: Any, refuse: bool, fname: str
    ) -> dict[str, Any]:
        """批量通过（解封）或驳回申诉。整批失败时逐条重试，定位具体失败的人。"""
        rows = [
            it
            for it in (items or [])
            if isinstance(it, dict) and int(it.get("appeal_id") or 0) > 0
        ]
        if not rows:
            raise ServiceError("请先勾选要处理的申诉")
        if len(rows) > self.MAX_APPEAL_BATCH:
            raise ServiceError(
                f"一次最多处理 {self.MAX_APPEAL_BATCH} 条申诉，请分批操作"
            )
        ids = list(dict.fromkeys(int(it["appeal_id"]) for it in rows))
        refuse = bool(refuse)

        def _row(it: dict, ok: bool, message: str, route: str = "") -> dict[str, Any]:
            uid = int(it.get("user_id") or 0)
            return {
                "appeal_id": int(it["appeal_id"]),
                "user_id": uid,
                "name": (it.get("nick_name") or it.get("user_name") or str(uid)).strip(),
                "user_name": it.get("user_name") or "",
                "punish_day": int(it.get("punish_day") or 0),
                "appeal_reason": (it.get("appeal_reason") or "").strip(),
                "ok": ok,
                "route": route,
                "message": message,
            }

        async def _do():
            try:
                client = await self._client_or_raise()
                fid, resolved = await self._resolve_forum(client, fname)

                ok, err, route = await self._handle_appeals_try(
                    client, fid, ids, refuse
                )
                hint = ""
                if ok:
                    results = [
                        _row(it, True, "已解封" if not refuse else "已驳回", route)
                        for it in rows
                    ]
                else:
                    text = self._err_text(err)
                    hint = self._appeal_hint(text)
                    if self._is_perm_error(text) or len(ids) == 1:
                        results = [
                            _row(it, False, f"处理失败：{text}") for it in rows
                        ]
                    else:
                        # 整批被拒且不是权限问题：逐条提交，把失败的人挑出来
                        results = []
                        for it in rows:
                            one_ok, one_err, one_route = await self._handle_appeals_try(
                                client, fid, [int(it["appeal_id"])], refuse
                            )
                            results.append(
                                _row(
                                    it,
                                    one_ok,
                                    "已解封" if (one_ok and not refuse) else (
                                        "已驳回" if one_ok else f"处理失败：{self._err_text(one_err)}"
                                    ),
                                    one_route,
                                )
                            )

                success = sum(1 for r in results if r["ok"])
                return {
                    "fid": fid,
                    "fname": resolved,
                    "refuse": refuse,
                    "results": results,
                    "success_count": success,
                    "fail_count": len(results) - success,
                    "hint": hint,
                }
            except ServiceError:
                raise
            except Exception as exc:
                await self._reset_and_raise(exc)

        return self._runner.call(_do(), timeout=60 + 20 * len(rows))

    # ---------- 辅助 ----------

    @staticmethod
    def _err_text(err: Any) -> str:
        return str(err or "").strip() or "未知原因"

    @classmethod
    def _explain(cls, err: Any, day: int, fname: str, attempts: list[dict] | None = None) -> str:
        text = cls._err_text(err)
        tried = len(attempts or [])
        suffix = f"（已尝试 {tried} 种方式，点右上角「诊断」看详情）" if tried > 1 else ""

        if "3150003" in text or "无封禁权限" in text:
            return (
                f"封禁失败：{text}。权限校验没通过{suffix}——"
                "点右上角「诊断」核对「目标贴吧」的 fid 是否为你管理的那个吧"
                "（贴吧里带「吧」字的同名空吧很常见，比如「QQ飞车吧」和「QQ飞车」是两个吧），"
                "再确认这个 BDUSS 对应的账号确实在本吧吧务团队里。"
            )
        if text:
            return f"封禁失败：{text}{suffix}"
        if day not in (1, 3, 10):
            return (
                f"封禁失败：{day} 天属于自定义封禁，需要吧主（含循环封禁权限）才能使用；"
                "小吧主请改用 1 / 3 / 10 天。"
            )
        return f"封禁失败：请确认你在「{fname}」有吧务管理权限，且目标不是吧务成员{suffix}"
