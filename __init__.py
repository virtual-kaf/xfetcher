"""nonebot_plugin_xfetch V2 - X/Twitter feed monitor with FxEmbed API.

When NoneBot has already been initialized this module keeps the original
plugin registration behavior. A standalone import, including the remote HTTP
service entry point, deliberately avoids importing commands and schedulers.
"""

from nonebot import get_driver

try:
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
    from .scheduler import check_lives, check_xfetch
    from .renderer import shutdown as shutdown_renderer

    get_driver().on_shutdown(shutdown_renderer)

    __plugin_meta__ = PluginMetadata(
        name="xfetch",
        description=(
            "X/Twitter feed monitor with FxEmbed API, translation, "
            "and live reminders"
        ),
        usage=(
            "/kabubu subscribe @id\n"
            "/kabubu unsubscribe @id\n"
            "/kabubu xfilter on | off\n"
            "/kabubu calendar [page]\n"
            "/kabubu update\n"
            "/kabubu generate <推特链接>"
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
