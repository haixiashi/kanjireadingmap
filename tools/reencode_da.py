#!/usr/bin/env python3
"""Re-encode DA string from snapshot.json using current encoding format."""

import json
import os
import re
import sys


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


def build_kt(kd_str):
    """Build KT (kanji table) from KD string, matching JS decoder."""
    bits = decode_b93(kd_str)
    kt = ['\u4e00']  # First char
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


def parse_entry(entry_str):
    """Parse entry like '4中あた|る' into components.

    Format: <tier><kanji><reading>[|<okurigana>]
    The reading may be hiragana (kun) or katakana (on).
    """
    tier = int(entry_str[0])
    rest = entry_str[1:]

    # The kanji is all chars until we hit hiragana/katakana
    kanji = ''
    i = 0
    while i < len(rest):
        c = ord(rest[i])
        # Hiragana: 3040-309F, Katakana: 30A0-30FF
        if 0x3040 <= c <= 0x30FF:
            break
        kanji += rest[i]
        i += 1

    reading_and_okuri = rest[i:]
    # Split on |
    if '|' in reading_and_okuri:
        parts = reading_and_okuri.split('|', 1)
        furigana = parts[0]
        okurigana = parts[1]
    else:
        furigana = reading_and_okuri
        okurigana = ''

    # Determine if on-yomi (katakana) or kun-yomi (hiragana)
    is_on = any(0x30A0 <= ord(c) <= 0x30FF for c in furigana) if furigana else False

    return kanji, tier, furigana, okurigana, is_on


def get_ground_truth():
    """Load data from snapshot.json (authoritative reference)."""
    snapshot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'snapshot.json')
    with open(snapshot_path, 'r') as f:
        snap = json.load(f)

    # Convert snapshot format (cell_key -> entries) to the nested dict format
    # that the encoder expects: data['data'][row_kana][col_kana] = entries
    # Also reconstruct kana_str from the cell keys
    kana_set = set()
    data_dict = {}
    for cell_key, entries in snap.items():
        row, col = cell_key.split('+', 1)
        kana_set.add(row)
        if col:
            kana_set.add(col)
        if row not in data_dict:
            data_dict[row] = {}
        data_dict[row][col] = entries

    kana_str = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわん"
    return {'kana': kana_str, 'data': data_dict}


def get_current_source():
    """Read current index.html."""
    with open('index.html', 'r') as f:
        return f.read()


def main():
    # Get ground truth
    data = get_ground_truth()
    kana_str = data['kana']

    # Get current source to extract KD
    src = get_current_source()
    kd_match = re.search(r'KD="([^"]*)"', src)
    kd_str = kd_match.group(1)

    # Build KT
    kt = build_kt(kd_str)
    kt_set = {c: i for i, c in enumerate(kt)}

    # Build the grid: kana_str has 45 chars for rows
    # Columns: header row has '' + 44 kana chars
    # From current JS: hd=['', ...KN], rw=[...KN].slice(0,-1)
    # KN = "$&(*,-/13579;=?ACFHJLMNOPQTWZ]`abcdfhjklmnoqu"
    # hd = [''] + list(KN) = 46 entries (columns)
    # rw = KN[:-1] = 44 entries (rows)
    # But wait, the original data uses actual kana for row/col keys

    # The current code maps kana to ASCII using KN string
    kn_match = re.search(r'KN="([^"]*)"', src)
    kn_str = kn_match.group(1)

    # hd (column headers): ['', ...KN] = 46 entries
    # rw (row labels): KN[:-1] = first 44 chars of KN = 44 entries

    # In the original data, rows are kana chars, cols are kana chars (or '' for first col)
    # The kana string maps to KN: kana_str[i] <-> KN[i]

    # Grid is 44 rows x 46 columns = 2024 cells
    # The DC function is called once per cell, reading from the bitstream

    # Build mapping from kana to grid position
    # Row kana: kana_str[0:44] (first 44 of 45 kana)
    # Col kana: '' for first col, then kana_str[0:44] for remaining 44 + kana_str[44] for last
    # Actually, hd has 46 entries, rw has 44 entries
    # KN has 45 chars. hd = [''] + all 45 KN chars = 46 entries
    # rw = KN[0:44] = first 44 chars = 44 entries

    # kana_str also has 45 chars. The mapping is kana_str[i] <-> KN[i]
    # But the original data uses kana for keys, not KN chars

    # In the original data format:
    # data['data'][row_kana][col_kana] = entries
    # where row_kana is from kana_str, col_kana is from kana_str or ''

    # In the grid:
    # row i (0-43): kana_str[i]
    # col 0: '' (first-col)
    # col j (1-45): kana_str[j-1]

    # Now encode the bitstream
    # For each cell (row 0-43, col 0-45):
    #   Look up data['data'][row_kana].get(col_kana, [])
    #   Encode entries

    # The encoding format from DC():
    # if R(1)==0: empty cell, done
    # if R(1)==1: cell has entries
    #   Loop:
    #     Read kanji list:
    #       Loop:
    #         if R(1)==0: kanji from KT, read R(11) -> KT index
    #         if R(1)==1:
    #           if R(1)==0: kanji by codepoint, read R(15) + 0x4E00
    #           if R(1)==1: end of kanji list
    #             if first kanji (kl is empty): end of cell (break A)
    #             else: end of this kanji group
    #     Read on: R(1) -> 0=kun, 1=on
    #     Read tier: if R(1) then R(2)+2 (values 2-5) else if R(1) then 1 else 0
    #       Wait, the code says: '354261'[R(1)?R(2)+2:R(1)]
    #       So tier encoding: R(1)?R(2)+2:R(1) gives index into '354261'
    #       if R(1)==1: idx = R(2)+2 -> 2,3,4,5 -> '4','2','6','1'
    #       if R(1)==0: idx = R(1) -> 0 or 1 -> '3','5'
    #       So possible tiers: 3,5,4,2,6,1
    #       Let me re-read: '354261'[idx]
    #       idx=0 -> '3', idx=1 -> '5', idx=2 -> '4', idx=3 -> '2', idx=4 -> '6', idx=5 -> '1'
    #     Read Dv (variant): if R(1) then (if R(1) then R(2)+2 else 1) else 0
    #     d2=(Dv+1)%3-1, d1=(Dv-d2)/3%2
    #       Dv=0: d2=1%3-1=0, d1=0
    #       Dv=1: d2=2%3-1=1, d1=0
    #       Dv=2: d2=0-1=-1, d1=(2-(-1))/3%2 = 3/3%2 = 1%2 = 1
    #       Dv=3: d2=1%3-1=0, d1=1 ... wait let me recalc
    #       Dv=2: d2=(2+1)%3-1=0-1=-1, d1=(2-(-1))/3%2=1%2=1
    #       Dv=3: d2=(3+1)%3-1=1-1=0, d1=(3-0)/3%2=1%2=1
    #       Dv=4: d2=(4+1)%3-1=2-1=1, d1=(4-1)/3%2=1%2=1
    #       Dv=5: d2=(5+1)%3-1=0-1=-1, d1=(5-(-1))/3%2=0%2=0
    #     ko = on * 96
    #     Then furigana prefix: pr = each char of pf (the cell's kana prefix) + H + ko + (ci?d2:d1)
    #       where pf = rl+cl (the row label + column label in ASCII/KN format)
    #       H = 12318 = 0x301E
    #       For the first char (ci=0): offset = H + ko + d1
    #       For subsequent chars (ci>0): offset = H + ko + d2
    #     Then furigana reading: while R(1), read RK() char + H + ko
    #     Then okurigana: while !on && R(1), read RK() char + H
    #       (okurigana only for kun readings)
    #     The tier char: '354261'[idx], stored as ASCII digit

    # Wait, I need to understand what the decoded entry looks like vs the original format.
    # Original: "4中あた|る"
    #   kanji: 中, tier: 4, furigana: あた, okurigana: る, is_on: False (kun)
    #
    # In the decoded format, the entry becomes: kanji + furigana_prefix + reading + tier_char + okurigana
    # where furigana_prefix encodes the cell's kana (rl+cl), and reading is extra chars
    #
    # Actually looking at the DC function more carefully:
    #   var t = pr + rd + tr + sf
    #   pr = furigana prefix (from cell kana pf=rl+cl)
    #   rd = additional reading chars
    #   tr = tier digit char
    #   sf = okurigana suffix
    #   Then for each kanji k: en.push(kl[k] + t)
    #
    # So the entry string is: kanji + cell_kana_encoded + extra_reading + tier_digit + okurigana
    #
    # In the K() function that renders:
    #   d = s.search(/\d/) -> finds the tier digit
    #   kj = s[0] -> kanji
    #   rd = s.substring(1, d) -> the full reading (furigana prefix + extra reading)
    #   tr = s[d] - 48 -> tier number
    #   sf = s.substring(d+1) -> okurigana
    #   on = rd && charcode > 12447 && < 12544 -> checks if katakana
    #
    # So the full reading = cell_kana_encoded + extra_reading
    # For kun: reading is hiragana (0x3040-0x309F range, + H = 12318 = 0x301E -> maps to 0x305E-0x30BD? No)
    # Wait, H = 12318. The kana chars are stored as: CA(pf[ci]) + H + ko + offset
    # pf is in KN ASCII format. So CA(pf[ci]) is the ASCII code of the KN character.
    # For example, KN[0] = '$', ASCII 36. 36 + 12318 = 12354 = 0x3042 = 'あ'
    # So H + ASCII_of_KN_char maps KN back to kana!
    # ko = on * 96 = 96 for on-yomi. 0x3042 + 96 = 0x30A2 = 'ア' (katakana)
    # The d1/d2 offsets handle variant kana mappings.

    # So the encoding of furigana is:
    # - Cell prefix kana: stored implicitly (from rl+cl position)
    # - Extra reading chars: encoded as RK() values, where each value + H + ko gives the kana codepoint
    # - Okurigana: encoded as RK() values, where each value + H gives the hiragana codepoint

    # The RK() function reads a kana index:
    # K4 = "m(&1"
    # K6 = ";b9c*-knl3`LFqJ."
    # if R(1)==1:
    #   if R(1)==1: return R(7)  -> 0-127 (7-bit raw)
    #   return CA(K6[R(4)])  -> lookup in K6 (16 entries, 4-bit index)
    # return CA(K4[R(2)])  -> lookup in K4 (4 entries, 2-bit index)

    # So to encode a kana, I need to find its "kana code" which is:
    # For furigana: code = ord(kana_char) - H - ko
    # For okurigana: code = ord(kana_char) - H

    # Then encode using VLC: if code is in K4 (4 most common), use 0 + 2-bit index
    # If code is in K6 (next 16), use 10 + 4-bit index
    # Otherwise, use 11 + 7-bit raw value

    K4 = "m(&1"
    K6 = ";b9c*-knl3`LFqJ."
    k4_codes = {ord(c): i for i, c in enumerate(K4)}
    k6_codes = {ord(c): i for i, c in enumerate(K6)}

    H = 12318

    # Now let's encode!
    bits = []

    def write(n, val):
        """Write n-bit value to bitstream."""
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)

    def write_kanji(kanji_char):
        """Encode a kanji character."""
        if kanji_char in kt_set:
            idx = kt_set[kanji_char]
            write(1, 0)  # KT lookup
            write(11, idx)
            return True
        else:
            cp = ord(kanji_char) - 0x4E00
            if cp < 0 or cp >= 32768:
                print(f"WARNING: skipping kanji {kanji_char} (U+{ord(kanji_char):04X}) - out of 15-bit range", file=sys.stderr)
                return False
            write(1, 1)  # raw codepoint
            write(1, 0)
            write(15, cp)
            return True

    def write_rk(code):
        """Encode a kana code using VLC."""
        if code in k4_codes:
            write(1, 0)
            write(2, k4_codes[code])
        elif code in k6_codes:
            write(1, 1)
            write(1, 0)
            write(4, k6_codes[code])
        else:
            write(1, 1)
            write(1, 1)
            write(7, code)

    # Tier encoding: '354261'[idx]
    # '345216'[idx]: idx 0->3, 1->4, 2->5, 3->2, 4->1, 5->6
    tier_to_idx = {3: 0, 4: 1, 5: 2, 2: 3, 1: 4, 6: 5}
    # idx encoding: 0,1 use short form; 2,3,4,5 use long form
    # if R(1)==0: idx = R(1) -> 0 or 1
    # if R(1)==1: idx = R(2)+2 -> 2,3,4,5

    def write_tier_idx(idx):
        if idx < 2:
            write(1, 0)
            write(1, idx)
        else:
            write(1, 1)
            write(2, idx - 2)

    # Variant encoding:
    # Dv=0: write(1,0)
    # Dv=1: write(1,1), write(1,0)
    # Dv=2: write(1,1), write(1,1), write(2,0)
    # Dv=3: write(1,1), write(1,1), write(2,1)
    # Dv=4: write(1,1), write(1,1), write(2,2)
    # Dv=5: write(1,1), write(1,1), write(2,3)

    def write_variant(dv):
        if dv == 0:
            write(1, 0)
        elif dv == 1:
            write(1, 1)
            write(1, 0)
        else:
            write(1, 1)
            write(1, 1)
            write(2, dv - 2)

    # Now figure out the Dv for each entry.
    # Dv encodes d1 and d2 offsets:
    # d2 = (Dv+1)%3 - 1  -> values: -1, 0, 1
    # d1 = ((Dv - d2) / 3) % 2 -> values: 0, 1
    # These offsets adjust the kana codepoints in the furigana prefix.
    # For standard entries, Dv=0 (d1=0, d2=0).
    #
    # To determine Dv from the original data:
    # The furigana prefix comes from the cell kana (rl+cl).
    # If the furigana of the entry matches the cell kana exactly, Dv=0.
    # If there are small offsets (like voicing marks), Dv encodes them.

    # Let me think about this differently. The original entry has explicit furigana.
    # The cell position determines the "expected" kana prefix.
    # The difference between actual and expected is encoded as d1 (first char) and d2 (rest).
    # Then extra reading chars beyond the prefix length are encoded separately.

    # For a cell at row=kana_str[ri], col=kana_str[ci-1] (or '' for ci=0):
    # The prefix is: row_kana + col_kana (if ci>0)
    # For the first column (ci=0), prefix is just row_kana (1 char)
    # For other columns, prefix is 2 chars

    # Example: row='あ', col='た' -> prefix = 'あた'
    # Entry "4中あた|る": furigana = 'あた', matches prefix exactly -> Dv=0
    # Extra reading = '' (no extra chars), okurigana = 'る'

    # For on-yomi with ko=96:
    # prefix chars become katakana (shifted by 96)

    # d1 = offset for first prefix char
    # d2 = offset for remaining prefix chars
    # So actual_first_char = expected_first_char + d1
    #    actual_other_chars = expected_other_char + d2 (each)

    # kana_str and KN mapping
    kana_to_kn = {}
    for i, k in enumerate(kana_str):
        kana_to_kn[k] = kn_str[i]

    # Grid iteration order: row 0-43, col 0-45
    # row i -> kana_str[i] (rw = KN[0:44], mapped from kana_str[0:44])
    # col 0 -> '' (first col, hd[0])
    # col j (1-45) -> kana_str[j-1] (hd[j] = KN[j-1])

    total_entries = 0
    total_cells = 0
    empty_cells = 0

    for ri in range(44):
        row_kana = kana_str[ri]
        for ci in range(46):
            if ci == 0:
                col_kana = ''
            else:
                col_kana = kana_str[ci - 1]

            cell_kana = row_kana + col_kana  # The full kana prefix for this cell
            cell_key_row = row_kana
            cell_key_col = col_kana

            entries = data['data'].get(cell_key_row, {}).get(cell_key_col, [])

            if not entries:
                write(1, 0)  # empty cell
                empty_cells += 1
                continue

            write(1, 1)  # non-empty cell
            total_cells += 1

            # Group entries by shared reading attributes
            # Each entry in the original: "4中あた|る"
            # Multiple kanji can share the same reading -> group them

            # Parse all entries
            parsed = []
            for e in entries:
                kanji, tier, furigana, okurigana, is_on = parse_entry(e)
                parsed.append((kanji, tier, furigana, okurigana, is_on))

            # Group entries that share (tier, furigana, okurigana, is_on)
            # The encoding groups kanji with identical readings
            groups = []
            for kanji, tier, furigana, okurigana, is_on in parsed:
                key = (tier, furigana, okurigana, is_on)
                # Check if last group has same key
                if groups and groups[-1][0] == key:
                    groups[-1][1].append(kanji)
                else:
                    groups.append((key, [kanji]))

            for (tier, furigana, okurigana, is_on), kanji_list in groups:
                # Filter out unencodable kanji
                encodable = []
                for kc in kanji_list:
                    if kc in kt_set:
                        encodable.append(kc)
                    else:
                        cp = ord(kc) - 0x4E00
                        if 0 <= cp < 32768:
                            encodable.append(kc)
                        else:
                            print(f"WARNING: skipping kanji {kc} (U+{ord(kc):04X})", file=sys.stderr)
                if not encodable:
                    continue
                # Write kanji list
                for kanji_char in encodable:
                    write_kanji(kanji_char)
                write(1, 1)  # end kanji list marker
                write(1, 1)

                # Write on/kun
                write(1, 1 if is_on else 0)

                # Write tier
                if tier not in tier_to_idx:
                    print(f"WARNING: unexpected tier {tier}, defaulting to 1", file=sys.stderr)
                    tier = 1
                write_tier_idx(tier_to_idx[tier])

                # Compute variant (Dv) and extra reading
                ko = 96 if is_on else 0
                prefix_kana = cell_kana  # Expected kana prefix
                # The furigana should start with prefix_kana (possibly shifted)
                # Compute expected furigana prefix chars
                expected_codes = []
                for c in prefix_kana:
                    expected_codes.append(ord(c) + ko)

                actual_codes = [ord(c) for c in furigana[:len(prefix_kana)]]

                # Compute d1, d2
                if len(prefix_kana) == 0:
                    d1 = 0
                    d2 = 0
                elif len(prefix_kana) == 1:
                    d1 = actual_codes[0] - expected_codes[0] if actual_codes else 0
                    d2 = 0
                else:
                    d1 = actual_codes[0] - expected_codes[0] if len(actual_codes) > 0 else 0
                    d2 = actual_codes[1] - expected_codes[1] if len(actual_codes) > 1 else 0

                # Recover Dv from d1, d2
                # d2 = (Dv+1)%3 - 1
                # d1 = ((Dv - d2) / 3) % 2
                # Possible (d1, d2) pairs and their Dv:
                dv_map = {}
                for dv in range(6):
                    dd2 = (dv + 1) % 3 - 1
                    dd1 = ((dv - dd2) // 3) % 2
                    dv_map[(dd1, dd2)] = dv

                if (d1, d2) not in dv_map:
                    print(f"WARNING: unexpected (d1={d1}, d2={d2}) for entry in cell {cell_kana}, furigana={furigana}", file=sys.stderr)
                    dv = 0
                else:
                    dv = dv_map[(d1, d2)]

                write_variant(dv)

                # Extra reading chars (beyond prefix)
                extra_reading = furigana[len(prefix_kana):]
                for c in extra_reading:
                    write(1, 1)  # more reading chars
                    code = ord(c) - H - ko
                    write_rk(code)
                write(1, 0)  # end of extra reading

                # Okurigana (only for kun readings)
                if not is_on:
                    for c in okurigana:
                        write(1, 1)  # more okurigana
                        code = ord(c) - H
                        write_rk(code)
                    write(1, 0)  # end of okurigana

                total_entries += len(kanji_list)

            # End of cell: write terminator (11 with empty kanji list)
            write(1, 1)  # first bit of kanji
            write(1, 1)  # second bit -> end marker with empty list = end of cell

    print(f"Encoded {total_entries} entries in {total_cells} non-empty cells ({empty_cells} empty)", file=sys.stderr)
    print(f"Total bits: {len(bits)}", file=sys.stderr)

    # Encode to base-85
    da_str = encode_b93(bits)
    print(f"DA string length: {len(da_str)}", file=sys.stderr)

    # Verify by decoding
    verify_bits = decode_b93(da_str)
    for i in range(len(bits)):
        assert verify_bits[i] == bits[i], f"Bit mismatch at position {i}"
    print("Base-85 round-trip verified", file=sys.stderr)

    sys.stdout.write(da_str)


if __name__ == '__main__':
    main()
