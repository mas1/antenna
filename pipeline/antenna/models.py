"""The one contract every collector speaks.

A collector observes something in the world and emits a Signal. A Signal
carries an EntityHint (whatever identifiers the source happened to expose)
and the resolver later merges hints that point at the same company.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

FAMILIES = (
    "github",
    "research",
    "hiring",
    "launch",
    "traffic",
    "social",
    "capital",
    "regulatory",
)

SECTORS = (
    "robotics",
    "autonomy",
    "defense",
    "energy",
    "manufacturing",
    "semiconductors",
    "space",
    "other",
)


@dataclass
class Person:
    name: str
    role: str | None = None
    github: str | None = None
    openalex_id: str | None = None
    affiliations: list[str] = field(default_factory=list)
    links: dict[str, str] = field(default_factory=dict)
    # Free-form numeric facts: h_index, cited_by_count, followers, ...
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass
class EntityHint:
    """Identifiers a source exposes for the thing it observed.

    Fill in everything the source gives you and nothing it does not. The
    resolver keys on domain first, then github login, then normalized name.
    """

    name: str
    kind: str = "company"  # company | project | person
    domain: str | None = None
    github: str | None = None  # org or user login, never a full URL
    aliases: list[str] = field(default_factory=list)
    one_liner: str | None = None
    description: str | None = None
    location: str | None = None
    founded: str | None = None  # ISO date or year
    links: dict[str, str] = field(default_factory=dict)


@dataclass
class Signal:
    source: str  # collector slug, e.g. "sec_form_d"
    family: str  # one of FAMILIES
    kind: str  # e.g. "star_velocity", "form_d", "sbir_phase1"
    entity: EntityHint
    title: str  # one plain sentence a partner can read
    occurred_at: str  # ISO 8601 date or datetime of the real-world event
    url: str  # evidence link, always
    value: float | None = None  # the headline number, if there is one
    unit: str | None = None  # "stars/30d", "USD", "open roles", ...
    strength: float = 0.5  # collector's own 0..1 read of how strong this is
    metrics: dict[str, Any] = field(default_factory=dict)
    people: list[Person] = field(default_factory=list)
    series: list[dict[str, Any]] = field(default_factory=list)  # [{t, v}]
    text: str | None = None  # raw text used for thesis classification

    def to_row(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        if self.family not in FAMILIES:
            raise ValueError(f"unknown family {self.family!r} from {self.source}")
        if not self.url.startswith("http"):
            raise ValueError(f"signal from {self.source} has no evidence url")
        if not self.entity.name.strip():
            raise ValueError(f"signal from {self.source} has an unnamed entity")
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError(f"strength out of range from {self.source}")
