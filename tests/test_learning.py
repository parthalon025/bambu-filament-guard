"""Learning from previous prints: the record, the model, what it changes, weigh-ins (guard_learn)."""
import json
import random
import time

import pytest

import filament_guard as fg
import guard_learn as gn
import guard_ledger as gl
import guard_stats as gs
from helpers import CFG, tray, fil, kept, running, BLUE, BLUE_JOB, TAG, h2d, benchy, run_job, blue_reads


def tray_c3(status):
    return status['ams']['ams'][0]['tray'][0]

def from_layer(n, change):
    """A fault: change(status, t) -> (status, t) at every RUNNING look from layer n on."""
    return lambda state, layer, status, t: change(status, t) if state == 'RUNNING' and layer >= n else (status, t)

def whole(start, end, printed=100.0, purge=0.0, before=None, layer0=1.0, sound=True):
    return {'sound': sound, 'whole': True, 'start': start, 'end': end, 'before': before, 'layer0': layer0, 'sliced': {'from': 1, 'to': None, 'printed': printed, 'purge': purge}}

def credited(grams, history, weight=1000):
    return gn.corrections('S', [tray('C3', grams, weight=weight)], {'S': {'C3': history}}).get('C3', {}).get('credited', grams)

def random_history(rnd):
    jobs = [{'sound': rnd.random() < 0.8, 'whole': rnd.random() < 0.8, 'start': rnd.uniform(0, 1000), 'end': rnd.uniform(0, 1000), 'before': rnd.choice([None, rnd.uniform(0, 1000)]),
             'layer0': rnd.uniform(0, 5), 'sliced': {'from': 1, 'to': None, 'printed': rnd.uniform(0, 300), 'purge': rnd.uniform(0, 30)}} for _ in range(rnd.randint(0, 12))]
    return {'jobs': jobs, 'dry': [{'at': 0.0, 'reading': rnd.uniform(0, 150)} for _ in range(rnd.randint(0, 4))]}

def scored(fell, n=100.0, at=0.0, learned=None, before=402.0):
    """A sound whole record of a print of n sliced grams (1 g of it in layer 0) from a tray whose reading fell `fell` from
    before layer 1 to the end, 2 g of it before layer 1 (1 g beyond layer 0), with its score."""
    return {'at': at, 'job': f'J{at:g}', 'sound': True, 'whole': True, 'before': before, 'start': before - 2.0, 'end': before - fell, 'layer0': 1.0,
            'sliced': {'from': 1, 'to': None, 'printed': n - 1.0, 'purge': 0.0}, 'spool': {}, 'fed': None,
            'score': {'n': n, 'fell': fell, 'learned': learned or n, 'applied': learned or n}}

def empty_at(reading, at=50.0, spool='S0'):
    return {'at': at, 'job': 'J', 'reading': reading, 'layer': 5, 'needed': 5.0, 'spool': {'id': spool}}

def random_history_14(rnd, labels):
    out = {}
    for label in labels:
        if rnd.random() < 0.3: continue
        jobs = [dict(scored(n * rnd.uniform(0.85, 1.2) + rnd.uniform(-3, 3), n, at=float(i)), sound=rnd.random() < 0.9)
                for i, n in enumerate(rnd.uniform(5, 300) for _ in range(rnd.randint(0, 14)))]
        spool = lambda: {'id': str(rnd.randint(0, 5))}
        out[label] = {'jobs': jobs, 'dry': [dict(empty_at(rnd.uniform(-5, 50), rnd.uniform(0, 20)), spool=spool()) for _ in range(rnd.randint(0, 4))],
                      'weighs': [{'at': rnd.uniform(0, 20), 'reading': rnd.uniform(0, 400), 'weight': rnd.uniform(0, 400), 'spool': spool()} for _ in range(rnd.randint(0, 3))],
                      'seen': [{'at': rnd.uniform(0, 20), 'reading': rnd.uniform(0, 100), 'spool': {'id': str(rnd.randint(0, 9))}} for _ in range(rnd.randint(0, 6))]}
    return out

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
    assert all(not m[x]['used'] for x in ('rate', 'pre') for m in [gn.rules({'jobs': [job] * 10})])           # and nothing is learned from it

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

def test_each_number_is_used_only_past_its_threshold():
    at = time.mktime((2026, 11, 19, 14, 0, 0, 0, 0, -1))
    assert not gn.rules({})['dry']['used'] and gn.rules({'dry': [{'at': at, 'reading': 20.0}]})['dry']['value'] == 20.0         # dry: from the first
    assert gn.rules({'dry': [{'at': at, 'reading': r} for r in (30.0, 10.0, 25.0, 15.0)]})['dry']['value'] == 25.0              # the largest of the last 3
    rate = lambda jobs: gn.rules({'jobs': jobs})['rate']
    assert not rate([whole(500, 320, 200)] * 2)['used'] and rate([whole(500, 320, 200)] * 2)['why'] == '2 of 3 prints'
    assert rate([whole(500, 419, 90)] * 3)['why'] == '270 of 300 g'
    assert not rate([whole(500, 350.5, 150)] * 3)['used']                                                                       # 0.997: noise
    assert rate([whole(500, 365, 150)] * 3)['used'] and rate([whole(500, 365, 150)] * 3)['value'] == pytest.approx(0.9)
    assert not rate([whole(500, 365, 150)] * 2 + [whole(500, 365, 150, sound=False)])['used']                                   # unsound jobs do not count
    assert rate([whole(500, 365, 150)] * 3 + [whole(500, 499, 4)])['jobs'] == 3                                                 # nor jobs of less than 5 g
    pre = lambda jobs: gn.rules({'jobs': jobs})['pre']
    assert not pre([whole(498, 458, before=501)])['used'] and pre([whole(498, 458, before=501)])['why'] == '1 of 2 prints'
    assert pre([whole(498, 458, before=501), whole(498, 458, before=500)])['used'] and pre([whole(498, 458, before=501), whole(498, 458, before=500)])['value'] == 2.0
    assert pre([whole(498, 458, before=498)] * 2)['value'] == 0.0                                                               # never below 0

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

def test_learned_says_what_was_learned_and_forget_empties_it(state, monkeypatch, capsys):
    def no_studio(): raise OSError('no Bambu Studio here')
    monkeypatch.setattr(fg, 'bs_conf', no_studio)
    benchy(); rec = gn.Recorder('SERIAL'); run_job(rec)
    gn.keep('SERIAL', 'C3', 'dry', {'at': time.mktime((2026, 11, 19, 14, 0, 0, 0, 0, -1)), 'job': 'Benchy', 'reading': 20.0, 'layer': 9, 'needed': 1.0, 'spool': {}}, gn.KEEP_DRY)
    assert fg.main(['learned']) == 0
    out = capsys.readouterr().out
    assert out.startswith('Learning from prints is on\nSERIAL\n  reserve: the reserve is the fixed one: 8 more scored prints and 2 more readings at empty in a tray that has one measure it\n')
    assert '  C3: 1 print recorded, 1 sound, 1 scored; 1 run-dry\n    forecast of the fall, off by on average: learned 1.0 g, without learning 1.0 g\n' in out
    assert 'rate  1.000 g per sliced gram (its own 1.000 over 1 print)' in out and 'pre   1.0 g (its own 1.0 g over 1 print)' in out
    assert 'empty 20 g: the most it reads with its spool empty (from its own spools); seen: 20 g ran dry; 1 spool still feeding at 462 g or more' in out
    assert 'ran dry 19 Nov 14:00 at layer 9 of "Benchy", reading 20 g, 1.0 g still to go' in out and 'per gram printed / purged: not enough to tell' in out
    assert 'margin not in use: 8 more scored prints on the printer, 2 more readings at empty in a tray that has one' in out
    assert fg.main(['learned', '--forget']) == 0 and gn.load() == {}

def test_printed_and_purged_are_told_apart_only_when_the_jobs_differ_enough():
    jobs = [whole(500, 500 - (0.9 * p + 0.5 * q), p, q) for p, q in ((100, 5), (100, 30), (80, 40))]
    assert gn.printed_and_purged(jobs) == pytest.approx((0.9, 0.5)) and gn.printed_and_purged(jobs[:2]) is None
    assert gn.printed_and_purged([whole(500, 400, 100, 5)] * 3) is None                                                         # one purge share: cannot tell

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

def test_a_rate_below_0_9_is_readings_that_lagged_and_is_not_used():
    assert not gn.rules({'jobs': [whole(500, 372.5, 150)] * 3})['rate']['used']                                               # 0.85
    assert gn.rules({'jobs': [whole(500, 372.5, 150)] * 3})['rate']['why'] == '0.85 g per gram is too far out to believe'

def test_a_learned_file_of_1_3_0_loads_and_counts_as_1_3_0_did(state, monkeypatch, capsys):
    """The owner's installed 1.3.0 is writing learned.json now: records without scores, run-dries marked contradicted."""
    old = {'SERIAL': {'C3': {'jobs': [dict(whole(500, 365, 150, before=503.0), at=float(i), job='Benchy', spool={'id': TAG}, samples=[[1, 500], [10, 365]], end_first=365, why=[]) for i in range(3)],
                             'dry': [empty_at(20.0, 0.5, ''), dict(empty_at(45.0, 0.1, ''), contradicted={'at': 0.2, 'job': 'Benchy', 'reading': 30.0})]}}}
    (state / 'learned.json').write_text(json.dumps(old))
    trays = [dict(tray('C3', 500, weight=1000), spool=TAG)]
    now, then = gn.corrections('SERIAL', trays)['C3'], gn.corrections('SERIAL', trays, plain_rules=True)['C3']
    assert now['credited'] == then['credited'] == pytest.approx(1000 - 500 / 0.9) and now['evidence'] == then['evidence'] and now['pre'] == then['pre'] == 2.0
    monkeypatch.setattr(fg, 'bs_conf', lambda: (_ for _ in ()).throw(OSError('no Bambu Studio here')))
    assert fg.main(['learned']) == 0 and '  C3: 3 prints recorded, 3 sound, 0 scored; 2 run-dries' in capsys.readouterr().out
    benchy(); run_job(gn.Recorder('SERIAL'), t=10.0)
    h = gn.load()['SERIAL']['C3']
    assert len(h['jobs']) == 4 and h['jobs'][-1]['score']['n'] == pytest.approx(41.0) and h['seen'][0]['reading'] == 462.0 and h['dry'] == old['SERIAL']['C3']['dry']

def test_every_whole_print_is_scored_by_the_model_as_it_stood_before(state):
    benchy(); rec = gn.Recorder('SERIAL'); t = run_job(rec)
    [job] = gn.load()['SERIAL']['C3']['jobs']
    assert job['score'] == {'n': pytest.approx(41.0), 'fell': 42.0, 'learned': pytest.approx(41.0), 'applied': pytest.approx(41.0)} and job['fed'] == 462.0
    t = run_job(rec, per_layer=4.4, t=t + 100)                                             # this print takes 10 % more reading per gram
    second = gn.load()['SERIAL']['C3']['jobs'][1]['score']
    assert (second['learned'], second['fell']) == (pytest.approx(41.0 * 1.0 + 1.0), pytest.approx(46.0))   # forecast from the first alone: rate 1, 1 g before layer 1
    run_job(rec, end='FAILED', t=t + 100)
    assert 'score' not in gn.load()['SERIAL']['C3']['jobs'][2]

def test_the_reserve_is_measured_once_there_are_nine_scored_prints_and_repeated_readings_at_empty(state, monkeypatch):
    errors = (0, 1, -1, 0, 2, 0, -1, 1, 0)
    gn.save({'SERIAL': {'C3': {'jobs': [scored(101.0 + e, at=float(i)) for i, e in enumerate(errors)], 'dry': [empty_at(r, 50.0 + i, f'S{i}') for i, r in enumerate((20.0, 22.0, 18.0))]}}})
    running(monkeypatch, blue_reads(130)); res = fg.evaluate(BLUE, CFG, 'Benchy'); row = res['rows'][0]
    a = 20 + gs.t_quantile(0.9, 2) * 2 * (4 / 3) ** 0.5                                       # three readings at empty, 2 g apart: one more spool's bound
    assert row['status'] == 'OK' and row['measured'] and row['reserve'] == 5.0 and row['trays'][0]['grams'] == pytest.approx(130 - a)
    assert res['printer']['reserve'] == 'the reserve is measured from 9 scored prints and 3 readings at empty' and row['need'] > 85.3 + 1.0
    assert "C3: its reading falls 1.002 g per sliced gram (9 of its prints, drawn to the printer's), which the need is counted by" in row['note']
    off = fg.evaluate(BLUE, dict(CFG, measured_reserve=False), 'Benchy')                     # the setting off: the fixed reserve, 30 g
    assert off['rows'][0]['status'] == 'TIGHT' and not off['rows'][0]['measured'] and off['printer']['reserve'] == 'the reserve is the fixed one (measuring it is off)'
    gn.save({'SERIAL': {'C3': {'jobs': [scored(110.0 + e, at=float(i)) for i, e in enumerate(errors)], 'dry': [empty_at(r, 50.0 + i, f'S{i}') for i, r in enumerate((20.0, 22.0, 18.0))]}}})
    row = fg.evaluate(BLUE, CFG, 'Benchy')['rows'][0]                                          # forecasts that fell up to 12 g short of 100 g
    assert row['measured'] and row['reserve'] == pytest.approx(12 / 120 * (85.3 + gn.MARGIN_K))
    running(monkeypatch, blue_reads(125)); row = fg.evaluate(BLUE, CFG, 'Benchy')['rows'][0]
    assert row['status'] == 'TIGHT' and 'g to spare, and past prints put the figure up to 11 g out' in row['note']
    gn.save({'SERIAL': {'C3': {'jobs': [scored(90.0, at=float(i)) for i in range(9)], 'dry': [empty_at(r, 50.0 + i, f'S{i}') for i, r in enumerate((20.0, 22.0, 18.0))]}}})
    assert fg.evaluate(BLUE, CFG, 'Benchy')['rows'][0]['reserve'] == 5.0                     # forecasts that fell long: never below the floor
    gn.save({'SERIAL': {'C3': {'jobs': [scored(101.0, at=float(i)) for i in range(9)]}, 'B1': {'dry': [empty_at(r, 50.0 + i, f'S{i}') for i, r in enumerate((20.0, 22.0, 18.0))]}}})
    assert not fg.evaluate(BLUE, CFG, 'Benchy')['rows'][0]['measured']                         # readings at empty of another tray only: the fixed reserve

def test_no_answer_is_looser_than_1_3_0s_until_the_reserve_is_measured():
    """Over random histories: a tray is never counted above its reading, a need never below the slicer's, a measured
    reserve never below its floor; and until the printer has earned the measured reserve, or with it off, no result is
    looser than 1.3.0's own rules give."""
    rnd = random.Random(1889)
    for _ in range(600):
        labels = rnd.sample(['A1', 'A2', 'B1', 'B2'], rnd.randint(1, 3))
        trays = [dict(tray(label, rnd.choice([None, rnd.randint(0, 600)]), weight=rnd.choice([None, 1000])), spool=str(rnd.randint(0, 5))) for label in labels]
        history, cfg = {'S': random_history_14(rnd, labels)}, dict(CFG, measured_reserve=rnd.random() < 0.7)
        est = gn.estimates(history['S'])
        earned = cfg['measured_reserve'] and est['printer']['scored'] >= gn.SCORED_MIN and est['printer']['df'] >= gn.SPREAD_DF
        plain = fg.credit([dict(t) for t in trays], {})
        old = fg.credit([dict(t) for t in trays], {}, gn.corrections('S', trays, history, plain_rules=True))
        new = fg.credit([dict(t) for t in trays], {}, gn.corrections('S', trays, history, cfg))
        for p, q in zip(plain, new): assert (p['grams'] is None and q['grams'] is None) or 0 <= q['grams'] <= p['grams']
        need = fil(max(1.0, rnd.choice([rnd.uniform(1, 400), max(t['grams'] or 0 for t in trays) - rnd.uniform(0, 40)])))
        for times in (1, 3):
            a, b = fg.assess(need, old, cfg, times), fg.assess(need, new, cfg, times)
            assert b.get('sliced') is None or b['need'] >= b['sliced'] == need['grams'] * times
            if b.get('measured'): assert earned and b['reserve'] >= max(5.0, 0.02 * b['need']) - 1e-9
            if not earned: assert fg.SEVERITY[b['status']] >= fg.SEVERITY[a['status']] and b['need'] >= a['need'] - 1e-9

def test_a_tray_borrows_the_printers_rate_from_its_first_print_and_one_odd_print_is_clipped():
    h = {'A1': {'jobs': [scored(107.0, at=float(i)) for i in range(3)]}}                     # reading fell 105 g for 99 g sliced from layer 1
    w = sum(gn.DISCOUNT ** i for i in range(3))
    assert gn.estimates(h)['blank']['rho'] == pytest.approx((105 * w + 50) / (99 * w + 50))   # a tray with no print yet: the printer's, drawn to 1 by 50 g
    h['A2'] = {'jobs': [scored(101.0, at=5.0)]}                                                # one print of its own that fell true
    est = gn.estimates(h)
    assert est['printer']['k_rho'] == 1.0 and est['trays']['A2']['rho'] == pytest.approx((1.0 + est['printer']['rho']) / 2)
    h['A1']['jobs'].append(scored(200.0, at=3.0))                                              # an odd print: the reading fell twice what was sliced
    assert gn.estimates(h)['trays']['A1']['ratio'][-1][1] == gn.CLIP_FIXED[1]

def test_old_prints_count_for_less_than_new_ones():
    jobs = [scored(111.0, at=float(i)) for i in range(20)] + [scored(101.0, at=20.0 + i) for i in range(20)]   # a rate of 1.10, then of 1.00
    assert gn.estimates({'A1': {'jobs': jobs}})['trays']['A1']['rho_own'][0] == pytest.approx(1.0264, abs=1e-3)   # equal weights: 1.05

def test_a_learned_forecast_that_does_worse_is_set_aside_and_taken_back_only_when_it_no_longer_does():
    worse = [{'n': 100.0, 'fell': 100.0, 'learned': 105.0}] * 5
    better = [{'n': 100.0, 'fell': 103.0, 'learned': 103.0}]
    assert gn.reference(worse) == (True, 5, 5)                                                # 5 of 5: p = 1/32
    assert gn.reference(worse + better * 3) == (True, 5, 8)                                   # 5 of 8 is p = 0.36: still aside
    assert gn.reference(worse + better * 5) == (False, 5, 10)                                 # 5 of 10: p = 0.62, back
    h = {'C3': {'jobs': [dict(scored(101.0, at=float(i)), score={'n': 100.0, 'fell': 101.0, 'learned': 110.0, 'applied': 110.0}) for i in range(5)]
                         + [scored(111.0, at=10.0 + i) for i in range(5)]}}
    fix = gn.corrections('S', [tray('C3', 500)], {'S': h})['C3']                               # the rate the forecasts did worse with is not applied
    assert 'rho' not in fix and gn.estimates(h)['trays']['C3']['aside']

def test_a_change_in_a_trays_readings_restarts_what_is_learned_of_it(state, monkeypatch, capsys):
    benchy(); rec, t = gn.Recorder('SERIAL'), 0.0
    for k in range(12): t = run_job(rec, t=t + 100, task=str(k))
    assert 'changes' not in gn.load()['SERIAL']['C3']
    for k in range(3): t = run_job(rec, per_layer=3.6, t=t + 100, task=str(20 + k))          # serviced: the reading now falls 10 % less
    h = gn.load()['SERIAL']['C3']
    [change] = h['changes']
    assert change['at'] == h['jobs'][12]['at'] and gn.estimates({'C3': h})['trays']['C3']['start'] == change['at']
    assert len(gn.estimates({'C3': h})['trays']['C3']['rated']) == 3
    monkeypatch.setattr(fg, 'bs_conf', lambda: (_ for _ in ()).throw(OSError('no Bambu Studio here')))
    fg.main(['learned']); assert 'a change in its readings was noticed on' in capsys.readouterr().out

def test_a_gauge_that_counts_too_little_is_counted_lower_even_with_a_reading_at_empty():
    """Found by the simulation: a reading at empty from before says nothing of a spool fed since by a gauge that
    counts 5 % short; W - (W - G) / rho stays a floor under a."""
    h = {'C3': {'jobs': [scored(0.95 * 99 + 2, at=float(i)) for i in range(2)], 'dry': [empty_at(5.0)]}}
    rho = gn.estimates(h)['trays']['C3']['rho']
    fix = gn.corrections('S', [tray('C3', 500, weight=1000)], {'S': h})['C3']
    assert rho < 0.99 and fix['credited'] == pytest.approx(1000 - 500 / rho) and 'reads more than the spool holds' in fix['evidence']

def test_the_largest_of_a_few_readings_at_empty_does_not_measure_the_reserve():
    """Found by the simulation: with n readings at empty the largest covers one more spool n / (n + 1) of the time, so
    spools that differ ran dry after "enough". The reserve is measured only once readings repeat within a tray, which
    gives their spread, and a is then a bound for one more spool."""
    base = [scored(101.0, at=float(i)) for i in range(9)]
    apart = {'A1': {'jobs': base, 'dry': [empty_at(20.0)]}, 'A2': {'dry': [empty_at(22.0)]}, 'A3': {'dry': [empty_at(18.0)]}}
    est = gn.estimates(apart)
    assert est['printer']['df'] == 0 and not gn.measured(est, est['trays']['A1'])
    together = {'A1': {'jobs': base, 'dry': [empty_at(r, 50.0 + i, f'S{i}') for i, r in enumerate((20.0, 22.0, 18.0))]}}
    t = gn.estimates(together)['trays']['A1']
    assert gn.measured(gn.estimates(together), t) and t['a'] == pytest.approx(20 + gs.t_quantile(0.9, 2) * 2 * (4 / 3) ** 0.5) and t['a'] > 22

def test_spools_seen_feeding_lower_outweigh_a_run_dry_once_there_are_enough(state):
    """1.3.0 dropped a run-dry at once when a later spool fed lower; 1.4.0 weighs the two (Kaplan-Meier): a few keep
    the run-dry, enough spools seen feeding lower outweigh it."""
    benchy()
    dry_at_7 = from_layer(6, lambda s, t: (dict(h2d(s['gcode_state'], s['layer_num'], 20.0 if s['layer_num'] == 6 else None), print_error=50348044, hms=[{'attr': 1, 'code': 2}]), t))
    run_job(gn.Recorder('SERIAL'), fault=dry_at_7)
    [dry] = gn.load()['SERIAL']['C3']['dry']
    assert (dry['reading'], dry['print_error'], dry['hms']) == (20.0, 50348044, [{'attr': 1, 'code': 2}])
    run_job(gn.Recorder('SERIAL'), before=50.0, lost=1.0, per_layer=4.0, t=time.mktime((2026, 11, 21, 12, 0, 0, 0, 0, -1)))        # a later spool in C3 still feeds at 49, 45, ... 13 g
    h = gn.load()['SERIAL']['C3']
    assert not gn.rules(h)['dry']['used'] and 'on 21 Nov a spool in it still gave filament at 13 g' in gn.contradicted(h['dry'][0], h)   # 1.3.0's rule
    assert gn.estimates({'C3': h})['trays']['C3']['a'] == 20.0                                 # two observations: the run-dry stands
    for i in range(10): gn.keep('SERIAL', 'C3', 'seen', {'at': 2e9 + i, 'reading': 8.0 + i, 'spool': {'id': f'LATER{i}'}, 'job': 'Benchy'})
    h = gn.load()['SERIAL']['C3']
    assert gn.estimates({'C3': h})['trays']['C3']['a'] is None and gn.corrections('SERIAL', [dict(tray('C3', 105, weight=1000), spool=TAG)], {'SERIAL': {'C3': h}}) == {}

def test_a_tray_with_no_reading_at_empty_of_its_own_takes_the_printers():
    est = gn.estimates({'A1': {'dry': [empty_at(20.0)]}, 'A2': {'jobs': [scored(101.0)]}, 'A3': {'dry': [empty_at(25.0, spool='S9')]}})
    assert (est['trays']['A2']['a'], est['trays']['A2']['a_from']) == (25.0, 'printer') and est['blank']['a'] == 25.0
    assert (est['trays']['A3']['a'], est['trays']['A1']['a']) == (25.0, 25.0)                # never less than the printer's while it has few of its own

def test_one_odd_print_is_not_a_change_and_two_that_agree_are():
    steady = [{'n': 100.0, 'fell': 100.0 + e, 'learned': 100.0} for e in (0, 1, -1, 0, 1, 0, -1, 0, 1, 0, 0, -1)]
    odd = {'n': 100.0, 'fell': 110.0, 'learned': 100.0}
    assert gn.change_found(steady + [odd]) is None and gn.change_found(steady + [odd] + steady[:2]) is None
    assert gn.change_found(steady + [odd, odd]) == len(steady)

def test_with_learning_off_nothing_learned_is_read(state, monkeypatch):
    def never(*a, **k): raise AssertionError('learning was read')
    for name in ('corrections', 'reserve_words', 'weigh_offer', 'estimates', 'load'): monkeypatch.setattr(gn, name, never)
    running(monkeypatch, blue_reads(110)); res = fg.evaluate(BLUE, dict(CFG, learn=False), 'Benchy')
    assert res['rows'][0]['status'] == 'TIGHT' and res['printer']['reserve'] is None and 'weigh' not in res and not res['rows'][0]['measured']

def test_a_weigh_in_is_asked_for_only_when_it_would_settle_the_answer(state):
    row = fg.assess(fil(85.3), fg.credit([dict(tray('C3', 110, weight=1000), spool=TAG)], {}), CFG)
    assert row['status'] == 'TIGHT' and gn.weigh_offer('S', [row], CFG, now=1e6)['label'] == 'C3'   # 25 g to spare: enough unless the reading runs high
    for quiet in (dict(CFG, ok_seconds=0), dict(CFG, learn=False)): assert gn.weigh_offer('S', [row], quiet, now=1e6) is None
    hopeless = fg.assess(fil(105.0), fg.credit([dict(tray('C3', 108, weight=1000), spool=TAG)], {}), CFG)
    assert hopeless['status'] == 'TIGHT' and gn.weigh_offer('S', [hopeless], CFG, now=1e6) is None   # 3 g to spare even if the reading is true
    entered = fg.assess(fil(85.3), fg.credit([dict(tray('C3', 110, weight=1000), entered=1.0)], {}), CFG)
    assert gn.weigh_offer('S', [entered], CFG, now=1e6) is None                                         # an amount entered by hand: nothing to weigh against
    gn.asked('S', 'C3', now=1e6)
    other = fg.assess(fil(85.3), fg.credit([dict(tray('B1', 110, weight=1000))], {}), CFG)
    assert gn.weigh_offer('S', [other], CFG, now=1e6 + 3600) is None                                    # once a day in all
    assert gn.weigh_offer('S', [row], CFG, now=1e6 + 2 * 86400) is None and gn.weigh_offer('S', [other], CFG, now=1e6 + 2 * 86400)['label'] == 'B1'   # once a week a tray
    assert gn.weigh_offer('S', [row], CFG, now=1e6 + 8 * 86400)['label'] == 'C3'

def test_a_weighed_spool_is_counted_with_no_more_than_it_held(state):
    spool = {'id': TAG, 'type': 'PLA', 'colour': '#0A2989', 'fid': 'GFA00', 'weight': 1000.0}
    c3 = lambda g, tag=TAG: gn.corrections('SERIAL', [dict(tray('C3', g, colour='#0A2989', weight=1000.0), spool=tag)]).get('C3', {})
    gn.weigh('SERIAL', 'C3', 300.0, 280.0, spool, now=1e6)
    assert c3(250)['credited'] == 230 and 'its spool held 20 g less than it read' in c3(250)['evidence'] and c3(250, 'ANOTHER SPOOL') == {}
    assert gn.estimates(gn.load()['SERIAL'])['trays']['C3']['exact'] == []                    # weighed half full: says nothing of the reading at empty
    gn.weigh('SERIAL', 'C3', 300.0, 320.0, spool, now=1e6 + 1)
    assert c3(250) == {}                                                                       # it held more than it read: never counted above the reading
    gn.weigh('SERIAL', 'C3', 200.0, 190.0, spool, now=1e6 + 2)                                 # a quarter full or less: the reading at empty, 10 g
    assert [(k, v) for k, v, _ in gn.estimates(gn.load()['SERIAL'])['trays']['C3']['exact']] == [('weighed', 10.0)] and c3(250)['credited'] == 240
