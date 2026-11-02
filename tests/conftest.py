import pytest

from emporio.config import PROJECT_ROOT
from emporio.data import load_store


@pytest.fixture(scope="session")
def store():
    return load_store(PROJECT_ROOT / "data")
