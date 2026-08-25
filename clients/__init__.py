"""Lazy client exports.

Keeping these imports lazy lets the standalone Grok service load without
importing the TwitterAPI fallback or the rest of the NoneBot pipeline.
"""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "discover_tweet_urls": ("discovery", "discover_tweet_urls"),
    "fetch_conversation": ("fxtwitter", "fetch_conversation"),
    "grok_fetch_urls": ("grok", "grok_fetch_urls"),
    "match_events_batch": ("deepseek", "match_events_batch"),
    "review_batch": ("deepseek", "review_batch"),
    "translate_and_review_batch": ("deepseek", "translate_and_review_batch"),
    "translate_batch": ("deepseek", "translate_batch"),
    "twitterapi_fetch_urls": ("twitterapi_io", "twitterapi_fetch_urls"),
}

__all__ = [
    "discover_tweet_urls",
    "fetch_conversation",
    "grok_fetch_urls",
    "match_events_batch",
    "review_batch",
    "translate_and_review_batch",
    "translate_batch",
    "twitterapi_fetch_urls",
]


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute_name = target
    value = getattr(import_module(f"{__name__}.{module_name}"), attribute_name)
    globals()[name] = value
    return value
