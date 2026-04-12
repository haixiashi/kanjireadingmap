#!/usr/bin/env python3
"""
Rebuild snapshot.json with optimal reading choices and correct tiers.

This script performs a full rebuild of the snapshot from the current
snapshot.json, applying the following fixes in order:

0. Expand with missing JMdict readings (e.g. 温い/ぬるい not in KANJIDIC2).

1. Fix reading choices: for each kanji in each cell, pick the
   KANJIDIC2 reading with the highest JMdict frequency score
   (among readings that map to the same cell).

2. Reassign tiers based on the (possibly new) reading's score.

3. Re-sort entries within each cell by frequency score descending.

Usage: PYTHONPATH=tools python3 tools/rebuild_snapshot.py
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter

# Allow importing from tools/ when run with PYTHONPATH=tools
from resort_by_reading import (
    parse_kanjidic2, parse_jmdict, get_reading_freq, parse_entry,
    sort_entries, kata_to_hira, normalize_kanjidic_reading,
    is_kana, is_kanji,
)
from expand_entries import (
    score_to_tier, reassign_tier, reading_to_cell, base_kana,
    KANJIDIC2_PATH,
)

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_PATH = os.path.join(TOOLS_DIR, 'snapshot.json')


def parse_kanjidic2_readings(path):
    """Parse KANJIDIC2 and return kanji -> list of (raw_reading, r_type)."""
    tree = ET.parse(path)
    result = {}
    for char in tree.getroot().iter('character'):
        lit = char.find('literal').text
        rmg = char.find('reading_meaning')
        if rmg is None:
            continue
        readings = []
        for group in rmg.findall('rmgroup'):
            for r in group.findall('reading'):
                if r.get('r_type') in ('ja_on', 'ja_kun'):
                    readings.append((r.text, r.get('r_type')))
        if readings:
            result[lit] = readings
    return result


def make_entry(tier, kanji, raw_reading, r_type):
    """Create entry string from a KANJIDIC2 reading.

    Returns (entry_string, full_reading_hira) or (None, None).
    """
    clean = raw_reading.strip('-')
    if not clean:
        return None, None
    if r_type == 'ja_on':
        full_hira = kata_to_hira(clean)
        return f"{tier}{kanji}{clean}", full_hira
    elif r_type == 'ja_kun':
        if '.' in clean:
            stem, okurigana = clean.split('.', 1)
            full_hira = kata_to_hira(stem + okurigana)
            return f"{tier}{kanji}{stem}|{okurigana}", full_hira
        else:
            full_hira = kata_to_hira(clean)
            return f"{tier}{kanji}{clean}", full_hira
    return None, None


def main():
    print("Loading data sources...")
    kanji_readings = parse_kanjidic2(KANJIDIC2_PATH)
    freq_map = parse_jmdict(os.path.join(TOOLS_DIR, 'JMdict_e.xml'), kanji_readings)
    kanjidic_readings = parse_kanjidic2_readings(KANJIDIC2_PATH)

    with open(SNAPSHOT_PATH, 'r') as f:
        snap = json.load(f)

    # --- Phase 0: Expand with missing JMdict readings ---
    # Adds entries for readings that KANJIDIC2 doesn't list but JMdict has
    # (e.g. 温い/ぬるい). Only adds if the kanji already exists in the snapshot.
    existing_kanji = set()
    existing_pairs = set()  # (kanji, full_reading_hira)
    existing_in_cell = {}   # cell -> set of kanji chars
    for cell, entries in snap.items():
        existing_in_cell[cell] = set()
        for e in entries:
            kanji = e[1]
            existing_kanji.add(kanji)
            existing_in_cell[cell].add(kanji)
            _, _, reading, okurigana, full_reading = parse_entry(e)
            existing_pairs.add((kanji, kata_to_hira(full_reading)))

    FREQ_THRESHOLD = 5
    jmdict_path = os.path.join(TOOLS_DIR, 'JMdict_e.xml')
    with open(jmdict_path, 'r', encoding='utf-8') as f:
        jmdict_content = f.read()

    re_restr_pat = re.compile(r'<re_restr>(.*?)</re_restr>')
    added = 0
    for m in re.finditer(r'<entry>(.*?)</entry>', jmdict_content, re.DOTALL):
        entry_text = m.group(1)
        kebs = re.findall(r'<keb>(.*?)</keb>', entry_text)
        if not kebs:
            continue

        r_eles = []
        for rm in re.finditer(r'<r_ele>(.*?)</r_ele>', entry_text, re.DOTALL):
            reb_m = re.search(r'<reb>(.*?)</reb>', rm.group(1))
            restrs = re_restr_pat.findall(rm.group(1))
            if reb_m:
                r_eles.append((reb_m.group(1), restrs))
        if not r_eles:
            continue

        for keb in kebs:
            chars = list(keb)
            non_kana = [c for c in chars if not is_kana(c)]
            if len(non_kana) != 1 or not is_kanji(non_kana[0]):
                continue
            kanji_char = non_kana[0]
            if kanji_char not in existing_kanji:
                continue

            for reb, restrs in r_eles:
                if restrs and keb not in restrs:
                    continue
                reading_hira = kata_to_hira(reb)

                # Extract kana suffix and prefix from word form
                kana_suffix = ''
                i = len(chars) - 1
                while i >= 0 and is_kana(chars[i]):
                    kana_suffix = kata_to_hira(chars[i]) + kana_suffix
                    i -= 1
                kana_prefix = ''
                j = 0
                while j < len(chars) and is_kana(chars[j]):
                    kana_prefix += kata_to_hira(chars[j])
                    j += 1

                furigana = reading_hira
                if kana_prefix:
                    if not furigana.startswith(kana_prefix):
                        continue
                    furigana = furigana[len(kana_prefix):]
                if kana_suffix:
                    if not furigana.endswith(kana_suffix):
                        continue
                    okurigana = kana_suffix
                    furigana = furigana[:-len(kana_suffix)]
                else:
                    okurigana = ''

                if not furigana or len(furigana) > 4:
                    continue
                if len(okurigana) > 3:
                    continue
                if 'ー' in furigana or 'ー' in okurigana:
                    continue  # Skip colloquial/emphatic elongated forms

                full_reading_hira = furigana + okurigana
                if (kanji_char, full_reading_hira) in existing_pairs:
                    continue

                cell = reading_to_cell(furigana)
                if cell is None:
                    continue
                row, col = cell
                cell_key = row + '+' + col
                if cell_key in existing_in_cell and kanji_char in existing_in_cell[cell_key]:
                    continue

                score = get_reading_freq(kanji_char, full_reading_hira, freq_map)
                if score < FREQ_THRESHOLD:
                    continue

                full_reading_text = furigana + ('|' + okurigana if okurigana else '')
                entry_str = f"1{kanji_char}{full_reading_text}"
                if cell_key not in snap:
                    snap[cell_key] = []
                    existing_in_cell[cell_key] = set()
                snap[cell_key].append(entry_str)
                existing_in_cell[cell_key].add(kanji_char)
                existing_pairs.add((kanji_char, full_reading_hira))
                added += 1

    print(f"Phase 0: Added {added} missing JMdict readings")

    # --- Phase 1: Fix reading choices ---
    # For each entry, pick the best KANJIDIC2 reading in the same cell.
    # Priority: 1) non-suffix okurigana form (e.g. たか.い)
    #           2) non-suffix bare form (e.g. たか)
    #           3) suffix form (e.g. -だか)
    # Within each priority level, pick the highest scoring reading.
    upgraded = 0
    for cell, entries in snap.items():
        row, col = cell.split('+', 1)
        new_entries = []
        for e in entries:
            tier_char = e[0]
            kanji = e[1]

            if kanji not in kanjidic_readings:
                new_entries.append(e)
                continue

            # Collect all readings for this kanji in this cell
            candidates = []  # (priority, score, raw_reading, r_type)
            for raw_reading, r_type in kanjidic_readings[kanji]:
                clean = raw_reading.strip('-')
                if not clean:
                    continue
                if '.' in clean:
                    stem = clean.split('.')[0]
                else:
                    stem = clean
                stem_hira = kata_to_hira(stem)
                cell_check = reading_to_cell(stem_hira)
                if cell_check is None:
                    continue
                check_row, check_col = cell_check
                if check_row != row or check_col != col:
                    continue

                full_hira = kata_to_hira(clean.replace('.', ''))
                score = get_reading_freq(kanji, full_hira, freq_map)

                is_suffix = raw_reading.startswith('-')
                has_okuri = '.' in raw_reading

                if is_suffix:
                    priority = 2  # lowest
                elif has_okuri:
                    priority = 0  # highest
                else:
                    priority = 1  # middle

                candidates.append((priority, score, raw_reading, r_type))

            if not candidates:
                new_entries.append(e)
                continue

            # Sort: suffixes always last, then highest score, then prefer okurigana
            candidates.sort(key=lambda c: (1 if c[0]==2 else 0, -c[1], c[0]))
            best_pri, best_score, best_raw, best_rtype = candidates[0]

            candidate, _ = make_entry(tier_char, kanji, best_raw, best_rtype)
            if candidate and candidate != e:
                upgraded += 1
                new_entries.append(candidate)
            else:
                new_entries.append(e)
        snap[cell] = new_entries

    print(f"Phase 1: Upgraded {upgraded} entries to higher-scoring readings")

    # --- Phase 2: Reassign tiers ---
    tc = Counter()
    for cell, entries in snap.items():
        new_entries = []
        for e in entries:
            _, kanji, reading, okurigana, full_reading = parse_entry(e)
            score = get_reading_freq(kanji, full_reading, freq_map)
            tier = score_to_tier(score)
            new_entries.append(reassign_tier(e, tier))
            tc[tier] += 1
        snap[cell] = new_entries

    total = sum(tc.values())
    print(f"Phase 2: Tier distribution ({total} entries):")
    for t in sorted(tc, reverse=True):
        print(f"  Tier {t}: {tc[t]:5d} ({tc[t]*100/total:.1f}%)")

    # --- Phase 3: Re-sort ---
    reordered = 0
    for cell, entries in snap.items():
        if len(entries) <= 1:
            continue
        new_entries = sort_entries(entries, freq_map)
        if new_entries != entries:
            reordered += 1
            snap[cell] = new_entries

    print(f"Phase 3: Re-sorted {reordered} cells")

    # --- Write ---
    with open(SNAPSHOT_PATH, 'w') as f:
        json.dump(snap, f, ensure_ascii=False, indent=1, sort_keys=False)
    print(f"Updated {SNAPSHOT_PATH}")

    # Show examples
    print("\nExamples:")
    for t in ['は+な', 'あ+い', 'た+ま', 'と+']:
        if t in snap:
            print(f"  {t}: {snap[t][:5]}")


if __name__ == '__main__':
    main()
