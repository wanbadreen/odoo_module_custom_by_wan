"""Normalize an official multi-page myGDEX Prime MPS PDF for thermal labels.

Only GDEX-provided labels are accepted; barcodes and QR codes are never
synthesized or modified. Each GDEX page is scaled as one whole page.
"""
import io
import re

from pypdf import PdfReader, PdfWriter, Transformation


PT_PER_MM = 72.0 / 25.4


def normalize_official_mps(pdf_bytes, parent_awb, child_awbs):
    if not pdf_bytes.startswith(b'%PDF'):
        raise ValueError('Upload the original PDF downloaded from myGDEX Prime.')
    if len(pdf_bytes) > 15 * 1024 * 1024:
        raise ValueError('PDF must not exceed 15 MB.')
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
        if reader.is_encrypted:
            raise ValueError('Encrypted PDFs cannot be imported.')
        expected = [parent_awb] + list(child_awbs)
        if len(reader.pages) != len(expected):
            raise ValueError('Expected %s pages (parent + child labels); received %s.' %
                             (len(expected), len(reader.pages)))
        writer = PdfWriter()
        width, height = 100 * PT_PER_MM, 150 * PT_PER_MM
        margin = 2 * PT_PER_MM
        for index, (page, number) in enumerate(zip(reader.pages, expected), 1):
            # Per-page text verification prevents a parent-only PDF or an
            # incorrect/reordered file from silently masquerading as MPS labels.
            text = re.sub(r'\s+', '', page.extract_text() or '').upper()
            if not text or number.upper() not in text:
                raise ValueError('Page %s does not contain its expected GDEX CN %s. '
                                 'Use a searchable original PDF from myGDEX Prime.' %
                                 (index, number))
            original_width = float(page.mediabox.width)
            original_height = float(page.mediabox.height)
            if original_width <= 0 or original_height <= 0:
                raise ValueError('Invalid PDF page size.')
            if abs(original_width - width) < 2 and abs(original_height - height) < 2:
                writer.add_page(page)
                continue
            scale = min((width - 2 * margin) / original_width,
                        (height - 2 * margin) / original_height)
            dx = (width - scale * original_width) / 2
            dy = (height - scale * original_height) / 2
            target = writer.add_blank_page(width=width, height=height)
            target.merge_transformed_page(
                page,
                Transformation().translate(-float(page.mediabox.left), -float(page.mediabox.bottom))
                .scale(scale, scale).translate(dx, dy),
                expand=False,
            )
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('Could not read/normalize the original GDEX PDF (%s).' %
                         type(exc).__name__) from exc
