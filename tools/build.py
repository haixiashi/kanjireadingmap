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

    # Read current index.html for DD string and CSS
    with open(os.path.join(ROOT_DIR, 'index.html')) as f:
        html = f.read()

    dd = re.search(r'DD="([^"]*)"', html).group(1)
    style = re.search(r'<style>(.*?)</style>', html, re.DOTALL).group(1)

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
    # GZ contains the base-93 gzipped JS
    # DD contains the arithmetic-coded data
    # The bootstrap decodes GZ, decompresses, and evals the result
    # The eval'd code can access DD as a global
    bootstrap = (
        'DD="' + dd + '";\n'
        'GZ="' + gz_b93 + '";\n'
        # Decode base-93 to Uint8Array
        '(async()=>{'
        'let b="",v=0n;'
        'GZ.replace(/./g,(c,i)=>{'
        'v=v*93n+BigInt((c.charCodeAt(0)+26)*58/59-57|0);'
        '++i%13||(b+=v.toString(2).padStart(85,0),v=0n)});'
        'let a=new Uint8Array(b.length>>3);'
        'for(let i=0;i<a.length;i++){'
        'let v=0;for(let j=0;j<8;j++)v=v*2|+b[i*8+j];a[i]=v}'
        # Decompress via DecompressionStream
        'let s=new Blob([a]).stream().pipeThrough(new DecompressionStream("gzip"));'
        'let r=new Response(s);'
        'let t=await r.text();'
        'eval(t)'
        '})()'
    )

    # Build the HTML
    out = (
        '<!DOCTYPE html>\n'
        '<html lang="ja">\n'
        '<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">\n'
        '<title>漢字読み表</title>\n'
        '<style>' + style + '</style>\n'
        '</head>\n'
        '<body>\n'
        '<div class="wp"><table id="kt"><tbody id="tb"></tbody></table></div>\n'
        '<script>\n'
        + bootstrap + '\n'
        '</script>\n'
        '</body>\n'
        '</html>\n'
    )

    with open(os.path.join(ROOT_DIR, 'index.html'), 'w') as f:
        f.write(out)

    print(f"Output: {len(out)} bytes", file=sys.stderr)


if __name__ == '__main__':
    main()
