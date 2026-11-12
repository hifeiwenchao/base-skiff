"""Opt-in tests against user-supplied local disposable database services.

Set BASESKIFF_LIVE_CONFIG to a private JSON file; credentials never belong in
source control. Services encrypt credentials in temporary local data directories.
"""
import json
import os
import uuid
from pathlib import Path

import pytest

from baseskiff.service import DatabaseService


@pytest.fixture(scope='session')
def live_config():
    path = os.environ.get('BASESKIFF_LIVE_CONFIG')
    if not path:
        pytest.skip('Set BASESKIFF_LIVE_CONFIG to run live database integration tests')
    return json.loads(Path(path).read_text())


@pytest.fixture(scope='session')
def run_tag():
    return 'baseskiff_it_' + uuid.uuid4().hex[:12]


@pytest.fixture
def live_service(tmp_path, live_config):
    return DatabaseService(tmp_path)
