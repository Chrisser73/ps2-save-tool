from __future__ import annotations

import math

from PIL import Image, ImageDraw

from .icon_model import IconModel
from .icon_sys import IconSys


# The save data itself does not contain a per-save camera rotation. Use a fixed
# browser-style camera. Ross Ridge's public-domain mymc exposes a "flat" camera at
# [0, 2, -7.5]; its elevation is about 14.9 degrees with no sideways yaw.
_ROT_X = -math.atan2(2.0, 7.5)
_ROT_Y = 0.0


def _clamp8(value: float) -> int:
    return max(0, min(255, int(round(value))))


def _ps2_color(value: int) -> int:
    # icon.sys background channels normally use the PS2 0..0x80 range.
    return _clamp8(value * 255.0 / 128.0)


def _background(icon_sys: IconSys | None, size: int) -> Image.Image:
    if icon_sys and len(icon_sys.background_colors) >= 4:
        corners = [tuple(_ps2_color(c) for c in color[:3]) for color in icon_sys.background_colors[:4]]
    else:
        corners = [(28, 32, 44), (42, 48, 68), (22, 26, 38), (34, 38, 56)]

    image = Image.new("RGB", (size, size))
    pixels = image.load()
    tl, tr, bl, br = corners
    denom = max(1, size - 1)
    for y in range(size):
        fy = y / denom
        for x in range(size):
            fx = x / denom
            top_r = tl[0] * (1.0 - fx) + tr[0] * fx
            top_g = tl[1] * (1.0 - fx) + tr[1] * fx
            top_b = tl[2] * (1.0 - fx) + tr[2] * fx
            bot_r = bl[0] * (1.0 - fx) + br[0] * fx
            bot_g = bl[1] * (1.0 - fx) + br[1] * fx
            bot_b = bl[2] * (1.0 - fx) + br[2] * fx
            pixels[x, y] = (
                _clamp8(top_r * (1.0 - fy) + bot_r * fy),
                _clamp8(top_g * (1.0 - fy) + bot_g * fy),
                _clamp8(top_b * (1.0 - fy) + bot_b * fy),
            )
    return image


def _rotate(point: tuple[float, float, float], ax: float = _ROT_X, ay: float = _ROT_Y) -> tuple[float, float, float]:
    x, y, z = point
    cy, sy = math.cos(ay), math.sin(ay)
    x, z = x * cy + z * sy, -x * sy + z * cy
    cx, sx = math.cos(ax), math.sin(ax)
    y, z = y * cx - z * sx, y * sx + z * cx
    return x, y, z


def _normalize(v: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = v
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-9:
        return (0.0, 0.0, 1.0)
    return x / length, y / length, z / length


def _texture_sample(model: IconModel, u: float, v: float) -> tuple[int, int, int, int]:
    """Bilinear PS2 icon texture lookup with clamp sampling.

    The earlier repeat-wrapped lookup worked for a few simple icons but causes
    visible seams and large texture pulls on many retail saves where UVs land a
    hair outside the nominal 0..1 range. The PS2 browser-style previews are much
    closer to clamp sampling at the texture edges.
    """
    if not model.texture_rgba:
        return (205, 210, 230, 255)

    width, height = model.texture_width, model.texture_height
    u = max(0.0, min(1.0, u))
    v = max(0.0, min(1.0, v))
    # Retail PS2 save icons line up more closely when V is sampled directly
    # rather than mirrored. This keeps front-facing textures upright on many
    # more complex icons.
    fx = u * (width - 1)
    fy = v * (height - 1)
    x0 = math.floor(fx)
    y0 = math.floor(fy)
    tx = fx - x0
    ty = fy - y0

    raw = model.texture_rgba

    def pixel(x: int, y: int) -> tuple[int, int, int, int]:
        x = max(0, min(width - 1, x))
        y = max(0, min(height - 1, y))
        idx = (y * width + x) * 4
        return raw[idx], raw[idx + 1], raw[idx + 2], raw[idx + 3]

    c00 = pixel(x0, y0)
    c10 = pixel(x0 + 1, y0)
    c01 = pixel(x0, y0 + 1)
    c11 = pixel(x0 + 1, y0 + 1)
    out = []
    for channel in range(4):
        top = c00[channel] * (1.0 - tx) + c10[channel] * tx
        bottom = c01[channel] * (1.0 - tx) + c11[channel] * tx
        out.append(_clamp8(top * (1.0 - ty) + bottom * ty))
    return out[0], out[1], out[2], out[3]


def _lighting(icon_sys: IconSys | None, normal: tuple[float, float, float]) -> tuple[float, float, float]:
    nx, ny, nz = _normalize(normal)
    if icon_sys is None:
        diffuse = max(0.0, nz)
        value = 0.58 + diffuse * 0.52
        return value, value, value

    ambient = list(icon_sys.ambient[:3])
    if not any(math.isfinite(v) and abs(v) > 1e-4 for v in ambient):
        ambient = [0.55, 0.55, 0.55]
    out = [max(0.0, float(v)) if math.isfinite(v) else 0.0 for v in ambient]

    for direction, color in zip(icon_sys.light_directions, icon_sys.light_colors):
        dx, dy, dz = direction[:3]
        if not all(math.isfinite(v) for v in (dx, dy, dz)):
            continue
        ldx, ldy, ldz = _normalize((-dx, -dy, -dz))
        amount = max(0.0, nx * ldx + ny * ldy + nz * ldz)
        for channel in range(3):
            c = color[channel] if channel < len(color) and math.isfinite(color[channel]) else 0.0
            out[channel] += max(0.0, c) * amount

    return tuple(max(0.16, min(1.75, value)) for value in out)  # type: ignore[return-value]


def _edge(ax: float, ay: float, bx: float, by: float, px: float, py: float) -> float:
    return (px - ax) * (by - ay) - (py - ay) * (bx - ax)


def render_texture(model: IconModel, *, size: int = 256) -> Image.Image:
    """Show the raw 128x128 texture embedded in a PS2 icon model.

    This is intentionally not a replacement for the 3D preview. Many icons use a UV
    atlas, so the texture can look like disassembled model parts. It is useful as a
    reliable fallback and as a quick way to distinguish parser errors from 3D-renderer
    errors.
    """
    if not model.texture_rgba:
        return render_placeholder("No embedded texture", size=size)
    texture = Image.frombytes("RGBA", (model.texture_width, model.texture_height), model.texture_rgba)
    # The high bit in the icon's BGR555 texture is not a dependable transparency bit.
    # Present the source texture as opaque RGB and scale it without smoothing.
    texture = texture.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    texture = texture.convert("RGB")
    return texture.resize((size, size), Image.Resampling.NEAREST)


def render_icon(
    icon_sys: IconSys | None,
    model: IconModel,
    seconds: float = 0.0,
    *,
    size: int = 256,
    lit: bool = True,
) -> Image.Image:
    """Render the PS2 3D save icon in software.

    The rasterizer uses a z-buffer, perspective-correct UV/normal/color interpolation,
    repeat-wrapped bilinear texture sampling, vertex colour modulation and icon.sys
    lighting. ``lit=False`` is kept as a diagnostic fallback for difficult models.
    """
    image = _background(icon_sys, size)
    if not model.vertices:
        return image

    weights = model.shape_weights(seconds)
    blended: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []

    for vertex in model.vertices:
        x = y = z = 0.0
        for shape_idx, weight in enumerate(weights):
            if shape_idx >= len(vertex.shapes):
                break
            sx, sy, sz = vertex.shapes[shape_idx]
            x += sx * weight
            y += sy * weight
            z += sz * weight
        blended.append(_rotate((x, y, z)))
        normals.append(_normalize(_rotate(vertex.normal)))

    xs = [p[0] for p in blended]
    ys = [p[1] for p in blended]
    center_x = (min(xs) + max(xs)) * 0.5
    center_y = (min(ys) + max(ys)) * 0.5
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-6)
    base_scale = size * 0.72 / span

    # x, y, z, reciprocal camera-space W. q is used for perspective-correct attrs.
    projected: list[tuple[float, float, float, float]] = []
    for x, y, z in blended:
        camera_w = max(3.0, 8.0 - z)
        q = 1.0 / camera_w
        perspective = 8.0 * q
        px = size * 0.5 + (x - center_x) * base_scale * perspective
        py = size * 0.5 - (y - center_y) * base_scale * perspective
        projected.append((px, py, z, q))

    pixels = image.load()
    zbuffer = [-1.0e30] * (size * size)
    textured = bool(model.texture_rgba)
    # Some valid PS2 icons store alpha=0 on every vertex. In that case the
    # console/browser treats the mesh as opaque; otherwise the entire icon vanishes.
    vertex_alpha_enabled = any(vertex.color[3] > 0 for vertex in model.vertices)
    rgb_values = [vertex.color[:3] for vertex in model.vertices]
    # A number of retail icons use one constant low RGB value on every textured
    # vertex. Treat that as a neutral material tint instead of darkening the whole
    # texture. Bright/variable vertex colours still modulate the texture normally.
    constant_rgb = bool(rgb_values) and all(rgb == rgb_values[0] for rgb in rgb_values)
    neutral_low_rgb = textured and constant_rgb and max(rgb_values[0]) <= 0x80

    vertex_count = len(model.vertices) - (len(model.vertices) % 3)
    for i in range(0, vertex_count, 3):
        p0, p1, p2 = projected[i], projected[i + 1], projected[i + 2]
        x0, y0, z0, q0 = p0
        x1, y1, z1, q1 = p1
        x2, y2, z2, q2 = p2
        area = _edge(x0, y0, x1, y1, x2, y2)
        if abs(area) < 1e-8:
            continue

        min_x = max(0, int(math.floor(min(x0, x1, x2))))
        max_x = min(size - 1, int(math.ceil(max(x0, x1, x2))))
        min_y = max(0, int(math.floor(min(y0, y1, y2))))
        max_y = min(size - 1, int(math.ceil(max(y0, y1, y2))))
        if min_x > max_x or min_y > max_y:
            continue

        v0 = model.vertices[i]
        v1 = model.vertices[i + 1]
        v2 = model.vertices[i + 2]
        u0, vv0 = v0.uv
        u1, vv1 = v1.uv
        u2, vv2 = v2.uv
        n0, n1, n2 = normals[i], normals[i + 1], normals[i + 2]

        c0, c1, c2 = v0.color, v1.color, v2.color
        inv_area = 1.0 / area
        positive = area > 0.0

        for py in range(min_y, max_y + 1):
            sy = py + 0.5
            for px in range(min_x, max_x + 1):
                sx = px + 0.5
                e0 = _edge(x1, y1, x2, y2, sx, sy)
                e1 = _edge(x2, y2, x0, y0, sx, sy)
                e2 = _edge(x0, y0, x1, y1, sx, sy)
                if positive:
                    if e0 < -1e-6 or e1 < -1e-6 or e2 < -1e-6:
                        continue
                else:
                    if e0 > 1e-6 or e1 > 1e-6 or e2 > 1e-6:
                        continue

                w0 = e0 * inv_area
                w1 = e1 * inv_area
                w2 = e2 * inv_area
                depth = w0 * z0 + w1 * z1 + w2 * z2
                idx = py * size + px
                if depth <= zbuffer[idx]:
                    continue

                denom = w0 * q0 + w1 * q1 + w2 * q2
                if abs(denom) < 1e-12:
                    continue
                pw0 = w0 * q0 / denom
                pw1 = w1 * q1 / denom
                pw2 = w2 * q2 / denom

                u = pw0 * u0 + pw1 * u1 + pw2 * u2
                vv = pw0 * vv0 + pw1 * vv1 + pw2 * vv2
                if textured:
                    tr, tg, tb, _ta = _texture_sample(model, u, vv)
                else:
                    tr = tg = tb = 255

                # PS2 fixed-function icon data commonly uses 0x80 as neutral vertex
                # colour. Preserve highlights above 0x80 instead of treating 255 as 1.0.
                if neutral_low_rgb:
                    vr = vg = vb = 1.0
                else:
                    vr = (pw0 * c0[0] + pw1 * c1[0] + pw2 * c2[0]) / 128.0
                    vg = (pw0 * c0[1] + pw1 * c1[1] + pw2 * c2[1]) / 128.0
                    vb = (pw0 * c0[2] + pw1 * c1[2] + pw2 * c2[2]) / 128.0
                va = (pw0 * c0[3] + pw1 * c1[3] + pw2 * c2[3]) / 128.0
                vr, vg, vb = (max(0.0, min(2.0, value)) for value in (vr, vg, vb))
                alpha = max(0.0, min(1.0, va)) if vertex_alpha_enabled else 1.0
                if alpha <= 0.002:
                    continue

                normal = (
                    pw0 * n0[0] + pw1 * n1[0] + pw2 * n2[0],
                    pw0 * n0[1] + pw1 * n1[1] + pw2 * n2[1],
                    pw0 * n0[2] + pw1 * n1[2] + pw2 * n2[2],
                )
                if lit:
                    lr, lg, lb = _lighting(icon_sys, normal)
                else:
                    lr = lg = lb = 1.0

                src = (
                    _clamp8(tr * vr * lr),
                    _clamp8(tg * vg * lg),
                    _clamp8(tb * vb * lb),
                )
                if alpha >= 0.995:
                    pixels[px, py] = src
                    zbuffer[idx] = depth
                else:
                    dst = pixels[px, py]
                    pixels[px, py] = tuple(
                        _clamp8(src[ch] * alpha + dst[ch] * (1.0 - alpha)) for ch in range(3)
                    )
                    # Semi-transparent surfaces still participate in depth testing to
                    # prevent the dense triangle soup seen in some icon previews.
                    zbuffer[idx] = depth

    return image



def render_builtin_ps2_icon(icon_sys: IconSys | None = None, *, size: int = 256) -> Image.Image:
    """Render a simple fallback for built-in PS2 browser icons.

    System/configuration saves sometimes reference ROM-resident icons via names such
    as ``_SCE`` instead of shipping a separate ICO/ICN model file. We do not have
    the console ROM assets here, so provide a small browser-style floppy/save icon
    instead of a blank placeholder.
    """
    image = Image.new("RGB", (size, size), (160, 160, 160))
    draw = ImageDraw.Draw(image)
    pad = int(size * 0.16)
    w = size - pad * 2
    h = int(w * 0.8)
    x0 = pad
    y0 = size // 2 - h // 2
    x1 = x0 + w
    y1 = y0 + h
    body = (30, 52, 135)
    body_hi = (60, 85, 185)
    label = (238, 241, 248)
    metal = (188, 192, 196)
    draw.rounded_rectangle((x0, y0, x1, y1), radius=int(size * 0.03), fill=body, outline=(20, 24, 40), width=2)
    draw.rectangle((x0, y0, x1, y0 + int(h * 0.18)), fill=body_hi)
    shutter_h = int(h * 0.24)
    draw.rectangle((x0 + int(w * 0.18), y0 + int(h * 0.07), x1 - int(w * 0.18), y0 + int(h * 0.07) + shutter_h), fill=metal, outline=(80, 80, 88))
    draw.rectangle((x0 + int(w * 0.38), y0 + int(h * 0.11), x1 - int(w * 0.38), y0 + int(h * 0.11) + int(shutter_h * 0.55)), fill=(120, 125, 130))
    draw.rectangle((x0 + int(w * 0.22), y0 + int(h * 0.44), x1 - int(w * 0.22), y0 + int(h * 0.7)), fill=label, outline=(0, 0, 0))
    draw.rectangle((x0 + int(w * 0.72), y0 + int(h * 0.5), x1 - int(w * 0.12), y0 + int(h * 0.64)), fill=(250, 205, 70), outline=(120, 96, 18))
    return image

def render_placeholder(text: str, *, size: int = 256) -> Image.Image:
    image = Image.new("RGB", (size, size), (32, 36, 48))
    draw = ImageDraw.Draw(image)
    lines = text.splitlines() or [text]
    y = size // 2 - (len(lines) * 10)
    for line in lines:
        bbox = draw.textbbox((0, 0), line)
        w = bbox[2] - bbox[0]
        draw.text(((size - w) // 2, y), line, fill=(220, 224, 234))
        y += 20
    return image
