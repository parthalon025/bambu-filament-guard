"""What the guard learns from previous prints, per tray: how far that tray's readings can be trusted.

The AMS's reading of a spool is an estimate. Measured 2026-11-19 on an H2D: tray C3 read 105 g and ran dry after 84-85 g
of a print, so it read about 20 g when it was empty; in the same print tray B4 fell 49 g for a need of 49.1 g. A fixed
reserve treats both alike. With the per-layer profile of the job that runs (filament_guard.layer_profile) the guard knows
how many grams left each tray between any two layers, and compares that with the tray's readings.

The model, in the reading's own units (every number in it can be observed):
  a print of N sliced grams makes the tray's reading fall by about rho x N, plus pre before layer 1; the spool is
  empty when the reading reaches a.
  enough  <=>  reading  >=  rho x N + pre + a + margin.
rho (reading fallen per sliced gram) and pre come from every sound whole print. a (the reading at empty) comes from
run-dries (exact), from spools seen still feeding at a low reading (a lies below it: censored), and from weigh-ins near
empty. The margin is the guard's own past forecast errors. Where a number is not yet measured, 1.3.0's rules stand.

Recorded (Recorder, fed every look of guard_ledger.JobWatch, every 3 seconds while it follows a printer), per job the
printer runs whose trays can be named:
  - per tray the job draws on, when the job was matched to a slice with a profile: the spool; the reading before layer
    1; the reading at the start of each layer seen to begin (at most SAMPLES, thinned evenly); the reading at the end;
    the grams the profile has the tray give over that span, printed and purge apart, the purge as the spool gave it
    (the header's own split charges the nozzle's content to the filament pushed out, see layer_profile); whether the
    record is sound (the same spool throughout, a reading that never rose, no gap in following longer than
    guard_ledger.GAP, the tray never empty, the job seen to finish); and its score: what the model as it stood before
    this job forecast for the tray's fall over the whole print, and what fell.
  - per tray of every job: the lowest reading at which the spool was seen still feeding ('seen'), a censored
    observation of a.
  - a run-dry: a tray the job draws on, giving filament by the profile at that point of the print, that is reported
    empty in two looks in a row while the job runs, with its last reading, when that was low enough for a spool that ran
    out (DRY_MOST); a fuller spool, or one the print was not drawing on then, was taken out. Only for a job with a
    profile. With it go the printer's print_error and hms codes at that look, raw, to judge it by later.
  - a weigh-in (weigh): the reading the card showed, the grams on the spool, the spool.
  - a change: when the tray's forecast errors shifted (change_points), from which job; what came before stops counting.
Kept in STATE_DIR/learned.json, per (printer, tray). A 1.3.0 file reads as it is: its records lack scores, which start
with the next print.

Estimated (estimates; the arithmetic is guard_stats), per printer and tray, over the tray's records since its last
change, each record weighed DISCOUNT^age:
  rho   a tray's ratio of what its readings fell to the sliced grams, the job ratios clipped, drawn to the printer's
        ratio by shrinkage; the printer's ratio drawn to 1 by RHO_PRIOR_GRAMS. From the first sound whole print.
  pre   the loss before layer 1 beyond the profile's layer 0, a weighted mean drawn to the printer's the same way.
  a     an upper quantile of the reading at empty, Kaplan-Meier over exact and censored observations; a tray with
        fewer than A_OWN of its own takes the printer's, but never less than its own largest exact one. Once exact
        observations repeat within trays (SPREAD_DF), also the bound for one more spool from their spread: the largest
        of a few is not a 90 % bound. A spool observed exactly more than once (weighed near empty twice, weighed and
        then run dry) is one observation, its largest: its repeats show how a scale and a gauge repeat, not how spools
        differ. And a is never taken smaller than it comes out with every record counted for itself, as 1.4.0 counted:
        counting a spool once must not count a tray with more.
  margin the one-sided conformal bound of the scaled forecast errors, the printer's (and the tray's, the larger, once
        it has SCORED_MIN of its own).
A tray whose learned forecast does clearly worse than the plain one (the sign test, REF_*) has rho and pre set aside.
The score is of the fall from before layer 1 to the end, pre included, which is what a check forecasts.

A check (corrections) counts a tray the printer measures (never an amount entered by hand) with its reading less a,
less a weighed spool's bias, and never more than W - (W - G) / rho where rho is below 1 (a gauge that counts short
reads more than the spool holds, whatever a was measured before); and needs max(1, rho) x N + pre (filament_guard.assess
takes the largest of the trays a filament could be printed from). Each tray is counted with the stricter of this and
of 1.3.0's rules (rules), and the reserve stays the fixed one: no answer is looser than 1.3.0's.

The measured reserve (setting measured_reserve: experimental, off unless turned on) replaces both. With it on, once
the printer has SCORED_MIN scored prints and repeated exact observations of a (SPREAD_DF), and the tray judged has one,
a check asks margin x (N + MARGIN_K), never below RESERVE_FLOOR, in place of the fixed reserve, and counts that tray
by the model alone. It is off by default because it loosens a check on evidence that cannot see a change in a: a
change is looked for in the forecast errors, and those do not move when spools begin to read more at empty
(docs/learning.md has the numbers). Learning off (setting learn): every check is as without it."""
import os, time
import filament_guard as fg
import guard_ledger as gl
import guard_stats as st

SAMPLES = 60                  # readings kept per tray and job: enough to see how a tray's reading follows the print
SAMPLE_SPAN = 10              # seconds: a layer change seen between two looks further apart does not say when it happened
SETTLE = 60                   # seconds after FINISH the end reading is taken: the end routine unloads the filament, and the
                              # AMS may correct its figure then; the first FINISH reading is kept beside it (end_first)
KEEP_JOBS, TRACE_JOBS = 100, 20   # records per (printer, tray), of which the newest 20 keep their readings layer by layer:
                              # DISCOUNT leaves a record of 100 prints ago 0.6 % of the weight; the trace is for judging later
KEEP_DRY, KEEP_WEIGHS, KEEP_SEEN, KEEP_CHANGES = 20, 20, 50, 5   # each an observation of a, or a reset: rare, kept long
DRY_MOST = 50.0               # grams: a spool that leaves reading more was taken out, not run dry. Flat: 10 % of a 1 kg spool
                              # would be 100 g, far beyond a gauge's error (the one measured run-dry read 20 g)

# 1.3.0's rules (rules), kept as the bound no answer goes beyond unless the measured reserve is turned on and earned
LEGACY_JOBS, LEGACY_DRY = 20, 5   # what 1.3.0 kept per tray
DRY_EVENTS = 3                # dry is the largest of the last 3: one odd event is not forgotten at once, nor kept for ever
RATE_JOBS = 10                # rate is taken over the last 10 sound whole jobs: recent, and many grams
RATE_JOB_GRAMS = 5.0          # a job of less, read in whole grams, says nothing about a rate
RATE_MIN_JOBS = 3             # rate is used from 3 such jobs on...
RATE_MIN_GRAMS = 300.0        # ...that took 300 g or more from the tray between them: the reading's whole grams are then 1 % at most
RATE_NOISE = 0.99             # a rate of 0.99 or more is used as 1: within 1 % is the noise of whole-gram readings
RATE_FLOOR = 0.9              # a rate below this is not used: it means the readings lagged behind the print, not a gauge that
                              # far out (the one measurement implies about 0.98), and applied it would make every check on the
                              # tray come out short
PRE_JOBS = 5                  # pre is the largest of the last 5 sound jobs that have both readings...
PRE_MIN_JOBS = 2              # ...and is used from 2 on: the loss before layer 1 is the same each print, so two agree on it

# 1.4.0's estimates
ALPHA = 0.10                  # the share of forecasts the measured margin may fall short of, and of spools that may read more
                              # at empty than the a used: one in ten, as close as a few dozen prints can tell
MARGIN_K = 20.0               # grams added to a print's size to scale its error: a whole-gram reading puts about 1 g of
                              # error on any fall, and a 20 g print is where that equals a 5 % rate error
SCORED_MIN = 9                # scored prints before a conformal bound exists: ceil((n + 1)(1 - ALPHA)) <= n needs n >= 9
SPREAD_DF = 2                 # the measured reserve needs the spread of a from spool to spool: exact observations of different
                              # spools that repeat within a tray, two degrees of freedom at least (3 spools in one tray, or 2
                              # in each of two). The largest of a few observations is not a 90 % bound (with n of them it
                              # covers n / (n + 1)): the simulation's noisy spools ran dry after "enough" when only that was used
OWN_EXACT_MIN = 1             # ...and one of the tray judged: what a tray reads at empty is its own (a wheel that counts
                              # short), and another tray's says nothing of it
RESERVE_FLOOR = (5.0, 0.02)   # grams, share of the need: the measured reserve never goes below either
DISCOUNT = 0.95               # weight lost per print of age: half after 14 prints, so a gauge that drifts is followed
CLIP_FIXED = (0.9, 1.15)      # a job's ratio is clipped to this before it counts: below 0.9 the readings lagged (see
                              # RATE_FLOOR), above 1.15 the reading fell for more than the print (a spool rubbing, a reload)
CLIP_QUANTILES, CLIP_FROM = (0.05, 0.95), 20   # ...and once the printer has 20 ratios, to their own 5 % and 95 % quantiles
RHO_PRIOR_GRAMS = 50.0        # the printer's ratio is drawn to 1 as if by 50 g of prints that fell true: one small print, in
                              # whole grams, cannot move it far
SHRINK_LEAST = 1.0            # a tray's own prints never count for less than one print's worth against the printer's
REF_WINDOW = 10               # the reference check looks at a tray's last 10 scored prints...
REF_OFF, REF_ON = 0.05, 0.5   # ...sets rho and pre aside when the learned forecast was the worse one more often than chance
                              # allows (one-sided sign test, p < 0.05), and takes them back once that is no longer so (p >= 0.5)
HAZARD = 1 / 50               # change-point detection: a tray's behaviour is taken to change about once in 50 prints
CHANGE_WITHIN = 5             # a change counts once it is more likely than not to have begun 2 to 5 prints ago: two prints
                              # that agree, not one odd one
CHANGE_PRIOR = (0.0, 1.0, 1.0, 0.03 ** 2)   # a new run's errors: mean 0, spread about 3 % of a print (Normal-Gamma)
CHANGE_ODD = 0.05             # one print in twenty may be an odd one (a reload, a spool rubbing): with none allowed for, one odd
                              # error ends the run for good and reads as a change (found by the estimators' test)
A_OWN = 3                     # observations of its own (exact, or seen feeding at DRY_MOST or less) before a tray's a is its own
A_WEIGH = 0.25                # a weigh-in at a quarter of the spool's full weight or less is an observation of a: the rest of a
                              # rate error, (1 - rho) x what is left, is then a few grams at most
WEIGH_TRAY, WEIGH_ANY = 7 * 86400, 86400   # "Weigh C3..." at most once a week per tray and once a day in all: asked often,
                                           # it would be ignored


# ---------------------------------------------------------------- the history file

def path():
    return os.path.join(fg.STATE_DIR, 'learned.json')

def load():
    """{serial: {tray label: {'jobs': [record], 'dry': [event], 'seen': [..], 'weighs': [..], 'changes': [..]}}}. No file
    is no history; a damaged one is set aside (filament_guard.read_json, under the state folder's lock like every write)."""
    return fg.read_json(path(), {})

def save(history):
    with fg.state_lock(): fg.write_json(path(), history, separators=(',', ':'))

LIMITS = {'jobs': KEEP_JOBS, 'dry': KEEP_DRY, 'seen': KEEP_SEEN, 'weighs': KEEP_WEIGHS, 'changes': KEEP_CHANGES}

def add(history, serial, label, kind, item):
    items = history.setdefault(serial, {}).setdefault(label, {}).setdefault(kind, [])
    items.append(item); del items[:-LIMITS[kind]]
    if kind == 'jobs':
        for j in items[:-TRACE_JOBS]: j.pop('samples', None)

def keep(serial, label, kind, item, most=None):
    """Adds one item to a tray's history (kind: 'jobs', 'dry', 'seen', 'weighs' or 'changes'), keeping the last few."""
    with fg.state_lock():
        history = load(); add(history, serial, label, kind, item); save(history)

def forget(label=None, printer=None, names=None):
    """Empties the history, or one tray's: label, and printer (its serial, or its model as names maps serials to models)
    where more than one printer has such a tray. Returns what was forgotten, in words; ValueError when that cannot be
    told (no such tray, or which printer's)."""
    with fg.state_lock():
        history = load()
        if label is None: what = 'everything learned'; history = {}
        else:
            names = names or {}
            mine = [s for s, trays in history.items() if label in trays and (printer is None or printer in (s, names.get(s)))]
            if not mine: raise ValueError(f'nothing is learned of a tray {label}' + (f' on {printer}' if printer else ''))
            if len(mine) > 1: raise ValueError(f"{label} is a tray of more than one printer: name one with --printer ({', '.join(names.get(s, s) for s in mine)})")
            del history[mine[0]][label]
            what = f'what was learned of {label} on {names.get(mine[0], mine[0])}'
        save(history)
    fg.log(f'{what} is forgotten')
    return what


# ---------------------------------------------------------------- 1.3.0's rules

def day(at):
    return time.strftime('%d %b', time.localtime(at)).lstrip('0')

def fed_of(job):
    """The lowest reading a record saw its tray still feeding at (kept with the record; worked out from the readings for
    a 1.3.0 record)."""
    if 'fed' in job: return job['fed']
    return Recorder.fed_at({'before': job.get('before'), 'samples': job.get('samples') or [], 'end': job.get('end')})

def contradicted(event, tray_history=None):
    """Why 1.3.0 no longer used a run-dry, in words, or '': a later print saw the tray still feeding at a lower reading."""
    c = event.get('contradicted')                                   # marked so by 1.3.0
    if not c and tray_history:
        later = [(s['at'], s['reading'], s.get('job', '')) for s in tray_history.get('seen', []) if s['at'] > event['at'] and s['reading'] < event['reading']]
        later += [(j.get('at', 0), fed_of(j), j.get('job', '')) for j in tray_history.get('jobs', []) if j.get('at', 0) > event['at']]
        later = sorted(x for x in later if x[1] is not None and x[1] < event['reading'])
        c = {'at': later[0][0], 'reading': later[0][1]} if later else None
    return f"on {day(c['at'])} a spool in it still gave filament at {c['reading']:.0f} g" if c else ''

def rules(tray_history):
    """1.3.0's three numbers of one tray: {'dry' | 'rate' | 'pre': {'value', 'used', 'why'}}. value None: no evidence
    yet. used: past its threshold. why: the evidence in words when used, what is missing when not."""
    jobs = [j for j in tray_history.get('jobs', [])[-LEGACY_JOBS:] if j.get('sound')]
    dries = [d for d in tray_history.get('dry', [])[-LEGACY_DRY:] if not contradicted(d, tray_history)][-DRY_EVENTS:]
    out = {}
    if dries:
        worst = max(dries, key=lambda d: d['reading'])
        out['dry'] = {'value': worst['reading'], 'used': True, 'why': f"it read {worst['reading']:.0f} g when a spool ran dry in it on {day(worst['at'])}"}
    else: out['dry'] = {'value': None, 'used': False, 'why': 'no spool has run dry in it yet'}
    whole = [j for j in jobs if j.get('whole') and j['sliced']['printed'] + j['sliced']['purge'] >= RATE_JOB_GRAMS][-RATE_JOBS:]
    given = sum(j['sliced']['printed'] + j['sliced']['purge'] for j in whole)
    rate = sum(j['start'] - j['end'] for j in whole) / given if given else None
    missing = [x for x in ((f'{len(whole)} of {RATE_MIN_JOBS} prints' if len(whole) < RATE_MIN_JOBS else ''),
                           (f'{given:.0f} of {RATE_MIN_GRAMS:.0f} g' if given < RATE_MIN_GRAMS else '')) if x]
    if rate is not None and not missing and rate >= RATE_NOISE: missing = [f'{rate:.3f} g per gram is within the reading\'s own noise']
    elif rate is not None and not missing and rate < RATE_FLOOR: missing = [f'{rate:.2f} g per gram is too far out to believe']
    out['rate'] = {'value': rate, 'used': not missing, 'jobs': len(whole), 'grams': given,
                   'why': f"its reading fell {rate:.2f} g for each gram it gave in its last {len(whole)} prints" if not missing else ', '.join(missing)}
    lost = [max(0.0, j['before'] - j['start'] - j['layer0']) for j in jobs if j.get('before') is not None and j.get('start') is not None][-PRE_JOBS:]
    pre = max(lost) if lost else None
    out['pre'] = {'value': pre, 'used': len(lost) >= PRE_MIN_JOBS, 'jobs': len(lost),
                  'why': (f"lost up to {pre:.1f} g before the first layer of its last {len(lost)} prints" if len(lost) >= PRE_MIN_JOBS
                          else f'{len(lost)} of {PRE_MIN_JOBS} prints')}
    return out

def ruled(tray_history, g, w):
    """What 1.3.0 counted a tray reading g with, and its pre: (credited, evidence, pre, pre note)."""
    m = rules(tray_history)
    options = [(g, '')]
    if m['dry']['used']: options.append((g - m['dry']['value'], m['dry']['why']))
    if m['rate']['used'] and w: options.append((w - (w - g) / m['rate']['value'], m['rate']['why']))
    credited, evidence = min(options, key=lambda o: o[0])
    pre = m['pre']['value'] if m['pre']['used'] else 0.0
    return max(0.0, credited), evidence, pre, m['pre']['why']


# ---------------------------------------------------------------- 1.4.0's estimates

def spool_key(s):
    s = s or {}
    return s.get('id') or (s.get('type'), s.get('colour'), s.get('fid'), s.get('weight'))

def since(items, start):
    return [x for x in items if x.get('at', 0) >= start]

def weights(n):
    return [DISCOUNT ** (n - 1 - i) for i in range(n)]

def scaled(score, forecast):
    return (score['fell'] - score[forecast]) / (score['n'] + MARGIN_K)

def given_all(j):
    return j['sliced']['printed'] + j['sliced']['purge']

def observations(h, start):
    """The observations of a tray's reading at empty since `start`: (every exact one, [(kind, a, spool)]; the same with
    each spool once, by its largest; the lowest reading each other spool was seen still feeding at, [(v, False)] as
    Kaplan-Meier takes what lies below v; the weigh-ins near empty)."""
    exact = [('dry', d['reading'], d.get('spool')) for d in since(h.get('dry', []), start)]
    near = [w for w in since(h.get('weighs', []), start) if w['weight'] <= A_WEIGH * ((w.get('spool') or {}).get('weight') or 1000.0)]
    exact += [('weighed', w['reading'] - w['weight'], w.get('spool')) for w in near]
    once = {}
    for o in exact:
        k = spool_key(o[2])
        if k not in once or o[1] > once[k][1]: once[k] = o
    low = {}
    for s in since(h.get('seen', []), start) + [dict(reading=fed_of(j), spool=j.get('spool')) for j in since(h.get('jobs', []), start) if 'fed' not in j]:
        k = spool_key(s.get('spool'))
        if s['reading'] is not None and k not in once: low[k] = min(low.get(k, s['reading']), s['reading'])
    return exact, list(once.values()), [(v, False) for v in low.values()], near

def reference(scores):
    """Whether a tray's learned forecast is set aside: walked through its scores in order, set aside when, over the last
    REF_WINDOW, it was further from what fell than the plain one more often than chance allows, and taken back when no
    longer. Returns (aside, worse, of)."""
    aside, worse, pairs = False, 0, 0
    for i in range(len(scores)):
        recent = [s for s in scores[max(0, i + 1 - REF_WINDOW):i + 1] if abs(abs(s['fell'] - s['learned']) - abs(s['fell'] - s['n'])) > 1e-9]
        worse, pairs = sum(1 for s in recent if abs(s['fell'] - s['learned']) > abs(s['fell'] - s['n'])), len(recent)
        p = st.sign_p(worse, pairs)
        if not aside and p < REF_OFF: aside = True
        elif aside and p >= REF_ON: aside = False
    return aside, worse, pairs

def estimates(printer_history):
    """Everything learned of one printer: {'trays': {label: tray}, 'printer': {...}, 'blank': the tray of a label with
    no history}. A tray: 'rho', 'pre' (None: no evidence; set aside: 'aside'), 'a' (None: nothing measured), 'q' (its
    margin, None until the printer has SCORED_MIN scores), and the evidence each rests on: 'exact', every exact
    observation of a, and 'spools', the same with each spool once."""
    trays = {}
    for label, h in printer_history.items():
        start = max([c['at'] for c in h.get('changes', [])] or [0])
        jobs = [j for j in since(h.get('jobs', []), start) if j.get('sound')]
        rated = [j for j in jobs if j.get('whole') and j.get('start') is not None and j.get('end') is not None and given_all(j) >= RATE_JOB_GRAMS]
        lost = [j for j in jobs if j.get('before') is not None and j.get('start') is not None]
        scores = [j['score'] for j in since(h.get('jobs', []), start) if j.get('score')]
        exact, spools, low, near = observations(h, start)
        trays[label] = {'start': start, 'change': ([c for c in h.get('changes', []) if c['at'] == start] or [None])[0], 'rated': rated, 'lost': lost,
                        'scores': scores, 'exact': exact, 'spools': spools, 'low': low, 'near': near, 'weighs': h.get('weighs', [])}
    ratios = [(j['start'] - j['end']) / given_all(j) for t in trays.values() for j in t['rated']]
    lo, hi = (st.quantile(ratios, CLIP_QUANTILES[0]), st.quantile(ratios, CLIP_QUANTILES[1])) if len(ratios) >= CLIP_FROM else CLIP_FIXED
    num = den = 0.0
    for t in trays.values():
        n = len(t['rated']); w = weights(n)
        t['ratio'] = [(given_all(j), min(hi, max(lo, (j['start'] - j['end']) / given_all(j))), wi) for j, wi in zip(t['rated'], w)]
        t['rho_own'] = st.group_stats([r for _, r, _ in t['ratio']], [g * wi for g, _, wi in t['ratio']])
        num += sum(g * r * wi for g, r, wi in t['ratio']); den += sum(g * wi for g, _, wi in t['ratio'])
        t['loss'] = st.group_stats([max(0.0, j['before'] - j['start'] - j['layer0']) for j in t['lost']], weights(len(t['lost'])))
    rho_p = (num + RHO_PRIOR_GRAMS) / (den + RHO_PRIOR_GRAMS) if den else None
    k_rho = st.shrinkage([t['rho_own'] for t in trays.values() if t['ratio']], SHRINK_LEAST)
    losses = [(t['loss'][0], t['loss'][1]) for t in trays.values() if t['lost']]
    pre_p = sum(m * n for m, n in losses) / sum(n for _, n in losses) if losses else None
    k_pre = st.shrinkage([t['loss'] for t in trays.values() if t['lost']], SHRINK_LEAST)
    every = [scaled(s, 'applied') for t in trays.values() for s in t['scores']]
    q_p = st.conformal_bound(every, ALPHA) if len(every) >= SCORED_MIN else None
    def at_empty(key):
        """({label: (a, what it is from)}, the printer's a, the spread of exact observations within trays and its degrees
        of freedom), with t[key] taken for the trays' exact observations."""
        seen = {label: [v for _, v, _ in t[key]] for label, t in trays.items()}
        obs = {label: [(v, True) for v in seen[label]] + t['low'] for label, t in trays.items()}
        a_p = st.km_upper([o for label in trays for o in obs[label]], ALPHA)
        spread, df = st.pooled_spread(list(seen.values()))
        out = {}
        for label, t in trays.items():
            mine = max(seen[label], default=None)
            if len(seen[label]) + sum(1 for v, _ in t['low'] if v <= DRY_MOST) >= A_OWN: a, whose = st.km_upper(obs[label], ALPHA), 'own'
            else: a, whose = max([x for x in (a_p, mine) if x is not None], default=None), 'printer' if a_p is not None and (mine is None or a_p > mine) else 'own'
            pred = st.prediction_bound(seen[label], spread, df, 1 - ALPHA) if seen[label] and df >= SPREAD_DF else None
            if pred is not None and (a is None or pred > a): a, whose = pred, 'spread'
            out[label] = (a, whose)
        return out, a_p, spread, df
    larger = lambda x, y: y if x[0] is None or (y[0] is not None and y[0] > x[0]) else x
    by_spool, a_p, spread, df = at_empty('spools')                  # each spool once: what the spread, and with it the measured reserve, rests on
    by_record, a_records = at_empty('exact')[:2]                    # every record for itself, as 1.4.0 counted: a floor under a, no more
    a_p = larger((a_p,), (a_records,))[0]
    for label, t in trays.items(): t['a'], t['a_from'] = larger(by_spool[label], by_record[label])
    def shrunk(own, k, whole):
        m, n, _ = own
        if whole is None: return None
        if m is None or k is None: return whole
        return (n * m + k * whole) / (n + k)
    for t in trays.values():
        t['rho'] = shrunk(t['rho_own'], k_rho, rho_p)
        t['pre'] = shrunk(t['loss'], k_pre, pre_p)
        t['aside'], t['worse'], t['pairs'] = reference(t['scores'])
        q_t = st.conformal_bound([scaled(s, 'applied') for s in t['scores']], ALPHA) if len(t['scores']) >= SCORED_MIN else None
        t['q'] = max(q_t, q_p) if q_t is not None and q_p is not None else q_p
        t['interval'] = st.ratio_interval([(g, g * r) for g, r, _ in t['ratio']])
    blank = {'start': 0, 'change': None, 'rated': [], 'lost': [], 'scores': [], 'exact': [], 'spools': [], 'low': [], 'near': [], 'weighs': [], 'ratio': [],
             'rho_own': (None, 0.0, None), 'loss': (None, 0.0, None), 'rho': rho_p, 'pre': pre_p, 'aside': False, 'worse': 0, 'pairs': 0,
             'q': q_p, 'a': a_p, 'a_from': 'printer', 'interval': None}
    products = {}
    for t in trays.values():
        for j, (g, r, _) in zip(t['rated'], t['ratio']): products.setdefault((j.get('spool') or {}).get('fid') or '?', []).append((g, g * r))
    printer = {'scored': len(every), 'spools': sum(len(t['spools']) for t in trays.values()), 'df': df, 'spread': spread, 'q': q_p, 'rho': rho_p, 'pre': pre_p, 'a': a_p,
               'k_rho': k_rho, 'k_pre': k_pre, 'clip': (lo, hi), 'ratios': len(ratios),
               'products': {fid: (sum(y for _, y in p) / sum(x for x, _ in p), len(p), st.ratio_interval(p)) for fid, p in products.items()}}
    return {'trays': trays, 'printer': printer, 'blank': blank}

def forecast(t, n):
    """(learned, applied): the fall of a tray's reading over a print of n sliced grams (layer 0 on) as a check counts it,
    max(1, rho) x n + pre, by the model as it stands, and with the tray's rho and pre set aside where the reference
    check says so."""
    learned = max(1.0, t['rho'] or 1.0) * n + (t['pre'] or 0.0)
    return learned, (n if t['aside'] else learned)

def measured(est, t, cfg_on=False):
    """Whether a check on this tray may use the measured reserve (see the module's docstring)."""
    p = est['printer']
    return bool(cfg_on and p['scored'] >= SCORED_MIN and p['df'] >= SPREAD_DF and len(t['spools']) >= OWN_EXACT_MIN and t['q'] is not None)

def bias(t, tray):
    """The latest weigh-in of the spool now in the tray: (reading - weight, when) if the spool read more than it held."""
    now = {'id': tray.get('spool'), 'type': tray.get('type'), 'colour': tray.get('colour'), 'fid': tray.get('fid'), 'weight': tray.get('weight')}
    mine = [w for w in t['weighs'] if same_spool(w.get('spool') or {}, now)]
    if not mine or mine[-1]['reading'] - mine[-1]['weight'] <= 0: return None
    return mine[-1]['reading'] - mine[-1]['weight'], mine[-1]['at']

def corrections(serial, trays, history=None, cfg=None, plain_rules=False):
    """{tray label: {'credited', 'evidence', 'pre', 'pre_note', 'rho', 'rho_note', 'margin', 'margin_note'}} for the
    trays the printer measures (not an amount entered by hand): what a check counts each with (see the module's
    docstring). credited: never above the reading, never below 0; rho: never below 1. plain_rules: 1.3.0's rules alone
    (for comparison). Trays nothing changes for are left out."""
    history = load() if history is None else history
    ph = history.get(serial, {})
    est = estimates(ph)
    on = (cfg or {}).get('measured_reserve', False)
    out = {}
    for tr in trays:
        if tr['grams'] is None or tr.get('entered') or tr.get('stale'): continue
        label, g, w = tr['label'], tr['grams'], tr.get('weight')
        old = ruled(ph.get(label, {}), g, w)
        fix = {}
        if plain_rules:
            if old[0] < g: fix.update(credited=old[0], evidence=old[1])
            if old[2] > 0: fix.update(pre=old[2], pre_note=old[3])
            if fix: out[label] = fix
            continue
        t = est['trays'].get(label) or est['blank']
        options = [(g, '')]
        if t['a'] is not None and t['a'] > 0: options.append((g - t['a'], a_words(t)))
        b = bias(t, tr)
        if b: options.append((g - b[0], f'weighed on {day(b[1])}, its spool held {b[0]:.0f} g less than it read'))
        if t['rho'] is not None and RATE_FLOOR <= t['rho'] < RATE_NOISE and w:   # what a gauge that counts too little leaves at empty: a floor
            options.append((w - (w - g) / t['rho'], f"its reading falls {t['rho']:.2f} g per gram it gives, so it reads more than the spool holds"))   # under a, whatever the reference check says of the need
        rho = None if t['aside'] else t['rho']
        credited, evidence = min(options, key=lambda o: o[0])
        credited = max(0.0, credited)
        pre = 0.0 if t['aside'] else (t['pre'] or 0.0)
        pre_note = f"loses about {pre:.1f} g before the first layer ({len(t['lost'])} of its prints, drawn to the printer's)"
        loose = measured(est, t, on)
        if not loose:                                                # no answer looser than 1.3.0's until the reserve is measured
            if old[0] < credited or (old[1] and old[0] <= credited): credited, evidence = old[0], old[1]   # at a tie, 1.3.0's words
            if old[2] > pre: pre, pre_note = old[2], old[3]
        if credited < g: fix.update(credited=credited, evidence=evidence)
        if round(pre, 1) > 0: fix.update(pre=pre, pre_note=pre_note)
        if rho is not None and round(rho, 3) > 1: fix.update(rho=rho, rho_note=f"its reading falls {rho:.3f} g per sliced gram ({len(t['rated'])} of its prints, drawn to the printer's)")
        if loose: fix.update(margin=t['q'], margin_note=f"measured from {est['printer']['scored']} scored prints")
        if fix: out[label] = fix
    return out

def a_words(t):
    kinds = {}
    for kind, _, _ in t['spools']: kinds[kind] = kinds.get(kind, 0) + 1
    seen = ', '.join(f"{n} {'run-dr' + ('ies' if n > 1 else 'y') if k == 'dry' else 'weighed near empty'}" for k, n in sorted(kinds.items()))
    whose = "spools in this printer's trays" if t['a_from'] == 'printer' else 'spools in it'
    return f"{whose} read up to {t['a']:.0f} g when empty" + (f' ({seen})' if seen else '')

def lacking(p):
    """What a printer (estimates' 'printer') still lacks before a reserve can be measured on it, in words; [] when nothing."""
    more = SPREAD_DF - p['df']
    return [x for x in (many(SCORED_MIN - p['scored'], 'more scored print') if p['scored'] < SCORED_MIN else '',
                        '' if more <= 0 else 'readings at empty of 3 spools in one tray, or of 2 in each of two' if not p['spools'] else
                        f"{many(more, 'more reading')} at empty, of {'another spool' if more == 1 else 'other spools'}, in a tray that already has one") if x]

def reserve_words(serial, rows, history=None, cfg=None):
    """The card's footnote on the reserve, in one clause: which reserve the rows of this check used, and what the measured
    one still lacks. '' with the measured reserve off, as it is unless turned on: the reserve is then the fixed one,
    and there is nothing to add to what the results say. '' also when no row came to a reserve (nothing loaded to
    judge)."""
    judged = [r for r in rows if r.get('reserve') is not None]
    if not (cfg or {}).get('measured_reserve', False) or not judged: return ''
    p = estimates((load() if history is None else history).get(serial, {}))['printer']
    used = [r for r in judged if r.get('measured')]
    if used:
        measured = f"the reserve is measured from {many(p['scored'], 'scored print')} and readings at empty of {many(p['spools'], 'spool')}"
        if len(used) == len(judged): return measured
        return f"for {', '.join(sorted({r['trays'][0]['label'] for r in used}))} {measured}; for the other filaments it is the fixed one"
    need = lacking(p)
    return 'the reserve is the fixed one; ' + (f"to be measured it still needs {' and '.join(need)}" if need else 'it is measured only for a tray that has a reading at empty of its own')


# ---------------------------------------------------------------- weigh-ins

def weigh(serial, label, reading, grams, spool, now=None):
    """A spool the printer measures, weighed: the grams of filament on it, against the reading the card showed."""
    now = time.time() if now is None else now
    keep(serial, label, 'weighs', {'at': now, 'reading': reading, 'weight': float(grams), 'spool': spool})
    fg.log(f'{label}: weighed, {grams:.0f} g on a spool that read {reading:.0f} g')

def weigh_offer(serial, rows, cfg, now=None, state=None):
    """The tray whose weighing would settle a check, or None: a row the check finds short of enough (tight, two spools,
    not enough) that would be enough at the hopeful end of what is known (its reading taken as true, the sliced need,
    the reserve's floor), on a spool the printer measures; at most once a week per tray and once a day in all, never
    for a quiet guard (no one-line cards) or with learning off."""
    if not cfg.get('learn') or not cfg.get('ok_seconds'): return None
    now = time.time() if now is None else now
    asked = ((state if state is not None else fg.load_state()).get('weigh_asked') or {}).get(serial, {})
    if now - asked.get('', 0) < WEIGH_ANY: return None
    for r in rows:
        if r['status'] not in ('TIGHT', 'SPLIT', 'SHORT') or not r['trays']: continue
        t = r['trays'][0]
        if t.get('entered') or t.get('reading') is None or now - asked.get(t['label'], 0) < WEIGH_TRAY: continue
        hopeful = t['reading'] - (t.get('reserved') or 0.0)
        if hopeful >= r['sliced'] + max(RESERVE_FLOOR[0], RESERVE_FLOOR[1] * r['sliced']): return t
    return None

def asked(serial, label, now=None):
    now = time.time() if now is None else now
    fg.update_state(lambda s: s.setdefault('weigh_asked', {}).setdefault(serial, {}).update({label: now, '': now}))


# ---------------------------------------------------------------- recording

def same_spool(a, b):
    """By the tag when both have one; otherwise by material, colour, product and full weight: two spools that agree on
    all four cannot be told apart, and a refill of one shows as a reading that rose."""
    if a.get('id') and b.get('id'): return a['id'] == b['id']
    return all(a.get(k) == b.get(k) for k in ('type', 'colour', 'fid', 'weight'))

def spool(t):
    return {'id': t.get('spool'), 'type': t['type'], 'colour': t['colour'], 'fid': t['fid'], 'weight': t['weight']}

def emptied(status):
    """The trays the status reports with no spool in them (a tray entry without a material), by label. A tray the
    status does not mention at all is not among them: a report may carry only part of the AMS."""
    out = set()
    for a in status.get('ams', {}).get('ams', []) or []:
        aid = int(a.get('id', 0))
        for t in a.get('tray', []) or []:
            if not t.get('tray_type'): out.add(f'HT-{chr(65 + aid - 128)}' if aid >= 128 else f"{chr(65 + aid)}{int(t.get('id', 0)) + 1}")
    return out

def layer_of(status):
    """The layer the printer is on; 0 before layer 1 and while it prepares (it may still show the job before's)."""
    if status.get('gcode_state') == 'PREPARE': return 0
    try: return max(0, int(status.get('layer_num')))
    except (TypeError, ValueError): return 0

def change_found(scores):
    """The index in scores of the first print of a run of forecast errors that differs from what came before, when the
    run-length posterior after the last score holds it more likely than not that such a run began 2 to CHANGE_WITHIN
    prints ago (and not with the first score); else None."""
    if len(scores) < 3: return None
    post = st.run_lengths([scaled(s, 'learned') for s in scores], HAZARD, CHANGE_PRIOR, CHANGE_ODD)[-1]
    top = min(CHANGE_WITHIN, len(scores) - 1)
    if sum(post[2:top + 1]) <= 0.5: return None
    r = max(range(2, top + 1), key=lambda i: post[i])
    return len(scores) - r


class Recorder:
    """Records the jobs one printer prints, for learning. look() is given the printer's whole status at every look of
    the follower. Each job's records are written when it ends: seen to finish (SETTLE seconds after FINISH), failed or
    cancelled, or replaced by another job."""
    def __init__(self, serial):
        self.serial, self.job, self.last = serial, None, None          # last: (when, state, layer, {label: tray}) of the look before

    def look(self, status, now=None):
        now = time.time() if now is None else now
        state, key, layer = status.get('gcode_state'), gl.job_key(status), layer_of(status)
        trays, empty = {t['label']: t for t in fg.trays_of(status)[0]}, emptied(status)
        last, self.last = self.last, (now, state, layer, trays)
        if self.job and (self.job['key'] != key or (self.job['finished'] and state != 'FINISH')):   # the next job, also with the same name (LAN-only)
            self.close(None if self.job['finished'] else 'the printer showed another job before this one was seen to finish', now)
        if self.job is None and state in fg.BUSY: self.job = self.open(status, key, now)
        job = self.job
        if job is None: return
        if last and now - last[0] > gl.GAP: self.unsound(f'not followed for {(now - last[0]) / 60:.0f} minutes')
        if status.get('mapping') != job['mapping'] and not job.get('remapped'):        # an AMS that refills from another tray may do this: the trace shows it
            job['remapped'] = [layer, status.get('mapping')]; self.unsound(f'its trays changed at layer {layer}')
        stepped = last and last[1] in fg.BUSY and state in ('RUNNING', 'PAUSE') and layer == last[2] + 1 and now - last[0] <= SAMPLE_SPAN
        for label, tr in job['trays'].items():
            t = trays.get(label)
            if t is None:
                if label not in empty or state not in ('RUNNING', 'PAUSE') or tr.get('gone'): continue
                if not tr.get('empty'): tr['empty'] = layer                     # the first look that shows it empty: one more to be sure
                else: tr['gone'] = True; self.gone(label, tr, tr['empty'], now, status)
                continue
            tr['empty'] = None
            g = t['grams']
            if g is None: continue
            if not same_spool(tr['spool'], spool(t)): self.unsound(f'{label} holds another spool')
            if tr['last'] is not None and g > tr['last']: self.unsound(f'{label} read {tr["last"]:.0f} g, then {g:.0f} g')
            if g <= 0: self.unsound(f'{label} read 0 g' + (f' (it read {tr["last"]:.0f} g before)' if tr['last'] is not None else ''))
            if tr['last'] is not None and g < tr['last'] and same_spool(tr['spool'], spool(t)): tr['fed'] = tr['last'] if tr.get('fed') is None else min(tr['fed'], tr['last'])
            tr['last'] = g
            if tr['before'] is None and layer < 1 and state in fg.BUSY: tr['before'] = g
            if stepped:
                if layer == 1: tr['start'] = g
                first = tr['samples'][0][0] if tr['samples'] else layer
                if (layer - first) % tr['stride'] == 0:                       # thinned evenly: every stride-th layer from the first sample
                    tr['samples'].append([layer, g])
                    if len(tr['samples']) > SAMPLES: tr['stride'] *= 2; tr['samples'] = [x for x in tr['samples'] if (x[0] - first) % tr['stride'] == 0]
            if state == 'FINISH':
                if tr['end_first'] is None: tr['end_first'] = g
                tr['end'] = g
        if state == 'FINISH':
            job['finished'] = job['finished'] or now
            if now - job['finished'] >= SETTLE: self.close(None, now)
        elif state not in fg.BUSY: self.close('failed or cancelled' if state == 'FAILED' else f'it stopped without finishing ({state})', now)

    def open(self, status, key, now):
        """A job record, or None when the job's trays cannot be named: then nothing of it can be told by tray."""
        trays = fg.trays_of(status)[0]
        s, pairs, _ = gl.paired(status, fg.load_state().get('slices', []), [t['label'] for t in trays])
        labels = gl.mapped_labels(status)
        drawn = {l for l, _ in pairs} or ({l for l in labels if l} if labels and None not in labels else set())
        if not drawn: return None
        parts = {part: fg.load_profile(s, part) for part in ('layers', 'purge', 'pushed')} if pairs else {}
        if not parts.get('layers') or any(f['slot'] not in parts['layers'] for _, f in pairs): parts = None
        by_label = {t['label']: t for t in trays}
        return {'key': key, 'name': status.get('subtask_name') or '', 'opened': now, 'mapping': status.get('mapping'), 'profile': parts,
                'slots': {l: [f['slot'] for m, f in pairs if m == l] for l in drawn}, 'sound': True, 'why': [], 'finished': None,
                'trays': {l: {'spool': spool(by_label[l]), 'before': None, 'start': None, 'samples': [], 'stride': 1, 'end_first': None, 'end': None,
                              'last': None, 'fed': None} for l in drawn if l in by_label and by_label[l]['grams'] is not None}}

    def unsound(self, why):
        if why not in self.job['why']: self.job['sound'] = False; self.job['why'].append(why)

    @staticmethod
    def given(job, label, first, last=None):
        """(printed, purge) grams the job's profile has the tray give from layer `first` up to layer `last` (not included;
        None: to the end), the purge as the spool gave it (the header's split moved back)."""
        p, printed, purge = job['profile'], 0.0, 0.0
        for slot in job['slots'][label]:
            pushed = (p.get('pushed') or {}).get(slot) or [0.0] * len(p['layers'][slot])
            for n in range(first, len(p['layers'][slot]) if last is None else last):
                printed += p['layers'][slot][n] - p['purge'][slot][n]; purge += p['purge'][slot][n] - pushed[n]
        return printed, purge

    def gone(self, label, tr, layer, now, status):
        """A tray the job draws on was reported empty in two looks in a row while the job ran (from `layer`): a run-dry when
        its last reading was low enough and the profile has the print drawing on it then; else its spool was taken out."""
        reading, job = tr['last'], self.job
        if reading is None: return
        if not job['profile']:                                         # what the print draws on, and when, cannot be told
            fg.log(f'{label} emptied during "{job["name"]}", a print not sliced on this computer: nothing learned from it')
            self.unsound(f'{label} emptied'); return
        feeding = sum(self.given(job, label, max(0, layer - 1), layer + 1)) > 0
        if reading > DRY_MOST or not feeding:
            fg.log(f'{label}: a spool reading {reading:.0f} g was taken out during "{job["name"]}" at layer {layer}'
                   + ('' if feeding else ', where the print was not drawing on it') + ': not a run-dry, nothing learned from it')
            self.unsound(f'{label}: its spool was taken out'); return
        needed = sum(self.given(job, label, layer))
        keep(self.serial, label, 'dry', {'at': now, 'job': job['name'], 'reading': reading, 'layer': layer, 'needed': needed, 'spool': tr['spool'],
                                         'print_error': status.get('print_error'), 'hms': status.get('hms')})
        fg.log(f'{label} ran dry at layer {layer} of "{job["name"]}" reading {reading:.0f} g; the job still needed {needed:.1f} g of it')
        self.unsound(f'{label} ran dry')

    @staticmethod
    def fed_at(tr):
        """The lowest reading at which the tray was seen still giving filament in this job (a reading followed by a lower
        one), or None."""
        seq = ([tr['before']] if tr['before'] is not None else []) + [r for _, r in tr['samples']] + ([tr['end']] if tr['end'] is not None else [])
        return min((a for a, b in zip(seq, seq[1:]) if b < a), default=None)

    def close(self, why, now=None):
        """Writes the job's records in one go: for every tray, the lowest reading it fed at; with a profile, its record and
        score (forecast by the model as it stood before this job); then looks for a change in each scored tray's errors."""
        job, self.job = self.job, None
        now = time.time() if now is None else now
        if why: job['sound'] = False; job['why'].append(why)
        with fg.state_lock():
            history = load()
            est, scored, notes = estimates(history.get(self.serial, {})), [], []
            for label, tr in job['trays'].items():
                fed = min([x for x in (tr.get('fed'), self.fed_at(tr)) if x is not None], default=None)
                if fed is not None: add(history, self.serial, label, 'seen', {'at': job['opened'], 'reading': fed, 'spool': tr['spool'], 'job': job['name']})
                if not job['profile'] or (not tr['samples'] and tr['before'] is None): continue
                first = tr['samples'][0][0] if tr['samples'] else 1
                to = None if job['finished'] else (tr['samples'][-1][0] if tr['samples'] else first)
                printed, purge = self.given(job, label, first, to)
                layer0 = sum(self.given(job, label, 0, 1))
                whole = bool(job['finished'] and tr['start'] is not None and first == 1 and tr['end'] is not None)
                record = {'at': job['opened'], 'job': job['name'], 'spool': tr['spool'], 'before': tr['before'], 'start': tr['start'], 'samples': tr['samples'],
                          'end_first': tr['end_first'], 'end': tr['end'], 'layer0': layer0, 'whole': whole, 'fed': fed,
                          'sliced': {'from': first, 'to': to, 'printed': printed, 'purge': purge}, 'remapped': job.get('remapped'),
                          'sound': job['sound'], 'why': list(job['why'])}
                n = printed + purge + layer0
                if job['sound'] and whole and tr['before'] is not None and n >= RATE_JOB_GRAMS:
                    learned, applied = forecast(est['trays'].get(label) or est['blank'], n)
                    record['score'] = {'n': n, 'fell': tr['before'] - tr['end'], 'learned': learned, 'applied': applied}
                    scored.append(label)
                add(history, self.serial, label, 'jobs', record)
                notes.append(label)
            for label in scored:
                h = history[self.serial][label]
                start = max([c['at'] for c in h.get('changes', [])] or [0])
                jobs = [j for j in since(h['jobs'], start) if j.get('score')]
                i = change_found([j['score'] for j in jobs])
                if i is not None:
                    add(history, self.serial, label, 'changes', {'at': jobs[i]['at'], 'found': now, 'job': jobs[i]['job']})
                    fg.log(f'{label}: its readings changed from "{jobs[i]["job"]}" on: what was learned of it before no longer counts')
            save(history)
        if notes: fg.log(f'recorded "{job["name"]}" for learning: {", ".join(notes)}' + ('' if job['sound'] else f' (not used: {"; ".join(job["why"])})'))


# ---------------------------------------------------------------- the command

def many(n, word):
    return f"{n} {word}{'s' if n != 1 else ''}"

def printed_and_purged(jobs):
    """(per gram printed, per gram purged) the reading fell by, least squares over sound whole jobs, or None when their
    purge shares do not differ enough to tell the two apart (at least 3 jobs, 10 points between the shares)."""
    rows = [(j['sliced']['printed'], j['sliced']['purge'], j['start'] - j['end']) for j in jobs if j.get('sound') and j.get('whole')]
    shares = [q / (p + q) for p, q, _ in rows if p + q > 0]
    if len(rows) < 3 or not shares or max(shares) - min(shares) < 0.10: return None
    a, b, c = sum(p * p for p, _, _ in rows), sum(p * q for p, q, _ in rows), sum(q * q for _, q, _ in rows)
    y1, y2, det = sum(p * y for p, _, y in rows), sum(q * y for _, q, y in rows), a * c - b * b
    if det <= 1e-9 * a * c: return None
    return (c * y1 - b * y2) / det, (a * y2 - b * y1) / det

def report(history, names=None, learning=True, cfg=None):
    """learned: the text, line by line."""
    names, on = names or {}, (cfg or {}).get('measured_reserve', False)
    lines = [f"Learning from prints is {'on' if learning else 'off'}" + ('' if history else ': nothing recorded yet')]
    for serial, by_tray in sorted(history.items()):
        est = estimates(by_tray); p = est['printer']
        lines.append(names.get(serial, serial))
        need = lacking(p)
        lines.append('  reserve: ' + ('the fixed one (measuring it from past prints is experimental, and off)' if not on else
                                      f"the fixed one; to be measured it still needs {' and '.join(need)}" if need else 'measured, for a tray that has a reading at empty of its own')
                     + (f"; forecasts fell short by up to {p['q']:.3f} x (print + {MARGIN_K:.0f} g)" if p['q'] is not None else ''))
        if p['rho'] is not None:
            lines.append(f"  printer: readings fall {p['rho']:.3f} g per sliced gram over {many(p['ratios'], 'print')} (ratios clipped to {p['clip'][0]:.3f}..{p['clip'][1]:.3f}); "
                         + ('trays are drawn to it fully (they cannot be told apart)' if p['k_rho'] is None else f"a tray's own prints count against {p['k_rho']:.1f} of the printer's"))
            lines.append('  by filament product (a report only): ' + '; '.join(f"{fid} {r:.3f} over {n} print{'s' if n != 1 else ''}" + (f" ({ci[0]:.3f}..{ci[1]:.3f})" if ci else '')
                                                                              for fid, (r, n, ci) in sorted(p['products'].items())))
        for label, h in sorted(by_tray.items()):
            t, jobs, dries = est['trays'][label], h.get('jobs', []), h.get('dry', [])
            lines.append(f"  {label}: {len(jobs)} print{'s' if len(jobs) != 1 else ''} recorded, {sum(1 for j in jobs if j.get('sound'))} sound, {len(t['scores'])} scored"
                         + (f"; {len(dries)} run-dr{'ies' if len(dries) != 1 else 'y'}" if dries else '') + (f"; weighed {len(h['weighs'])} time{'s' if len(h['weighs']) != 1 else ''}" if h.get('weighs') else ''))
            if t['change']: lines.append(f"    a change in its readings was noticed on {day(t['change']['found'])}, from \"{t['change']['job']}\": what came before no longer counts")
            if t['scores']:
                err = lambda f: sum(abs(s['fell'] - s[f]) for s in t['scores']) / len(t['scores'])
                lines.append(f"    forecast of the fall, off by on average: learned {err('learned'):.1f} g, without learning {err('n'):.1f} g"
                             + (f"; the learned one was worse in {t['worse']} of the last {t['pairs']}: its rate and pre are set aside" if t['aside'] else ''))
            else: lines.append('    no scored print yet: it needs a sound whole print followed from before its first layer')
            m, n, _ = t['rho_own']
            ci = t['interval']
            lines.append('    rate  ' + ('-' if t['rho'] is None else f"{t['rho']:.3f} g per sliced gram")
                         + (f" (its own {m:.3f} over {len(t['rated'])} print{'s' if len(t['rated']) != 1 else ''}" + (f", 90 % interval {ci[0]:.3f}..{ci[1]:.3f}" if ci else '') + ')' if m is not None else
                            ' (no sound whole print of its own: the printer\'s)' if t['rho'] is not None else ': no sound whole print on this printer yet'))
            lines.append('    pre   ' + ('-' if t['pre'] is None else f"{t['pre']:.1f} g") + (f" (its own {t['loss'][0]:.1f} g over {many(len(t['lost']), 'print')})" if t['loss'][0] is not None else ''))
            seen = [v for v, _ in t['low']]
            source = {'own': 'from its own spools', 'printer': "from the printer's spools", 'spread': 'its spools, with their spread from spool to spool'}[t['a_from']]
            lines.append('    empty ' + (f"{t['a']:.0f} g: the most it reads with its spool empty ({source})" if t['a'] is not None else 'what it reads with its spool empty is not known') + '; seen: '
                         + (', '.join(f"{v:.0f} g {'ran dry' if k == 'dry' else 'weighed near empty'}" for k, v, _ in t['exact']) or 'no run-dry or weigh-in near empty')
                         + (f"; {len(seen)} spool{'s' if len(seen) != 1 else ''} still feeding at {min(seen):.0f} g or more" if seen else ''))
            for d in dries[-3:]:
                lines.append(f"      ran dry {time.strftime('%d %b %H:%M', time.localtime(d['at']))} at layer {d['layer']} of \"{d['job']}\", reading {d['reading']:.0f} g"
                             + (f", {d['needed']:.1f} g still to go" if d.get('needed') is not None else ''))
            if measured(est, t, on): lines.append(f"    margin {t['q']:.3f} x (print + {MARGIN_K:.0f} g): the measured reserve is in use")
            else:
                why = need + [x for x in ('a reading at empty of its own' if len(t['spools']) < OWN_EXACT_MIN else '', 'the setting is off' if not on else '') if x]
                lines.append('    margin not in use: ' + ('; '.join(why) or 'no bound yet'))
            split = printed_and_purged(jobs)
            lines.append('    per gram printed / purged: ' + (f'{split[0]:.3f} / {split[1]:.3f} g (a report only, not used)' if split else 'not enough to tell'))
    return lines
