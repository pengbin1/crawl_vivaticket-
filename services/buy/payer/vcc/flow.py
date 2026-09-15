from __future__ import annotations

from pathlib import Path

from payer.vcc.amounts import cents_to_major, expiry_mm_yy, split_cardholder_name
from payer.vcc.client import VccClient
from payer.vcc.errors import VccError
from payer.vcc.models import VccPayContext
from payer.vcc.open_card import (
    build_client_request_id,
    fetch_sensitive_card,
    open_vcc_for_payment,
    recover_application,
)
from payer.vcc.otp import build_payment_id, utc_started_at
from payer.vcc.report import build_report_payload, submit_payment_report
from payer.vcc.store import VccStore
from shared.config import AppConfig, CardConfig, VccConfig
from shared.log import get_logger
from shared.models import PaymentJob

logger = get_logger(__name__)


def make_client(cfg: VccConfig) -> VccClient:
    return VccClient(
        cfg.base_url,
        expected_env=cfg.expected_env,
        timeout=cfg.timeout_seconds,
        client_cert=cfg.client_cert,
        client_key=cfg.client_key,
    )


def make_store(cfg: VccConfig, fallback_root: Path) -> VccStore:
    root = Path(cfg.persist_dir) if cfg.persist_dir else fallback_root / "vcc"
    return VccStore(root)


def prepare_vcc_card(
    job: PaymentJob,
    app_cfg: AppConfig,
    *,
    artifact_root: Path | None = None,
) -> VccPayContext:
    """
    Documented order:
      persist client_request_id → create → (refresh if needed) → sensitive-details
    Card PAN/CVV stay in the returned context only (process memory).
    """
    vcfg = app_cfg.vcc
    if not vcfg.enabled:
        raise VccError("VCC is disabled in config", code="vcc_disabled")
    if not vcfg.base_url:
        raise VccError("payment.vcc.base_url is required", code="config")
    if not vcfg.card_amount or not vcfg.card_currency:
        raise VccError(
            "payment.vcc.card_amount and card_currency are required (deploy VCC_CARD_*)",
            code="config",
        )
    if not vcfg.pnr_source:
        raise VccError(
            "payment.vcc.pnr_source is required (server profile; confirm with payment team)",
            code="config",
        )

    root = artifact_root or Path(app_cfg.raw.get("_artifact_root") or ".")
    client = make_client(vcfg)
    store = make_store(vcfg, root)

    client_request_id = (
        (job.vcc_client_request_id or "").strip()
        or build_client_request_id(job.custref, attempt=1)
    )
    if job.vcc_application_id or job.vcc_order_id:
        logger.info(
            "[VCC][准备开卡] 复用已有申请 application_id=%s order_id=%s "
            "client_request_id=%s（不换键重开）",
            job.vcc_application_id or "-",
            job.vcc_order_id or "-",
            client_request_id,
        )
    else:
        logger.info(
            "[VCC][准备开卡] custref=%s client_request_id=%s "
            "开卡额度=%s %s BIN=%s MCC=%s pnr_source=%s",
            job.custref,
            client_request_id,
            vcfg.card_amount,
            vcfg.card_currency,
            vcfg.card_bin,
            vcfg.allowed_merchant_categories or "(默认)",
            vcfg.pnr_source,
        )

    application = open_vcc_for_payment(
        client,
        store,
        client_request_id=client_request_id,
        amount=vcfg.card_amount,
        currency=vcfg.card_currency,
        card_bin=vcfg.card_bin,
        allowed_merchant_categories=vcfg.allowed_merchant_categories,
    )

    if not application.card_id:
        logger.warning(
            "[VCC][准备开卡] 尚无 card_id，进入刷新恢复 application_id=%s",
            application.application_id,
        )
        application = recover_application(client, store, application)
        if not application.card_id:
            raise VccError(
                "刷新后仍无 card_id",
                code="state_conflict",
                retryable=True,
            )

    logger.info(
        "[VCC][取卡] 开始拉取完整卡信息 application_id=%s order_id=%s "
        "（卡号/CVV仅内存，不写日志）",
        application.application_id,
        application.order_id,
    )
    sensitive = fetch_sensitive_card(client, application)
    first, last = split_cardholder_name(application.cardholder_name)
    card = CardConfig(
        number=sensitive.number,
        expiry=expiry_mm_yy(sensitive.expiry_month, sensitive.expiry_year),
        cvv=sensitive.cvv,
        first_name=first,
        last_name=last,
        email=application.cardholder_email,
    )

    cost_currency = vcfg.pay_currency or "EUR"
    if job.amount_cents > 0:
        cost_amount = cents_to_major(job.amount_cents, cost_currency)
    else:
        cost_amount = vcfg.fallback_cost_amount or vcfg.card_amount

    ctx = VccPayContext(
        application=application,
        sensitive=sensitive,
        card=card,
        payment_id=build_payment_id(job.custref),
        started_at=utc_started_at(),
        cost_amount=cost_amount,
        cost_currency=cost_currency,
    )
    logger.info(
        "[VCC][开卡完成] 可进入供应商支付 "
        "order_id=%s application_id=%s status=%s "
        "卡末四位=****%s 持卡人=%s payment_id=%s "
        "采购成本=%s %s",
        application.order_id,
        application.application_id,
        application.status,
        sensitive.last4(),
        application.cardholder_name or "-",
        ctx.payment_id,
        cost_amount,
        cost_currency,
    )
    return ctx


def report_after_success(
    job: PaymentJob,
    app_cfg: AppConfig,
    ctx: VccPayContext,
    *,
    artifact_root: Path | None = None,
    trade_no: str = "",
) -> dict:
    """Only call after supplier payment success is confirmed."""
    vcfg = app_cfg.vcc
    client = make_client(vcfg)
    root = artifact_root or Path(".")
    store = make_store(vcfg, root)

    logger.info(
        "[VCC][支付上报] 供应商已确认成功，开始上报 "
        "custref=%s vcc_order_id=%s cost=%s %s",
        job.custref,
        ctx.application.order_id,
        ctx.cost_amount,
        ctx.cost_currency,
    )
    payload = build_report_payload(
        job,
        pnr_source=vcfg.pnr_source,
        vcc_order_id=ctx.application.order_id,
        cost_amount=ctx.cost_amount,
        currency=ctx.cost_currency,
        trade_no=trade_no,
        sale_price=ctx.cost_amount,
        sale_currency=ctx.cost_currency,
    )
    ctx.report_payload = payload
    data = submit_payment_report(client, store, payload)
    ctx.purchase_id = str(data.get("purchase_id") or "")
    asset_ids = [str(a) for a in (data.get("asset_ids") or []) if a]
    logger.info(
        "[VCC][支付上报] 已受理 purchase_id=%s asset_ids=%s",
        ctx.purchase_id or "-",
        asset_ids,
    )

    loss_results: list = []
    if app_cfg.vcc.auto_confirm_loss and asset_ids:
        from payer.vcc.errors import VccError, VccManualRequired
        from payer.vcc.loss import confirm_losses_for_assets

        logger.info(
            "[VCC][损耗] auto_confirm_loss=true，对不可售/不退票资产报损 "
            "count=%s reason=%s",
            len(asset_ids),
            app_cfg.vcc.loss_reason,
        )
        try:
            loss_results = confirm_losses_for_assets(
                client,
                store,
                asset_ids=asset_ids,
                custref=job.custref,
                business_order_no=job.business_order_no,
                reason=app_cfg.vcc.loss_reason,
            )
        except (VccManualRequired, VccError) as exc:
            # Procurement already committed; loss failure must not undo that fact.
            logger.error(
                "[VCC][损耗] 报损失败（采购已成功，需人工补报） code=%s err=%s "
                "asset_ids=%s",
                getattr(exc, "code", "") or "-",
                exc,
                asset_ids,
            )
            loss_results = [
                {
                    "error": True,
                    "code": getattr(exc, "code", "") or "loss_failed",
                    "message": str(exc)[:300],
                    "asset_ids": asset_ids,
                }
            ]
    elif app_cfg.vcc.auto_confirm_loss and not asset_ids:
        logger.warning(
            "[VCC][损耗] auto_confirm_loss=true 但上报响应无 asset_ids，跳过报损"
        )

    data = {**data, "asset_ids": asset_ids, "loss_results": loss_results}
    return data
