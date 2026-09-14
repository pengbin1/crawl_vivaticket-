from __future__ import annotations

import time

from DrissionPage import ChromiumPage

from locker.waf import build_chromium_options
from shared.config import AppConfig, CardConfig
from shared.deadline import assert_time_left, seconds_remaining
from shared.log import get_logger
from shared.models import PayResult, PaymentJob

logger = get_logger(__name__)


def wait_for(page: ChromiumPage, locator: str, timeout: float = 20.0):
    ele = page.ele(locator, timeout=timeout)
    if not ele:
        raise RuntimeError(f"超时未找到元素: {locator}")
    return ele


def try_find_any(page, locators: list[str], timeout: float = 15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for loc in locators:
            ele = page.ele(loc, timeout=0.3)
            if ele:
                return ele
        time.sleep(0.3)
    return None


def _switch_into_card_form_frame_if_any(page: ChromiumPage):
    frames = page.get_frames()
    if not frames:
        return None
    for frame in frames:
        try:
            if frame.ele("css:input[placeholder*='0000']", timeout=0.5):
                return frame
        except Exception:
            continue
    return None


def step_choose_credit_card(page: ChromiumPage) -> None:
    radio = wait_for(page, "#pmcreditcard", timeout=30)
    radio.click()
    pay_btn = try_find_any(
        page,
        [
            "tag:button@@text():PAY WITH CREDIT CARD",
            "tag:button@@text():Pay with credit card",
            "tag:a@@text():PAY WITH CREDIT CARD",
            "#creditcard_collapse button",
            "xpath://div[@id='creditcard_collapse']//button",
        ],
        timeout=20,
    )
    if not pay_btn:
        raise RuntimeError("没找到 PAY WITH CREDIT CARD 按钮")
    pay_btn.click()


def step_fill_card_info(page: ChromiumPage, card: CardConfig) -> None:
    deadline = time.time() + 45
    target = page
    card_input = None
    while time.time() < deadline:
        target = _switch_into_card_form_frame_if_any(page) or page
        card_input = target.ele("css:input[placeholder*='0000']", timeout=0.5)
        if card_input:
            break
        time.sleep(0.4)
    if not card_input:
        raise RuntimeError("超时未找到卡号输入框")

    def _fill(ele, value: str) -> None:
        ele.clear()
        ele.input(value)
        try:
            ele.run_js("this.dispatchEvent(new Event('input', {bubbles:true}));"
                       "this.dispatchEvent(new Event('change', {bubbles:true}));"
                       "this.dispatchEvent(new Event('blur', {bubbles:true}));")
        except Exception:
            pass

    _fill(card_input, card.number)
    time.sleep(0.5)

    expiry_input = try_find_any(
        target,
        [
            "css:input[placeholder*='MM/YY']",
            "css:input[placeholder*='MM/AA']",
            "css:input[name*='expir' i]",
            "css:input[id*='expir' i]",
            "css:input[autocomplete='cc-exp']",
        ],
        timeout=10,
    )
    if expiry_input and card.expiry:
        _fill(expiry_input, card.expiry)
        time.sleep(0.3)

    cvv_input = try_find_any(
        target,
        [
            "css:input[placeholder='000']",
            "css:input[placeholder*='CVV' i]",
            "css:input[name*='cvv' i]",
            "css:input[id*='cvv' i]",
            "css:input[autocomplete='cc-csc']",
        ],
        timeout=10,
    )
    if cvv_input and card.cvv:
        _fill(cvv_input, card.cvv)
        time.sleep(0.3)

    for locator, value in (
        (
            [
                "css:input[name*='first' i]",
                "css:input[placeholder*='Nome' i]",
                "css:input[placeholder*='First' i]",
            ],
            card.first_name,
        ),
        (
            [
                "css:input[name*='last' i]",
                "css:input[placeholder*='Cognome' i]",
                "css:input[placeholder*='Last' i]",
            ],
            card.last_name,
        ),
        (
            [
                "css:input[type='email']",
                "css:input[name*='email' i]",
                "css:input[placeholder*='mail' i]",
            ],
            card.email,
        ),
    ):
        if not value:
            continue
        ele = try_find_any(target, locator, timeout=5)
        if ele:
            try:
                ele.clear()
                ele.input(value)
            except Exception:
                pass


def step_select_dcc_if_present(page: ChromiumPage) -> None:
    """When DCC is offered, pick pay-in-original-currency (EUR) before submit."""
    for loc in (
        "css:label[for='dcc_off']",
        "css:#dcc_off",
        "css:input#dcc_off",
        "xpath://label[contains(., 'EUR')]",
    ):
        ele = page.ele(loc, timeout=1)
        if ele:
            try:
                ele.click()
                logger.info("[browser_pay] selected DCC EUR (dcc_off)")
                time.sleep(0.5)
                return
            except Exception:
                pass


def step_submit_pay(page: ChromiumPage) -> None:
    deadline = time.time() + 25
    btn = None
    while time.time() < deadline:
        target = _switch_into_card_form_frame_if_any(page) or page
        btn = try_find_any(
            target,
            [
                "xpath://button[contains(., 'Paga') and not(@disabled)]",
                "xpath://button[contains(., 'Pay') and not(@disabled)]",
                "tag:button@@text():Paga",
                "tag:button@@text():Pay",
                "css:button[type='submit']:not([disabled])",
            ],
            timeout=2,
        )
        if btn:
            try:
                if btn.attr("disabled") in (None, False, "false"):
                    break
            except Exception:
                break
        time.sleep(0.5)
    if not btn:
        btn = try_find_any(
            page,
            [
                "xpath://button[contains(., 'Paga')]",
                "xpath://button[contains(., 'Pay')]",
                "css:button[type='submit']",
            ],
            timeout=5,
        )
    if not btn:
        raise RuntimeError("未找到支付提交按钮")
    logger.info("[browser_pay] click pay btn text=%r", (btn.text or "")[:40])
    btn.click()


def _looks_success(page: ChromiumPage) -> bool:
    url = (page.url or "").lower()
    html = (page.html or "").lower()
    success_hints = (
        "payment successful",
        "pagamento riuscito",
        "thank you",
        "grazie",
        "esito=ok",
        "transaction approved",
        "ordine confermato",
    )
    fail_hints = (
        "declined",
        "failed",
        "error",
        "scaduta",
        "expired",
        "no longer available",
        "not allowed",
        "non disponibile",
        "rifiut",
    )
    if "receipttr.php" in url:
        if any(h in html for h in success_hints):
            return True
        if any(h in html for h in fail_hints):
            return False
    if any(h in url or h in html for h in success_hints):
        return True
    if any(h in url for h in fail_hints):
        return False
    return False


def browser_pay(
    job: PaymentJob,
    cfg: AppConfig,
    page: ChromiumPage | None = None,
    start_url: str | None = None,
) -> PayResult:
    assert_time_left(job.deadline_at, need_seconds=30)
    if not cfg.card.ready():
        return PayResult(
            False,
            "browser",
            "卡信息不完整（number/expiry/cvv），无法自动支付",
            final_url=job.payment_url,
        )

    owns_page = page is None
    if owns_page:
        page = ChromiumPage(build_chromium_options(cfg.headless, cfg.proxy))
    assert page is not None

    url = start_url or job.payment_url
    try:
        left = seconds_remaining(job.deadline_at)
        logger.info("[browser_pay] open url remaining=%.0fs ****%s", left, cfg.card.last4())
        page.get(url, timeout=60)

        # If still on formtr selection page
        if page.ele("#pmcreditcard", timeout=3):
            step_choose_credit_card(page)
            time.sleep(2.5)

        step_fill_card_info(page, cfg.card)
        time.sleep(1.0)
        step_select_dcc_if_present(page)
        step_submit_pay(page)

        # wait for navigation / result
        end = time.time() + min(90, max(20, seconds_remaining(job.deadline_at) - 10))
        while time.time() < end:
            if _looks_success(page):
                return PayResult(True, "browser", "payment success heuristics matched", page.url)
            time.sleep(1.0)

        # unknown outcome — treat as submitted but unverified
        return PayResult(
            False,
            "browser",
            "已提交支付，但未确认成功页（可能 3DS/异步）；请检查邮箱/订单",
            final_url=page.url or "",
            raw_hint="unverified",
        )
    except Exception as exc:
        logger.exception("[browser_pay] failed")
        return PayResult(False, "browser", str(exc), final_url=getattr(page, "url", "") or "")
    finally:
        if owns_page:
            try:
                page.quit()
            except Exception:
                pass
