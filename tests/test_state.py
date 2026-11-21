"""Settings and the state file (filament_guard)."""
import json
import os
import subprocess
import sys
import threading as _threading
import time

import pytest

import filament_guard as fg


RACE = '''
import sys, time, json
sys.path.insert(0, sys.argv[1]); import filament_guard as fg
fg.STATE_DIR = sys.argv[2]
while time.time() < float(sys.argv[3]): pass
for i in range(int(sys.argv[4])): fg.update_state(lambda st: st.__setitem__(sys.argv[5], st.get(sys.argv[5], 0) + 1))
'''

def test_settings_file_holds_only_what_was_changed(state):
    cfg = fg.load_config(); cfg['ok_seconds'] = 0; fg.save_config(cfg)
    assert json.load(open(fg.CONFIG)) == {'ok_seconds': 0} and fg.load_config()['popup_on'] == fg.DEFAULTS['popup_on']

def test_state_file_is_never_replaced_by_an_empty_one(state):
    assert fg.load_state() == {}                                                        # no file: an empty state
    fg.update_state(lambda st: st.__setitem__('spools', {'S': {'A1': {'grams': 5}}}))
    (state / 'state.json').write_text('{"spools": {"S"')                                # cut short by a crash
    assert fg.load_state() == {} and (state / 'state.json.damaged').exists()            # set aside, and said in the log

def test_damaged_settings_fall_back_to_the_defaults(state):
    (state / 'config.json').write_text('{"ok_seconds": ')
    assert fg.load_config() == fg.DEFAULTS

@pytest.mark.windows
def test_two_processes_writing_the_state_file_lose_nothing(state):
    """The watcher and a command beside it each read, change and write state.json: no write may undo another's."""
    fg.update_state(lambda st: st.__setitem__('slices', [{'title': f'P{i}', 'plate': 1, 'layers': 200, 'filaments': [{'slot': 1, 'grams': 12.3}] * 4} for i in range(30)]))
    here, start = os.path.dirname(os.path.abspath(fg.__file__)), time.time() + 1.0
    runs = [subprocess.Popen([sys.executable, '-c', RACE, here, str(state), str(start), '150', key]) for key in ('watcher', 'command')]
    stop, failed = _threading.Event(), []
    def reader():
        while not stop.is_set():
            try: fg.load_state()
            except Exception as e: failed.append(e)
    r = _threading.Thread(target=reader); r.start()
    try: assert [p.wait(60) for p in runs] == [0, 0]
    finally: stop.set(); r.join()
    st = fg.load_state()
    assert (st['watcher'], st['command'], len(st['slices']), failed) == (150, 150, 30, []) and not (state / 'state.json.damaged').exists()
