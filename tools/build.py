#!/usr/bin/env python3
"""Build index.html with gzip-compressed JS payload.

Reads tools/kanjimap.js (the uncompressed JS), gzips it, encodes as
base-93, and embeds it in index.html with a bootstrap that decodes,
decompresses, and evals at runtime.

Usage: python3 tools/build.py
"""

import gzip
import os
import re
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(TOOLS_DIR)

sys.path.insert(0, TOOLS_DIR)
from reencode_da import encode_b93, decode_b93

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

    # Remove the model declaration line
    lines = js_payload.split('\n')
    js_payload = '\n'.join(l for l in lines if not l.startswith('CP='))

    # Replace variable references with computed values
    kp = ','.join(str(inner(m)[0]) for m in M_KT0)
    tp = ','.join(str(inner(m)) for m in M_TDP[1:])
    replacements = [
        ('Z(CP)', f'Z({inner(M_CELL)[0]})'),
        ('Z(K1)', f'Z({inner(M_KT1)[0]})'),
        ('Z(OK)', f'Z({inner(M_ONKUN)[0]})'),
        ('Z(x?DO:DK)', f'Z(x?{inner(M_D1O)[0]}:{inner(M_D1K)[0]})'),
        ('Z(...D0)', f'Z({",".join(str(x) for x in inner(M_D2_0))})'),
        ('Z(...D1)', f'Z({",".join(str(x) for x in inner(M_D2_1))})'),
        ('Z(EF)', f'Z({inner(M_EXTRA)[0]})'),
        ('Z(OF)', f'Z({inner(M_OKURI)[0]})'),
        ('KP[pt-1]', f'[{kp}][pt-1]'),
        ('TP[pt-1]', f'[{tp.replace(" ","")}][pt-1]'),
    ]
    for old, new in replacements:
        js_payload = js_payload.replace(old, new)

    # Read current index.html for D string
    with open(os.path.join(ROOT_DIR, 'index.html')) as f:
        html = f.read()

    dd = re.search(r'D="([^"]*)"', html).group(1)

    # Gzip the JS payload
    gz = gzip.compress(js_payload.encode('utf-8'), compresslevel=9)
    print(f"JS: {len(js_payload)} bytes → gzip: {len(gz)} bytes", file=sys.stderr)

    # Encode gzipped bytes as base-93
    # Convert bytes to bit string
    bits = []
    for byte in gz:
        for bit in range(7, -1, -1):
            bits.append((byte >> bit) & 1)
    gz_b93 = encode_b93(bits)
    print(f"Base-93: {len(gz_b93)} chars", file=sys.stderr)

    # Bootstrap: decode base-93 → bytes → decompress → eval
    # F contains the base-93 gzipped JS
    # D contains the arithmetic-coded data
    # The bootstrap decodes F, decompresses, and evals the result
    # The eval'd code can access D as a global
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
        # Decompress via DecompressionStream
        'let s=new Blob([a]).stream().pipeThrough(new DecompressionStream("gzip"));'
        'eval(await new Response(s).text())'
        '})()'
    )

    # Build the HTML
    out = (
        '<!DOCTYPE html>\n'
        '<html lang="ja">\n'
        '<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">\n'
        '<title>漢字読み方表</title>\n'
        '</head>\n'
        '<body>\n'
        '<script>\n'
        + bootstrap + '\n'
        '</script>\n'
        '</body>\n'
        '</html>\n'
    )

    out_path = os.path.join(ROOT_DIR, 'index.html')
    with open(out_path, 'w') as f:
        f.write(out)

    file_size = os.path.getsize(out_path)
    print(f"Output: {file_size} bytes", file=sys.stderr)



if __name__ == '__main__':
    main()
