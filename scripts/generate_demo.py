"""Generate original CC0 synthetic papers; never use them as research evidence."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def create_paper(path: Path, label: str, *, scanned: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = canvas.Canvas(str(path), pagesize=(612, 792), invariant=1)
    doc.setTitle(f"SYNTHETIC DEMONSTRATION Study {label}")
    doc.setAuthor("Review Extractor demo generator - CC0")
    for page_number in (1, 2):
        doc.setFillColorRGB(0.08, 0.22, 0.3)
        doc.setFont("Helvetica-Bold", 17)
        doc.drawString(44, 742, f"SYNTHETIC DEMONSTRATION - Study {label}")
        doc.setFont("Helvetica", 10)
        doc.drawString(44, 720, "Original test fixture. Fictional participants and results. Not research evidence.")
        doc.setFillColorRGB(0, 0, 0)
        if page_number == 1:
            lines = [
                "Study design: randomized, sham-controlled synthetic trial.",
                "Mean age: treatment 35.9 years; control 35.6 years.",
                "Country / setting: fictional research center.",
                f"Protocol {label}: stimulation at 20 Hz in the treatment group.",
                "Treatment group: 13 participants completed; control: 12 completed.",
                "Treatment group had 0 adverse events at week 3.",
                "Control group had 2 adverse events at week 3.",
                "Neither an absent match nor a zero result represents missing information.",
            ]
        else:
            lines = [
                "Follow-up timing: week 5, two weeks after treatment.",
                "Treatment group at week 3: score 32.6 points; at week 5: 34.2 points.",
                "Control group at week 3: score 36.0 points; at week 5: 36.1 points.",
                "Flow diagram: treatment 12 completed; control 13 completed.",
                "The participant counts conflict with the text on physical PDF page 1.",
                "An approved discrepancy narrative should retain both supporting passages.",
                "No statement about injury phase is provided in this fictional paper.",
            ]
        doc.setFont("Helvetica", 11)
        for line, y in zip(lines, range(674, 430, -27)):
            doc.drawString(44, y, line)
        doc.setFont("Helvetica", 9)
        doc.drawString(44, 36, f"CC0 synthetic fixture | Physical PDF page {page_number} | Printed label {page_number + 8}")
        doc.showPage()
    if scanned:
        image = Image.new("RGB", (1200, 800), "white")
        draw = ImageDraw.Draw(image)
        draw.text((50, 70), "IMAGE-ONLY SYNTHETIC PAGE", fill="black", font_size=44)
        draw.text((50, 150), "Dose: 50 units. Requires manual review or OCR.", fill="black", font_size=35)
        doc.drawImage(ImageReader(image), 30, 180, width=550, height=367)
        doc.showPage()
    doc.save()


def make_geometry_fixture(path: Path):
    buffer = BytesIO()
    doc = canvas.Canvas(buffer, pagesize=(612, 792), invariant=1)
    doc.setFont("Helvetica", 16)
    doc.drawString(130, 620, "Geometry marker 20 Hz")
    doc.save()
    reader = PdfReader(buffer)
    writer = PdfWriter()
    for rotation in (0, 90, 180, 270):
        page = writer.add_page(reader.pages[0])
        page.cropbox = RectangleObject((70, 80, 550, 730))
        page.rotate(rotation)
    with path.open("wb") as stream:
        writer.write(stream)


def main():
    folder = Path(__file__).resolve().parents[1] / "demo"
    for label in "ABCD":
        create_paper(folder / f"study-{label}.pdf", label, scanned=label == "C")
    make_geometry_fixture(folder / "geometry-cropped-rotated.pdf")


if __name__ == "__main__":
    main()
