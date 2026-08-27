"""nonebot_plugin_xfetch V2 - X/Twitter feed monitor with FxEmbed API.

When NoneBot has already been initialized this module keeps the original
plugin registration behavior. A standalone import, including the remote HTTP
service entry point, deliberately avoids importing commands and schedulers.
"""

from nonebot import get_driver

try:
    if not __package__:
        # Pytest can collect this root-mapped package file as bare ``__init__``.
        # In that case relative plugin imports are intentionally unavailable.
        raise ValueError("package context is unavailable")
    get_driver()
except ValueError:
    __all__: list[str] = []
else:
    from nonebot.plugin import PluginMetadata

    from .commands import (
        cal_cmd,
        fetch_cmd,
        sub_cmd,
        sublist_cmd,
        unsub_cmd,
        update_cmd,
        xfilter_cmd,
    )
    from .debug import gen_cmd
    from .renderer import shutdown as shutdown_renderer
    from .scheduler import check_lives, check_xfetch

    get_driver().on_shutdown(shutdown_renderer)

    __plugin_meta__ = PluginMetadata(
        name="xfetch",
        description=(
            "X/Twitter feed monitor with FxEmbed API, translation, "
            "and live reminders"
        ),
        usage=(
            "/sub @id | /订阅 @id\n"
            "/unsub @id | /取消订阅 @id\n"
            "/sublist | /订阅名单\n"
            "/calendar [page] | /日历 [page]\n"
            "/fetch @id [数量] | /获取 @id [数量]\n"
            "/filter on | off | /水帖过滤 on | off\n"
            "/updatex\n"
            "/generate <推特链接>"
        ),
        type="application",
        supported_adapters={"~onebot.v11"},
    )

    __all__ = [
        "cal_cmd",
        "check_lives",
        "check_xfetch",
        "fetch_cmd",
        "gen_cmd",
        "sub_cmd",
        "sublist_cmd",
        "unsub_cmd",
        "update_cmd",
        "xfilter_cmd",
    ]
