"""Judging one filament against the spools that could print it (filament_guard.assess)."""
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
    assert fg.assess(fil(180), [dict(small), tray('C1', 10)], CFG)['reserve'] == 30.0      # a spool whose full weight the printer does not know counts as a 1 kg spool

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
    fils = [dict(fil(80), slot=1), dict(fil(80), slot=2)]; trays = [tray('B2', 100, weight=250)]
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

def tray_labels(row):
    return [t['label'] for t in row['trays']]

def judged(fils, trays, times=1):
    """The rows of a plate, as evaluate judges them: (each filament's status alone, the rows once those that share a spool were judged together)."""
    rows = [fg.assess(f, trays, CFG, times) for f in fils]; alone = [r['status'] for r in rows]
    fg.share_spools(rows, fils, trays, CFG, times)
    return alone, rows

def test_a_spool_that_stands_in_for_another_product_is_shared_with_its_own_filament():
    """Slot 1 is PLA Basic black, slot 2 PLA Matte black, of which no spool is loaded: A1, PLA Basic, counts for both, and
    A2 for slot 2 alone. Their tray lists differ, so each was judged alone, and both read "enough" on A1's 100 g."""
    fils = [dict(fil(60, '#000000'), slot=1), dict(fil(60, '#000000', fid='GFA01'), slot=2)]
    alone, rows = judged(fils, [tray('A1', 100, '#000000', weight=1000), tray('A2', 30, '#000000', fid='GFL99', name='Generic PLA', weight=1000)])
    assert alone == ['OK', 'OK'] and [tray_labels(r) for r in rows] == [['A1'], ['A1', 'A2']]
    assert [r['status'] for r in rows] == ['SHORT', 'SHORT']
    assert rows[0]['note'] == 'filaments 1, 2 share A1 and need 120 g together: short by 20 g; similar colour: A2 Generic PLA 30 g'
    assert rows[1]['note'] == ("filaments 1, 2 share A1 and need 120 g together: short by 20 g; AMS auto refill does not go on from A1 to A2 (30 g); "
                               "A1 is PLA Basic and A2 is Generic PLA, another product than the project's Bambu PLA Basic")

def test_the_external_spool_is_shared_by_the_filaments_of_both_nozzles():
    """The external spool counts for either nozzle: the left nozzle's filament can also print from B1, the right one's
    only from Ext, which does not hold both."""
    fils = [dict(fil(60, nozzle='left'), slot=1), dict(fil(60, nozzle='right'), slot=2)]
    alone, rows = judged(fils, [tray('B1', 20, nozzle='left', weight=1000), tray('Ext', 100, weight=1000)])
    assert alone == ['OK', 'OK'] and [tray_labels(r) for r in rows] == [['Ext', 'B1'], ['Ext']]
    assert [r['status'] for r in rows] == ['SHORT', 'SHORT']
    assert rows[0]['note'] == 'filaments 1, 2 share Ext and need 120 g together: short by 20 g; AMS auto refill does not go on from Ext to B1 (20 g)'
    assert rows[1]['note'] == 'filaments 1, 2 share Ext and need 120 g together: short by 20 g'

def test_two_shades_within_tolerance_of_one_spool_share_it():
    """Two greys of the project, both within the colour tolerance of the grey in A1; only the lighter one is also within
    it of A2."""
    fils = [dict(fil(60, '#808080', fid=''), slot=1), dict(fil(60, '#949494', fid=''), slot=2)]
    alone, rows = judged(fils, [tray('A1', 100, '#8A8A8A', fid='', weight=1000), tray('A2', 20, '#9E9E9E', fid='', weight=1000)])
    assert alone == ['OK', 'OK'] and [tray_labels(r) for r in rows] == [['A1'], ['A1', 'A2']]
    assert [r['status'] for r in rows] == ['SHORT', 'SHORT']
    assert rows[0]['note'] == 'filaments 1, 2 share A1 and need 120 g together: short by 20 g'
    assert rows[1]['note'] == 'filaments 1, 2 share A1 and need 120 g together: short by 20 g; AMS auto refill does not go on from A1 to A2 (20 g)'

def test_a_filament_is_judged_with_those_it_shares_a_spool_with_against_its_own_spools():
    """Three greys in a row: slot 1 prints from A1, slot 2 from A1 or A2, slot 3 from A2. Slot 2 may take either, so it
    is added to both of the others, and they to it; slots 1 and 3 share nothing and are not added to each other."""
    fils = [dict(fil(60, c, fid=''), slot=n) for n, c in ((1, '#808080'), (2, '#949494'), (3, '#A8A8A8'))]
    alone, rows = judged(fils, [tray('A1', 250, '#8A8A8A', fid='', weight=1000), tray('A2', 100, '#9E9E9E', fid='', weight=1000)])
    assert alone == ['OK', 'OK', 'OK'] and [tray_labels(r) for r in rows] == [['A1'], ['A1', 'A2'], ['A2']]
    assert [r['status'] for r in rows] == ['OK', 'OK', 'SHORT']                         # 120 g of A1's 250; 180 g of them; 120 g of A2's 100
    assert [r['note'] for r in rows] == ['filaments 1, 2 share A1 and need 120 g together', 'filaments 1, 2, 3 share A1 + A2 and need 180 g together',
                                         'filaments 2, 3 share A2 and need 120 g together: short by 20 g']

def test_a_spool_of_another_product_that_counts_is_named_even_when_there_is_enough():
    """PLA Basic red in the project, only a PLA Silk+ red loaded (31 apart in colour): it counts, as it can be picked at
    "Send print job", and the row says which spool that is. It read "enough" with nothing said."""
    silk = tray('B3', 192, colour='#D02727', fid='GFA06', name='PLA Silk+')
    row = fg.assess(fil(50), [silk], CFG)
    assert row['status'] == 'OK' and row['note'] == "B3 is PLA Silk+, another product than the project's Bambu PLA Basic"
    row = fg.assess(fil(50), [silk, tray('C1', 20, colour='#C02020', fid='GFL99', name='')], CFG)       # two of them, one that names no product
    assert row['status'] == 'OK' and row['note'] == "B3 is PLA Silk+ and C1 is PLA, another product than the project's Bambu PLA Basic"
    row = fg.assess(fil(50), [dict(silk, grams=None)], CFG)
    assert row['status'] == 'UNKNOWN' and row['note'] == "B3 reports no remaining amount; B3 is PLA Silk+, another product than the project's Bambu PLA Basic"
    assert fg.assess(fil(50), [tray('B2', 81)], CFG)['note'] == ''                                     # the project's own product
    assert fg.assess(fil(50, fid=''), [silk], CFG)['note'] == ''                                       # a project that names no product

def test_two_spools_count_together_only_where_ams_auto_refill_goes_on_from_one_to_the_other():
    """The same product and colour, both in an AMS. A total reached only with another spool is "not enough", and the note
    names that spool and its amount: it read "AMS auto refill must be on" across an AMS tray and the external spool."""
    row = fg.assess(fil(100), [tray('A1', 60), tray('Ext', 60)], CFG)
    assert (row['status'], row['best'], row['total']) == ('SHORT', 60, 120)
    assert row['note'] == 'short by 40 g; AMS auto refill does not go on from A1 to Ext (60 g)'
    row = fg.assess(fil(100, fid='GFA01'), [tray('A1', 60), tray('A2', 60, colour='#D02727', fid='GFA06', name='PLA Silk+')], CFG)   # two other products
    assert row['status'] == 'SHORT' and row['note'] == ("short by 40 g; AMS auto refill does not go on from A1 to A2 (60 g); "
                                                        "A1 is PLA Basic and A2 is PLA Silk+, another product than the project's Bambu PLA Basic")
    row = fg.assess(fil(100, fid=''), [tray('A1', 60, fid=''), tray('A2', 60, colour='#C43020', fid='')], CFG)                       # two shades
    assert row['status'] == 'SHORT' and row['note'] == 'short by 40 g; AMS auto refill does not go on from A1 to A2 (60 g)'
    three = [tray('A1', 60), tray('A2', 60), tray('Ext', 30)]
    row = fg.assess(fil(100), three, CFG)
    assert row['status'] == 'SPLIT' and row['note'] == 'only across A1 + A2: AMS auto refill must be on'
    row = fg.assess(fil(140), three, CFG)
    assert row['status'] == 'SHORT' and row['note'] == 'short by 20 g; AMS auto refill does not go on from A1 + A2 to Ext (30 g)'
    assert fg.assess(fil(200), three, CFG)['note'] == 'short by 50 g'                                  # not there with every spool either
    row = fg.assess(fil(100), [tray('A1', 60), tray('A2', 60)], CFG)
    assert row['status'] == 'SPLIT' and row['note'] == 'only across these spools: AMS auto refill must be on'
    res = {'error': None, 'worst': 'SHORT', 'rows': [fg.assess(fil(100), [tray('A1', 60), tray('Ext', 60)], CFG)]}
    assert fg.exit_code(res) == 2 and fg.headline(res) == 'Not enough filament for this print'

def test_a_filament_without_a_weight_is_unknown_and_says_why():
    """Bambu Studio writes 0.00 g for a filament whose profile has density 0 (parse_plate keeps it, with no grams): how
    much it needs cannot be told, so it is not "enough" whatever the spool holds. A spool that is not loaded is still
    the worse answer."""
    blue = dict(fil(None, colour='#0A2989'), density=0.0)
    row = fg.assess(blue, [tray('C3', 900, colour='#0A2989')], CFG, times=3)
    assert (row['status'], row['need'], tray_labels(row), row['best']) == ('UNKNOWN', None, ['C3'], 900)   # with its spools: those that share them are judged by it
    assert row['note'] == "Bambu Studio gives no weight for this filament (its profile's density is 0)"
    assert fg.assess(dict(blue, density=1.24), [tray('C3', 900, colour='#0A2989')], CFG)['note'] == 'Bambu Studio gives no weight for this filament'
    assert fg.assess(blue, [tray('C3', 900)], CFG)['status'] == 'COLOUR' and fg.assess(blue, [tray('A2', 900, typ='PETG')], CFG)['status'] == 'MISSING'
    res = {'title': 'Vase', 'times': 1, 'error': None, 'worst': 'UNKNOWN', 'rows': [row], 'printer': {'model': 'Bambu Lab H2D', 'state': 'IDLE'}}
    assert fg.need_text(row) == '?' and fg.need_text(fg.assess(fil(50), [tray('B2', 81)], CFG)) == '50.0 g'
    assert fg.exit_code(res) == 1 and fg.report_text(res).splitlines()[2] == (
        "   2 PLA      #0A2989 Bambu PLA Basic        needs         ?   C3         has 900 g                  unknown"
        "  (Bambu Studio gives no weight for this filament (its profile's density is 0))")

def test_a_spool_of_unknown_full_weight_or_with_an_entered_amount_counts_as_1_kg_for_the_reserve():
    """Such a tray had 10 % of the need for a reserve and nothing of the spool: 100 g of a spool put at 115 g read
    "enough" where a 1 kg spool at 115 g is "tight". An amount typed in rests on a kitchen scale and a typed empty-spool
    weight, and had 3 % of itself."""
    row = fg.assess(fil(100), [tray('B2', 115)], CFG)                                      # the printer reports no full weight
    assert (row['status'], row['reserve']) == ('TIGHT', 30.0) and "15 g to spare, and the AMS's figure can be 30 g out" in row['note']
    row = fg.assess(fil(100), [dict(tray('A3', 115, weight=115.0), entered=1.7e9)], CFG)   # as guard_ledger gives it: the amount first entered for a weight
    assert (row['status'], row['reserve']) == ('TIGHT', 30.0)
    assert fg.assess(fil(100), [tray('B2', 115, weight=250)], CFG)['status'] == 'OK'       # a 250 g spool by the printer's word: 10 % of the need is the larger
    assert fg.assess(fil(400), [tray('B2', 445)], CFG)['reserve'] == 40.0                  # as it is of a large need

def test_a_filament_that_shares_a_spool_with_one_without_a_weight_is_not_enough_either():
    """Two blues of a plate print from C3, and Bambu Studio gives the first no weight (its profile has density 0). The
    second, 50 g of a spool that holds 900 g, read "enough" in its row, though what the first takes of C3 cannot be
    told. A filament that shares no spool with it is judged as before."""
    blue, no_weight = dict(fil(50, colour='#0A2989'), slot=2), dict(fil(None, colour='#0A2989'), slot=1, density=0.0)
    trays = [tray('C3', 900, colour='#0A2989', weight=1000), tray('B2', 900, weight=1000)]
    alone, rows = judged([no_weight, blue, dict(fil(50), slot=3)], trays)
    assert alone == ['UNKNOWN', 'OK', 'OK'] and [tray_labels(r) for r in rows] == [['C3'], ['C3'], ['B2']]
    assert [(r['status'], r['note']) for r in rows] == [('UNKNOWN', "Bambu Studio gives no weight for this filament (its profile's density is 0)"),
                                                        ('UNKNOWN', 'filament 1 can also print from C3, and Bambu Studio gives it no weight'), ('OK', '')]
    alone, rows = judged([no_weight, dict(blue, grams=500), dict(blue, grams=500, slot=3)], trays)     # with others that have a weight: those are added up, and the worse answer stays
    assert [r['status'] for r in rows] == ['UNKNOWN', 'SHORT', 'SHORT']
    assert rows[1]['note'] == 'filaments 2, 3 share C3 and need 1000 g together: short by 100 g; filament 1 can also print from C3, and Bambu Studio gives it no weight'
    alone, rows = judged([no_weight, dict(no_weight, slot=4), blue], trays)
    assert (rows[2]['status'], rows[2]['note']) == ('UNKNOWN', 'filaments 1, 4 can also print from C3, and Bambu Studio gives them no weight')
    assert rows[0]['note'] == rows[1]['note'] == "Bambu Studio gives no weight for this filament (its profile's density is 0)"   # each says its own
