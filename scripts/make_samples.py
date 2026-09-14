"""
Generate three fictional sample invoices for the demo sidebar.

NON-COMPETE INVARIANT #1: every vendor, address and org number below is
invented, and every document is a freight / wholesale / corporate-travel
invoice. Nothing here touches energy, utilities, metering or ESG.

    python scripts/make_samples.py

Produces in sample_invoices/:
    1_clean_freight.pdf     - tidy, machine-generated carrier invoice
    2_messy_wholesale.pdf   - 11 line items, mixed fonts, decimal commas,
                              a deliberate 3.00 SEK arithmetic error
    3_skewed_scan.pdf       - rotated, greyscale, speckled "phone photo" scan
"""

from __future__ import annotations

import random
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas

OUT = Path(__file__).resolve().parent.parent / "sample_invoices"
OUT.mkdir(parents=True, exist_ok=True)
W, H = A4


def _money(value: float, comma: bool = False) -> str:
    text = f"{value:,.2f}"
    if comma:  # Swedish convention: 1 450,00
        text = text.replace(",", " ").replace(".", ",")
    return text


# ---------------------------------------------------------------- sample 1
def clean_freight(path: Path) -> None:
    c = pdfcanvas.Canvas(str(path), pagesize=A4)
    c.setFillColor(colors.HexColor("#1b3a5c"))
    c.rect(0, H - 32 * mm, W, 32 * mm, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(20 * mm, H - 20 * mm, "NORDFRAKT LOGISTIK AB")
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, H - 26 * mm, "Hamngatan 14, 411 06 Goteborg, Sweden")

    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * mm, H - 46 * mm, "INVOICE")

    meta = [
        ("Invoice number", "NF-2026-04417"),
        ("Invoice date", "2026-08-21"),
        ("Due date", "2026-09-20"),
        ("VAT reg. no.", "SE556677889901"),
        ("Currency", "SEK"),
    ]
    y = H - 56 * mm
    for label, value in meta:
        c.setFont("Helvetica", 9)
        c.drawString(20 * mm, y, f"{label}:")
        c.setFont("Helvetica-Bold", 9)
        c.drawString(58 * mm, y, value)
        y -= 5 * mm

    c.setFont("Helvetica", 9)
    c.drawString(120 * mm, H - 56 * mm, "Bill to:")
    for i, line in enumerate(["Vastkust Handel AB", "Box 1182", "405 26 Goteborg"]):
        c.drawString(120 * mm, H - 61 * mm - i * 5 * mm, line)

    rows = [
        ("Road freight Gothenburg -> Malmo, 4 pallets", 4, 185.00, 740.00),
        ("Fuel surcharge 12%", 1, 88.80, 88.80),
        ("Waiting time, 1.5 h", 1.5, 420.00, 630.00),
        ("Pallet exchange fee", 4, 22.80, 91.20),
    ]
    y = H - 100 * mm
    c.setFillColor(colors.HexColor("#eef2f6"))
    c.rect(18 * mm, y - 2 * mm, W - 36 * mm, 8 * mm, stroke=0, fill=1)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 9)
    for x, head in ((20, "Description"), (118, "Qty"), (140, "Unit price"), (172, "Amount")):
        c.drawString(x * mm, y + 1 * mm, head)

    y -= 10 * mm
    c.setFont("Helvetica", 9)
    for desc, qty, unit, total in rows:
        c.drawString(20 * mm, y, desc)
        c.drawRightString(128 * mm, y, f"{qty:g}")
        c.drawRightString(160 * mm, y, _money(unit))
        c.drawRightString(190 * mm, y, _money(total))
        y -= 7 * mm

    y -= 4 * mm
    c.line(120 * mm, y, 190 * mm, y)
    y -= 7 * mm
    for label, value, bold in (("Subtotal", 1550.00, False),
                               ("VAT 25%", 387.50, False),
                               ("TOTAL DUE", 1937.50, True)):
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 11 if bold else 9)
        c.drawString(140 * mm, y, label)
        c.drawRightString(190 * mm, y, _money(value))
        y -= 7 * mm

    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(colors.grey)
    c.drawString(20 * mm, 20 * mm, "Payment terms: 30 days net. Bankgiro 123-4567.")
    c.drawString(20 * mm, 16 * mm, "Fictional sample document generated for demonstration purposes.")
    c.showPage()
    c.save()


# ---------------------------------------------------------------- sample 2
def messy_wholesale(path: Path) -> None:
    """Dense, ugly, decimal commas -- and the total is 3.00 SEK wrong on purpose."""
    c = pdfcanvas.Canvas(str(path), pagesize=A4)
    c.setFont("Courier-Bold", 14)
    c.drawString(18 * mm, H - 18 * mm, "SODRA PARTIHANDEL & GROSSIST HB")
    c.setFont("Courier", 8)
    c.drawString(18 * mm, H - 23 * mm, "Industrivagen 7 * 431 53 Molndal * Org.nr 969876-5432")
    c.drawString(18 * mm, H - 27 * mm, "Momsreg: SE969876543201")

    c.setFont("Helvetica-Bold", 11)
    c.drawString(18 * mm, H - 38 * mm, "FAKTURA / INVOICE   No. 2026-1183-B")
    c.setFont("Helvetica", 8)
    c.drawString(18 * mm, H - 43 * mm, "Fakturadatum 04.09.2026     Forfallodatum 04.10.2026     Valuta: SEK")

    rows = [
        ("Kaffebonor Arabica 1kg / Coffee beans", 24, 89.50),
        ("Papperspase 250-pack", 12, 145.00),
        ("Servetter vit 33cm, kartong", 8, 212.75),
        ("Engangsmugg 40cl, 1000 st", 6, 398.00),
        ("Ratt: Rengoringsmedel 5L", 4, 176.50),
        ("Handskar nitril M, 100-pack", 15, 94.25),
        ("Sopsackar 125L, rulle", 20, 67.80),
        ("Diskmedel koncentrat 5L", 5, 289.00),
        ("Toapapper 2-lagers, 36 rullar", 9, 178.90),
        ("Frakt / Freight", 1, 450.00),
        ("Miljoavgift / Environmental fee", 1, 35.00),
    ]

    y = H - 55 * mm
    c.setFont("Courier-Bold", 7.5)
    c.drawString(18 * mm, y, "ART  BENAMNING".ljust(58) + "ANTAL   A-PRIS      BELOPP")
    y -= 3 * mm
    c.line(18 * mm, y, 192 * mm, y)
    y -= 5 * mm

    subtotal = 0.0
    c.setFont("Courier", 7.5)
    for idx, (desc, qty, unit) in enumerate(rows, start=1):
        total = qty * unit
        subtotal += total
        c.drawString(18 * mm, y, f"{idx:03d}  {desc[:52]}")
        c.drawRightString(140 * mm, y, str(qty))
        c.drawRightString(163 * mm, y, _money(unit, comma=True))
        c.drawRightString(192 * mm, y, _money(total, comma=True))
        y -= 4.6 * mm

    vat = round(subtotal * 0.25, 2)
    # Deliberate transcription error: the printed total is 3.00 too high.
    printed_total = round(subtotal + vat + 3.00, 2)

    y -= 4 * mm
    c.line(120 * mm, y, 192 * mm, y)
    y -= 6 * mm
    c.setFont("Courier", 8)
    c.drawString(140 * mm, y, "Netto")
    c.drawRightString(192 * mm, y, _money(subtotal, comma=True))
    y -= 5 * mm
    c.drawString(140 * mm, y, "Moms 25%")
    c.drawRightString(192 * mm, y, _money(vat, comma=True))
    y -= 6 * mm
    c.setFont("Courier-Bold", 9.5)
    c.drawString(140 * mm, y, "ATT BETALA")
    c.drawRightString(192 * mm, y, _money(printed_total, comma=True))

    c.setFont("Helvetica-Oblique", 7)
    c.setFillColor(colors.grey)
    c.drawString(18 * mm, 14 * mm, "Fictional sample document generated for demonstration purposes.")
    c.showPage()
    c.save()
    print(f"    sample 2 printed total {printed_total:.2f} vs true "
          f"{subtotal + vat:.2f} (seeded 3.00 error for the audit to catch)")


# ---------------------------------------------------------------- sample 3
def skewed_scan(path: Path) -> None:
    """A corporate travel invoice rendered as a rotated, noisy greyscale scan."""
    from io import BytesIO

    from PIL import Image, ImageDraw, ImageFont
    from reportlab.lib.utils import ImageReader

    random.seed(7)
    page = Image.new("L", (1240, 1754), 246)
    draw = ImageDraw.Draw(page)

    def font(size: int, bold: bool = False):
        for name in (("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
                     ("Arial Bold.ttf" if bold else "Arial.ttf")):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    draw.text((90, 110), "ATLAS CORPORATE TRAVEL LTD", font=font(38, True), fill=25)
    draw.text((90, 160), "42 Fenchurch Street, London EC3M 4AB", font=font(20), fill=70)
    draw.text((90, 188), "VAT GB 412 8876 22", font=font(20), fill=70)

    draw.text((90, 265), "INVOICE  ATC-88213", font=font(30, True), fill=25)
    draw.text((90, 310), "Date: 12 August 2026        Terms: 14 days", font=font(20), fill=45)
    draw.text((90, 340), "Client: Vastkust Handel AB      Currency: GBP", font=font(20), fill=45)

    rows = [
        ("Return flight LHR-ARN, economy", "2", "214.00", "428.00"),
        ("Hotel Stockholm, 3 nights", "3", "156.50", "469.50"),
        ("Airport transfer", "4", "38.00", "152.00"),
        ("Booking service fee", "1", "45.00", "45.00"),
    ]
    y = 430
    draw.line((90, y - 14, 1150, y - 14), fill=140, width=2)
    draw.text((90, y), "DESCRIPTION", font=font(19, True), fill=25)
    draw.text((720, y), "QTY", font=font(19, True), fill=25)
    draw.text((830, y), "RATE", font=font(19, True), fill=25)
    draw.text((1010, y), "AMOUNT", font=font(19, True), fill=25)
    y += 40
    draw.line((90, y, 1150, y), fill=140, width=2)
    y += 22

    for desc, qty, rate, amount in rows:
        draw.text((90, y), desc, font=font(20), fill=35)
        draw.text((740, y), qty, font=font(20), fill=35)
        draw.text((830, y), rate, font=font(20), fill=35)
        draw.text((1020, y), amount, font=font(20), fill=35)
        y += 42

    y += 30
    draw.line((760, y, 1150, y), fill=140, width=2)
    y += 20
    for label, value, bold in (("Subtotal", "1,094.50", False),
                               ("VAT 20%", "218.90", False),
                               ("TOTAL", "1,313.40", True)):
        draw.text((790, y), label, font=font(22 if bold else 20, bold), fill=25)
        draw.text((1010, y), value, font=font(22 if bold else 20, bold), fill=25)
        y += 38

    draw.text((90, 1660), "Fictional sample document generated for demonstration purposes.",
              font=font(16), fill=120)

    # Scanner artefacts: speckle, rotation, soft edges.
    pixels = page.load()
    for _ in range(9000):
        x, y2 = random.randint(0, page.width - 1), random.randint(0, page.height - 1)
        pixels[x, y2] = max(0, pixels[x, y2] - random.randint(20, 90))
    page = page.rotate(-3.2, resample=Image.BICUBIC, expand=True, fillcolor=238)

    buf = BytesIO()
    page.convert("RGB").save(buf, format="JPEG", quality=72)
    buf.seek(0)

    c = pdfcanvas.Canvas(str(path), pagesize=A4)
    c.drawImage(ImageReader(buf), 0, 0, width=W, height=H, preserveAspectRatio=True, anchor="c")
    c.showPage()
    c.save()


if __name__ == "__main__":
    print("Generating fictional sample invoices...")
    clean_freight(OUT / "1_clean_freight.pdf")
    print("  [1/3] 1_clean_freight.pdf     (tidy carrier invoice, maths correct)")
    messy_wholesale(OUT / "2_messy_wholesale.pdf")
    print("  [2/3] 2_messy_wholesale.pdf   (11 rows, decimal commas, seeded error)")
    skewed_scan(OUT / "3_skewed_scan.pdf")
    print("  [3/3] 3_skewed_scan.pdf       (rotated greyscale phone-scan)")
    print(f"\nWritten to {OUT}")
