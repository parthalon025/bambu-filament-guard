"""Reading a sliced plate: the G-code header and the grams each layer takes (filament_guard)."""
import io
import json
import zipfile

import pytest

import filament_guard as fg
import guard_ledger as gl
from helpers import CFG, gcode, sliced, e, running, blue_reads


def profile_of(body, weights, layers=3, why=None, **more):
    text = sliced(body, weights, layers, **more)
    return fg.layer_profile(io.BytesIO(text.encode()), fg.parse_plate(text), why)

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

def test_layer_count_is_read():
    assert fg.parse_plate(gcode().replace('; filament_density', '; total layer number: 27\n; filament_density'))['layers'] == 27
    assert fg.parse_plate(gcode())['layers'] is None

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

def exported(tmp_path, plates, name='Brackets.gcode.3mf'):
    """A sliced file as Bambu Studio exports it, with the G-code of each plate (a text, or the grams of blue PLA it needs)."""
    path = tmp_path / name
    with zipfile.ZipFile(path, 'w') as z:
        for n, plate in enumerate(plates, start=1):
            z.writestr(f'Metadata/plate_{n}.gcode', plate if isinstance(plate, str) else gcode(weights=f'{plate:.2f}', used='1'))
    return str(path)

def test_the_plates_of_a_file_add_up_by_filament():
    """The same material, colour, product and nozzle is one filament, whatever its slot; anything else is another."""
    first = fg.parse_plate(gcode(weights='60.00,10.00', nozzles='0.4,0.4', fmap='1,1,2'))
    second = fg.parse_plate(gcode(weights='60.00,5.00,7.00', used='1,2,3', nozzles='0.4,0.4', fmap='1,1,1'))   # its red is on the other nozzle
    both = fg.plates_together([first, second])
    assert [(f['slot'], f['type'], f['colour'], f['nozzle'], f['grams']) for f in both['filaments']] == [
        (1, 'PLA', '#0A2989', 'left', 120.0), (3, 'PLA', '#C12E1F', 'right', 10.0), (2, 'PETG', '#FFFFFF', 'left', 5.0), (3, 'PLA', '#C12E1F', 'left', 7.0)]
    assert both['printer_model'] == 'Bambu Lab H2D' and first['filaments'][0]['grams'] == 60.0

def test_check_judges_the_plates_of_a_file_together_as_well(state, tmp_path, monkeypatch, capsys):
    """"Print all plates" needs the sum, and docs/limits.md sends there to `check FILE`. Three plates of 400 g of one
    blue against a spool that reads 1000 g: each plate alone read "enough", and the exit code was 0."""
    path, asked = exported(tmp_path, [400, 400, 400]), []
    running(monkeypatch, blue_reads(1000)); monkeypatch.setattr(fg, 'read_status', lambda *a, **k: asked.append(1) or blue_reads(1000))
    assert fg.main(['check', path]) == 2 and len(asked) == 1                              # every answer from one reading of the printer
    blocks = capsys.readouterr().out.splitlines()
    assert [line for line in blocks if not line.startswith(' ')] == [f'Enough filament for this print: Brackets.gcode.3mf plate {n}' for n in (1, 2, 3)] + [
        'Not enough filament for this print: Brackets.gcode.3mf all plates together']
    assert 'needs  1200.0 g' in blocks[-1] and 'NOT ENOUGH  (short by 200 g)' in blocks[-1]
    assert fg.main(['check', path, '--json']) == 2
    results = json.loads(capsys.readouterr().out)
    assert [(r['title'], r['worst']) for r in results] == [(f'Brackets.gcode.3mf plate {n}', 'OK') for n in (1, 2, 3)] + [('Brackets.gcode.3mf all plates together', 'SHORT')]
    assert fg.main(['check', exported(tmp_path, [400, 400]), '--times', '2']) == 2 and 'all plates together x 2' in capsys.readouterr().out   # 1600 g
    assert fg.main(['check', exported(tmp_path, [480, 480])]) == 1 and 'Filament is tight for this print: Brackets.gcode.3mf all plates together' in capsys.readouterr().out
    assert fg.main(['check', exported(tmp_path, [400])]) == 0 and 'together' not in capsys.readouterr().out   # one plate: one answer

def no_weight(weights='0.00,10.87', density='0,1.27,1.26', lengths=None, **more):
    """A G-code whose blue (slot 1) Bambu Studio gives 0.00 g: its profile has density 0, as a profile made by hand does
    until one is entered. lengths: the header's "total filament length [mm]", one for each filament in use."""
    text = gcode(weights=weights, **more).replace('; filament_density: 1.26,1.27,1.26', f'; filament_density: {density}')
    return text.replace('; total filament weight', f'; total filament length [mm] : {lengths}\n; total filament weight') if lengths else text

def test_a_filament_in_use_that_bambu_studio_gives_no_weight_is_kept_without_one():
    """A slot the "; filament:" line names, at 0.00 g: in use when its density is 0 or its length is above 0. It was
    dropped without a word, and the plate judged on the other filaments alone."""
    kept = lambda text: [(f['slot'], f['colour'], f['grams'], f['density']) for f in fg.parse_plate(text)['filaments']]
    assert kept(no_weight()) == [(1, '#0A2989', None, 0.0), (3, '#C12E1F', 10.87, 1.26)]
    assert kept(no_weight(density='1.24,1.27,1.26', lengths='310.52,3600.10')) == [(1, '#0A2989', None, 1.24), (3, '#C12E1F', 10.87, 1.26)]   # by its length alone
    assert kept(no_weight(density='1.24,1.27,1.26', lengths='0.00,3600.10')) == [(3, '#C12E1F', 10.87, 1.26)]    # named, and not used: as before
    assert kept(no_weight(density='1.24,1.27,1.26')) == [(3, '#C12E1F', 10.87, 1.26)]                             # nothing says it is used
    assert [f['slot'] for f in fg.parse_plate(no_weight(weights='0.00,5.00,10.87', used=None))['filaments']] == [2, 3]   # no "; filament:" line: a slot at 0 is not in use
    assert kept(gcode()) == [(1, '#0A2989', 9.99, 1.26), (3, '#C12E1F', 10.87, 1.26)]

def test_a_plate_with_no_filament_the_guard_can_weigh_is_not_checked(state, tmp_path, monkeypatch, capsys):
    """Every filament in use at 0.00 g: no row to judge, and the worst of no rows read "Enough filament for this print",
    exit code 0. With another filament beside it the plate is checked, and is not "enough"."""
    running(monkeypatch, blue_reads(1000)); empty = fg.parse_plate(no_weight(weights='0.00', used='1'))
    for plate in (empty, dict(empty, filaments=[])):
        res = fg.evaluate(plate, CFG, 'Vase')
        assert res['error'] == 'this plate uses no filament the guard can weigh' and res['rows'] == [] and fg.exit_code(res) == 3
        assert fg.report_text(res) == 'Filament could not be checked: Vase\n  this plate uses no filament the guard can weigh'
    path = tmp_path / 'Vase.gcode'; path.write_text(no_weight(weights='0.00', used='1'))
    assert fg.main(['check', str(path)]) == 3 and 'this plate uses no filament the guard can weigh' in capsys.readouterr().out
    mixed = no_weight(weights='0.00,10.87', colours='#0A2989;#FFFFFF;#0A2989'); path.write_text(mixed)      # two blues, one of them without a weight
    res = fg.evaluate(fg.parse_plate(mixed), CFG, 'Vase')
    assert [(r['slot'], r['status'], r['need']) for r in res['rows']] == [(1, 'UNKNOWN', None), (3, 'OK', 10.87)] and res['worst'] == 'UNKNOWN'
    assert fg.headline(res) == 'Enough filament could not be confirmed' and fg.main(['check', str(path)]) == 1

def test_a_filament_without_a_weight_is_carried_as_unknown_wherever_the_grams_are_used(state):
    """Nothing adds it up as 0 g: an amount entered for its spool is out of date once a job draws on it, the plate gets
    no per-layer profile, and all plates of a file together have no weight for it either."""
    text = no_weight().replace('; filament_density', '; total layer number: 3\n; filament_density'); plate = fg.parse_plate(text)
    fg.remember_slice('Vase', 1, plate)
    job = {'gcode_state': 'RUNNING', 'subtask_name': 'Vase', 'task_id': '5', 'layer_num': 1, 'total_layer_num': 3, 'mapping': [254, 65535, 0],
           'vt_tray': {'id': '254', 'tray_type': 'PLA', 'tray_color': '0A2989FF', 'remain': -1, 'tray_weight': '0'},
           'ams': {'ams': [{'id': '0', 'tray': [{'id': '0', 'tray_type': 'PLA', 'tray_color': 'C12E1FFF', 'remain_g': 500, 'tray_weight': '1000'}]}]}}
    assert gl.draws(job, fg.load_state()['slices'], ['A1', 'Ext']) == ({'A1': 10.87}, {'Ext'})
    gl.enter('SERIAL', {'label': 'Ext', 'type': 'PLA', 'colour': '#0A2989'}, 300); gl.count('SERIAL', job)
    assert fg.load_state()['spools']['SERIAL']['Ext']['stale'] is True and gl.owed(job, fg.load_state()['slices']) == ({}, False)
    why = []
    assert fg.layer_profile(io.BytesIO(text.encode()), plate, why) is None and why == ['a filament of it has no weight in the header']
    both = fg.plates_together([plate, fg.parse_plate(gcode())])
    assert [(f['slot'], f['grams']) for f in both['filaments']] == [(1, None), (3, 21.74)]
