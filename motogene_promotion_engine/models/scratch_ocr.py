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
    image = image.convert("L")
    # The printed serial is at the top right of the card. A photo often shows
    # the whole card, so search its right-hand area before trying the full shot.
    right = image.crop((int(image.width * 5 / 9), int(image.height * 25 / 72),
                        image.width, image.height))
    wider_right = image.crop((int(image.width * .5), int(image.height * .33),
                              image.width, image.height))
    attempts = ((right, 11), (wider_right, 11), (image, 7), (image, 6))
    results = []
    for candidate_image, page_mode in attempts:
        candidate_image = ImageOps.autocontrast(candidate_image)
        if candidate_image.width < 800:
            candidate_image = candidate_image.resize(
                (candidate_image.width * 2, candidate_image.height * 2)
            )
        candidate_image = candidate_image.filter(ImageFilter.UnsharpMask(radius=2, percent=200))
        buffer = io.BytesIO()
        candidate_image.save(buffer, format="PNG")
        try:
            result = subprocess.run(
                [executable, "stdin", "stdout", "--psm", str(page_mode), "-l", "eng",
                 "-c", "tessedit_char_whitelist=ABCDEFG0123456789"],
                input=buffer.getvalue(), capture_output=True, check=True, timeout=12,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise RuntimeError("Unable to read this photo with Tesseract OCR.") from exc
        text = result.stdout.decode("utf-8", errors="replace").upper()
        results.append(text)
        serials = list(dict.fromkeys(
            f"{prefix}{number}" for prefix, number in SERIAL_PATTERN.findall(text)
        ))
        if serials:
            return serials, text
    raw_text = "\n".join(results)
    return [], raw_text
