"""A simulated printer whose gauges are known, run through the guard's own recorder and check."""
import itertools
import math as _math
import random

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
}

def simulate(scenario, prints, seed=1889, cfg=None):
    """Runs `prints` prints of 1 or 2 filaments on four trays, 5 hours apart: 5 to 250 g each, or (4 times in 10) 60 to 105 %
    of what the tray reads, as when a spool is printed down, so that many checks fall near empty. Before each print the user
    swaps any spool that reads less than the print needs (+ 5 g), and weighs a spool when the card asks; whatever the
    answer, the print then runs, and a spool that runs out mid-print is replaced. Returns, per method, the counts of
    answers against what happened, and the print at which each number first came into use."""
    rnd, serial, ids = random.Random(seed), 'SIM', itertools.count(1)
    cfg = dict(fg.DEFAULTS, **(cfg or {}))
    gauges, false_dry = SIM_SCENARIOS[scenario], [scenario == 'one false run-dry']
    def spool(i):                                                     # full, or half the time part used (moved from elsewhere)
        held = 1000.0 - gauges[i].get('short', 0.0) + rnd.gauss(0, gauges[i].get('fill', 2.0))
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
    def check(plate, idle, plain):
        trs = fg.trays_of(idle)[0]
        fg.credit(trs, {}, gn.corrections(serial, trs, cfg=cfg, plain_rules=plain))
        return [fg.assess(f, trs, cfg) for f in plate['filaments']]
    counts = {m: {'ok': 0, 'ok_dry': 0, 'fit': 0, 'fit_warned': 0, 'rows': 0} for m in ('1.3.0', '1.4.0', '1.4.0 measured')}
    first, rec, weighed, misses = {}, gn.Recorder(serial), 0, []
    for k in range(prints):
        t = k * 5 * 3600.0
        idx = rnd.sample(range(4), 2 if rnd.random() < 0.3 else 1)
        need = {i: round(min(250.0, reading(i) * rnd.uniform(0.6, 1.05)) if rnd.random() < 0.4 and reading(i) > 10    # printing the spool down
                         else _math.exp(rnd.uniform(_math.log(5), _math.log(250))), 1) for i in idx}
        for i in idx:
            if reading(i) < need[i] + 5: trays[i] = spool(i)
        title, layers = f'P{k}', 8
        plate = {'printer_model': 'Bambu Lab H2D', 'time': '', 'layers': layers,
                 'filaments': [{'slot': n + 1, 'type': 'PLA', 'fid': 'GFA00', 'colour': '#' + SIM_COLOURS[i], 'name': 'PLA Basic', 'grams': need[i], 'nozzle': None} for n, i in enumerate(idx)]}
        fg.remember_slice(title, 1, plate, kept({n + 1: [0.5] + [(need[i] - 0.5) / layers] * layers for n, i in enumerate(idx)}))
        idle = status('IDLE', 0, k, '', 0, None)
        rows = check(plate, idle, False)
        offer = gn.weigh_offer(serial, rows, cfg, now=t)
        if offer:
            i = int(offer['label'][1]) - 1; weighed += 1
            gn.asked(serial, offer['label'], now=t)
            gn.weigh(serial, offer['label'], offer['reading'], round(trays[i]['left']), {'id': trays[i]['id'], 'type': 'PLA', 'colour': '#' + SIM_COLOURS[i], 'fid': 'GFA00', 'weight': 1000.0}, now=t)
            rows = check(plate, idle, False)
        old = check(plate, idle, True)
        kappa = {i: 1 + rnd.gauss(0, gauges[i].get('spread', 0.003)) for i in idx}
        for i, new, was in zip(idx, rows, old):
            fits = trays[i]['left'] >= 1.5 + kappa[i] * need[i]
            for method, row in (('1.3.0', was), ('1.4.0', new)) + ((('1.4.0 measured', new),) if new.get('measured') else ()):
                c = counts[method]; c['rows'] += 1
                if row['status'] == 'OK': c['ok'] += 1; c['ok_dry'] += not fits
                if row['status'] == 'OK' and not fits and method == '1.4.0':
                    misses.append({'print': k, 'tray': f'A{i + 1}', 'reads': reading(i), 'left': round(trays[i]['left'], 1), 'takes': round(1.5 + kappa[i] * need[i], 1),
                                   'need': round(row['need'], 1), 'counted': round(row['best'], 1), 'reserve': round(row['reserve'], 1), 'measured': row.get('measured')})
                if fits: c['fit'] += 1; c['fit_warned'] += row['status'] in ('TIGHT', 'SPLIT', 'SHORT')
            if new.get('measured'): first.setdefault('measured reserve', k)
        est = gn.estimates(gn.load().get(serial, {}))
        for name, key in (('rho', 'rho'), ('pre', 'pre'), ('a', 'a'), ('margin', 'q')):
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
                    false_dry[0] = False; look('RUNNING', n, {i}); look('RUNNING', n, {i}); trays[i] = spool(i)
                short = take(i, kappa[i] * (need[i] - 0.5) / layers, k)
                if short > 0:                                                         # ran out: the printer pauses, a new spool goes in
                    look('PAUSE', n, {i}); look('PAUSE', n, {i}); trays[i] = spool(i); take(i, short, k)
        for _ in range(3): tt += 27; look('FINISH', layers)
    return {'counts': counts, 'first': first, 'weighed': weighed, 'misses': misses}

@pytest.mark.simulation
@pytest.mark.parametrize('scenario, prints', [('accurate gauge', 130), ('a mix across trays', 100), ('a change halfway', 100), ('one false run-dry', 60)])
def test_the_simulated_printer_runs_dry_no_more_often_and_warns_less_where_its_gauge_is_true(state, scenario, prints):
    """Before the measured reserve, no "enough" after which the print ran dry that 1.3.0 would not have given too; with
    it, at most ALPHA of them; and for a true gauge fewer warnings on prints that fitted than 1.3.0's."""
    out = simulate(scenario, prints)
    old, new, measured = out['counts']['1.3.0'], out['counts']['1.4.0'], out['counts']['1.4.0 measured']
    assert new['ok_dry'] - measured['ok_dry'] <= old['ok_dry'] and measured['ok_dry'] <= gn.ALPHA * max(1, measured['ok'])
    if scenario == 'accurate gauge': assert measured['rows'] > 0 and new['fit_warned'] < old['fit_warned']
