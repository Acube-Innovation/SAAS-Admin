"""GST tax invoice PDF (A4) with reportlab. Uses DejaVu Sans for the ₹ sign when it is installed."""

import io
import os

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.core.constants import STATE_CHOICES

STATES = dict(STATE_CHOICES)
ONES = [
    "Zero",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
]
TENS = ["_", "_", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _fonts():
    """(regular, bold, currency symbol). Falls back to Helvetica and "Rs." without the TTF."""
    path = settings.INVOICE_FONT_PATH
    bold = path.replace("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")
    if os.path.exists(path):
        if "Invoice" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("Invoice", path))
            pdfmetrics.registerFont(TTFont("Invoice-Bold", bold if os.path.exists(bold) else path))
        return "Invoice", "Invoice-Bold", "₹"
    return "Helvetica", "Helvetica-Bold", "Rs."


def _state_line(code: str, label: str = "State") -> str:
    return f"{label}: {STATES.get(code, code)} ({code})"


def rupees(paise: int, symbol: str = "₹") -> str:
    """Indian digit grouping: 123456789 paise -> ₹12,34,567.89."""
    whole, fraction = divmod(paise, 100)
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join([*groups, tail])
    return f"{symbol}{digits}.{fraction:02d}"


def _words(number: int) -> str:
    if number < 20:
        return ONES[number]
    if number < 100:
        tens, ones = divmod(number, 10)
        return TENS[tens] + (f" {ONES[ones]}" if ones else "")
    if number < 1000:
        hundreds, rest = divmod(number, 100)
        return f"{ONES[hundreds]} Hundred" + (f" {_words(rest)}" if rest else "")
    for size, name in ((10_000_000, "Crore"), (100_000, "Lakh"), (1000, "Thousand")):
        if number >= size:
            head, rest = divmod(number, size)
            return f"{_words(head)} {name}" + (f" {_words(rest)}" if rest else "")
    raise AssertionError


def amount_in_words(paise: int) -> str:
    """Indian numbering in words: 118000 paise -> "Rupees One Thousand One Hundred Eighty only"."""
    whole, fraction = divmod(paise, 100)
    text = f"Rupees {_words(whole)}"
    if fraction:
        text += f" and {_words(fraction)} Paise"
    return f"{text} only"


def render_invoice(invoice) -> ContentFile:
    regular, bold, symbol = _fonts()
    styles = {
        "title": ParagraphStyle("title", fontName=bold, fontSize=16, leading=20),
        "h": ParagraphStyle(
            "h", fontName=bold, fontSize=9, leading=12, textColor=colors.HexColor("#475569")
        ),
        "p": ParagraphStyle("p", fontName=regular, fontSize=9, leading=12),
        "right": ParagraphStyle(
            "right", fontName=regular, fontSize=9, leading=12, alignment=TA_RIGHT
        ),
        "small": ParagraphStyle(
            "small", fontName=regular, fontSize=8, leading=10, textColor=colors.HexColor("#64748b")
        ),
    }
    seller, buyer = invoice.seller, invoice.buyer
    money = lambda paise: rupees(paise, symbol)  # noqa: E731
    issued = timezone.localtime(invoice.issued_at).strftime("%d %b %Y")

    def block(lines):
        return [Paragraph(line, styles["p"]) for line in lines if line]

    story = [
        Table(
            [
                [
                    [Paragraph("TAX INVOICE", styles["title"]), Spacer(1, 4)]
                    + [Paragraph(f"<b>{seller['legal_name']}</b>", styles["p"])]
                    + block(
                        [
                            seller["address"],
                            f"GSTIN: {seller['gstin']}"
                            if seller["gstin"]
                            else "GSTIN: not registered",
                            _state_line(seller["state"]),
                            seller["email"],
                        ]
                    ),
                    [
                        Paragraph(f"Invoice no. <b>{invoice.number}</b>", styles["right"]),
                        Paragraph(f"Date: {issued}", styles["right"]),
                        Paragraph(
                            _state_line(invoice.place_of_supply, "Place of supply"), styles["right"]
                        ),
                        Paragraph(f"Financial year: {invoice.financial_year}", styles["right"]),
                    ],
                ]
            ],
            colWidths=[105 * mm, 70 * mm],  # the frame is 180 mm less 2 × 6 pt padding
            style=TableStyle(
                [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]
            ),
        ),
        Spacer(1, 8 * mm),
        Paragraph("BILL TO", styles["h"]),
        Paragraph(f"<b>{buyer['legal_name']}</b>", styles["p"]),
        *block(
            [
                buyer["address"],
                _state_line(buyer["state"]),
                f"GSTIN: {buyer['gstin']}" if buyer["gstin"] else "GSTIN: unregistered",
                buyer["email"],
            ]
        ),
        Spacer(1, 6 * mm),
    ]

    rows = [["#", "Description", "SAC", "Qty", "Rate", "Amount"]]
    for n, line in enumerate(invoice.lines, start=1):
        rows.append(
            [
                str(n),
                Paragraph(line["description"], styles["p"]),
                line["sac"],
                str(line["quantity"]),
                money(line["unit_paise"]),
                money(line["amount_paise"]),
            ]
        )
    rate = settings.GST_RATE_PERCENT
    totals = [["", "", "", "", "Taxable value", money(invoice.taxable_paise)]]
    if invoice.igst_paise:
        totals.append(["", "", "", "", f"IGST {rate}%", money(invoice.igst_paise)])
    else:
        totals.append(["", "", "", "", f"CGST {rate / 2:g}%", money(invoice.cgst_paise)])
        totals.append(["", "", "", "", f"SGST {rate / 2:g}%", money(invoice.sgst_paise)])
    totals.append(["", "", "", "", "Total", money(invoice.total_paise)])
    table = Table(
        rows + totals,
        colWidths=[8 * mm, 73 * mm, 18 * mm, 12 * mm, 30 * mm, 34 * mm],
        repeatRows=1,
    )
    last = len(rows) + len(totals) - 1
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), regular),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("FONTNAME", (0, 0), (-1, 0), bold),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#cbd5e1")),
                (
                    "LINEBELOW",
                    (0, len(rows) - 1),
                    (-1, len(rows) - 1),
                    0.5,
                    colors.HexColor("#cbd5e1"),
                ),
                ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTNAME", (4, last), (-1, last), bold),
                ("LINEABOVE", (4, last), (-1, last), 0.75, colors.HexColor("#0f172a")),
            ]
        )
    )
    story += [
        table,
        Spacer(1, 4 * mm),
        Paragraph(f"Amount in words: <b>{amount_in_words(invoice.total_paise)}</b>", styles["p"]),
    ]
    if invoice.payment_reference:
        story.append(Paragraph(f"Payment reference: {invoice.payment_reference}", styles["p"]))
    story += [
        Spacer(1, 10 * mm),
        Paragraph(
            "Tax is not payable on reverse charge. This is a computer-generated invoice and "
            "needs no signature.",
            styles["small"],
        ),
    ]

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"Invoice {invoice.number}",
        author=seller["legal_name"],
    )
    doc.build(story)
    return ContentFile(buffer.getvalue())
