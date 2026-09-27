from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import re
from typing import Any


GSTIN_RE = re.compile(
    r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$",
    re.IGNORECASE,
)


@dataclass
class VerificationResult:
    status: str
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, bool | None] = field(default_factory=dict)

    @property
    def verified(self) -> bool:
        return self.status == "verified"


def _money(value: Any) -> float | None:
    if value is None:
        return None

    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _close(
    a: float | None,
    b: float | None,
) -> bool | None:

    if a is None or b is None:
        return None

    tolerance = max(
        1.0,
        abs(b) * 0.005,
    )

    return abs(a - b) <= tolerance


def validate_gstin(
    gstin: str | None,
) -> bool | None:
    """
    Structural GSTIN validation.

    This validates the GSTIN format only.
    It does not perform a government portal lookup.
    """

    if not gstin:
        return None

    gstin = gstin.strip().upper()

    if not GSTIN_RE.fullmatch(gstin):
        return False

    return True


def validate_required_fields(
    invoice: dict[str, Any],
) -> list[str]:

    reasons = []

    required_fields = {
        "supplier_name": "Supplier name could not be extracted",
        "gstin": "GSTIN could not be extracted",
        "invoice_number": "Invoice number could not be extracted",
        "invoice_date": "Invoice date could not be extracted",
        "taxable_value": "Taxable value could not be extracted",
        "total_amount": "Invoice total could not be extracted",
    }

    for field_name, reason in required_fields.items():

        value = invoice.get(field_name)

        if value is None:
            reasons.append(reason)
            continue

        if isinstance(value, str) and not value.strip():
            reasons.append(reason)

    return reasons


def validate_invoice_date(
    invoice_date: str | None,
) -> tuple[bool | None, str | None]:

    if not invoice_date:
        return None, None

    try:
        date.fromisoformat(invoice_date)
        return True, None

    except ValueError:
        return (
            False,
            "Invoice date is invalid",
        )


def validate_tax_structure(
    invoice: dict[str, Any],
) -> tuple[bool | None, list[str]]:

    cgst = _money(
        invoice.get("cgst")
    )

    sgst = _money(
        invoice.get("sgst")
    )

    igst = _money(
        invoice.get("igst")
    )

    reasons = []

    has_cgst = cgst is not None
    has_sgst = sgst is not None
    has_igst = igst is not None

    # --------------------------------------------------------
    # Actual structural conflict
    # --------------------------------------------------------

    if has_igst and (
        has_cgst or has_sgst
    ):

        reasons.append(
            "IGST and CGST/SGST are both present"
        )

        return False, reasons

    # --------------------------------------------------------
    # Actual structural conflict
    # --------------------------------------------------------

    if has_cgst != has_sgst:

        reasons.append(
            "CGST and SGST are not both present"
        )

        return False, reasons

    # --------------------------------------------------------
    # Missing tax information is an extraction/review issue.
    #
    # Do NOT call it a tax validation failure.
    # --------------------------------------------------------

    if not has_igst and not (
        has_cgst and has_sgst
    ):

        reasons.append(
            "GST tax amounts could not be extracted"
        )

        return None, reasons

    return True, reasons


def validate_invoice_math(
    invoice: dict[str, Any],
) -> tuple[bool | None, str | None]:

    taxable = _money(
        invoice.get("taxable_value")
    )

    cgst = _money(
        invoice.get("cgst")
    )

    sgst = _money(
        invoice.get("sgst")
    )

    igst = _money(
        invoice.get("igst")
    )

    total = _money(
        invoice.get("total_amount")
    )

    # --------------------------------------------------------
    # Cannot verify arithmetic without taxable or total.
    # --------------------------------------------------------

    if taxable is None or total is None:

        return None, None

    # --------------------------------------------------------
    # Cannot perform a complete GST reconciliation when
    # tax amounts were not extracted.
    # --------------------------------------------------------

    if (
        cgst is None
        and sgst is None
        and igst is None
    ):

        return (
            None,
            "Invoice arithmetic could not be verified "
            "because GST amounts are missing",
        )

    cgst = cgst or 0.0
    sgst = sgst or 0.0
    igst = igst or 0.0

    expected_total = round(
        taxable + cgst + sgst + igst,
        2,
    )

    if _close(
        expected_total,
        total,
    ):

        return True, None

    return (
        False,
        (
            "Invoice total does not reconcile with "
            "taxable value + GST"
        ),
    )


def validate_line_items(
    invoice: dict[str, Any],
) -> tuple[bool | None, list[str]]:

    items = invoice.get("items") or []

    taxable = _money(
        invoice.get("taxable_value")
    )

    if not items:
        return None, []

    reasons = []

    item_total = 0.0

    for index, item in enumerate(
        items,
        start=1,
    ):

        quantity = _money(
            item.get("quantity")
        )

        rate = _money(
            item.get("rate")
        )

        line_taxable = _money(
            item.get("taxable_value")
        )

        # ----------------------------------------------------
        # Missing line data = extraction/review issue
        # ----------------------------------------------------

        if quantity is None:

            reasons.append(
                f"Line item {index}: quantity could not be extracted"
            )

            continue

        if rate is None:

            reasons.append(
                f"Line item {index}: rate could not be extracted"
            )

            continue

        if line_taxable is None:

            reasons.append(
                f"Line item {index}: taxable value "
                f"could not be extracted"
            )

            continue

        # ----------------------------------------------------
        # Actual arithmetic validation
        # ----------------------------------------------------

        expected_line_value = round(
            quantity * rate,
            2,
        )

        if not _close(
            expected_line_value,
            line_taxable,
        ):

            reasons.append(
                f"Line item {index}: quantity ? rate "
                f"does not match taxable value"
            )

        item_total += line_taxable

    # --------------------------------------------------------
    # Compare line-item total with invoice taxable value
    # --------------------------------------------------------

    if taxable is not None:

        item_total = round(
            item_total,
            2,
        )

        if not _close(
            item_total,
            taxable,
        ):

            reasons.append(
                "Line-item taxable values do not reconcile "
                "with invoice taxable value"
            )

    if reasons:

        return False, reasons

    return True, []


def verify_invoice(
    invoice: dict[str, Any],
) -> VerificationResult:

    reasons: list[str] = []

    checks: dict[str, bool | None] = {}

    # ========================================================
    # Required fields
    # ========================================================

    required_reasons = validate_required_fields(
        invoice
    )

    checks["required_fields"] = (
        not required_reasons
    )

    reasons.extend(
        required_reasons
    )

    # ========================================================
    # GSTIN
    # ========================================================

    gstin_result = validate_gstin(
        invoice.get("gstin")
    )

    checks["gstin_format"] = (
        gstin_result
    )

    if gstin_result is False:

        reasons.append(
            "GSTIN format is invalid"
        )

    # ========================================================
    # Invoice date
    # ========================================================

    date_ok, date_reason = (
        validate_invoice_date(
            invoice.get("invoice_date")
        )
    )

    checks["invoice_date"] = date_ok

    if date_reason:

        reasons.append(
            date_reason
        )

    # ========================================================
    # Tax structure
    # ========================================================

    tax_ok, tax_reasons = (
        validate_tax_structure(
            invoice
        )
    )

    checks["tax_structure"] = tax_ok

    reasons.extend(
        tax_reasons
    )

    # ========================================================
    # Invoice arithmetic
    # ========================================================

    math_ok, math_reason = (
        validate_invoice_math(
            invoice
        )
    )

    checks["invoice_math"] = math_ok

    if math_reason:

        reasons.append(
            math_reason
        )

    # ========================================================
    # Line-item arithmetic
    # ========================================================

    items_ok, item_reasons = (
        validate_line_items(
            invoice
        )
    )

    checks["line_items"] = items_ok

    reasons.extend(
        item_reasons
    )

    # ========================================================
    # Final decision
    #
    # Any unresolved extraction issue OR actual validation
    # failure keeps the invoice in needs_review.
    # ========================================================

    if reasons:

        status = "needs_review"

    else:

        status = "verified"

    return VerificationResult(
        status=status,
        reasons=reasons,
        checks=checks,
    )
