#!/usr/bin/env python3
"""Verify that index.html DA string decodes to match the canonical snapshot.

Usage: python3 tools/verify_data.py

Exits 0 if all entries match, 1 if there are mismatches.
The snapshot (tools/snapshot.json) is the authoritative reference derived
from commit aa96857. Any data-encoding change should be verified against it.
"""

import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
SNAPSHOT_PATH = os.path.join(SCRIPT_DIR, 'snapshot.json')
INDEX_PATH = os.path.join(ROOT_DIR, 'index.html')

sys.path.insert(0, SCRIPT_DIR)
from reencode_da import decode_b93


# 24-bit arithmetic decoder (must match JS decoder exactly)
BITS = 24
MASK = (1 << BITS) - 1
TOP = 1 << (BITS - 1)
QTR = 1 << (BITS - 2)


class ArithDecoder:
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


def build_kt(kd_str):
    """Decode KD string using arithmetic decoder (matching JS)."""
    bits = decode_b93(kd_str)
    dec = ArithDecoder(bits)
    KD_CASE = [459, 877, 993]
    kt = [chr(0x4E00)]
    cp = 0x4E00
    bit_counts = [4, 16, 64, 512]
    offsets = [1, 5, 21, 85]

    for _ in range(2047):
        q = dec.decode_model(KD_CASE)
        cp += dec.decode_uniform(bit_counts[q]) + offsets[q]
        kt.append(chr(cp))
    return kt


def decode_da(da_str, kt, kana_str):
    """Decode DA string using arithmetic decoder, matching JS DC()."""
    bits = decode_b93(da_str)
    dec = ArithDecoder(bits)
    FC = chr
    H = 12318
    K4 = "m(&1"
    K6 = ";b9c*-knl3`LFqJ."

    # Probability tables (999-scale, inner values only)
    CP = [555]
    KY = [472, 531]
    OK = [628]
    TI = [191, 477, 597, 769, 932]
    D1 = [885]
    D2_0 = [71, 886]
    D2_1 = [199, 998]
    EF = [794]
    KF = [420, 786]
    OF = [585]
    K4M = [452, 685, 859]

    k4_codes = {ord(c): i for i, c in enumerate(K4)}
    k6_codes = {ord(c): i for i, c in enumerate(K6)}

    def Z(c):
        return dec.decode_model(c)

    def U(k):
        return dec.decode_uniform(k)

    def RK(f):
        l = Z(KF)
        if l == 0:
            return ord(K4[Z(K4M)]) + H + f
        elif l == 1:
            return ord(K6[U(16)]) + H + f
        else:
            return U(118) + H + f

    cells = {}

    for ri in range(44):
        row_kana = kana_str[ri]
        for ci in range(46):
            col_kana = '' if ci == 0 else kana_str[ci - 1]
            pf = row_kana + col_kana
            cell_key = row_kana + '+' + col_kana

            if Z(CP) == 0:
                continue

            entries = []
            while True:
                kl = []
                while True:
                    j = Z(KY)
                    if j == 2:
                        break
                    if j == 1:
                        kl.append(FC(U(20667) + 19968))
                    else:
                        kl.append(kt[U(2048)])
                if not kl:
                    break

                on = Z(OK)
                tr = '345216'[Z(TI)]
                d1 = Z(D1)
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

    return cells


def main():
    with open(SNAPSHOT_PATH, 'r') as f:
        snapshot = json.load(f)

    with open(INDEX_PATH, 'r') as f:
        src = f.read()

    kd = re.search(r'KD="([^"]*)"', src).group(1)
    da = re.search(r'DA="([^"]*)"', src).group(1)
    kn = re.search(r'KN="([^"]*)"', src).group(1)

    # Recover kana_str from KN
    kana_str = ''.join(chr(ord(c) + 12318) for c in kn)

    kt = build_kt(kd)
    decoded = decode_da(da, kt, kana_str)

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
