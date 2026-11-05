"""
The rider's signature at pickup (ADR 0034). The driver app sends what
was drawn on the phone as a PNG data URL; it's re-drawn here as a small
greyscale PNG, so nothing but the drawing is ever stored.
"""

import base64
import binascii
import io

from PIL import Image, UnidentifiedImageError

PREFIX = "data:image/png;base64,"
MAX_UPLOAD_BYTES = 400_000
MAX_SIDE = 4000
STORED_WIDTH = 600


class SignatureError(Exception):
    pass


def clean_signature(data_url: str) -> bytes:
    if not data_url.startswith(PREFIX):
        raise SignatureError("The signature didn't come through. Ask the rider to sign again.")
    try:
        raw = base64.b64decode(data_url[len(PREFIX):], validate=True)
    except (binascii.Error, ValueError):
        raise SignatureError("The signature didn't come through. Ask the rider to sign again.") from None
    if len(raw) > MAX_UPLOAD_BYTES:
        raise SignatureError("The signature is too large. Clear it and sign again.")
    try:
        image = Image.open(io.BytesIO(raw))
        if image.format != "PNG" or max(image.size) > MAX_SIDE:
            raise SignatureError("The signature didn't come through. Ask the rider to sign again.")
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise SignatureError("The signature didn't come through. Ask the rider to sign again.") from None

    # Flatten onto white: the phone draws dark strokes on a transparent canvas.
    flat = Image.new("RGBA", image.size, "white")
    flat.alpha_composite(image.convert("RGBA"))
    grey = flat.convert("L")
    if grey.getextrema()[0] > 160:
        raise SignatureError("The signature box is empty. Ask the rider to sign, or choose why they can't.")
    if grey.width > STORED_WIDTH:
        grey = grey.resize((STORED_WIDTH, max(1, round(grey.height * STORED_WIDTH / grey.width))))
    out = io.BytesIO()
    grey.save(out, format="PNG", optimize=True)
    return out.getvalue()
