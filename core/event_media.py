"""Attach source-post media metadata to detected calendar events."""

from ..models.tweet import TweetConversation, TweetItem


def attach_event_media(
    events_detected: list[dict],
    conversations: dict[str, TweetConversation],
) -> None:
    """Mutate detected events with source IDs and their first photo URL."""
    source_items: dict[str, TweetItem] = {}
    for tweet_id, conversation in conversations.items():
        if conversation.target:
            source_items[f"{tweet_id}:target"] = conversation.target
        for index, ancestor in enumerate(conversation.ancestors):
            source_items[f"{tweet_id}:anc{index}"] = ancestor
        if conversation.quote:
            source_items[f"{tweet_id}:quote"] = conversation.quote

    for detected in events_detected:
        source = source_items.get(str(detected.get("tid", "")))
        if source is None:
            continue
        detected["source_tweet_id"] = source.id
        first_photo = next(
            (media.url for media in source.media if media.type == "photo" and media.url),
            "",
        )
        if first_photo:
            detected["cover_url"] = first_photo
