"""Publish bounded pytest failure details without requiring Actions log access."""

import html
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    report = Path(sys.argv[1])
    if not report.exists():
        print('No pytest report was produced; see the earlier CI step.')
        return 0
    root = ET.parse(report).getroot()
    cases = list(root.iter('testcase'))
    failures = [(case, result) for case in cases for result in case
                if result.tag in {'failure', 'error'}]
    skipped = sum(case.find('skipped') is not None for case in cases)
    counts = f'{len(cases)} tests; {len(failures)} failed; {skipped} skipped'
    print(counts)
    summary = [f'### Pytest results\n\n{counts}\n\n']
    for case, result in failures[:5]:
        name = f"{case.get('classname', '')}.{case.get('name', '')}"
        details = (result.text or result.get('message', ''))[-12000:]
        summary.append(f'<h4>{html.escape(name)}</h4>\n<pre>{html.escape(details)}</pre>\n\n')
        annotation = f'{name[:500]}\n{details[-3500:]}'
        annotation = annotation.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
        print(f'::error title=pytest failure::{annotation}')
    if len(failures) > 5:
        summary.append('Only the first five failures are shown.\n')
    destination = os.environ.get('GITHUB_STEP_SUMMARY')
    if destination:
        with Path(destination).open('a', encoding='utf-8') as stream:
            stream.write(''.join(summary))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
