from __future__ import annotations

from PIL import Image, ImageDraw


def render_ps1_icon(frame_rgba: bytes | None, *, size: int = 256) -> Image.Image:
    """Render a 16x16 PS1 save icon with crisp nearest-neighbour scaling."""
    background = Image.new("RGB", (size, size), (35, 39, 52))
    if not frame_rgba or len(frame_rgba) != 16 * 16 * 4:
        draw = ImageDraw.Draw(background)
        text = "No PS1 icon"
        box = draw.textbbox((0, 0), text)
        draw.text(((size - (box[2] - box[0])) // 2, size // 2 - 8), text, fill=(225, 228, 236))
        return background

    icon = Image.frombytes("RGBA", (16, 16), frame_rgba)
    # Leave a margin so the tiny original pixel art remains obvious and readable.
    target = max(16, int(size * 0.68))
    target -= target % 16
    icon = icon.resize((target, target), Image.Resampling.NEAREST)
    x = (size - target) // 2
    y = (size - target) // 2
    background.paste(icon.convert("RGB"), (x, y), icon.getchannel("A"))
    return background
