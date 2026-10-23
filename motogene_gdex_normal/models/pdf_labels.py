"""Convert GDEX's top-left A6 quadrant on A4 into thermal 100 x 150 mm.

Vector PDF remains vector; if GDEX layout changes, QA and crop must be revisited.
"""
import io
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import RectangleObject


def convert_to_thermal(pdf):
    reader = PdfReader(io.BytesIO(pdf))
    if len(reader.pages) != 1:
        raise ValueError('Expected one original GDEX page')
    source = reader.pages[0]
    source_width = float(source.mediabox.width)
    source_height = float(source.mediabox.height)
    crop_width = min(300.0, source_width)
    crop_height = min(405.0, source_height)
    crop_bottom = source_height - crop_height
    source.cropbox = RectangleObject([0, crop_bottom, crop_width, source_height])
    source.trimbox = source.cropbox
    pt_per_mm = 72.0 / 25.4
    target_width = 100.0 * pt_per_mm
    target_height = 150.0 * pt_per_mm
    margin = 4.0 * pt_per_mm
    scale = min((target_width - 2 * margin) / crop_width,
                (target_height - 2 * margin) / crop_height)
    dx = (target_width - crop_width * scale) / 2
    dy = (target_height - crop_height * scale) / 2
    transformation = (Transformation().translate(0, -crop_bottom)
                      .scale(scale, scale).translate(dx, dy))
    writer = PdfWriter()
    target = writer.add_blank_page(width=target_width, height=target_height)
    target.merge_transformed_page(source, transformation, expand=False)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
