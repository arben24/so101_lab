"""Die Tabellen in der Doku stimmen mit dem Code überein (keine Abschreibfehler)."""

import re
from pathlib import Path

from so101_lab import registers as regs
from so101_lab.devices import get_device_spec

DOCS = Path(__file__).resolve().parent.parent / "docs"


def table_rows(text: str, pattern: str) -> list[tuple[str, ...]]:
    return [m.groups() for line in text.splitlines() if (m := re.match(pattern, line))]


def test_joint_tables_match_device_specs():
    text = (DOCS / "01-aufbau.md").read_text(encoding="utf-8")
    so101_part, lekiwi_part = text.split("### LeKiwi")
    so101_part = so101_part.split("### SO-101")[1]
    pattern = r"^\| (\d+) \| `([a-z_]+)` \|"

    so101 = {int(i): n for i, n in table_rows(so101_part, pattern)}
    assert so101 == {j.motor_id: j.name for j in get_device_spec("so101_follower").joints}
    assert so101 == {j.motor_id: j.name for j in get_device_spec("so101_leader").joints}

    lekiwi_part = lekiwi_part.split("## Der Servo")[0]
    lekiwi = {int(i): n for i, n in table_rows(lekiwi_part, pattern)}
    assert lekiwi == {j.motor_id: j.name for j in get_device_spec("lekiwi").joints}


def test_register_table_matches_driver():
    text = (DOCS / "03-servos.md").read_text(encoding="utf-8")
    rows = table_rows(text, r"^\| (\d+) \| `([a-z_0-9]+)` \| (\d) \| ([^|]+) \| (RW?) \|")
    expected = {
        r.address: (
            r.name,
            r.size,
            {"EEPROM": "EEPROM", "RAM": "RAM"}.get(r.area.value, "nicht in V3.7"),
            "RW" if r.writable else "R",
        )
        for r in regs.registers()
    }
    documented = {int(a): (n, int(size), area.strip(), acc) for a, n, size, area, acc in rows}
    assert documented == expected


def test_every_doc_page_in_nav_exists():
    nav = (DOCS.parent / "mkdocs.yml").read_text(encoding="utf-8")
    for name in re.findall(r": ([\w-]+\.md)$", nav, flags=re.MULTILINE):
        assert (DOCS / name).exists(), name
