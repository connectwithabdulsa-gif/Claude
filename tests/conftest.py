import csv
from pathlib import Path

import pytest

from engine.db import connect
from engine.ingest.clay_csv import ClayCsvSource, load_column_map

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def column_map():
    return load_column_map(ROOT / "config" / "clay_columns.yaml")


@pytest.fixture
def write_csv(tmp_path):
    def _write(rows, name="export.csv", headers=None):
        headers = headers or list(rows[0].keys())
        p = tmp_path / name
        with p.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=headers)
            w.writeheader(); w.writerows(rows)
        return p
    return _write


@pytest.fixture
def source(column_map):
    return lambda path: ClayCsvSource(path, column_map)
