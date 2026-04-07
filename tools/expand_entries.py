#!/usr/bin/env python3
"""
Expand kanji reading map with missing entries from KANJIDIC2 + JMdict,
and assign 6-tier frequency levels based on JMdict reading frequency.

Tiers (by reading frequency score):
  6: score >= 2000  (core readings - dark green)
  5: score >= 500   (very common - green)
  4: score >= 100   (common - blue)
  3: score >= 30    (moderate - purple)
  2: score >= 5     (uncommon - orange)
  1: score < 5      (rare/Hyogai - red)
"""

import xml.etree.ElementTree as ET
import json
import re
import os
from collections import defaultdict
from resort_by_reading import (
    parse_kanjidic2, parse_jmdict, sort_entries, get_reading_freq,
    extract_kanji_data, format_kanji_data, kata_to_hira, is_katakana,
    parse_entry, KANJIDIC2_PATH, JMDICT_PATH, INDEX_PATH
)

KANA_ROW = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわ"
KANA_COL = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわん"

DAKUTEN_MAP = {}
for group in ['がかぎきぐくげけごこ', 'ざさじしずすぜせぞそ',
              'だたぢちづつでてどと', 'ばはびひぶふべへぼほ',
              'ぱはぴひぷふぺへぽほ']:
    for i in range(0, len(group), 2):
        DAKUTEN_MAP[group[i]] = group[i + 1]

SMALL_MAP = {'ぁ': 'あ', 'ぃ': 'い', 'ぅ': 'う', 'ぇ': 'え', 'ぉ': 'お',
             'ゃ': 'や', 'ゅ': 'ゆ', 'ょ': 'よ', 'っ': 'つ'}

# Tier thresholds: score >= threshold → tier
TIER_THRESHOLDS = [
    (2000, 6),
    (500,  5),
    (100,  4),
    (30,   3),
    (5,    2),
    (0,    1),
]


def score_to_tier(score):
    for threshold, tier in TIER_THRESHOLDS:
        if score >= threshold:
            return tier
    return 1


def base_kana(c):
    return SMALL_MAP.get(c, DAKUTEN_MAP.get(c, c))


def reading_to_cell(reading_hira):
    if not reading_hira:
        return None
    row = base_kana(reading_hira[0])
    if row not in KANA_ROW:
        return None
    if len(reading_hira) >= 2:
        col = base_kana(reading_hira[1])
        if col not in KANA_COL:
            return None
    else:
        col = ''
    return (row, col)


def parse_kanjidic2_full(path):
    tree = ET.parse(path)
    root = tree.getroot()
    kanji_info = {}
    for char in root.iter('character'):
        literal = char.find('literal').text
        misc = char.find('misc')
        grade_el = misc.find('grade')
        grade = int(grade_el.text) if grade_el is not None else None
        freq_el = misc.find('freq')
        freq = int(freq_el.text) if freq_el is not None else None
        readings = []
        rmg = char.find('reading_meaning')
        if rmg is not None:
            for group in rmg.findall('rmgroup'):
                for reading in group.findall('reading'):
                    r_type = reading.get('r_type')
                    if r_type in ('ja_on', 'ja_kun'):
                        readings.append((reading.text, r_type))
        kanji_info[literal] = {
            'grade': grade, 'freq': freq, 'readings': readings,
        }
    return kanji_info


def make_entry_str(tier, kanji, raw_reading, r_type):
    """Create entry string from KANJIDIC2 reading."""
    clean = raw_reading.strip('-')
    if not clean:
        return None, None
    if r_type == 'ja_on':
        furigana_hira = kata_to_hira(clean)
        return f"{tier}{kanji}{clean}", furigana_hira
    elif r_type == 'ja_kun':
        if '.' in clean:
            stem, okurigana = clean.split('.', 1)
            furigana_hira = kata_to_hira(stem)
            return f"{tier}{kanji}{stem}|{okurigana}", furigana_hira
        else:
            furigana_hira = kata_to_hira(clean)
            return f"{tier}{kanji}{clean}", furigana_hira
    return None, None


def reassign_tier(entry_str, new_tier):
    """Replace the tier digit in an entry string."""
    return str(new_tier) + entry_str[1:]


def main():
    print("Loading data sources...")
    kanji_readings = parse_kanjidic2(KANJIDIC2_PATH)
    freq_map = parse_jmdict(JMDICT_PATH, kanji_readings)
    kanji_info = parse_kanjidic2_full(KANJIDIC2_PATH)

    with open(INDEX_PATH, 'r', encoding='utf-8') as f:
        html = f.read()
    data, start, end = extract_kanji_data(html)

    # Build index
    existing_in_cell = defaultdict(set)
    existing_pairs = set()
    existing_kanji = set()
    for row in data['data']:
        for col in data['data'][row]:
            for entry in data['data'][row][col]:
                kanji = entry[1]
                existing_in_cell[(row, col)].add(kanji)
                existing_kanji.add(kanji)
                reading_text = entry[2:].replace('|', '')
                existing_pairs.add((kanji, kata_to_hira(reading_text)))

    # --- Phase 1: Expand with new entries ---
    FREQ_THRESHOLD = 5
    new_count = 0
    new_kanji = set()

    for kanji, info in kanji_info.items():
        if kanji not in existing_kanji and info['grade'] is None and info['freq'] is None:
            continue

        for raw_reading, r_type in info['readings']:
            if raw_reading.startswith('-'):
                continue

            entry_str, furigana_hira = make_entry_str(1, kanji, raw_reading, r_type)
            if entry_str is None:
                continue

            full_reading = entry_str[2:].replace('|', '')
            if (kanji, kata_to_hira(full_reading)) in existing_pairs:
                continue

            cell = reading_to_cell(furigana_hira)
            if cell is None or cell[0] not in data['data']:
                continue

            row, col = cell
            if kanji in existing_in_cell[(row, col)]:
                continue

            score = get_reading_freq(kanji, kata_to_hira(full_reading), freq_map)
            if score < FREQ_THRESHOLD:
                continue

            if col not in data['data'][row]:
                data['data'][row][col] = []

            data['data'][row][col].append(entry_str)
            existing_in_cell[(row, col)].add(kanji)
            existing_pairs.add((kanji, kata_to_hira(full_reading)))
            new_count += 1
            if kanji not in existing_kanji:
                new_kanji.add(kanji)

    print(f"\nPhase 1 - Expansion: {new_count} new entries, {len(new_kanji)} new kanji")

    # --- Phase 2: Reassign all tiers based on reading frequency ---
    tier_counts = defaultdict(int)
    for row in data['data']:
        for col in data['data'][row]:
            new_entries = []
            for entry in data['data'][row][col]:
                level, kanji, reading, okurigana, full_reading = parse_entry(entry)
                score = get_reading_freq(kanji, full_reading, freq_map)
                tier = score_to_tier(score)
                new_entry = reassign_tier(entry, tier)
                new_entries.append(new_entry)
                tier_counts[tier] += 1
            data['data'][row][col] = new_entries

    print(f"\nPhase 2 - Tier assignment:")
    total = sum(tier_counts.values())
    for tier in sorted(tier_counts, reverse=True):
        pct = tier_counts[tier] * 100 / total
        print(f"  Tier {tier}: {tier_counts[tier]:5d} ({pct:4.1f}%)")

    # --- Phase 3: Re-sort all cells ---
    sort_changes = 0
    for kana, readings in data['data'].items():
        for rkey, entries in readings.items():
            if len(entries) <= 1:
                continue
            new_entries = sort_entries(entries, freq_map)
            if new_entries != entries:
                sort_changes += 1
                readings[rkey] = new_entries
    print(f"\nPhase 3 - Re-sorted {sort_changes} cells")

    # Show examples
    print("\n=== Examples ===")
    test_cells = [
        ('や', 'み', 'やみ'),
        ('ま', 'と', 'まど'),
        ('え', 'ん', 'えん'),
        ('あ', 'ん', 'あん'),
        ('せ', 'い', 'セイ'),
        ('こ', 'う', 'コウ'),
    ]
    for row, col, label in test_cells:
        if row in data['data'] and col in data['data'][row]:
            entries = data['data'][row][col]
            print(f"  {label}: {entries[:10]}")

    total = sum(len(e) for r in data['data'].values() for e in r.values())
    print(f"\nTotal entries: {total}")

    # Write
    new_json = format_kanji_data(data)
    new_html = html[:start] + new_json + html[end:]
    with open(INDEX_PATH, 'w', encoding='utf-8') as f:
        f.write(new_html)
    print(f"Updated {INDEX_PATH}")


if __name__ == '__main__':
    main()
