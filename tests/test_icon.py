from __future__ import annotations

import struct
import unittest

from ps2icon.icon_model import parse_icon_model
from ps2icon.icon_sys import parse_icon_sys, update_icon_sys_title
from ps2icon.renderer import render_icon, render_texture


def icon_sys_bytes() -> bytes:
    out = bytearray(964)
    out[:4] = b"PS2D"
    colors = [(0, 0, 0, 0x80)] * 4
    pos = 16
    for color in colors:
        struct.pack_into("<4I", out, pos, *color)
        pos += 16
    out[192:201] = b"Test Save"
    out[260:268] = b"icon.ico"
    return bytes(out)


def icon_model_bytes(*, compressed: bool = False, alpha: int = 128, full_range_uv: bool = False) -> bytes:
    vertices = [(-2048, -2048, 0), (2048, -2048, 0), (0, 2048, 0)]
    texture_type = 0x0C if compressed else 0x04
    out = bytearray(struct.pack("<5I", 0x00010000, 1, texture_type, 0, len(vertices)))
    uvs = [(4096, 0), (4096, 4096), (0, 4096)] if full_range_uv else [(0, 0), (4095, 0), (2048, 4095)]
    for (x, y, z), (u, v) in zip(vertices, uvs):
        out += struct.pack("<hhhH", x, y, z, 0)
        out += struct.pack("<hhhHhhBBBB", 0, 0, 4096, 0, u, v, 128, 128, 128, alpha)
    out += struct.pack("<IIfII", 1, 60, 1.0, 0, 0)

    if compressed:
        # Two repeated runs of 8192 pixels each.
        packed = struct.pack("<HHHH", 8192, 0x801F, 8192, 0x801F)
        out += struct.pack("<I", len(packed)) + packed
    else:
        texture = bytearray()
        for y in range(128):
            for x in range(128):
                # Horizontal red gradient in ABGR1555/R-low-bit layout.
                r5 = max(1, min(31, x * 31 // 127))
                texture += struct.pack("<H", 0x8000 | r5)
        out += texture
    return bytes(out)


class IconTests(unittest.TestCase):
    def test_parse_and_render_textured_triangle(self):
        sysinfo = parse_icon_sys(icon_sys_bytes())
        model = parse_icon_model(icon_model_bytes())
        image = render_icon(sysinfo, model, 0.0, size=128)
        self.assertEqual(image.size, (128, 128))
        self.assertEqual(sysinfo.icon_name, "icon.ico")
        self.assertEqual(len(model.vertices), 3)

        # The previous renderer sampled one average UV per triangle, so the model
        # could only have one flat red value. Per-pixel UV mapping must produce many.
        reds = {r for r, g, b in image.getdata() if r > 0 and g < 10 and b < 10}
        self.assertGreater(len(reds), 5)

    def test_compressed_texture_rle(self):
        model = parse_icon_model(icon_model_bytes(compressed=True))
        self.assertIsNotNone(model.texture_rgba)
        self.assertEqual(model.texture_rgba[:4], bytes((255, 0, 0, 255)))
        self.assertEqual(len(model.texture_rgba), 128 * 128 * 4)

    def test_texture_preview_and_black_is_opaque(self):
        model = parse_icon_model(icon_model_bytes())
        texture = render_texture(model, size=256)
        self.assertEqual(texture.size, (256, 256))
        self.assertEqual(model.texture_rgba[3], 255)

    def test_all_zero_vertex_alpha_is_treated_as_opaque(self):
        # Several retail PS2 saves use alpha=0 for every vertex. Rendering those
        # vertices literally makes the complete icon disappear.
        sysinfo = parse_icon_sys(icon_sys_bytes())
        model = parse_icon_model(icon_model_bytes(alpha=0))
        image = render_icon(sysinfo, model, 0.0, size=128)
        foreground = [pixel for pixel in image.getdata() if pixel != (0, 0, 0)]
        self.assertGreater(len(foreground), 100)

    def test_full_range_uvs_are_not_collapsed_as_a_wrap_seam(self):
        # Exact 0 -> 1 mappings are common on simple box/billboard icons. Seam
        # normalization must not turn all three coordinates into the same texel.
        sysinfo = parse_icon_sys(icon_sys_bytes())
        model = parse_icon_model(icon_model_bytes(full_range_uv=True))
        image = render_icon(sysinfo, model, 0.0, size=128)
        reds = {r for r, g, b in image.getdata() if r > 0 and g < 20 and b < 20}
        self.assertGreater(len(reds), 5)

    def test_update_icon_sys_title(self):
        raw = icon_sys_bytes()
        updated = update_icon_sys_title(raw, "Line One\nLine Two")
        parsed = parse_icon_sys(updated)
        self.assertEqual(parsed.title, "Line One\nLine Two")
        self.assertEqual(parsed.icon_name, "icon.ico")

    def test_compressed_texture_literal_rle(self):
        vertices = [(-2048, -2048, 0), (2048, -2048, 0), (0, 2048, 0)]
        out = bytearray(struct.pack("<5I", 0x00010000, 1, 0x0C, 0, len(vertices)))
        uvs = [(0, 0), (4095, 0), (2048, 4095)]
        for (x, y, z), (u, v) in zip(vertices, uvs):
            out += struct.pack("<hhhH", x, y, z, 0)
            out += struct.pack("<hhhHhhBBBB", 0, 0, 4096, 0, u, v, 128, 128, 128, 128)
        out += struct.pack("<IIfII", 1, 60, 1.0, 0, 0)
        # 0xC001 has the high bit set and therefore encodes 16383 literal
        # pixels. A 0xFF00 threshold incorrectly interprets it as a huge repeat.
        literal_pixels = [0x001F, 0x03E0] + [0x7C00] * 16381
        packed = struct.pack("<H", 0xC001) + struct.pack("<16383H", *literal_pixels)
        packed += struct.pack("<HH", 1, 0x001F)
        out += struct.pack("<I", len(packed)) + packed
        model = parse_icon_model(bytes(out))
        self.assertEqual(model.texture_rgba[:4], bytes((255, 0, 0, 255)))
        self.assertEqual(model.texture_rgba[4:8], bytes((0, 255, 0, 255)))
        self.assertEqual(model.texture_rgba[8:12], bytes((0, 0, 255, 255)))
        self.assertEqual(model.texture_rgba[-4:], bytes((255, 0, 0, 255)))


if __name__ == "__main__":
    unittest.main()
