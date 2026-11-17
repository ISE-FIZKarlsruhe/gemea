#!/usr/bin/env python3
# Purpose: Percent-encode bare " characters inside IRI fields in an N-Quads file.
#          QLever's strict parser rejects IRIs containing unencoded double-quotes.
#          Source URLs from some DDB providers (e.g. museum websites) embed " in
#          path segments; these need to become %22.
# Usage:   python3 scripts/transform/tools/fix_nq_iris.py input.nq output.nq
# Inputs:  input.nq  — N-Quads file (possibly large; processed as a stream)
# Outputs: output.nq — cleaned N-Quads file
# Dependencies: stdlib only
# Assumptions: Standard N-Quads encoding; string literals escape " as \".

import sys


def fix_line(line: str) -> str:
    result = []
    i = 0
    s = line.rstrip('\n')

    while i < len(s):
        c = s[i]

        if c == '<':
            # IRI token: collect until >, replace any bare " with %22
            j = i + 1
            while j < len(s) and s[j] != '>':
                j += 1
            iri = s[i + 1:j].replace('"', '%22')
            result.append(f'<{iri}>')
            i = j + 1

        elif c == '"':
            # String literal: pass through verbatim, respecting \" escapes
            result.append(c)
            i += 1
            while i < len(s):
                c2 = s[i]
                if c2 == '\\':
                    result.append(c2)
                    i += 1
                    if i < len(s):
                        result.append(s[i])
                        i += 1
                elif c2 == '"':
                    result.append(c2)
                    i += 1
                    break
                else:
                    result.append(c2)
                    i += 1

        else:
            result.append(c)
            i += 1

    return ''.join(result) + '\n'


def main():
    if len(sys.argv) != 3:
        print(f'Usage: {sys.argv[0]} input.nq output.nq', file=sys.stderr)
        sys.exit(1)

    in_path, out_path = sys.argv[1], sys.argv[2]
    fixed = 0

    with open(in_path, encoding='utf-8') as fin, \
         open(out_path, 'w', encoding='utf-8') as fout:
        for n, line in enumerate(fin, 1):
            if n % 1_000_000 == 0:
                print(f'  {n:,} lines processed, {fixed:,} fixed', file=sys.stderr)
            cleaned = fix_line(line)
            if cleaned != line:
                fixed += 1
            fout.write(cleaned)

    print(f'Done: {n:,} lines, {fixed:,} IRIs fixed → {out_path}', file=sys.stderr)


if __name__ == '__main__':
    main()
