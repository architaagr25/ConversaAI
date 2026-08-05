"""
Builds the sample PDF sources the extraction pipeline is tested against.

Three real documents and one broken one:

  policy wording    prose with running headers, footers, page numbers and
                    clause numbering, which is what a parser has to strip
  rate table        figures laid out as tables, which lose their meaning when
                    flattened into a line of text
  agreement terms   the Indonesian equivalent, so the parser is exercised on
                    non-English content and different number formatting
  truncated file    a PDF cut off part way through, to prove a bad source is
                    reported and skipped rather than stopping the run

The documents are generated rather than committed as binaries so that the
content is reviewable in version control and can be regenerated if a test needs
different material.

Usage:
    .venv\\Scripts\\python scripts/build_sample_pdfs.py
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "raw" / "documents"

styles = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9.5, leading=13,
                      spaceAfter=6)
H1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=14, spaceAfter=10)
H2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11, spaceBefore=10,
                    spaceAfter=6)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, textColor=colors.grey)


def _decorate(canvas, doc, title: str, reference: str) -> None:
    """Draw the running header and footer that every page carries.

    These repeat on all pages and are exactly the kind of text that has to be
    removed before the content is usable, so they are here on purpose.
    """
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.grey)
    canvas.drawString(20 * mm, 285 * mm, "Solara Finance Group")
    canvas.drawRightString(190 * mm, 285 * mm, title)
    canvas.line(20 * mm, 283 * mm, 190 * mm, 283 * mm)
    canvas.line(20 * mm, 18 * mm, 190 * mm, 18 * mm)
    canvas.drawString(20 * mm, 13 * mm, reference)
    canvas.drawRightString(190 * mm, 13 * mm, f"Page {doc.page}")
    canvas.drawCentredString(105 * mm, 13 * mm, "Confidential")
    canvas.restoreState()


def build(path: Path, title: str, reference: str, story: list) -> None:
    doc = BaseDocTemplate(str(path), pagesize=A4,
                          leftMargin=20 * mm, rightMargin=20 * mm,
                          topMargin=28 * mm, bottomMargin=24 * mm,
                          title=title, author="Solara Finance Group")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([
        PageTemplate(id="all", frames=[frame],
                     onPage=lambda c, d: _decorate(c, d, title, reference))
    ])
    doc.build(story)
    print(f"  wrote {path.name}")


def table(data: list[list[str]], widths: list[float]) -> Table:
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


# --- Policy wording ---------------------------------------------------------


def policy_wording() -> list:
    s: list = []
    s.append(Paragraph("Solara Health Shield: Policy Wording", H1))
    s.append(Paragraph("Document reference SHS-PW-2026-03. Effective 1 March 2026. "
                       "This document sets out the terms of cover and takes precedence "
                       "over any summary, brochure or advertisement.", BODY))

    s.append(Paragraph("1. Definitions", H2))
    s.append(Paragraph("1.1 <b>Pre-existing condition</b> means any injury, illness, condition or "
                       "symptom for which the Member received treatment, took medication, sought "
                       "advice, or of which the Member was reasonably aware, before the "
                       "Commencement Date.", BODY))
    s.append(Paragraph("1.2 <b>Waiting Period</b> means a continuous period of membership that must "
                       "elapse before a benefit becomes payable.", BODY))
    s.append(Paragraph("1.3 <b>Accredited Hospital</b> means a hospital listed in the Solara "
                       "accredited network as amended from time to time.", BODY))
    s.append(Paragraph("1.4 <b>Commencement Date</b> means the first day of the month following "
                       "approval of the application and receipt of the first premium.", BODY))

    s.append(Paragraph("2. Waiting periods", H2))
    s.append(Paragraph("2.1 No Waiting Period applies to treatment required as a direct result of an "
                       "Accident occurring on or after the Commencement Date.", BODY))
    s.append(Paragraph("2.2 A Waiting Period of thirty (30) days applies to all illness claims.", BODY))
    s.append(Paragraph("2.3 A Waiting Period of twenty-four (24) months applies to any Pre-existing "
                       "Condition declared at application. A Pre-existing Condition which was not "
                       "declared is excluded for the lifetime of the policy.", BODY))
    s.append(Paragraph("2.4 A Waiting Period of twelve (12) months applies to cataract surgery, "
                       "hernia repair, removal of tonsils or adenoids, and treatment of "
                       "haemorrhoids.", BODY))
    s.append(Paragraph("2.5 A Waiting Period of ten (10) months applies to maternity benefits where "
                       "the Maternity Rider is attached.", BODY))
    s.append(Paragraph("2.6 Waiting Periods run from the Commencement Date and are not restarted on "
                       "renewal, provided cover has been continuous. A lapse of more than thirty "
                       "(30) days restarts all Waiting Periods.", BODY))

    s.append(Paragraph("3. General exclusions", H2))
    s.append(Paragraph("3.1 No benefit is payable for cosmetic or aesthetic procedures, other than "
                       "reconstructive surgery required following an Accident covered under this "
                       "policy.", BODY))
    s.append(Paragraph("3.2 Dental treatment is excluded on all plan levels, other than "
                       "reconstructive dental work required following a covered Accident.", BODY))
    s.append(Paragraph("3.3 Pregnancy, childbirth and fertility treatment are excluded unless the "
                       "Maternity Rider is attached and its Waiting Period has elapsed.", BODY))
    s.append(Paragraph("3.4 Self-inflicted injury, and injury sustained while committing or "
                       "attempting to commit an offence, are excluded.", BODY))
    s.append(Paragraph("3.5 Treatment received outside the Republic of the Philippines is excluded "
                       "unless the International Treatment Rider is attached.", BODY))

    s.append(PageBreak())

    s.append(Paragraph("4. Premiums and grace period", H2))
    s.append(Paragraph("4.1 Premiums are payable in advance on the due date stated in the Schedule.", BODY))
    s.append(Paragraph("4.2 A grace period of thirty-one (31) days is allowed from each due date. "
                       "Cover continues during the grace period.", BODY))
    s.append(Paragraph("4.3 If premium remains unpaid at the end of the grace period the policy "
                       "lapses with effect from the original due date, and no benefit is payable in "
                       "respect of any event occurring after that date.", BODY))
    s.append(Paragraph("4.4 A lapsed policy may be reinstated within six (6) months of the lapse "
                       "date on payment of all arrears and submission of a fresh declaration of "
                       "health. Reinstatement is at the sole discretion of Solara.", BODY))

    s.append(Paragraph("5. Eligibility", H2))
    s.append(Paragraph("5.1 Entry age is eighteen (18) to sixty (60) years at the Commencement Date.", BODY))
    s.append(Paragraph("5.2 Cover is renewable annually to age seventy (70).", BODY))
    s.append(Paragraph("5.3 Dependants may be added from age six (6) months to twenty-one (21) "
                       "years. A dependant reaching twenty-two (22) must take out a separate "
                       "policy.", BODY))
    s.append(Paragraph("5.4 The Member must be a resident of the Republic of the Philippines.", BODY))

    s.append(Paragraph("6. Claims", H2))
    s.append(Paragraph("6.1 For a planned admission, Solara must be notified at least three (3) "
                       "working days in advance so that a Letter of Guarantee may be issued.", BODY))
    s.append(Paragraph("6.2 For an emergency admission, Solara must be notified within "
                       "twenty-four (24) hours.", BODY))
    s.append(Paragraph("6.3 A reimbursement claim must be submitted within ninety (90) days of "
                       "discharge, with original official receipts and the discharge summary.", BODY))
    s.append(Paragraph("6.4 Where treatment is received at a hospital outside the accredited "
                       "network, reimbursement is limited to the amount that would have been "
                       "payable at an Accredited Hospital for the same treatment.", BODY))

    s.append(Paragraph("7. Cancellation", H2))
    s.append(Paragraph("7.1 The Member may cancel within fifteen (15) days of receipt of the policy "
                       "documents and receive a full refund, provided no claim has been made.", BODY))
    s.append(Paragraph("7.2 After that period, cancellation takes effect at the next renewal date. "
                       "Premiums already paid are not refundable.", BODY))

    s.append(Spacer(1, 8))
    s.append(Paragraph("Solara Insurance Corporation. Registered office 18F Meridian Tower, "
                       "Bonifacio Global City, Taguig. This wording supersedes SHS-PW-2025-09.", SMALL))
    return s


# --- Rate table -------------------------------------------------------------


def rate_table() -> list:
    s: list = []
    s.append(Paragraph("Solara Health Shield: Premium Rate Table", H1))
    s.append(Paragraph("Document reference SHS-RT-2026-03. Effective 1 March 2026. "
                       "Rates are monthly, in Philippine Pesos, for a single principal member "
                       "paying monthly. Annual payment attracts a 5 per cent discount.", BODY))

    s.append(Paragraph("Standard monthly premium by entry age", H2))
    s.append(table([
        ["Entry age", "Essential", "Plus", "Max"],
        ["18 to 25", "1,180", "2,340", "4,920"],
        ["26 to 30", "1,290", "2,560", "5,380"],
        ["31 to 35", "1,470", "2,910", "6,120"],
        ["36 to 40", "1,760", "3,480", "7,340"],
        ["41 to 45", "2,180", "4,310", "9,080"],
        ["46 to 50", "2,840", "5,620", "11,840"],
        ["51 to 55", "3,910", "7,740", "16,300"],
        ["56 to 60", "5,470", "10,830", "22,810"],
    ], [35 * mm, 35 * mm, 35 * mm, 35 * mm]))

    s.append(Paragraph("Dependant monthly premium", H2))
    s.append(table([
        ["Dependant type", "Essential", "Plus", "Max"],
        ["Spouse, 18 to 40", "1,410", "2,790", "5,880"],
        ["Spouse, 41 to 60", "2,620", "5,180", "10,920"],
        ["Child, 6 months to 17", "740", "1,470", "3,090"],
        ["Child, 18 to 21", "980", "1,940", "4,080"],
    ], [45 * mm, 30 * mm, 30 * mm, 30 * mm]))

    s.append(Paragraph("Rider monthly premium", H2))
    s.append(table([
        ["Rider", "Essential", "Plus", "Max", "Waiting period"],
        ["Maternity", "Not available", "1,120", "1,980", "10 months"],
        ["International treatment", "Not available", "890", "1,640", "30 days"],
        ["Critical illness", "620", "1,240", "2,310", "90 days"],
        ["Daily hospital cash", "310", "580", "1,040", "30 days"],
    ], [40 * mm, 26 * mm, 22 * mm, 22 * mm, 30 * mm]))

    s.append(Paragraph("Loadings", H2))
    s.append(Paragraph("Where underwriting indicates increased risk, a loading is applied to the "
                       "standard premium above. Loadings are expressed as a percentage of the "
                       "standard rate.", BODY))
    s.append(table([
        ["Assessment", "Loading"],
        ["Standard", "0%"],
        ["Mild, controlled condition declared", "25%"],
        ["Moderate condition declared", "50%"],
        ["Significant condition declared", "100%"],
        ["Occupational hazard class 3", "35%"],
        ["Occupational hazard class 4", "75%"],
    ], [70 * mm, 30 * mm]))

    s.append(Spacer(1, 6))
    s.append(Paragraph("Rates are guaranteed for the policy year and are reviewed annually. "
                       "This table supersedes SHS-RT-2025-09. Quotations must be produced from this "
                       "table; figures quoted in marketing material are illustrative only.", SMALL))
    return s


# --- Indonesian agreement terms ---------------------------------------------


def agreement_terms() -> list:
    s: list = []
    s.append(Paragraph("Solara Multifinance Indonesia: Ketentuan Perjanjian Pembiayaan", H1))
    s.append(Paragraph("Nomor dokumen SMI-KP-2026-03. Berlaku sejak 1 Maret 2026. "
                       "Dokumen ini memuat ketentuan yang mengikat antara Solara Multifinance "
                       "Indonesia dan Nasabah.", BODY))

    s.append(Paragraph("Pasal 1: Kewajiban Pembayaran", H2))
    s.append(Paragraph("1.1 Nasabah wajib membayar angsuran setiap bulan pada tanggal jatuh tempo "
                       "yang tercantum dalam Perjanjian.", BODY))
    s.append(Paragraph("1.2 Apabila tanggal jatuh tempo jatuh pada hari libur nasional, pembayaran "
                       "dapat dilakukan pada hari kerja berikutnya tanpa dikenakan denda.", BODY))
    s.append(Paragraph("1.3 Pembayaran dianggap sah pada saat dana diterima dan terverifikasi di "
                       "rekening Solara, bukan pada saat transfer dilakukan.", BODY))

    s.append(Paragraph("Pasal 2: Denda Keterlambatan", H2))
    s.append(Paragraph("2.1 Denda keterlambatan sebesar 0,5 persen per hari dihitung dari jumlah "
                       "angsuran yang tertunggak.", BODY))
    s.append(Paragraph("2.2 Denda dihitung sejak hari pertama setelah tanggal jatuh tempo.", BODY))
    s.append(Paragraph("2.3 Total denda tidak melebihi 30 persen dari nilai satu angsuran.", BODY))

    s.append(Paragraph("Pasal 3: Tahapan Penagihan", H2))
    s.append(table([
        ["Keterlambatan", "Tindakan"],
        ["1 sampai 7 hari", "Pengingat melalui pesan singkat dan telepon"],
        ["8 sampai 29 hari", "Penagihan melalui telepon oleh tim penagihan"],
        ["30 hari", "Kunjungan lapangan dapat dilakukan"],
        ["60 hari", "Surat Peringatan Pertama diterbitkan"],
        ["75 hari", "Surat Peringatan Kedua diterbitkan"],
        ["90 hari", "Penarikan kendaraan sesuai ketentuan Perjanjian"],
    ], [40 * mm, 105 * mm]))

    s.append(Paragraph("Pasal 4: Pelunasan Dipercepat", H2))
    s.append(Paragraph("4.1 Nasabah dapat melakukan pelunasan dipercepat sewaktu-waktu.", BODY))
    s.append(Paragraph("4.2 Penalti pelunasan dipercepat sebesar 3 persen dari sisa pokok "
                       "dikenakan apabila pelunasan dilakukan sebelum setengah masa tenor "
                       "berjalan. Setelah itu tidak dikenakan penalti.", BODY))
    s.append(Paragraph("4.3 BPKB dikembalikan paling lambat 14 hari kerja setelah pelunasan penuh "
                       "diterima dan diverifikasi.", BODY))

    s.append(Paragraph("Pasal 5: Restrukturisasi", H2))
    s.append(Paragraph("5.1 Nasabah yang mengalami kesulitan pembayaran dapat mengajukan "
                       "restrukturisasi sebelum keterlambatan mencapai 60 hari.", BODY))
    s.append(Paragraph("5.2 Opsi restrukturisasi meliputi perpanjangan tenor, penjadwalan ulang "
                       "pembayaran, dan keringanan denda.", BODY))
    s.append(Paragraph("5.3 Restrukturisasi memerlukan dokumen pendukung dan persetujuan Solara. "
                       "Pengajuan tidak menghentikan perhitungan denda sampai disetujui.", BODY))

    s.append(Spacer(1, 8))
    s.append(Paragraph("Solara Multifinance Indonesia terdaftar dan diawasi oleh Otoritas Jasa "
                       "Keuangan. Menara Meridian Lantai 12, Jakarta Selatan.", SMALL))
    return s


def write_truncated_pdf(path: Path) -> None:
    """Write a PDF that stops part way through.

    Real document sets contain files damaged in transfer or export. The pipeline
    has to notice, record which source failed, and carry on with the rest, so
    there needs to be one of these to test against.
    """
    source = OUTPUT_DIR / "health_shield_policy_wording.pdf"
    data = source.read_bytes()
    path.write_bytes(data[: len(data) // 3])
    print(f"  wrote {path.name}  (deliberately damaged, {len(data) // 3} of {len(data)} bytes)")


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Building sample documents in {OUTPUT_DIR}")

    build(OUTPUT_DIR / "health_shield_policy_wording.pdf",
          "Health Shield Policy Wording", "SHS-PW-2026-03", policy_wording())
    build(OUTPUT_DIR / "health_shield_rate_table.pdf",
          "Health Shield Premium Rate Table", "SHS-RT-2026-03", rate_table())
    build(OUTPUT_DIR / "multifinance_agreement_terms.pdf",
          "Ketentuan Perjanjian Pembiayaan", "SMI-KP-2026-03", agreement_terms())
    write_truncated_pdf(OUTPUT_DIR / "health_shield_annex_damaged.pdf")

    print("\nDone. Four files, one of which is deliberately unreadable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
