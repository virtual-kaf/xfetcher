import json
from typing import Optional
from datetime import datetime
from urllib.request import urlopen, Request

from nonebot import logger

from ..config import FXTWITTER_API_BASE, JST, REQUEST_TIMEOUT
from ..models.tweet import TweetAuthor, TweetConversation, TweetItem, TweetMedia


def _parse_date(raw: str) -> str:
    try:
        dt = datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        dt = datetime.strptime(raw, "%a %b %d %H:%M:%S +0000 %Y")
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S JST")


def _parse_parent_id(raw: dict) -> Optional[str]:
    """Read reply parent IDs from both legacy and current FxTwitter fields."""
    reply_ref = raw.get("in_reply_to") or raw.get("replying_to")
    if isinstance(reply_ref, dict):
        reply_ref = reply_ref.get("status") or reply_ref.get("id")
    if reply_ref is None or reply_ref == "":
        return None
    return str(reply_ref)


def _parse_tweet(raw: dict) -> TweetItem:
    author_raw = raw.get("author", {})
    author = TweetAuthor(
        id=author_raw.get("id", ""),
        name=author_raw.get("name", ""),
        screen_name=author_raw.get("screen_name", ""),
        avatar_url=author_raw.get("avatar_url", ""),
    )
    media = []
    m = raw.get("media")
    if m:
        for p in m.get("photos", []):
            media.append(TweetMedia(url=p.get("url", ""), width=p.get("width", 0),
                                    height=p.get("height", 0), type="photo"))
        for v in m.get("videos", []):
            media.append(TweetMedia(
                url=v.get("url", ""),
                thumbnail_url=v.get("thumbnail_url") or "",
                width=v.get("width", 0),
                height=v.get("height", 0),
                type="video",
            ))

    parent_id = _parse_parent_id(raw)

    return TweetItem(
        id=str(raw.get("id", "")),
        url=raw.get("url", ""),
        author=author,
        text=raw.get("text", ""),
        created_at=_parse_date(raw.get("created_at", "")),
        media=media,
        likes=raw.get("likes", 0) or 0,
        retweets=raw.get("retweets", 0) or 0,
        replies=raw.get("replies", 0) or 0,
        views=raw.get("views", 0) or 0,
        is_reply=bool(parent_id),
        parent_id=parent_id,
    )


def fetch_conversation(tweet_id: str) -> TweetConversation:
    """Fetch full conversation: status + thread ancestors + replies + quote."""
    url = f"{FXTWITTER_API_BASE}/2/conversation/{tweet_id}?ranking_mode=likes"
    req = Request(url, headers={"User-Agent": "xfetch/2.0"})
    with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        data = json.loads(resp.read())
    if data.get("code") != 200:
        raise RuntimeError(f"FxTwitter API code {data.get('code')}")

    tweet_raw = data.get("status") or data
    target = _parse_tweet(tweet_raw)

    # Ancestors from thread
    thread = data.get("thread") or []
    ancestors = []
    root = None
    for t in thread:
        if str(t.get("id", "")) != target.id:
            ancestors.append(_parse_tweet(t))
    ancestors.sort(key=lambda tweet: tweet.created_at)
    if ancestors:
        root = ancestors[0]

    # Quote
    quote_raw = tweet_raw.get("quote")
    quote = None
    if quote_raw and isinstance(quote_raw, dict) and quote_raw.get("type") != "tombstone":
        quote = _parse_tweet(quote_raw)

    # Replies
    replies_raw = data.get("replies") or []
    replies = [_parse_tweet(r) for r in replies_raw if r.get("type") != "tombstone"]

    logger.info(f"[FxTwitter] conversation: target={target.id}, ancestors={len(ancestors)}, "
                f"quote={quote.id if quote else 'none'}, replies={len(replies)}")

    return TweetConversation(
        root=root,
        ancestors=ancestors,
        target=target,
        quote=quote,
        replies=replies,
    )
