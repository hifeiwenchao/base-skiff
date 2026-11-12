"""CI exposes real pytest failures safely even to a logged-out reviewer."""

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def test_failure_report_publishes_bounded_utf8_details_and_safe_annotations(tmp_path):
    report = tmp_path / 'results.xml'
    root = ET.Element('testsuites')
    suite = ET.SubElement(root, 'testsuite')
    ET.SubElement(suite, 'testcase', name='passed')
    ET.SubElement(ET.SubElement(suite, 'testcase', name='skipped'), 'skipped')
    for index in range(7):
        case = ET.SubElement(suite, 'testcase', name=f'失败{index}', classname='tests.sample')
        ET.SubElement(case, 'failure').text = 'discarded-prefix' + 'x' * 13000 + '\n<script>100%</script>\n'
    ET.ElementTree(root).write(report, encoding='utf-8')
    summary = tmp_path / 'summary.md'
    result = subprocess.run(
        [sys.executable, 'scripts/report_tests.py', str(report)],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, 'GITHUB_STEP_SUMMARY': str(summary), 'PYTHONIOENCODING': 'utf-8'},
        capture_output=True, text=True, encoding='utf-8', check=True,
    )
    assert '9 tests; 7 failed; 1 skipped' in result.stdout
    assert result.stdout.count('::error title=pytest failure::') == 5
    assert '%0A<script>100%25</script>%0A' in result.stdout
    output = summary.read_text(encoding='utf-8')
    assert '失败0' in output and '失败4' in output and '失败5' not in output
    assert '<script>' not in output and '&lt;script&gt;100%&lt;/script&gt;' in output
    assert 'discarded-prefix' not in output
    assert len(output.encode('utf-8')) < 65000
