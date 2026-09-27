import re
import subprocess
import tempfile
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps, ImageEnhance, ImageFilter


GSTIN_RE = re.compile(
    r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b",
    re.IGNORECASE,
)


# ============================================================
# IMAGE PREPROCESSING
# ============================================================

def _preprocess_image(src: Path, dst: Path):
    img = Image.open(src).convert("L")

    img = ImageOps.autocontrast(img)
    img = ImageEnhance.Contrast(img).enhance(1.6)
    img = img.filter(ImageFilter.SHARPEN)

    if img.width < 1800:
        scale = 1800 / img.width

        img = img.resize(
            (
                int(img.width * scale),
                int(img.height * scale),
            )
        )

    img.save(
        dst,
        format="PNG"
    )


# ============================================================
# TESSERACT OCR
# ============================================================

def _tesseract(image_path: Path) -> str:

    proc = subprocess.run(
        [
            "tesseract",
            str(image_path),
            "stdout",
            "--oem", "1",
            "--psm", "6",
            "-l", "eng",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.strip()
            or "Tesseract OCR failed"
        )

    return proc.stdout


# ============================================================
# PDF -> IMAGES
# ============================================================

def _pdf_to_images(
    pdf_path: Path,
    output_dir: Path
):

    document = pymupdf.open(pdf_path)

    pages = []

    try:

        for page_number, page in enumerate(
            document,
            start=1
        ):

            matrix = pymupdf.Matrix(
                2.5,
                2.5
            )

            pixmap = page.get_pixmap(
                matrix=matrix,
                alpha=False
            )

            image_path = (
                output_dir
                / f"page-{page_number}.png"
            )

            pixmap.save(image_path)

            pages.append(image_path)

    finally:
        document.close()

    return pages


# ============================================================
# DIRECT PDF TEXT EXTRACTION
# ============================================================

def _pdf_text(pdf_path: Path) -> str:

    document = pymupdf.open(pdf_path)

    pages = []

    try:

        for page_number, page in enumerate(
            document,
            start=1
        ):

            text = page.get_text("text") or ""

            pages.append(
                f"--- PAGE {page_number} ---\n{text}"
            )

    finally:
        document.close()

    return "\n".join(pages)


# ============================================================
# CHECK WHETHER PDF TEXT IS USEFUL
# ============================================================

def _has_useful_pdf_text(text: str) -> bool:

    if not text.strip():
        return False

    # If we can directly see a GSTIN, the PDF text layer
    # is usually much more reliable than OCR.
    if GSTIN_RE.search(text):
        return True

    # Also accept reasonably large text layers.
    # Scanned PDFs usually return almost no text.
    if len(text.strip()) >= 100:
        return True

    return False


# ============================================================
# OCR IMAGE FILE
# ============================================================

def _extract_image_text(
    file_path: Path,
    temp_dir: Path
) -> str:

    prepared = (
        temp_dir
        / "prepared-image.png"
    )

    _preprocess_image(
        file_path,
        prepared
    )

    return _tesseract(prepared)


# ============================================================
# OCR PDF
# ============================================================

def _extract_pdf_text(
    file_path: Path,
    temp_dir: Path
) -> str:

    # --------------------------------------------------------
    # STEP 1:
    # Try the PDF's existing text layer.
    # --------------------------------------------------------

    native_text = _pdf_text(file_path)

    if _has_useful_pdf_text(native_text):

        return native_text

    # --------------------------------------------------------
    # STEP 2:
    # If the PDF is scanned/image-only,
    # fall back to Tesseract.
    # --------------------------------------------------------

    pages = _pdf_to_images(
        file_path,
        temp_dir
    )

    output = []

    for i, page in enumerate(
        pages,
        start=1
    ):

        prepared = (
            temp_dir
            / f"prepared-{i}.png"
        )

        _preprocess_image(
            page,
            prepared
        )

        text = _tesseract(
            prepared
        )

        output.append(
            f"--- PAGE {i} ---\n{text}"
        )

    return "\n".join(output)


# ============================================================
# MAIN TEXT EXTRACTION
# ============================================================

def extract_text(
    file_path: Path
) -> str:

    suffix = (
        file_path.suffix.lower()
    )

    with tempfile.TemporaryDirectory() as temp:

        temp_dir = Path(temp)

        # ----------------------------------------------------
        # PDF
        # ----------------------------------------------------

        if suffix == ".pdf":

            return _extract_pdf_text(
                file_path,
                temp_dir
            )

        # ----------------------------------------------------
        # IMAGE
        # ----------------------------------------------------

        if suffix in {
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
        }:

            return _extract_image_text(
                file_path,
                temp_dir
            )

        raise ValueError(
            f"Unsupported file type: {suffix}"
        )