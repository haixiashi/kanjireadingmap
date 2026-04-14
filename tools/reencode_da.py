#!/usr/bin/env python3
"""Base-93 codec library (rANS streaming).

Encodes bytes to base-93 string and decodes back using rANS-style
streaming base conversion. No BigInt needed in the JS decoder.

Used by reencode_bac.py, verify_data.py, and build.py.
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


def encode_b93(data):
    """Encode byte array to base-93 string using rANS-style streaming.

    Process bytes in reverse. For each byte:
      flush while state // 93 >= 0x10000: output state % 93, state //= 93
      state = state * 256 + byte
    Final: flush remaining state until 0.
    Output digits are reversed and converted to chars.
    """
    state = 1
    digits_rev = []

    for byte in reversed(data):
        while state // 93 >= 0x10000:
            digits_rev.append(state % 93)
            state //= 93
        state = state * 256 + byte

    while state > 0:
        digits_rev.append(state % 93)
        state //= 93

    digits_rev.reverse()
    return ''.join(digit_to_char(d) for d in digits_rev)


def decode_b93(s, num_bytes):
    """Decode base-93 string to byte array using rANS-style streaming.

    Read string left-to-right. Init by refilling state until >= 0x1000000.
    Extract bytes: byte = state % 256, state //= 256.
    Refill while state < 0x1000000.
    """
    pos = 0
    state = 0

    # Init: refill until state >= LOWER_BOUND
    while state < 0x1000000 and pos < len(s):
        state = state * 93 + char_to_digit(s[pos])
        pos += 1

    out = []
    while len(out) < num_bytes:
        out.append(state % 256)
        state //= 256
        while state < 0x1000000 and pos < len(s):
            state = state * 93 + char_to_digit(s[pos])
            pos += 1

    return out
