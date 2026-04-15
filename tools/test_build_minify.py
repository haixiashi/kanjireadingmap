#!/usr/bin/env python3

import re
import subprocess
import sys

import build


def minify(code):
    return build.minify_js(code, build.compute_rename_map(code))


def run_js(code):
    return subprocess.run(
        ['node', '-e', code],
        capture_output=True,
        text=True,
        check=False,
    )


def assert_same_runtime(label, code):
    original = run_js(code)
    minified = run_js(minify(code))
    if (original.returncode, original.stdout, original.stderr) != (
        minified.returncode, minified.stdout, minified.stderr
    ):
        raise AssertionError(
            f'{label}: runtime mismatch\n'
            f'original rc={original.returncode} stdout={original.stdout!r} stderr={original.stderr!r}\n'
            f'minified rc={minified.returncode} stdout={minified.stdout!r} stderr={minified.stderr!r}\n'
            f'code={code}\n'
            f'minified={minify(code)}'
        )


def assert_matches(label, code, pattern):
    result = minify(code)
    if not re.search(pattern, result):
        raise AssertionError(
            f'{label}: expected /{pattern}/ in {result}'
        )


def assert_contains(label, code, needle):
    result = minify(code)
    if needle not in result:
        raise AssertionError(
            f'{label}: expected {needle!r} in {result}'
        )


def main():
    assert_same_runtime(
        'for-let-header-and-body',
        'for(let outerIndex=0;outerIndex<3;outerIndex++)console.log(outerIndex)',
    )
    assert_same_runtime(
        'outer-binding-visible-in-child',
        'let outerCount=0;{console.log(outerCount);let innerCount=1;console.log(innerCount)}',
    )
    assert_matches(
        'sibling-block-reuse',
        'let outerCount=0;{let innerCount=1;console.log(innerCount)}{let otherCount=2;console.log(otherCount)}',
        r'^\s*let i=0;\{let i=1;console\.log\(i\)\}\{let i=2;console\.log\(i\)\}\s*$',
    )
    assert_matches(
        'local-frequency-order',
        '{let zName=0;let aName=0;zName++;zName++;aName++;console.log(zName,aName)}',
        r'^\s*\{let i=0,j=0;i\+\+;i\+\+;j\+\+;console\.log\(i,j\)\}\s*$',
    )
    assert_contains(
        'string-literals-not-rewritten',
        'console.log("true false ;}")',
        '"true false ;}"',
    )
    assert_contains(
        'template-literals-not-rewritten',
        'console.log(`true false ;}`)',
        '`true false ;}`',
    )
    assert_contains(
        'strings-not-touched-by-let-merge',
        'console.log("let a=1;let b=2")',
        '"let a=1;let b=2"',
    )
    assert_contains(
        'decimal-leading-zero-trim',
        'console.log(0.5,0.95,1.0)',
        'console.log(.5,.95,1)',
    )
    assert_contains(
        'boolean-literals-rewritten',
        'console.log(true,false)',
        'console.log(!0,!1)',
    )
    assert_matches(
        'property-access-not-rewritten',
        'console.log(obj.true,obj.false)',
        r'console\.log\([A-Za-z_$][A-Za-z0-9_$]*\.true,[A-Za-z_$][A-Za-z0-9_$]*\.false\)',
    )
    css = build._minify_css_numbers('opacity:0.10;zoom:1.0;scale:0.95;')
    if css != 'opacity:.10;zoom:1;scale:.95;':
        raise AssertionError(f'css-number-minify: got {css!r}')
    print('build.py minify regression tests passed')


if __name__ == '__main__':
    try:
        main()
    except AssertionError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
