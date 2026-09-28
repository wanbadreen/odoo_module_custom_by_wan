# -*- coding: utf-8 -*-
"""Read printed Scratch & Win serials from a photo without an external OCR API."""
import io
import re
import shutil
import subprocess

from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError


SERIAL_PATTERN = re.compile(r"(?<![A-Z0-9])([A-G])\s*[-:]?\s*(\d{4})(?!\d)")


def read_serials(photo_bytes):
    """Return (recognized serials, raw OCR text). Human confirmation is still required."""
    executable = shutil.which("tesseract")
    if not executable:
        raise RuntimeError("Tesseract OCR is unavailable on this Odoo server.")
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(photo_bytes)))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("Upload a clear JPG or PNG photo of the serial number.") from exc
    if image.width * image.height > 20_000_000:
        raise ValueError("The photo is too large; use an image below 20 megapixels.")
    image = ImageOps.autocontrast(image.convert("L"))
    if image.width < 800:
        image = image.resize((image.width * 2, image.height * 2))
    image = image.filter(ImageFilter.UnsharpMask(radius=1, percent=150))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    results = []
    for page_mode in (7, 6):
        try:
            result = subprocess.run(
                [executable, "stdin", "stdout", "--psm", str(page_mode), "-l", "eng"],
                input=buffer.getvalue(), capture_output=True, check=True, timeout=12,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise RuntimeError("Unable to read this photo with Tesseract OCR.") from exc
        text = result.stdout.decode("utf-8", errors="replace").upper()
        results.append(text)
    raw_text = "\n".join(results)
    serials = list(dict.fromkeys(
        f"{prefix}{number}" for prefix, number in SERIAL_PATTERN.findall(raw_text)
    ))
    return serials, raw_text
