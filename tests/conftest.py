from __future__ import annotations

import pytest
from sqlalchemy import Engine

from common import db


@pytest.fixture
def engine(tmp_path) -> Engine:
    eng = db.get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.init_db(eng)
    return eng
