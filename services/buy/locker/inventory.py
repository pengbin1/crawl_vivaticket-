from __future__ import annotations

import json
import re

import requests

from shared.config import AppConfig
from shared.log import get_logger
from shared.models import DayInventory, TimeSlot

logger = get_logger(__name__)

EVENTI_RE = re.compile(
    r"eventi\['(?P<event_id>\d+)'\]\.push\(new Array \("
    r"'(?P<tcode>[^']+)','(?P<pcode>[^']+)', "
    r"new Date \((?P<year>\d+), \((?P<month>\d+)-1\), (?P<day>\d+)\), "
    r"'(?P<calendar_code>[^']*)', (?P<num_unknown>\d+), '(?P<seats>[^']*)'\)\);"
)


def parse_calendar(html_text: str) -> list[DayInventory]:
    items: list[DayInventory] = []
    for m in EVENTI_RE.finditer(html_text):
        date_text = (
            f"{int(m.group('year')):04d}-"
            f"{int(m.group('month')):02d}-"
            f"{int(m.group('day')):02d}"
        )
        items.append(
            DayInventory(
                event_id=m.group("event_id"),
                date=date_text,
                tcode=m.group("tcode"),
                pcode=m.group("pcode"),
                seats=m.group("seats"),
                calendar_code=m.group("calendar_code"),
            )
        )
    return items


def positive_count(value: str) -> bool:
    return value.isdigit() and int(value) > 0


def select_days(items: list[DayInventory], cfg: AppConfig) -> list[DayInventory]:
    available = [i for i in items if positive_count(i.seats)]
    if cfg.use_any_available or not cfg.target_dates:
        return available
    wanted = set(cfg.target_dates)
    return [i for i in available if i.date in wanted]


def fetch_event_html(
    session: requests.Session,
    cfg: AppConfig,
    event_url: str | None = None,
) -> str:
    url = event_url or cfg.event_url
    resp = session.get(url, timeout=60)
    resp.raise_for_status()
    if "Incapsula" in resp.text and len(resp.text) < 1500:
        raise RuntimeError("活动页 Incapsula，需要重新 WAF")
    return resp.text


def fetch_time_slots(
    session: requests.Session,
    cfg: AppConfig,
    day: DayInventory,
    referer: str,
) -> list[TimeSlot]:
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": cfg.base,
        "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": session.headers.get("User-Agent", ""),
    }
    data = {
        "ajax": "1",
        "cal": "1",
        "tcode": day.tcode,
        "pcode": day.pcode,
        "seat-filter": "undefined",
    }
    resp = session.post(
        f"{cfg.base}/eventoWidgetTlite.php",
        headers=headers,
        data=data,
        timeout=60,
    )
    resp.raise_for_status()
    rows = json.loads(resp.text)
    slots: list[TimeSlot] = []
    for row in rows:
        code_text = str(row.get("code", "")).replace("&amp;", "&")
        t_m = re.search(r"tcode=([^&]+)", code_text)
        p_m = re.search(r"pcode=([^&]+)", code_text)
        if not t_m or not p_m:
            continue
        slots.append(
            TimeSlot(
                date=day.date,
                ora=str(row.get("ora", "")),
                seats=str(row.get("d", "0")),
                tcode=t_m.group(1),
                pcode=p_m.group(1),
            )
        )
    return slots


def find_available_slots(
    session: requests.Session,
    cfg: AppConfig,
    event_url: str | None = None,
    browser_html: str = "",
) -> tuple[str, list[TimeSlot]]:
    html_text = ""
    if browser_html and "eventi[" in browser_html:
        html_text = browser_html
        logger.info("[inventory] using browser html len=%s", len(html_text))
    else:
        html_text = fetch_event_html(session, cfg, event_url=event_url)
        logger.info("[inventory] fetched html len=%s", len(html_text))

    days = parse_calendar(html_text)
    logger.info("[inventory] calendar days=%s", len(days))
    candidates = select_days(days, cfg)
    logger.info("[inventory] days with seats=%s", len(candidates))

    # Fallback: if calendar all-zero but browser had seats, re-fetch with qubs URL
    if not candidates and event_url and event_url != cfg.event_url:
        html_text = fetch_event_html(session, cfg, event_url=event_url)
        days = parse_calendar(html_text)
        candidates = select_days(days, cfg)
        logger.info(
            "[inventory] refetch via qubs URL days=%s available=%s",
            len(days),
            len(candidates),
        )

    if not candidates:
        return html_text, []

    referer = event_url or cfg.event_url
    all_slots: list[TimeSlot] = []
    for day in candidates:
        slots = fetch_time_slots(session, cfg, day, referer)
        open_slots = [s for s in slots if positive_count(s.seats)]
        logger.info(
            "[inventory] %s times=%s open=%s", day.date, len(slots), len(open_slots)
        )
        all_slots.extend(open_slots)
    return html_text, all_slots
