from __future__ import annotations

import time

import requests
from DrissionPage import ChromiumPage

from locker.auth import clear_cart, login
from locker.checkout import try_lock_slot
from locker.inventory import find_available_slots
from locker.waf import bootstrap_waf, click_queue_it_rejoin_if_present
from shared.config import AppConfig
from shared.log import get_logger
from shared.models import PaymentJob

logger = get_logger(__name__)


def run_locker(cfg: AppConfig) -> tuple[PaymentJob, requests.Session, ChromiumPage]:
    """
    Full lock pipeline. Returns job + live session/page.
    Caller must pay immediately (20 min window).
    """
    session, page = bootstrap_waf(cfg)
    login(session, cfg)
    clear_cart(session, cfg)

    # Prefer the post-Safetynet browser URL (qubs*) — plain event_url often
    # returns a thinner calendar with all seats=0.
    event_referer = page.url or cfg.event_url
    logger.info("[locker] event referer=%s", event_referer)
    first_pass = True

    while True:
        # First pass: keep the HTML just loaded by WAF (refresh often yields a
        # thinner calendar with all seats=0). Later polls refresh.
        if not first_pass:
            try:
                page.get(
                    event_referer if "qubsq=" in event_referer else cfg.event_url,
                    timeout=cfg.browser_get_timeout,
                )
                click_queue_it_rejoin_if_present(page)
                time.sleep(0.8)
                event_referer = page.url or event_referer
            except Exception as exc:
                logger.warning("[locker] refresh failed: %s", exc)
        first_pass = False

        browser_html = page.html or ""
        logger.info("[locker] inventory html_len=%s", len(browser_html))
        _, slots = find_available_slots(
            session,
            cfg,
            event_url=event_referer,
            browser_html=browser_html,
        )
        if not slots:
            # Extra attempt: HTTP fetch with current referer before giving up / sleep
            _, slots = find_available_slots(
                session, cfg, event_url=event_referer, browser_html=""
            )
        if not slots:
            if not cfg.poll_when_empty:
                raise RuntimeError("当前无可用票（日历/时段余量为 0）")
            logger.info(
                "[locker] no seats, sleep %ss", cfg.poll_interval_seconds
            )
            time.sleep(cfg.poll_interval_seconds)
            continue

        for slot in slots:
            logger.info(
                "[locker] trying %s %s seats=%s pcode=%s",
                slot.date,
                slot.ora,
                slot.seats,
                slot.pcode,
            )
            job = try_lock_slot(session, cfg, slot)
            if job:
                return job, session, page
            logger.info("[locker] slot failed, next")

        if not cfg.poll_when_empty:
            raise RuntimeError("有库存但锁座全部失败（打码/座位冲突等）")
        logger.info("[locker] all slots failed, poll again")
        time.sleep(cfg.poll_interval_seconds)
