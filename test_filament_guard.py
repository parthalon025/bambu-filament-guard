"""python -m pytest test_filament_guard.py   (needs no printer and no Bambu Studio)"""
import io
import pytest
import filament_guard as fg

CFG = dict(fg.DEFAULTS)


def gcode(weights='9.99,10.87', used='1,3', types='PLA;PETG;PLA', colours='#0A2989;#FFFFFF;#C12E1F', ids='GFA00;GFG02;GFA00',
          nozzles='0.4', fmap='1,1,1', complete=True, body=('G1 X1',), settings=()):
    """A Bambu Studio G-code as 02.09.01.70 writes it, cut down to the lines that are read."""
    return '\n'.join([
        '; HEADER_BLOCK_START', '; BambuStudio 02.09.01.70', '; model printing time: 1h 2m 3s; total estimated time: 1h 9m 0s',
        f'; total filament weight [g] : {weights}', '; filament_density: 1.26,1.27,1.26', '; filament_diameter: 1.75,1.75,1.75'] + ([f'; filament: {used}'] if used else []) + [
        '; HEADER_BLOCK_END', '', '; CONFIG_BLOCK_START', f'; filament_colour = {colours}', f'; filament_ids = {ids}', f'; filament_map = {fmap}',
        '; filament_settings_id = "Bambu PLA Basic @BBL H2D";"Bambu PETG HF @BBL H2D 0.4 nozzle";"Bambu PLA Basic @BBL H2D"',
        f'; filament_type = {types}', f'; nozzle_diameter = {nozzles}', '; printer_model = Bambu Lab H2D'] + list(settings) + (['; CONFIG_BLOCK_END'] if complete else []) + list(body))

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

import json
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
        def __init__(self): self.shown, self.win = [], None
        def is_open(self): return bool(self.shown)
        def show(self, res, **how): self.shown.append((res['plate_no'], how.get('brief', 0))); self.win = object()
    guard = fg.Guard(dict(CFG), type('Root', (), {'quit': None})(), standing=False); guard.card = Card()
    def answer(plate_no, status):
        return {'title': 'Benchy', 'plate_no': plate_no, 'error': None, 'worst': status, 'rows': [{'slot': 1, 'status': status}]}
    guard.deliver(answer(1, 'SHORT')); guard.deliver(answer(2, 'OK'))                   # "slice all": plate 2 is fine, plate 1 is not
    assert guard.card.shown == [(1, 0)] and guard.last['plate_no'] == 1
    guard.deliver(answer(1, 'OK'))                                                      # plate 1 sliced again, and fine now
    assert guard.card.shown[-1] == (1, CFG['ok_seconds'])


# ---------------------------------------------------------------- the print that is running (1.3.0)

import math, os, threading

K = math.pi * 0.875 ** 2 * 1.26 / 1000                                                  # grams in a mm of 1.75 mm filament at 1.26 g/cm3

def sliced(body, weights, layers=3, **more):
    """A whole G-code with two filaments, slots 1 and 3 (T0 and T2), of 1.26 g/cm3."""
    return gcode(weights=weights, body=body, **more).replace('; filament_density', f'; total layer number: {layers}\n; filament_density')

def profile_of(body, weights, layers=3, why=None, **more):
    text = sliced(body, weights, layers, **more)
    return fg.layer_profile(io.BytesIO(text.encode()), fg.parse_plate(text), why)

def e(grams):
    """The E of a move that extrudes this many grams (of slot 1 or 3)."""
    return f'{grams / K:.4f}'

def kept(layers):
    """A profile as layer_profile gives it, with nothing of it purge."""
    return {'layers': layers, 'purge': {slot: [0.0] * len(grams) for slot, grams in layers.items()}, 'pushed': {slot: [0.0] * len(grams) for slot, grams in layers.items()}}

# Bambu Studio's header total of a filament is its moves along X or Y that extrude, and its purge; measured on 02.09.01.70.
FLUSH_FORM = ['M83', 'T0', 'G1 E30 F300',                         # A1, P1, X1: the load is a move of the filament alone, not counted
              f'G1 X10 Y10 E{e(0.10)}', 'G1 E-.8',                 # the prime line counts, the retraction does not
              'M73 L1', 'G1 E.8', f'G1 X20 Y20 E{e(0.50)}',        # nor does the unretraction
              'M620 S2A', 'T2', 'M620.11 S1 E-10 F600',            # an E on another command is not a move
              '; FLUSH_START', f'G1 E{e(0.30)}', f'G1 E{e(0.05)} F50', '; FLUSH_END',   # the purge is these moves of the filament alone
              'G1 E-2 F1800', 'G1 E2 F300', f'G1 X30 Y30 E{e(0.40)}',
              'M73 L2', f'G1 X1 Y1 E{e(0.25)}',
              'T0', '; FLUSH_START', f'G1 X5 E{e(0.20)}', '; FLUSH_END',                  # a move along X inside a purge is purge
              'M73 L3', f'G1 X2 Y2 E{e(0.20)}']
VFLUSH_FORM = ['T0 H-1', f'G1 X1 Y1 E{e(0.10)}', 'M73 L1', f'G1 X2 Y2 E{e(0.40)}',
               'M620 S2A H-1', 'T2 H-1', 'M73 E51', ';VG1 E4 F997', ';VG1 E4 F498',    # H2, P2S, A2L, X2D, H3C: the printer purges by itself;
               '; VFLUSH_START', f';VG1 E{e(0.30)} F997', f';VG1 E{e(0.10)} F997', '; VFLUSH_END',   # only the comments inside the block count
               f'G1 X3 Y3 E{e(0.30)}', 'M73 L2', f'G1 X4 Y4 E{e(0.30)}',
               'T0 H-1', '; VFLUSH_START', f';VG1 E{e(0.20)} F997', '; VFLUSH_END', f'G1 X5 Y5 E{e(0.10)}', 'M73 L3', f'G1 X6 Y6 E{e(0.20)}']

def test_profile_adds_up_to_the_header_with_the_purge_in_moves():
    p = profile_of(FLUSH_FORM, '1.00,1.00')
    assert p['layers'][1] == pytest.approx([0.10, 0.50, 0.20, 0.20], abs=1e-6) and p['purge'][1] == pytest.approx([0, 0, 0.20, 0], abs=1e-6)
    assert p['layers'][3] == pytest.approx([0, 0.75, 0.25, 0], abs=1e-6) and p['purge'][3] == pytest.approx([0, 0.35, 0, 0], abs=1e-6)
    assert [sum(p['layers'][s]) for s in (1, 3)] == pytest.approx([1.0, 1.0], abs=1e-12)

def test_profile_adds_up_to_the_header_with_the_purge_in_comments():
    p = profile_of(VFLUSH_FORM, '1.00,1.00')
    assert p['layers'][1] == pytest.approx([0.10, 0.40, 0.30, 0.20], abs=1e-6) and p['purge'][1] == pytest.approx([0, 0, 0.20, 0], abs=1e-6)
    assert p['layers'][3] == pytest.approx([0, 0.70, 0.30, 0], abs=1e-6) and p['purge'][3] == pytest.approx([0, 0.40, 0, 0], abs=1e-6)
    assert [sum(p['layers'][s]) for s in (1, 3)] == pytest.approx([1.0, 1.0], abs=1e-12)
    assert profile_of(VFLUSH_FORM[:4], '1.00,1.00') is None                               # ends before its last layer: being rewritten
    resliced = io.BytesIO(sliced(VFLUSH_FORM, '1.00,1.10').encode())
    assert fg.layer_profile(resliced, fg.parse_plate(sliced(VFLUSH_FORM, '1.00,1.00'))) is None   # not the file the plate was read from

def test_of_a_purge_as_much_as_the_nozzle_holds_is_the_filament_pushed_out():
    """Bambu Studio counts that much of each change's purge for the filament leaving the nozzle (nozzle_volume, mm3).
    It shows when a nozzle ends on another filament than it began with: one nozzle-full more for the first, less for the last."""
    held = 107 * 1.26 / 1000                                                              # grams of PLA in a 107 mm3 nozzle
    body = ['T0', f'G1 X1 Y1 E{e(0.5)}', 'M73 L1', f'G1 X2 Y2 E{e(0.5 - held)}',
            'T2', '; FLUSH_START', f'G1 E{e(0.35)}', '; FLUSH_END', f'G1 X3 Y3 E{e(0.40)}', 'M73 L2', f'G1 X4 Y4 E{e(0.25 + held)}']
    p = profile_of(body, '1.00,1.00', layers=2, settings=['; nozzle_volume = 107'])
    assert p['layers'][1] == pytest.approx([0.5, 0.5, 0], abs=1e-6) and p['purge'][1] == pytest.approx([0, held, 0], abs=1e-6)
    assert p['layers'][3] == pytest.approx([0, 0.75 - held, 0.25 + held], abs=1e-6) and p['purge'][3] == pytest.approx([0, 0.35 - held, 0], abs=1e-6)
    assert p['pushed'] == {1: pytest.approx([0, held, 0], abs=1e-6), 3: pytest.approx([0, -held, 0], abs=1e-6)}   # kept apart: slot 3's spool gave all 0.35 g
    assert [sum(p['layers'][s]) for s in (1, 3)] == pytest.approx([1.0, 1.0], abs=1e-12)
    why = []
    assert profile_of(body, '1.00,1.00', layers=2, why=why) is None and why[0].startswith('filament 1 adds up to 0.865 g')   # without the nozzle's volume

def test_a_purge_smaller_than_the_nozzle_is_all_the_filament_pushed_out():
    """Two nozzles, both filaments on the left one (133 mm3); its first filament pushes nothing out."""
    full = 133 * 1.26 / 1000
    body = ['T0 H-1', f'G1 X1 Y1 E{e(0.5)}', 'M73 L1',
            'T2 H-1', '; VFLUSH_START', f';VG1 E{e(0.05)} F997', '; VFLUSH_END', f'G1 X2 Y2 E{e(1 - full)}',   # 40 mm3: all of it slot 1's
            'M73 L2', 'T0 H-1', '; VFLUSH_START', f';VG1 E{e(0.30)} F997', '; VFLUSH_END', f'G1 X3 Y3 E{e(0.15 + full)}']   # 238 mm3: 133 of it slot 3's
    p = profile_of(body, '1.00,1.00', layers=2, nozzles='0.4,0.4', fmap='1,1,1', settings=['; nozzle_volume = 133,148'])
    assert p['layers'][1] == pytest.approx([0.5, 0.05, 0.45], abs=1e-6) and p['purge'][1] == pytest.approx([0, 0.05, 0.30 - full], abs=1e-6)
    assert p['layers'][3] == pytest.approx([0, 1 - full, full], abs=1e-6) and p['purge'][3] == pytest.approx([0, 0, full], abs=1e-6)
    assert p['pushed'] == {1: pytest.approx([0, 0.05, -full], abs=1e-6), 3: pytest.approx([0, -0.05, full], abs=1e-6)}
    assert [sum(p['layers'][s]) for s in (1, 3)] == pytest.approx([1.0, 1.0], abs=1e-12)

def test_profile_counts_only_the_moves_that_extrude():
    body = ['T0', 'M73 L1', 'G1 X1 Y2 ; E99 in a comment', f'G1 X1 E{e(0.01)} ; then E99',
            'T1000', f'G1 X2 E{e(0.02)}', 'T65535', f'G1 X2 E{e(0.03)}',                  # not filaments: slot 1 goes on
            'M82', 'G92 E0', f'G1 X3 E{e(0.04)}', f'G2 X4 Y4 I1 J0 E{e(0.09)}', f'G3 X5 Y5 I1 J0 E{e(0.10)}', 'G92 E0', f'G1 Y6 E{e(0.05)}',
            'G1 X6 E0', 'M83 ; relative again', f'    G1 X7 E{e(0.09)}',                   # absolute: 0.04, 0.05, 0.01 and 0.05; an indented line counts
            'M620.11 S1 E-10', f'M650.11 E{e(5)}', 'T2', f'G1 X8 E{e(0.30)}', 'M73 L2', f'G1 X9 E{e(0.30)}', 'M73 L3', f'G1 X1 E{e(0.40)}']
    p = profile_of(body, '0.30,1.00')['layers']
    assert p[1] == pytest.approx([0, 0.30, 0, 0], abs=1e-6) and p[3] == pytest.approx([0, 0.30, 0.30, 0.40], abs=1e-6)

def test_profile_out_of_tolerance_is_refused_with_the_figures_and_within_it_scaled():
    """max(0.02 g, 0.2 %): Bambu Studio 02.09.01.70 agreed with itself to 0.005 g on every slice measured. Further off,
    its accounting has changed, and the log says by how much."""
    body = ['T0', 'M73 L1', f'G1 X1 Y1 E{e(0.99)}', 'T2', f'G1 X1 Y1 E{e(20.0)}']
    p = profile_of(body, '1.00,20.03', layers=1)                                          # 0.01 g and 0.03 g off: within
    assert p['layers'] == {1: pytest.approx([0, 1.0], abs=1e-12), 3: pytest.approx([0, 20.03], abs=1e-12)}
    why = []
    assert profile_of(body, '1.03,20.05', layers=1, why=why) is None
    assert why == ['filament 1 adds up to 0.990 g, the header says 1.03 g (-0.040 g)', 'filament 3 adds up to 20.000 g, the header says 20.05 g (-0.050 g)']

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

def running(monkeypatch, status):
    """The printer the guard asks: SERIAL, an H2D, reporting status."""
    monkeypatch.setattr(fg, 'bs_conf', lambda: {'access_code': {'SERIAL': 'code'}}); monkeypatch.setattr(fg, 'model_by_serial_prefix', lambda: {'SER': 'Bambu Lab H2D'})
    monkeypatch.setattr(fg, 'printer_ip', lambda *a, **k: 'printer'); monkeypatch.setattr(fg, 'read_status', lambda *a, **k: status)

BLUE = {'printer_model': 'Bambu Lab H2D', 'time': '', 'layers': 27,
        'filaments': [{'slot': 1, 'type': 'PLA', 'fid': 'GFA00', 'colour': '#0A2989', 'name': 'Bambu PLA Basic', 'grams': 85.3, 'nozzle': None}]}
BLUE_JOB = {'gcode_state': 'RUNNING', 'subtask_name': 'Benchy', 'layer_num': 1, 'total_layer_num': 27, 'mc_percent': 0, 'mapping': [514],
            'ams': {'ams': [{'id': '2', 'tray': [{'id': '2', 'tray_type': 'PLA', 'tray_color': '0A2989FF', 'tray_info_idx': 'GFA00', 'remain_g': 105, 'tray_weight': '1000'}]}]}}

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

PETG_PLATE = {'printer_model': 'Bambu Lab H2D', 'time': '', 'layers': 3,
              'filaments': [{'slot': 1, 'type': 'PETG', 'fid': '', 'colour': '#101010', 'name': 'PETG', 'grams': 100.0, 'nozzle': None}]}
PETG_JOB = {'gcode_state': 'PREPARE', 'subtask_name': 'Benchy', 'task_id': '11', 'layer_num': 0, 'total_layer_num': 3, 'mapping': [514],
            'ams': {'ams': [{'id': '2', 'tray': [{'id': '2', 'tray_type': 'PETG', 'tray_color': '101010FF', 'remain_g': -1, 'remain': -1, 'tray_weight': '0'}]}]}}

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
    assert fg.load_state()['spools']['SERIAL']['C3'] == dict(fg.load_state()['spools']['SERIAL']['C3'], grams=150.0, missed=['12', 'Sent from the phone'])
    running(monkeypatch, phone); res = fg.evaluate(PETG_PLATE, CFG, 'Next plate')
    assert res['printer']['counted'] is False and 'could not count that print' in fg.report_text(res)

def test_profiles_are_kept_beside_the_state_file_and_go_with_their_slice(state):
    plate = dict(SLICE, layers=3)
    fg.remember_slice('Benchy', 1, plate, {'layers': {1: [0.0, 40.0, 40.0, 40.0], 2: [0.0, 10.0, 10.0, 10.0]}, 'purge': {1: [0.0, 3.5, 0.0, 0.0], 2: [0.0] * 4}})
    entry = fg.load_state()['slices'][0]
    assert set(entry) == {'title', 'plate', 'layers', 'at', 'filaments', 'profile'} and os.listdir(state / 'profiles') == [entry['profile']]
    assert fg.load_profile(entry) == {1: [0.0, 40.0, 40.0, 40.0], 2: [0.0, 10.0, 10.0, 10.0]} and fg.load_profile(entry, 'purge') == {1: [0.0, 3.5, 0.0, 0.0], 2: [0.0] * 4}
    again = dict(plate, filaments=[{'slot': 1, 'grams': 110.0}, {'slot': 2, 'grams': 30.0}])
    fg.remember_slice('Benchy', 1, again, kept({1: [0.0, 30.0, 40.0, 40.0], 2: [0.0, 10.0, 10.0, 10.0]}))   # the plate sliced again
    slices = fg.load_state()['slices']
    assert len(slices) == 1 and slices[0]['profile'] != entry['profile'] and os.listdir(state / 'profiles') == [slices[0]['profile']]
    for n in range(30): fg.remember_slice(f'Other {n}', 1, plate)                         # thirty newer slices: this one drops out
    assert os.listdir(state / 'profiles') == []


# ---------------------------------------------------------------- the card while the guard works

class Card:
    """The card, as the guard sees it: what it was asked to show, in order."""
    def __init__(self): self.win, self.one_line, self.shown = None, False, []
    def is_open(self): return self.win is not None
    def line(self, text, tone='muted', seconds=0, pid=None, hold=False):
        self.win, self.one_line = object(), True; self.shown.append(('line', text, hold)); return self.win
    def show(self, res, brief=0, beside=None, hold=False): self.win, self.one_line = object(), bool(brief); self.shown.append(('show', brief))
    def note(self, headline, lines, tone='muted', pid=None): self.win, self.one_line = object(), False; self.shown.append(('note', headline))
    def close(self): self.win = None; self.shown.append(('close',))

def guard_with(cfg=CFG):
    guard = fg.Guard(dict(cfg), type('Root', (), {'quit': None})(), standing=False); guard.card = Card()
    return guard

def run_queued(guard):
    while not guard.todo.empty(): guard.todo.get_nowait()()

ENOUGH = {'gcode_state': 'IDLE', 'ams': {'ams': [{'id': '0', 'tray': [{'id': '0', 'tray_type': 'PLA', 'tray_color': '0A2989FF', 'remain_g': 500},
                                                                       {'id': '1', 'tray_type': 'PLA', 'tray_color': 'C12E1FFF', 'remain_g': 500}]}]}}

def slice_file(tmp_path, text):
    meta = tmp_path / 'bamboo_model' / 'Thu_Nov_19' / '01_02_03#1#9' / 'Metadata'; meta.mkdir(parents=True, exist_ok=True)
    (meta.parent / 'origin.txt').write_text(r'C:\prints\Benchy.3mf')
    f = meta / '.1.0.gcode'; f.write_text(text)
    return f

def test_a_reslice_of_the_plate_that_is_printing_is_checked_against_that_print(state, tmp_path, monkeypatch):
    """The check comes before the new slice replaces the one the running print was made from, and before the profile is read."""
    f = slice_file(tmp_path, sliced(['T0', 'M73 L1', f'G1 X1 E{e(2.0)}', 'T2', f'G1 X1 E{e(1.3)}', 'M73 L2', 'M73 L3'], '2.00,1.30'))
    plate = fg.parse_plate(f.read_text())
    fg.remember_slice('Benchy', 1, plate, kept({1: [0.0, 2.0, 2.0, 2.0], 3: [0.0, 1.0, 1.0, 2.0]}))   # the slice the running print was made from
    running(monkeypatch, {'gcode_state': 'RUNNING', 'subtask_name': 'Benchy', 'layer_num': 1, 'total_layer_num': 3, 'mapping': [514, 257],
                          'ams': {'ams': [{'id': '1', 'tray': [{'id': '1', 'tray_type': 'PLA', 'tray_color': 'C12E1FFF', 'remain_g': 81}]},
                                          {'id': '2', 'tray': [{'id': '2', 'tray_type': 'PLA', 'tray_color': '0A2989FF', 'remain_g': 7}]}]}})
    guard, queued, read = guard_with(), [], fg.layer_profile
    monkeypatch.setattr(fg, 'layer_profile', lambda stream, plate, why: (queued.append(guard.todo.qsize()), read(stream, plate, why))[1])
    guard.take_slice(str(f), plate); run_queued(guard)
    assert queued == [3]                                                                    # line, answer, end of line: all on their way before the profile was read
    assert guard.last['printer']['counted'] and 'C3 reads 7 g, of which the running print still needs 6 g' in guard.last['rows'][0]['note']
    fresh = read(io.BytesIO(f.read_bytes()), plate)['layers']                             # and the new slice now has its own
    assert fg.load_profile(fg.load_state()['slices'][0]) == {s: pytest.approx(g, abs=1e-4) for s, g in fresh.items()} and sum(fresh[3]) == pytest.approx(1.3)

def test_a_new_slice_says_at_once_that_it_is_being_checked(state, tmp_path, monkeypatch):
    f = slice_file(tmp_path, gcode()); plate = fg.parse_plate(f.read_text()); running(monkeypatch, ENOUGH)
    guard = guard_with(); guard.take_slice(str(f), plate); run_queued(guard)
    assert guard.card.shown == [('line', 'Checking the spools on the Bambu Lab H2D…', True), ('show', CFG['ok_seconds'])]   # the answer takes its place
    guard.card.note('Self-test: the guard works', []); guard.take_slice(str(f), plate); run_queued(guard)
    assert [kind for kind, *_ in guard.card.shown[2:]] == ['note', 'show']                # a full card that stands is not replaced by the line
    guard = guard_with(dict(CFG, ok_seconds=0)); guard.take_slice(str(f), plate); run_queued(guard)
    assert guard.card.shown == [] and not guard.busy                                       # quiet was asked for: neither the line nor the answer

def test_asking_again_while_a_check_runs_starts_no_second_one(state, tmp_path, monkeypatch):
    """2026-11-20: five clicks on the icon in one second made five checks, with nothing on screen while they ran."""
    f = slice_file(tmp_path, gcode()); monkeypatch.setattr(fg, 'newest_gcode', lambda pid=None: str(f)); running(monkeypatch, ENOUGH)
    release, calls, check = threading.Event(), [], fg.evaluate
    monkeypatch.setattr(fg, 'evaluate', lambda plate, cfg, title='', times=1, seen=None: (calls.append(title), release.wait(5), check(plate, cfg, title, times, seen))[2])
    guard = guard_with()
    for _ in range(5): guard.check_newest()
    release.set(); guard.todo.get(timeout=5)()
    assert len(calls) == 1 and (state / 'guard.log').read_text().count('checked on request') == 1
    assert guard.card.shown == [('line', 'Checking the spools on the Bambu Lab H2D…', False), ('show', 0)] and not guard.busy

def test_the_checking_line_never_stays(state, tmp_path, monkeypatch):
    f = slice_file(tmp_path, gcode()); monkeypatch.setattr(fg, 'newest_gcode', lambda pid=None: str(f)); running(monkeypatch, ENOUGH)
    monkeypatch.setattr(fg, 'evaluate', lambda *a, **k: 1 / 0)
    guard = guard_with(); guard.check_newest(); guard.todo.get(timeout=5)()               # the check failed
    assert guard.card.shown == [('line', 'Checking the spools on the Bambu Lab H2D…', False), ('close',)] and not guard.busy
    f.write_text(gcode(weights='0,0'))                                                    # a plate that needs nothing: no answer
    guard = guard_with(); guard.check_newest(); guard.todo.get(timeout=5)()
    assert guard.card.shown[-1] == ('close',) and not guard.busy

def test_the_guard_says_once_that_it_is_running(state, monkeypatch):
    import guard_ui
    windows = {}; monkeypatch.setattr(guard_ui, 'studio_windows', lambda *a: windows)
    guard = guard_with(); guard.greet()
    assert guard.card.shown == []                                                          # Bambu Studio not open: later
    windows[1234] = 99; guard.greet(); guard.card.close(); guard.greet()
    assert guard.card.shown == [('line', f'Bambu Filament Guard {fg.VERSION} is watching for sliced plates', True), ('close',)]   # held while minimised
    quiet = guard_with(dict(CFG, ok_seconds=0)); quiet.greet()
    assert quiet.card.shown == []

def test_setup_starts_the_guard_without_its_start_line(monkeypatch):
    """Setup shows a card of its own in the corner where the start line would stand."""
    started = []
    monkeypatch.setattr(fg, 'is_running', lambda: False); monkeypatch.setattr(fg.subprocess, 'Popen', lambda cmd, **how: started.append(cmd[2:]))
    fg.start(); fg.start(start_line=False)
    assert started == [['watch'], ['watch', '--no-start-line']]

def test_a_card_held_while_bambu_studio_is_minimised_counts_its_seconds_from_when_it_shows(monkeypatch):
    import guard_ui
    class Win:
        def __init__(self): self.timers = []
        def after(self, ms, then): self.timers.append(ms)
        def update_idletasks(self): pass
        def winfo_exists(self): return True
    minimised = [True]
    monkeypatch.setattr(guard_ui, 'u32', type('User32', (), {'IsIconic': staticmethod(lambda hwnd: minimised[0])})())
    monkeypatch.setattr(guard_ui, 'visible', lambda hwnd: True)
    def opened(card, pid=None, beside=None, hold=False):
        card.win, card.owner, card.held, card.closes_after = Win(), 1234, False, 0; return card.win
    monkeypatch.setattr(guard_ui.Card, '_open', opened); monkeypatch.setattr(guard_ui.Card, '_label', lambda *a, **k: None)
    monkeypatch.setattr(guard_ui.Card, '_attach', lambda card: card._shown())             # on screen: no window here to move
    card = guard_ui.Card(None)
    win = card.line(f'Bambu Filament Guard {fg.VERSION} is watching for sliced plates', 'ok', 8, hold=True)
    card.follow()
    assert card.held and win.timers == []                                                  # held, and not counting
    minimised[0] = False; card.follow(); card._shown()
    assert not card.held and win.timers == [8000]                                          # shown: its 8 seconds start, once

def test_a_plate_that_cannot_be_read_at_send_print_job_leaves_the_others_checked(state, tmp_path, monkeypatch):
    import guard_ui
    good = slice_file(tmp_path, gcode()); bad = good.with_name('.1.1.gcode'); bad.write_text(gcode(used=None))   # its totals cannot be placed
    monkeypatch.setattr(fg, 'newest_gcode', lambda pid=None: str(bad)); monkeypatch.setattr(fg, 'temp_gcodes', lambda: [str(bad), str(good)])
    monkeypatch.setattr(guard_ui, 'visible', lambda hwnd: True); running(monkeypatch, ENOUGH)
    guard = guard_with(); guard.check_newest(1, beside=4321); guard.todo.get(timeout=5)()
    log = (state / 'guard.log').read_text()
    assert guard.last['plate_no'] == 1 and log.count('at "Send print job"') == 1 and f'cannot read the slice {bad}: ValueError' in log

def test_a_plate_that_fails_does_not_cost_the_others_their_check(state, monkeypatch):
    guard, taken = guard_with(), []
    class Watcher:
        problems = []
        def new_plates(self): guard.stopping.set(); return [('a', {}), ('b', {})]
    def take(path, plate):
        taken.append(path)
        if path == 'a': raise ValueError('a broken plate')
    monkeypatch.setattr(fg, 'Watcher', Watcher); guard.take_slice = take
    guard.watch_slices()
    assert taken == ['a', 'b'] and 'error: ValueError: a broken plate' in (state / 'guard.log').read_text()


# ---------------------------------------------------------------- learning from previous prints, tray by tray (guard_learn)

import random, time
import guard_learn as gn

TAG = 'A1B2C3D4E5F60718293A4B5C6D7E8F90'

def h2d(state, layer, c3, task='31'):
    """The H2D's status during a job of Benchy: slot 1 from C3 (AMS C, tray 3), which reads c3 g; None: C3 is empty."""
    tray = ({'id': '2', 'tray_type': 'PLA', 'tray_color': '0A2989FF', 'tray_info_idx': 'GFA00', 'remain_g': c3, 'remain': int(c3 / 10), 'tray_weight': '1000',
             'tray_uuid': TAG, 'tag_uid': '0000000000000000'} if c3 is not None else {'id': '2', 'state': 0})
    return {'gcode_state': state, 'subtask_name': 'Benchy', 'task_id': task, 'layer_num': layer, 'total_layer_num': 10, 'mapping': [514], 'ams': {'ams': [{'id': '2', 'tray': [tray]}]}}

def benchy():
    """The slice Benchy is printed from: 1 g in layer 0, 4 g in each of its 10 layers, all from C3."""
    fg.remember_slice('Benchy', 1, {'layers': 10, 'filaments': [{'slot': 1, 'grams': 41.0}]}, kept({1: [1.0] + [4.0] * 10}))

def run_job(rec, before=500.0, lost=2.0, per_layer=4.0, end='FINISH', fault=None, t=0.0, task='31'):
    """Feeds the recorder one print of Benchy, a look every 3 seconds: PREPARE (showing the job before's layer), RUNNING
    from layer 0 to 10, and the end state three times, 30 s apart. C3 reads `before`, `lost` less at layer 1, then
    per_layer less at each layer. fault(state, layer, status, t) -> (status, t) may change any look."""
    g, looks = before, [('PREPARE', 27, before), ('RUNNING', 0, before)]
    g -= lost
    for n in range(1, 11): looks.append(('RUNNING', n, g)); g -= per_layer
    looks += [(end, 10, g)] * 3
    for state, layer, reading in looks:
        t += 30 if state == end else 3
        status = h2d(state, layer, reading, task)
        if fault: status, t = fault(state, layer, status, t)
        rec.look(status, t)
    return t

def tray_c3(status):
    return status['ams']['ams'][0]['tray'][0]

def from_layer(n, change):
    """A fault: change(status, t) -> (status, t) at every RUNNING look from layer n on."""
    return lambda state, layer, status, t: change(status, t) if state == 'RUNNING' and layer >= n else (status, t)

def test_a_whole_print_is_recorded_tray_by_tray(state):
    benchy(); rec = gn.Recorder('SERIAL'); run_job(rec)
    job = gn.load()['SERIAL']['C3']['jobs'][0]
    assert job['sound'] and job['whole'] and job['why'] == [] and job['spool']['id'] == TAG
    assert (job['before'], job['start'], job['end_first'], job['end']) == (500, 498, 458, 458)                   # before: at PREPARE, not the job before's layer
    assert job['samples'] == [[n, 498 - 4 * (n - 1)] for n in range(1, 11)]                                      # the start of each layer
    assert job['sliced'] == {'from': 1, 'to': None, 'printed': pytest.approx(40.0), 'purge': 0.0} and job['layer0'] == pytest.approx(1.0)

def test_the_end_reading_is_the_one_a_minute_after_the_print_finished(state):
    """The end routine unloads the filament, and the AMS may correct its figure then: both readings are kept."""
    benchy(); rec, seen = gn.Recorder('SERIAL'), []
    def settles(state, layer, status, t):
        if state == 'FINISH': seen.append(t); tray_c3(status)['remain_g'] -= 2 * (len(seen) > 1)
        return status, t
    run_job(rec, fault=settles)
    job = gn.load()['SERIAL']['C3']['jobs'][0]
    assert (job['end_first'], job['end']) == (458, 456) and seen[-1] - seen[0] >= gn.SETTLE

def test_at_most_sixty_readings_are_kept_per_tray_and_print_evenly(state):
    fg.remember_slice('Benchy', 1, {'layers': 150, 'filaments': [{'slot': 1, 'grams': 150.0}]}, kept({1: [0.0] + [1.0] * 150}))
    rec, t = gn.Recorder('SERIAL'), 0.0
    for n in range(0, 151): t += 3; rec.look(dict(h2d('RUNNING', n, 900.0 - n), total_layer_num=150), t)
    for k in range(3): t += 30; rec.look(dict(h2d('FINISH', 150, 750.0), total_layer_num=150), t)
    job = gn.load()['SERIAL']['C3']['jobs'][0]
    assert len(job['samples']) <= gn.SAMPLES and [n for n, _ in job['samples']] == list(range(1, 151, 4)) and job['whole']

@pytest.mark.parametrize('fault, end, why', [
    (from_layer(5, lambda s, t: (tray_c3(s).update(tray_uuid='FFFF' + TAG[4:]) or s, t)), 'FINISH', 'C3 holds another spool'),
    (from_layer(5, lambda s, t: (tray_c3(s).update(remain_g=tray_c3(s)['remain_g'] + 20) or s, t)), 'FINISH', 'C3 read 486 g, then 502 g'),
    (lambda st, n, s, t: (s, t + gl.GAP + 1 if (st, n) == ('RUNNING', 5) else t), 'FINISH', 'not followed for 10 minutes'),
    (from_layer(5, lambda s, t: (tray_c3(s).update(remain_g=0, remain=0) or s, t)), 'FINISH', 'C3 read 0 g (it read 486 g before)'),
    (None, 'FAILED', 'failed or cancelled'),
    (None, 'IDLE', 'it stopped without finishing (IDLE)'),
    (from_layer(5, lambda s, t: (dict(s, mapping=[514, 257]), t)), 'FINISH', 'its trays changed at layer 5'),
    (from_layer(5, lambda s, t: (dict(s, task_id='32'), t)), 'FINISH', 'the printer showed another job before this one was seen to finish')])
def test_a_record_that_is_not_sound_is_kept_and_marked(state, fault, end, why):
    benchy(); rec = gn.Recorder('SERIAL'); run_job(rec, fault=fault, end=end)
    job = gn.load()['SERIAL']['C3']['jobs'][0]
    assert not job['sound'] and why in job['why']
    assert all(not m[x]['used'] for x in ('rate', 'pre') for m in [gn.model({'jobs': [job] * 10})])           # and nothing is learned from it

def test_a_spool_that_runs_dry_is_recorded_and_one_taken_out_half_full_is_not(state):
    benchy(); rec = gn.Recorder('SERIAL')
    gone_at_7 = from_layer(6, lambda s, t: (h2d(s['gcode_state'], s['layer_num'], None if s['layer_num'] >= 7 else 20.0), t))
    run_job(rec, fault=gone_at_7)
    [dry] = gn.load()['SERIAL']['C3']['dry']
    assert (dry['reading'], dry['layer'], dry['job'], dry['spool']['id']) == (20.0, 7, 'Benchy', TAG) and dry['needed'] == pytest.approx(16.0)   # layers 7 to 10
    assert 'C3 ran dry' in gn.load()['SERIAL']['C3']['jobs'][0]['why']
    gn.forget(); rec = gn.Recorder('SERIAL')
    run_job(rec, fault=from_layer(6, lambda s, t: (h2d(s['gcode_state'], s['layer_num'], None if s['layer_num'] >= 7 else 300.0), t)))
    assert 'dry' not in gn.load()['SERIAL']['C3'] and 'a spool reading 300 g was taken out during "Benchy"' in (state / 'guard.log').read_text()
    gn.forget(); rec = gn.Recorder('SERIAL')
    low_then_left_out = from_layer(6, lambda s, t: (h2d(s['gcode_state'], s['layer_num'], 20.0) if s['layer_num'] == 6 else dict(s, ams={'ams': []}), t))
    run_job(rec, fault=low_then_left_out)                                                                  # a report without the AMS: not an empty tray
    assert 'dry' not in gn.load()['SERIAL']['C3']

def whole(start, end, printed=100.0, purge=0.0, before=None, layer0=1.0, sound=True):
    return {'sound': sound, 'whole': True, 'start': start, 'end': end, 'before': before, 'layer0': layer0, 'sliced': {'from': 1, 'to': None, 'printed': printed, 'purge': purge}}

def test_each_number_is_used_only_past_its_threshold():
    at = time.mktime((2026, 11, 19, 14, 0, 0, 0, 0, -1))
    assert not gn.model({})['dry']['used'] and gn.model({'dry': [{'at': at, 'reading': 20.0}]})['dry']['value'] == 20.0         # dry: from the first
    assert gn.model({'dry': [{'at': at, 'reading': r} for r in (30.0, 10.0, 25.0, 15.0)]})['dry']['value'] == 25.0              # the largest of the last 3
    rate = lambda jobs: gn.model({'jobs': jobs})['rate']
    assert not rate([whole(500, 320, 200)] * 2)['used'] and rate([whole(500, 320, 200)] * 2)['why'] == '2 of 3 prints'
    assert rate([whole(500, 419, 90)] * 3)['why'] == '270 of 300 g'
    assert not rate([whole(500, 350.5, 150)] * 3)['used']                                                                       # 0.997: noise
    assert rate([whole(500, 365, 150)] * 3)['used'] and rate([whole(500, 365, 150)] * 3)['value'] == pytest.approx(0.9)
    assert not rate([whole(500, 365, 150)] * 2 + [whole(500, 365, 150, sound=False)])['used']                                   # unsound jobs do not count
    assert rate([whole(500, 365, 150)] * 3 + [whole(500, 499, 4)])['jobs'] == 3                                                 # nor jobs of less than 5 g
    pre = lambda jobs: gn.model({'jobs': jobs})['pre']
    assert not pre([whole(498, 458, before=501)])['used'] and pre([whole(498, 458, before=501)])['why'] == '1 of 2 prints'
    assert pre([whole(498, 458, before=501), whole(498, 458, before=500)])['used'] and pre([whole(498, 458, before=501), whole(498, 458, before=500)])['value'] == 2.0
    assert pre([whole(498, 458, before=498)] * 2)['value'] == 0.0                                                               # never below 0

def credited(grams, history, weight=1000):
    return gn.corrections('S', [tray('C3', grams, weight=weight)], {'S': {'C3': history}}).get('C3', {}).get('credited', grams)

def test_a_tray_is_credited_with_the_smallest_reading_its_history_allows():
    at = time.mktime((2026, 11, 19, 14, 0, 0, 0, 0, -1))
    dry, slow = {'dry': [{'at': at, 'reading': 20.0}]}, {'jobs': [whole(500, 365, 150)] * 3}                                  # rate 0.9
    assert credited(105, {}) == 105 and credited(105, dry) == 85 and credited(10, dry) == 0
    assert credited(500, slow) == pytest.approx(1000 - 500 / 0.9) and credited(500, slow, weight=None) == 500                   # W - (W - G) / rate
    assert credited(980, dict(dry, **slow)) == pytest.approx(960)                                                              # the smaller of the two
    fixed = gn.corrections('S', [tray('C3', 105, weight=1000)], {'S': {'C3': dry}})['C3']
    assert fixed['evidence'] == 'it read 20 g when a spool ran dry in it on 19 Nov'
    entered = dict(tray('C3', 105, weight=1000), entered=1.0)
    assert gn.corrections('S', [entered], {'S': {'C3': dry}}) == {}                                                            # an amount entered by hand: never

def test_after_a_run_dry_at_20_g_a_spool_reading_105_g_is_counted_as_85_g(state, monkeypatch):
    """The logged case: C3 read 105 g and ran dry after 84-85 g, so it read about 20 g when it was empty."""
    gn.keep('SERIAL', 'C3', 'dry', {'at': time.mktime((2026, 11, 19, 14, 0, 0, 0, 0, -1)), 'job': 'Benchy', 'reading': 20.0, 'layer': 9, 'needed': 1.0, 'spool': {}}, gn.KEEP_DRY)
    running(monkeypatch, dict(BLUE_JOB, gcode_state='IDLE')); row = fg.evaluate(BLUE, CFG, 'Benchy')['rows'][0]
    assert row['status'] == 'SHORT' and (row['trays'][0]['grams'], row['trays'][0]['reads'], row['trays'][0]['credited']) == (85, 105, 85)
    assert 'C3 reads 105 g, counted as 85 g: it read 20 g when a spool ran dry in it on 19 Nov' in row['note']
    assert fg.load_state()['looked']['SERIAL'] == pytest.approx(time.time(), abs=60)                                          # this printer is followed for learning now
    assert fg.evaluate(BLUE, dict(CFG, learn=False), 'Benchy')['rows'][0]['status'] == 'TIGHT'                                # learning off: as before
    fg.remember_slice('Benchy', 1, BLUE, kept({1: [0.0] + [1.5] * 26 + [1.0]}))                                                # 40 g still to print
    running(monkeypatch, BLUE_JOB); row = fg.evaluate(BLUE, CFG, 'Benchy')['rows'][0]
    assert 'C3 reads 105 g, counted as 85 g: it read 20 g when a spool ran dry in it on 19 Nov; the running print still needs 40 g of it' in row['note']
    assert row['trays'][0]['grams'] == pytest.approx(45.0)

def test_the_loss_before_the_first_layer_is_added_to_the_need(state, monkeypatch):
    for before in (503.0, 504.0): gn.keep('SERIAL', 'C3', 'jobs', whole(500, 400, before=before), gn.KEEP_JOBS)               # lost 2 and 3 g beyond layer 0
    running(monkeypatch, dict(BLUE_JOB, gcode_state='IDLE')); row = fg.evaluate(BLUE, CFG, 'Benchy', times=2)['rows'][0]
    assert row['need'] == pytest.approx(2 * 85.3 + 2 * 3.0) and 'C3 lost up to 3.0 g before the first layer of its last 2 prints, which is added to the need' in row['note']

def random_history(rnd):
    jobs = [{'sound': rnd.random() < 0.8, 'whole': rnd.random() < 0.8, 'start': rnd.uniform(0, 1000), 'end': rnd.uniform(0, 1000), 'before': rnd.choice([None, rnd.uniform(0, 1000)]),
             'layer0': rnd.uniform(0, 5), 'sliced': {'from': 1, 'to': None, 'printed': rnd.uniform(0, 300), 'purge': rnd.uniform(0, 30)}} for _ in range(rnd.randint(0, 12))]
    return {'jobs': jobs, 'dry': [{'at': 0.0, 'reading': rnd.uniform(0, 150)} for _ in range(rnd.randint(0, 4))]}

def test_a_tray_counted_lower_does_not_shrink_the_reserve():
    """The reserve is a share of the fullest spool's weight: learning that makes a small spool the fullest keeps the
    larger reserve."""
    trays = [tray('A1', 120, weight=1000), tray('A2', 110, weight=250)]
    learned = {'A1': {'credited': 90.0, 'evidence': 'it read 30 g when a spool ran dry in it on 19 Nov'}}
    plain, strict = fg.assess(fil(100), fg.credit([dict(t) for t in trays], {}), CFG), fg.assess(fil(100), fg.credit([dict(t) for t in trays], {}, learned), CFG)
    assert plain['status'] == strict['status'] == 'TIGHT'                                   # 110 g is not 100 g and a 1 kg spool's 30 g
    trays = [tray('A1', 100, weight=1000), tray('A2', 100, weight=250)]                     # a tie: without learning A1 comes first
    learned = {'A1': {'credited': 60.0, 'evidence': 'it read 40 g when a spool ran dry in it on 19 Nov'}}
    plain, strict = fg.assess(fil(85), fg.credit([dict(t) for t in trays], {}), CFG), fg.assess(fil(85), fg.credit([dict(t) for t in trays], {}, learned), CFG)
    assert plain['status'] == strict['status'] == 'TIGHT'

def test_learning_can_only_make_a_check_stricter():
    """Over random histories: what a tray is counted with only falls, a need only rises, a result only gets worse; with
    no history every tray and every result is what it was without learning."""
    rnd = random.Random(1889)
    for _ in range(1500):
        tie = rnd.randint(0, 300)                                                          # whole grams, and ties: the AMS reads in whole grams
        trays = [tray(label, rnd.choice([None, tie, tie, rnd.randint(0, 600)]), weight=rnd.choice([None, 250, 1000])) for label in rnd.sample(['A1', 'A2', 'B1', 'B2'], rnd.randint(1, 3))]
        for t in trays[1:]:
            if rnd.random() < 0.2: t.update(entered=1.0, accounted=rnd.choice([None, 'J']))  # an amount entered by hand, the running job counted on it or not
        owed = {t['label']: rnd.choice([0.0, 0.0, float(rnd.randint(0, 200))]) for t in trays}
        history = {'S': {t['label']: random_history(rnd) if rnd.random() < 0.5 else {} for t in trays}}   # some trays learned of, some not
        plain = fg.credit([dict(t) for t in trays], owed, running='J')
        learned = fg.credit([dict(t) for t in trays], owed, gn.corrections('S', trays, history), 'J')
        assert fg.credit([dict(t) for t in trays], owed, gn.corrections('S', trays, {}), 'J') == plain
        for p, q in zip(plain, learned): assert (p['grams'] is None and q['grams'] is None) or 0 <= q['grams'] <= p['grams']
        need = fil(max(1.0, rnd.choice([rnd.uniform(1, 400), max(t['grams'] or 0 for t in trays) - rnd.uniform(0, 40)])))   # often just under the fullest: where a reserve decides
        for times in (1, 3):
            a, b = fg.assess(need, plain, CFG, times), fg.assess(need, learned, CFG, times)
            assert fg.SEVERITY[b['status']] >= fg.SEVERITY[a['status']] and b['need'] >= a['need']

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

def test_learned_says_what_was_learned_and_forget_empties_it(state, monkeypatch, capsys):
    def no_studio(): raise OSError('no Bambu Studio here')
    monkeypatch.setattr(fg, 'bs_conf', no_studio)
    benchy(); rec = gn.Recorder('SERIAL'); run_job(rec)
    gn.keep('SERIAL', 'C3', 'dry', {'at': time.mktime((2026, 11, 19, 14, 0, 0, 0, 0, -1)), 'job': 'Benchy', 'reading': 20.0, 'layer': 9, 'needed': 1.0, 'spool': {}}, gn.KEEP_DRY)
    assert fg.main(['learned']) == 0
    out = capsys.readouterr().out
    assert out.startswith('Learning from prints is on\nSERIAL\n  C3: 1 print recorded, 1 sound; 1 run-dry\n')
    assert 'dry   20.0 g' in out and 'used: it read 20 g when a spool ran dry in it on 19 Nov' in out and 'rate  1.000 g per gram' in out
    assert 'not used: 1 of 3 prints, 40 of 300 g' in out and 'pre   1.0 g' in out and 'not used: 1 of 2 prints' in out
    assert 'ran dry 19 Nov 14:00 at layer 9 of "Benchy", reading 20 g, 1.0 g still to go' in out and 'per gram printed / purged: not enough to tell' in out
    assert fg.main(['learned', '--forget']) == 0 and gn.load() == {}

def test_printed_and_purged_are_told_apart_only_when_the_jobs_differ_enough():
    jobs = [whole(500, 500 - (0.9 * p + 0.5 * q), p, q) for p, q in ((100, 5), (100, 30), (80, 40))]
    assert gn.printed_and_purged(jobs) == pytest.approx((0.9, 0.5)) and gn.printed_and_purged(jobs[:2]) is None
    assert gn.printed_and_purged([whole(500, 400, 100, 5)] * 3) is None                                                         # one purge share: cannot tell

def test_the_newest_slice_that_cannot_be_read_says_so_on_a_card(state, tmp_path, monkeypatch):
    f = slice_file(tmp_path, gcode(used=None)); monkeypatch.setattr(fg, 'newest_gcode', lambda pid=None: str(f)); running(monkeypatch, ENOUGH)
    guard = guard_with(); guard.check_newest(); guard.todo.get(timeout=5)()
    assert guard.card.shown == [('line', 'Checking the spools…', False), ('note', 'Filament could not be checked')] and not guard.busy

def test_the_self_test_says_whether_learning_is_on_and_what_it_holds(state, monkeypatch):
    monkeypatch.setattr(fg, 'bs_conf', lambda: {'app': {'version': '02.09.01.70'}}); monkeypatch.setattr(fg, 'newest_gcode', lambda pid=None: None)
    benchy(); run_job(gn.Recorder('SERIAL'))
    assert (True, 'Learning from prints is on: 1 tray record(s) of past prints, 1 sound ("learned" says more)') in fg.selftest(CFG)
    assert (None, 'Learning from prints is off: 1 tray record(s) of past prints, 1 sound ("learned" says more)') in fg.selftest(dict(CFG, learn=False))


# ---------------------------------------------------------------- found by the pre-merge review (stage 4)

import subprocess, sys, threading as _threading

RACE = '''
import sys, time, json
sys.path.insert(0, sys.argv[1]); import filament_guard as fg
fg.STATE_DIR = sys.argv[2]
while time.time() < float(sys.argv[3]): pass
for i in range(int(sys.argv[4])): fg.update_state(lambda st: st.__setitem__(sys.argv[5], st.get(sys.argv[5], 0) + 1))
'''

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

def test_a_low_spool_taken_out_is_not_a_run_dry(state):
    """A 1 kg spool taken out at 79 g, a tray the print is not drawing on that empties, a tray empty in one look only,
    and a print not sliced here: none of them teaches what the tray reads when its spool runs dry."""
    benchy()
    for fault, why in ((from_layer(6, lambda s, t: (h2d(s['gcode_state'], s['layer_num'], None if s['layer_num'] >= 7 else 79.0), t)), 'a spool reading 79 g was taken out'),
                       (from_layer(6, lambda s, t: (h2d(s['gcode_state'], s['layer_num'], 20.0 if s['layer_num'] == 6 else None if s['layer_num'] == 7 else 480.0), t)), None)):
        gn.forget(); run_job(gn.Recorder('SERIAL'), fault=fault)
        assert 'dry' not in gn.load()['SERIAL']['C3'] and (why is None or why in (state / 'guard.log').read_text())
    fg.remember_slice('Benchy', 1, {'layers': 10, 'filaments': [{'slot': 1, 'grams': 41.0}]}, kept({1: [1.0] + [4.0] * 5 + [0.0] * 5}))   # C3 gives nothing after layer 5
    gn.forget(); run_job(gn.Recorder('SERIAL'), fault=from_layer(8, lambda s, t: (h2d(s['gcode_state'], s['layer_num'], 20.0 if s['layer_num'] == 8 else None), t)))
    assert 'dry' not in gn.load()['SERIAL']['C3'] and 'where the print was not drawing on it' in (state / 'guard.log').read_text()
    gn.forget(); rec, t = gn.Recorder('SERIAL'), 0.0
    for st, n, g in [('RUNNING', 5, 30.0), ('RUNNING', 6, 26.0), ('RUNNING', 7, None), ('PAUSE', 7, None)]:   # not sliced here, followed from its middle
        t += 3; rec.look(dict(h2d(st, n, g), subtask_name='Sent from the phone'), t)
    assert gn.load() == {} and 'C3 emptied during "Sent from the phone", a print not sliced on this computer' in (state / 'guard.log').read_text()

def test_a_run_dry_keeps_the_printers_codes_and_is_dropped_when_a_later_print_contradicts_it(state, monkeypatch, capsys):
    benchy()
    dry_at_7 = from_layer(6, lambda s, t: (dict(h2d(s['gcode_state'], s['layer_num'], 20.0 if s['layer_num'] == 6 else None), print_error=50348044, hms=[{'attr': 1, 'code': 2}]), t))
    run_job(gn.Recorder('SERIAL'), fault=dry_at_7)
    [dry] = gn.load()['SERIAL']['C3']['dry']
    assert (dry['reading'], dry['print_error'], dry['hms']) == (20.0, 50348044, [{'attr': 1, 'code': 2}]) and gn.model(gn.load()['SERIAL']['C3'])['dry']['used']
    run_job(gn.Recorder('SERIAL'), before=40.0, lost=1.0, per_layer=3.0, t=time.mktime((2026, 11, 21, 12, 0, 0, 0, 0, -1)))        # a later spool in C3 still feeds at 39, 36, ... 9 g
    [dry] = gn.load()['SERIAL']['C3']['dry']
    assert dry['contradicted']['reading'] == 12.0 and not gn.model(gn.load()['SERIAL']['C3'])['dry']['used']
    def no_studio(): raise OSError('no Bambu Studio here')
    monkeypatch.setattr(fg, 'bs_conf', no_studio); fg.main(['learned'])
    assert '; no longer used: on 21 Nov a spool in it still gave filament at 12 g' in capsys.readouterr().out

def test_forget_takes_one_tray_and_asks_which_printer_when_two_have_it(state):
    for serial, label in (('S1', 'C3'), ('S1', 'B2'), ('S2', 'C3')): gn.keep(serial, label, 'jobs', whole(500, 400), gn.KEEP_JOBS)
    assert gn.forget('B2', None, {'S1': 'Bambu Lab H2D'}) == 'what was learned of B2 on Bambu Lab H2D' and 'B2' not in gn.load()['S1']
    with pytest.raises(ValueError, match='more than one printer'): gn.forget('C3')
    gn.forget('C3', 'Bambu Lab H2D', {'S1': 'Bambu Lab H2D'}); assert set(gn.load()['S1']) == set() and 'C3' in gn.load()['S2']
    with pytest.raises(ValueError, match='nothing is learned of a tray A1'): gn.forget('A1')

def test_a_print_sent_right_after_one_finished_is_recorded_and_the_first_counts_as_finished(state):
    benchy()
    def quick(rec, t, before, task):                                                     # FINISH seen twice, 15 s apart: not yet settled
        t = run_job(rec, before=before, end='FINISH', t=t, task=task, fault=lambda st, n, s, tt: (s, tt - 15 if st == 'FINISH' else tt))
        return t
    rec = gn.Recorder('SERIAL'); t = quick(rec, 0.0, 500.0, '0'); run_job(rec, before=458.0, t=t, task='0')       # LAN-only: both named alike
    jobs = gn.load()['SERIAL']['C3']['jobs']
    assert [(j['start'], j['end'], j['sound'], j['whole']) for j in jobs] == [(498, 458, True, True), (456, 416, True, True)]
    gn.forget(); rec = gn.Recorder('SERIAL'); t = quick(rec, 0.0, 500.0, '41'); run_job(rec, before=458.0, t=t, task='42')
    assert [j['sound'] for j in gn.load()['SERIAL']['C3']['jobs']] == [True, True]

def test_a_closed_warning_does_not_hold_back_the_next_answer(state):
    """"Slice all": a fine plate does not wipe another plate's warning; once that warning is closed, it is no reason."""
    guard = guard_with()
    def answer(plate_no, status):
        return {'title': 'Benchy', 'plate_no': plate_no, 'error': None, 'worst': status, 'rows': [{'slot': 1, 'status': status}], 'printer': {'model': 'Bambu Lab H2D', 'state': 'IDLE'}}
    guard.deliver(answer(1, 'SHORT')); guard.card.close()
    guard.checking('slice', 'Checking the spools on the Bambu Lab H2D…', asked=False); guard.deliver(answer(2, 'OK')); guard.checked('slice')
    assert guard.card.shown[-1] == ('show', CFG['ok_seconds']) and guard.last['plate_no'] == 2

def test_a_follower_says_each_failure_once(state, monkeypatch):
    watch = gl.JobWatch('SERIAL', CFG)
    for _ in range(3): watch.say('following the printer: 10.0.0.9 refused the access code Bambu Studio holds for it')
    monkeypatch.setattr(gl, 'catch_up', lambda *a: (_ for _ in ()).throw(fg.StateBusy('the state folder was held elsewhere for more than 10 s')))
    gl.enter('SERIAL', {'label': 'C3', 'type': 'PETG', 'colour': '#101010'}, 400)
    for _ in range(3): watch.see(H2D_JOB)                                                  # the state file cannot be written: said once, and the count waits
    log = (state / 'guard.log').read_text()
    assert log.count('refused the access code') == 1 and log.count('held elsewhere for more than 10 s; tried again at the next look') == 1

def test_a_rate_below_0_9_is_readings_that_lagged_and_is_not_used():
    assert not gn.model({'jobs': [whole(500, 372.5, 150)] * 3})['rate']['used']                                               # 0.85
    assert gn.model({'jobs': [whole(500, 372.5, 150)] * 3})['rate']['why'] == '0.85 g per gram is too far out to believe'

def test_the_spools_a_print_goes_on_with_are_taken_in_the_order_without_learning():
    """B owes 65 g more than it holds; A and C hold the same filament. Learning that counts A lower must not leave A
    counted higher than without learning, as taking C first (now the fullest) would."""
    trays = [tray('A1', 100), tray('B1', 20), tray('C1', 90)]
    learned = {'A1': {'credited': 50.0, 'evidence': 'it read 50 g when a spool ran dry in it on 19 Nov'}}
    plain = {t['label']: t['grams'] for t in fg.credit([dict(t) for t in trays], {'B1': 85.0})}
    strict = {t['label']: t['grams'] for t in fg.credit([dict(t) for t in trays], {'B1': 85.0}, learned)}
    assert plain == {'A1': 35.0, 'B1': 0.0, 'C1': 90.0} and strict == {'A1': 0.0, 'B1': 0.0, 'C1': 75.0}


# ---------------------------------------------------------------- the estimators (guard_stats, 1.4.0)

import guard_stats as gs

def test_the_conformal_bound_needs_nine_scores_and_is_then_the_largest():
    """ceil((n + 1) x 0.9)-th smallest: none below 9 scores, the largest of 9, the 18th of 19."""
    assert gs.conformal_bound(list(range(8)), 0.1) is None and gs.conformal_bound(list(range(9)), 0.1) == 8 and gs.conformal_bound(list(range(19)), 0.1) == 17

def test_shrinkage_pools_trays_that_cannot_be_told_apart_and_not_those_that_can():
    alike = [gs.group_stats([1.0, 1.02, 0.98], [1, 1, 1]), gs.group_stats([1.01, 0.99, 1.0], [1, 1, 1])]
    apart = [gs.group_stats([1.0, 1.01, 0.99], [1, 1, 1]), gs.group_stats([0.95, 0.96, 0.94], [1, 1, 1]), gs.group_stats([0.97, 0.98, 0.96], [1, 1, 1])]
    assert gs.shrinkage(alike) is None and gs.shrinkage(alike[:1]) is None                   # alike, or one tray: each takes the printer's figure
    assert gs.shrinkage(apart) == 1.0                                                         # far apart: its own prints count, never less than one print's worth
    near = [gs.group_stats([m, m + 0.03, m - 0.03], [1, 1, 1]) for m in (1.0, 0.98, 1.03)]
    means = (1.0, 0.98, 1.03)
    spread = sum((m - sum(means) / 3) ** 2 for m in means) / 2                                # by hand: the variance within is 0.0009; between the true
    assert gs.shrinkage(near) == pytest.approx(0.0009 / (spread - 0.0009 / 3), rel=1e-6) and gs.shrinkage(near) > 1   # means, what the means spread beyond it
    assert gs.group_stats([2.0, 4.0], [1.0, 3.0])[:2] == (3.5, pytest.approx(1.6))            # weighted mean, Kish's effective count

def test_t_quantiles_match_the_tables():
    for df, want in ((1, 6.314), (2, 2.920), (3, 2.353), (5, 2.015), (10, 1.812), (30, 1.697)): assert gs.t_quantile(0.95, df) == pytest.approx(want, abs=0.01)
    assert gs.t_quantile(0.90, 2) == pytest.approx(1.886, abs=0.001) and gs.t_quantile(0.90, 10) == pytest.approx(1.372, abs=0.01)

def test_the_rate_interval_is_fiellers_with_the_slicers_grams_exact():
    pairs = [(100, 98), (50, 49), (200, 197), (30, 29)]
    r = 373 / 380
    half = gs.t_quantile(0.95, 3) * (4 / 3 * sum((q - r * p) ** 2 for p, q in pairs)) ** 0.5 / 380
    assert gs.ratio_interval(pairs) == pytest.approx((r - half, r + half))                    # x without noise: Fieller is the ratio's own t interval
    exact, noisy = gs.fieller(10.0, 9.0, 0.0, 1.0, 0.0, 2.0), gs.fieller(10.0, 9.0, 4.0, 1.0, 0.0, 2.0)
    assert noisy[0] < exact[0] < 0.9 < exact[1] < noisy[1]                                     # a noisy denominator widens it, unevenly
    assert gs.fieller(1.0, 1.0, 1.0, 1.0, 0.0, 2.0) is None and gs.ratio_interval(pairs[:1]) is None

def test_kaplan_meier_on_the_flipped_scale_takes_spools_still_feeding_as_below():
    assert gs.km_upper([(20, True), (5, True), (3, True)], 0.1) == 20                         # fewer than ten: the largest
    assert gs.km_upper([(20, True)] + [(12, False)] * 10, 0.1) is None                        # ten spools fed below 20 without stopping: 20 is the odd one
    assert gs.km_upper([(20, True)] + [(25, False)] * 10, 0.1) == 20                          # spools that left above it say nothing of it
    assert gs.km_upper([(12, False)] * 3, 0.1) is None and gs.km_upper([(10, True), (10, False)], 0.1) == 10   # a tie: the exact value first
    assert gs.km_upper([(v, True) for v in range(1, 12)], 0.1) == 10

def test_the_sign_test():
    assert gs.sign_p(5, 5) == 1 / 32 and gs.sign_p(0, 4) == 1.0 and gs.sign_p(8, 10) == pytest.approx(56 / 1024) and gs.sign_p(0, 0) == 1.0

def test_a_change_point_needs_two_values_that_agree_not_one_odd_one():
    rnd = random.Random(7)
    base, prior = [rnd.gauss(0, 0.01) for _ in range(30)], (0.0, 1.0, 1.0, 0.03 ** 2)
    recent = lambda xs, odd=0.05: sum(gs.run_lengths(xs, 1 / 50, prior, odd)[-1][2:6])
    assert recent(base) < 0.05 and recent(base + [0.08]) < 0.5 and recent(base + [0.08, 0.0, 0.0]) < 0.5
    assert recent(base + [0.08, 0.0, 0.0], odd=0.0) > 0.5                                   # with no odd value allowed for, one ends the run for good
    assert recent(base + [0.08, 0.08]) > 0.5 and recent(base + [-0.045, -0.045]) > 0.5
    assert all(sum(p) == pytest.approx(1.0) for p in gs.run_lengths(base + [0.08, 0.08], 1 / 50, prior, 0.05))

def test_the_spread_from_spool_to_spool_bounds_one_more_spool():
    assert gs.pooled_spread([[20.0, 22.0, 18.0], [5.0]]) == (2.0, 2) and gs.pooled_spread([[1.0], [2.0]]) == (None, 0)
    assert gs.prediction_bound([5.0], 2.0, 2, 0.9) == pytest.approx(5 + 1.8856 * 2 * 2 ** 0.5, abs=1e-3)
    assert gs.quantile([1, 2, 3, 4], 0.5) == 2.5 and gs.quantile([], 0.5) is None
