from dataclasses import dataclass, field
from typing import List

@dataclass
class LiveEvent:
    event_id: str
    members: List[str] = field(default_factory=list)
    title: str = ""
    start_time_utc: str = ""
    is_precise: bool = True
    notified: bool = False
    cover_url: str = ""
    source_tweet_id: str = ""
