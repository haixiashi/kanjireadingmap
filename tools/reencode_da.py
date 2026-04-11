#!/usr/bin/env python3
"""Base-93 codec library (2:13 block code, 85 bits per 13 chars).

Used by reencode_bac.py and verify_data.py.
"""


def digit_to_char(d):
    """Convert digit 0-92 to printable ASCII char, skipping \" and \\."""
    if d >= 59:
        d += 1  # skip \ (0x5C = 92, offset 60 from 0x20, but after " skip = 59)
    if d >= 2:
        d += 1  # skip " (0x22 = 34, offset 2 from 0x20)
    return chr(d + 0x20)


def char_to_digit(c):
    """Convert printable ASCII char to digit 0-92, inverse of digit_to_char."""
    d = ord(c) - 0x20
    if d > 2:
        d -= 1  # undo " skip
    if d > 59:
        d -= 1  # undo \ skip
    return d


def decode_b93(s):
    """Decode base-93 string to bit array. 13 chars -> 85 bits."""
    P = 2 ** 32
    bits = []
    for i in range(0, len(s), 13):
        l = m = h = 0
        for j in range(13):
            d = char_to_digit(s[i + j]) if i + j < len(s) else 0
            v = l * 93 + d; l = v % P; c = (v - l) // P
            v = m * 93 + c; m = v % P; c = (v - m) // P
            h = h * 93 + c
        for j in range(84, -1, -1):
            if j > 63:
                bits.append((h >> (j - 64)) & 1)
            elif j > 31:
                bits.append((m >> (j - 32)) & 1)
            else:
                bits.append((l >> j) & 1)
    return bits


def encode_b93(bits):
    """Encode bit array to base-93 string. 85 bits -> 13 chars."""
    P = 2 ** 32
    while len(bits) % 85 != 0:
        bits.append(0)
    chars = []
    for i in range(0, len(bits), 85):
        block = bits[i:i + 85]
        hi = mi = lo = 0
        for j in range(21):
            hi = (hi << 1) | block[j]
        for j in range(32):
            mi = (mi << 1) | block[21 + j]
        for j in range(32):
            lo = (lo << 1) | block[53 + j]
        digits = []
        for _ in range(13):
            r = hi % 93; hi = hi // 93
            v = r * P + mi; r = v % 93; mi = v // 93
            v = r * P + lo; r = v % 93; lo = v // 93
            digits.append(r)
        digits.reverse()
        chars.extend(digit_to_char(d) for d in digits)
    return ''.join(chars)
