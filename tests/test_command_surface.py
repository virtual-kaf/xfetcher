from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_xfetch_registers_only_the_new_command_surface():
    commands = (PLUGIN_DIR / "commands.py").read_text(encoding="utf-8")
    debug = (PLUGIN_DIR / "debug.py").read_text(encoding="utf-8")

    assert 'on_command("sub", aliases={"订阅"}' in commands
    assert '"unsub",\n    aliases={"取消订阅"}' in commands
    assert 'on_command("sublist", aliases={"订阅名单"}' in commands
    assert '"updatex",\n    permission=SUPERUSER' in commands
    assert 'on_command("fetch", aliases={"获取"}' in commands
    assert '"filter",\n    aliases={"水帖过滤"}' in commands
    assert 'on_command("calendar",\n                     aliases={"日历"}' in commands
    assert '"generate",\n    permission=SUPERUSER' in debug
    assert "on_command(\"kabubu" not in commands + debug
    assert "aliases=" not in debug.split("gen_cmd =", maxsplit=1)[1].split(
        "@gen_cmd.handle", maxsplit=1
    )[0]


def test_permissions_are_declared_on_restricted_commands():
    commands = (PLUGIN_DIR / "commands.py").read_text(encoding="utf-8")

    assert commands.count("permission=GROUP_ADMIN | GROUP_OWNER") == 2
    assert commands.count("permission=SUPERUSER") == 1
