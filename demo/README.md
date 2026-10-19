# Synthetic demonstration files

These original fixtures are dedicated to the public domain under CC0 1.0. All studies, participant counts and results are fictional. They are for exercising software, never for a research synthesis.

- `study-A.pdf`, `study-B.pdf`, `study-C.pdf`, `study-D.pdf`: labeled groups and time points, zero adverse events, conflicting participant counts on two physical pages. Printed page labels deliberately differ from physical PDF page numbers. Study C has an image-only third page.
- `geometry-cropped-rotated.pdf`: cropped pages with four rotations for checking text selection and highlight coordinates.
- The template in this folder contains column headings only and/or explicitly synthetic dictionary definitions; it has no extracted studies.

Rebuild the PDFs using `python scripts/generate_demo.py` after installing developer dependencies.

CC0 legal text: https://creativecommons.org/publicdomain/zero/1.0/legalcode
