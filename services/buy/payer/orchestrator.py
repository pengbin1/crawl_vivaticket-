from __future__ import annotations

from DrissionPage import ChromiumPage

from payer.browser_pay import browser_pay
from payer.http_pay import try_http_pay
from payer.secure_waf import bootstrap_secure_session
from shared.config import AppConfig
from shared.deadline import is_urgent, seconds_remaining
from shared.log import get_logger
from shared.models import PayResult, PaymentJob
from shared.notify import notify

logger = get_logger(__name__)


def pay_job(
    job: PaymentJob,
    cfg: AppConfig,
    page: ChromiumPage | None = None,
) -> PayResult:
    """
    Payment strategy (captured + practical):

    1) Optional HTTP: formtr → doauth(300) → Sella → Axerve URL
    2) Always finish card entry with Drission (final Axerve fulfill POST
       was never fully captured in the old project notes).

    prefer_browser=true  → skip step 1, open formtr in Drission directly
    prefer_browser=false → HTTP first to land on Axerve, then Drission fill
    """
    left = seconds_remaining(job.deadline_at)
    logger.info(
        "[payer] start custref=%s remaining=%.0fs prefer_browser=%s",
        job.custref,
        left,
        cfg.prefer_browser,
    )
    if left <= 0:
        result = PayResult(False, "none", "支付窗口已过期")
        _alert(cfg, job, result)
        return result

    start_url = job.payment_url

    if not cfg.prefer_browser and not is_urgent(
        job.deadline_at, cfg.urgent_remaining_seconds
    ):
        http_session = None
        secure_page = None
        try:
            http_session, secure_page = bootstrap_secure_session(
                job.payment_url,
                headless=cfg.headless,
                proxy=cfg.proxy,
            )
        except Exception as exc:
            logger.warning("[payer] secure WAF bootstrap failed: %s", exc)
        finally:
            if secure_page:
                try:
                    secure_page.quit()
                except Exception:
                    pass
        http_result = try_http_pay(job, cfg, http_session=http_session)
        if http_result.ok:
            _alert(cfg, job, http_result)
            return http_result
        if http_result.final_url:
            start_url = http_result.final_url
        logger.info("[payer] continue with Drission from %s (%s)", start_url, http_result.message)

    if is_urgent(job.deadline_at, cfg.urgent_remaining_seconds):
        logger.warning(
            "[payer] URGENT remaining=%.0fs",
            seconds_remaining(job.deadline_at),
        )

    result = browser_pay(job, cfg, page=page, start_url=start_url)
    _alert(cfg, job, result)
    return result


def _alert(cfg: AppConfig, job: PaymentJob, result: PayResult) -> None:
    status = "SUCCESS" if result.ok else "FAIL"
    text = (
        f"[cenacolo_buy] pay {status}\n"
        f"date={job.date} time={job.time}\n"
        f"custref={job.custref}\n"
        f"amount_cents={job.amount_cents}\n"
        f"method={result.method}\n"
        f"msg={result.message}\n"
        f"url={result.final_url or job.payment_url}\n"
        f"card=****{cfg.card.last4()}"
    )
    notify(text, cfg.feishu_webhook, cfg.feishu_secret)
