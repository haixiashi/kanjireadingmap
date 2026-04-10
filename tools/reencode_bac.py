#!/usr/bin/env python3
"""BAC encoder for DA data.

Encodes cell data from snapshot.json using binary arithmetic coding
with 12 probability models (999-scale) for low-cardinality fields
and uniform encoding for high-cardinality fields (U(k) where k=log2(n)).

Architecture:
1. Arithmetic encode symbols → bit stream (24-bit range coder)
2. Pack bits into base-93 (13:85 block code)
3. Verify round-trip with built-in ArithDecoder before outputting
"""

import json
import os
import re
import sys
from collections import Counter

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS_DIR)
from reencode_da import encode_b93, decode_b93, digit_to_char, char_to_digit

# 24-bit arithmetic coder
BITS = 24
MASK = (1 << BITS) - 1
TOP = 1 << (BITS - 1)
QTR = 1 << (BITS - 2)


class ArithEncoder:
    def __init__(self):
        self.mn = 0
        self.mx = MASK
        self.pd = 0
        self.bits = []

    def encode_model(self, cum, sym):
        """Encode using cumulative frequency array. total = cum[-1]."""
        t = cum[-1]
        r = self.mx - self.mn + 1
        self.mx = self.mn + r * cum[sym + 1] // t - 1 if sym < len(cum) - 2 else self.mx
        self.mn = self.mn + r * cum[sym] // t
        self._norm()

    def encode_uniform(self, val, n):
        """Encode uniform symbol 0..n-1."""
        r = self.mx - self.mn + 1
        q = r // n
        self.mx = self.mn + q * (val + 1) - 1 if val < n - 1 else self.mx
        self.mn = self.mn + q * val
        self._norm()

    def _norm(self):
        while True:
            if self.mx < TOP:
                self._emit(0)
            elif self.mn >= TOP:
                self._emit(1)
                self.mn -= TOP
                self.mx -= TOP
            elif self.mn >= QTR and self.mx < 3 * QTR:
                self.pd += 1
                self.mn -= QTR
                self.mx -= QTR
            else:
                break
            self.mn = (self.mn << 1) & MASK
            self.mx = ((self.mx << 1) | 1) & MASK

    def _emit(self, bit):
        self.bits.append(bit)
        for _ in range(self.pd):
            self.bits.append(bit ^ 1)
        self.pd = 0

    def finish(self):
        self.pd += 1
        self._emit(0 if self.mn < QTR else 1)
        return self.bits


class ArithDecoder:
    """For verification only. Must match encoder exactly."""

    def __init__(self, bits):
        self.mn = 0
        self.mx = MASK
        self.pk = 0
        self.p = 0
        self.bits = bits
        for _ in range(24):
            self.pk = (self.pk << 1 | self._rb()) & MASK

    def _rb(self):
        b = self.bits[self.p] if self.p < len(self.bits) else 0
        self.p += 1
        return b

    def _norm(self):
        while True:
            if self.mn >= TOP:
                self.mn -= TOP; self.mx -= TOP; self.pk -= TOP
            elif self.mx < TOP:
                pass
            elif self.mn >= QTR and self.mx < 3 * QTR:
                self.mn -= QTR; self.mx -= QTR; self.pk -= QTR
            else:
                break
            self.mn = (self.mn << 1) & MASK
            self.mx = ((self.mx << 1) | 1) & MASK
            self.pk = (self.pk << 1 | self._rb()) & MASK

    def decode_model(self, cum):
        """Decode using step-based lookup (matches encoder boundaries)."""
        t = cum[-1]
        r = self.mx - self.mn + 1
        s = 0
        o = self.mn
        while s < len(cum) - 2 and o + r * cum[s + 1] // t <= self.pk:
            s += 1
        self.mn = o + r * cum[s] // t
        if s < len(cum) - 2:
            self.mx = o + r * cum[s + 1] // t - 1
        self._norm()
        return s

    def decode_uniform(self, n):
        """Decode uniform symbol 0..n-1."""
        r = self.mx - self.mn + 1
        q = r // n
        s = (self.pk - self.mn) // q
        if s >= n:
            s = n - 1
        o = self.mn
        self.mn = o + q * s
        if s < n - 1:
            self.mx = o + q * (s + 1) - 1
        self._norm()
        return s


def decode_kd(kd_str):
    """Decode KD string to KT list."""
    bits = decode_b93(kd_str)
    kt = [chr(0x4E00)]; cp = 0x4E00; p = 0
    def read(n):
        nonlocal p; v = 0
        for _ in range(n): v = v * 2 + bits[p]; p += 1
        return v
    for _ in range(2047):
        if read(1):
            if read(1):
                if read(1): cp += read(9) + 85
                else: cp += read(6) + 21
            else: cp += read(4) + 5
        else: cp += read(2) + 1
        kt.append(chr(cp))
    return kt


# Probability models (cumulative frequencies)
# All-uniform versions for initial testing
def uniform_cum(n):
    return list(range(n + 1))

# Non-uniform models (enable one at a time)
M_CELL = [0, 555, 999]              # cell_present: empty/non-empty
M_KTYPE = [0, 472, 531, 999]       # kanji_type: kt/raw/term
M_ONKUN = [0, 628, 999]            # on_kun: kun/on
M_TIER = [0, 191, 477, 597, 769, 932, 999]  # tier_idx 0-5
M_D1 = [0, 885, 999]              # d1: 0/1
M_D2_0 = [0, 71, 886, 999]        # d2 when d1=0: -1/0/1
M_D2_1 = [0, 199, 998, 999]       # d2 when d1=1: -1/0/1
M_EXTRA = [0, 794, 999]            # extra_rd_flag: no/yes
M_KANA = [0, 420, 786, 999]        # kana_type: k4/k6/raw
M_OKURI = [0, 585, 999]            # okurigana_flag: done/more
M_K4 = [0, 452, 685, 859, 999]    # K4 kana index: る/う/い/く
M_KD_CASE = [0, 459, 877, 993, 999]  # KD delta bucket: 2b/4b/6b/9b


def encode_kd(kt):
    """Encode KT list into arithmetic-coded KD string."""
    enc = ArithEncoder()
    ops = []

    def em(cum, sym):
        enc.encode_model(cum, sym)
        ops.append(('M', cum, sym))

    def eu(val, n):
        enc.encode_uniform(val, n)
        ops.append(('U', n, val))

    prev = ord(kt[0])  # 0x4E00
    for i in range(1, len(kt)):
        delta = ord(kt[i]) - prev
        prev = ord(kt[i])
        if delta <= 4:
            em(M_KD_CASE, 0)
            eu(delta - 1, 4)
        elif delta <= 20:
            em(M_KD_CASE, 1)
            eu(delta - 5, 16)
        elif delta <= 84:
            em(M_KD_CASE, 2)
            eu(delta - 21, 64)
        else:
            em(M_KD_CASE, 3)
            eu(delta - 85, 512)

    bits = enc.finish()

    # Verify
    dec = ArithDecoder(bits)
    errors = 0
    for i, op in enumerate(ops):
        if op[0] == 'M':
            got = dec.decode_model(op[1])
            if got != op[2]:
                print(f"KD Op {i}: M got {got} expected {op[2]}", file=sys.stderr)
                errors += 1
                if errors > 3:
                    break
        else:
            got = dec.decode_uniform(op[1])
            if got != op[2]:
                print(f"KD Op {i}: U({op[1]}) got {got} expected {op[2]}", file=sys.stderr)
                errors += 1
                if errors > 3:
                    break

    print(f"KD: {len(ops)} ops, {len(bits)} bits, verify: {errors} errors", file=sys.stderr)
    return bits, errors


def main():
    with open(os.path.join(TOOLS_DIR, 'snapshot.json')) as f:
        snap = json.load(f)
    with open(os.path.join(TOOLS_DIR, '..', 'index.html')) as f:
        src = f.read()

    kd_str = re.search(r'KD="([^"]*)"', src).group(1)
    kt = decode_kd(kd_str)
    kt_index = {c: i for i, c in enumerate(kt)}

    kana_str = 'あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわん'
    H = 12318; K4 = "m(&1"; K6 = ";b9c*-knl3`LFqJ."
    k4_codes = {ord(c): i for i, c in enumerate(K4)}
    k6_codes = {ord(c): i for i, c in enumerate(K6)}
    tier_to_idx = {3: 0, 4: 1, 5: 2, 2: 3, 1: 4, 6: 5}

    enc = ArithEncoder()
    ops = []  # for verification

    def em(cum, sym):
        enc.encode_model(cum, sym)
        ops.append(('M', cum, sym))

    def eu(val, n):
        enc.encode_uniform(val, n)
        ops.append(('U', n, val))

    for ri in range(44):
        row = kana_str[ri]
        for ci in range(46):
            col = '' if ci == 0 else kana_str[ci - 1]
            cell_kana = row + col
            cell_key = row + '+' + col
            entries = snap.get(cell_key, [])

            if not entries:
                em(M_CELL, 0)
                continue
            em(M_CELL, 1)

            parsed = []
            for e in entries:
                kanji = e[1]; rest = e[2:]
                parts = rest.split('|', 1) if '|' in rest else [rest, '']
                is_on = any(0x30A0 <= ord(c) <= 0x30FF for c in parts[0]) if parts[0] else False
                parsed.append((kanji, int(e[0]), parts[0], parts[1], is_on))

            groups = []
            for kanji, tier, furigana, okurigana, is_on in parsed:
                key = (tier, furigana, okurigana, is_on)
                if groups and groups[-1][0] == key:
                    groups[-1][1].append(kanji)
                else:
                    groups.append((key, [kanji]))

            for (tier, furigana, okurigana, is_on), kanji_list in groups:
                encodable = [k for k in kanji_list
                             if k in kt_index or (0x4E00 <= ord(k) < 0x4E00 + 32768)]
                if not encodable:
                    continue

                for kc in encodable:
                    if kc in kt_index:
                        em(M_KTYPE, 0)
                        eu(kt_index[kc], 2048)
                    else:
                        em(M_KTYPE, 1)
                        eu(ord(kc) - 0x4E00, 32768)
                em(M_KTYPE, 2)

                em(M_ONKUN, 1 if is_on else 0)
                em(M_TIER, tier_to_idx[tier])

                ko = 96 if is_on else 0
                prefix = cell_kana
                exp = [ord(c) + ko for c in prefix]
                act = [ord(c) for c in furigana[:len(prefix)]]
                d1 = act[0] - exp[0] if act else 0
                d2 = act[1] - exp[1] if len(act) > 1 and len(exp) > 1 else 0
                if len(prefix) <= 1:
                    d2 = 0
                em(M_D1, d1)
                em(M_D2_1 if d1 else M_D2_0, d2 + 1)  # d2 is -1/0/1, encode as 0/1/2

                extra = furigana[len(prefix):]
                for c in extra:
                    em(M_EXTRA, 1)
                    code = ord(c) - H - ko
                    if code in k4_codes:
                        em(M_KANA, 0)
                        em(M_K4, k4_codes[code])
                    elif code in k6_codes:
                        em(M_KANA, 1)
                        eu(k6_codes[code], 16)
                    else:
                        em(M_KANA, 2)
                        eu(code, 128)
                em(M_EXTRA, 0)

                if not is_on:
                    for c in okurigana:
                        em(M_OKURI, 1)
                        code = ord(c) - H
                        if code in k4_codes:
                            em(M_KANA, 0)
                            em(M_K4, k4_codes[code])
                        elif code in k6_codes:
                            em(M_KANA, 1)
                            eu(k6_codes[code], 16)
                        else:
                            em(M_KANA, 2)
                            eu(code, 128)
                    em(M_OKURI, 0)

            em(M_KTYPE, 2)  # end of cell

    bits = enc.finish()
    print(f"Ops: {len(ops)}, bits: {len(bits)}", file=sys.stderr)

    # Verify decode
    dec = ArithDecoder(bits)
    errors = 0
    for i, op in enumerate(ops):
        if op[0] == 'M':
            got = dec.decode_model(op[1])
            if got != op[2]:
                print(f"Op {i}: M got {got} expected {op[2]}", file=sys.stderr)
                errors += 1
                if errors > 3:
                    break
        else:
            got = dec.decode_uniform(op[1])
            if got != op[2]:
                print(f"Op {i}: U({op[1]}) got {got} expected {op[2]}", file=sys.stderr)
                errors += 1
                if errors > 3:
                    break
    print(f"Verify: {errors} errors in {len(ops)} ops", file=sys.stderr)

    if errors == 0:
        da_str = encode_b93(bits)
        print(f"DA: {len(da_str)} chars", file=sys.stderr)
        old_da = re.search(r'DA="([^"]*)"', src).group(1)
        print(f"Old DA: {len(old_da)} chars, saving: {len(old_da) - len(da_str)}", file=sys.stderr)
    else:
        print("DA FAILED - not writing output", file=sys.stderr)
        sys.exit(1)

    # Encode KD
    kd_bits, kd_errors = encode_kd(kt)
    if kd_errors == 0:
        kd_new = encode_b93(kd_bits)
        print(f"KD: {len(kd_new)} chars", file=sys.stderr)
        old_kd = re.search(r'KD="([^"]*)"', src).group(1)
        print(f"Old KD: {len(old_kd)} chars, saving: {len(old_kd) - len(kd_new)}", file=sys.stderr)
    else:
        print("KD FAILED", file=sys.stderr)
        sys.exit(1)

    # Output both: KD\nDA
    sys.stdout.write(kd_new + '\n' + da_str)


if __name__ == '__main__':
    main()
