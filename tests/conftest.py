import pytest

from clipper import db
from clipper.config import load_config


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def cfg(tmp_path):
    c = load_config(tmp_path)  # valeurs par défaut, sans config.yaml
    return c
