"""Original CC0 synthetic PDF fixture; contains no real study or people."""
from io import BytesIO

from reportlab.pdfgen import canvas


def concept_pdf():
    stream = BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(612, 792))
    pdf.setTitle("Synthetic concept retrieval fixture")
    # Deliberately conflicts with the byline: metadata must stay a separate aid.
    pdf.setAuthor("Morgan Example")
    pdf.setFont("Helvetica", 8)
    pdf.drawString(50, 760, "SYNTHETIC RETRIEVAL FIXTURE - CC0 - NO REAL STUDY")
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(50, 720, "Motor Learning After Stimulation")
    pdf.setFont("Helvetica", 12)
    pdf.drawString(50, 687, "Alice Example, Bob Sample")
    pdf.setFont("Helvetica", 9)
    pdf.drawString(50, 668, "Department of Rehabilitation, Example University")
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(50, 620, "Abstract")
    pdf.setFont("Helvetica", 10)
    for y, line in [(599, "Participants averaged 42 years in the intervention group."),
                    (580, "The comparator group averaged 47 years at enrollment."),
                    (550, "Treatment delivered pulses at 20 Hz; sham used 1 Hz."),
                    (530, "The recording system sampled at 1000 Hz."),
                    (500, "Exhaustion persisted during the subsequent assessment."),
                    (475, "The authors cited Carol Reference and David Citation (2010)."),
                    (450, "The equipment was installed twelve years ago.")]:
        pdf.drawString(50, y, line)
    pdf.showPage()
    pdf.setFont("Helvetica", 12)
    for y, line in [(730, "References"), (700, "Carol Reference, David Citation. Earlier study. 2010."),
                    (640, "Acknowledgments"), (610, "We thank Evelyn Helper at Example Hospital.")]:
        pdf.drawString(50, y, line)
    pdf.save()
    return stream.getvalue()
