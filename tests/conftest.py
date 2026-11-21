"""Fixtures and markers for every test file."""
import sys
import pytest
import filament_guard as fg


@pytest.fixture
def state(tmp_path, monkeypatch):
    """The guard's state and settings in a folder of the test's own."""
    monkeypatch.setattr(fg, 'STATE_DIR', str(tmp_path)); monkeypatch.setattr(fg, 'CONFIG', str(tmp_path / 'config.json'))
    return tmp_path


def pytest_collection_modifyitems(config, items):
    # The card, the icon and the state file's named mutex call the Windows API: off Windows those tests cannot run, so
    # they are skipped and say why, rather than failing on ctypes.windll. CI runs on Windows, where every test runs.
    if sys.platform == 'win32': return
    skip = pytest.mark.skip(reason='needs Windows (the card, the icon or the named mutex call the Windows API)')
    for item in items:
        if 'windows' in item.keywords: item.add_marker(skip)
