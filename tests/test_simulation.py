"""A simulated printer whose gauges are known, run through the guard's own recorder and check.

python tests/test_simulation.py prints the tables of docs/learning.md (some minutes)."""
if __name__ == '__main__':
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import contextlib
import itertools
import json
import math as _math
import random
import statistics
import tempfile

import pytest

import filament_guard as fg
import guard_learn as gn
from helpers import kept


SIM_COLOURS = ['0A2989', 'C12E1F', '00AE42', 'F4EE2A']

SIM_SCENARIOS = {
    'accurate gauge': [{}] * 4,
    '20 g at empty': [{'short': 20.0}] * 4,
    'rate 0.97': [{'r': 0.97}] * 4,
    'noisy': [{'spread': 0.03, 'fill': 8.0}] * 4,
    'a change halfway': [{'change': 0.95}] * 4,                   # from the middle on the gauges count 0.95 g per gram fed
    'one false run-dry': [{}] * 4,                                # a spool reading 50 g or less taken out while it feeds, once
    'a mix across trays': [{}, {'short': 20.0}, {'r': 0.97}, {'spread': 0.03, 'fill': 8.0}],
    # The two cases the review of 1.4.0 found the measured reserve cannot see (docs/learning.md); not among the base ones.
    '25 g more at empty from halfway': [{'shift': 25.0}] * 4,     # spools loaded from the middle on hold 25 g less than their tag says
    'one tray unlike the others': [{}, {}, {}, {'short': (0.0, 50.0)}],   # tray 4's spools hold anything from 0 to 50 g less than their tag
}
BASE = list(SIM_SCENARIOS)[:7]

@contextlib.contextmanager
def state_in_memory():
    """Keeps the state folder's JSON files in memory while a simulation runs, as the text a file would hold, and the log
    nowhere: a run of 300 prints writes the history 300 times, and most of its time went to that."""
    files, real = {}, (fg.read_json, fg.write_json, fg.log)
    fg.read_json = lambda path, empty: json.loads(files[path]) if path in files else empty
    fg.write_json = lambda path, data, **how: files.__setitem__(path, json.dumps(data, **how))
    fg.log = lambda msg: None
    try: yield
    finally: fg.read_json, fg.write_json, fg.log = real

def simulate(scenario, prints, seed=1889, cfg=None):
    """Runs `prints` prints of 1 or 2 filaments on four trays, 5 hours apart: 5 to 250 g each, or (4 times in 10) 60 to 105 %
    of what the tray reads, as when a spool is printed down, so that many checks fall near empty. Before each print the user
    swaps any spool that reads less than the print needs (+ 5 g), and weighs a spool when the card asks; whatever the
    answer, the print then runs, and a spool that runs out mid-print is replaced. cfg: the settings that differ from
    those the guard comes with. Each filament of each print is answered twice on the same history: by 1.3.0's rules alone
    ('rules') and by the guard as set ('guard'). Returns the counts of both against what happened; how many answers were
    looser than the rules' ('looser'); those the guard gave as "enough" where the rules did not ('loosened': the print,
    the tray, whether it then ran dry); how many answers used the measured reserve ('measured'); and the print at which
    each learned number first came into use ('first')."""
    rnd, serial, ids = random.Random(seed), 'SIM', itertools.count(1)
    cfg = dict(fg.DEFAULTS, **(cfg or {}))
    gauges, false_dry = SIM_SCENARIOS[scenario], [scenario == 'one false run-dry']
    def spool(i, k=0):                                                # full, or half the time part used (moved from elsewhere); k: the print it is loaded for
        short = gauges[i].get('short', 0.0)
        if isinstance(short, tuple): short = rnd.uniform(*short)
        if k >= prints // 2: short += gauges[i].get('shift', 0.0)
        held = 1000.0 - short + rnd.gauss(0, gauges[i].get('fill', 2.0))
        left = held if rnd.random() < 0.5 else rnd.uniform(50.0, 900.0)
        return {'id': f'51{next(ids):030d}', 'left': left, 'gauge': 1000.0 - gauges[i].get('r', 1.0) * (held - left)}
    trays = [spool(i) for i in range(4)]
    def reading(i): return max(0, round(trays[i]['gauge']))
    def status(state, layer, task, title, layers, mapping, empty=()):
        tl = [{'id': str(i)} if i in empty else {'id': str(i), 'tray_type': 'PLA', 'tray_color': SIM_COLOURS[i] + 'FF', 'tray_info_idx': 'GFA00',
               'remain_g': reading(i), 'remain': -1, 'tray_weight': '1000', 'tray_uuid': trays[i]['id']} for i in range(4)]
        return {'gcode_state': state, 'subtask_name': title, 'task_id': str(task), 'layer_num': layer, 'total_layer_num': layers, 'mapping': mapping, 'ams': {'ams': [{'id': '0', 'tray': tl}]}}
    def take(i, grams, k):
        """Feeds grams from tray i; returns what the spool could not give (it ran out)."""
        r = gauges[i]['change'] if gauges[i].get('change') and k >= prints // 2 else gauges[i].get('r', 1.0)
        given = min(grams, max(0.0, trays[i]['left']))
        trays[i]['left'] -= given; trays[i]['gauge'] -= r * given
        return grams - given
    def check(plate, idle, history, plain=False):
        trs = fg.trays_of(idle)[0]
        fg.credit(trs, {}, gn.corrections(serial, trs, history, cfg, plain))
        return [fg.assess(f, trs, cfg) for f in plate['filaments']]
    counts = {m: {'ok': 0, 'ok_dry': 0, 'fit': 0, 'fit_warned': 0, 'rows': 0} for m in ('rules', 'guard')}
    first, rec, weighed, misses, loosened, looser, measured = {}, gn.Recorder(serial), 0, [], [], 0, 0
    for k in range(prints):
        t = k * 5 * 3600.0
        idx = rnd.sample(range(4), 2 if rnd.random() < 0.3 else 1)
        need = {i: round(min(250.0, reading(i) * rnd.uniform(0.6, 1.05)) if rnd.random() < 0.4 and reading(i) > 10    # printing the spool down
                         else _math.exp(rnd.uniform(_math.log(5), _math.log(250))), 1) for i in idx}
        for i in idx:
            if reading(i) < need[i] + 5: trays[i] = spool(i, k)
        title, layers = f'P{k}', 8
        plate = {'printer_model': 'Bambu Lab H2D', 'time': '', 'layers': layers,
                 'filaments': [{'slot': n + 1, 'type': 'PLA', 'fid': 'GFA00', 'colour': '#' + SIM_COLOURS[i], 'name': 'PLA Basic', 'grams': need[i], 'nozzle': None} for n, i in enumerate(idx)]}
        fg.remember_slice(title, 1, plate, kept({n + 1: [0.5] + [(need[i] - 0.5) / layers] * layers for n, i in enumerate(idx)}))
        idle, history = status('IDLE', 0, k, '', 0, None), gn.load()
        rows = check(plate, idle, history)
        offer = gn.weigh_offer(serial, rows, cfg, now=t)
        if offer:
            i = int(offer['label'][1]) - 1; weighed += 1
            gn.asked(serial, offer['label'], now=t)
            gn.weigh(serial, offer['label'], offer['reading'], round(trays[i]['left']), {'id': trays[i]['id'], 'type': 'PLA', 'colour': '#' + SIM_COLOURS[i], 'fid': 'GFA00', 'weight': 1000.0}, now=t)
            history = gn.load(); rows = check(plate, idle, history)
        old = check(plate, idle, history, plain=True)
        kappa = {i: 1 + rnd.gauss(0, gauges[i].get('spread', 0.003)) for i in idx}
        for i, new, was in zip(idx, rows, old):
            fits = trays[i]['left'] >= 1.5 + kappa[i] * need[i]
            for method, row in (('rules', was), ('guard', new)):
                c = counts[method]; c['rows'] += 1
                if row['status'] == 'OK': c['ok'] += 1; c['ok_dry'] += not fits
                if fits: c['fit'] += 1; c['fit_warned'] += row['status'] in ('TIGHT', 'SPLIT', 'SHORT')
            looser += fg.SEVERITY[new['status']] < fg.SEVERITY[was['status']]
            if new['status'] == 'OK' and was['status'] != 'OK': loosened.append((k, f'A{i + 1}', not fits))
            if new['status'] == 'OK' and not fits:
                misses.append({'print': k, 'tray': f'A{i + 1}', 'reads': reading(i), 'left': round(trays[i]['left'], 1), 'takes': round(1.5 + kappa[i] * need[i], 1), 'need': round(new['need'], 1),
                               'counted': round(new['best'], 1), 'reserve': round(new['reserve'], 1), 'measured': new.get('measured'), 'rules': was['status']})
            if new.get('measured'): measured += 1; first.setdefault('measured reserve', k)
        numbers = {'rho': 'rho', 'pre': 'pre', 'a': 'a', 'margin': 'q'}
        if not set(numbers) <= set(first):
            est = gn.estimates(history.get(serial, {}))
            for name, key in numbers.items():
                if any(t[key] is not None for t in est['trays'].values()): first.setdefault(name, k)
        mapping = list(idx)
        def look(state, layer, empty=()):
            nonlocal tt
            rec.look(status(state, layer, k, title, layers, mapping, empty), tt); tt += 3
        tt = t - 3
        look('IDLE', 0)                                                             # the follower was already looking: no gap
        look('PREPARE', 0)
        for i in idx: take(i, 1.5 + kappa[i] * 0.5, k)
        look('RUNNING', 0)
        for n in range(1, layers + 1):
            look('RUNNING', n)
            for i in idx:
                if false_dry[0] and n == layers // 2 and 10 < reading(i) <= 50:      # taken out while it feeds
                    false_dry[0] = False; look('RUNNING', n, {i}); look('RUNNING', n, {i}); trays[i] = spool(i, k)
                short = take(i, kappa[i] * (need[i] - 0.5) / layers, k)
                if short > 0:                                                         # ran out: the printer pauses, a new spool goes in
                    look('PAUSE', n, {i}); look('PAUSE', n, {i}); trays[i] = spool(i, k); take(i, short, k)
        for _ in range(3): tt += 27; look('FINISH', layers)
    return {'counts': counts, 'looser': looser, 'loosened': loosened, 'measured': measured, 'first': first, 'weighed': weighed, 'misses': misses}

@pytest.fixture
def memory(state):
    with state_in_memory(): yield

@pytest.mark.simulation
@pytest.mark.parametrize('scenario', list(SIM_SCENARIOS))
def test_with_the_settings_as_they_come_no_simulated_answer_is_looser_than_1_3_0s_rules(memory, scenario):
    """In every scenario, the two the measured reserve cannot see among them: no answer looser than the same history
    gets by 1.3.0's rules, none loosened to "enough", and so no more "enough" answers after which the print ran dry."""
    out = simulate(scenario, 70)
    rules, guard = out['counts']['rules'], out['counts']['guard']
    assert (out['looser'], out['loosened'], out['measured']) == (0, [], 0) and guard['ok_dry'] <= rules['ok_dry'] and guard['ok'] <= rules['ok']
    assert guard['rows'] == rules['rows'] >= 70 and {'rho', 'pre', 'a', 'margin'} <= set(out['first'])    # and the guard had learned: it was not as without learning

@pytest.mark.simulation
@pytest.mark.parametrize('scenario', BASE)
def test_with_the_measured_reserve_on_at_most_one_in_ten_of_the_answers_it_loosened_ran_dry(memory, scenario):
    """What docs/learning.md states of the base scenarios, on one seed of 150 prints each. Counted over the answers the
    measured reserve changed to "enough", not over all "enough" answers: most of those are far from empty, and would hide
    a reserve that failed on most of what it loosened. Not asked of the two scenarios the measured reserve cannot see."""
    out = simulate(scenario, 150, cfg={'measured_reserve': True})
    dry = sum(1 for _, _, ran_dry in out['loosened'] if ran_dry)
    assert out['measured'] > 0 and dry <= gn.ALPHA * len(out['loosened']), out['misses']
    if scenario == 'accurate gauge': assert out['loosened'] and out['counts']['guard']['fit_warned'] < out['counts']['rules']['fit_warned']   # a true gauge: fewer warnings on prints that fitted

@pytest.mark.simulation
def test_the_loosened_answers_show_a_reserve_that_all_the_enough_answers_hide(memory, monkeypatch):
    """A guard made blind to what trays read at empty, on spools that read 20 g at empty: a third of the answers its
    measured reserve loosens run dry, and under 3 % of all its "enough" answers, which is what 1.4.0's test counted
    and allowed up to 10 % of."""
    def blind(history, estimates=gn.estimates):
        est = estimates(history)
        for t in list(est['trays'].values()) + [est['blank']]: t['a'] = None
        return est
    monkeypatch.setattr(gn, 'estimates', blind)
    out = simulate('20 g at empty', 150, cfg={'measured_reserve': True})
    dry, guard = sum(1 for _, _, ran_dry in out['loosened'] if ran_dry), out['counts']['guard']
    assert (len(out['loosened']), dry, guard['ok'], guard['ok_dry']) == (16, 5, 184, 5)
    assert dry > gn.ALPHA * len(out['loosened']) and guard['ok_dry'] <= gn.ALPHA * guard['ok']

def tables(seeds=range(1889, 1894), prints=300):
    """The two tables of docs/learning.md, as text: every scenario on each seed, once with the settings as they come and
    once with the measured reserve on."""
    names = dict(zip(SIM_SCENARIOS, ('a true gauge', 'reads 20 g at empty', 'counts 0.97 g per gram fed', 'spools that differ (8 g) and prints that vary (3 %)', 'the gauge changes halfway (to 0.95)',
                                     'one false run-dry', 'four trays: true, 20 g at empty, 0.97, spools that differ', 'spools read 25 g more at empty from halfway', 'one tray whose spools are 0 to 50 g short')))
    share = lambda c: f"{100 * c['fit_warned'] / c['fit']:.1f} %"
    total = lambda runs, method: {key: sum(r['counts'][method][key] for r in runs) for key in ('ok', 'ok_dry', 'fit', 'fit_warned', 'rows')}
    off = ['| Scenario | "Enough", then ran dry: 1.3.0\'s rules | the guard | Looser than 1.3.0\'s rules | Fitted, but warned: 1.3.0\'s rules | the guard |', '|---|---|---|---|---|---|']
    on = ['| Scenario | Loosened to "enough" | of which ran dry | "Enough", then ran dry: 1.3.0\'s rules | the guard | Fitted, but warned: 1.3.0\'s rules | the guard | Measured reserve from print |', '|---|---|---|---|---|---|---|---|']
    firsts, notes = {}, []
    def run(*how):
        with tempfile.TemporaryDirectory() as folder, state_in_memory():         # each run from nothing; the folder only names the files
            fg.STATE_DIR, was = folder, fg.STATE_DIR
            try: return simulate(*how)
            finally: fg.STATE_DIR = was
    for scenario in SIM_SCENARIOS:
        plain, asked = [run(scenario, prints, seed) for seed in seeds], [run(scenario, prints, seed, {'measured_reserve': True}) for seed in seeds]
        rules, guard = total(plain, 'rules'), total(plain, 'guard')
        off.append(f"| {names[scenario]} | {rules['ok_dry']} of {rules['ok']} | {guard['ok_dry']} of {guard['ok']} | {sum(r['looser'] for r in plain)} of {guard['rows']} | {share(rules)} | {share(guard)} |")
        rules, guard, loosened = total(asked, 'rules'), total(asked, 'guard'), [x for r in asked for x in r['loosened']]
        earned = [r['first']['measured reserve'] + 1 for r in asked if 'measured reserve' in r['first']]
        on.append(f"| {names[scenario]} | {len(loosened)} | {sum(1 for x in loosened if x[2])} | {rules['ok_dry']} of {rules['ok']} | {guard['ok_dry']} of {guard['ok']} | {share(rules)} | {share(guard)} | "
                  + (f"{statistics.median(earned):.0f}" + (f" ({len(earned)} of {len(asked)} seeds)" if len(earned) < len(asked) else '') if earned else 'never') + ' |')
        after, tray4 = [x for x in loosened if x[0] >= prints // 2], [x for x in loosened if x[1] == 'A4']
        notes.append(f"{names[scenario]}: loosened from print {prints // 2 + 1} on {len(after)}, ran dry {sum(1 for x in after if x[2])}; loosened on tray 4 {len(tray4)}, ran dry {sum(1 for x in tray4 if x[2])}; "
                     f"weighed {sum(r['weighed'] for r in plain)} times as it comes, {sum(r['weighed'] for r in asked)} with the measured reserve on")
        for r in plain:
            for name, k in r['first'].items(): firsts.setdefault(name, []).append(k + 1)
    used = '; '.join(f"{name} from print {min(ks)} to {max(ks)}, median {statistics.median(ks):.0f} ({len(ks)} runs)" for name, ks in firsts.items())
    return '\n'.join([f'{len(seeds)} seeds ({seeds[0]} to {seeds[-1]}) of {prints} prints each', '', 'The settings as they come:', ''] + off + ['', 'With the measured reserve on:', ''] + on
                     + ['', 'First in use, with the settings as they come: ' + used, ''] + notes)

if __name__ == '__main__':
    print(tables())
