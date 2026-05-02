"""
GeoHan PDF Rapor Servisi — ReportLab
"""

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)
from reportlab.graphics.shapes import Drawing, Rect

# ─── FONTLAR ──────────────────────────────────────────────────────────────────
try:
    pdfmetrics.registerFont(TTFont("Arial",      "C:/Windows/Fonts/arial.ttf"))
    pdfmetrics.registerFont(TTFont("Arial-Bold", "C:/Windows/Fonts/arialbd.ttf"))
    FONT       = "Arial"
    FONT_BOLD  = "Arial-Bold"
except Exception:
    FONT = FONT_BOLD = "Helvetica"

# ─── RENKLER ──────────────────────────────────────────────────────────────────
C_DARK    = colors.HexColor("#1A252F")
C_BLUE    = colors.HexColor("#2C3E50")
C_GREEN   = colors.HexColor("#27AE60")
C_ORANGE  = colors.HexColor("#E67E22")
C_RED     = colors.HexColor("#E74C3C")
C_LIGHT   = colors.HexColor("#F2F3F4")
C_WHITE   = colors.white
C_BORDER  = colors.HexColor("#BDC3C7")

# ─── STİLLER ──────────────────────────────────────────────────────────────────
def _style(name, font=None, size=10, bold=False, color=None, align="LEFT", space_after=4):
    return ParagraphStyle(
        name,
        fontName=FONT_BOLD if bold else (font or FONT),
        fontSize=size,
        textColor=color or colors.black,
        alignment={"LEFT": 0, "CENTER": 1, "RIGHT": 2}[align],
        spaceAfter=space_after,
    )

S_TITLE   = _style("title",   size=20, bold=True,  color=C_WHITE,  align="CENTER", space_after=2)
S_SUB     = _style("sub",     size=11, bold=False, color=C_WHITE,  align="CENTER", space_after=0)
S_H2      = _style("h2",      size=12, bold=True,  color=C_BLUE,   space_after=6)
S_NORMAL  = _style("normal",  size=9,  space_after=3)
S_SMALL   = _style("small",   size=8,  color=colors.HexColor("#7F8C8D"), space_after=2)
S_FOOTER  = _style("footer",  size=7,  color=colors.HexColor("#95A5A6"), align="CENTER")
S_SCORE_BIG = _style("score_big", size=36, bold=True, align="CENTER", space_after=0)


def _score_color(score: int) -> colors.Color:
    if score >= 80: return C_GREEN
    if score >= 55: return C_ORANGE
    return C_RED


def _score_bar(score: int, width_cm: float = 5.5) -> Drawing:
    """Yatay dolgu çubuğu."""
    w = width_cm * cm
    h = 0.35 * cm
    filled = w * score / 100
    d = Drawing(w, h)
    d.add(Rect(0, 0, w, h, fillColor=C_LIGHT, strokeColor=C_BORDER, strokeWidth=0.5))
    d.add(Rect(0, 0, filled, h, fillColor=_score_color(score), strokeColor=None))
    return d


def _header_table(name: str, lat: float, lon: float,
                  area_ha: float, panel: str, tracking: str,
                  date_str: str) -> Table:
    data = [[
        Paragraph("GEOHAN", _style("logo", size=18, bold=True, color=C_WHITE)),
        Paragraph("SOLAR-INTELLIGENCE RAPORU", _style("brand", size=11, color=colors.HexColor("#BDC3C7"))),
        Paragraph(date_str, _style("date", size=8, color=colors.HexColor("#BDC3C7"), align="RIGHT")),
    ]]
    t = Table(data, colWidths=[4*cm, 10*cm, 3*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,-1), C_DARK),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",  (0,0), (-1,-1), 10),
        ("BOTTOMPADDING",(0,0),(-1,-1), 10),
        ("LEFTPADDING", (0,0), (0,-1),  14),
    ]))
    return t


def _info_table(name, lat, lon, area_ha, panel, tracking, utm_zone) -> Table:
    loc_name = name or "—"
    rows = [
        ["Lokasyon", loc_name,        "Koordinat", f"{lat:.4f}°N  {lon:.4f}°E"],
        ["Alan",     f"{area_ha} ha", "UTM Zone",  str(utm_zone)],
        ["Panel",    panel,           "Tracking",  tracking],
    ]
    t = Table(rows, colWidths=[2.5*cm, 5.5*cm, 2.5*cm, 5.5*cm])
    t.setStyle(TableStyle([
        ("FONTNAME",    (0,0), (-1,-1), FONT),
        ("FONTNAME",    (0,0), (0,-1),  FONT_BOLD),
        ("FONTNAME",    (2,0), (2,-1),  FONT_BOLD),
        ("FONTSIZE",    (0,0), (-1,-1), 9),
        ("BACKGROUND",  (0,0), (0,-1),  C_LIGHT),
        ("BACKGROUND",  (2,0), (2,-1),  C_LIGHT),
        ("GRID",        (0,0), (-1,-1), 0.4, C_BORDER),
        ("ROWBACKGROUNDS",(0,0),(-1,-1),[C_WHITE, C_LIGHT]),
        ("TOPPADDING",  (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0),(-1,-1), 5),
    ]))
    return t


def _score_summary_table(total_score: float) -> Table:
    sc = int(total_score)
    color = _score_color(sc)
    label = "MÜKEMMEL" if sc >= 85 else "İYİ" if sc >= 65 else "ORTA" if sc >= 45 else "ZAYIF"
    data = [[
        Paragraph(f"{total_score:.1f}", _style("s1", size=40, bold=True, color=color, align="CENTER")),
        Paragraph("/100", _style("s2", size=14, color=colors.HexColor("#7F8C8D"), align="LEFT")),
        Paragraph(label,  _style("s3", size=16, bold=True, color=color, align="LEFT")),
    ]]
    t = Table(data, colWidths=[3.5*cm, 1.5*cm, 11*cm])
    t.setStyle(TableStyle([
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ("BACKGROUND",  (0,0), (-1,-1), C_LIGHT),
        ("TOPPADDING",  (0,0), (-1,-1), 8),
        ("BOTTOMPADDING",(0,0),(-1,-1), 8),
        ("LEFTPADDING", (0,0), (0,-1),  20),
        ("BOX",         (0,0), (-1,-1), 1, color),
    ]))
    return t


CRITERION_LABELS = {
    "egim":   "Arazi Egimi",
    "ghi":    "Gunes Isinimi (GHI)",
    "baki":   "Baki (Aspect)",
    "golge":  "Olge Kaybi",
    "arazi":  "Arazi Ortusu (ESA)",
    "sebeke": "Sebeke Mesafesi",
    "erisim": "Yol Erisimi",
}


def _breakdown_table(breakdown: dict) -> Table:
    header = [
        Paragraph("KRİTER",         _style("h", size=8, bold=True, color=C_WHITE)),
        Paragraph("DEĞER",          _style("h", size=8, bold=True, color=C_WHITE, align="CENTER")),
        Paragraph("SKOR ÇUBUĞU",    _style("h", size=8, bold=True, color=C_WHITE)),
        Paragraph("SKOR",           _style("h", size=8, bold=True, color=C_WHITE, align="CENTER")),
        Paragraph("AĞIRLIK",        _style("h", size=8, bold=True, color=C_WHITE, align="CENTER")),
    ]
    rows = [header]
    for key, crit in breakdown.items():
        score = crit["score"]
        value = crit["value"]
        unit  = crit["unit"]
        weight = crit["weight"]
        label = CRITERION_LABELS.get(key, key)
        rows.append([
            Paragraph(label, _style("cl", size=9)),
            Paragraph(f"{value} {unit}", _style("cv", size=9, align="CENTER")),
            _score_bar(score),
            Paragraph(str(score), _style("cs", size=10, bold=True,
                                         color=_score_color(score), align="CENTER")),
            Paragraph(f"%{int(weight*100)}", _style("cw", size=9,
                                                      color=colors.HexColor("#7F8C8D"), align="CENTER")),
        ])

    col_w = [4.5*cm, 3*cm, 5.5*cm, 1.8*cm, 1.7*cm]
    t = Table(rows, colWidths=col_w)
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,0),  C_BLUE),
        ("TEXTCOLOR",    (0,0), (-1,0),  C_WHITE),
        ("FONTSIZE",     (0,0), (-1,-1), 9),
        ("GRID",         (0,0), (-1,-1), 0.4, C_BORDER),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_WHITE, C_LIGHT]),
        ("VALIGN",       (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",   (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0),(-1,-1),  5),
        ("LEFTPADDING",  (0,0), (0,-1),  6),
    ]))
    return t


def _two_col_table(left_data: list, right_data: list,
                   left_title: str, right_title: str) -> Table:
    def _sub(rows, title):
        header = [[Paragraph(title, _style("th", size=9, bold=True, color=C_WHITE)),
                   Paragraph("",    _style("th"))]]
        styled = header + rows
        t = Table(styled, colWidths=[4.5*cm, 3*cm])
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,0),  C_BLUE),
            ("SPAN",          (0,0), (-1,0)),
            ("FONTSIZE",      (0,0), (-1,-1), 9),
            ("GRID",          (0,0), (-1,-1), 0.4, C_BORDER),
            ("ROWBACKGROUNDS",(0,1), (-1,-1), [C_WHITE, C_LIGHT]),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
            ("TOPPADDING",    (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
            ("LEFTPADDING",   (0,0), (0,-1),  6),
            ("FONTNAME",      (0,1), (0,-1),  FONT_BOLD),
        ]))
        return t

    left_t  = _sub(left_data,  left_title)
    right_t = _sub(right_data, right_title)
    outer = Table([[left_t, Spacer(0.5*cm, 1), right_t]], colWidths=[7.8*cm, 0.5*cm, 7.8*cm])
    outer.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP")]))
    return outer


def generate(job_id: str, job_data: dict) -> bytes:
    result   = job_data["result"]
    name     = job_data.get("name")
    bd       = result["breakdown"]
    cap      = result["capacity"]
    fin      = result["financial"]
    score    = result["total_score"]
    date_str = datetime.now().strftime("%d.%m.%Y %H:%M")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=1.5*cm, leftMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
    )

    story = []

    # Başlık
    story.append(_header_table(
        name, result["lat"], result["lon"],
        result["area_ha"], cap["panel_tech"], cap["tracking"], date_str
    ))
    story.append(Spacer(1, 0.4*cm))

    # Lokasyon bilgisi
    story.append(Paragraph("Lokasyon ve Parametreler", S_H2))
    story.append(_info_table(
        name, result["lat"], result["lon"], result["area_ha"],
        cap["panel_tech"], cap["tracking"], result["utm_zone"]
    ))
    story.append(Spacer(1, 0.4*cm))

    # Toplam skor
    story.append(Paragraph("Yatirim Skoru", S_H2))
    story.append(_score_summary_table(score))
    story.append(Spacer(1, 0.4*cm))

    # Kriter dökümü
    story.append(Paragraph("Kriter Analizi", S_H2))
    story.append(_breakdown_table(bd))
    story.append(Spacer(1, 0.4*cm))

    # Kapasite + Finansal
    cap_rows = [
        [Paragraph("MW/ha (dinamik)", S_NORMAL), Paragraph(str(cap["mw_per_ha"]), S_NORMAL)],
        [Paragraph("GCR Efektif",     S_NORMAL), Paragraph(str(cap["gcr_effective"]), S_NORMAL)],
        [Paragraph("Toplam Kurulu",   S_NORMAL), Paragraph(f"{cap['total_mw']:.1f} MW", S_NORMAL)],
        [Paragraph("Yillik Uretim",   S_NORMAL), Paragraph(f"{cap['annual_gwh']:.1f} GWh", S_NORMAL)],
    ]
    fin_rows = [
        [Paragraph("USD/TL (TCMB)",   S_NORMAL), Paragraph(f"{fin['usd_tl']:.2f}", S_NORMAL)],
        [Paragraph("Yatirim",         S_NORMAL), Paragraph(f"${fin['investment_usd']:,.0f}", S_NORMAL)],
        [Paragraph("Yillik Gelir",    S_NORMAL), Paragraph(f"{fin['annual_revenue_tl']/1e6:.1f} M TL", S_NORMAL)],
        [Paragraph("Geri Odeme",      S_NORMAL), Paragraph(f"{fin['payback_years']:.1f} yil", S_NORMAL)],
    ]
    story.append(Paragraph("Kapasite ve Finansal Ozet", S_H2))
    story.append(_two_col_table(cap_rows, fin_rows, "ENERJİ KAPASİTESİ", "FİNANSAL ANALİZ"))
    story.append(Spacer(1, 0.6*cm))

    # Footer
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_BORDER))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        "Bu rapor GeoHan Solar-Intelligence tarafindan otomatik uretilmistir. "
        "Yatirim karari vermeden once bagimsiz fizibilite calismalari yapilmasi onerilir. | geohanmaps.com",
        S_FOOTER
    ))

    doc.build(story)
    return buf.getvalue()
