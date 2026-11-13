"""UsbBackend gegen ein Pseudo-Terminal: echter rustypot-Treiber, aber niemand antwortet."""

import os
import sys

import pytest

from so101_lab.backends import BackendError
from so101_lab.backends.usb import UsbBackend

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Pseudo-Terminal nur unter Linux getestet")


@pytest.fixture
def pty_port():
    import pty

    master, slave = pty.openpty()
    yield os.ttyname(slave)
    os.close(master)
    os.close(slave)


def test_scan_empty_bus(pty_port):
    with UsbBackend(pty_port, timeout_s=0.01) as bus:
        assert bus.scan(ids=[1, 2]) == []
        assert bus.ping(1) is None


def test_missing_port_message():
    with pytest.raises(BackendError, match="nicht gefunden"):
        UsbBackend("/dev/gibt_es_nicht").connect()


def test_port_is_exclusive(pty_port):
    with UsbBackend(pty_port, timeout_s=0.01), pytest.raises(BackendError, match="belegt"):
        UsbBackend(pty_port).connect()
