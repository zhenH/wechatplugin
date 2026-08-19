"""访问认证：单密码登录 + HMAC 签名 Cookie + 简单限流。

启用方式：.env 里设置 ACCESS_PASSWORD（非空即启用）；留空则完全开放（本地开发）。
公网部署建议：配 HTTPS + 强密码。
"""
import hashlib
import hmac
import time

from fastapi.responses import JSONResponse, RedirectResponse

from .config import settings

COOKIE_NAME = "dsh_access"
_SALT = b"dsh-access-v1"

# 登录限流：同 IP 失败 N 次锁 M 秒
_attempts: dict[str, list[float]] = {}
MAX_FAILS = 5
LOCK_SECONDS = 60


def enabled() -> bool:
    return bool(settings.access_password)


def _secret() -> bytes:
    base = settings.access_secret or settings.access_password or "dsh-local"
    return (base + "|" + _SALT.decode()).encode("utf-8")


def _sig(payload: str) -> str:
    return hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def make_token() -> str:
    exp = int(time.time()) + settings.session_days * 86400
    return f"{exp}.{_sig(str(exp))}"


def token_valid(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    exp_s, sig = token.rsplit(".", 1)
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if time.time() > exp:
        return False
    return hmac.compare_digest(sig, _sig(exp_s))


def verify_password(password: str) -> bool:
    return hmac.compare_digest(password.encode("utf-8"), settings.access_password.encode("utf-8"))


def is_locked(ip: str) -> bool:
    now = time.time()
    fails = [t for t in _attempts.get(ip, []) if now - t < LOCK_SECONDS]
    _attempts[ip] = fails
    return len(fails) >= MAX_FAILS


def record_fail(ip: str):
    _attempts.setdefault(ip, []).append(time.time())


def reset_fails(ip: str):
    _attempts.pop(ip, None)


def _parse_cookies(headers) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for name, value in headers:
        if name == b"cookie":
            for part in value.decode("latin-1").split(";"):
                if "=" in part:
                    k, _, v = part.strip().partition("=")
                    cookies[k] = v
    return cookies


_PUBLIC = ("/login.html", "/login.js", "/style.css", "/avatars/", "/favicon.ico")


class AuthMiddleware:
    """纯 ASGI 中间件：启用后拦截未登录请求。

    页面 → 重定向 /login.html；API → 401 JSON。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not enabled():
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        # 登录接口本身必须放行
        if path == "/api/login":
            await self.app(scope, receive, send)
            return
        valid = token_valid(_parse_cookies(scope.get("headers") or []).get(COOKIE_NAME))
        if valid:
            await self.app(scope, receive, send)
            return
        if path.startswith("/api/"):
            await JSONResponse({"detail": "未登录"}, status_code=401)(scope, receive, send)
        elif path.startswith(_PUBLIC):
            await self.app(scope, receive, send)
        else:
            await RedirectResponse("/login.html", status_code=303)(scope, receive, send)
