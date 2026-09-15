from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from payer.vcc.amounts import cents_to_major, expiry_mm_yy, format_major, split_cardholder_name
from payer.vcc.loss import build_loss_order_id, build_loss_request_id
from payer.vcc.open_card import build_client_request_id
from payer.vcc.otp import build_payment_id
from payer.vcc.report import build_report_payload, build_resource_key
from payer.vcc.store import VccStore
from shared.models import Passenger, PaymentJob, utc_now


def test_amounts():
    assert cents_to_major(1500, "EUR") == "15.00"
    assert format_major("100", "USD") == "100.00"
    assert format_major("50000", "KRW") == "50000"
    assert expiry_mm_yy("12", "2031") == "12/31"
    assert split_cardholder_name("XUGUIBIN") == ("XUGUIBIN", "XUGUIBIN")
    assert split_cardholder_name("Ada Lovelace") == ("Ada", "Lovelace")


def test_ids():
    assert build_client_request_id("VIVATK123") == "CENACOLO-CARD-VIVATK123-1"
    assert build_payment_id("VIVATK123") == "PAY-VIVATK123"
    rid = build_loss_request_id("VIVATK9", "AST-ABCDEF1234567890", 1)
    assert rid.startswith("CENACOLO-LOSS-VIVATK9-")
    assert rid.endswith("-1")
    assert build_loss_order_id(business_order_no="CEN123", custref="VIVATK9") == "CEN123"
    assert build_loss_order_id(business_order_no="", custref="VIVATK9") == "VIVATK9"


def test_loss_store_idempotent():
    store = VccStore(Path(tempfile.mkdtemp()))
    payload = {
        "asset_id": "AST-1",
        "request_id": "CENACOLO-LOSS-X-1",
        "order_id": "CEN1",
        "reason": "debug_unsellable_no_refund",
        "confirmed_at": "2026-09-15T10:00:00+08:00",
    }
    store.save_loss_payload("CENACOLO-LOSS-X-1", payload)
    store.save_loss_payload(
        "CENACOLO-LOSS-X-1",
        {**payload, "reason": "changed"},  # must not overwrite
    )
    loaded = store.load_loss_payload("CENACOLO-LOSS-X-1")
    assert loaded is not None
    assert loaded["reason"] == "debug_unsellable_no_refund"


def test_resource_key_and_report(tmp_path: Path | None = None):
    key = build_resource_key(
        event_id="151991",
        date="2026-09-10",
        time_slot="09:00",
        tcode="T1",
        pcode="P1",
    )
    assert key == "CENACOLO_VIVATICKET:EVENT=151991:DATE=20260910:SESSION=0900:TYPE=T1:PCODE=P1"
    assert build_resource_key(
        event_id="151991", date="", time_slot="09:00", tcode="T1", pcode="P1"
    ) is None

    job = PaymentJob(
        payment_url="https://secure.vivaticket.com/paycmd/formtr.php?custref=VIVATK9&shop=CV0",
        custref="VIVATK9",
        account_email="buyer@example.com",
        date="2026-09-10",
        time="09:00",
        tcode="T1",
        pcode="P1",
        ticket_count=1,
        amount_cents=1500,
        passengers=[Passenger("a", "b")],
        user_agent="",
        locked_at=utc_now(),
        deadline_at=utc_now(),
    )
    payload = build_report_payload(
        job,
        pnr_source="cenacolo_vivaticket",
        vcc_order_id="VCC-abc",
        cost_amount="15.00",
        currency="EUR",
        paid_at="2026-09-14T12:00:00+00:00",
    )
    assert payload["pnr_source"] == "cenacolo_vivaticket"
    assert payload["supplier_order_no"] == "VIVATK9"
    assert payload["vcc_order_id"] == "VCC-abc"
    assert payload["tickets"][0]["cost_amount"] == "15.00"
    assert "card_number" not in json.dumps(payload)
    assert payload["paid_at"] == "2026-09-14T12:00:00+00:00"

    root = Path(tempfile.mkdtemp()) if tmp_path is None else Path(tmp_path)
    store = VccStore(root)
    store.save_application(
        {
            "client_request_id": "CENACOLO-CARD-VIVATK9-1",
            "create_body": {
                "client_request_id": "CENACOLO-CARD-VIVATK9-1",
                "amount": "50.00",
                "currency": "EUR",
            },
        }
    )
    store.save_report_payload("VCC-abc", payload)
    again = {
        **payload,
        "paid_at": "2026-09-14T99:00:00+00:00",  # must not overwrite first snapshot
    }
    store.save_report_payload("VCC-abc", again)
    loaded = store.load_report_payload("VCC-abc")
    assert loaded is not None
    assert loaded["paid_at"] == "2026-09-14T12:00:00+00:00"


def test_store_rejects_pan():
    store = VccStore(Path(tempfile.mkdtemp()))
    try:
        store.save_application({"client_request_id": "x", "card_number": "4111111111111111"})
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


if __name__ == "__main__":
    test_amounts()
    test_ids()
    test_loss_store_idempotent()
    test_resource_key_and_report()
    test_store_rejects_pan()
    print("ok")
