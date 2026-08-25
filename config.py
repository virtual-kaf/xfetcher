import os
import pathlib
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from nonebot import get_driver
except ImportError:  # Support isolated helpers with a minimal NoneBot stub.
    get_driver = None

# 时区
try:
    JST = ZoneInfo("Asia/Tokyo")
except ZoneInfoNotFoundError:
    JST = timezone(timedelta(hours=9))
CST = timezone(timedelta(hours=8))

def _get_config_str(name: str, default: str = "") -> str:
    """Read process environment first, then NoneBot's loaded dotenv config."""
    env_value = os.getenv(name)
    if env_value is not None:
        return env_value
    if get_driver is None:
        return default
    try:
        nonebot_config = get_driver().config
    except ValueError:
        return default
    value = getattr(nonebot_config, name.lower(), default)
    return default if value is None else str(value)

def _get_config_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """Read a bounded integer setting without making startup fragile."""
    raw = _get_config_str(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)



# Standalone remote fetch service. Keep raw strings here and validate them
# when the service starts so bad remote settings cannot break the QQ plugin.
XFETCH_REMOTE_ENABLED: str = _get_config_str(
    "KABUBU_XFETCH_REMOTE_ENABLED", "false"
).strip()
XFETCH_REMOTE_HOST: str = _get_config_str(
    "KABUBU_XFETCH_REMOTE_HOST", "100.98.44.83"
).strip()
XFETCH_REMOTE_PORT: str = _get_config_str(
    "KABUBU_XFETCH_REMOTE_PORT", "8765"
).strip()
XFETCH_REMOTE_TOKEN: str = _get_config_str(
    "KABUBU_XFETCH_REMOTE_TOKEN", ""
).strip()


# API
GROK_API_URL: str = _get_config_str(
    "KABUBU_GROK_API_URL", "http://127.0.0.1:8000/v1/chat/completions"
).strip()
_GROK_API_KEY_RAW: str = _get_config_str("KABUBU_GROK_API_KEY", "").strip()
GROK_API_KEY: str = (
    _GROK_API_KEY_RAW
    if _GROK_API_KEY_RAW.lower().startswith("bearer ")
    else f"Bearer {_GROK_API_KEY_RAW}"
    if _GROK_API_KEY_RAW
    else ""
)

TWITTERAPI_IO_API_KEY: str = _get_config_str(
    "KABUBU_TWITTERAPI_IO_API_KEY", ""
).strip()
TWITTERAPI_IO_API_BASE: str = _get_config_str(
    "KABUBU_TWITTERAPI_IO_API_BASE", "https://api.twitterapi.io"
).rstrip("/")

DEEPSEEK_API_KEY: str = _get_config_str(
    "KABUBU_DEEPSEEK_API_KEY", ""
).strip()
DEEPSEEK_BASE_URL: str = _get_config_str(
    "KABUBU_DEEPSEEK_BASE_URL", "https://api.deepseek.com"
).rstrip("/")
DEEPSEEK_API_URL: str = (
    DEEPSEEK_BASE_URL
    if DEEPSEEK_BASE_URL.endswith("/chat/completions")
    else f"{DEEPSEEK_BASE_URL}/chat/completions"
)
DEEPSEEK_MODEL: str = _get_config_str(
    "KABUBU_DEEPSEEK_MODEL",
    _get_config_str("KABUBU_CHAT_MODEL", "deepseek-v4-flash"),
)

FXTWITTER_API_BASE: str = "https://api.fxtwitter.com"

# 成员
CORE_MEMBERS: list[str] = [
    "virtual_kaf", "RIM_virtual", "harusaruhi", "isekaijoucho",
    "KOKO__virtual","kaf_info"
]

OPTIONAL_MEMBERS: list[str] = [
    "CIEL_VanillaSky", "sooda_oda", "KUUSOU_virtual", "kurogaki0311",
    "ASU_virtual", "BEMA_virtual", "garasumiya_gr", "orihime_gr",
    "kakyoin_gr", "hinageshi_gr", "mikoto_gr", "yunagi_gr", 
    "qurux2_flower", "GuiAnoDayo", "neuron_yz", "eumza1","huyuyasumi_"
]

# 路径：运行数据和静态资源都以插件目录为根，不依赖进程 CWD。
PLUGIN_DIR: pathlib.Path = pathlib.Path(__file__).resolve().parent
DATA_DIR: pathlib.Path = PLUGIN_DIR / "data"
CARD_DIR: pathlib.Path = PLUGIN_DIR / "data" / "cards"

# SnowLuma runs in Docker and reads outbound images through a shared bind
# mount.  NoneBot copies a rendered image to the host path, while the OneBot
# message references the corresponding path inside the container.
XFETCH_SHARED_IMAGE_HOST_DIR: pathlib.Path = pathlib.Path(
    _get_config_str(
        "KABUBU_XFETCH_SHARED_IMAGE_HOST_DIR",
        "/home/admin/snowluma/data/xfetch_cards",
    )
)
XFETCH_SHARED_IMAGE_CONTAINER_DIR: str = _get_config_str(
    "KABUBU_XFETCH_SHARED_IMAGE_CONTAINER_DIR",
    "/app/data/xfetch_cards",
).rstrip("/")
XFETCH_SHARED_IMAGE_TTL_SECONDS: int = _get_config_int(
    "KABUBU_XFETCH_SHARED_IMAGE_TTL_SECONDS",
    3600,
    minimum=60,
    maximum=86400,
)

# 运行参数
MAX_POST_AGE: timedelta = timedelta(hours=6)
REQUEST_TIMEOUT: float = 120.0
HISTORY_LIMIT: int = 10
GLOBAL_MEMBER_LIMIT: int = 30
IMAGE_PROXY: str = "http://127.0.0.1:58309"

# 2 GB 服务器保护。FxTwitter 会返回整条会话，Playwright 还会解码卡片图片；
# 这里限制两个最容易产生峰值的阶段，避免它们挤占同机 WebUI 的内存。
XFETCH_FETCH_CONCURRENCY: int = _get_config_int(
    "KABUBU_XFETCH_FETCH_CONCURRENCY", 4, minimum=1, maximum=8
)
XFETCH_RENDER_TIMEOUT: int = _get_config_int(
    "KABUBU_XFETCH_RENDER_TIMEOUT", 90, minimum=30, maximum=180
)
XFETCH_RENDER_IMAGE_TIMEOUT: int = _get_config_int(
    "KABUBU_XFETCH_RENDER_IMAGE_TIMEOUT", 6, minimum=2, maximum=30
)
XFETCH_RENDER_IMAGE_MAX_BYTES: int = _get_config_int(
    "KABUBU_XFETCH_RENDER_IMAGE_MAX_BYTES",
    5 * 1024 * 1024,
    minimum=256 * 1024,
    maximum=20 * 1024 * 1024,
)
XFETCH_RENDER_IMAGE_CONCURRENCY: int = _get_config_int(
    "KABUBU_XFETCH_RENDER_IMAGE_CONCURRENCY", 4, minimum=1, maximum=8
)
XFETCH_RENDER_MAX_HEIGHT: int = _get_config_int(
    "KABUBU_XFETCH_RENDER_MAX_HEIGHT", 4096, minimum=800, maximum=8192
)
XFETCH_RENDER_MAX_THREAD_ITEMS: int = _get_config_int(
    "KABUBU_XFETCH_RENDER_MAX_THREAD_ITEMS", 3, minimum=1, maximum=12
)
XFETCH_RENDER_BROWSER_MAX_USES: int = _get_config_int(
    "KABUBU_XFETCH_RENDER_BROWSER_MAX_USES", 5, minimum=1, maximum=100
)

# 卡片
CARD_WIDTH: int = 800
CARD_FONT_PATHS: list[str] = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\NotoSansSC-VF.ttf",
    r"C:\Windows\Fonts\msgothic.ttc",
    r"C:\Windows\Fonts\simsun.ttc",
]
