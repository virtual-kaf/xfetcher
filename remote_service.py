import hmac
import ipaddress
import os
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from aiohttp import web
from nonebot import logger

POLL_PATH = "/api/xfetch/poll"
Fetcher = Callable[[Sequence[str]], Awaitable[list[str]]]


class RemoteServiceConfigError(RuntimeError):
    """Raised when the standalone service configuration is unsafe or invalid."""


@dataclass(frozen=True)
class RemoteServiceSettings:
    enabled: bool
    host: str
    port: int
    token: str

    @classmethod
    def from_raw(
        cls,
        *,
        enabled: str,
        host: str,
        port: str,
        token: str,
    ) -> "RemoteServiceSettings":
        normalized_enabled = enabled.strip().casefold()
        if normalized_enabled in {"1", "true", "yes", "on"}:
            parsed_enabled = True
        elif normalized_enabled in {"0", "false", "no", "off"}:
            parsed_enabled = False
        else:
            raise RemoteServiceConfigError(
                "KABUBU_XFETCH_REMOTE_ENABLED must be true or false"
            )
        if not parsed_enabled:
            raise RemoteServiceConfigError(
                "xfetch remote service is disabled by configuration"
            )

        normalized_host = host.strip()
        try:
            ipaddress.ip_address(normalized_host)
        except ValueError as exc:
            raise RemoteServiceConfigError(
                "KABUBU_XFETCH_REMOTE_HOST must be a valid IP address"
            ) from exc

        try:
            parsed_port = int(port)
        except (TypeError, ValueError) as exc:
            raise RemoteServiceConfigError(
                "KABUBU_XFETCH_REMOTE_PORT must be an integer"
            ) from exc
        if not 1 <= parsed_port <= 65535:
            raise RemoteServiceConfigError(
                "KABUBU_XFETCH_REMOTE_PORT must be between 1 and 65535"
            )

        normalized_token = token.strip()
        if not normalized_token:
            raise RemoteServiceConfigError(
                "KABUBU_XFETCH_REMOTE_TOKEN is required when enabled"
            )

        return cls(
            enabled=True,
            host=normalized_host,
            port=parsed_port,
            token=normalized_token,
        )


SETTINGS_KEY = web.AppKey("xfetch_remote_settings", RemoteServiceSettings)
FETCHER_KEY = web.AppKey("xfetch_remote_fetcher", object)


def _project_root() -> Path:
    package_root = Path(__file__).resolve().parents[3]
    cwd = Path.cwd().resolve()
    if (cwd / ".env").is_file():
        return cwd
    return package_root


def load_remote_settings() -> RemoteServiceSettings:
    """Read process environment and the project's NoneBot dotenv files."""
    from nonebot.config import Config, Env

    root = _project_root()
    base_env = root / ".env"
    env_files: list[Path] = []
    if base_env.is_file():
        environment = Env(_env_file=base_env).environment
        env_files.append(base_env)
        environment_file = root / f".env.{environment}"
        if environment_file.is_file():
            env_files.append(environment_file)

    loaded = Config(_env_file=tuple(env_files), driver="~none")

    def read(name: str, default: str) -> str:
        process_value = os.getenv(name)
        if process_value is not None:
            return process_value
        return str(getattr(loaded, name.lower(), default))

    return RemoteServiceSettings.from_raw(
        enabled=read("KABUBU_XFETCH_REMOTE_ENABLED", "false"),
        host=read("KABUBU_XFETCH_REMOTE_HOST", "100.98.44.83"),
        port=read("KABUBU_XFETCH_REMOTE_PORT", "8765"),
        token=read("KABUBU_XFETCH_REMOTE_TOKEN", ""),
    )


def _json_envelope(
    *,
    status: int,
    ok: bool,
    urls: list[str] | None = None,
    source: str | None = None,
    error: str | None = None,
) -> web.Response:
    return web.json_response(
        {
            "ok": ok,
            "urls": urls or [],
            "source": source,
            "error": error,
        },
        status=status,
    )


def _has_valid_bearer(header: str | None, expected_token: str) -> bool:
    if not header:
        return False
    scheme, separator, supplied_token = header.partition(" ")
    return bool(
        separator
        and scheme.casefold() == "bearer"
        and supplied_token
        and hmac.compare_digest(supplied_token, expected_token)
    )


async def _poll(request: web.Request) -> web.Response:
    settings = request.app[SETTINGS_KEY]
    if not _has_valid_bearer(
        request.headers.get("Authorization"), settings.token
    ):
        response = _json_envelope(
            status=401,
            ok=False,
            error="unauthorized",
        )
        response.headers["WWW-Authenticate"] = "Bearer"
        return response

    try:
        body: Any = await request.json()
    except Exception:  # noqa: BLE001 - malformed body is always a 400
        return _json_envelope(
            status=400,
            ok=False,
            error="invalid_request",
        )
    if not isinstance(body, dict) or "members" not in body:
        return _json_envelope(
            status=400,
            ok=False,
            error="invalid_request",
        )

    from .clients.grok import GrokDiscoveryError
    from .remote_fetch import InvalidMembersError

    fetcher = cast(Fetcher, request.app[FETCHER_KEY])
    try:
        urls = await fetcher(body["members"])
    except InvalidMembersError:
        return _json_envelope(
            status=400,
            ok=False,
            error="invalid_request",
        )
    except GrokDiscoveryError as exc:
        logger.warning(f"[RemoteService] Grok poll failed: {exc.reason}")
        return _json_envelope(
            status=502,
            ok=False,
            error="grok_failed",
        )
    except Exception:  # noqa: BLE001 - keep internal details out of the response
        logger.exception("[RemoteService] Unexpected poll failure")
        return _json_envelope(
            status=500,
            ok=False,
            error="internal_error",
        )

    return _json_envelope(
        status=200,
        ok=True,
        urls=urls,
        source="grok",
    )


def create_app(
    settings: RemoteServiceSettings,
    *,
    fetcher: Fetcher | None = None,
) -> web.Application:
    if fetcher is None:
        from .remote_fetch import fetch_latest_urls

        fetcher = fetch_latest_urls

    app = web.Application(client_max_size=64 * 1024)
    app[SETTINGS_KEY] = settings
    app[FETCHER_KEY] = fetcher
    app.router.add_post(POLL_PATH, _poll)
    return app


def main() -> None:
    try:
        settings = load_remote_settings()
    except RemoteServiceConfigError as exc:
        logger.error(f"[RemoteService] Refusing to start: {exc}")
        raise SystemExit(2) from exc

    logger.info(
        f"[RemoteService] Listening on http://{settings.host}:{settings.port}"
    )
    web.run_app(
        create_app(settings),
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
