"""Judging one filament against the spools that could print it (filament_guard.assess)."""
import json

import filament_guard as fg
from helpers import CFG, tray, fil


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

def test_the_reserve_is_a_share_of_the_largest_spool_the_filament_could_be_printed_from():
    """Not of the fullest spool's: which spool is the fullest changes with what is learned of the trays, and the reserve
    must not."""
    small, large = tray('B2', 200, weight=250), tray('C1', 10, weight=1000)
    assert fg.assess(fil(180), [dict(small)], CFG)['reserve'] == 18.0 and fg.assess(fil(180), [dict(small)], CFG)['status'] == 'OK'   # alone: 10 % of the need
    for trays in ([small, large], [large, small]):
        row = fg.assess(fil(180), [dict(t) for t in trays], CFG)
        assert (row['status'], row['reserve'], row['best']) == ('TIGHT', 30.0, 200) and "the AMS's figure can be 30 g out" in row['note']
    assert fg.assess(fil(180), [dict(small), tray('C1', 10)], CFG)['reserve'] == 18.0      # a spool whose full weight the printer does not know adds nothing

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

def test_two_slots_of_one_colour_are_judged_together():
    fils = [dict(fil(80), slot=1), dict(fil(80), slot=2)]; trays = [tray('B2', 100)]
    rows = [fg.assess(f, trays, CFG) for f in fils]
    assert [r['status'] for r in rows] == ['OK', 'OK']                                  # each alone fits the spool
    fg.share_spools(rows, fils, trays, CFG, 1)
    assert [r['status'] for r in rows] == ['SHORT', 'SHORT'] and 'filaments 1, 2 share B2 and need 160 g together: short by 60 g' in rows[0]['note']

def test_the_spools_a_print_goes_on_with_are_taken_in_the_order_without_learning():
    """B owes 65 g more than it holds; A and C hold the same filament. Learning that counts A lower must not leave A
    counted higher than without learning, as taking C first (now the fullest) would."""
    trays = [tray('A1', 100), tray('B1', 20), tray('C1', 90)]
    learned = {'A1': {'credited': 50.0, 'evidence': 'it read 50 g when a spool ran dry in it on 19 Nov'}}
    plain = {t['label']: t['grams'] for t in fg.credit([dict(t) for t in trays], {'B1': 85.0})}
    strict = {t['label']: t['grams'] for t in fg.credit([dict(t) for t in trays], {'B1': 85.0}, learned)}
    assert plain == {'A1': 35.0, 'B1': 0.0, 'C1': 90.0} and strict == {'A1': 0.0, 'B1': 0.0, 'C1': 75.0}

def linked(monkeypatch, reports):
    """read_status on a stand-in for the link to the printer: `reports` are (tenths of a second after the link is up, the
    "print" part of a report), on a clock that only time.sleep moves. Returns the status read."""
    tenths, heard = [0], []
    def link(ip, serial, code, client_id, on_report, on_refused=None):
        heard.append(on_report); told()
        return type('Client', (), {'loop_stop': lambda self: None, 'disconnect': lambda self: None})()
    def told():
        for at, report in reports:
            if at == tenths[0]: heard[0](json.loads(json.dumps(report)))                 # as it comes off the link: a message of its own
    monkeypatch.setattr(fg, 'printer_link', link); monkeypatch.setattr(fg.time, 'sleep', lambda s: (tenths.__setitem__(0, tenths[0] + 1), told()))
    return fg.read_status('printer', 'SERIAL', 'code')

WHOLE = {'gcode_state': 'IDLE', 'ams': {'tray_now': '255', 'ams': [{'id': '0', 'tray': [{'id': '0', 'tray_type': 'PLA', 'tray_color': '0A2989FF', 'remain_g': 640}]}]}}

def test_a_report_still_in_pieces_is_waited_for_until_it_has_its_spools(monkeypatch):
    """A printer sends its report in pieces. With a small piece of "ams" ahead of the whole ({"tray_now": ...}), the
    status was returned after a second as it stood: no spool loaded, yet taken for complete."""
    piece = {'gcode_state': 'IDLE', 'ams': {'tray_now': '255', 'version': 4}}
    assert not fg.has_spools(piece) and not fg.has_spools({'gcode_state': 'IDLE'}) and not fg.has_spools({'ams': None}) and not fg.has_spools({'ams': {'ams': None}})
    assert fg.has_spools(WHOLE) and fg.has_spools({'ams': {'ams': []}})                    # an AMS with nothing in it, or none: said by the printer
    status = linked(monkeypatch, [(0, piece), (15, WHOLE)])
    assert fg.has_spools(status) and [t['label'] for t in fg.trays_of(status)[0]] == ['A1']
    assert not fg.has_spools(linked(monkeypatch, [(0, piece), (40, WHOLE)]))               # not within three seconds: returned as it is, and not taken for whole

def two_nozzles(infos=(), external=()):
    """The status of a printer with two nozzles: an AMS for each 'info' word, each with one spool of red PLA, and the
    external holders with these ids. Returns the nozzle of each tray, by label."""
    spool = {'tray_type': 'PLA', 'tray_color': 'C12E1FFF', 'remain_g': 100}
    status = {'device': {'extruder': {'info': [{'id': 0, 'snow': 0xFFFF}, {'id': 1, 'snow': 0xFFFF}]}},
              'ams': {'ams': [{'id': str(i), 'info': info, 'tray': [dict(spool, id='0')]} for i, info in enumerate(infos)]}, 'vir_slot': [dict(spool, id=i) for i in external]}
    return {t['label']: t['nozzle'] for t in fg.trays_of(status)[0]}

def test_the_nozzle_an_ams_feeds_is_read_as_bambu_studio_reads_it():
    """Bambu Studio takes the extruder an AMS is bound to from bits 8-11 of its 'info' word: 0 the main one (right), 1 the
    deputy (left), 0xE none. The H2D on firmware 01.04 sends 0xE there and tells the side in bits 24-27, the only place
    the guard looked: an AMS that says it by Bambu Studio's bits read as right whatever it fed."""
    assert two_nozzles(['00000103', '00000003']) == {'A1': 'left', 'B1': 'right'}             # bound: 1 the left nozzle, 0 the right
    assert two_nozzles(['51001E03', '50001E03']) == {'A1': 'left', 'B1': 'right'}             # not bound (0xE): the top byte, as the H2D sends it
    assert two_nozzles(['50000103', '51000003']) == {'A1': 'left', 'B1': 'right'}             # both there, and at odds: the binding counts
    assert two_nozzles([0x51001E03, None, '', 'no number', ['51001E03']]) == {label: None for label in ('A1', 'B1', 'C1', 'D1', 'E1')}   # not text: not known, and no error

def test_an_external_spool_counts_for_its_own_nozzle_on_a_printer_with_two():
    """The H2D has an external holder for each nozzle: id 254 the left (Ext-L), 255 the right (Ext-R). Both counted for
    either nozzle: 900 g on the left holder made "enough" of a filament the right nozzle prints, which had 20 g."""
    assert two_nozzles(['50001E03'], ['254', '255']) == {'A1': 'right', 'Ext': 'left', 'Ext2': 'right'}
    assert two_nozzles([], [255, 254, '7']) == {'Ext': 'right', 'Ext2': 'left', 'Ext3': None}  # by its id, not its place; an id that is neither: not known
    trays = [tray('A1', 20, nozzle='right'), tray('Ext', 900, nozzle=two_nozzles([], ['254'])['Ext'])]
    row = fg.assess(fil(300, nozzle='right'), trays, CFG)
    assert row['status'] == 'SHORT' and [t['label'] for t in row['trays']] == ['A1'] and fg.assess(fil(300, nozzle='left'), trays, CFG)['status'] == 'OK'
    one = fg.trays_of({'ams': {'ams': []}, 'vt_tray': {'id': '254', 'tray_type': 'PLA', 'tray_color': 'C12E1FFF'}})
    assert one == ([{'label': 'Ext', 'type': 'PLA', 'fid': '', 'colour': '#C12E1F', 'name': '', 'grams': None, 'weight': None, 'nozzle': None, 'spool': None}], False)   # one nozzle: no side
