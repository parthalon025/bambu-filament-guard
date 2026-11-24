"""Counting the print that is running before a new plate is checked (guard_ledger, filament_guard.evaluate)."""
import os

import pytest

import filament_guard as fg
import guard_ledger as gl
from helpers import CFG, H2D_JOB, SLICE, kept, running, BLUE, BLUE_JOB, needing, part, printing, spool


PETG_PLATE = {'printer_model': 'Bambu Lab H2D', 'time': '', 'layers': 3,
              'filaments': [{'slot': 1, 'type': 'PETG', 'fid': '', 'colour': '#101010', 'name': 'PETG', 'grams': 100.0, 'nozzle': None}]}

PETG_JOB = {'gcode_state': 'PREPARE', 'subtask_name': 'Benchy', 'task_id': '11', 'layer_num': 0, 'total_layer_num': 3, 'mapping': [514],
            'ams': {'ams': [{'id': '2', 'tray': [{'id': '2', 'tray_type': 'PETG', 'tray_color': '101010FF', 'remain_g': -1, 'remain': -1, 'tray_weight': '0'}]}]}}

def test_the_running_print_owes_what_it_has_not_printed(state):
    plate = {'layers': 3, 'filaments': [{'slot': 1, 'grams': 6.0}, {'slot': 2, 'grams': 3.0}]}
    fg.remember_slice('Benchy', 1, plate, kept({1: [1.0, 2.0, 2.0, 1.0], 2: [0.0, 0.0, 1.0, 2.0]}))
    job, slices = dict(H2D_JOB, subtask_name='Benchy', total_layer_num=3, layer_num=2), fg.load_state()['slices']     # slot 1 prints from C3, slot 2 from B2
    assert gl.owed(job, slices) == ({'C3': 3.0, 'B2': 3.0}, True)                          # layers 2 and 3: the one it is on counts whole
    assert gl.owed(dict(job, gcode_state='PREPARE'), slices) == ({'C3': 6.0, 'B2': 3.0}, True)   # the layer shown may be the job before's
    assert gl.owed(dict(job, layer_num=0), slices) == ({'C3': 6.0, 'B2': 3.0}, True)
    assert gl.owed(dict(job, gcode_state='FINISH'), slices) == ({}, None) and gl.owed(dict(job, gcode_state='IDLE'), slices) == ({}, None)
    assert gl.owed(dict(job, subtask_name='Sent from the phone'), slices) == ({}, False)   # not sliced on this computer: never guessed
    assert gl.owed(dict(job, mapping=[9999, 257]), slices) == ({}, False)                 # a tray that cannot be named
    fg.remember_slice('Benchy', 1, plate)                                                  # sliced again, and its G-code could not be read
    assert gl.owed(job, fg.load_state()['slices']) == ({}, False)

def test_a_job_is_matched_to_the_slice_of_its_own_plate(state):
    """Two plates of one project with as many layers: the printer's file names the plate ("/data/Metadata/plate_1.gcode")."""
    fg.remember_slice('Benchy', 1, {'layers': 3, 'filaments': [{'slot': 1, 'grams': 6.0}, {'slot': 2, 'grams': 3.0}]}, kept({1: [0, 2, 2, 2], 2: [0, 1, 1, 1]}))
    fg.remember_slice('Benchy', 2, {'layers': 3, 'filaments': [{'slot': 1, 'grams': 7.0}, {'slot': 2, 'grams': 3.0}]}, kept({1: [0, 1, 2, 4], 2: [0, 0, 0, 3]}))
    job, slices = dict(H2D_JOB, subtask_name='Benchy_plate A', total_layer_num=3, layer_num=3), fg.load_state()['slices']
    first, second = dict(job, gcode_file='/data/Metadata/plate_1.gcode'), dict(job, gcode_file='/data/Metadata/plate_2.gcode')
    assert gl.owed(first, slices) == ({'C3': 2.0, 'B2': 1.0}, True) and gl.owed(second, slices) == ({'C3': 4.0, 'B2': 3.0}, True)
    assert gl.draws(first, slices, ['B2', 'C3']) == ({'C3': 6.0, 'B2': 3.0}, set())       # and entered amounts are counted down by it
    assert gl.owed(dict(job, gcode_file=''), slices) == ({'C3': 4.0, 'B2': 3.0}, True)     # not said (an A1 mini from SD): the newer slice, as before
    assert gl.owed(dict(job, gcode_file='/data/Metadata/plate_3.gcode'), slices) == ({}, False)   # a plate not sliced here: not guessed
    assert gl.job_plate({'gcode_file': 'Benchy.gcode.3mf'}) is None and gl.job_plate({}) is None

def test_the_running_print_is_taken_off_its_spool_first(state, monkeypatch):
    """2026-11-19: a 9-up plate needing 85.3 g of blue was printing at 0 % from C3, read at 105 g. The same plate, sliced
    again for the next run, read "enough"; after the running print C3 held about 20 g, and blue ran dry."""
    fg.remember_slice('Benchy', 1, BLUE, kept({1: [0.0] + [3.2] * 26 + [2.1]}))
    running(monkeypatch, BLUE_JOB); res = fg.evaluate(BLUE, CFG, 'Benchy'); row = res['rows'][0]
    assert row['status'] == 'SHORT' and 'C3 reads 105 g, of which the running print still needs 85 g' in row['note']
    assert row['trays'][0]['grams'] == pytest.approx(19.7) and row['trays'][0]['reads'] == 105 and res['printer']['counted'] is True
    assert 'RUNNING (Benchy, layer 1 of 27): the running print is counted' in fg.report_text(res)
    running(monkeypatch, dict(BLUE_JOB, gcode_state='FINISH')); res = fg.evaluate(BLUE, CFG, 'Benchy'); row = res['rows'][0]
    assert row['status'] == 'TIGHT' and 'reads' not in row['note'] and row['trays'][0]['grams'] == 105 and res['printer']['counted'] is None
    running(monkeypatch, dict(BLUE_JOB, subtask_name='Sent from the phone')); res = fg.evaluate(BLUE, CFG, 'Benchy')
    assert res['rows'][0]['status'] == 'TIGHT' and res['printer']['counted'] is False and 'the guard could not count that print' in fg.report_text(res)

def test_the_job_is_named_without_a_figure_the_printer_does_not_give():
    p = {'state': 'RUNNING', 'job': 'Benchy', 'layer': None, 'layers': None, 'percent': None}
    assert fg.job_text(p) == ' (Benchy)' and fg.job_text(dict(p, percent=40)) == ' (Benchy, 40 %)' and fg.job_text(dict(p, job='')) == ''
    assert fg.job_text(dict(p, layer=11, layers=27, percent=40)) == ' (Benchy, layer 11 of 27)' and fg.job_text(dict(p, state='PREPARE', layer=27)) == ' (Benchy, preparing)'

def test_an_entered_amount_is_not_reduced_twice(state, monkeypatch):
    """guard_ledger takes the whole job off an entered amount when the job starts: the running print is not taken off it again."""
    gl.enter('SERIAL', {'label': 'C3', 'type': 'PETG', 'colour': '#101010'}, 400)
    fg.remember_slice('Benchy', 1, SLICE, kept({1: [0.0] + [0.5] * 240, 2: [0.0] + [0.125] * 240}))
    watch = gl.JobWatch('SERIAL', CFG); watch.look(dict(H2D_JOB, gcode_state='IDLE', task_id='10')); watch.look(H2D_JOB)   # the ledger counts the job: 120 g
    running(monkeypatch, H2D_JOB)                                                          # slot 1 (120 g) prints from C3, slot 2 (30 g) from B2
    plate = {'printer_model': 'Bambu Lab H2D', 'time': '', 'layers': 10, 'filaments': [
        {'slot': 1, 'type': 'PETG', 'fid': '', 'colour': '#101010', 'name': 'PETG', 'grams': 100.0, 'nozzle': None},
        {'slot': 2, 'type': 'PLA', 'fid': 'GFA00', 'colour': '#C12E1F', 'name': 'PLA Basic', 'grams': 40.0, 'nozzle': None}]}
    petg, pla = fg.evaluate(plate, CFG, 'Other')['rows']
    assert petg['trays'][0]['grams'] == 280 and not petg['trays'][0]['reserved'] and 'running print' not in petg['note']
    assert pla['trays'][0]['grams'] == 51 and 'B2 reads 81 g, of which the running print still needs 30 g' in pla['note']

def test_a_job_the_ledger_has_not_yet_counted_comes_off_an_entered_amount_at_the_check(state, monkeypatch):
    """An entered 150 g spool, a 120 g job preparing (the ledger counts a job once it runs): the next 100 g plate does not fit."""
    fg.remember_slice('Benchy', 1, dict(PETG_PLATE, filaments=[dict(PETG_PLATE['filaments'][0], grams=120.0)]), kept({1: [0.0, 40.0, 40.0, 40.0]}))
    gl.enter('SERIAL', {'label': 'C3', 'type': 'PETG', 'colour': '#101010'}, 150)
    watch = gl.JobWatch('SERIAL', CFG); watch.look(dict(PETG_JOB, gcode_state='IDLE', task_id='10')); watch.look(PETG_JOB)
    running(monkeypatch, PETG_JOB); res = fg.evaluate(PETG_PLATE, CFG, 'Next plate'); row = res['rows'][0]
    assert row['status'] == 'SHORT' and row['trays'][0]['grams'] == 30 and res['printer']['counted']
    assert 'C3 holds 150 g as entered, of which the running print still needs 120 g' in row['note']
    watch.look(dict(PETG_JOB, gcode_state='RUNNING', layer_num=1)); running(monkeypatch, dict(PETG_JOB, gcode_state='RUNNING', layer_num=1))
    assert fg.evaluate(PETG_PLATE, CFG, 'Next plate')['rows'][0]['trays'][0]['grams'] == 30          # counted now by the ledger: once

def test_an_amount_entered_during_a_print_has_the_rest_of_that_print_taken_off(state, monkeypatch):
    fg.remember_slice('Benchy', 1, dict(PETG_PLATE, filaments=[dict(PETG_PLATE['filaments'][0], grams=120.0)]), kept({1: [0.0, 40.0, 40.0, 40.0]}))
    job = dict(PETG_JOB, gcode_state='RUNNING', layer_num=2)                             # layers 2 and 3 to go: 80 g
    gl.enter('SERIAL', {'label': 'C3', 'type': 'PETG', 'colour': '#101010'}, 150)        # weighed now, mid-print
    running(monkeypatch, job); row = fg.evaluate(PETG_PLATE, CFG, 'Next plate')['rows'][0]
    assert row['trays'][0]['grams'] == 70                                                 # before the ledger has looked: the check takes it off
    watch = gl.JobWatch('SERIAL', CFG); watch.look(job)
    assert fg.load_state()['spools']['SERIAL']['C3']['grams'] == 70 and 'what that print still needs of it, 80 g, taken off' in (state / 'guard.log').read_text()
    row = fg.evaluate(PETG_PLATE, CFG, 'Next plate')['rows'][0]
    assert row['trays'][0]['grams'] == 70 and 'running print' not in row['note']          # and not twice
    gl.enter('SERIAL', {'label': 'C3', 'type': 'PETG', 'colour': '#101010'}, None); gl.enter('SERIAL', {'label': 'C3', 'type': 'PETG', 'colour': '#101010'}, 150)
    phone = dict(job, subtask_name='Sent from the phone', task_id='12')                   # first entered during a print not sliced here: its rest is not known
    watch = gl.JobWatch('SERIAL', CFG); watch.look(phone)
    entry = fg.load_state()['spools']['SERIAL']['C3']; assert entry['stale'] and 'missed' not in entry      # so the amount is out of date
    running(monkeypatch, phone); res = fg.evaluate(PETG_PLATE, CFG, 'Next plate')
    assert res['printer']['counted'] is False and res['rows'][0]['status'] == 'UNKNOWN' and 'could not count that print' in fg.report_text(res)

def test_profiles_are_kept_beside_the_state_file_and_go_with_their_slice(state):
    plate = dict(SLICE, layers=3)
    fg.remember_slice('Benchy', 1, plate, {'layers': {1: [0.0, 40.0, 40.0, 40.0], 2: [0.0, 10.0, 10.0, 10.0]}, 'purge': {1: [0.0, 3.5, 0.0, 0.0], 2: [0.0] * 4}})
    entry = fg.load_state()['slices'][0]
    assert set(entry) == {'title', 'plate', 'layers', 'at', 'filaments', 'profile'} and os.listdir(state / 'profiles') == [entry['profile']]
    assert fg.load_profile(entry) == {1: [0.0, 40.0, 40.0, 40.0], 2: [0.0, 10.0, 10.0, 10.0]} and fg.load_profile(entry, 'purge') == {1: [0.0, 3.5, 0.0, 0.0], 2: [0.0] * 4}
    again = dict(plate, filaments=[{'slot': 1, 'grams': 110.0}, {'slot': 2, 'grams': 30.0}])
    for _ in range(2): fg.remember_slice('Benchy', 1, again, kept({1: [0.0, 30.0, 40.0, 40.0], 2: [0.0, 10.0, 10.0, 10.0]}))   # the plate sliced again, twice alike
    slices = fg.load_state()['slices']                                                    # the earlier slice is kept: a job may be printing from it
    assert len(slices) == 2 and slices[1]['profile'] == entry['profile'] != slices[0]['profile'] and sorted(os.listdir(state / 'profiles')) == sorted(s['profile'] for s in slices)
    for n in range(30): fg.remember_slice(f'Other {n}', 1, plate)                         # thirty newer slices: these drop out
    assert os.listdir(state / 'profiles') == []

def test_a_check_marks_the_printer_looked_at_only_when_that_changes_who_is_followed(state, monkeypatch):
    running(monkeypatch, dict(BLUE_JOB, gcode_state='IDLE'))
    fg.evaluate(BLUE, dict(CFG, learn=False), 'Benchy'); assert 'looked' not in fg.load_state()          # learning off: nobody is followed for it
    fg.evaluate(BLUE, CFG, 'Benchy'); first = fg.load_state()['looked']['SERIAL']
    writes = []; save = fg.save_state; monkeypatch.setattr(fg, 'save_state', lambda st: (writes.append(1), save(st)))
    fg.evaluate(BLUE, CFG, 'Benchy')
    assert fg.load_state()['looked']['SERIAL'] == first and writes == []                                   # again within the minute: no write

def test_what_the_running_print_needs_beyond_its_tray_comes_off_the_next_spool_of_it(state, monkeypatch):
    """C3 reads 20 g and the running print still needs 85 g of it: the AMS goes on with C4, the same filament."""
    fg.remember_slice('Benchy', 1, BLUE, kept({1: [0.0] + [3.2] * 26 + [2.1]}))
    blue = lambda tid, g, colour='0A2989FF': {'id': tid, 'tray_type': 'PLA', 'tray_color': colour, 'tray_info_idx': 'GFA00', 'remain_g': g, 'tray_weight': '1000'}
    job = dict(BLUE_JOB, ams={'ams': [{'id': '2', 'tray': [blue('2', 20), blue('3', 120), blue('0', 500, 'C12E1FFF')]}]})   # C3, C4, and C1 (red)
    running(monkeypatch, job); res = fg.evaluate(BLUE, CFG, 'Benchy'); row = res['rows'][0]
    assert row['status'] == 'SHORT' and row['best'] == pytest.approx(54.7) and {t['label']: t['grams'] for t in row['trays']} == pytest.approx({'C3': 0, 'C4': 54.7})
    assert 'C3 reads 20 g; the running print still needs 85 g: 65 g more than it holds, taken off C4' in row['note']
    assert 'C4 reads 120 g, of which the running print takes 65 g once C3 runs out' in row['note'] and res['printer']['beyond'] == {}
    job = dict(BLUE_JOB, ams={'ams': [{'id': '2', 'tray': [blue('2', 20), blue('0', 500, 'C12E1FFF')]}]})   # no other blue
    running(monkeypatch, job); res = fg.evaluate(BLUE, CFG, 'Benchy')
    assert res['printer']['beyond'] == {'C3': pytest.approx(65.3)} and "it needs 65 g more of C3's filament than the loaded spools hold" in fg.report_text(res)
    assert 'C3 reads 20 g; the running print still needs 85 g: 65 g more than it holds, and no other spool of this filament is loaded' in res['rows'][0]['note']

def test_a_plate_sliced_again_while_it_prints_is_counted_by_the_slice_that_needs_most(state, monkeypatch):
    """A nine-up plate "Part" (225 g) prints at layer 10 of 100 from A1, which reads 300 g, and still needs 205 g. The
    plate is sliced again with four copies (100 g, as many layers) for the next run. The job on the printer may be
    either slice: it is counted by the one that needs most, and the four-up does not fit beside it."""
    part(225.0); job = printing('RUNNING', [spool(0, 300)], layer=10, gcode_file='/data/Metadata/plate_1.gcode')
    assert gl.owed(job, fg.load_state()['slices']) == ({'A1': pytest.approx(204.75)}, True)
    part(100.0); slices = fg.load_state()['slices']
    assert [s['filaments'][0]['grams'] for s in slices] == [100.0, 225.0] and len(os.listdir(state / 'profiles')) == 2
    assert gl.owed(job, slices) == ({'A1': pytest.approx(204.75)}, True) and gl.draws(job, slices, ['A1']) == ({'A1': 225.0}, set())
    running(monkeypatch, job); res = fg.evaluate(needing(100), CFG, 'Part'); row = res['rows'][0]
    assert row['status'] == 'SHORT' and 'A1 reads 300 g, of which the running print still needs 205 g' in row['note'] and res['printer']['counted'] is True
    part(100.0, layers=80)                                                               # sliced again at another layer height: not what the printer runs
    assert gl.owed(job, fg.load_state()['slices']) == ({'A1': pytest.approx(204.75)}, True)

def test_an_entered_amount_is_counted_down_by_the_slice_that_needs_most(state):
    """The nine-up is sent; while the printer prepares it, the plate is sliced again with four copies. The entered
    500 g are counted down by the 225 g the job may take, not by the 100 g of the newer slice."""
    part(225.0); spools = [spool(2)]
    gl.enter('SERIAL', {'label': 'A3', 'type': 'PLA', 'colour': '#101010'}, 500)
    watch = gl.JobWatch('SERIAL', CFG); watch.look(printing('IDLE', spools, task='1')); watch.look(printing('PREPARE', spools, mapping=(2,)))
    part(100.0); watch.look(printing('RUNNING', spools, mapping=(2,), layer=1))
    assert fg.load_state()['spools']['SERIAL']['A3']['grams'] == 275

def test_three_slices_of_a_plate_are_kept_and_none_that_the_job_on_the_printer_may_be(state, monkeypatch):
    """Sliced again, a plate keeps its last three slices that differ, each with its profile; an unchanged re-slice
    takes the place of its like. While the printer has a job on it that a slice fits, that slice is not let go: not
    for a fourth slice of its plate, and not for thirty newer slices of other plates. Once the printer is seen without
    the job, it goes like any other."""
    grams = lambda title='Part': [s['filaments'][0]['grams'] for s in fg.load_state()['slices'] if s['title'] == title]
    for g in (225.0, 100.0, 100.0, 125.0, 150.0): part(g)
    assert grams() == [150.0, 125.0, 100.0] and len(os.listdir(state / 'profiles')) == 3
    job = printing('RUNNING', [spool(0, 300)], layer=10, gcode_file='/data/Metadata/plate_1.gcode')
    running(monkeypatch, job); fg.evaluate(needing(100), CFG, 'Part'); assert fg.load_state()['on']['SERIAL'] == {'name': 'Part', 'layers': 100, 'plate': 1}
    part(175.0); part(200.0); assert grams() == [200.0, 175.0, 150.0, 125.0, 100.0]      # any of them may be on the printer
    for n in range(30): part(10.0, title=f'Other {n}')
    slices = fg.load_state()['slices']
    assert len(slices) == 35 and grams() == [200.0, 175.0, 150.0, 125.0, 100.0] and gl.owed(job, slices) == ({'A1': pytest.approx(182.0)}, True)
    running(monkeypatch, dict(job, gcode_state='FINISH')); fg.evaluate(needing(100), CFG, 'Part'); assert 'SERIAL' not in fg.load_state()['on']
    part(10.0, title='One more'); assert grams() == [] and len(fg.load_state()['slices']) == 30 and len(os.listdir(state / 'profiles')) == 30

def test_the_follower_notes_the_job_on_the_printer_too(state):
    """A printer that is followed has its job noted at every look, also when no check reaches it."""
    part(225.0); gl.enter('SERIAL', {'label': 'A3', 'type': 'PLA', 'colour': '#101010'}, 500)
    watch = gl.JobWatch('SERIAL', dict(CFG, learn=False)); watch.see(printing('RUNNING', [spool(2)], mapping=(2,), layer=1))
    assert fg.load_state()['on'] == {'SERIAL': {'name': 'Part', 'layers': 100, 'plate': None}}
    watch.see(printing('FINISH', [spool(2)], mapping=(2,), layer=100)); assert fg.load_state()['on'] == {}

def test_a_job_is_matched_to_the_project_it_is_named_after(state):
    """"Bracket_v2" (300 g) is sliced and sent; then the project "Bracket" (40 g, as many layers, plate 1) is sliced. The
    job's name also starts with "Bracket_", as a plate of that project would be named, but it is the name of a project
    that was sliced: the job is counted by that one, and an entered 500 g go down by 300 g, not by 40 g. When no slice
    of the project a job is named after fits it, it is not counted by another project's."""
    part(300.0, title='Bracket_v2'); part(40.0, title='Bracket'); slices = fg.load_state()['slices']
    assert gl.match_slice('Bracket_v2', 100, slices, 1)['title'] == 'Bracket_v2' and gl.match_slice('Bracket_v2_lid', 100, slices, 1)['title'] == 'Bracket_v2'
    assert gl.match_slice('Bracket_plate A', 100, slices, 1)['title'] == 'Bracket' and gl.match_slice('Bracket', 100, slices)['title'] == 'Bracket'
    job = printing('RUNNING', [spool(0, 400), spool(2)], name='Bracket_v2', mapping=(0,), layer=1, gcode_file='/data/Metadata/plate_1.gcode')
    assert gl.owed(job, slices) == ({'A1': 300.0}, True)
    gl.enter('SERIAL', {'label': 'A3', 'type': 'PLA', 'colour': '#101010'}, 500)
    watch = gl.JobWatch('SERIAL', CFG); watch.look(dict(job, gcode_state='IDLE', task_id='1')); watch.look(dict(job, mapping=[2]))
    assert fg.load_state()['spools']['SERIAL']['A3']['grams'] == 200
    part(40.0, title='Bracket', layers=50)                                               # "Bracket" at 50 layers: it fits a job of 50 layers, "Bracket_v2" does not
    assert gl.match_slice('Bracket_v2', 50, fg.load_state()['slices'], 1) is None and gl.owed(dict(job, total_layer_num=50), fg.load_state()['slices']) == ({}, False)
