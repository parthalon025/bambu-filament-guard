"""Amounts entered by hand, counted down as prints draw on them, and the link that follows a printer (guard_ledger)."""
import json
import time

import filament_guard as fg
import guard_ledger as gl
from helpers import CFG, H2D_JOB, SLICE, c3, kept


def test_job_mapping_names_the_trays():
    assert gl.mapped_labels({'mapping': [514, 257, 259, 513, 256]}) == ['C3', 'B2', 'B4', 'C2', 'B1']       # read on the H2D, 2026-11-19
    assert gl.mapped_labels({'mapping': [65535, 0, 33000]}) == ['', 'A1', 'HT-A'] and gl.mapped_labels({'mapping': None}) is None
    assert gl.mapped_labels({'mapping': [254], 'vt_tray': {'id': '254'}}) == ['Ext'] and gl.mapped_labels({'mapping': [9999]}) == [None]

def test_entered_amount_counts_down_with_the_jobs_it_sees(state):
    assert c3()['grams'] is None
    gl.enter('SERIAL', {'label': 'C3', 'type': 'PETG', 'colour': '#101010'}, 400)
    tray = c3(); assert tray['grams'] == 400 and tray['weight'] == 400 and tray['entered']
    row = fg.assess({'slot': 1, 'type': 'PETG', 'fid': '', 'colour': '#101010', 'name': 'PETG', 'grams': 100.0, 'nozzle': None}, [tray], CFG)
    assert row['status'] == 'OK' and 'C3 as entered on' in row['note'] and row['trays'][0]['entered']
    fg.remember_slice('Benchy', 1, SLICE)
    watch = gl.JobWatch('SERIAL', CFG)
    assert not watch.look(dict(H2D_JOB, gcode_state='FINISH', task_id='10'))            # first sight: what has run is not counted
    assert not watch.look(dict(H2D_JOB, gcode_state='PREPARE'))                          # nothing drawn yet
    assert watch.look(H2D_JOB) and c3()['grams'] == 280                                  # slot 1 prints from C3: 120 g
    assert not watch.look(dict(H2D_JOB, gcode_state='PAUSE')) and not watch.look(dict(H2D_JOB, gcode_state='FAILED'))   # cancelled: stays counted in full
    assert not watch.look(dict(H2D_JOB, gcode_state='PREPARE')) and watch.look(H2D_JOB) and c3()['grams'] == 160      # the same file printed again
    assert gl.JobWatch('SERIAL', CFG).counted == ['11', 'Benchy_plate A']                # the next guard knows it is counted

def test_a_job_that_cannot_be_counted_makes_the_amount_unknown(state):
    gl.enter('SERIAL', {'label': 'C3', 'type': 'PETG', 'colour': '#101010'}, 400)
    watch = gl.JobWatch('SERIAL', CFG); watch.look(dict(H2D_JOB, gcode_state='IDLE', task_id='1'))
    assert watch.look(dict(H2D_JOB, subtask_name='Sent from the phone', task_id='12'))   # no slice of that name on this computer
    tray = c3(); assert tray['grams'] is None and tray['stale']
    row = fg.assess({'slot': 1, 'type': 'PETG', 'fid': '', 'colour': '#101010', 'name': 'PETG', 'grams': 100.0, 'nozzle': None}, [tray], CFG)
    assert row['status'] == 'UNKNOWN' and 'out of date' in row['note']

def test_entered_amount_ends_with_the_spool(state):
    gl.enter('SERIAL', {'label': 'C3', 'type': 'PETG', 'colour': '#101010'}, 400)
    other = json.loads(json.dumps(H2D_JOB)); other['ams']['ams'][1]['tray'][0]['tray_color'] = 'FFFFFFFF'
    assert all(t['grams'] is None for t in gl.apply(fg.trays_of(other)[0], 'SERIAL') if t['label'] == 'C3')
    assert fg.load_state()['spools']['SERIAL'] == {} and gl.serials() == []

def test_status_without_its_spools_ends_no_entry(state):
    gl.enter('SERIAL', {'label': 'C3', 'type': 'PETG', 'colour': '#101010'}, 400)
    assert gl.apply([], 'SERIAL', complete=False) == [] and gl.serials() == ['SERIAL']  # the spools had not arrived yet
    gl.count('SERIAL', {'gcode_state': 'RUNNING', 'subtask_name': 'x'})                 # a job, and no spools in the status: nothing is certain
    assert c3()['stale']

def test_after_a_gap_another_job_makes_the_amounts_unknown(state):
    gl.enter('SERIAL', {'label': 'C3', 'type': 'PETG', 'colour': '#101010'}, 400); fg.remember_slice('Benchy', 1, SLICE)
    watch = gl.JobWatch('SERIAL', CFG); watch.look(dict(H2D_JOB, gcode_state='FINISH', task_id='10'))
    assert not watch.look(dict(H2D_JOB, gcode_state='FINISH', task_id='10'), gap=True) and c3()['grams'] == 400     # nothing printed meanwhile
    assert watch.look(dict(H2D_JOB, task_id='14'), gap=True) and c3()['stale']          # the fourth job since: how many before it?

def test_a_job_that_fails_to_count_makes_the_amounts_unknown(state, monkeypatch):
    gl.enter('SERIAL', {'label': 'C3', 'type': 'PETG', 'colour': '#101010'}, 400)
    watch = gl.JobWatch('SERIAL', CFG); watch.look(dict(H2D_JOB, gcode_state='IDLE', task_id='1'))
    monkeypatch.setattr(gl, 'count', lambda *a: 1 / 0)
    assert watch.look(H2D_JOB) and c3()['stale'] and watch.counted == ['11', 'Benchy_plate A']

def test_a_printer_is_followed_for_learning_after_a_check_and_while_it_prints(state):
    now = time.time()
    st = {'spools': {'E': {'C3': {}}}, 'looked': {'L': now - 60, 'OLD': now - gl.LOOKED - 1}, 'printing': {'P': now - 60, 'Q': 0, 'GONE': now - gl.PRINTING - 1}}
    assert gl.followed(dict(CFG, learn=True), st) == ['E', 'L', 'P'] and gl.followed(dict(CFG, learn=False), st) == ['E']   # a printer gone mid-print lapses

def test_the_follower_counts_entered_amounts_while_it_learns(state):
    """A printer followed for learning gets its first entered amount halfway through a print: what that print still
    needs comes off at the next look (not the whole print, as a new job would), and the next print comes off in full."""
    fg.remember_slice('Benchy', 1, SLICE, kept({1: [0.0] + [0.5] * 240, 2: [0.0] + [0.125] * 240}))
    fg.update_state(lambda st: st.setdefault('jobs', {}).__setitem__('SERIAL', ['9', 'An entry long gone']))   # from amounts entered before
    halfway = dict(H2D_JOB, layer_num=121)                                                # slot 1 from C3: 60 g still to go of 120 g
    watch = gl.JobWatch('SERIAL', CFG)
    watch.see(halfway); assert watch.recorder.job and watch.recorder.job['name'] == 'Benchy_plate A' and fg.load_state()['jobs']['SERIAL'] == ['9', 'An entry long gone']
    gl.enter('SERIAL', {'label': 'C3', 'type': 'PETG', 'colour': '#101010'}, 400)
    watch.see(halfway); assert c3()['grams'] == 340                                       # weighed now: the rest of this print comes off
    watch.see(halfway); assert c3()['grams'] == 340                                       # once
    watch.see(dict(H2D_JOB, gcode_state='FINISH')); watch.see(dict(H2D_JOB, gcode_state='PREPARE', task_id='12')); watch.see(dict(H2D_JOB, task_id='12'))
    assert c3()['grams'] == 220 and fg.load_state()['printing']['SERIAL'] > 0               # the next job: counted, 120 g

def test_a_follower_says_each_failure_once(state, monkeypatch):
    watch = gl.JobWatch('SERIAL', CFG)
    for _ in range(3): watch.say('following the printer: 10.0.0.9 refused the access code Bambu Studio holds for it')
    monkeypatch.setattr(gl, 'catch_up', lambda *a: (_ for _ in ()).throw(fg.StateBusy('the state folder was held elsewhere for more than 10 s')))
    gl.enter('SERIAL', {'label': 'C3', 'type': 'PETG', 'colour': '#101010'}, 400)
    for _ in range(3): watch.see(H2D_JOB)                                                  # the state file cannot be written: said once, and the count waits
    log = (state / 'guard.log').read_text()
    assert log.count('refused the access code') == 1 and log.count('held elsewhere for more than 10 s; tried again at the next look') == 1
