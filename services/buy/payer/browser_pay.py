from __future__ import annotations

import time
from typing import TYPE_CHECKING

from DrissionPage import ChromiumPage

from locker.waf import build_chromium_options
from shared.config import AppConfig, CardConfig
from shared.deadline import assert_time_left, seconds_remaining
from shared.log import get_logger
from shared.models import PayResult, PaymentJob

if TYPE_CHECKING:
    from payer.vcc.client import VccClient
    from payer.vcc.models import VccPayContext

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
            ele.run_js(
                "this.dispatchEvent(new Event('input', {bubbles:true}));"
                "this.dispatchEvent(new Event('change', {bubbles:true}));"
                "this.dispatchEvent(new Event('blur', {bubbles:true}));"
            )
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
                logger.info("[浏览器支付] 已选择原币种 EUR（关闭 DCC）")
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
    logger.info("[浏览器支付] 点击支付按钮 text=%r", (btn.text or "")[:40])
    btn.click()


def _find_otp_input(page: ChromiumPage):
    locators = [
        "css:input[name*='otp' i]",
        "css:input[id*='otp' i]",
        "css:input[autocomplete='one-time-code']",
        "css:input[name*='password' i]",
        "css:input[placeholder*='OTP' i]",
        "css:input[placeholder*='codice' i]",
        "css:input[placeholder*='code' i]",
        "css:input[type='tel']",
        "css:input[maxlength='6']",
        "css:input[maxlength='8']",
    ]
    frames = [page]
    try:
        frames.extend(page.get_frames() or [])
    except Exception:
        pass
    for frame in frames:
        ele = try_find_any(frame, locators, timeout=1.5)
        if ele:
            return frame, ele
    return None, None


def _submit_otp_if_possible(target, page: ChromiumPage) -> None:
    btn = try_find_any(
        target,
        [
            "xpath://button[contains(., 'Confirm')]",
            "xpath://button[contains(., 'Conferma')]",
            "xpath://button[contains(., 'Submit')]",
            "xpath://button[contains(., 'Continue')]",
            "xpath://button[contains(., 'Continua')]",
            "css:button[type='submit']",
        ],
        timeout=3,
    )
    if btn:
        try:
            btn.click()
        except Exception:
            pass
        return
    try:
        page.actions.key_down("ENTER").key_up("ENTER")
    except Exception:
        pass


def step_handle_3ds_otp(
    page: ChromiumPage,
    job: PaymentJob,
    cfg: AppConfig,
    vcc_ctx: "VccPayContext",
    vcc_client: "VccClient",
) -> None:
    """Wait OTP after submit (register already done before click), fill without logging."""
    from payer.vcc.otp import wait_for_otp_code

    otp_deadline = time.time() + min(25, max(8, seconds_remaining(job.deadline_at) - 15))
    frame = None
    otp_input = None
    while time.time() < otp_deadline:
        if _looks_success(page):
            return
        frame, otp_input = _find_otp_input(page)
        if otp_input:
            break
        time.sleep(0.8)

    if not otp_input:
        logger.info("[浏览器支付] 未检测到 3DS 验证码输入框，继续等结果页")
        return

    started_epoch = time.time()
    try:
        from datetime import datetime

        started_epoch = datetime.fromisoformat(
            vcc_ctx.started_at.replace("Z", "+00:00")
        ).timestamp()
    except Exception:
        pass

    code = wait_for_otp_code(
        vcc_client,
        vcc_ctx.payment_id,
        overall_deadline=time.time() + max(5, seconds_remaining(job.deadline_at) - 5),
        started_at_epoch=started_epoch,
        max_age_seconds=cfg.vcc.otp_max_age_seconds,
    )
    try:
        otp_input.clear()
        otp_input.input(code)
    finally:
        del code
    _submit_otp_if_possible(frame or page, page)
    logger.info("[浏览器支付] 已提交 3DS 验证码（验证码本身不写日志）")


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
    *,
    vcc_ctx: "VccPayContext | None" = None,
    vcc_client: "VccClient | None" = None,
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
        logger.info(
            "[浏览器支付] 打开支付页 剩余=%.0fs 卡末四位=****%s",
            left,
            cfg.card.last4(),
        )
        page.get(url, timeout=60)

        if page.ele("#pmcreditcard", timeout=3):
            step_choose_credit_card(page)
            time.sleep(2.5)

        step_fill_card_info(page, cfg.card)
        time.sleep(1.0)
        step_select_dcc_if_present(page)

        if vcc_ctx is not None and vcc_client is not None:
            from payer.vcc.otp import register_otp_context

            register_otp_context(
                vcc_client,
                payment_id=vcc_ctx.payment_id,
                card_id=vcc_ctx.sensitive.card_id
                or (vcc_ctx.application.card_id or ""),
                card_last4=vcc_ctx.sensitive.last4(),
                amount=vcc_ctx.cost_amount,
                currency=vcc_ctx.cost_currency,
                started_at=vcc_ctx.started_at,
                expected_merchant=cfg.vcc.expected_merchant,
            )
            logger.info(
                "[浏览器支付] 提交前已登记 3DS 上下文 payment_id=%s "
                "vcc_order_id=%s",
                vcc_ctx.payment_id,
                vcc_ctx.application.order_id,
            )

        step_submit_pay(page)

        if vcc_ctx is not None and vcc_client is not None:
            step_handle_3ds_otp(page, job, cfg, vcc_ctx, vcc_client)

        end = time.time() + min(90, max(20, seconds_remaining(job.deadline_at) - 10))
        while time.time() < end:
            if _looks_success(page):
                logger.info("[浏览器支付] 检测到成功页 url=%s", page.url)
                return PayResult(
                    True,
                    "browser",
                    "已匹配支付成功页",
                    page.url,
                    confirmed=True,
                )
            time.sleep(1.0)

        return PayResult(
            False,
            "browser",
            "已提交支付，但未确认成功页（可能 3DS/异步）；请检查邮箱/订单",
            final_url=page.url or "",
            raw_hint="unverified",
            confirmed=False,
        )
    except Exception as exc:
        logger.exception("[浏览器支付] 失败")
        return PayResult(
            False, "browser", str(exc), final_url=getattr(page, "url", "") or ""
        )
    finally:
        if owns_page:
            try:
                page.quit()
            except Exception:
                pass
