import pytest

from so101_lab.backends.mock import MockBackend


class FakeClock:
    """Steuerbare Zeit für die Simulation."""

    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def mock_bus(clock):
    backend = MockBackend.for_ids(range(1, 7), clock=clock)
    backend.connect()
    yield backend
    backend.disconnect()
