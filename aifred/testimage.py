"""A tiny test picture for the vision test: a random number in block digits, as a PNG made with the standard library."""

from __future__ import annotations

import random
import struct
import zlib

_FONT = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
}
SCALE = 12
GAP = 1


def digits_png(text: str) -> bytes:
    """A black-on-white PNG of ``text`` (digits only), each pixel of the 5x7 font drawn ``SCALE`` times larger."""
    cols = len(text) * (5 + GAP) + GAP
    pixel_w, pixel_h = cols * SCALE, (7 + 2 * GAP) * SCALE
    rows = []
    for y in range(pixel_h):
        fy = y // SCALE - GAP
        line = bytearray()
        for x in range(pixel_w):
            fx = x // SCALE - GAP
            on = False
            if 0 <= fy < 7 and fx >= 0:
                index, col = divmod(fx, 5 + GAP)
                if index < len(text) and col < 5:
                    on = _FONT[text[index]][fy][col] == "1"
            line.append(0 if on else 255)
        rows.append(b"\x00" + bytes(line))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", pixel_w, pixel_h, 8, 0, 0, 0, 0)) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def random_number(digits: int = 4) -> str:
    """A number whose digits cannot be guessed from a previous test. The digits are all different: a small model
    that reads "4885" as "485" has read the picture, it has only lost a repeated digit."""
    return "".join(random.SystemRandom().sample("123456789", digits))
