#!/usr/bin/env python3
"""
Rebuild snapshot.json with optimal reading choices and correct tiers.

This script performs a full rebuild of the snapshot from the current
snapshot.json, applying the following fixes in order:

1. Fix reading choices: for each kanji in each cell, pick the
   KANJIDIC2 reading with the highest JMdict frequency score
   (among readings that map to the same cell).

2. Reassign tiers based on the (possibly new) reading's score.

3. Re-sort entries within each cell by frequency score descending.

Usage: PYTHONPATH=tools python3 tools/rebuild_snapshot.py
"""

import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter

# Allow importing from tools/ when run with PYTHONPATH=tools
from resort_by_reading import (
    parse_kanjidic2, parse_jmdict, get_reading_freq, parse_entry,
    sort_entries, kata_to_hira, normalize_kanjidic_reading,
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

            # Sort: lowest priority number first, then highest score
            candidates.sort(key=lambda c: (c[0], -c[1]))
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
