"""Dings VCC ticket API: open card → pay on supplier → OTP → report → optional loss."""

from payer.vcc.flow import prepare_vcc_card, report_after_success
from payer.vcc.loss import confirm_asset_loss, confirm_losses_for_assets
from payer.vcc.models import VccPayContext

__all__ = [
    "VccPayContext",
    "prepare_vcc_card",
    "report_after_success",
    "confirm_asset_loss",
    "confirm_losses_for_assets",
]
