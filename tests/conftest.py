from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("VERISMO_EMBEDDER", "hashing")  # never touch the network in tests

from verismo_engine.config import Config  # noqa: E402
from verismo_engine.context import Engine, open_engine  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def make_config(tmp: Path) -> Config:
    cfg = Config(
        data_dir=tmp / "data", shared_dir=tmp / "shared", keystore="file", token="test-token", start_workers=False
    )
    return cfg


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = open_engine(make_config(tmp_path))
    yield eng
    eng.close()


@pytest.fixture
def refdata_sample(engine: Engine) -> Engine:
    """Engine with the bundled sample reference data installed."""
    from verismo_engine.clinical.refdata import install_dev_sample

    install_dev_sample(engine)
    return engine
