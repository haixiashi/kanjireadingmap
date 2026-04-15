#!/usr/bin/env python3
"""Verify that index.html DA string decodes to match the canonical snapshot.

Usage: python3 tools/verify_data.py

Exits 0 if all entries match, 1 if there are mismatches.
The snapshot (src/data.json) is the authoritative reference derived
from commit aa96857. Any data-encoding change should be verified against it.
"""

import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
SRC_DIR = os.path.join(ROOT_DIR, 'src')
SNAPSHOT_PATH = os.path.join(SRC_DIR, 'data.json')
INDEX_PATH = os.path.join(ROOT_DIR, 'index.html')

# 32-bit arithmetic decoder (must match JS decoder exactly)
BITS = 32
MASK = (1 << BITS) - 1
TOP = 1 << (BITS - 1)
QTR = 1 << (BITS - 2)


def decode_bootstrap_b93(s):
    """Match the inline JS B(s) decoder used by index.html exactly."""
    pos = 0
    state = 0
    out = []
    while True:
        while state < 0x1000000 and pos < len(s):
            state = state * 93 + (ord(s[pos]) + 26) * 58 // 59 - 57
            pos += 1
        out.append(state & 255)
        state >>= 8
        if state <= 1:
            return out


class ArithDecoder:
    def __init__(self, byte_data):
        self.mn = 0
        self.mx = MASK
        self.pk = 0
        self.p = 0
        self.bytes = byte_data
        for _ in range(BITS):
            self.pk = (self.pk << 1 | self._rb()) & MASK

    def _rb(self):
        byte_idx = self.p >> 3
        # Match the encoder and JS sentinel reader: bytes are packed LSB-first.
        bit_idx = self.p & 7
        b = (self.bytes[byte_idx] >> bit_idx) & 1 if byte_idx < len(self.bytes) else 0
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
        """Decode using cumulative frequency array (999-scale).

        cum is the INNER array (without leading 0 or trailing 999).
        E.g. [555] for cell_present means boundaries [0, 555, 999].
        """
        total = 999
        r = self.mx - self.mn + 1
        # Step-based lookup matching encoder boundaries
        s = 0
        o = self.mn
        while s < len(cum) and o + (r * cum[s] // total) <= self.pk:
            s += 1
        # Update range
        if s > 0:
            self.mn = o + r * cum[s - 1] // total
        if s < len(cum):
            self.mx = o + r * cum[s] // total - 1
        self._norm()
        return s

    def decode_uniform(self, n):
        """Decode uniform symbol 0..n-1."""
        r = self.mx - self.mn + 1
        q = r // n
        s = (self.pk - self.mn) // q
        if s >= n:
            s = n - 1
        self.mn = self.mn + q * s
        if s < n - 1:
            self.mx = self.mn + q - 1
        self._norm()
        return s


def count_kanji(snapshot):
    """Count unique kanji in snapshot."""
    kanji = set()
    for entries in snapshot.values():
        for e in entries:
            cp = ord(e[1])
            if 0x4E00 <= cp < 0x10000:
                kanji.add(e[1])
    return len(kanji)


def decode_kt_from_decoder(dec, kt_count):
    """Decode KT from an existing arithmetic decoder."""
    from reencode_bac import M_KD_CASE
    KD_CASE = M_KD_CASE[1:-1]
    kt = [chr(0x4E00)]
    cp = 0x4E00

    for _ in range(kt_count - 1):
        delta_range = 2 << dec.decode_model(KD_CASE)
        cp += dec.decode_uniform(delta_range) + delta_range - 1
        kt.append(chr(cp))
    return kt


def decode_da_from_decoder(dec, kt):
    """Decode cell data from an existing arithmetic decoder."""
    FC = chr
    H = 0x3042

    # Probability tables — computed from snapshot via encoder
    import json as _json
    from reencode_bac import compute_models
    with open(SNAPSHOT_PATH) as _f:
        compute_models(_json.load(_f))
    from reencode_bac import (M_CELL, M_KT0, M_KT1, M_ONKUN, M_TDP,
                               M_D1K, M_D1O, M_D2_0, M_D2_1, M_EXTRA, M_OKURI)
    CP = M_CELL[1:-1]
    KT0 = [m[1:-1] for m in M_KT0]
    KT1 = M_KT1[1:-1]
    OK = [m[1:-1] for m in M_ONKUN]
    TDP = [None] + [m[1:-1] for m in M_TDP[1:]]
    D1K = M_D1K[1:-1]
    D1O = M_D1O[1:-1]
    D2_0 = M_D2_0[1:-1]
    D2_1 = M_D2_1[1:-1]
    EF = M_EXTRA[1:-1]
    OF = M_OKURI[1:-1]

    def Z(c):
        return dec.decode_model(c)

    def U(n):
        return dec.decode_uniform(n)

    # Read kana prob table from stream (82 symbols, k² deltas)
    KA = []
    v = 0
    for _ in range(81):
        k = U(14)
        v += k * k
        KA.append(v)

    # Read KN (kana mapping) from stream - delta encoded
    v_kn = 0
    kana_str = chr(H)
    for _ in range(44):
        v_kn += U(4) + 1
        kana_str += chr(v_kn + H)

    def RK(f):
        return Z(KA) + H + f

    cells = {}

    for ri in range(44):
        row_kana = kana_str[ri]
        for ci in range(46):
            col_kana = '' if ci == 0 else kana_str[ci - 1]
            pf = row_kana + col_kana
            cell_key = row_kana + '+' + col_kana

            if ci > 0 and Z(CP) == 0:
                continue

            entries = []
            pt = 5
            ok_score = 0
            while True:
                kl = []
                if Z(KT0[pt - 1]):  # conditioned on pt
                    break
                kl.append(kt[U(len(kt))])
                while not Z(KT1):
                    kl.append(kt[U(len(kt))])

                on = Z(OK[max(-1, min(2, ok_score)) + 1])
                if pt > 1:
                    pt -= Z(TDP[pt])
                tier = pt
                tr = str(tier)
                ok_score += 1 if on else -1
                d1 = Z(D1O if on else D1K)
                d2 = Z(D2_1 if d1 else D2_0) - 1
                ko = on * 96

                pr = ''
                for ci2, c in enumerate(pf):
                    pr += FC(ord(c) + ko + (d2 if ci2 else d1))

                rd = ''
                while Z(EF):
                    rd += FC(RK(ko))

                sf = ''
                while not on and Z(OF):
                    sf += FC(RK(0))

                t = pr + rd + tr + sf
                for k in kl:
                    entries.append(k + t)

            if entries:
                cells[cell_key] = entries

    return cells, kana_str


def main():
    with open(SNAPSHOT_PATH, 'r') as f:
        snapshot = json.load(f)

    with open(INDEX_PATH, 'r') as f:
        src = f.read()

    dd = re.search(r'D="([^"]*)"', src).group(1)
    # kana_str is decoded from the DD stream

    # Decode everything from single stream
    # index.html decodes D with the inline B(s) helper, then consumes bytes
    # via pop(); reverse to turn that into forward sequential access here.
    byte_data = decode_bootstrap_b93(dd)[::-1]
    dec = ArithDecoder(byte_data)
    kt = decode_kt_from_decoder(dec, count_kanji(snapshot))
    decoded, kana_str = decode_da_from_decoder(dec, kt)

    errors = 0
    warnings = 0

    # Check all snapshot cells exist in decoded
    for cell_key, expected_entries in snapshot.items():
        # Filter entries that can't be encoded (U+10000+)
        encodable = [e for e in expected_entries if ord(e[1]) < 0x10000]

        actual = decoded.get(cell_key, [])

        if len(actual) != len(encodable):
            print(f'MISMATCH {cell_key}: {len(actual)} entries decoded vs {len(encodable)} expected')
            errors += 1
            continue

        # Compare by converting decoded entries back to original format
        for i, (act, exp) in enumerate(zip(actual, encodable)):
            exp_fmt = exp[1:]  # strip tier prefix
            tier = exp[0]
            # Reconstruct: kanji + reading + tier + okurigana
            kanji = ''
            j = 0
            while j < len(exp_fmt):
                if 0x3040 <= ord(exp_fmt[j]) <= 0x30FF:
                    break
                kanji += exp_fmt[j]
                j += 1
            reading_okuri = exp_fmt[j:]
            parts = reading_okuri.split('|', 1) if '|' in reading_okuri else [reading_okuri, '']
            expected_decoded = kanji + parts[0] + tier + parts[1]

            if act != expected_decoded:
                print(f'MISMATCH {cell_key}[{i}]: got "{act}", expected "{expected_decoded}"')
                errors += 1

    # Check for extra cells in decoded that aren't in snapshot
    for cell_key in decoded:
        if cell_key not in snapshot:
            print(f'EXTRA cell {cell_key} with {len(decoded[cell_key])} entries (not in snapshot)')
            warnings += 1

    total_expected = sum(len(v) for v in snapshot.values())
    total_encodable = sum(len([e for e in v if ord(e[1]) < 0x10000]) for v in snapshot.values())
    total_decoded = sum(len(v) for v in decoded.values())

    print(f'\nSnapshot: {total_expected} entries ({total_expected - total_encodable} unencodable)')
    print(f'Decoded:  {total_decoded} entries')
    print(f'Errors:   {errors}')
    if warnings:
        print(f'Warnings: {warnings}')

    if errors:
        print('\nFAILED: Data does not match snapshot!')
        sys.exit(1)
    else:
        print('\nPASSED: All entries match snapshot.')
        sys.exit(0)


if __name__ == '__main__':
    main()
