"""Odoo 20 GDEX compatibility utilities, including lazy BinaryValue PDFs."""
import base64
import binascii


def gdex_pdf_bytes(value):
    """Convert Odoo 20 BinaryValue, raw attachment bytes or RPC base64 to PDF bytes.

    Require PDF signature to prevent silently caching/printing empty attachments.
    This function never performs network requests or mutates anything.
    """
    if hasattr(value, "content"):
        value = value.content
    if not value:
        raise ValueError("GDEX PDF content is empty")
    if isinstance(value, str):
        try:
            pdf = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("GDEX PDF contains invalid base64 data") from exc
    elif isinstance(value, (bytes, bytearray, memoryview)):
        pdf = bytes(value)
        # Legacy binary fields can sometimes supply base64 instead of raw bytes.
        if not pdf.startswith(b"%PDF"):
            try:
                candidate = base64.b64decode(pdf, validate=True)
                if candidate.startswith(b"%PDF"):
                    pdf = candidate
            except (binascii.Error, ValueError):
                pass
    else:
        raise TypeError("Unsupported GDEX PDF binary value: %s" % type(value).__name__)
    if not pdf.startswith(b"%PDF"):
        raise ValueError("GDEX content does not contain a PDF header")
    return pdf
