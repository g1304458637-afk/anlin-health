"""Generate deterministic, entirely fictional OCR fixtures. Never use real patient data.

Requires reportlab, Pillow and Poppler's pdftoppm. No network or app database access.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "assets" / "report-samples"
PDF = OUT / "fictional-two-page-report.pdf"
SCAN = OUT / "fictional-first-page-scan.png"
FONT_PATH = Path(os.environ.get("REPORT_SAMPLE_FONT", str(Path.home() / "Library/Fonts/Arial Unicode.ttf")))
FONT = "ArialUnicodeFixture"
MARKER = "虚构测试样本 非真实医疗报告"
CENTER = "安邻演示检验中心（虚构）"
PATIENT = "虚构王一"
SAMPLE_DATE = "2026-10-20 08:30 +08:00"
REPORT_DATE = "2026-10-20 10:00 +08:00"

ROWS = [
    {"page": 1, "code": "BP", "name": "血压", "value": "132/84", "unit": "mmHg", "reference_range": "--", "flag": "--"},
    {"page": 1, "code": "GLU", "name": "血糖", "value": "5.8", "unit": "mmol/L", "reference_range": "3.9-6.1", "flag": "N"},
    {"page": 1, "code": "WT", "name": "体重", "value": "62.5", "unit": "kg", "reference_range": "--", "flag": "--"},
    {"page": 1, "code": "HGB", "name": "血红蛋白", "value": "131", "unit": "g/L", "reference_range": "115-150", "flag": "N"},
    {"page": 1, "code": "WBC", "name": "白细胞计数", "value": "6.2", "unit": "x10^9/L", "reference_range": "3.5-9.5", "flag": "N"},
    {"page": 1, "code": "TC", "name": "总胆固醇", "value": "5.42", "unit": "mmol/L", "reference_range": "<5.20", "flag": "H"},
    {"page": 2, "code": "CREA", "name": "肌酐", "value": "77", "unit": "μmol/L", "reference_range": "44-97", "flag": "N"},
    {"page": 2, "code": "ALT", "name": "丙氨酸氨基转移酶", "value": "22", "unit": "U/L", "reference_range": "7-40", "flag": "N"},
    {"page": 2, "code": "AST", "name": "天门冬氨酸氨基转移酶", "value": "19", "unit": "U/L", "reference_range": "13-35", "flag": "N"},
    {"page": 2, "code": "UA", "name": "尿酸", "value": "318", "unit": "μmol/L", "reference_range": "155-357", "flag": "N"},
    {"page": 2, "code": "UPRO", "name": "尿蛋白（定性）", "value": "阴性", "unit": "--", "reference_range": "阴性", "flag": "N"},
]


def register_font() -> None:
    if not FONT_PATH.is_file():
        raise RuntimeError(f"Chinese-capable TTF missing: {FONT_PATH}")
    pdfmetrics.registerFont(TTFont(FONT, str(FONT_PATH)))


def txt(pdf: canvas.Canvas, x: float, y: float, value: str, size: int = 10,
        color: str = "#25364A") -> None:
    pdf.setFont(FONT, size)
    pdf.setFillColor(HexColor(color))
    pdf.drawString(x, y, value)


def center(pdf: canvas.Canvas, y: float, value: str, size: int = 12,
           color: str = "#25364A") -> None:
    pdf.setFont(FONT, size)
    pdf.setFillColor(HexColor(color))
    pdf.drawCentredString(A4[0] / 2, y, value)


def page(pdf: canvas.Canvas, index: int) -> None:
    width, height = A4
    pdf.setFillColor(HexColor("#FAFCFF"))
    pdf.rect(0, 0, width, height, stroke=0, fill=1)
    pdf.setFillColor(HexColor("#FBE9E8"))
    pdf.rect(0, height - 42, width, 42, stroke=0, fill=1)
    center(pdf, height - 27, MARKER, 12, "#AA2A2A")
    center(pdf, height - 88, CENTER, 19)
    center(pdf, height - 113, "检验记录功能测试单", 11, "#57718C")
    pdf.setStrokeColor(HexColor("#CCD8E3"))
    pdf.line(43, height - 129, width - 43, height - 129)
    txt(pdf, 47, height - 155, f"姓名：{PATIENT}", 11)
    txt(pdf, 304, height - 155, f"页码：{index} / 2", 10)
    txt(pdf, 47, height - 177, f"采样时间：{SAMPLE_DATE}", 9)
    txt(pdf, 304, height - 177, f"报告时间：{REPORT_DATE}", 9)
    txt(pdf, 47, height - 207, "以下数值和标记仅为功能测试，禁止用于医疗判断。", 9, "#8B4A3B")

    top = height - 242
    pdf.setFillColor(HexColor("#E9F1F8"))
    pdf.roundRect(43, top - 28, width - 86, 31, 5, stroke=0, fill=1)
    cols = [(50, "项目"), (223, "结果"), (307, "单位"), (403, "参考范围"), (520, "标记")]
    for x, label in cols:
        txt(pdf, x, top - 16, label, 9)
    y = top - 53
    for row in (item for item in ROWS if item["page"] == index):
        if (top - 53 - y) // 44 % 2:
            pdf.setFillColor(HexColor("#F2F6FA"))
            pdf.rect(43, y - 14, width - 86, 37, stroke=0, fill=1)
        txt(pdf, 50, y, f"{row['name']}  {row['code']}", 9)
        txt(pdf, 223, y, row["value"], 10)
        txt(pdf, 307, y, row["unit"], 9)
        txt(pdf, 403, y, row["reference_range"], 9)
        txt(pdf, 520, y, row["flag"], 9, "#AA2A2A" if row["flag"] == "H" else "#25364A")
        y -= 44

    pdf.setStrokeColor(HexColor("#CCD8E3"))
    pdf.line(43, 98, width - 43, 98)
    txt(pdf, 47, 77, "标记为样本字面数据：N / H / --；不代表医学解释。", 8, "#57718C")
    txt(pdf, 47, 59, MARKER, 9, "#AA2A2A")
    txt(pdf, 47, 42, "样本编号：ANLIN-FICTION-20261020-01", 8, "#57718C")
    pdf.showPage()


def create_pdf() -> None:
    register_font()
    pdf = canvas.Canvas(str(PDF), pagesize=A4, pageCompression=1, invariant=1)
    pdf.setTitle("安邻健康虚构双页检验报告 - 功能测试")
    pdf.setAuthor("Anlin fictional fixture generator")
    pdf.setSubject(MARKER)
    for index in (1, 2):
        page(pdf, index)
    pdf.save()


def create_scan() -> None:
    with tempfile.TemporaryDirectory() as directory:
        prefix = str(Path(directory) / "first")
        subprocess.run(["pdftoppm", "-f", "1", "-l", "1", "-r", "180",
                        "-gray", "-png", "-singlefile", str(PDF), prefix], check=True,
                       stdout=subprocess.DEVNULL)
        image = Image.open(prefix + ".png").convert("L")
        image = ImageEnhance.Contrast(image).enhance(0.92)
        image = image.filter(ImageFilter.GaussianBlur(radius=0.35))
        # Fixed, sparse paper grain; keep every printed character readable.
        rng = random.Random(20261020)
        pixels = image.load()
        for _ in range(1200):
            x = rng.randrange(12, image.width - 12)
            y = rng.randrange(12, image.height - 12)
            if pixels[x, y] > 225:
                pixels[x, y] = rng.choice((226, 231, 237, 244))
        image.save(SCAN, optimize=False)


def create_expected() -> None:
    golden = {
        "fixture_id": "ANLIN-FICTION-20261020-01",
        "fictional": True,
        "required_marker_each_page": MARKER,
        "center": CENTER,
        "patient": PATIENT,
        "sample_datetime": SAMPLE_DATE,
        "report_datetime": REPORT_DATE,
        "pdf_pages": 2,
        "scan_source_page": 1,
        "items": ROWS,
        "note": "Flags are literal fixture text, not a clinical interpretation.",
    }
    (OUT / "expected.json").write_text(json.dumps(golden, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    create_pdf()
    create_scan()
    create_expected()
    print(PDF)
    print(SCAN)
    print(OUT / "expected.json")


if __name__ == "__main__":
    main()
