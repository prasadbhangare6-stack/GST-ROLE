import re
from datetime import datetime

GSTIN_RE = re.compile(
    r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", re.I
)
DATE_RE = re.compile(
    r"\b("
    r"(?:\d{1,2}[/-]\d{1,2}[/-](?:\d{2}|\d{4}))"
    r"|"
    r"(?:\d{1,2}[-/ ](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[-/ ]\d{2,4})"
    r"|"
    r"(?:\d{1,2}[-/ ](?:January|February|March|April|May|June|July|August|September|October|November|December)[-/ ]\d{2,4})"
    r")\b",
    re.I
)
MONEY_VALUE = r"(?:₹|Rs\.?|INR)?\s*([0-9][0-9,]*(?:\.\d{1,2})?)"


def _money(value):
    if value is None:
        return None
    try:
        return float(value.replace(",", ""))
    except Exception:
        return None


def _lines(text):
    return [re.sub(r"\s+", " ", x).strip() for x in text.splitlines() if x.strip()]


def _label_value(text, labels):
    """
    Extract a monetary value associated with a label.

    Supports:
        IGST 32400
        IGST: ₹32,400.00
        IGST Rate (18%)
        ₹32,400.00

    The amount may be on the same line or on following lines.
    """

    lines = _lines(text)

    amount_re = re.compile(
        r"(?:₹|Rs\.?|INR)\s*"
        r"([0-9][0-9,]*(?:\.\d{1,2})?)"
        r"|"
        r"\b([0-9][0-9,]*\.[0-9]{1,2})\b"
        r"|"
        r"\b([0-9]{1,3}(?:,[0-9]{2,3})+)\b",
        re.I,
    )

    for label in labels:

        label_re = re.compile(
            rf"^\s*{label}",
            re.I
        )

        for index, line in enumerate(lines):

            if not label_re.search(line):
                continue

            # Same line
            for match in amount_re.finditer(line):

                raw_value = (
                    match.group(1)
                    or match.group(2)
                    or match.group(3)
                )

                value = _money(raw_value)

                if value is not None:
                    return value

            # Following lines
            for next_index in range(
                index + 1,
                min(index + 4, len(lines))
            ):

                next_line = lines[next_index]

                # Stop if another major financial field begins
                if re.match(
                    r"^(?:"
                    r"taxable|cgst|sgst|igst|"
                    r"total|grand\s+total|"
                    r"invoice|bank|account|"
                    r"place\s+of\s+supply"
                    r")\b",
                    next_line,
                    re.I,
                ):
                    break

                for match in amount_re.finditer(next_line):

                    raw_value = (
                        match.group(1)
                        or match.group(2)
                        or match.group(3)
                    )

                    value = _money(raw_value)

                    if value is not None:
                        return value

    return None

def _invoice_number(text):
    patterns = [
        r"(?im)\b(?:invoice|inv)\s*(?:no\.?|number|#)\s*[:#-]?\s*([A-Z0-9][A-Z0-9/_-]{2,})",
        r"(?im)\bbill\s*(?:no\.?|number|#)\s*[:#-]?\s*([A-Z0-9][A-Z0-9/_-]{2,})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(1).strip()
    return None


def _supplier(text):
    lines = _lines(text)

    banned = (
        "tax invoice",
        "invoice",
        "gstin",
        "bill to",
        "ship to",
        "date",
        "invoice no",
        "invoice number",
    )

    for line in lines[:15]:
        low = line.lower()

        # Ignore page markers added by our OCR/PDF extraction
        if re.fullmatch(r"-+\s*page\s+\d+\s*-+", low):
            continue

        if (
            len(line) >= 3
            and not any(x in low for x in banned)
            and not GSTIN_RE.search(line)
            and not re.fullmatch(r"[\d\W]+", line)
        ):
            return line[:120]

    return None


def _buyer_gstin(text):
    upper = text.upper()
    bill_pos = re.search(
        r"\bBILL\s+TO\b|\bBILLED\s+TO\b|\bBUYER\b|\bCUSTOMER\b", upper
    )
    if bill_pos:
        after = upper[bill_pos.end():]
        m = GSTIN_RE.search(after[:1000])
        if m:
            return m.group(0).upper()

    gstins = GSTIN_RE.findall(upper)
    return gstins[1].upper() if len(gstins) >= 2 else (gstins[0].upper() if gstins else None)


def _date(text):
    m = re.search(
        r"(?im)\b(?:invoice\s*)?date\s*[:#-]?\s*" + DATE_RE.pattern,
        text
    )

    raw = m.group(1) if m else (
        DATE_RE.search(text).group(1)
        if DATE_RE.search(text)
        else None
    )

    if not raw:
        return None

    for fmt in (
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d/%m/%y",
        "%d-%m-%y",
        "%d-%b-%Y",
        "%d-%b-%y",
        "%d %b %Y",
        "%d %b %y",
        "%d-%B-%Y",
        "%d-%B-%y",
        "%d %B %Y",
        "%d %B %y",
    ):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass

    return raw


def _line_items(text):
    lines = _lines(text)
    items = []

    # Find the item-table header
    start = None
    for i, line in enumerate(lines):
        if line.lower() == "s.no":
            start = i + 1
            break

    if start is None:
        return items

    i = start

    while i + 5 < len(lines):
        # Each item is represented by 6 consecutive lines:
        # S.No
        # Description
        # HSN/SAC
        # Qty
        # Rate
        # Taxable Value

        if not re.fullmatch(r"\d{1,3}", lines[i]):
            i += 1
            continue

        try:
            serial_no = int(lines[i])
            description = lines[i + 1]
            hsn_sac = lines[i + 2]
            quantity = float(lines[i + 3].replace(",", ""))
            rate = _money(lines[i + 4])
            taxable_value = _money(lines[i + 5])

            # Validate that this really looks like an item row
            if (
                re.fullmatch(r"\d{4,8}", hsn_sac)
                and rate is not None
                and taxable_value is not None
            ):
                items.append({
                    "serial_no": serial_no,
                    "description": description,
                    "hsn_sac": hsn_sac,
                    "quantity": quantity,
                    "rate": rate,
                    "taxable_value": taxable_value,
                })

                i += 6
                continue

        except (ValueError, TypeError):
            pass

        i += 1

    return items


def parse_invoice(text: str):
    taxable = _label_value(
        text,
        [
            r"total\s+taxable\s*(?:value|amount)",
            r"taxable\s*(?:value|amount)",
            r"subtotal",
            r"sub\s*total",
        ],
    )
    cgst = _label_value(text, [r"cgst(?:\s*amount)?"])
    sgst = _label_value(text, [r"sgst(?:\s*amount)?"])
    igst = _label_value(text, [r"igst(?:\s*amount)?"])

    total = _label_value(
        text,
        [
            r"total\s+invoice\s+value",
            r"grand\s+total",
            r"invoice\s+total",
            r"total\s*amount",
            r"amount\s*payable",
            r"total",
        ],
    )

    items = _line_items(text)

    if taxable is None and items:
        taxable = round(sum(i["taxable_value"] or 0 for i in items), 2)

    fields = {
        "supplier_name": _supplier(text),
        "gstin": _buyer_gstin(text),
        "invoice_number": _invoice_number(text),
        "invoice_date": _date(text),
        "taxable_value": taxable,
        "cgst": cgst,
        "sgst": sgst,
        "igst": igst,
        "total_amount": total,
        "items": items,
    }

    core_fields = [
        fields["supplier_name"],
        fields["gstin"],
        fields["invoice_number"],
        fields["invoice_date"],
        fields["taxable_value"],
        fields["total_amount"],
    ]
    confidence = round(sum(v is not None for v in core_fields) / len(core_fields), 2)

    math_ok = None
    taxes = sum(x or 0 for x in (cgst, sgst, igst))
    if taxable is not None and total is not None:
        math_ok = abs((taxable + taxes) - total) <= max(1.0, total * 0.005)

    items_math_ok = None
    if items and taxable is not None:
        item_sum = round(sum(i["taxable_value"] or 0 for i in items), 2)
        items_math_ok = abs(item_sum - taxable) <= max(1.0, taxable * 0.005)

    fields["confidence"] = confidence
    fields["math_ok"] = math_ok
    fields["items_math_ok"] = items_math_ok

    if math_ok is False or items_math_ok is False:
        fields["status"] = "needs_review"
    elif confidence >= 0.75:
        fields["status"] = "verified"
    else:
        fields["status"] = "needs_review"

    return fields
