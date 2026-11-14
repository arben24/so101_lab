"""ID-Vergabe (servo_setup) gegen simulierte Servos: Ein-Servo-Pflicht, Baudrate, Prüfung, Protokoll."""

import csv

import pytest

from so101_lab import registers as regs
from so101_lab.backends.base import ServoNotRespondingError
from so101_lab.backends.mock import MockBackend, MockServo
from so101_lab.devices import get_device_spec
from so101_lab.servo_setup import ServoSetup, SetupError, setup_order


def bus_with(*servos: MockServo) -> MockBackend:
    backend = MockBackend(list(servos))
    backend.connect()
    return backend


def new_servo(servo_id: int = 1, baudrate: int = 1_000_000) -> MockServo:
    servo = MockServo.factory_new(servo_id)
    servo.values["baud_rate"] = servo.persisted["baud_rate"] = regs.baudrates()[baudrate]
    return servo


@pytest.fixture
def log_path(tmp_path):
    return tmp_path / "logs" / "servo_ids.csv"


def make_setup(backend, log_path, device_type="so101_follower"):
    return ServoSetup(backend, device_type, port="/dev/ttyTEST", adapter_sn="SN1", log_path=log_path)


def read_log(path):
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_order_like_lerobot():
    assert [j.motor_id for j in setup_order(get_device_spec("so101_follower"))] == [6, 5, 4, 3, 2, 1]
    assert [j.motor_id for j in setup_order(get_device_spec("lekiwi"))] == [6, 5, 4, 3, 2, 1, 9, 8, 7]


def test_new_servo_gets_id_and_is_logged(log_path):
    bus = bus_with(new_servo())
    setup = make_setup(bus, log_path)
    assert setup.next_joint() == "gripper"
    [found] = setup.scan()
    assert found.id == 1 and found.baudrate == 1_000_000
    assert setup.plan("gripper") == ["ID 1 → 6 (gripper)"]

    result = setup.assign("gripper")
    assert result.ok, result.message
    assert result.action == "id_gesetzt"
    assert [c.ok for c in result.checks] == [True] * len(result.checks)
    assert bus.servos[0].persisted["id"] == 6  # dauerhaft im EEPROM
    assert setup.status["gripper"] == "gesetzt" and setup.next_joint() == "wrist_roll"
    assert setup.found is None  # nach dem Schreiben neu suchen

    [row] = read_log(log_path)
    assert row["aktion"] == "id_gesetzt" and row["ergebnis"] == "ok"
    assert (row["alte_id"], row["neue_id"], row["gelenk"]) == ("1", "6", "gripper")
    assert (row["port"], row["adapter_sn"], row["geraetetyp"]) == ("/dev/ttyTEST", "SN1", "so101_follower")


def test_other_baudrate_is_set_to_1mbit(log_path):
    bus = bus_with(new_servo(baudrate=115_200))
    setup = make_setup(bus, log_path)
    [found] = setup.scan()
    assert found.baudrate == 115_200
    assert bus.baudrate == 1_000_000  # Port nach der Suche wieder auf Standard
    assert setup.plan("elbow_flex") == ["ID 1 → 3 (elbow_flex)", "Baudrate 115200 → 1000000"]

    result = setup.assign("elbow_flex")
    assert result.ok, result.message
    servo = bus.servos[0]
    assert servo.id == 3 and servo.baudrate == 1_000_000
    assert servo.persisted["baud_rate"] == regs.baudrates()[1_000_000]
    assert bus.baudrate == 1_000_000
    # LeRobot-Reihenfolge: erst ID (bei der alten Rate), dann Baudrate
    writes = [(e[1], e[2]) for e in bus.log if e[0] == "write" and e[2] in ("id", "baud_rate")]
    assert writes == [(1, "id"), (3, "baud_rate")]


def test_nothing_found(log_path):
    setup = make_setup(bus_with(), log_path)
    assert setup.scan() == []
    with pytest.raises(SetupError, match="Kein Servo gefunden"):
        setup.assign("gripper")
    assert not log_path.exists()


def test_several_servos_are_refused(log_path):
    bus = bus_with(new_servo(1), new_servo(2, baudrate=500_000))
    setup = make_setup(bus, log_path)
    assert len(setup.scan()) == 2
    with pytest.raises(SetupError, match="nur EIN Servo"):
        setup.assign("gripper")
    assert sorted(s.id for s in bus.servos) == [1, 2]
    assert not log_path.exists()


def test_two_servos_with_same_id_are_detected_by_probe(log_path):
    # Die Suche sieht nur „ID 1“, das wiederholte Lesen bemerkt die Überlagerung.
    bus = bus_with(new_servo(1), new_servo(1))
    setup = make_setup(bus, log_path)
    assert len(setup.scan()) == 1
    with pytest.raises(SetupError, match="unzuverlässig"):
        setup.assign("gripper")
    assert [s.id for s in bus.servos] == [1, 1]


def test_servo_swapped_after_scan(log_path):
    bus = bus_with(new_servo(1))
    setup = make_setup(bus, log_path)
    setup.scan()
    bus.servos = [new_servo(4)]  # anderer Servo gesteckt
    with pytest.raises(SetupError, match="abgezogen"):
        setup.assign("gripper")
    assert bus.servos[0].id == 4


def test_already_correct_is_only_checked(log_path):
    bus = bus_with(new_servo(6))
    setup = make_setup(bus, log_path)
    setup.scan()
    assert setup.plan("gripper") == []
    result = setup.assign("gripper")
    assert result.ok and result.action == "unveraendert"
    assert not [e for e in bus.log if e[0] == "write"]  # nichts geschrieben
    assert read_log(log_path)[0]["aktion"] == "unveraendert"


def test_unknown_joint(log_path):
    setup = make_setup(bus_with(new_servo()), log_path)
    setup.scan()
    with pytest.raises(SetupError, match="base_left_wheel"):
        setup.assign("base_left_wheel")  # gibt es nur bei LeKiwi


def test_restart_check_passes_when_stored_in_eeprom(log_path):
    bus = bus_with(new_servo())
    setup = make_setup(bus, log_path)
    setup.scan()
    assert setup.assign("gripper").ok
    bus.power_cycle()
    result = setup.verify_after_restart("gripper")
    assert result.ok, result.message
    assert setup.status["gripper"] == "dauerhaft"
    assert [r["aktion"] for r in read_log(log_path)] == ["id_gesetzt", "neustart"]


def test_restart_check_fails_when_not_persisted(log_path):
    bus = bus_with(new_servo())
    setup = make_setup(bus, log_path)
    setup.scan()
    assert setup.assign("gripper").ok
    bus.servos[0].persisted["id"] = 1  # als hätte das EEPROM die ID nicht übernommen
    bus.power_cycle()
    result = setup.verify_after_restart("gripper")
    assert not result.ok and "nicht dauerhaft" in result.message
    assert setup.status["gripper"] == "fehler"
    assert read_log(log_path)[-1]["ergebnis"] == "fehler"


def test_write_failure_is_reported_and_logged(log_path, monkeypatch):
    bus = bus_with(new_servo())
    setup = make_setup(bus, log_path)
    setup.scan()

    def broken(old_id, new_id):
        raise ServoNotRespondingError("Servo 1 bestätigt das Schreiben von 'id' nicht.")

    monkeypatch.setattr(bus, "change_id", broken)
    result = setup.assign("gripper")
    assert not result.ok and "bestätigt" in result.message
    assert setup.status["gripper"] == "fehler"
    assert read_log(log_path)[0]["ergebnis"] == "fehler"


def test_snapshot(log_path):
    setup = make_setup(bus_with(new_servo()), log_path, device_type="lekiwi")
    snap = setup.snapshot()
    assert snap["next"] == "arm_gripper" and len(snap["order"]) == 9
    assert snap["found"] is None and snap["last"] is None
    setup.scan()
    assert setup.snapshot()["found"][0]["id"] == 1


def test_finished_servo_is_not_reassigned_by_mistake(log_path):
    # Servo nicht getauscht, neu gesucht, nächstes Gelenk vorgeschlagen: darf nicht umschreiben.
    bus = bus_with(new_servo())
    setup = make_setup(bus, log_path)
    setup.scan()
    assert setup.assign("gripper").ok
    setup.scan()
    with pytest.raises(SetupError, match="eben als gripper eingerichtet"):
        setup.assign(setup.next_joint())
    assert bus.servos[0].id == 6
    assert setup.assign("gripper").ok  # dasselbe Gelenk noch einmal prüfen geht


def test_restart_check_requires_new_scan_afterwards(log_path):
    bus = bus_with(new_servo())
    setup = make_setup(bus, log_path)
    setup.scan()
    setup.assign("gripper")
    setup.verify_after_restart("gripper")
    assert setup.found is None


# ------------------------------------------------------------------ freie ID


def test_free_id_for_a_single_servo(log_path):
    bus = bus_with(new_servo(baudrate=115_200))
    setup = make_setup(bus, log_path)
    setup.scan()
    assert setup.plan(servo_id=42) == ["ID 1 → 42", "Baudrate 115200 → 1000000"]
    result = setup.assign(servo_id=42)
    assert result.ok, result.message
    assert result.joint == "" and result.new_id == 42
    assert bus.servos[0].id == 42 and bus.servos[0].baudrate == 1_000_000
    assert all(v == "offen" for v in setup.status.values())  # gehört zu keinem Gelenk
    row = read_log(log_path)[0]
    assert (row["gelenk"], row["neue_id"], row["ergebnis"]) == ("", "42", "ok")
    bus.power_cycle()
    assert setup.verify_after_restart(servo_id=42).ok


def test_free_id_of_a_joint_counts_as_that_joint(log_path):
    setup = make_setup(bus_with(new_servo()), log_path)
    setup.scan()
    result = setup.assign(servo_id=6)
    assert result.ok and result.joint == "gripper"
    assert setup.status["gripper"] == "gesetzt"


@pytest.mark.parametrize("bad", [-1, 254, 255])
def test_free_id_range(log_path, bad):
    setup = make_setup(bus_with(new_servo()), log_path)
    setup.scan()
    with pytest.raises(SetupError, match="0 bis 253"):
        setup.assign(servo_id=bad)


def test_target_needs_exactly_one_of_joint_or_id(log_path):
    setup = make_setup(bus_with(new_servo()), log_path)
    with pytest.raises(SetupError, match="Entweder"):
        setup.target()
    with pytest.raises(SetupError, match="Entweder"):
        setup.target("gripper", 6)
