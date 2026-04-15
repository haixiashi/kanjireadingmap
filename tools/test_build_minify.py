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
    print('build.py minify regression tests passed')


if __name__ == '__main__':
    try:
        main()
    except AssertionError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
