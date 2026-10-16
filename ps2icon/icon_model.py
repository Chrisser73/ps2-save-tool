from __future__ import annotations

from dataclasses import dataclass
import math
import struct


class IconModelError(ValueError):
    pass


@dataclass(slots=True)
class Vertex:
    shapes: list[tuple[float, float, float]]
    normal: tuple[float, float, float]
    uv: tuple[float, float]
    color: tuple[int, int, int, int]


@dataclass(slots=True)
class AnimationTrack:
    shape_id: int
    keys: list[tuple[float, float]]


@dataclass(slots=True)
class IconModel:
    shape_count: int
    texture_type: int
    vertices: list[Vertex]
    animation_length: float
    animation_speed: float
    tracks: list[AnimationTrack]
    texture_rgba: bytes | None
    texture_width: int = 128
    texture_height: int = 128

    def shape_weights(self, seconds: float) -> list[float]:
        """Return sequential morph weights for the icon's animation shapes.

        PS2 icons store multiple geometry shapes. For preview purposes the browser-like
        behaviour is to move through those shapes at 60 animation frames per second,
        scaled by the icon animation speed, and tween to the next shape.
        """
        count = max(1, self.shape_count)
        weights = [0.0] * count
        if count == 1 or self.animation_length <= 0:
            weights[0] = 1.0
            return weights

        speed = max(0.01, float(self.animation_speed or 1.0))
        frame_length = max(1.0, float(self.animation_length))
        current_frame = (seconds * 60.0 * speed) % frame_length
        frames_per_shape = frame_length / count
        if frames_per_shape <= 0:
            weights[0] = 1.0
            return weights

        current = min(count - 1, int(current_frame // frames_per_shape))
        local = (current_frame - current * frames_per_shape) / frames_per_shape
        next_shape = (current + 1) % count
        weights[current] = 1.0 - local
        weights[next_shape] += local
        return weights


def _interpolate(keys: list[tuple[float, float]], t: float, length: float) -> float:
    ordered = sorted(keys, key=lambda item: item[0])
    if len(ordered) == 1:
        return ordered[0][1]
    if t <= ordered[0][0]:
        # Cyclic interpolation from the final key back to the first key.
        t0, v0 = ordered[-1]
        t1, v1 = ordered[0][0] + length, ordered[0][1]
        tt = t + length
    else:
        for i in range(len(ordered) - 1):
            if ordered[i][0] <= t <= ordered[i + 1][0]:
                t0, v0 = ordered[i]
                t1, v1 = ordered[i + 1]
                break
        else:
            t0, v0 = ordered[-1]
            t1, v1 = ordered[0][0] + length, ordered[0][1]
            if t < t0:
                t += length
            tt = t
        if 'tt' not in locals():
            tt = t
    span = t1 - t0
    if abs(span) < 1e-9:
        return v1
    factor = (tt - t0) / span
    return v0 + (v1 - v0) * factor


def _decode_texture_1555(raw: bytes) -> bytes:
    if len(raw) < 128 * 128 * 2:
        raise IconModelError("PS2 icon texture is truncated")
    out = bytearray(128 * 128 * 4)
    for i in range(128 * 128):
        value = struct.unpack_from("<H", raw, i * 2)[0]
        r = (value & 0x1F) * 255 // 31
        g = ((value >> 5) & 0x1F) * 255 // 31
        b = ((value >> 10) & 0x1F) * 255 // 31
        # The high bit is documented as unused/don't-care by multiple PS2 icon
        # references. Treat black (0x0000) as a valid opaque texel; transparency
        # comes from the per-vertex alpha value instead.
        j = i * 4
        out[j : j + 4] = bytes((r, g, b, 255))
    return bytes(out)


def _decompress_texture(data: bytes, expected: int = 128 * 128 * 2) -> bytes:
    """Decode the ICN 16-bit RLE stream used by compressed PS2 icon textures."""
    out = bytearray()
    pos = 0
    while pos + 2 <= len(data) and len(out) < expected:
        code = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        if code & 0x8000:
            count = 0x10000 - code
            literal_bytes = count * 2
            if pos + literal_bytes > len(data):
                raise IconModelError("Compressed icon texture literal run is truncated")
            if len(out) + literal_bytes > expected:
                raise IconModelError("Compressed icon texture expands beyond 128x128")
            out += data[pos : pos + literal_bytes]
            pos += literal_bytes
        else:
            if pos + 2 > len(data):
                raise IconModelError("Compressed icon texture repeat run is truncated")
            pixel = data[pos : pos + 2]
            pos += 2
            literal_bytes = code * 2
            if len(out) + literal_bytes > expected:
                raise IconModelError("Compressed icon texture expands beyond 128x128")
            out += pixel * code
    if len(out) != expected:
        raise IconModelError("Compressed icon texture did not produce a full 128x128 texture")
    return bytes(out)


def parse_icon_model(data: bytes) -> IconModel:
    if len(data) < 20:
        raise IconModelError("PS2 icon model is too small")
    magic, shape_count, texture_type, _unknown, vertex_count = struct.unpack_from("<5I", data, 0)
    if magic != 0x00010000:
        raise IconModelError(f"Unsupported PS2 icon model magic 0x{magic:08X}")
    if not (1 <= shape_count <= 64):
        raise IconModelError(f"Invalid animation shape count: {shape_count}")
    if not (3 <= vertex_count <= 200000):
        raise IconModelError(f"Invalid icon vertex count: {vertex_count}")

    pos = 20
    vertices: list[Vertex] = []
    bytes_per_vertex = shape_count * 8 + 16
    required = pos + vertex_count * bytes_per_vertex
    if required > len(data):
        raise IconModelError("PS2 icon vertex table is truncated")

    for _ in range(vertex_count):
        shapes: list[tuple[float, float, float]] = []
        for _shape in range(shape_count):
            x, y, z, _pad = struct.unpack_from("<hhhH", data, pos)
            pos += 8
            shapes.append((x / 4096.0, -y / 4096.0, -z / 4096.0))
        nx, ny, nz, _pad, u, v, r, g, b, a = struct.unpack_from("<hhhHhhBBBB", data, pos)
        pos += 16
        vertices.append(
            Vertex(
                shapes=shapes,
                normal=(nx / 4096.0, -ny / 4096.0, -nz / 4096.0),
                uv=(u / 4096.0, v / 4096.0),
                color=(r, g, b, a),
            )
        )

    animation_length = 0.0
    animation_speed = 1.0
    tracks: list[AnimationTrack] = []

    # Animation blocks immediately follow the vertex table. Models without a valid
    # animation block are still useful as static previews.
    if pos + 20 <= len(data):
        tag, frame_length, speed, _play_offset, frame_count = struct.unpack_from("<IIfII", data, pos)
        if tag == 1 and frame_count < 4096 and math.isfinite(speed):
            pos += 20
            animation_length = float(frame_length)
            animation_speed = float(speed) if speed > 0 else 1.0
            try:
                for _ in range(frame_count):
                    if pos + 16 > len(data):
                        raise IconModelError("Icon animation frame table is truncated")
                    shape_id, key_count_raw, _u1, _u2 = struct.unpack_from("<4I", data, pos)
                    pos += 16
                    # Sony's structure stores one more than the number of explicit keys.
                    key_count = max(0, key_count_raw - 1)
                    if key_count > 65535 or pos + key_count * 8 > len(data):
                        raise IconModelError("Icon animation key table is invalid")
                    keys: list[tuple[float, float]] = []
                    for _k in range(key_count):
                        key_time, value = struct.unpack_from("<ff", data, pos)
                        pos += 8
                        if math.isfinite(key_time) and math.isfinite(value):
                            keys.append((float(key_time), float(value)))
                    if shape_id == 0 and (not keys or keys[0][0] > 0.0):
                        keys.insert(0, (0.0, 1.0))
                    tracks.append(AnimationTrack(int(shape_id), keys))
            except IconModelError:
                # Keep a valid mesh even if animation metadata is damaged.
                tracks.clear()
                animation_length = 0.0
                animation_speed = 1.0

    texture_rgba: bytes | None = None
    if texture_type & 0x04:
        remaining = data[pos:]
        raw_texture: bytes
        if texture_type & 0x08:
            if len(remaining) < 4:
                raise IconModelError("Compressed icon texture header is truncated")
            compressed_size = struct.unpack_from("<I", remaining, 0)[0]
            if compressed_size > len(remaining) - 4:
                raise IconModelError("Compressed icon texture is truncated")
            raw_texture = _decompress_texture(remaining[4 : 4 + compressed_size])
        else:
            raw_texture = remaining[: 128 * 128 * 2]
        texture_rgba = _decode_texture_1555(raw_texture)

    return IconModel(
        shape_count=shape_count,
        texture_type=texture_type,
        vertices=vertices,
        animation_length=animation_length,
        animation_speed=animation_speed,
        tracks=tracks,
        texture_rgba=texture_rgba,
    )
