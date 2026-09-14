from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.config import AppConfig, CardConfig, PayQueueConfig, VccConfig
from shared.models import Passenger, PaymentJob
from worker.accounts import release_account, reserve_account
from worker.adapter import build_app_config
from worker.memory_store import MemoryStore
from worker.orders import claim_order, seed_order
from worker.pipeline import process_once
from worker.reaper import reap_expired
from worker.timeutil import iso_now, to_iso


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _base_cfg() -> AppConfig:
    return AppConfig(
        email="yaml@example.com",
        password="yaml-pass",
        base="https://cenacolovinciano.vivaticket.it",
        event_path="/en/event/cenacolo-vinciano/151991",
        idt="2547",
        target_dates=[],
        use_any_available=True,
        ticket_count=1,
        poll_when_empty=True,
        poll_interval_seconds=30,
        passengers=[Passenger("yaml", "user")],
        captcha_key="key",
        captcha_china=False,
        captcha_timeout=90,
        lock_ttl_seconds=1200,
        http_attempt_timeout_seconds=45,
        urgent_remaining_seconds=120,
        prefer_browser=True,
        card=CardConfig(),
        vcc=VccConfig(),
        pay_queue=PayQueueConfig(enabled=True, wake_url=""),
        headless=True,
        proxy="",
        browser_get_timeout=25,
        feishu_webhook="",
        feishu_secret="",
    )


def _ready_account(store: MemoryStore, email: str = "ready@163.com") -> dict:
    doc = {
        "email": email,
        "password": "acct-pass",
        "site": "cenacolovinciano",
        "status": "ready",
        "lease": {
            "order_id": None,
            "worker_id": None,
            "lease_token": None,
            "lease_until": None,
        },
    }
    store.insert("vivaticket_accounts", doc)
    return doc


def _job(cfg: AppConfig) -> PaymentJob:
    locked = _now()
    return PaymentJob(
        payment_url="https://secure.vivaticket.com/paycmd/formtr.php?custref=VIVATK1&shop=CV0",
        custref="VIVATK1",
        account_email=cfg.email,
        date="2026-09-10",
        time="09:00",
        tcode="t",
        pcode="p",
        ticket_count=cfg.ticket_count,
        amount_cents=1500,
        passengers=list(cfg.passengers),
        user_agent="test",
        locked_at=locked,
        deadline_at=locked + timedelta(seconds=1200),
    )


def test_two_workers_cannot_claim_same_order():
    store = MemoryStore()
    seed_order(store, passengers=[{"first_name": "Peng", "last_name": "Bin"}])
    first = claim_order(store, worker_id="w1")
    second = claim_order(store, worker_id="w2")
    assert first is not None
    assert first["status"] == "processing"
    assert first["worker"]["worker_id"] == "w1"
    assert second is None


def test_claim_skips_order_before_next_attempt():
    store = MemoryStore()
    future = to_iso(_now() + timedelta(hours=1))
    seed_order(
        store,
        passengers=[{"first_name": "Peng", "last_name": "Bin"}],
        next_attempt_at=future,
    )
    assert claim_order(store, worker_id="w1") is None


def test_adapter_overrides_yaml_with_order_and_account():
    order = {
        "passengers": [{"first_name": "Peng", "last_name": "Bin"}],
        "request": {
            "target_dates": ["2026-09-10", "2026-09-11"],
            "use_any_available": False,
            "ticket_count": 1,
        },
    }
    account = {"email": "ready@163.com", "password": "acct-pass"}
    cfg = build_app_config(_base_cfg(), order, account)
    assert cfg.email == "ready@163.com"
    assert cfg.password == "acct-pass"
    assert cfg.target_dates == ["2026-09-10", "2026-09-11"]
    assert cfg.use_any_available is False
    assert cfg.ticket_count == 1
    assert cfg.passengers[0].first_name == "Peng"
    assert cfg.poll_when_empty is False


def test_lock_success_writes_payment_url_and_releases_account():
    store = MemoryStore()
    seed_order(store, passengers=[{"first_name": "Peng", "last_name": "Bin"}])
    _ready_account(store)

    def locker(cfg):
        return _job(cfg), None, None

    outcome = process_once(
        store,
        _base_cfg(),
        worker_id="w1",
        locker_fn=locker,
    )
    assert outcome is not None
    assert outcome["status"] == "locked"
    assert outcome["result"]["custref"] == "VIVATK1"
    assert "formtr.php" in outcome["result"]["payment_url"]
    assert outcome["worker"]["worker_id"] is None
    acct = store.find("vivaticket_accounts", {"email": "ready@163.com"}, limit=1)[0]
    assert acct["status"] == "ready"


def test_lock_enqueues_pay_job():
    import tempfile
    from dataclasses import replace

    from worker.queue_util import make_pay_queue

    store = MemoryStore()
    seed_order(store, passengers=[{"first_name": "Peng", "last_name": "Bin"}])
    _ready_account(store)
    qdir = Path(tempfile.mkdtemp())
    cfg = replace(
        _base_cfg(),
        pay_queue=PayQueueConfig(
            enabled=True, queue_dir=str(qdir), wake_url=""
        ),
    )

    def locker(c):
        return _job(c), None, None

    outcome = process_once(store, cfg, worker_id="w1", locker_fn=locker)
    assert outcome["status"] == "locked"
    q = make_pay_queue(cfg)
    assert q.pending_count() == 1
    job = q.claim_next("pay-1")
    assert job is not None
    assert job.order_id == outcome["order_id"]
    assert job.custref == "VIVATK1"
    q.ack(job.order_id)
    assert q.pending_count() == 0


def test_pay_worker_claims_locked_and_marks_paid():
    from worker.orders import claim_locked_for_pay, write_locked
    from worker.pay_pipeline import process_pay_once
    from shared.models import PayResult

    store = MemoryStore()
    seed_order(store, passengers=[{"first_name": "Peng", "last_name": "Bin"}])
    claimed = claim_order(store, worker_id="lock-1")
    assert claimed is not None
    write_locked(store, claimed["order_id"], _job(_base_cfg()))

    first = claim_locked_for_pay(store, worker_id="pay-1")
    second = claim_locked_for_pay(store, worker_id="pay-2")
    assert first is not None
    assert first["status"] == "paying"
    assert second is None

    # Reset to locked for full pipeline test
    store.update(
        "cenacolo_orders",
        {"order_id": claimed["order_id"]},
        {"status": "locked", "version": int(first["version"]) + 1},
    )

    def payer(job, cfg, page=None):
        assert job.custref == "VIVATK1"
        return PayResult(
            True,
            "vcc",
            "ok",
            confirmed=True,
            purchase_id="PUR-1",
            vcc_order_id="VCC-1",
        )

    outcome = process_pay_once(
        store, _base_cfg(), worker_id="pay-9", pay_fn=payer, prefer_queue=False
    )
    assert outcome is not None
    assert outcome["status"] == "paid"
    assert outcome["result"]["purchase_id"] == "PUR-1"
    assert outcome["result"]["vcc_order_id"] == "VCC-1"


def test_payment_job_from_order():
    from worker.adapter import payment_job_from_order
    from worker.orders import write_locked

    store = MemoryStore()
    order = seed_order(store, passengers=[{"first_name": "Peng", "last_name": "Bin"}])
    write_locked(store, order["order_id"], _job(_base_cfg()))
    refreshed = store.find("cenacolo_orders", {"order_id": order["order_id"]}, limit=1)[0]
    job = payment_job_from_order(refreshed)
    assert job.custref == "VIVATK1"
    assert job.amount_cents == 1500
    assert "formtr.php" in job.payment_url



def test_no_inventory_waits_and_returns_account():
    store = MemoryStore()
    seed_order(store, passengers=[{"first_name": "Peng", "last_name": "Bin"}])
    _ready_account(store)

    def locker(_cfg):
        raise RuntimeError("当前无可用票（日历/时段余量为 0）")

    outcome = process_once(store, _base_cfg(), worker_id="w1", locker_fn=locker)
    assert outcome["status"] == "waiting_inventory"
    assert outcome["attempts"] == 0
    acct = store.find("vivaticket_accounts", {"email": "ready@163.com"}, limit=1)[0]
    assert acct["status"] == "ready"


def test_reaper_recovers_expired_processing_order():
    store = MemoryStore()
    order = seed_order(store, passengers=[{"first_name": "Peng", "last_name": "Bin"}])
    claimed = claim_order(store, worker_id="w1")
    _ready_account(store)
    reserved = reserve_account(store, order_id=claimed["order_id"], worker_id="w1")
    past = to_iso(_now() - timedelta(minutes=10))
    store.update(
        "cenacolo_orders",
        {"order_id": order["order_id"]},
        {"worker.lease_until": past},
    )
    store.update(
        "vivaticket_accounts",
        {"email": reserved["email"]},
        {"lease.lease_until": past},
    )
    n = reap_expired(store, now=_now())
    assert n >= 1
    refreshed = store.find("cenacolo_orders", {"order_id": order["order_id"]}, limit=1)[0]
    assert refreshed["status"] == "retry_wait"
    acct = store.find("vivaticket_accounts", {"email": reserved["email"]}, limit=1)[0]
    assert acct["status"] == "ready"


def test_reaper_does_not_auto_retry_locked_order():
    store = MemoryStore()
    order = seed_order(store, passengers=[{"first_name": "Peng", "last_name": "Bin"}])
    past = to_iso(_now() - timedelta(minutes=10))
    store.update(
        "cenacolo_orders",
        {"order_id": order["order_id"]},
        {
            "status": "locked",
            "worker.worker_id": "w1",
            "worker.lease_until": past,
            "result.payment_url": "https://secure.example/pay",
        },
    )
    _ready_account(store)
    store.update(
        "vivaticket_accounts",
        {"email": "ready@163.com"},
        {
            "status": "reserved",
            "lease.order_id": order["order_id"],
            "lease.lease_until": past,
        },
    )
    reap_expired(store, now=_now())
    refreshed = store.find("cenacolo_orders", {"order_id": order["order_id"]}, limit=1)[0]
    assert refreshed["status"] == "manual_review"
    acct = store.find("vivaticket_accounts", {"email": "ready@163.com"}, limit=1)[0]
    assert acct["status"] == "review"


def test_two_workers_cannot_reserve_same_account():
    store = MemoryStore()
    _ready_account(store)
    first = reserve_account(store, order_id="o1", worker_id="w1")
    second = reserve_account(store, order_id="o2", worker_id="w2")
    assert first is not None
    assert first["status"] == "reserved"
    assert second is None


def test_release_account_clears_lease():
    store = MemoryStore()
    _ready_account(store)
    reserved = reserve_account(store, order_id="o1", worker_id="w1")
    assert reserved["status"] == "reserved"
    release_account(store, reserved["email"], to_status="ready")
    acct = store.find("vivaticket_accounts", {"email": reserved["email"]}, limit=1)[0]
    assert acct["status"] == "ready"
    assert acct["lease"]["order_id"] is None


if __name__ == "__main__":
    test_two_workers_cannot_claim_same_order()
    test_claim_skips_order_before_next_attempt()
    test_adapter_overrides_yaml_with_order_and_account()
    test_lock_success_writes_payment_url_and_releases_account()
    test_lock_enqueues_pay_job()
    test_pay_worker_claims_locked_and_marks_paid()
    test_payment_job_from_order()
    test_no_inventory_waits_and_returns_account()
    test_reaper_recovers_expired_processing_order()
    test_reaper_does_not_auto_retry_locked_order()
    test_two_workers_cannot_reserve_same_account()
    test_release_account_clears_lease()
    print("ok")
