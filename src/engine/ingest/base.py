"""DataSource adapter interface. Clay csv now, Vibe Prospecting or an api later."""
from dataclasses import dataclass, field
from typing import Iterator, Protocol


@dataclass
class SourceRow:
    fields: dict            # canonical engine fields (first_name, email, company_domain, ...)
    raw: dict               # every original column, untouched
    line: int               # 1-based data row number in the source


@dataclass
class SourceInfo:
    source: str             # e.g. "clay"
    source_file: str
    unmapped_columns: list = field(default_factory=list)


class DataSource(Protocol):
    info: SourceInfo

    def rows(self) -> Iterator[SourceRow]: ...
