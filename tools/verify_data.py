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


def decode_b93(s):
    bits = []
    for i in range(0, len(s), 2):
        d0 = ord(s[i]) - 0x20
        if d0 > 2: d0 -= 1
        if d0 > 59: d0 -= 1
        d1 = ord(s[i + 1]) - 0x20
        if d1 > 2: d1 -= 1
        if d1 > 59: d1 -= 1
        v = d0 * 93 + d1
        for j in range(12, -1, -1):
            bits.append((v >> j) & 1)
    return bits


def build_kt(kd_str):
    bits = decode_b93(kd_str)
    kt = [chr(0x4E00)]
    cp = 0x4E00
    p = 0

    def read(n):
        nonlocal p
        v = 0
        for _ in range(n):
            v = v * 2 + bits[p]
            p += 1
        return v

    for _ in range(2047):
        if read(1):
            if read(1):
                if read(1):
                    cp += read(9) + 85
                else:
                    cp += read(6) + 21
            else:
                cp += read(4) + 5
        else:
            cp += read(2) + 1
        kt.append(chr(cp))
    return kt


def decode_da(da_str, kt, kana_str):
    bits = decode_b93(da_str)
    p = 0
    H = 12318
    K4 = "m(&1"
    K6 = ";b9c*-knl3`LFqJ."

    def R(n):
        nonlocal p
        v = 0
        for _ in range(n):
            v = v * 2 + bits[p]
            p += 1
        return v

    def RK():
        if R(1):
            if R(1):
                return R(7)
            return ord(K6[R(4)])
        return ord(K4[R(2)])

    FC = chr
    cells = {}

    for ri in range(44):
        row_kana = kana_str[ri]
        for ci in range(46):
            col_kana = '' if ci == 0 else kana_str[ci - 1]
            pf = row_kana + col_kana
            cell_key = row_kana + '+' + col_kana

            if not R(1):
                continue

            entries = []
            while True:
                kl = []
                while True:
                    if R(1):
                        if R(1):
                            if not kl:
                                break
                            break
                        kl.append(FC(R(15) + 19968))
                    else:
                        kl.append(kt[R(11)])
                if not kl:
                    break

                on = R(1)
                tr_idx = R(2) + 2 if R(1) else R(1)
                tr = '345216'[tr_idx]
                Dv = (R(2) + 2 if R(1) else 1) if R(1) else 0
                d2 = (Dv + 1) % 3 - 1
                d1 = ((Dv - d2) // 3) % 2
                ko = on * 96

                pr = ''
                for ci2, c in enumerate(pf):
                    pr += FC(ord(c) + ko + (d2 if ci2 else d1))

                rd = ''
                while R(1):
                    rd += FC(RK() + H + ko)

                sf = ''
                while not on and R(1):
                    sf += FC(RK() + H)

                t = pr + rd + tr + sf
                for k in kl:
                    entries.append(k + t)

            if entries:
                cells[cell_key] = entries

    return cells


def decoded_to_original(entry, cell_kana):
    """Convert decoded entry back to original format like '4中あた|る'."""
    kanji = entry[0]
    rest = entry[1:]
    # Find the tier digit
    d = None
    for i, c in enumerate(rest):
        if c.isdigit():
            d = i
            break
    if d is None:
        return None
    reading = rest[:d]
    tier = rest[d]
    okurigana = rest[d + 1:]

    # The reading includes the cell kana prefix
    # Separate: prefix = cell_kana (possibly shifted for on-yomi), extra = rest
    if okurigana:
        result = tier + kanji + reading + '|' + okurigana
    else:
        result = tier + kanji + reading
    return result


def main():
    with open(SNAPSHOT_PATH, 'r') as f:
        snapshot = json.load(f)

    with open(INDEX_PATH, 'r') as f:
        src = f.read()

    kd = re.search(r'KD="([^"]*)"', src).group(1)
    da = re.search(r'DA="([^"]*)"', src).group(1)
    kn = re.search(r'KN="([^"]*)"', src).group(1)

    # Recover kana_str from KN by reversing the ASCII mapping
    # KN[i] + 12318 = kana codepoint
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
