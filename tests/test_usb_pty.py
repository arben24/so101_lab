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


@pytest.fixture
def pty_pair():
    import pty

    master, slave = pty.openpty()
    yield master, os.ttyname(slave)
    os.close(master)
    os.close(slave)


def test_id_and_baudrate_go_through_rustypot(pty_pair):
    """ID und Baudrate laufen wie bei LeRobot über rustypot. Ohne Antwort: verständlicher Fehler."""
    from so101_lab.backends.base import ServoNotRespondingError

    master, port = pty_pair
    with UsbBackend(port, timeout_s=0.01) as bus:
        with pytest.raises(ServoNotRespondingError, match="ID-Wechsel auf 6"):
            bus._write_id(1, 6)
        sent = os.read(master, 4096)
        # rustypot schaltet zuerst das Drehmoment aus: Schreiben an ID 1, Adresse 40, Wert 0
        assert bytes([0xFF, 0xFF, 0x01, 0x04, 0x03, 40, 0x00]) in sent
        with pytest.raises(ServoNotRespondingError, match="1000000 Baud"):
            bus._write_baudrate(1, 1_000_000)
        with pytest.raises(BackendError):
            bus._write_baudrate(1, 1234)
