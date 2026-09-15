from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from DrissionPage import ChromiumPage

from payer.browser_pay import browser_pay
from payer.http_pay import try_http_pay
from payer.secure_waf import bootstrap_secure_session
from shared.config import AppConfig
from shared.deadline import is_urgent, seconds_remaining
from shared.log import default_artifact_dir, get_logger
from shared.models import PayResult, PaymentJob
from shared.notify import notify

logger = get_logger(__name__)


def pay_job(
    job: PaymentJob,
    cfg: AppConfig,
    page: ChromiumPage | None = None,
    *,
    order_id: str | None = None,
    order_store: object | None = None,
) -> PayResult:
    """
    Payment strategy (documented VCC flow when payment.vcc.enabled):

    1) Persist client_request_id → create VCC → refresh if needed → sensitive-details
    2) Optional HTTP hop to Axerve, then browser fill with VCC cardholder/PAN/CVV
    3) Register OTP before submit → wait/fill 3DS if challenged
    4) Only after confirmed supplier success → POST /api/v2/procurement-payment-events

    When VCC is disabled, falls back to static payment.card (debug only).
    """
    left = seconds_remaining(job.deadline_at)
    logger.info(
        "[支付] 开始 custref=%s 剩余=%.0fs 优先浏览器=%s VCC开卡=%s",
        job.custref,
        left,
        cfg.prefer_browser,
        "开启" if cfg.vcc.enabled else "关闭(不会开卡)",
    )
    if left <= 0:
        result = PayResult(False, "none", "支付窗口已过期")
        _alert(cfg, job, result)
        return result

    vcc_ctx = None
    vcc_client = None
    pay_cfg = cfg
    artifacts = default_artifact_dir()

    if not cfg.vcc.enabled:
        logger.info(
            "[支付] VCC 未启用：跳过开卡/取卡/上报；"
            "如需走文档流程请把 payment.vcc.enabled 设为 true"
        )
    else:
        from payer.vcc.errors import VccError, VccManualRequired
        from payer.vcc.flow import make_client, prepare_vcc_card

        try:
            logger.info("[支付] VCC 已启用，开始开卡流程…")
            vcc_ctx = prepare_vcc_card(job, cfg, artifact_root=Path(artifacts))
            vcc_client = make_client(cfg.vcc)
            pay_cfg = replace(cfg, card=vcc_ctx.card)
            if order_store is not None and order_id:
                try:
                    from worker.orders import write_vcc_ids

                    write_vcc_ids(
                        order_store,
                        order_id,
                        client_request_id=vcc_ctx.application.client_request_id,
                        application_id=vcc_ctx.application.application_id,
                        order_id_vcc=vcc_ctx.application.order_id,
                        card_id=vcc_ctx.application.card_id or "",
                    )
                    logger.info(
                        "[支付] 已回写订单 VCC 标识 order_id=%s vcc_order_id=%s",
                        order_id,
                        vcc_ctx.application.order_id,
                    )
                except Exception as exc:
                    logger.warning("[支付] 回写 VCC 标识失败: %s", exc)
            logger.info(
                "[支付] 开卡就绪，进入供应商页面填卡 "
                "vcc_order_id=%s application_id=%s 末四位=****%s",
                vcc_ctx.application.order_id,
                vcc_ctx.application.application_id,
                vcc_ctx.sensitive.last4(),
            )
        except (VccManualRequired, VccError) as exc:
            logger.error(
                "[支付] 开卡/取卡失败 code=%s msg=%s",
                getattr(exc, "code", "") or "-",
                exc,
            )
            result = PayResult(
                False,
                "vcc",
                f"VCC开卡失败: {exc}",
                final_url=job.payment_url,
                raw_hint=getattr(exc, "code", "") or "vcc_error",
            )
            _alert(cfg, job, result)
            return result

    start_url = job.payment_url

    if not pay_cfg.prefer_browser and not is_urgent(
        job.deadline_at, pay_cfg.urgent_remaining_seconds
    ):
        http_session = None
        secure_page = None
        try:
            http_session, secure_page = bootstrap_secure_session(
                job.payment_url,
                headless=pay_cfg.headless,
                proxy=pay_cfg.proxy,
            )
        except Exception as exc:
            logger.warning("[支付] 安全域 WAF 引导失败: %s", exc)
        finally:
            if secure_page:
                try:
                    secure_page.quit()
                except Exception:
                    pass
        http_result = try_http_pay(job, pay_cfg, http_session=http_session)
        if http_result.ok and http_result.confirmed:
            return _finish(cfg, job, http_result, vcc_ctx, artifacts)
        if http_result.ok and not cfg.vcc.enabled:
            _alert(cfg, job, http_result)
            return http_result
        if http_result.final_url:
            start_url = http_result.final_url
        logger.info(
            "[支付] HTTP 未完成填卡，改用浏览器继续 url=%s 原因=%s",
            start_url,
            http_result.message,
        )

    if is_urgent(job.deadline_at, pay_cfg.urgent_remaining_seconds):
        logger.warning(
            "[支付] 窗口紧急 剩余=%.0fs，加快浏览器路径",
            seconds_remaining(job.deadline_at),
        )

    result = browser_pay(
        job,
        pay_cfg,
        page=page,
        start_url=start_url,
        vcc_ctx=vcc_ctx,
        vcc_client=vcc_client,
    )
    return _finish(cfg, job, result, vcc_ctx, artifacts)


def _finish(
    cfg: AppConfig,
    job: PaymentJob,
    result: PayResult,
    vcc_ctx,
    artifacts: Path,
) -> PayResult:
    if (
        cfg.vcc.enabled
        and vcc_ctx is not None
        and result.ok
        and result.confirmed
    ):
        from payer.vcc.errors import VccError, VccManualRequired
        from payer.vcc.flow import report_after_success

        try:
            data = report_after_success(
                job, cfg, vcc_ctx, artifact_root=Path(artifacts)
            )
            result.vcc_order_id = vcc_ctx.application.order_id
            result.purchase_id = str(data.get("purchase_id") or "")
            result.asset_ids = [str(a) for a in (data.get("asset_ids") or []) if a]
            result.loss_results = list(data.get("loss_results") or [])
            loss_note = ""
            if result.loss_results:
                loss_note = f"; 已损耗确认 {len(result.loss_results)} 张"
            result.message = (
                f"{result.message}; 已上报 purchase_id={result.purchase_id}"
                f"{loss_note}"
            )
            result.method = "vcc"
            logger.info(
                "[支付] 全流程成功 custref=%s vcc_order_id=%s purchase_id=%s "
                "asset_ids=%s loss=%s",
                job.custref,
                result.vcc_order_id,
                result.purchase_id,
                result.asset_ids,
                len(result.loss_results),
            )
        except (VccManualRequired, VccError) as exc:
            logger.error(
                "[支付] 供应商已成功但上报失败 code=%s vcc_order_id=%s err=%s",
                getattr(exc, "code", "") or "-",
                vcc_ctx.application.order_id,
                exc,
            )
            result = PayResult(
                False,
                "vcc",
                f"供应商支付成功但上报失败: {exc}",
                final_url=result.final_url,
                raw_hint=getattr(exc, "code", "") or "report_failed",
                confirmed=True,
                vcc_order_id=vcc_ctx.application.order_id,
            )
    elif cfg.vcc.enabled and result.ok and not result.confirmed:
        logger.warning(
            "[支付] 未确认成功页，跳过 VCC 上报（防误报） "
            "custref=%s vcc_order_id=%s msg=%s",
            job.custref,
            vcc_ctx.application.order_id if vcc_ctx else "-",
            result.message,
        )
        result = PayResult(
            False,
            result.method,
            result.message + "（未确认成功，已跳过VCC上报）",
            final_url=result.final_url,
            raw_hint=result.raw_hint or "unverified",
            confirmed=False,
            vcc_order_id=vcc_ctx.application.order_id if vcc_ctx else "",
        )
    else:
        logger.info(
            "[支付] 结束 ok=%s method=%s msg=%s",
            result.ok,
            result.method,
            result.message,
        )

    _alert(cfg, job, result)
    return result


def _alert(cfg: AppConfig, job: PaymentJob, result: PayResult) -> None:
    status = "成功" if result.ok else "失败"
    card_hint = (
        f"vcc_order={result.vcc_order_id or '-'}"
        if cfg.vcc.enabled
        else f"card=****{cfg.card.last4()}"
    )
    text = (
        f"[cenacolo_buy] 支付{status}\n"
        f"日期={job.date} 场次={job.time}\n"
        f"custref={job.custref}\n"
        f"金额分={job.amount_cents}\n"
        f"方式={result.method}\n"
        f"说明={result.message}\n"
        f"url={result.final_url or job.payment_url}\n"
        f"{card_hint}\n"
        f"purchase_id={result.purchase_id or '-'}"
    )
    notify(text, cfg.feishu_webhook, cfg.feishu_secret)
