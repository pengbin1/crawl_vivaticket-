from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from locker.checkout import extract_custref
from locker.inventory import parse_calendar, positive_count


def test_parse_calendar():
    html = (
        "eventi['151991'].push(new Array ('vt0005655','13792799', "
        "new Date (2026, (08-1), 14), '0', 49, '3'));"
    )
    items = parse_calendar(html)
    assert len(items) == 1
    assert items[0].date == "2026-08-14"
    assert items[0].seats == "3"
    assert positive_count(items[0].seats)


def test_extract_custref():
    url = "https://secure.vivaticket.com/paycmd/formtr.php?custref=VIVATK123&shop=CV0"
    assert extract_custref(url) == "VIVATK123"


if __name__ == "__main__":
    test_parse_calendar()
    test_extract_custref()
    print("ok")
