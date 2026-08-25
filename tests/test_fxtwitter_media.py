from nonebot_plugin_xfetch.clients.fxtwitter import _parse_tweet


def test_video_media_keeps_stream_url_and_thumbnail():
    tweet = _parse_tweet({
        "id": "1",
        "created_at": "Wed Oct 05 18:40:30 +0000 2022",
        "author": {},
        "media": {
            "videos": [{
                "url": "https://video.twimg.com/video.mp4",
                "thumbnail_url": "https://pbs.twimg.com/video-thumb.jpg",
                "width": 1280,
                "height": 720,
                "formats": [{"url": "https://video.twimg.com/high.mp4"}],
            }],
        },
    })

    video = tweet.media[0]
    assert video.url == "https://video.twimg.com/video.mp4"
    assert video.thumbnail_url == "https://pbs.twimg.com/video-thumb.jpg"


def test_video_media_accepts_missing_thumbnail():
    tweet = _parse_tweet({
        "id": "1",
        "created_at": "Wed Oct 05 18:40:30 +0000 2022",
        "author": {},
        "media": {"videos": [{"url": "https://video.twimg.com/video.mp4"}]},
    })

    assert tweet.media[0].thumbnail_url == ""
