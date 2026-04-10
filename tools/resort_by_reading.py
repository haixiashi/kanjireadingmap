#!/usr/bin/env python3
"""
Re-sort kanji reading map entries by reading frequency.

Uses JMdict word frequency data to compute how often each kanji is read
a particular way. Entries with higher reading frequency come first;
rare/Hyogai readings are pushed to the end.

Approach:
1. Parse KANJIDIC2 for possible readings per kanji
2. Parse JMdict for words with frequency tags
3. Segment compound word readings using KANJIDIC2
4. Aggregate frequency scores per (kanji, reading) pair
5. Re-sort entries in kanjiData
"""

import xml.etree.ElementTree as ET
import json
import re
import os
from collections import defaultdict

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TOOLS_DIR)
KANJIDIC2_PATH = os.path.join(TOOLS_DIR, 'kanjidic2.xml')
JMDICT_PATH = os.path.join(TOOLS_DIR, 'JMdict_e.xml')
INDEX_PATH = os.path.join(PROJECT_DIR, 'index.html')


def is_kanji(c):
    cp = ord(c)
    return (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF
            or 0xF900 <= cp <= 0xFAFF or 0x20000 <= cp <= 0x2A6DF
            or c == '々')


def is_hiragana(c):
    return '\u3040' <= c <= '\u309F'


def is_katakana(c):
    return '\u30A0' <= c <= '\u30FF'


def is_kana(c):
    return is_hiragana(c) or is_katakana(c)


def kata_to_hira(text):
    return ''.join(
        chr(ord(c) - 0x60) if '\u30A1' <= c <= '\u30F6' else c
        for c in text
    )


def normalize_kanjidic_reading(reading):
    """Remove dots and dashes from KANJIDIC2 readings, convert to hiragana."""
    r = reading.replace('.', '').replace('-', '').strip()
    return kata_to_hira(r)


def parse_kanjidic2(path):
    """Parse KANJIDIC2 and build kanji -> list of normalized readings."""
    print(f"Parsing KANJIDIC2...")
    tree = ET.parse(path)
    root = tree.getroot()

    kanji_readings = {}  # kanji -> list of hiragana readings (on converted to hira)

    for char in root.iter('character'):
        literal = char.find('literal').text
        readings = []

        rmg = char.find('reading_meaning')
        if rmg is not None:
            for group in rmg.findall('rmgroup'):
                for reading in group.findall('reading'):
                    r_type = reading.get('r_type')
                    if r_type in ('ja_on', 'ja_kun'):
                        normalized = normalize_kanjidic_reading(reading.text)
                        if normalized and normalized not in readings:
                            readings.append(normalized)

        if readings:
            kanji_readings[literal] = readings

    print(f"  {len(kanji_readings)} kanji with readings")
    return kanji_readings


def segment_reading(word_chars, reading, kanji_readings, depth=0):
    """
    Try to segment a compound word reading into per-kanji readings.
    word_chars: list of characters in the word
    reading: hiragana reading string
    Returns: list of (char, reading_segment) or None if segmentation fails.
    """
    if not word_chars and not reading:
        return []
    if not word_chars or not reading:
        return None
    if depth > 20:
        return None

    char = word_chars[0]
    rest_chars = word_chars[1:]

    if is_kana(char):
        hira_char = kata_to_hira(char)
        if reading[0] == hira_char:
            result = segment_reading(rest_chars, reading[1:], kanji_readings, depth + 1)
            if result is not None:
                return [(char, hira_char)] + result
        # Handle dakuten/handakuten matching for rendaku
        return None

    if char == '々' or not is_kanji(char):
        return None

    # Kanji character - try all known readings
    possible = kanji_readings.get(char, [])
    for r in possible:
        if reading.startswith(r):
            result = segment_reading(rest_chars, reading[len(r):],
                                     kanji_readings, depth + 1)
            if result is not None:
                return [(char, r)] + result

    # Also try single-character reading (for rare cases)
    if len(reading) >= 1:
        result = segment_reading(rest_chars, reading[1:], kanji_readings, depth + 1)
        if result is not None:
            return [(char, reading[0])] + result

    return None


PRI_SCORES = {
    'ichi1': 30, 'ichi2': 15,
    'news1': 20, 'news2': 10,
    'spec1': 15, 'spec2': 8,
    'gai1': 10, 'gai2': 5,
}
for i in range(1, 49):
    PRI_SCORES[f'nf{i:02d}'] = max(1, 49 - i)


def compute_entry_score(pri_tags):
    """Compute a frequency score from JMdict priority tags."""
    if not pri_tags:
        return 0.5  # Word exists but has no frequency data
    return sum(PRI_SCORES.get(t, 0) for t in pri_tags)


def parse_jmdict(path, kanji_readings):
    """
    Parse JMdict and compute (kanji, reading) frequency scores.
    Returns: dict of (kanji_char, reading_hiragana) -> total_frequency_score
    """
    print(f"Parsing JMdict...")

    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Extract entries using regex (avoids XML entity issues)
    entry_pattern = re.compile(r'<entry>(.*?)</entry>', re.DOTALL)
    keb_pattern = re.compile(r'<keb>(.*?)</keb>')
    reb_pattern = re.compile(r'<reb>(.*?)</reb>')
    ke_pri_pattern = re.compile(r'<ke_pri>(.*?)</ke_pri>')
    re_pri_pattern = re.compile(r'<re_pri>(.*?)</re_pri>')
    re_restr_pattern = re.compile(r'<re_restr>(.*?)</re_restr>')

    freq_all = defaultdict(list)  # (kanji_char, reading_hira) -> [word scores]
    total_entries = 0
    segmented = 0
    unsegmented = 0

    for m in entry_pattern.finditer(content):
        entry_text = m.group(1)

        # Extract kanji elements
        k_eles = []
        for km in re.finditer(r'<k_ele>(.*?)</k_ele>', entry_text, re.DOTALL):
            keb = keb_pattern.search(km.group(1))
            pri_tags = ke_pri_pattern.findall(km.group(1))
            if keb:
                k_eles.append((keb.group(1), pri_tags))

        if not k_eles:
            continue  # No kanji forms, skip

        # Extract reading elements
        r_eles = []
        for rm in re.finditer(r'<r_ele>(.*?)</r_ele>', entry_text, re.DOTALL):
            reb = reb_pattern.search(rm.group(1))
            pri_tags = re_pri_pattern.findall(rm.group(1))
            restrs = re_restr_pattern.findall(rm.group(1))
            if reb:
                r_eles.append((reb.group(1), pri_tags, restrs))

        total_entries += 1

        # For each kanji-reading pair
        for keb, k_pri in k_eles:
            for reb, r_pri, restrs in r_eles:
                # If re_restr exists, this reading only applies to specific kanji forms
                if restrs and keb not in restrs:
                    continue

                # Use reading priority if available; only fall back to kanji
                # priority when reading also has its own tags (avoids
                # archaic readings inheriting the kanji form's high score)
                if r_pri:
                    all_pri = list(set(k_pri + r_pri))
                else:
                    all_pri = k_pri if not any(rp for _, rp, _ in r_eles) else []
                score = compute_entry_score(all_pri)

                # Convert reading to hiragana
                reading_hira = kata_to_hira(reb)

                # Extract kanji characters from word
                chars = list(keb)
                kanji_chars_in_word = [c for c in chars if is_kanji(c)]

                if not kanji_chars_in_word:
                    continue

                if len(kanji_chars_in_word) == 1:
                    # Single kanji - extract its reading
                    kanji_char = kanji_chars_in_word[0]
                    # Strip kana from word to get kanji reading
                    # Match kana in word against reading to find kanji's portion
                    kana_suffix = ''
                    kana_prefix = ''
                    i = len(chars) - 1
                    while i >= 0 and is_kana(chars[i]):
                        kana_suffix = kata_to_hira(chars[i]) + kana_suffix
                        i -= 1
                    j = 0
                    while j < len(chars) and is_kana(chars[j]):
                        kana_prefix += kata_to_hira(chars[j])
                        j += 1

                    kanji_reading = reading_hira
                    if kana_suffix and kanji_reading.endswith(kana_suffix):
                        kanji_reading = kanji_reading[:-len(kana_suffix)]
                    if kana_prefix and kanji_reading.startswith(kana_prefix):
                        kanji_reading = kanji_reading[len(kana_prefix):]

                    if kanji_reading:
                        # Collect word scores for decay-weighted aggregation
                        full_reading = reading_hira
                        if kana_prefix:
                            full_reading = reading_hira[len(kana_prefix):]
                        freq_all[(kanji_char, full_reading)].append(score)
                        if full_reading != kanji_reading:
                            freq_all[(kanji_char, kanji_reading)].append(score)
                    segmented += 1

                else:
                    # Multi-kanji compound - try segmentation
                    result = segment_reading(chars, reading_hira, kanji_readings)
                    if result:
                        for char, char_reading in result:
                            if is_kanji(char):
                                freq_all[(char, char_reading)].append(score)
                        segmented += 1
                    else:
                        unsegmented += 1

    print(f"  {total_entries} entries with kanji")
    print(f"  {segmented} segmented, {unsegmented} unsegmented")
    print(f"  {len(freq_all)} unique (kanji, reading) pairs")

    # Use max word score per (kanji, reading) pair.
    freq = {}
    for key, scores in freq_all.items():
        freq[key] = max(scores)
    return freq


def parse_entry(entry_str):
    """Parse a kanjiData entry string."""
    level = int(entry_str[0])
    kanji = entry_str[1]
    reading_text = entry_str[2:]
    parts = reading_text.split('|')
    reading = parts[0]
    okurigana = parts[1] if len(parts) > 1 else ''
    full_reading = reading + okurigana
    return level, kanji, reading, okurigana, full_reading


def get_reading_freq(kanji, full_reading, freq_map):
    """Look up frequency score for a kanji-reading pair."""
    reading_hira = kata_to_hira(full_reading)

    # Try exact match
    score = freq_map.get((kanji, reading_hira), 0)
    if score > 0:
        return score

    # Try without okurigana (just the stem)
    # This handles cases where JMdict has the word with okurigana
    # but our entry format splits it differently
    for (k, r), s in freq_map.items():
        if k == kanji and (r.startswith(reading_hira) or reading_hira.startswith(r)):
            score = max(score, s)

    return score


def sort_entries(entries, freq_map):
    """Sort entries by reading frequency."""
    scored = []
    for idx, entry in enumerate(entries):
        level, kanji, reading, okurigana, full_reading = parse_entry(entry)
        reading_freq = get_reading_freq(kanji, full_reading, freq_map)

        # Sort key: higher freq first, then higher JLPT, then original order
        sort_key = (-reading_freq, -level, idx)
        scored.append((sort_key, entry))

    scored.sort(key=lambda x: x[0])
    return [entry for _, entry in scored]


def extract_kanji_data(html_content):
    """Extract the kanjiData JSON from index.html."""
    match = re.search(
        r'const kanjiData = (\{.*?\n\}\s*\});',
        html_content,
        re.DOTALL
    )
    if not match:
        raise ValueError("Could not find kanjiData in index.html")
    data = json.loads(match.group(1))
    return data, match.start(1), match.end(1)


def format_kanji_data(data):
    """Format kanjiData back to compact JSON matching the original style."""
    lines = []
    lines.append('{')
    lines.append(f'"kana":"{data["kana"]}",')
    lines.append('"data":{')

    kana_list = list(data['data'].keys())
    for ki, kana in enumerate(kana_list):
        readings = data['data'][kana]
        lines.append(f'"{kana}":{{')

        reading_keys = list(readings.keys())
        for ri, rkey in enumerate(reading_keys):
            entries = readings[rkey]
            entries_json = json.dumps(entries, ensure_ascii=False)
            comma = ',' if ri < len(reading_keys) - 1 else ''
            lines.append(f'"{rkey}":{entries_json}{comma}')

        comma = ',' if ki < len(kana_list) - 1 else ''
        lines.append(f'}}{comma}')

    lines.append('}')
    lines.append('}')
    return '\n'.join(lines)


def main():
    # Step 1: Parse KANJIDIC2
    kanji_readings = parse_kanjidic2(KANJIDIC2_PATH)

    # Step 2: Parse JMdict and compute reading frequencies
    freq_map = parse_jmdict(JMDICT_PATH, kanji_readings)

    # Show some example scores
    print("\n=== Example reading frequency scores ===")
    examples = [
        ('円', 'えん'), ('円', 'まど'), ('円', 'まる'),
        ('窓', 'まど'), ('窓', 'そう'),
        ('生', 'せい'), ('生', 'なま'), ('生', 'いきる'), ('生', 'うまれる'),
        ('行', 'こう'), ('行', 'ぎょう'), ('行', 'いく'), ('行', 'あん'),
        ('安', 'あん'), ('会', 'かい'), ('会', 'あう'),
    ]
    for kanji, reading in examples:
        score = freq_map.get((kanji, reading), 0)
        print(f"  {kanji}({reading}): {score:.1f}")

    # Step 3: Read and re-sort kanjiData
    with open(INDEX_PATH, 'r', encoding='utf-8') as f:
        html = f.read()

    data, start, end = extract_kanji_data(html)

    # Show before
    print("\n=== BEFORE sorting ===")
    test_cells = [
        ('ま', 'と', 'まど'),
        ('あ', 'ん', 'あん'),
        ('え', 'ん', 'えん'),
        ('な', 'ま', 'なま'),
        ('せ', 'い', 'セイ'),
    ]
    for row, col, label in test_cells:
        if row in data['data'] and col in data['data'][row]:
            entries = data['data'][row][col]
            print(f"  {label}: {entries[:8]}")

    # Re-sort
    changes = 0
    for kana, readings in data['data'].items():
        for rkey, entries in readings.items():
            if len(entries) <= 1:
                continue
            new_entries = sort_entries(entries, freq_map)
            if new_entries != entries:
                changes += 1
                readings[rkey] = new_entries

    print(f"\n  Cells reordered: {changes}")

    # Show after
    print("\n=== AFTER sorting ===")
    for row, col, label in test_cells:
        if row in data['data'] and col in data['data'][row]:
            entries = data['data'][row][col]
            print(f"  {label}: {entries[:8]}")

    # Detailed view
    print("\n=== Detailed まど cell ===")
    if 'ま' in data['data'] and 'と' in data['data']['ま']:
        for entry in data['data']['ま']['と']:
            level, kanji, reading, okurigana, full_reading = parse_entry(entry)
            score = get_reading_freq(kanji, full_reading, freq_map)
            print(f"  {entry:20s}  freq_score={score:8.1f}  JLPT={level}")

    # Write back
    new_json = format_kanji_data(data)
    new_html = html[:start] + new_json + html[end:]
    with open(INDEX_PATH, 'w', encoding='utf-8') as f:
        f.write(new_html)
    print(f"\nUpdated {INDEX_PATH}")


if __name__ == '__main__':
    main()
