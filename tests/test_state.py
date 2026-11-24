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
    cfg['ok_seconds'] = 0; fg.save_config(cfg, 'ok_seconds')
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
        cfg['empty_spool_g'] = 212.0; fg.save_config(cfg, 'empty_spool_g')
        assert (state / 'config.json.unreadable').read_bytes() == written and json.load(open(fg.CONFIG)) == {'empty_spool_g': 212.0}
    text = '{"margin_pct": 25, "ok_seconds": 0,}'; (state / 'config.json').write_text(text)
    assert fg.load_config()['_wrong'] == [f'config.json cannot be read ({why_not_json(text)}): the defaults are in use'] and ': line ' in why_not_json(text)   # the place is named
    assert 'config.json could not be read: copied to config.json.unreadable before a setting was saved' in (state / 'guard.log').read_text()

def test_a_setting_saved_leaves_what_was_edited_by_hand_since_the_guard_started(state):
    """The guard reads config.json when it starts. A setting changed from its menu was written from that copy: a
    printer's address or a margin added by hand meanwhile was gone. The file is read again and the one key set in it."""
    (state / 'config.json').write_text('{"learn": false}'); cfg = fg.load_config()
    edited = {'learn': False, 'margin_pct': 25, 'printers': {'SERIAL': {'ip': '192.0.2.50'}}, 'ok_seconds': '8', 'mine': 'a note'}
    (state / 'config.json').write_text(json.dumps(edited))                               # by hand, while the guard runs
    cfg['spool_reserve_pct'] = 5; fg.save_config(cfg, 'spool_reserve_pct')
    assert json.load(open(fg.CONFIG)) == dict(edited, spool_reserve_pct=5)                # a key the guard does not know, and one it could not use, stay as written
    cfg['spool_reserve_pct'] = 3; fg.save_config(cfg, 'spool_reserve_pct')              # back at its default: that key goes, the rest stays
    assert json.load(open(fg.CONFIG)) == edited

def test_a_guard_ended_while_it_saves_a_setting_leaves_the_file_as_it_was(state, monkeypatch):
    """config.json was opened for writing, which empties it, and then written: a guard ended in between left 0 bytes, and
    the next start ran on the defaults, with learning the user had turned off on again. It is written beside the file,
    flushed to the disk, and put in place by a replace."""
    (state / 'config.json').write_text('{"learn": false, "margin_pct": 25}'); cfg = fg.load_config(); cfg['spool_reserve_pct'] = 5
    def ended(*a, **k): raise KeyboardInterrupt
    with monkeypatch.context() as m:
        m.setattr(fg.json, 'dump', ended)
        with pytest.raises(KeyboardInterrupt): fg.save_config(cfg, 'spool_reserve_pct')
    assert json.load(open(fg.CONFIG)) == {'learn': False, 'margin_pct': 25}
    steps, sync, replace = [], os.fsync, os.replace
    monkeypatch.setattr(fg.os, 'fsync', lambda fd: (steps.append('flushed to the disk'), sync(fd))[1])
    monkeypatch.setattr(fg.os, 'replace', lambda a, b: (steps.append('put in place'), replace(a, b))[1])
    fg.save_config(cfg, 'spool_reserve_pct')
    assert steps == ['flushed to the disk', 'put in place'] and json.load(open(fg.CONFIG)) == {'learn': False, 'margin_pct': 25, 'spool_reserve_pct': 5}
    assert sorted(os.listdir(state)) == ['config.json']                                  # and no temp file stays

def test_a_state_file_is_flushed_to_the_disk_before_it_takes_the_old_ones_place(state, monkeypatch):
    """NTFS journals a rename and not the new file's data: a power cut seconds after a write left state.json as zeros,
    which the next start set aside as damaged, and every entered amount with it. The new file is flushed first, and the
    file it replaces stays beside it as state.json.bak (a rename, not a copy)."""
    steps, sync, replace = [], os.fsync, os.replace
    monkeypatch.setattr(fg.os, 'fsync', lambda fd: (steps.append('flushed to the disk'), sync(fd))[1])
    monkeypatch.setattr(fg.os, 'replace', lambda a, b: (steps.append(f'{os.path.basename(a)} -> {os.path.basename(b)}'), replace(a, b))[1])
    fg.update_state(lambda st: st.__setitem__('spools', {'S': {'A1': {'grams': 400.0}}}))
    assert steps[0] == 'flushed to the disk' and steps[1].endswith('.new -> state.json') and len(steps) == 2
    del steps[:]; fg.update_state(lambda st: st.__setitem__('followed', {'S': 1.0}))
    assert steps[:2] == ['flushed to the disk', 'state.json -> state.json.bak'] and steps[2].endswith('.new -> state.json') and len(steps) == 3
    assert json.load(open(state / 'state.json.bak')) == {'spools': {'S': {'A1': {'grams': 400.0}}}} and sorted(os.listdir(state)) == ['state.json', 'state.json.bak']

def test_a_state_file_lost_to_a_power_cut_is_read_from_its_last_good_copy(state):
    """state.json as zeros (what a power cut leaves of a file whose data was not yet on the disk), cut short, or gone
    between the two steps of a write: the copy of one write earlier is taken, not an empty state, and the log says so.
    Only with no good copy does the guard start empty."""
    entered = {'spools': {'S': {'A1': {'grams': 400.0}}}}
    fg.update_state(lambda st: st.update(entered)); fg.update_state(lambda st: st.__setitem__('followed', {'S': 1.0}))
    (state / 'state.json').write_bytes(b'\0' * 64)
    assert fg.load_state() == entered and (state / 'state.json.damaged').read_bytes() == b'\0' * 64
    assert 'state.json was damaged: set aside as state.json.damaged: its last good copy, state.json.bak, is used' in (state / 'guard.log').read_text()
    assert fg.load_state() == entered and (state / 'guard.log').read_text().count('last good copy') == 1    # it is the state file again: said once
    fg.update_state(lambda st: st.__setitem__('followed', {'S': 2.0})); (state / 'state.json').unlink()   # the guard ended between the two steps of a write
    assert fg.load_state() == entered and 'state.json was gone: its last good copy, state.json.bak, is used' in (state / 'guard.log').read_text()
    fg.update_state(lambda st: st.__setitem__('followed', {'S': 3.0}))
    (state / 'state.json').write_text('{"spools": {"S"'); (state / 'state.json.bak').write_text('{"spools": ')    # neither can be read
    assert fg.load_state() == {} and 'state.json was damaged: set aside as state.json.damaged, and its last good copy cannot be read either: starting empty' in (state / 'guard.log').read_text()
    assert fg.load_state() == {} and fg.update_state(lambda st: st.setdefault('x', 1)) == 1 and fg.load_state() == {'x': 1}

def test_a_state_file_that_holds_no_object_is_set_aside_like_a_damaged_one(state):
    """state.json or learned.json holding null, a list or a number is JSON, so it was not set aside: every change of the
    state then raised AttributeError, for good."""
    import guard_learn as gn
    for text in ('null', '[1]', '5'):
        (state / 'state.json').write_text(text)
        assert fg.update_state(lambda st: st.setdefault('x', 1)) == 1 and (state / 'state.json.damaged').read_text() == text
        (state / 'state.json').unlink(); (state / 'state.json.bak').unlink(missing_ok=True)
        (state / 'learned.json').write_text(text)
        assert gn.load() == {} and (state / 'learned.json.damaged').read_text() == text and not (state / 'learned.json').exists()

def test_only_the_files_that_cannot_be_had_again_keep_a_last_good_copy(state):
    """A per-layer profile is read again from its slice, and goes when its slice does: a copy of each beside it would
    stay for good. What was learned keeps one, and forgetting it takes the copy too: it still held what was forgotten."""
    import guard_learn as gn
    plate = {'layers': 2, 'filaments': [{'slot': 1, 'grams': 3.0}]}
    for _ in range(2): fg.remember_slice('Benchy', 1, plate, {'layers': {1: [1.0, 1.0, 1.0]}, 'purge': {1: [0.0] * 3}, 'pushed': {1: [0.0] * 3}})
    assert [name for name in os.listdir(state / 'profiles') if not name.endswith('.json')] == [] and (state / 'state.json.bak').exists()
    gn.save({'SERIAL': {'C3': {'jobs': []}}}); gn.save({'SERIAL': {'C3': {'jobs': []}, 'A1': {'jobs': []}}})
    assert json.load(open(state / 'learned.json.bak')) == {'SERIAL': {'C3': {'jobs': []}}}
    gn.forget()
    assert gn.load() == {} and not (state / 'learned.json.bak').exists()

def test_the_settings_file_is_with_the_state_not_beside_the_script():
    """Beside the script, config.json stayed behind in the old folder at every update into a new one: the reserve, the
    warnings and a printer's fixed address silently went back to the defaults."""
    assert fg.CONFIG == os.path.join(fg.STATE_DIR, 'config.json') and fg.CONFIG_BEFORE == os.path.join(fg.HERE, 'config.json')

def test_settings_found_beside_the_script_are_brought_over_once(state):
    """The layout up to 1.4.0: with no settings in the state folder yet, a config.json beside the script is read, and
    written to the new place as it is. From then on the new place counts."""
    before = state / 'before' / 'config.json'; before.parent.mkdir(); before.write_bytes(b'\xef\xbb\xbf{"spool_reserve_pct": 5,\r\n "printers": {"SERIAL": "192.0.2.50"}}')
    cfg = fg.load_config()
    assert (cfg['spool_reserve_pct'], cfg['printers']) == (5, {'SERIAL': {'ip': '192.0.2.50'}}) and (state / 'config.json').read_bytes() == before.read_bytes()
    assert f'settings brought over from {before}' in (state / 'guard.log').read_text()
    before.write_text('{"spool_reserve_pct": 2}'); cfg['ok_seconds'] = 0; fg.save_config(cfg, 'ok_seconds')
    assert (fg.load_config()['spool_reserve_pct'], fg.load_config()['ok_seconds']) == (5, 0) and before.read_text() == '{"spool_reserve_pct": 2}'   # the old file is left alone, and read no more
