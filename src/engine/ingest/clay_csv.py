"""Clay csv export reader. headers mapped through config/clay_columns.yaml."""
import csv
from pathlib import Path
from typing import Iterator

import yaml

from ..normalize import norm_key
from .base import SourceInfo, SourceRow


def load_column_map(path: Path) -> dict[str, str]:
    """returns {normalized header: engine field}."""
    spec = yaml.safe_load(path.read_text()) or {}
    out = {}
    for field_name, aliases in spec.items():
        for alias in [field_name, *(aliases or [])]:
            out.setdefault(norm_key(alias), field_name)
    return out


class ClayCsvSource:
    def __init__(self, path: Path, column_map: dict[str, str]):
        self.path = Path(path)
        self.column_map = column_map
        self.info = SourceInfo(source="clay", source_file=self.path.name)

    def rows(self) -> Iterator[SourceRow]:
        with self.path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            mapping: dict[str, str] = {}
            for h in headers:
                target = self.column_map.get(norm_key(h))
                # first header wins if two map to the same field
                if target and target not in mapping.values():
                    mapping[h] = target
            self.info.unmapped_columns = [h for h in headers if h not in mapping]
            for i, raw in enumerate(reader, start=1):
                fields = {target: (raw.get(h) or "").strip() for h, target in mapping.items()}
                yield SourceRow(fields=fields, raw=dict(raw), line=i)
