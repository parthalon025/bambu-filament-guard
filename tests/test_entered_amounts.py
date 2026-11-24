"""Amounts entered by hand, counted down as prints draw on them, and the link that follows a printer (guard_ledger)."""
import json
import time
import types

import filament_guard as fg
import guard_ledger as gl
from helpers import CFG, H2D_JOB, SLICE, c3, kept, needing, part, printing, running, spool


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

def test_the_filament_is_what_the_scale_shows_less_the_empty_spool():
    assert fg.filament_on('1012', '250') == (762.0, None) and fg.filament_on(' 1 012,5 g', '212.5g') == (800.0, None)
    assert fg.filament_on('640', '') == (640.0, None) and fg.filament_on('640', '0') == (640.0, None)      # the filament weighed alone
    for scale, spool, word in (('', '250', 'scale'), ('abc', '250', 'scale'), ('0', '0', 'scale'), ('nan', '0', 'scale'), ('20000', '250', 'scale'),
                               ('900', 'x', 'empty spool'), ('900', '-5', 'empty spool'), ('900', '6000', 'empty spool'),
                               ('250', '250', 'no filament'), ('200', '250', 'no filament'), ('14000', '100', 'not a spool')):
        grams, why = fg.filament_on(scale, spool)
        assert grams is None and word in why, (scale, spool)

def test_a_weight_far_from_the_printers_reading_is_doubted_either_way():
    """More than 50 g apart is more than a gauge is out when a spool runs dry: a slipped key, or another spool."""
    assert fg.weigh_doubt(92.0, 110.0) == '' and fg.weigh_doubt(60.0, 110.0) == '' and fg.weigh_doubt(160.0, 110.0) == ''      # up to 50 g, either way
    assert fg.weigh_doubt(8.0, 110.0) == '8 g weighed, 110 g read by the printer: 102 g apart. Save again if the weight is right.'
    assert fg.weigh_doubt(59.9, 110.0).startswith('60 g weighed, 110 g read by the printer: 50 g apart') and '51 g apart' in fg.weigh_doubt(161.0, 110.0)


def follow(monkeypatch, script, passes, leap=(0, 0)):
    """JobWatch.run for SERIAL against a scripted link and clock. script: {pass: report}, what the printer sends at that
    pass of the follower's 3-second wait; leap: (pass, seconds) the wall clock jumps there (the computer slept). Returns
    the follower, [(pass, job id)] of the statuses it looked at, and for each link it made whether the address was
    looked up afresh."""
    clock, n, looks, links, tell = [time.time()], [0], [], [], {}
    monkeypatch.setattr(gl, 'time', types.SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setattr(fg, 'bs_conf', lambda: {'access_code': {'SERIAL': 'code'}})
    monkeypatch.setattr(fg, 'printer_ip', lambda serial, cfg, refresh=False: links.append(refresh) or 'printer')
    def printer_link(ip, serial, code, client_id, on_report, on_refused=None):
        tell['report'] = on_report; return types.SimpleNamespace(loop_stop=lambda: None, disconnect=lambda: None)
    monkeypatch.setattr(fg, 'printer_link', printer_link)
    watch = gl.JobWatch('SERIAL', dict(CFG, learn=False)); see = watch.see
    monkeypatch.setattr(watch, 'see', lambda status, *more: (looks.append((n[0], status.get('task_id'))), see(status, *more))[1])
    def wait(seconds):
        clock[0] += seconds
        if seconds == 3:
            n[0] += 1
            if n[0] == leap[0]: clock[0] += leap[1]
            if n[0] in script: tell['report'](script[n[0]])
        return n[0] >= passes
    monkeypatch.setattr(watch.stopped, 'wait', wait); monkeypatch.setattr(watch.stopped, 'is_set', lambda: n[0] >= passes)
    watch.run()
    return watch, looks, links

def test_a_link_gone_silent_is_not_looked_at_and_the_first_report_after_it_is_one_after_a_gap(state, monkeypatch):
    """The follower's link goes silent for 15 minutes (the Wi-Fi away, the printer at a new address) while the guard runs;
    jobs 11 to 13 are printed unseen, job 14 runs when reports come again. The status held is looked at for a minute after
    its last report and no longer; the link is then ended and made anew with the address looked up afresh; and the first
    report after the silence is one after a gap: every entered amount is out of date."""
    gl.enter('SERIAL', {'label': 'C3', 'type': 'PETG', 'colour': '#101010'}, 400); fg.remember_slice('Benchy', 1, SLICE)
    over, job = dict(H2D_JOB, gcode_state='FINISH', task_id='10'), dict(H2D_JOB, task_id='14')
    watch, looks, links = follow(monkeypatch, {1: over, 300: job}, 310)
    assert [n for n, task in looks if task == '10'] == list(range(2, 22))                # a minute of looks at the last report, then none
    assert links[0] is False and len(links) > 1 and all(links[1:])                        # the link made anew, its address looked up afresh
    assert ('14' in [task for _, task in looks]) and c3()['stale'] and c3()['grams'] is None
    assert (state / 'guard.log').read_text().count('no report for a minute: the link is made anew') == 1

def test_a_report_a_minute_ago_is_still_the_printers_status(state, monkeypatch):
    """A printer that reports every 45 seconds is followed on one link, and its job is counted when it starts."""
    gl.enter('SERIAL', {'label': 'C3', 'type': 'PETG', 'colour': '#101010'}, 400); fg.remember_slice('Benchy', 1, SLICE)
    over = dict(H2D_JOB, gcode_state='FINISH', task_id='10')
    watch, looks, links = follow(monkeypatch, {n: over if n < 200 else H2D_JOB for n in range(1, 300, 15)}, 300)
    assert links == [False] and c3()['grams'] == 280 and not c3().get('stale')

def test_a_follower_that_stood_still_takes_what_it_holds_for_out_of_date(state, monkeypatch):
    """The computer sleeps for 8 hours. On waking, a piece of a report arrives before the follower's next look: the status
    it holds is still the one from before, the job long over. It is not looked at; the link is made anew, and the first
    whole status, another job running, is one after a gap."""
    gl.enter('SERIAL', {'label': 'C3', 'type': 'PETG', 'colour': '#101010'}, 400); fg.remember_slice('Benchy', 1, SLICE)
    over, job = dict(H2D_JOB, gcode_state='FINISH', task_id='10'), dict(H2D_JOB, task_id='14')
    watch, looks, links = follow(monkeypatch, {1: over, 20: {'wifi_signal': '-40dBm'}, 60: job}, 70, leap=(20, 8 * 3600))
    assert max(n for n, task in looks if task == '10') < 20 and links[1:] and all(links[1:]) and c3()['stale']

def amounts_at(monkeypatch, watch, status, plate):
    """The follower's look at a status, then the check of a plate against it: (the answer, {tray: grams it is counted with})."""
    watch.look(status); running(monkeypatch, status); res = fg.evaluate(plate, CFG, 'Next')
    return res['worst'], {t['label']: t['grams'] for t in res['rows'][0]['trays']}

def test_what_a_job_needs_beyond_an_entered_spool_comes_off_the_next_entered_spool_of_it(state, monkeypatch):
    """A1 and A2 hold the same black PLA without tags, entered as 50 g and 1000 g. A 300 g job goes to A1; the AMS goes
    on with A2. The check shows A1 at 0 g and A2 at 750 g while the job prepares, and the same once the ledger has
    counted the job: while it runs, and after it."""
    part(300.0); spools = [spool(0), spool(1)]
    for label, grams in (('A1', 50), ('A2', 1000)): gl.enter('SERIAL', {'label': label, 'type': 'PLA', 'colour': '#101010'}, grams)
    watch = gl.JobWatch('SERIAL', CFG); watch.look(printing('IDLE', spools, task='1'))
    before = amounts_at(monkeypatch, watch, printing('PREPARE', spools), needing(850))
    assert before == ('SHORT', {'A2': 750, 'A1': 0})
    for status in (printing('RUNNING', spools, layer=1), printing('RUNNING', spools, layer=60), printing('FINISH', spools, layer=100)):
        assert amounts_at(monkeypatch, watch, status, needing(850)) == before
    assert {label: entry['grams'] for label, entry in fg.load_state()['spools']['SERIAL'].items()} == {'A1': 0, 'A2': 750}
    assert 'A1 -50 g, A2 -250 g that it needs beyond A1' in (state / 'guard.log').read_text()

def test_what_a_job_needs_beyond_a_measured_spool_comes_off_the_entered_spool_of_it_once(state, monkeypatch):
    """The job's own tray, A1, is one the printer measures, at 50 g; A2, the same filament without a tag, is entered at
    1000 g. The 250 g the job needs beyond A1's reading come off A2 when the job is counted, and the check does not take
    them off again while the job runs: A2 is counted with 750 g throughout, and still after the print."""
    part(300.0); spools = lambda a1: [spool(0, a1, fid='GFA00'), spool(1, fid='GFA00')]
    gl.enter('SERIAL', {'label': 'A2', 'type': 'PLA', 'colour': '#101010'}, 1000)
    watch = gl.JobWatch('SERIAL', CFG); watch.look(printing('IDLE', spools(50), task='1'))
    before = amounts_at(monkeypatch, watch, printing('PREPARE', spools(50)), needing(850))
    assert before == ('SHORT', {'A2': 750, 'A1': 0})
    assert amounts_at(monkeypatch, watch, printing('RUNNING', spools(50), layer=1), needing(850)) == before
    res = fg.evaluate(needing(850), CFG, 'Next')
    assert res['printer']['beyond'] == {} and 'A1 reads 50 g; the running print still needs 300 g: 250 g more than it holds, taken off A2' in res['rows'][0]['note']
    assert fg.load_state()['spools']['SERIAL']['A2']['gave'] == {'A1': 250}
    for status in (printing('RUNNING', spools(0), layer=60), printing('FINISH', spools(0), layer=100)):
        assert amounts_at(monkeypatch, watch, status, needing(850)) == before

def test_what_a_job_needs_beyond_an_entered_spool_stays_owed_to_the_measured_spool_of_it(state, monkeypatch):
    """A1, entered at 50 g, is the job's tray; A2, the same filament, is measured at 900 g. No entered spool can give the
    250 g the job needs beyond A1: the check goes on taking them off A2 while the job runs, as it did while it prepared,
    until A2's own reading has fallen by them."""
    part(300.0); spools = lambda a2: [spool(0, fid='GFA00'), spool(1, a2, fid='GFA00')]
    gl.enter('SERIAL', {'label': 'A1', 'type': 'PLA', 'colour': '#101010'}, 50)
    watch = gl.JobWatch('SERIAL', CFG); watch.look(printing('IDLE', spools(900), task='1'))
    before = amounts_at(monkeypatch, watch, printing('PREPARE', spools(900)), needing(700))
    assert before == ('SHORT', {'A2': 650, 'A1': 0})
    assert amounts_at(monkeypatch, watch, printing('RUNNING', spools(900), layer=1), needing(700)) == before
    assert 'A1 holds 0 g as entered; the running print still needs 250 g: 250 g more than it holds, taken off A2' in fg.evaluate(needing(700), CFG, 'Next')['rows'][0]['note']
    for status in (printing('RUNNING', spools(773), layer=60), printing('FINISH', spools(650), layer=100)):
        assert amounts_at(monkeypatch, watch, status, needing(700)) == before         # at layer 60 A2 has given 127 g and owes 123 g more

def test_a_job_on_a_spool_of_unknown_amount_makes_the_entered_spools_of_its_filament_out_of_date(state):
    """A1 has no tag and no entered amount; A2, the same filament, is entered at 1000 g; A3 is another material. How
    much of a job on A1 goes beyond A1 cannot be told: A2's amount is out of date, A3's stands."""
    part(300.0); spools = [spool(0), spool(1), spool(2, typ='PETG')]
    gl.enter('SERIAL', {'label': 'A2', 'type': 'PLA', 'colour': '#101010'}, 1000); gl.enter('SERIAL', {'label': 'A3', 'type': 'PETG', 'colour': '#101010'}, 400)
    watch = gl.JobWatch('SERIAL', CFG); watch.look(printing('IDLE', spools, task='1')); watch.look(printing('RUNNING', spools, layer=1))
    book = fg.load_state()['spools']['SERIAL']
    assert book['A2'].get('stale') and not book['A3'].get('stale') and book['A3']['grams'] == 400
    assert "out of date: A2, which the print may go on from: A1's amount is not known" in (state / 'guard.log').read_text()

def test_an_amount_entered_during_a_print_has_what_the_print_needs_beyond_its_tray_taken_off(state, monkeypatch):
    """The print's own tray, A1, reads 30 g at layer 60 of 100 and the print still needs 123 g: it goes on from A2, the
    same filament without a tag, weighed and entered now at 500 g. The check takes the 93 g off A2 before the follower
    has looked; the follower's next look takes them off for good, and the check not again."""
    part(300.0); spools = [spool(0, 30, fid='GFA00'), spool(1, fid='GFA00')]; job = printing('RUNNING', spools, layer=60)
    gl.enter('SERIAL', {'label': 'A3', 'type': 'PETG', 'colour': '#101010'}, 400)        # an amount from before: the printer is followed
    watch = gl.JobWatch('SERIAL', CFG); watch.look(printing('IDLE', spools, task='1')); watch.look(job)
    gl.enter('SERIAL', {'label': 'A2', 'type': 'PLA', 'colour': '#101010'}, 500)
    running(monkeypatch, job); seen = lambda: {t['label']: t['grams'] for t in fg.evaluate(needing(450), CFG, 'Next')['rows'][0]['trays']}
    assert seen() == {'A2': 407, 'A1': 0}
    watch.look(job); assert fg.load_state()['spools']['SERIAL']['A2']['grams'] == 407 and seen() == {'A2': 407, 'A1': 0}
    assert 'A2: entered during "Part": what that print still needs beyond A1, 93 g, taken off: 407 g' in (state / 'guard.log').read_text()
    watch.look(printing('FINISH', [spool(0, 0, fid='GFA00'), spool(1, fid='GFA00')], layer=100)); assert fg.load_state()['spools']['SERIAL']['A2']['grams'] == 407

def test_an_amount_entered_during_a_print_the_guard_cannot_count_is_out_of_date(state):
    """150 g are entered for A3 at layer 10 of a print sent from the phone, which goes on drawing on A3 for 90 layers.
    The entry is out of date at the follower's next look, as it would be had the print started after it was made; the
    amount entered for A1, which the print does not use, stands."""
    spools = [spool(0, typ='PETG'), spool(2)]; job = printing('RUNNING', spools, name='Sent from the phone', task='12', mapping=(2,), layer=10)
    gl.enter('SERIAL', {'label': 'A1', 'type': 'PETG', 'colour': '#101010'}, 400)
    watch = gl.JobWatch('SERIAL', CFG); watch.look(job)                                   # the baseline: the print already runs
    gl.enter('SERIAL', {'label': 'A3', 'type': 'PLA', 'colour': '#101010'}, 150); gl.enter('SERIAL', {'label': 'A1', 'type': 'PETG', 'colour': '#101010'}, 380)
    watch.look(job); watch.look(dict(job, layer_num=90)); watch.look(dict(job, gcode_state='FINISH', layer_num=100))
    book = fg.load_state()['spools']['SERIAL']
    assert book['A3'].get('stale') and 'missed' not in book['A3']
    assert not book['A1'].get('stale') and book['A1']['grams'] == 380 and book['A1']['missed'] == ['12', 'Sent from the phone']
    assert 'A3: entered during "Sent from the phone", a print the guard cannot count: out of date' in (state / 'guard.log').read_text()

def test_an_amount_entered_during_a_print_whose_trays_cannot_be_told_is_out_of_date_whatever_its_tray(state):
    """The printer reports no tray mapping for the print: any loaded spool may be the one it draws on."""
    spools = [spool(0, typ='PETG'), spool(2)]; job = printing('RUNNING', spools, mapping=(), layer=10)
    part(120.0); gl.enter('SERIAL', {'label': 'A1', 'type': 'PETG', 'colour': '#101010'}, 400)
    watch = gl.JobWatch('SERIAL', CFG); watch.look(job)
    gl.enter('SERIAL', {'label': 'A3', 'type': 'PLA', 'colour': '#101010'}, 150); gl.enter('SERIAL', {'label': 'A1', 'type': 'PETG', 'colour': '#101010'}, 380)
    watch.look(job)
    assert all(entry.get('stale') for entry in fg.load_state()['spools']['SERIAL'].values())

def test_an_amount_entered_during_a_print_without_a_profile_has_the_whole_print_taken_off(state):
    """The print was sliced here, 120 g from A3, but its G-code could not be counted by layer: how far it is cannot be
    told. An amount entered while it runs has all 120 g taken off, the low side."""
    fg.remember_slice('Part', 1, {'layers': 100, 'filaments': [{'slot': 1, 'grams': 120.0}]})
    spools = [spool(2)]; job = printing('RUNNING', spools, mapping=(2,), layer=10)
    gl.enter('SERIAL', {'label': 'A3', 'type': 'PLA', 'colour': '#101010'}, 400)
    watch = gl.JobWatch('SERIAL', CFG); watch.look(job)
    gl.enter('SERIAL', {'label': 'A3', 'type': 'PLA', 'colour': '#101010'}, None); gl.enter('SERIAL', {'label': 'A3', 'type': 'PLA', 'colour': '#101010'}, 150)
    watch = gl.JobWatch('SERIAL', CFG); watch.look(job); watch.look(dict(job, gcode_state='FINISH', layer_num=100))
    entry = fg.load_state()['spools']['SERIAL']['A3']
    assert entry['grams'] == 30 and not entry.get('stale') and 'missed' not in entry
    assert 'A3: entered during "Part": all that print needs of it, 120 g (how far it is cannot be told), taken off: 30 g' in (state / 'guard.log').read_text()

def test_a_job_sent_again_under_the_same_name_and_number_is_another_run(state, monkeypatch):
    """A LAN-only printer gives every job the number 0. "Part" (120 g) is printed from A3, entered at 400 g: 280 g.
    Sent again, it comes off the amount at the check while the printer prepares it and when it has started, before
    the follower has looked, though the entry carries its key from the first run; and the follower counts it once."""
    part(120.0); spools = [spool(2)]; job = lambda state, layer=0: printing(state, spools, task='0', mapping=(2,), layer=layer)
    gl.enter('SERIAL', {'label': 'A3', 'type': 'PLA', 'colour': '#101010'}, 400)
    watch = gl.JobWatch('SERIAL', CFG); watch.look(printing('IDLE', spools, name='', task='0'))
    for status in (job('PREPARE'), job('RUNNING', 1), job('FINISH', 100)): watch.look(status)
    assert fg.load_state()['spools']['SERIAL']['A3']['grams'] == 280
    def a3(status): running(monkeypatch, status); res = fg.evaluate(needing(200), CFG, 'Next'); return res['worst'], res['rows'][0]['trays'][0]['grams']
    assert a3(job('FINISH', 100)) == ('OK', 280)
    watch.look(job('PREPARE')); assert a3(job('PREPARE')) == ('SHORT', 160)
    assert a3(job('RUNNING', 1)) == ('SHORT', 160)                                       # it runs, and the follower has not looked yet
    assert watch.look(job('RUNNING', 1)) and fg.load_state()['spools']['SERIAL']['A3']['grams'] == 160 and a3(job('RUNNING', 1)) == ('SHORT', 160)
    assert not watch.look(job('RUNNING', 50)) and not watch.look(job('FINISH', 100)) and a3(job('FINISH', 100)) == ('SHORT', 160)

def test_a_new_follower_knows_the_job_it_counted_was_over(state):
    """The follower saw "Part" end; the guard is started again and finds the same name and number running. Minutes
    later, that is another run, and it is counted; after ten minutes or more nobody knows how many runs there were, and
    the amounts are out of date. A job the guard last saw running, and finds running, is taken for the same run."""
    part(120.0); spools = [spool(2)]; job = lambda state, layer=0: printing(state, spools, task='0', mapping=(2,), layer=layer)
    a3 = lambda: fg.load_state()['spools']['SERIAL']['A3']
    gl.enter('SERIAL', {'label': 'A3', 'type': 'PLA', 'colour': '#101010'}, 400)
    watch = gl.JobWatch('SERIAL', CFG); watch.look(printing('IDLE', spools, name='', task='0')); watch.look(job('RUNNING', 1))
    assert not gl.JobWatch('SERIAL', CFG).look(job('RUNNING', 40)) and a3()['grams'] == 280     # started again mid-print: the same run
    watch.look(job('FINISH', 100))
    assert gl.JobWatch('SERIAL', CFG).look(job('RUNNING', 30)) and a3()['grams'] == 160 and not a3().get('stale')
    gl.JobWatch('SERIAL', CFG).look(job('FINISH', 100))
    assert gl.JobWatch('SERIAL', CFG).look(job('PREPARE'), gap=True) and a3()['stale']        # the computer was off overnight

RED = dict(colour='C12E1F', fid='GFA00')                                                  # a red spool with a tag, beside the one without

def applied(status, label='A1'):
    """The tray as a check sees it, the entered amounts applied; None when it is not loaded."""
    return next((t for t in gl.apply(fg.trays_of(status)[0], 'SERIAL', 'ams' in status) if t['label'] == label), None)

def test_an_amount_typed_while_the_spool_is_on_the_scale_stands_until_the_spool_is_back(state):
    """The spool is taken out of A1 and weighed, and its weight typed while it is still on the scale: the check that
    follows sees A1 empty. The entry stands, applies when the spool is back in A1, and ends when A1 is empty again."""
    loaded, out = printing('IDLE', [spool(0), spool(1, 500, **RED)]), printing('IDLE', [{'id': '0'}, spool(1, 500, **RED)])
    gl.enter('SERIAL', {'label': 'A1', 'type': 'PLA', 'colour': '#101010'}, 640)
    assert applied(out) is None and gl.serials() == ['SERIAL']
    assert applied(loaded)['grams'] == 640 and applied(loaded)['entered']
    assert applied(out) is None and gl.serials() == [] and 'A1: amount ended: the tray is empty' in (state / 'guard.log').read_text()

def test_a_status_that_shows_no_ams_tray_ends_no_entry(state):
    """A report still in pieces (an "ams" part with no trays in it yet) reads as no spool loaded at all: it ends no
    entry, though the entry's tray was seen loaded before. Nor does a status that shows the external spool alone."""
    loaded = printing('IDLE', [spool(0), spool(1, 500, **RED)])
    gl.enter('SERIAL', {'label': 'A1', 'type': 'PLA', 'colour': '#101010'}, 640); assert applied(loaded)['grams'] == 640
    piece = {'gcode_state': 'IDLE', 'ams': {'tray_now': '255', 'version': 4}}
    assert applied(piece) is None and gl.apply([], 'SERIAL', True) == [] and gl.serials() == ['SERIAL']
    assert applied(dict(piece, vt_tray={'id': '254', 'tray_type': 'PLA', 'tray_color': '101010FF'})) is None and gl.serials() == ['SERIAL']

def test_the_follower_ends_an_amount_whose_spool_is_gone_once_two_looks_agree(state, monkeypatch):
    """900 g are entered for A1. The spool is taken out, and a 150 g remnant of the same material and colour put in; no
    plate is sliced in between, so no check sees the empty tray. The follower does: one look at it ends nothing (a
    report may carry a piece of the AMS), the second ends the entry, and the remnant's amount is unknown. Another
    colour in the tray, or a spool the printer measures, end an entry the same way."""
    loaded, out = printing('IDLE', [spool(0), spool(1, 500, **RED)]), printing('IDLE', [{'id': '0'}, spool(1, 500, **RED)])
    def followed(*statuses):
        gl.enter('SERIAL', {'label': 'A1', 'type': 'PLA', 'colour': '#101010'}, 900); watch = gl.JobWatch('SERIAL', dict(CFG, learn=False)); watch.see(loaded)
        for status in statuses: watch.see(status)
        return gl.serials()
    assert followed(out, loaded, out) == ['SERIAL'] and fg.load_state()['spools']['SERIAL']['A1']['loaded']
    assert followed(out, out) == [] and (state / 'guard.log').read_text().count('A1: amount ended: the tray is empty') == 1
    running(monkeypatch, loaded); assert fg.evaluate(needing(300), CFG, 'Next')['rows'][0]['status'] == 'UNKNOWN'
    white = printing('IDLE', [spool(0, colour='FFFFFF'), spool(1, 500, **RED)]); assert followed(white) == ['SERIAL'] and followed(white, white) == []
    tagged = printing('IDLE', [spool(0, 700), spool(1, 500, **RED)]); assert followed(tagged) == ['SERIAL'] and followed(tagged, tagged) == []
    log = (state / 'guard.log').read_text()
    assert 'A1: amount ended: the tray holds PLA #FFFFFF now' in log and 'A1: amount ended: the printer measures the tray now' in log

def test_an_amount_entered_long_before_the_printer_was_first_followed_is_out_of_date(state, monkeypatch):
    """400 g are entered on the card of "check --popup", with no guard running: nothing follows the printer. "Part" is
    printed twice. The guard is started the next day, and its first look, the baseline, counts nothing of what has
    run: the amount is out of date. An amount entered minutes before that look stands."""
    part(120.0); over = printing('FINISH', [spool(0, typ='PETG'), spool(2)], task='9', mapping=(2,), layer=100)
    gl.enter('SERIAL', {'label': 'A3', 'type': 'PLA', 'colour': '#101010'}, 400)
    fg.update_state(lambda st: st['spools']['SERIAL']['A3'].__setitem__('at', time.time() - 86400))
    gl.enter('SERIAL', {'label': 'A1', 'type': 'PETG', 'colour': '#101010'}, 300)        # this one just now
    gl.JobWatch('SERIAL', dict(CFG, learn=False)).see(over); book = fg.load_state()['spools']['SERIAL']
    assert book['A3']['stale'] and not book['A1'].get('stale')
    assert 'entered amounts out of date: A3: entered more than ten minutes before the printer was first followed' in (state / 'guard.log').read_text()
    running(monkeypatch, over); res = fg.evaluate(needing(300), CFG, 'Next')
    assert res['worst'] == 'UNKNOWN' and 'the amount entered for A3 is out of date' in res['rows'][0]['note']

def test_a_job_sent_again_that_ends_before_it_is_seen_running_is_counted_like_any_other(state):
    """On a LAN-only printer "Part" is sent again and cancelled so early that no look sees it run: preparing, then
    failed. Under a new number such a job is taken off in full, the low side; under the number of the run before it
    is no different, and the next guard knows it too."""
    part(120.0); spools = [spool(2)]; job = lambda state, layer=0: printing(state, spools, task='0', mapping=(2,), layer=layer)
    a3 = lambda: fg.load_state()['spools']['SERIAL']['A3']['grams']
    gl.enter('SERIAL', {'label': 'A3', 'type': 'PLA', 'colour': '#101010'}, 400)
    watch = gl.JobWatch('SERIAL', CFG); watch.look(printing('IDLE', spools, name='', task='0'))
    for status in (job('PREPARE'), job('RUNNING', 1), job('FINISH', 100)): watch.look(status)
    assert a3() == 280 and not watch.look(job('PREPARE')) and watch.look(job('FAILED', 1)) and a3() == 160
    assert not watch.look(job('PREPARE')) and gl.JobWatch('SERIAL', CFG).look(job('FINISH', 100)) and a3() == 40

def test_what_a_job_needs_beyond_its_spool_goes_to_the_fullest_spool_of_it_measured_or_entered(state, monkeypatch):
    """A1 and A2 are entered at 30 g each, A3 is the same filament with a tag and reads 900 g. A 50 g job goes to A1:
    the check takes the 20 g beyond A1 off A3, the fullest, while the job prepares. Counting the job leaves A2's entry
    alone and the check goes on taking them off A3: no spool is counted with more than before the job started."""
    part(50.0); spools = lambda a3=900: [spool(0, fid='GFA00'), spool(1, fid='GFA00'), spool(2, a3, fid='GFA00')]
    for label in ('A1', 'A2'): gl.enter('SERIAL', {'label': label, 'type': 'PLA', 'colour': '#101010'}, 30)
    watch = gl.JobWatch('SERIAL', CFG); watch.look(printing('IDLE', spools(), task='1'))
    before = amounts_at(monkeypatch, watch, printing('PREPARE', spools()), needing(100))
    assert before == ('OK', {'A3': 880, 'A2': 30, 'A1': 0})
    assert amounts_at(monkeypatch, watch, printing('RUNNING', spools(), layer=1), needing(100)) == before
    assert amounts_at(monkeypatch, watch, printing('FINISH', spools(880), layer=100), needing(100)) == before
    assert {label: entry['grams'] for label, entry in fg.load_state()['spools']['SERIAL'].items()} == {'A1': 0, 'A2': 30}

def test_after_a_gap_a_printer_that_names_every_job_alike_makes_the_amounts_out_of_date(state):
    """A LAN-only printer gives every job the number 0. "Part" is printed, counted and seen to end; the computer sleeps,
    and "Part" is printed again from the printer's screen, start to finish. On waking the printer shows what it showed
    before: the same name and number, finished. Nothing tells the two runs apart, so after ten minutes or more without
    following the amount is out of date. A printer that numbers its jobs shows the very job that was counted, and the
    amount stands."""
    def after_the_gap(task):
        part(120.0); spools = [spool(2)]; job = lambda state, layer=0: printing(state, spools, task=task, mapping=(2,), layer=layer)
        gl.enter('SERIAL', {'label': 'A3', 'type': 'PLA', 'colour': '#101010'}, 400)
        watch = gl.JobWatch('SERIAL', CFG); watch.look(printing('IDLE', spools, name='', task=task))
        for status in (job('PREPARE'), job('RUNNING', 1), job('FINISH', 100)): watch.look(status)
        gl.JobWatch('SERIAL', CFG).look(job('FINISH', 100), gap=True)
        return fg.load_state()['spools']['SERIAL']['A3']
    numbered = after_the_gap('7'); assert numbered['grams'] == 280 and not numbered.get('stale')
    assert after_the_gap('0')['stale'] and 'it names every job alike' in (state / 'guard.log').read_text()

def test_an_amount_whose_spool_is_first_seen_in_its_tray_half_an_hour_later_is_out_of_date(state):
    """The weight is typed while the spool is on the scale, and the tray is seen empty. A spool of that material and
    colour seen in the tray within 30 minutes is the one that was weighed: the amount applies. One first seen there
    later may be another spool, the weighed one having gone elsewhere: the amount is out of date, at a check and at the
    follower's look alike, and ends like any other when the tray is empty again."""
    loaded, out = printing('IDLE', [spool(0), spool(1, 500, **RED)]), printing('IDLE', [{'id': '0'}, spool(1, 500, **RED)])
    def typed(minutes_ago):
        gl.enter('SERIAL', {'label': 'A1', 'type': 'PLA', 'colour': '#101010'}, 640); assert applied(out) is None
        fg.update_state(lambda st: st['spools']['SERIAL']['A1'].__setitem__('at', time.time() - minutes_ago * 60))
    typed(29); assert applied(loaded)['grams'] == 640
    typed(31); late = applied(loaded); assert late['grams'] is None and late['stale'] and fg.load_state()['spools']['SERIAL']['A1']['stale']
    assert 'A1: amount out of date: its spool was first seen in the tray more than 30 minutes after it was entered' in (state / 'guard.log').read_text()
    assert applied(out) is None and gl.serials() == []                                   # taken out: the entry ends
    typed(0); watch = gl.JobWatch('SERIAL', dict(CFG, learn=False)); watch.see(out)       # the printer is followed, the tray still empty
    typed(31); watch.see(loaded); assert fg.load_state()['spools']['SERIAL']['A1']['stale']
