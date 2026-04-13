#!/usr/bin/env python3
"""Build index.html from kanjimap.js and snapshot.json.

1. Computes probability models from snapshot.json
2. Replaces variable placeholders in kanjimap.js with computed values
3. Encodes snapshot data into arithmetic-coded D string
4. Deflate-raw compresses the JS payload, encodes as base-93 (F string)
5. Assembles final HTML with bootstrap

Usage: PYTHONPATH=tools python3 tools/build.py
"""

import zlib
import os
import re
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(TOOLS_DIR)

sys.path.insert(0, TOOLS_DIR)
from reencode_da import encode_b93, decode_b93

RENAME_MAP = {
    # 1-char names, assigned by descending usage frequency.
    # Pool (36 available): A C E F G H I J K L M N O P Q R S T U V W X Y Z _ $ h j l m o p q r u w
    # Already used as-is in source (excluded): B D a b c d e f g i k n s t v x y z
    'pivotX':               'P',   # freq 72
    'viewport':             'V',   # freq 44
    'gesture':              'G',   # freq 38
    'td':                   'T',   # freq 27 (unrenammed local)
    'hoverCard':            'H',   # freq 21
    'scale':                'S',   # freq 21
    'span':                 'Z',   # freq 20
    'rect':                 'R',   # freq 19 (unrenammed local)
    'table':                'W',   # freq 18
    'modeIdx':              'I',   # freq 17
    'sym':                  'Y',   # freq 15 (unrenammed local in decode)
    'rangeLow':             'L',   # freq 14
    'decode':               'O',   # freq 14
    'watermark':            'M',   # freq 13
    'modes':                'N',   # freq 12
    'rangeHigh':            'J',   # freq 11
    'hoverCell':            'K',   # freq 11
    'el':                   'E',   # freq 11 (unrenammed local)
    'scrollH':              'Q',   # freq 10
    'ratio':                'U',   # freq 10
    'rangeValue':           '_',   # freq  9
    'entries':              'C',   # freq  9
    'minimap':              '$',   # freq  9
    'cx':                   'h',   # freq  8 (unrenammed local)
    'cy':                   'j',   # freq  8 (unrenammed local)
    'RANGE_TOP':            'l',   # freq  8
    'velX':                 'm',   # freq  8
    'velY':                 'o',   # freq  8
    'isOn':                 'p',   # freq  7
    'tbody':                'q',   # freq  7 (unrenammed local)
    'themeBtn':             'r',   # freq  7
    'TABLE_MARGIN':         'u',   # freq  7
    'mmDrag':               'w',   # freq  7
    'mmView':               'A',   # freq  7
    'RANGE_QUARTER':        'F',   # freq  6
    'RANGE_MODULUS':        'X',   # freq  6

    # 2-char names for the rest
    'pivotY':               'py',
    'innerBoundaries':      'ib',
    'decodeUniform':        'du',
    'cellKana':             'ck',
    'colKana':              'ck',  # same target ok — different scopes
    'okurigana':            'og',
    'rubyEl':               'rb',
    'rowBorders':           'rb',  # same target ok — different scopes
    'readingBtn':           'rB',
    'hiddenClass':          'hc',
    'cellW':                'cw',
    'animFrame':            'af',
    'wrapper':              'wr',
    'scaleRatio':           'sr',
    'isDark':               'id',
    'prevTier':             'pt',
    'reading':              'rd',
    'storage':              'sg',
    'side':                 'sd',
    'MINIMAP_SIZE':         'ms',
    'dragging':             'dg',
    'didDrag':              'dd',
    'mmNavigate':           'MN',
    'schedMinimap':         'SM',
    'startCell':            's0',
    'codepoint':            'cp',
    'kanjiTable':           'kT',
    'kanaCumFreq':          'kf',
    'kanjiGroup':           'kg',
    'contentDiv':           'cd',
    'showHover':            'SH',
    'lastX':                'lx',
    'lastY':                'ly',
    'mmPending':            'mp',
    'resetTimer':           'rT',
    'applyScale':           'AS',
    'coast':                'CO',
    'prevScale':            'ps',
    'bitString':            'bs',
    'bitPos':               'bp',
    'normalize':            'nz',
    'deltaRange':           'dr',
    'kanaFreqAcc':          'fa',
    'kanaGridCodepoint':    'gc',
    'firstKanaVariant':     'fv',
    'katakanaShift':        'ks',
    'rtEl':                 're',
    'rowKana':              'rk',
    'colBorders':           'cb',
    'modeLabels':           'ml',
    'isKatakana':           'ik',
    'visibleCount':         'vc',
    'largeAssigned':        'la',
    'visible':              'vi',
    'contentW':             'cW',
    'contentH':             'cH',
    'wrapW':                'wW',
    'wrapH':                'wH',
    'mouseX':               'mx',
    'mouseY':               'my',
    'dist':                 'di',
    'newCX':                'nx',
    'newCY':                'ny',
    'entry':                'en',
    'decodeCell':           'DC',
    'kanaGrid':             'KG',
    'makeEntrySpan':        'ME',
    'updateReadings':       'UR',
    'resetWillChange':      'RW',
    'scheduleWillChangeReset': 'SR',
    'updateMM':             'UM',
    'startDrag':            'SD',
    'moveDrag':             'MD',
    'endDrag':              'ED',
    'lastTime':             'lt',
    'tableW':               'tW',
    'tableH':               'tH',
    'cells':                'cl',
    'colLabel':             'cl',  # same target ok — different scopes
    'secondKanaVariant':    'sv',
    'variantOffsets':       'vo',
    'colIdx':               'ci',
    'rowLabel':             'rl',
    'rowIdx':               'ri',
    'spans':                'ss',
    'startScrollX':         'sx',
    'startScrollY':         'sy',
    'translateX':           'tx',
    'translateY':           'ty',
    # Added with clipCellEntries / fsCap / font-scale features
    'clipCellEntries':      'CE',
    'maxLargeEntryWidth':   'mW',
    'allSpans':             'aS',
    'allContent':           'aC',
    'firstKun':             'fK',
    'firstOn':              'fO',
    'fsCap':                'fc',
    'anyHidden':            'ah',
    'widestKun':            'wK',
    'widestOn':             'wO',
    'maxKunLen':            'mK',
    'maxOnLen':             'mO',
    'overflows':            'ov',
    'transformScale':       'ts',
    'fontScale':            'fs',
    'contentH':             'cH',
}


def minify_js(code, rename_map):
    """Strip comments, rename identifiers, collapse whitespace.

    Uses a single-pass tokenizer so string literals are never modified.
    Whitespace is dropped and re-inserted only where required between
    adjacent word tokens (identifiers/keywords/numbers).
    """
    import re
    TOKEN_RE = re.compile(
        r'("(?:[^"\\]|\\.)*")'                             # double-quoted string
        r"|('(?:[^'\\]|\\.)*')"                            # single-quoted string
        r'|(`(?:[^`\\]|\\.)*`)'                            # template literal
        r'|(//[^\n]*)'                                     # line comment
        r'|(/\*[\s\S]*?\*/)'                               # block comment
        r'|([a-zA-Z_$][a-zA-Z0-9_$]*)'                    # identifier / keyword
        r'|(0[xX][0-9a-fA-F]+|[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)'  # number (hex or decimal)
        r'|(\s+)'                                          # whitespace
        r'|(.)'                                            # any other char
    )
    out = []
    prev_word = False  # last emitted token was an identifier/keyword/number

    for m in TOKEN_RE.finditer(code):
        str_dq, str_sq, template, line_cmt, block_cmt, ident, num, ws, other = m.groups()

        if str_dq or str_sq or template:
            out.append(m.group(0))
            prev_word = False
        elif line_cmt or block_cmt:
            pass  # strip
        elif ident:
            tok = rename_map.get(ident, ident)
            if prev_word:
                out.append(' ')
            out.append(tok)
            prev_word = True
        elif num:
            if prev_word:
                out.append(' ')
            out.append(m.group(0))
            prev_word = True
        elif ws:
            pass  # strip; spacing re-added by prev_word logic
        else:
            out.append(other)
            prev_word = False

    return ''.join(out)


def main():
    # Read the JS payload
    with open(os.path.join(TOOLS_DIR, 'kanjimap.js')) as f:
        js_payload = f.read()

    # Compute models from snapshot and inject into JS
    import json as _json
    from reencode_bac import (compute_models, M_CELL, M_KT0, M_KT1, M_ONKUN,
                               M_TDP, M_D1K, M_D1O, M_D2_0, M_D2_1,
                               M_EXTRA, M_OKURI)
    with open(os.path.join(TOOLS_DIR, 'snapshot.json')) as f:
        snap = _json.load(f)
    compute_models(snap)
    from reencode_bac import (M_CELL, M_KT0, M_KT1, M_ONKUN,
                               M_TDP, M_D1K, M_D1O, M_D2_0, M_D2_1,
                               M_EXTRA, M_OKURI)

    # Inline model values into JS (replace variable refs with literals)
    def inner(m):
        return m[1:-1] if isinstance(m[0], int) else m

    # Compute KT length from snapshot
    all_kanji = set()
    for entries in snap.values():
        for e in entries:
            cp = ord(e[1])
            if 0x4E00 <= cp < 0x10000:
                all_kanji.add(e[1])
    kl = len(all_kanji)

    # Replace variable placeholders with computed values
    from reencode_bac import M_KD_CASE
    kd = ','.join(str(x) for x in inner(M_KD_CASE))
    kp = ','.join(str(inner(m)[0]) for m in M_KT0)
    tp = ','.join(str(inner(m)) for m in M_TDP[1:])
    d2_0 = ','.join(str(x) for x in inner(M_D2_0))
    d2_1 = ','.join(str(x) for x in inner(M_D2_1))
    replacements = [
        ('decode(KD)', f'decode({kd})'),
        ('decodeUniform(KL)', f'decodeUniform({kl})'),
        ('KL - 1', f'{kl-1}'),
        ('decode(CP)', f'decode({inner(M_CELL)[0]})'),
        ('decode(K1)', f'decode({inner(M_KT1)[0]})'),
        ('decode(OK)', f'decode({inner(M_ONKUN)[0]})'),
        ('decode(isOn ? DO : DK)', f'decode(isOn?{inner(M_D1O)[0]}:{inner(M_D1K)[0]})'),
        ('decode(D0)', f'decode({d2_0})'),
        ('decode(D1)', f'decode({d2_1})'),
        ('decode(EF)', f'decode({inner(M_EXTRA)[0]})'),
        ('decode(OF)', f'decode({inner(M_OKURI)[0]})'),
        ('KP[prevTier - 1]', f'[{kp}][prevTier-1]'),
        ('TP[prevTier - 1]', f'[{tp.replace(" ","")}][prevTier-1]'),
    ]
    for old, new in replacements:
        js_payload = js_payload.replace(old, new)

    # Validate: check no symbolic placeholders remain unreplaced
    import re as _re
    KNOWN_PLACEHOLDERS = ['KD', 'KL', 'CP', 'K1', 'OK', 'DO', 'DK', 'D0', 'D1',
                          'EF', 'OF', 'KP', 'TP']
    for ph in KNOWN_PLACEHOLDERS:
        if _re.search(r'\b' + ph + r'\b', js_payload):
            print(f"ERROR: placeholder {ph!r} was not replaced in JS", file=sys.stderr)
            sys.exit(1)

    # Minify: rename identifiers + strip whitespace/comments
    js_minified = minify_js(js_payload, RENAME_MAP)
    print(f"JS: {len(js_payload)} bytes → minified: {len(js_minified)} bytes", file=sys.stderr)

    # Write minified JS for reference
    with open(os.path.join(TOOLS_DIR, 'kanjimap_processed.js'), 'w') as f:
        f.write(js_minified)

    # Encode D string from snapshot
    from reencode_bac import encode_snapshot
    dd = encode_snapshot(snap)

    # Gzip the minified JS payload
    gz = zlib.compress(js_minified.encode('utf-8'), level=9, wbits=-15)
    print(f"Minified: {len(js_minified)} bytes → gzip: {len(gz)} bytes", file=sys.stderr)

    # Encode gzipped bytes as base-93
    bits = []
    for byte in gz:
        for bit in range(7, -1, -1):
            bits.append((byte >> bit) & 1)
    gz_b93 = encode_b93(bits)
    print(f"Base-93: {len(gz_b93)} chars", file=sys.stderr)

    bootstrap = (
        'D="' + dd + '";\n'
        'F="' + gz_b93 + '";\n'
        # Shared base-93 decoder (used by bootstrap and eval'd DC decoder)
        'B=s=>{let b="",v=0n;'
        '[...s].map((c,i)=>{'
        'v=v*93n+BigInt((c.charCodeAt(0)+26)*58/59-57|0);'
        '++i%13||(b+=v.toString(2).padStart(85,0),v=0n)});'
        'return b};\n'
        # Decode F from base-93, truncated to exact gzip length
        '(async()=>{'
        'let b=B(F);'
        'let a=new Uint8Array(' + str(len(gz)) + ');'
        'for(let i=0;i<' + str(len(gz)) + ';i++)'
        'a[i]=parseInt(b.substr(i*8,8),2);'
        'let s=new Blob([a]).stream().pipeThrough(new DecompressionStream("deflate-raw"));'
        'eval(await new Response(s).text())'
        '})()'
    )

    # Build the HTML
    out = (
        '<!DOCTYPE html>\n'
        '<meta charset="UTF-8">\n'
        '<script>\n'
        + bootstrap + '\n'
        '</script>\n'
    )

    out_path = os.path.join(ROOT_DIR, 'index.html')
    with open(out_path, 'w') as f:
        f.write(out)

    file_size = os.path.getsize(out_path)
    print(f"Output: {file_size} bytes", file=sys.stderr)



if __name__ == '__main__':
    main()
