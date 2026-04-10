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
    is_kanji, is_kana, parse_entry, normalize_kanjidic_reading,
    KANJIDIC2_PATH, JMDICT_PATH, INDEX_PATH
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
# Scores use max-per-word scoring from JMdict priority tags.
# Thresholds concentrate most entries in the middle tiers (j3/j4).
TIER_THRESHOLDS = [
    (98,  6),   # ~8%  - core readings (e.g. 手て, 足あし, 秋あき)
    (93,  5),   # ~15% - very common (e.g. 青あお, 犬いぬ, 山やま)
    (56,  4),   # ~27% - common
    (15,  3),   # ~26% - moderate
    (0.5, 2),   # ~15% - attested in JMdict but low/no freq tags
    (0,   1),   # ~8%  - rare / not in JMdict / Hyogai
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
    jis_to_kanji = {}
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
        jis_variants = []
        for var in char.findall('.//variant'):
            if var.get('var_type') == 'jis208':
                jis_variants.append(var.text)
        for cp in char.findall('.//cp_value'):
            if cp.get('cp_type') == 'jis208':
                jis_to_kanji[cp.text] = literal
        kanji_info[literal] = {
            'grade': grade, 'freq': freq, 'readings': readings,
            'jis_variants': jis_variants,
        }
    # Build archaic -> common mapping
    archaic_to_common = {}
    # Method 1: JIS208 variant references
    for lit, info in kanji_info.items():
        for code in info['jis_variants']:
            target = jis_to_kanji.get(code)
            if target and target != lit:
                f_lit = info['freq']
                f_target = kanji_info[target]['freq']
                if f_lit is not None and f_target is None:
                    archaic_to_common[target] = lit
                elif f_target is not None and f_lit is None:
                    archaic_to_common[lit] = target
    # Method 2: Subset reading match (catches variants KANJIDIC2 doesn't link)
    # If a no-freq kanji's readings are a subset of a freq'd kanji's readings,
    # and there are at least 2 readings, it's likely a variant.
    all_kanji = list(kanji_info.keys())
    reading_sets = {}
    for lit, info in kanji_info.items():
        norms = frozenset(
            normalize_kanjidic_reading(r) for r, _ in info['readings']
        )
        if len(norms) >= 2:
            reading_sets[lit] = norms
    for lit, norms in reading_sets.items():
        if lit in archaic_to_common or kanji_info[lit]['freq'] is not None:
            continue
        for other, other_norms in reading_sets.items():
            if other == lit or kanji_info[other]['freq'] is None:
                continue
            if norms <= other_norms:  # subset
                archaic_to_common[lit] = other
                break
    return kanji_info, archaic_to_common


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
    kanji_info, archaic_to_common = parse_kanjidic2_full(KANJIDIC2_PATH)

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

    print(f"\nPhase 1a - KANJIDIC2 expansion: {new_count} new entries, {len(new_kanji)} new kanji")

    # --- Phase 1b: Expand from JMdict single-kanji words ---
    # Catches readings KANJIDIC2 doesn't list (e.g. 温い/ぬるい)
    jmdict_count = 0
    with open(JMDICT_PATH, 'r', encoding='utf-8') as f:
        jmdict_content = f.read()

    import re as re2
    re_restr_pat = re2.compile(r'<re_restr>(.*?)</re_restr>')

    for m in re2.finditer(r'<entry>(.*?)</entry>', jmdict_content, re2.DOTALL):
        entry_text = m.group(1)

        kebs = re2.findall(r'<keb>(.*?)</keb>', entry_text)
        if not kebs:
            continue

        # Parse r_ele with re_restr info
        r_eles = []
        for rm in re2.finditer(r'<r_ele>(.*?)</r_ele>', entry_text, re2.DOTALL):
            reb_m = re2.search(r'<reb>(.*?)</reb>', rm.group(1))
            restrs = re_restr_pat.findall(rm.group(1))
            if reb_m:
                r_eles.append((reb_m.group(1), restrs))
        if not r_eles:
            continue

        for keb in kebs:
            chars = list(keb)
            # Must be exactly one kanji + only kana (no digits, symbols, etc.)
            non_kana = [c for c in chars if not is_kana(c)]
            if len(non_kana) != 1 or not is_kanji(non_kana[0]):
                continue
            kanji_chars = non_kana

            kanji_char = kanji_chars[0]
            info = kanji_info.get(kanji_char)
            if not info:
                continue
            if kanji_char not in existing_kanji and info['grade'] is None and info['freq'] is None:
                continue

            for reb, restrs in r_eles:
                # Skip readings restricted to other kanji forms
                if restrs and keb not in restrs:
                    continue

                reading_hira = kata_to_hira(reb)

                # Extract kana prefix and suffix from the word form
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

                # Derive kanji's own reading by stripping surrounding kana
                furigana = reading_hira
                if kana_prefix:
                    if not furigana.startswith(kana_prefix):
                        continue  # Prefix mismatch (rendaku, colloquial, etc.)
                    furigana = furigana[len(kana_prefix):]
                if kana_suffix:
                    if not furigana.endswith(kana_suffix):
                        continue  # Suffix mismatch (rendaku, colloquial, etc.)
                    okurigana = kana_suffix
                    furigana = furigana[:-len(kana_suffix)]
                else:
                    okurigana = ''

                if not furigana or len(furigana) > 4:
                    continue  # Single kanji rarely has 5+ char reading
                if len(okurigana) > 3:
                    continue  # Overly long okurigana indicates compound

                full_reading_text = furigana + ('|' + okurigana if okurigana else '')
                full_reading_hira = furigana + okurigana

                if (kanji_char, full_reading_hira) in existing_pairs:
                    continue

                cell = reading_to_cell(furigana)
                if cell is None or cell[0] not in data['data']:
                    continue

                row, col = cell
                if kanji_char in existing_in_cell[(row, col)]:
                    continue

                score = get_reading_freq(kanji_char, full_reading_hira, freq_map)
                if score < FREQ_THRESHOLD:
                    continue

                entry_str = f"1{kanji_char}{full_reading_text}"
                if col not in data['data'][row]:
                    data['data'][row][col] = []

                data['data'][row][col].append(entry_str)
                existing_in_cell[(row, col)].add(kanji_char)
                existing_pairs.add((kanji_char, full_reading_hira))
                jmdict_count += 1

    print(f"Phase 1b - JMdict expansion: {jmdict_count} new entries")

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

    # --- Phase 4: Remove archaic variants ---
    dedup_count = 0
    for row in data['data']:
        for col in data['data'][row]:
            entries = data['data'][row][col]
            kanji_in_cell = {e[1] for e in entries}
            new_entries = []
            for e in entries:
                common = archaic_to_common.get(e[1])
                if common and common in kanji_in_cell:
                    dedup_count += 1
                else:
                    new_entries.append(e)
            data['data'][row][col] = new_entries
    print(f"\nPhase 4 - Removed {dedup_count} archaic variants")

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
