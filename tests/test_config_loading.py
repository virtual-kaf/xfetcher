from types import SimpleNamespace

from nonebot_plugin_xfetch import config


def test_process_environment_takes_precedence(monkeypatch):
    monkeypatch.setattr(
        config.os,
        "getenv",
        lambda name: "from-process" if name == "SETTING" else None,
    )
    monkeypatch.setattr(
        config,
        "get_driver",
        lambda: SimpleNamespace(
            config=SimpleNamespace(setting="from-nonebot")
        ),
    )

    assert config._get_config_str("SETTING", "default") == "from-process"


def test_nonebot_dotenv_config_is_used_when_process_env_is_absent(
    monkeypatch,
):
    monkeypatch.setattr(config.os, "getenv", lambda _name: None)
    monkeypatch.setattr(
        config,
        "get_driver",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                kabubu_deepseek_api_key="from-nonebot-dotenv"
            )
        ),
    )

    assert (
        config._get_config_str("KABUBU_DEEPSEEK_API_KEY")
        == "from-nonebot-dotenv"
    )


def test_default_is_used_before_nonebot_initialization(monkeypatch):
    monkeypatch.setattr(config.os, "getenv", lambda _name: None)

    def missing_driver():
        raise ValueError("NoneBot has not been initialized")

    monkeypatch.setattr(config, "get_driver", missing_driver)

    assert config._get_config_str("SETTING", "default") == "default"
