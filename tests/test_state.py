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

def why_not_json(text):
    """What Python's JSON reader says of a text it cannot read: its words differ from one Python to the next."""
    with pytest.raises(ValueError) as e: json.loads(text)
    return str(e.value)

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
    cfg = fg.load_config(); wrong = cfg.pop('_wrong')
    assert cfg == fg.DEFAULTS and wrong == [f"config.json cannot be read ({why_not_json('{\"ok_seconds\": ')}): the defaults are in use"] and 'line 1 column 16' in wrong[0]

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

def test_a_setting_of_the_wrong_kind_falls_back_to_its_default_alone_and_is_named(state):
    """config.json written by hand: a number as text, an on/off setting as text or a number, a list that is none, a
    weight below zero, one printer's address as a number. Each falls back to its own default; the rest of the file is in
    use; what was wrong is kept for the card at start and the self-test. An address may be plain text or {"ip": text}."""
    (state / 'config.json').write_text(json.dumps({'margin_pct': '25', 'spool_reserve_pct': 5, 'measured_reserve': 'false', 'learn': 0, 'popup_on': None, 'poll_seconds': '2',
                                                  'empty_spool_g': -1, 'printers': {'SERIAL1': '192.0.2.50', 'SERIAL2': {'ip': '192.0.2.51'}, 'SERIAL3': 5}}))
    cfg = fg.load_config(); bad = ('margin_pct', 'measured_reserve', 'learn', 'popup_on', 'poll_seconds', 'empty_spool_g')
    assert {k: cfg[k] for k in bad} == {k: fg.DEFAULTS[k] for k in bad} and cfg['spool_reserve_pct'] == 5
    assert cfg['printers'] == {'SERIAL1': {'ip': '192.0.2.50'}, 'SERIAL2': {'ip': '192.0.2.51'}}
    assert len(cfg['_wrong']) == 7 and 'margin_pct in config.json is "25", not a number from 0 to 100: the default is in use' in cfg['_wrong']
    assert 'measured_reserve in config.json is "false", not true or false: the default is in use' in cfg['_wrong']
    assert any(line.startswith('printers in config.json: the address for the printer …IAL3 is 5') for line in cfg['_wrong'])
    cfg['ok_seconds'] = 0; fg.save_config(cfg)
    assert '_wrong' not in json.load(open(fg.CONFIG))                                    # what was wrong is said, never saved

def test_settings_that_are_no_json_object_leave_the_defaults_in_use_and_say_so(state):
    """config.json holding null, a list or a number ended the guard at its start (TypeError), with nothing on screen."""
    for text in ('null', '[1, 2]', '5', '"margin_pct"'):
        (state / 'config.json').write_text(text); cfg = fg.load_config()
        assert {k: v for k, v in cfg.items() if k != '_wrong'} == fg.DEFAULTS and len(cfg['_wrong']) == 1 and cfg['_wrong'][0].startswith('config.json cannot be read')
    (state / 'config.json').write_text('{"popup_on": ["SHORT", "short"]}')              # a name that is no result
    assert fg.load_config()['popup_on'] == fg.DEFAULTS['popup_on'] and fg.load_config()['_wrong']
    (state / 'config.json').write_text('{"popup_on": ["SHORT"], "at_send": false}')
    assert (fg.load_config()['popup_on'], fg.load_config()['at_send'], fg.load_config().get('_wrong')) == (['SHORT'], False, None)

def test_settings_written_with_a_byte_order_mark_are_read(state):
    """Windows PowerShell 5.1 (Set-Content -Encoding UTF8) and older Notepad put a byte order mark before the JSON. Such
    a file was ignored, with one line in the log: the guard ran on the defaults the user had changed."""
    (state / 'config.json').write_bytes(b'\xef\xbb\xbf{"margin_pct": 25, "popup_on": ["SHORT", "UNKNOWN"]}')
    cfg = fg.load_config()
    assert (cfg['margin_pct'], cfg['popup_on']) == (25, ['SHORT', 'UNKNOWN']) and '_wrong' not in cfg

def test_settings_the_guard_cannot_read_are_kept_when_a_setting_is_saved(state):
    """A trailing comma left by hand, or a file in UTF-16 (PowerShell's ">"): the guard says that the defaults are in
    use, and the next setting saved (a menu click, another empty spool in the amount window) does not cost what was
    written: the file is copied to config.json.unreadable first. It was replaced by the one key saved, with no copy."""
    for written in (b'{"margin_pct": 25, "printers": {"SERIAL": {"ip": "192.0.2.50"}},}', '{"margin_pct": 25}'.encode('utf-16')):
        (state / 'config.json').write_bytes(written)
        cfg = fg.load_config()
        assert cfg['margin_pct'] == 10 and len(cfg['_wrong']) == 1 and cfg['_wrong'][0].startswith('config.json cannot be read (') and cfg['_wrong'][0].endswith('): the defaults are in use')
        cfg['empty_spool_g'] = 212.0; fg.save_config(cfg)
        assert (state / 'config.json.unreadable').read_bytes() == written and json.load(open(fg.CONFIG)) == {'empty_spool_g': 212.0}
    text = '{"margin_pct": 25, "ok_seconds": 0,}'; (state / 'config.json').write_text(text)
    assert fg.load_config()['_wrong'] == [f'config.json cannot be read ({why_not_json(text)}): the defaults are in use'] and ': line ' in why_not_json(text)   # the place is named
    assert 'config.json could not be read: copied to config.json.unreadable before a setting was saved' in (state / 'guard.log').read_text()
