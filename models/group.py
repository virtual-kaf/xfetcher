from dataclasses import dataclass, field


@dataclass
class GroupConfig:
    group_id: str
    subs: list[str] = field(default_factory=list)
    unsubs: list[str] = field(default_factory=list)
    filter_water: bool = True
