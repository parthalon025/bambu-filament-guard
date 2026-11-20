"""python -m pytest test_filament_guard.py   (needs no printer and no Bambu Studio)"""
import io
import pytest
import filament_guard as fg

CFG = dict(fg.DEFAULTS)


def gcode(weights='9.99,10.87', used='1,3', types='PLA;PETG;PLA', colours='#0A2989;#FFFFFF;#C12E1F', ids='GFA00;GFG02;GFA00',
          nozzles='0.4', fmap='1,1,1', complete=True):
    """The head of a Bambu Studio G-code as 02.09.01.70 writes it, cut down to the lines that are read."""
    return '\n'.join([
        '; HEADER_BLOCK_START', '; BambuStudio 02.09.01.70', '; model printing time: 1h 2m 3s; total estimated time: 1h 9m 0s',
        f'; total filament weight [g] : {weights}', '; filament_density: 1.26,1.27,1.26'] + ([f'; filament: {used}'] if used else []) + [
        '; HEADER_BLOCK_END', '', '; CONFIG_BLOCK_START', f'; filament_colour = {colours}', f'; filament_ids = {ids}', f'; filament_map = {fmap}',
        '; filament_settings_id = "Bambu PLA Basic @BBL H2D";"Bambu PETG HF @BBL H2D 0.4 nozzle";"Bambu PLA Basic @BBL H2D"',
        f'; filament_type = {types}', f'; nozzle_diameter = {nozzles}', '; printer_model = Bambu Lab H2D'] + (['; CONFIG_BLOCK_END'] if complete else []) + ['G1 X1'])

def tray(label, grams, colour='#C12E1F', typ='PLA', fid='GFA00', nozzle=None, name='PLA Basic', weight=None):
    return {'label': label, 'type': typ, 'fid': fid, 'colour': colour, 'name': name, 'grams': grams, 'weight': weight, 'nozzle': nozzle}

def fil(grams=50.0, colour='#C12E1F', typ='PLA', fid='GFA00', nozzle=None):
    return {'slot': 2, 'type': typ, 'fid': fid, 'colour': colour, 'name': 'Bambu PLA Basic', 'grams': grams, 'nozzle': nozzle}


def test_totals_belong_to_the_slots_in_use():
    plate = fg.parse_plate(gcode())
    assert [(f['slot'], f['type'], f['colour'], f['grams']) for f in plate['filaments']] == [(1, 'PLA', '#0A2989', 9.99), (3, 'PLA', '#C12E1F', 10.87)]
    assert plate['printer_model'] == 'Bambu Lab H2D' and plate['time'] == '1h 9m 0s'
    assert plate['filaments'][0]['name'] == 'Bambu PLA Basic'

def test_file_still_being_written_is_not_read():
    assert fg.parse_plate(gcode(complete=False)) is None
    assert fg.parse_plate(fg.read_head(io.BytesIO(b'; HEADER_BLOCK_START\n'))) is None

def test_totals_that_cannot_be_placed_are_refused():
    with pytest.raises(ValueError): fg.parse_plate(gcode(used=None))                 # 2 totals, 3 filaments, no slot list
    with pytest.raises(ValueError): fg.parse_plate(gcode(used='1,2,3'))

def test_nozzle_only_on_a_two_nozzle_printer():
    assert [f['nozzle'] for f in fg.parse_plate(gcode(fmap='2,1,1'))['filaments']] == [None, None]
    assert [f['nozzle'] for f in fg.parse_plate(gcode(fmap='2,1,1', nozzles='0.4,0.4'))['filaments']] == ['right', 'left']

def test_enough_tight_short():
    assert fg.assess(fil(50), [tray('B2', 81)], CFG)['status'] == 'OK'
    assert fg.assess(fil(50), [tray('B2', 52)], CFG)['status'] == 'TIGHT'            # holds it, not with the 10 % margin
    row = fg.assess(fil(50), [tray('B2', 33)], CFG)
    assert row['status'] == 'SHORT' and 'short by 17 g' in row['note']
    assert fg.assess(fil(10), [tray('B2', 81)], CFG, times=9)['status'] == 'SHORT'

def test_a_nearly_empty_spool_is_tight_however_small_the_need():
    """2026-11-19: C3, a 1 kg spool the AMS put at 105 g, ran dry after 84-85 g of a print that needed 85.3 g."""
    row = fg.assess(fil(85.3, colour='#0A2989'), [tray('C3', 105, colour='#0A2989', weight=1000)], CFG)
    assert row['status'] == 'TIGHT' and '20 g to spare' in row['note'] and '30 g out' in row['note']
    assert fg.assess(fil(85.3, colour='#0A2989'), [tray('C3', 116, colour='#0A2989', weight=1000)], CFG)['status'] == 'OK'
    assert fg.assess(fil(5), [tray('B2', 30, weight=1000)], CFG)['status'] == 'TIGHT'       # 5 g from a spool that may be 30 g out
    assert fg.assess(fil(400), [tray('B2', 435, weight=1000)], CFG)['status'] == 'TIGHT'    # the 10 % of a large need is the larger reserve

def test_two_spools_of_the_same_filament():
    row = fg.assess(fil(50), [tray('B2', 30), tray('C1', 40)], CFG)
    assert row['status'] == 'SPLIT' and row['best'] == 40 and row['total'] == 70

def test_another_material_never_counts():
    row = fg.assess(fil(50), [tray('A2', 900, typ='PETG', fid='GFG02')], CFG)
    assert row['status'] == 'MISSING' and row['trays'] == []

def test_spool_on_the_other_nozzle_does_not_count():
    row = fg.assess(fil(50, nozzle='right'), [tray('B2', 900, nozzle='left')], CFG)
    assert row['status'] == 'MISSING' and 'other nozzle (B2)' in row['note']
    assert fg.assess(fil(50, nozzle='right'), [tray('Ext', 900, nozzle=None)], CFG)['status'] == 'OK'

def test_similar_colour_of_another_product_is_named_not_counted():
    row = fg.assess(fil(50), [tray('B2', 33), tray('B3', 192, colour='#D02727', fid='GFA06', name='PLA Silk+')], CFG)
    assert row['status'] == 'SHORT' and [t['label'] for t in row['trays']] == ['B2'] and 'B3 PLA Silk+ 192 g' in row['note']

def test_another_colour_never_counts():
    """2026-11-19: with the blue spool empty, blue read "enough" on C1, a terracotta PLA Marble with 708 g."""
    row = fg.assess(fil(85.3, colour='#0A2989'), [tray('C1', 708, colour='#AD4E38', name='PLA Marble'), tray('C2', 538, colour='#FFFFFF')], CFG)
    assert row['status'] == 'COLOUR' and row['trays'] == [] and row['best'] is None
    assert 'nearest is C1 PLA Marble (#AD4E38), 708 g' in row['note']
    res = {'error': None, 'worst': 'COLOUR', 'rows': [row]}
    assert fg.exit_code(res) == 2 and fg.headline(res) == 'A filament of this print is not loaded' and 'COLOUR' in fg.DEFAULTS['popup_on']

def test_spool_without_an_amount_is_unknown_not_empty():
    assert fg.assess(fil(50), [tray('A3', None)], CFG)['status'] == 'UNKNOWN'

def test_grams_left():
    assert fg.grams_left({'remain_g': 81, 'remain': 8, 'tray_weight': '1000'}) == 81
    assert fg.grams_left({'remain': 8, 'tray_weight': '1000'}) == 80                  # older firmware: percent of the spool
    assert fg.grams_left({'remain_g': 0, 'remain': 0, 'tray_weight': '1000'}) == 0    # a tagged spool that is empty
    assert fg.grams_left({'remain_g': -1, 'remain': -1, 'tray_weight': '0'}) is None  # no tag, nothing measured
    assert fg.grams_left({'remain_g': 0, 'remain': 0, 'tray_weight': '0'}) is None    # an external spool holder with nothing known

def test_trays_of_a_two_nozzle_status():
    status = {'device': {'extruder': {'info': [{'id': 0, 'snow': 0x0202}, {'id': 1, 'snow': 0xFEFF}]}},
              'ams': {'ams': [{'id': '1', 'info': '51001E03', 'tray': [{'id': '1', 'tray_type': 'PLA', 'tray_color': 'C12E1FFF', 'remain_g': 81, 'tray_info_idx': 'GFA00'}, {'id': '2'}]},
                              {'id': '2', 'info': '50001E03', 'tray': [{'id': '2', 'tray_type': 'PLA', 'tray_color': '0A2989FF', 'remain_g': 105}]}]},
              'vir_slot': [{'id': '254', 'tray_type': ''}]}
    trays, two = fg.trays_of(status)
    assert two and [(t['label'], t['nozzle'], t['grams'], t['colour']) for t in trays] == [('B2', 'left', 81, '#C12E1F'), ('C3', 'right', 105, '#0A2989')]
    assert [t['weight'] for t in trays] == [None, None]
    status['ams']['ams'][0]['tray'][0]['tray_weight'] = '1000'
    assert fg.trays_of(status)[0][0]['weight'] == 1000

def test_watcher_reports_a_finished_plate_once(tmp_path, monkeypatch):
    meta = tmp_path / 'bamboo_model' / 'Thu_Nov_19' / '01_02_03#1#9' / 'Metadata'; meta.mkdir(parents=True)
    (meta.parent / 'origin.txt').write_text(r'C:\prints\Benchy.3mf')
    monkeypatch.setattr(fg.tempfile, 'gettempdir', lambda: str(tmp_path)); monkeypatch.setattr(fg, 'BS_DATA', str(tmp_path / 'none'))
    watcher = fg.Watcher()
    f = meta / '.1.0.gcode'; f.write_text(gcode())
    assert watcher.new_plates() == []                                               # first sight: it may still be growing
    monkeypatch.setattr(fg.time, 'time', lambda: f.stat().st_mtime + 5)
    found = watcher.new_plates()
    assert [p for p, _ in found] == [str(f)] and fg.project_title(str(f)) == 'Benchy'
    assert fg.studio_pid(str(f)) == 1 and fg.studio_pid('plate_1.gcode') is None      # the card goes to the window of the copy that sliced
    assert watcher.new_plates() == []                                               # and never twice


# ---------------------------------------------------------------- settings, state, entered amounts (guard_ledger)

import json, time
import guard_ledger as gl

@pytest.fixture
def state(tmp_path, monkeypatch):
    """The guard's state and settings in a folder of the test's own."""
    monkeypatch.setattr(fg, 'STATE_DIR', str(tmp_path)); monkeypatch.setattr(fg, 'CONFIG', str(tmp_path / 'config.json'))
    return tmp_path

H2D_JOB = {'gcode_state': 'RUNNING', 'subtask_name': 'Benchy_plate A', 'task_id': '11', 'total_layer_num': 240, 'mapping': [514, 257],
           'ams': {'ams': [{'id': '1', 'tray': [{'id': '1', 'tray_type': 'PLA', 'tray_color': 'C12E1FFF', 'remain_g': 81}]},
                           {'id': '2', 'tray': [{'id': '2', 'tray_type': 'PETG', 'tray_color': '101010FF', 'remain_g': -1, 'remain': -1, 'tray_weight': '0'}]}]}}
SLICE = {'printer_model': 'Bambu Lab H2D', 'time': '', 'layers': 240, 'filaments': [{'slot': 1, 'grams': 120.0}, {'slot': 2, 'grams': 30.0}]}

def c3():
    return next(t for t in gl.apply(fg.trays_of(H2D_JOB)[0], 'SERIAL') if t['label'] == 'C3')

def test_settings_file_holds_only_what_was_changed(state):
    cfg = fg.load_config(); cfg['ok_seconds'] = 0; fg.save_config(cfg)
    assert json.load(open(fg.CONFIG)) == {'ok_seconds': 0} and fg.load_config()['popup_on'] == fg.DEFAULTS['popup_on']

def test_layer_count_is_read():
    assert fg.parse_plate(gcode().replace('; filament_density', '; total layer number: 27\n; filament_density'))['layers'] == 27
    assert fg.parse_plate(gcode())['layers'] is None

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

def test_slice_the_guard_cannot_read_is_reported_once(tmp_path, monkeypatch):
    meta = tmp_path / 'bamboo_model' / 'Thu_Nov_19' / '01_02_03#1#9' / 'Metadata'; meta.mkdir(parents=True)
    monkeypatch.setattr(fg.tempfile, 'gettempdir', lambda: str(tmp_path)); monkeypatch.setattr(fg, 'BS_DATA', str(tmp_path / 'none'))
    watcher = fg.Watcher(); f = meta / '.1.2.gcode'; f.write_text(gcode(used=None))                   # totals that cannot be placed
    watcher.new_plates(); monkeypatch.setattr(fg.time, 'time', lambda: f.stat().st_mtime + 5)
    assert watcher.new_plates() == [] and [p for p, _ in watcher.problems] == [str(f)] and fg.plate_number(str(f)) == 3
    watcher.problems.clear(); assert watcher.new_plates() == [] and watcher.problems == []


# ---------------------------------------------------------------- found by review, 2026-11-19

def test_two_slots_of_one_colour_are_judged_together():
    fils = [dict(fil(80), slot=1), dict(fil(80), slot=2)]; trays = [tray('B2', 100)]
    rows = [fg.assess(f, trays, CFG) for f in fils]
    assert [r['status'] for r in rows] == ['OK', 'OK']                                  # each alone fits the spool
    fg.share_spools(rows, fils, trays, CFG, 1)
    assert [r['status'] for r in rows] == ['SHORT', 'SHORT'] and 'filaments 1, 2 share B2 and need 160 g together: short by 60 g' in rows[0]['note']

def test_state_file_is_never_replaced_by_an_empty_one(state):
    assert fg.load_state() == {}                                                        # no file: an empty state
    fg.update_state(lambda st: st.__setitem__('spools', {'S': {'A1': {'grams': 5}}}))
    (state / 'state.json').write_text('{"spools": {"S"')                                # cut short by a crash
    assert fg.load_state() == {} and (state / 'state.json.damaged').exists()            # set aside, and said in the log

def test_damaged_settings_fall_back_to_the_defaults(state):
    (state / 'config.json').write_text('{"ok_seconds": ')
    assert fg.load_config() == fg.DEFAULTS

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

def test_a_fine_plate_does_not_wipe_another_plates_warning(state):
    class Card:
        beside = None
        def __init__(self): self.shown = []
        def is_open(self): return bool(self.shown)
        def show(self, res, **how): self.shown.append((res['plate_no'], how.get('brief', 0)))
    guard = fg.Guard(dict(CFG), type('Root', (), {'quit': None})(), standing=False); guard.card = Card()
    def answer(plate_no, status):
        return {'title': 'Benchy', 'plate_no': plate_no, 'error': None, 'worst': status, 'rows': [{'slot': 1, 'status': status}]}
    guard.deliver(answer(1, 'SHORT')); guard.deliver(answer(2, 'OK'))                   # "slice all": plate 2 is fine, plate 1 is not
    assert guard.card.shown == [(1, 0)] and guard.last['plate_no'] == 1
    guard.deliver(answer(1, 'OK'))                                                      # plate 1 sliced again, and fine now
    assert guard.card.shown[-1] == (1, CFG['ok_seconds'])
