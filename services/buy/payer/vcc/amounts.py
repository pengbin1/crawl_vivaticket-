from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def format_major(amount: Decimal | str | int | float, currency: str) -> str:
    """Format a major-unit amount as a decimal string for the given ISO currency."""
    cur = (currency or "").upper()
    value = Decimal(str(amount))
    if cur in {"KRW", "JPY", "VND"}:
        quantized = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        text = format(quantized, "f")
        if "." in text:
            raise ValueError(f"{cur} amount must be whole units, got {text}")
        return text
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(quantized, "f")


def cents_to_major(cents: int, currency: str = "EUR") -> str:
    """Convert integer minor units (e.g. EUR cents) to a VCC amount string."""
    cur = (currency or "").upper()
    if cur in {"KRW", "JPY", "VND"}:
        return format_major(cents, cur)
    return format_major(Decimal(cents) / Decimal(100), cur)


def split_cardholder_name(full: str) -> tuple[str, str]:
    name = (full or "").strip()
    if not name:
        return "", ""
    parts = name.split()
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], " ".join(parts[1:])


def expiry_mm_yy(month: str | int, year: str | int) -> str:
    mm = int(str(month).strip())
    yy = int(str(year).strip())
    if yy >= 100:
        yy = yy % 100
    return f"{mm:02d}/{yy:02d}"
