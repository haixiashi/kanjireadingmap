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
DATA_DIR = os.path.join(PROJECT_DIR, 'data')
KANJIDIC2_PATH = os.path.join(DATA_DIR, 'kanjidic2.xml')
JMDICT_PATH = os.path.join(DATA_DIR, 'JMdict_e.xml')
INDEX_PATH = os.path.join(PROJECT_DIR, 'index.html')

KANA_ROW = ("あいうえおかきくけこさしすせそ"
            "たちつてとなにぬねのはひふへほ"
            "まみむめもやゆよらりるれろわ")
KANA_COL = KANA_ROW + "ん"

_DAKUTEN_MAP = {}
for _group in ['がかぎきぐくげけごこ', 'ざさじしずすぜせぞそ',
               'だたぢちづつでてどと', 'ばはびひぶふべへぼほ',
               'ぱはぴひぷふぺへぽほ']:
    for _i in range(0, len(_group), 2):
        _DAKUTEN_MAP[_group[_i]] = _group[_i + 1]

_SMALL_MAP = {'ぁ': 'あ', 'ぃ': 'い', 'ぅ': 'う', 'ぇ': 'え', 'ぉ': 'お',
              'ゃ': 'や', 'ゅ': 'ゆ', 'ょ': 'よ', 'っ': 'つ'}


def base_kana(c):
    return _SMALL_MAP.get(c, _DAKUTEN_MAP.get(c, c))


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

class ReadingFreqMap(dict):
    """Dictionary of base reading scores plus optional secondary bonuses."""

    def __init__(self, *args, family_bonus=None, leading_ratio=None,
                 alt_forms=None, kanji_kun_sum=None, kanji_on_sum=None,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.family_bonus = family_bonus or {}
        self.leading_ratio = leading_ratio or {}
        self.alt_forms = alt_forms or {}
        self.kanji_kun_sum = kanji_kun_sum or {}
        self.kanji_on_sum = kanji_on_sum or {}


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


def parse_kanjidic2_typed(path):
    """Parse KANJIDIC2 and return kanji -> list of (normalized_reading, is_on)."""
    tree = ET.parse(path)
    result = {}
    for char in tree.getroot().iter('character'):
        literal = char.find('literal').text
        readings = []
        rmg = char.find('reading_meaning')
        if rmg is not None:
            for group in rmg.findall('rmgroup'):
                for reading in group.findall('reading'):
                    r_type = reading.get('r_type')
                    if r_type in ('ja_on', 'ja_kun'):
                        normalized = normalize_kanjidic_reading(reading.text)
                        if normalized:
                            readings.append((normalized, r_type == 'ja_on'))
        if readings:
            result[literal] = readings
    return result


def parse_kanjidic2_grade(path):
    """Parse KANJIDIC2 and return kanji -> school grade."""
    tree = ET.parse(path)
    kd_grade = {}
    for char in tree.getroot().iter('character'):
        lit = char.find('literal').text
        grade_el = char.find('misc').find('grade')
        if grade_el is not None:
            kd_grade[lit] = int(grade_el.text)
    return kd_grade


def parse_kanjidic2_freq(path):
    """Parse KANJIDIC2 and return kanji -> newspaper frequency rank."""
    tree = ET.parse(path)
    kd_freq = {}
    for char in tree.getroot().iter('character'):
        lit = char.find('literal').text
        freq_el = char.find('misc').find('freq')
        if freq_el is not None:
            kd_freq[lit] = int(freq_el.text)
    return kd_freq


def parse_kanjidic2_kun_families(path):
    """Parse KANJIDIC2 kun readings with okurigana as (stem, full) pairs."""
    tree = ET.parse(path)
    root = tree.getroot()
    families = defaultdict(list)

    for char in root.iter('character'):
        literal = char.find('literal').text
        rmg = char.find('reading_meaning')
        if rmg is None:
            continue
        for group in rmg.findall('rmgroup'):
            for reading in group.findall('reading'):
                if reading.get('r_type') != 'ja_kun':
                    continue
                clean = reading.text.strip('-')
                if '.' not in clean:
                    continue
                stem, okuri = clean.split('.', 1)
                stem_hira = kata_to_hira(stem)
                full_hira = kata_to_hira(stem + okuri)
                if stem_hira and full_hira and stem_hira != full_hira:
                    families[literal].append((stem_hira, full_hira))

    return families


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
    lead_count = defaultdict(int)  # (kanji, reading) -> times kanji leads
    word_count = defaultdict(int)  # (kanji, reading) -> total appearances
    _kd_reading_types = parse_kanjidic2_typed(KANJIDIC2_PATH)
    alt_forms = defaultdict(list)  # reading_hira -> list of (primary, secondaries)
    kun_families = parse_kanjidic2_kun_families(KANJIDIC2_PATH)
    total_entries = 0
    segmented = 0
    unsegmented = 0

    for m in entry_pattern.finditer(content):
        entry_text = m.group(1)

        # Extract kanji elements
        ke_inf_pattern = re.compile(r'<ke_inf>&(\w+);</ke_inf>')
        k_eles = []
        for km in re.finditer(r'<k_ele>(.*?)</k_ele>', entry_text, re.DOTALL):
            keb = keb_pattern.search(km.group(1))
            pri_tags = ke_pri_pattern.findall(km.group(1))
            inf_tags = set(ke_inf_pattern.findall(km.group(1)))
            if keb:
                k_eles.append((keb.group(1), pri_tags, inf_tags))

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

        # Detect alternative single-kanji keb forms sharing a reading.
        # Keb order in JMdict reflects primary → secondary, so we preserve
        # it as (primary_kanji, set_of_secondaries).
        if len(k_eles) >= 2:
            single_k = {}  # preserves insertion (keb) order
            for keb, _, _ in k_eles:
                chars = list(keb)
                kanji_chars = [c for c in chars if is_kanji(c)]
                if len(kanji_chars) == 1 and all(
                        is_kanji(c) or is_kana(c) for c in chars):
                    sfx = ''
                    for c in reversed(chars):
                        if is_kana(c):
                            sfx = kata_to_hira(c) + sfx
                        else:
                            break
                    pfx = ''
                    for c in chars:
                        if is_kana(c):
                            pfx += kata_to_hira(c)
                        else:
                            break
                    single_k[kanji_chars[0]] = (pfx, sfx)
            if len(single_k) >= 2:
                for reb, _, _ in r_eles:
                    rh = kata_to_hira(reb)
                    matching = []
                    for k, (pfx, sfx) in single_k.items():
                        ok = True
                        if pfx and not rh.startswith(pfx):
                            ok = False
                        if sfx and not rh.endswith(sfx):
                            ok = False
                        if ok:
                            matching.append(k)
                    if len(matching) >= 2:
                        alt_forms[rh].append(
                            (matching[0], frozenset(matching[1:])))

        # Score all kanji forms except those tagged as rare/archaic (oK, rK, iK).
        # This allows legitimate alternate spellings (e.g. 代わる vs 替わる)
        # to receive proper scores while keeping archaic variants low.
        for k_idx, (keb, k_pri, k_inf) in enumerate(k_eles):
            if k_inf & {'oK', 'rK', 'iK'}:
                continue
            applicable_r_eles = []
            for reb, r_pri, restrs in r_eles:
                # If re_restr exists, this reading only applies to specific kanji forms
                if restrs and keb not in restrs:
                    continue
                applicable_r_eles.append((reb, r_pri))

            single_reading_form = len(applicable_r_eles) == 1
            for reb, r_pri in applicable_r_eles:
                # JMdict ke_pri is attached to the written form, not necessarily to
                # every reading of that form. Only let keb-level priority flow into a
                # reading when that written form has a single applicable reading.
                #
                # If a keb has multiple readings, only readings with explicit re_pri
                # may inherit the keb's priority. This prevents cases like 銅 from
                # incorrectly giving nf06 to あかがね.
                if single_reading_form:
                    all_pri = list(set(k_pri + r_pri)) if (k_pri or r_pri) else []
                elif r_pri:
                    all_pri = list(set(k_pri + r_pri)) if k_pri else r_pri
                else:
                    all_pri = []
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
                        full_reading = reading_hira
                        if kana_prefix:
                            full_reading = reading_hira[len(kana_prefix):]
                        freq_all[(kanji_char, full_reading)].append(score)
                        if full_reading != kanji_reading:
                            freq_all[(kanji_char, kanji_reading)].append(score)
                        is_leading = (chars.index(kanji_char) == 0)
                        word_count[(kanji_char, kanji_reading)] += 1
                        if is_leading:
                            lead_count[(kanji_char, kanji_reading)] += 1
                    segmented += 1

                else:
                    # Multi-kanji compound - try segmentation
                    result = segment_reading(chars, reading_hira, kanji_readings)
                    if result:
                        first_kanji = True
                        for char, char_reading in result:
                            if is_kanji(char):
                                freq_all[(char, char_reading)].append(score)
                                word_count[(char, char_reading)] += 1
                                if first_kanji:
                                    lead_count[(char, char_reading)] += 1
                                first_kanji = False
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

    # Conservative secondary signal for kun-yomi dictionary forms:
    # if a weakly-scored lemma has multiple common same-kanji derived forms
    # sharing its KANJIDIC2 stem, let that family evidence raise the lemma.
    # This helps cases like 忘れる, where 忘れ物 is common but the base lemma
    # itself has only ichi1 in JMdict.
    family_hits = defaultdict(list)
    for (kanji, observed), score in freq.items():
        for stem, full in kun_families.get(kanji, []):
            if not full.endswith(('る', 'い')):
                continue
            family_prefix = full[:-1]
            if observed != full and observed.startswith(family_prefix):
                family_hits[(kanji, full)].append(score)

    family_bonus = {}
    boosted = 0
    for key, hits in family_hits.items():
        base = freq.get(key, 0)
        if base > 30 or not hits:
            continue
        hits.sort(reverse=True)
        if hits[0] < 45:
            continue
        bonus = min(35, hits[0] * 0.6)
        if bonus > 0:
            family_bonus[key] = bonus
            boosted += 1

    print(f"  {boosted} weak kun readings boosted by family evidence")

    leading_ratio = {}
    for key in word_count:
        leading_ratio[key] = lead_count[key] / word_count[key]

    # Per-kanji total score across KANJIDIC2 readings, split by on/kun.
    # Kun readings that are prefixes of each other (e.g. やわ, やわら,
    # やわらか, やわらかい) are stem variants — count only the best score
    # per stem group to avoid diluting dominance.
    kanji_kun_sum = defaultdict(float)
    kanji_on_sum = defaultdict(float)
    for kanji, info_readings in _kd_reading_types.items():
        on_readings = {}
        kun_readings = {}
        for r, is_on in info_readings:
            if is_on:
                on_readings[r] = max(on_readings.get(r, 0),
                                     freq.get((kanji, r), 0))
            else:
                kun_readings[r] = max(kun_readings.get(r, 0),
                                      freq.get((kanji, r), 0))
        for r, s in on_readings.items():
            if s > 0:
                kanji_on_sum[kanji] += s
        # Group kun readings by shared prefix: if one reading is a prefix
        # of another, count only the max score in the group.
        kun_sorted = sorted(kun_readings.keys(), key=len)
        stems = {}  # stem -> max score
        for r in kun_sorted:
            matched = None
            for stem in stems:
                if r.startswith(stem):
                    matched = stem
                    break
            if matched:
                stems[matched] = max(stems[matched], kun_readings[r])
            else:
                stems[r] = kun_readings[r]
        for s in stems.values():
            if s > 0:
                kanji_kun_sum[kanji] += s

    return ReadingFreqMap(freq, family_bonus=family_bonus,
                          leading_ratio=leading_ratio,
                          alt_forms=dict(alt_forms),
                          kanji_kun_sum=dict(kanji_kun_sum),
                          kanji_on_sum=dict(kanji_on_sum))


def parse_entry(entry_str):
    """Parse a kanjiData entry string."""
    kanji = entry_str[0]
    reading_text = entry_str[1:]
    parts = reading_text.split('|')
    reading = parts[0]
    okurigana = parts[1] if len(parts) > 1 else ''
    full_reading = reading + okurigana
    return kanji, reading, okurigana, full_reading


def get_reading_freq(kanji, full_reading, freq_map):
    """Look up frequency score for a kanji-reading pair."""
    reading_hira = kata_to_hira(full_reading)
    family_bonus = getattr(freq_map, 'family_bonus', {}).get((kanji, reading_hira), 0)

    # Try exact match
    score = freq_map.get((kanji, reading_hira), 0)
    if score > 0:
        return score + family_bonus if score <= 30 else score

    # Try without okurigana (just the stem)
    # This handles cases where JMdict has the word with okurigana
    # but our entry format splits it differently
    for (k, r), s in freq_map.items():
        if k == kanji and (r.startswith(reading_hira) or reading_hira.startswith(r)):
            score = max(score, s)

    return score + family_bonus if score <= 30 else score


def _reading_dominance(kanji, full_reading, freq_map, is_on=False):
    """Fraction of this kanji's same-type reading weight owned by this reading.

    Computed within on-yomi or kun-yomi separately, so a kanji with one
    kun reading and one on reading gets dominance 1.0 for each, not 0.5.
    """
    score = get_reading_freq(kanji, full_reading, freq_map)
    if is_on:
        total = getattr(freq_map, 'kanji_on_sum', {}).get(kanji, 0)
    else:
        total = getattr(freq_map, 'kanji_kun_sum', {}).get(kanji, 0)
    if total <= 0:
        return 0.5
    return score / total


def _effective_score(kanji, full_reading, freq_map):
    """Score with penalty for de-facto suffix readings.

    When JMdict evidence shows a (kanji, reading) pair almost never
    appears with the kanji in leading position (leading ratio < 0.05),
    halve the score.  Only exact key matches are used.
    """
    score = get_reading_freq(kanji, full_reading, freq_map)
    reading_hira = kata_to_hira(full_reading)
    lr = getattr(freq_map, 'leading_ratio', {}).get((kanji, reading_hira))
    if lr is not None:
        if lr == 0.0:
            score = 0
        elif lr < 0.05:
            score *= 0.5
    return score


def sort_entries(entries, freq_map, kd_freq=None, kd_grade=None):
    """Sort entries with contiguous on-yomi reading groups.

    Kun-yomi stay ahead of on-yomi for codec compatibility. Entries are
    sorted by effective reading frequency (with suffix penalty), then
    KANJIDIC2 school grade (lower = more fundamental), then newspaper
    frequency rank, then Unicode codepoint.
    """
    if kd_freq is None:
        kd_freq = {}
    if kd_grade is None:
        kd_grade = {}

    parsed = []
    for entry in entries:
        kanji, reading, okurigana, full_reading = parse_entry(entry)
        is_on = bool(reading) and is_katakana(reading[0])
        reading_freq = _effective_score(kanji, full_reading, freq_map)
        grade = kd_grade.get(kanji, 99)
        freq_rank = kd_freq.get(kanji, 99999)
        codepoint = ord(kanji)
        parsed.append((entry, kanji, reading, okurigana, is_on,
                        reading_freq, grade, freq_rank, codepoint))

    kun = []
    on_groups = {}
    for entry, kanji, reading, okurigana, is_on, reading_freq, grade, freq_rank, codepoint in parsed:
        sort_key = (-reading_freq, grade, freq_rank, codepoint)
        if not is_on:
            kun.append((sort_key, entry))
            continue
        key = (reading, okurigana)
        bucket = on_groups.setdefault(key, [])
        bucket.append((sort_key, entry, reading_freq, freq_rank))

    kun.sort(key=lambda x: x[0])

    grouped = []
    for key, bucket in on_groups.items():
        bucket.sort(key=lambda x: x[0])
        best_score = max(item[2] for item in bucket)
        best_freq = min(item[3] for item in bucket)
        grouped.append(((-best_score, best_freq, key[0]),
                         [item[1] for item in bucket]))

    grouped.sort(key=lambda x: x[0])
    ordered_on = []
    for _, group_entries in grouped:
        ordered_on.extend(group_entries)

    result = [entry for _, entry in kun] + ordered_on

    # Within alt-form groups sharing a reading AND the same on/kun type,
    # ensure the JMdict primary sorts before secondaries — but only when
    # both have the same effective score (don't override score differences).
    alt_forms = getattr(freq_map, 'alt_forms', {})
    entry_scores = {}
    for e in result:
        k, r, o, fr = parse_entry(e)
        entry_scores[id(e)] = _effective_score(k, fr, freq_map)
    by_reading = {}
    for i, e in enumerate(result):
        k, r, o, fr = parse_entry(e)
        is_on = bool(r) and is_katakana(r[0])
        by_reading.setdefault((kata_to_hira(fr), is_on), []).append((i, k, e))
    for (full_hira, _), members in by_reading.items():
        if len(members) < 2:
            continue
        for primary, secondaries in alt_forms.get(full_hira, []):
            pri = [(i, k, e) for i, k, e in members if k == primary]
            secs = [(i, k, e) for i, k, e in members if k in secondaries]
            if not pri or not secs:
                continue
            pi, _, pe = pri[0]
            ps = entry_scores[id(pe)]
            for si, _, se in secs:
                if si < pi and entry_scores[id(se)] == ps:
                    result[si], result[pi] = result[pi], result[si]
                    pi = si

    return result


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
            kanji, reading, okurigana, full_reading = parse_entry(entry)
            score = get_reading_freq(kanji, full_reading, freq_map)
            print(f"  {entry:20s}  freq_score={score:8.1f}")

    # Write back
    new_json = format_kanji_data(data)
    new_html = html[:start] + new_json + html[end:]
    with open(INDEX_PATH, 'w', encoding='utf-8') as f:
        f.write(new_html)
    print(f"\nUpdated {INDEX_PATH}")


if __name__ == '__main__':
    main()
