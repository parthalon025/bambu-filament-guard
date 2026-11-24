"""The watcher, the card and the commands, as the running guard drives them (filament_guard.Guard, guard_ui)."""
import io
import sys
import threading
import time

import pytest

import filament_guard as fg
import guard_learn as gn
from helpers import CFG, MEASURED, gcode, sliced, e, kept, running, BLUE, benchy, run_job, blue_reads


class Card:
    """The card, as the guard sees it: what it was asked to show, in order; of the last, the answer (res), the lines and
    buttons of a note (said, buttons), and the dialog it stands beside (where)."""
    def __init__(self): self.win, self.one_line, self.shown, self.beside = None, False, [], None
    def is_open(self): return self.win is not None
    def line(self, text, tone='muted', seconds=0, pid=None, hold=False):
        self.win, self.one_line = object(), True; self.shown.append(('line', text, hold)); return self.win
    def show(self, res, brief=0, beside=None, hold=False): self.win, self.one_line, self.res, self.where = object(), bool(brief), res, beside; self.shown.append(('show', brief))
    def note(self, headline, lines, tone='muted', pid=None, beside=None, buttons=()):
        self.win, self.one_line, self.said, self.where, self.buttons = object(), False, [text for _, text in lines], beside, dict(buttons); self.shown.append(('note', headline))
    def close(self): self.win = None; self.shown.append(('close',))
    def follow(self): pass

def guard_with(cfg=CFG):
    guard = fg.Guard(dict(cfg), type('Root', (), {'quit': None})(), standing=False); guard.card = Card()
    return guard

def run_queued(guard):
    while not guard.todo.empty(): guard.todo.get_nowait()()

ENOUGH = {'gcode_state': 'IDLE', 'ams': {'ams': [{'id': '0', 'tray': [{'id': '0', 'tray_type': 'PLA', 'tray_color': '0A2989FF', 'remain_g': 500},
                                                                       {'id': '1', 'tray_type': 'PLA', 'tray_color': 'C12E1FFF', 'remain_g': 500}]}]}}

def tk_stand_in(monkeypatch):
    """Puts a stand-in in tkinter's place (there is none where no screen is) and returns the widgets made on it, in
    order: each keeps what it was made and configured with (k) and the keys bound to it (keys)."""
    made = []
    class Widget:
        def __init__(self, *a, **k): self.k, self.keys = dict(k), {}; made.append(self)
        def configure(self, **k): self.k.update(k)
        def bind(self, key, then): self.keys[key] = then
        def destroy(self): self.k['destroyed'] = True
        def winfo_reqheight(self): return 100
        def __getitem__(self, key): return self.k.get(key, '')
        def __getattr__(self, name): return lambda *a, **k: None
    class Var:
        def __init__(self, value=''): self.value, self.watch = value, []
        def get(self): return self.value
        def set(self, value): self.value = value; [w() for w in self.watch]
        def trace_add(self, how, then): self.watch.append(then)
    monkeypatch.setitem(sys.modules, 'tkinter', type(sys)('tkinter'))
    for name in ('Toplevel', 'Frame', 'Label', 'Button', 'Entry'): setattr(sys.modules['tkinter'], name, type(name, (Widget,), {}))
    sys.modules['tkinter'].StringVar = Var
    return made

def card_shown(monkeypatch, res, brief=0):
    """The card guard_ui makes for an answer, on the stand-in for tkinter: (the texts of its labels, the texts of its
    buttons, what happened in order: 'placed' when the card was put on the screen, and each thing it told the guard)."""
    import guard_ui
    made, order = tk_stand_in(monkeypatch), []
    def opened(card, pid=None, beside=None, hold=False):
        card.win, card.t, card.owner, card.beside, card.held, card.closes_after = sys.modules['tkinter'].Toplevel(), guard_ui.THEMES['dark'], None, None, False, 0
        return card.win
    monkeypatch.setattr(guard_ui.Card, '_open', opened); monkeypatch.setattr(guard_ui.Card, '_place', lambda card: order.append('placed'))
    guard_ui.Card(None, act=lambda name, res, arg=None: order.append((name, arg))).show(res, brief)
    return [w.k['text'] for w in made if type(w).__name__ == 'Label'], [w.k['text'] for w in made if type(w).__name__ == 'Button'], order

def amount_window(monkeypatch, tray, weigh=False):
    """The window guard_ui opens for a tray's amount, on the stand-in for tkinter, starting from an empty spool of 200 g:
    (the scale's field, the empty spool's, the label of the result, the label of what is wrong, what Save does, the
    buttons' texts, what the window told the guard)."""
    import guard_ui
    made, acts = tk_stand_in(monkeypatch), []
    card = guard_ui.Card(None, act=lambda name, res, arg=None: acts.append((name, arg))); card.t = guard_ui.THEMES['dark']; card.empty_spool = 200
    card.ask_amount(None, tray, weigh)
    scale, spool = [w.k['textvariable'] for w in made if type(w).__name__ == 'Entry']
    result, wrong = [w for w in made if type(w).__name__ == 'Label'][-2:]
    return scale, spool, result, wrong, next(w.k['command'] for w in made if w.k.get('text') == 'Save'), [w.k['text'] for w in made if type(w).__name__ == 'Button'], acts

def slice_file(tmp_path, text):
    meta = tmp_path / 'bamboo_model' / 'Thu_Nov_19' / '01_02_03#1#9' / 'Metadata'; meta.mkdir(parents=True, exist_ok=True)
    (meta.parent / 'origin.txt').write_text(r'C:\prints\Benchy.3mf')
    f = meta / '.1.0.gcode'; f.write_text(text)
    return f

@pytest.mark.windows
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

def test_slice_the_guard_cannot_read_is_reported_once(tmp_path, monkeypatch):
    meta = tmp_path / 'bamboo_model' / 'Thu_Nov_19' / '01_02_03#1#9' / 'Metadata'; meta.mkdir(parents=True)
    monkeypatch.setattr(fg.tempfile, 'gettempdir', lambda: str(tmp_path)); monkeypatch.setattr(fg, 'BS_DATA', str(tmp_path / 'none'))
    watcher = fg.Watcher(); f = meta / '.1.2.gcode'; f.write_text(gcode(used=None))                   # totals that cannot be placed
    watcher.new_plates(); monkeypatch.setattr(fg.time, 'time', lambda: f.stat().st_mtime + 5)
    assert watcher.new_plates() == [] and [p for p, _ in watcher.problems] == [str(f)] and fg.plate_number(str(f)) == 3
    watcher.problems.clear(); assert watcher.new_plates() == [] and watcher.problems == []

@pytest.mark.windows
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

@pytest.mark.windows
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

@pytest.mark.windows
def test_a_new_slice_says_at_once_that_it_is_being_checked(state, tmp_path, monkeypatch):
    f = slice_file(tmp_path, gcode()); plate = fg.parse_plate(f.read_text()); running(monkeypatch, ENOUGH)
    guard = guard_with(); guard.take_slice(str(f), plate); run_queued(guard)
    assert guard.card.shown == [('line', 'Checking the spools on the Bambu Lab H2D…', True), ('show', CFG['ok_seconds'])]   # the answer takes its place
    guard.card.note('Self-test: the guard works', []); guard.take_slice(str(f), plate); run_queued(guard)
    assert [kind for kind, *_ in guard.card.shown[2:]] == ['note', 'show']                # a full card that stands is not replaced by the line
    guard = guard_with(dict(CFG, ok_seconds=0)); guard.take_slice(str(f), plate); run_queued(guard)
    assert guard.card.shown == [] and not guard.busy                                       # quiet was asked for: neither the line nor the answer

@pytest.mark.windows
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

@pytest.mark.windows
def test_the_checking_line_never_stays(state, tmp_path, monkeypatch):
    f = slice_file(tmp_path, gcode()); monkeypatch.setattr(fg, 'newest_gcode', lambda pid=None: str(f)); running(monkeypatch, ENOUGH)
    monkeypatch.setattr(fg, 'evaluate', lambda *a, **k: 1 / 0)
    guard = guard_with(); guard.check_newest(); guard.todo.get(timeout=5)()               # the check failed
    assert guard.card.shown == [('line', 'Checking the spools on the Bambu Lab H2D…', False), ('close',)] and not guard.busy
    f.write_text(gcode(weights='0,0'))                                                    # a plate that needs nothing: no answer
    guard = guard_with(); guard.check_newest(); guard.todo.get(timeout=5)()
    assert guard.card.shown[-1] == ('close',) and not guard.busy

@pytest.mark.windows
def test_the_guard_says_once_that_it_is_running(state, monkeypatch):
    import guard_ui
    windows = {}; monkeypatch.setattr(guard_ui, 'studio_windows', lambda *a: windows)
    guard = guard_with(); guard.greet()
    assert guard.card.shown == []                                                          # Bambu Studio not open: later
    windows[1234] = 99; guard.greet(); guard.card.close(); guard.greet()
    assert guard.card.shown == [('line', f'Bambu Filament Guard {fg.VERSION} is watching for sliced plates', True), ('close',)]   # held while minimised
    quiet = guard_with(dict(CFG, ok_seconds=0)); quiet.greet()
    assert quiet.card.shown == []

@pytest.mark.windows
def test_setup_starts_the_guard_without_its_start_line(monkeypatch):
    """Setup shows a card of its own in the corner where the start line would stand."""
    started = []
    monkeypatch.setattr(fg, 'is_running', lambda: False); monkeypatch.setattr(fg.subprocess, 'Popen', lambda cmd, **how: started.append(cmd[2:]))
    fg.start(); fg.start(start_line=False)
    assert started == [['watch'], ['watch', '--no-start-line']]

@pytest.mark.windows
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

@pytest.mark.windows
def test_a_plate_that_cannot_be_read_at_send_print_job_leaves_the_others_checked(state, tmp_path, monkeypatch):
    import guard_ui
    good = slice_file(tmp_path, gcode()); bad = good.with_name('.1.1.gcode'); bad.write_text(gcode(used=None))   # its totals cannot be placed
    monkeypatch.setattr(fg, 'newest_gcode', lambda pid=None: str(bad)); monkeypatch.setattr(fg, 'temp_gcodes', lambda: [str(bad), str(good)])
    monkeypatch.setattr(guard_ui, 'visible', lambda hwnd: True); running(monkeypatch, ENOUGH)
    guard = guard_with(); guard.check_newest(1, beside=4321); guard.todo.get(timeout=5)()
    log = (state / 'guard.log').read_text()
    assert guard.last['plate_no'] == 1 and log.count('at "Send print job"') == 1 and f'cannot read the slice {bad}: ValueError' in log

@pytest.mark.windows
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

@pytest.mark.windows
def test_the_newest_slice_that_cannot_be_read_says_so_on_a_card(state, tmp_path, monkeypatch):
    f = slice_file(tmp_path, gcode(used=None)); monkeypatch.setattr(fg, 'newest_gcode', lambda pid=None: str(f)); running(monkeypatch, ENOUGH)
    guard = guard_with(); guard.check_newest(); guard.todo.get(timeout=5)()
    assert guard.card.shown == [('line', 'Checking the spools…', False), ('note', 'Filament could not be checked')] and not guard.busy

@pytest.mark.windows
def test_the_self_test_says_whether_learning_is_on_and_what_it_holds(state, monkeypatch):
    monkeypatch.setattr(fg, 'bs_conf', lambda: {'app': {'version': '02.09.01.70'}}); monkeypatch.setattr(fg, 'newest_gcode', lambda pid=None: None)
    benchy(); run_job(gn.Recorder('SERIAL'))
    assert (True, 'Learning from prints is on: 1 tray record(s) of past prints, 1 sound ("learned" says more)') in fg.selftest(CFG)
    assert (None, 'Learning from prints is off: 1 tray record(s) of past prints, 1 sound ("learned" says more)') in fg.selftest(dict(CFG, learn=False))

@pytest.mark.windows
def test_a_closed_warning_does_not_hold_back_the_next_answer(state):
    """"Slice all": a fine plate does not wipe another plate's warning; once that warning is closed, it is no reason."""
    guard = guard_with()
    def answer(plate_no, status):
        return {'title': 'Benchy', 'plate_no': plate_no, 'error': None, 'worst': status, 'rows': [{'slot': 1, 'status': status}], 'printer': {'model': 'Bambu Lab H2D', 'state': 'IDLE'}}
    guard.deliver(answer(1, 'SHORT')); guard.card.close()
    guard.checking('slice', 'Checking the spools on the Bambu Lab H2D…', asked=False); guard.deliver(answer(2, 'OK')); guard.checked('slice')
    assert guard.card.shown[-1] == ('show', CFG['ok_seconds']) and guard.last['plate_no'] == 2

@pytest.mark.windows
def test_the_card_offers_a_weigh_in_records_it_and_checks_again(state, monkeypatch):
    running(monkeypatch, blue_reads(110))
    res = fg.evaluate(BLUE, CFG, 'Benchy')
    assert res['rows'][0]['status'] == 'TIGHT' and res['weigh']['label'] == 'C3' and res['weigh']['reading'] == 110
    assert res['printer']['reserve'] is None                                                # the measured reserve is off: the card has nothing to say of the reserve
    assert fg.evaluate(BLUE, MEASURED, 'Benchy')['printer']['reserve'] == 'the reserve is the fixed one; to be measured it still needs 9 more scored prints and readings at empty of 3 spools in one tray, or of 2 in each of two'
    guard, again = guard_with(), []
    monkeypatch.setattr(guard, 'recheck', lambda res, beside=None: again.append(res['title']))
    guard.act('asked', res, res['weigh'])
    assert fg.load_state()['weigh_asked']['SERIAL']['C3'] == pytest.approx(time.time(), abs=60) and 'weigh' not in fg.evaluate(BLUE, CFG, 'Benchy')
    guard.act('weigh', res, (res['weigh'], 92.0))
    [w] = gn.load()['SERIAL']['C3']['weighs']
    assert (w['reading'], w['weight'], w['spool']['type'], w['spool']['colour']) == (110, 92.0, 'PLA', '#0A2989') and again == ['Benchy']
    assert fg.evaluate(BLUE, CFG, 'Benchy')['rows'][0]['trays'][0]['grams'] == 92.0            # weighed near empty: 18 g the tray reads at empty, and the spool's own

@pytest.mark.windows
def test_the_empty_spool_typed_is_what_the_next_amount_window_starts_with(state):
    guard = guard_with(); guard.card = type('Card', (), {'beside': None})()
    assert fg.DEFAULTS['empty_spool_g'] == 250
    guard.act('spool', None, 212.0)
    assert guard.card.empty_spool == 212.0 and fg.load_config()['empty_spool_g'] == 212.0 and guard.cfg['empty_spool_g'] == 212.0

@pytest.mark.windows
def test_the_amount_window_takes_the_scale_and_the_empty_spool(monkeypatch):
    tray = {'label': 'Ext', 'name': '', 'type': 'PETG', 'grams': None}
    scale, spool, result, wrong, save, buttons, acts = amount_window(monkeypatch, tray)
    assert (scale.get(), spool.get(), result['text'], buttons) == ('', '200', '', ['Save'])        # nothing entered yet; the spool's weight of last time
    scale.set('950'); assert result['text'] == '750 g of filament'
    spool.set('1000'); save()
    assert result['text'] == '' and 'no filament' in wrong['text'] and acts == []                  # not saved, and it says why
    spool.set('230'); assert wrong['text'] == '' and result['text'] == '720 g of filament'
    save(); assert acts == [('amount', (tray, 720.0)), ('spool', 230.0)]                            # the amount first, then the spool's weight for next time
    entered = dict(tray, grams=412.4, entered=1e6)
    scale, spool, result, wrong, save, buttons, acts = amount_window(monkeypatch, entered)
    assert (scale.get(), spool.get(), result['text'], buttons) == ('', '200', '', ['Save', 'Forget this amount'])   # the row's own figure is not where the window starts: see the ledger's test
    spool.set(''); scale.set('300'); save()                                                        # the filament weighed alone: no spool's weight to remember
    assert acts == [('amount', (entered, 300.0))]
    measured = dict(tray, label='C3', grams=110.0, reading=110.0)
    scale, spool, result, wrong, save, buttons, acts = amount_window(monkeypatch, measured, weigh=True)
    assert (scale.get(), buttons) == ('', ['Save'])                                                # a weigh-in is never guessed from the reading
    scale.set('292'); save(); assert acts == [('weigh', (measured, 92.0)), ('spool', 200.0)]

@pytest.mark.windows
def test_a_weigh_in_far_from_the_reading_is_saved_only_when_saved_again(monkeypatch):
    """The review's slips: 8 typed for 86, another spool on the scale, a spool weighed after the running print had taken
    60 g of it. Each was kept as what the tray reads at empty and counted every tray of the printer lower."""
    c3 = {'label': 'C3', 'name': '', 'type': 'PLA', 'grams': 110.0, 'reading': 110.0}
    scale, spool, result, wrong, save, buttons, acts = amount_window(monkeypatch, c3, weigh=True)
    scale.set('208'); save()                                                                       # 8 g of filament, where 86 was meant
    assert acts == [] and wrong['text'] == '8 g weighed, 110 g read by the printer: 102 g apart. Save again if the weight is right.'
    scale.set('286'); assert wrong['text'] == '' and result['text'] == '86 g of filament'          # put right: the line goes
    save(); assert acts == [('weigh', (c3, 86.0)), ('spool', 200.0)]                               # 24 g apart: saved at the first Save
    scale, spool, result, wrong, save, buttons, acts = amount_window(monkeypatch, c3, weigh=True)
    scale.set('208'); save(); assert acts == []
    save(); assert acts == [('weigh', (c3, 8.0)), ('spool', 200.0)]                                # saved again with the same numbers: taken as right
    scale, spool, result, wrong, save, buttons, acts = amount_window(monkeypatch, c3, weigh=True)
    scale.set('208'); save(); spool.set('190'); save()                                             # a number changed in between: asked anew
    assert acts == [] and wrong['text'].startswith('18 g weighed, 110 g read by the printer: 92 g apart')
    scale.set('370'); save(); assert acts == [] and '180 g weighed' in wrong['text'] and '70 g apart' in wrong['text']   # more than it reads, too
    scale.set('350'); save(); assert acts == [('weigh', (c3, 160.0)), ('spool', 190.0)]            # 50 g apart is within what a gauge can be out
    ext = {'label': 'Ext', 'name': '', 'type': 'PETG', 'grams': 400.0, 'entered': 1e6, 'reading': 400.0}
    scale, spool, result, wrong, save, buttons, acts = amount_window(monkeypatch, ext)
    scale.set('205'); save(); assert acts == [('amount', (ext, 5.0)), ('spool', 200.0)]            # an amount entered for a spool the printer cannot measure has no reading to doubt it by

@pytest.mark.windows
def test_the_card_says_when_what_was_learned_could_not_be_used(state, monkeypatch):
    """The check went on as with learning off: the full card says so under its table, the one line beside its count."""
    running(monkeypatch, blue_reads(120))
    with monkeypatch.context() as m:
        m.setattr(gn, 'load', lambda: (_ for _ in ()).throw(KeyError('at'))); res = fg.evaluate(BLUE, CFG, 'Benchy')
    assert res['error'] is None and res['worst'] == 'OK' and res['printer']['unlearned']
    labels, buttons, _ = card_shown(monkeypatch, res)
    assert labels[-1].endswith("the amounts are the AMS's estimate. What was learned from previous prints could not be used (KeyError): this check is as without it.")
    labels, buttons, _ = card_shown(monkeypatch, res, brief=8)
    assert labels[-1] == '1 filament against the spools on the Bambu Lab H2D; what was learned could not be used' and buttons == ['Details']
    assert not any('learned' in text for text in card_shown(monkeypatch, fg.evaluate(BLUE, CFG, 'Benchy'))[0])

@pytest.mark.windows
def test_the_card_is_on_the_screen_before_its_weigh_in_offer_is_noted(state, monkeypatch):
    """The card is made unseen and shown by its last step. The note that a weigh-in was offered writes the state file,
    which can be held elsewhere for seconds and then fail: made before that last step, it left a warning that never
    showed. It comes after it, and a note that fails is logged, not raised into the card."""
    running(monkeypatch, blue_reads(110)); res = fg.evaluate(BLUE, CFG, 'Benchy')
    assert res['worst'] == 'TIGHT' and res['weigh']['label'] == 'C3'
    labels, buttons, order = card_shown(monkeypatch, res)
    assert buttons[-1] == 'Weigh C3…' and order == ['placed', ('asked', res['weigh'])]
    assert card_shown(monkeypatch, res, brief=8)[2] == ['placed']                             # the one line has no such button: nothing was offered
    guard = guard_with()
    for error in (fg.StateBusy('the state folder was held elsewhere for more than 10 s'), PermissionError(13, 'Permission denied')):
        monkeypatch.setattr(gn, 'asked', lambda *a, error=error: (_ for _ in ()).throw(error))
        guard.act('asked', res, res['weigh'])                                                  # does not raise
    log = (state / 'guard.log').read_text()
    assert 'C3: that a weigh-in was offered could not be noted (StateBusy: the state folder was held elsewhere for more than 10 s): it may be offered again' in log and 'could not be noted (PermissionError' in log
    assert 'weigh_asked' not in fg.load_state() and fg.evaluate(BLUE, CFG, 'Benchy')['weigh']['label'] == 'C3'   # not noted: offered again


@pytest.mark.windows
def test_a_filament_without_a_weight_does_not_cost_the_card_or_the_self_test(state, tmp_path, monkeypatch):
    """Bambu Studio gives a filament whose profile has density 0 no weight (parse_plate keeps it, with no grams): the
    card shows "?" for what it needs and says why, and the self-test still reads the slice."""
    import guard_ui
    text = gcode(weights='0.00,10.87', colours='#0A2989;#FFFFFF;#0A2989').replace('; filament_density: 1.26,', '; filament_density: 0,')
    running(monkeypatch, blue_reads(500)); res = fg.evaluate(fg.parse_plate(text), CFG, 'Benchy')
    labels, buttons, _ = card_shown(monkeypatch, res)
    assert res['worst'] == 'UNKNOWN' and labels[labels.index('1  Bambu PLA Basic') + 1] == '?' and labels[labels.index('3  Bambu PLA Basic') + 1] == '10.9 g'
    assert "Bambu Studio gives no weight for this filament (its profile's density is 0)" in labels
    f = slice_file(tmp_path, text); monkeypatch.setattr(fg, 'newest_gcode', lambda pid=None: str(f)); monkeypatch.setattr(fg, 'bs_conf', lambda: {'app': {'version': '02.09.01.70'}})
    monkeypatch.setattr(guard_ui, 'studio_windows', lambda: {}); monkeypatch.setattr(fg, 'is_running', lambda: True)
    assert [text[-21:] for mark, text in fg.selftest(CFG) if mark and text.startswith('Newest slice')] == [': 2 filament(s), 11 g']

def spools(*trays):
    """The H2D idle, with these PLA spools in its first AMS: (colour, grams) each."""
    return {'gcode_state': 'IDLE', 'ams': {'ams': [{'id': '0', 'tray': [{'id': str(i), 'tray_type': 'PLA', 'tray_color': colour + 'FF', 'remain_g': grams, 'tray_weight': '1000'}
                                                                         for i, (colour, grams) in enumerate(trays)]}]}}

BLUE_PLA, RED_PLA, GREEN_PLA = '0A2989', 'C12E1F', '00AE42'                                # gcode() needs 9.99 g of the blue and 10.87 g of the red

@pytest.mark.windows
def test_a_filament_that_falls_short_opens_the_card_whatever_the_other_rows_are(state, monkeypatch):
    """Any row that opens the card opens it. With "Warn when a colour is not loaded" unticked, a blue whose colour is
    not loaded hid the red's shortfall: both rank as the worst, the first was taken, and it was not one to warn of. One
    line for 8 seconds after the slice, nothing beside "Send print job"."""
    import guard_ui
    running(monkeypatch, spools((GREEN_PLA, 500), (RED_PLA, 2))); monkeypatch.setattr(guard_ui, 'visible', lambda hwnd: True)
    cfg = dict(CFG, popup_on=[s for s in CFG['popup_on'] if s != 'COLOUR'])
    res = fg.evaluate(fg.parse_plate(gcode()), cfg, 'Benchy'); res['plate_no'] = 1
    assert [r['status'] for r in res['rows']] == ['COLOUR', 'SHORT'] and res['worst'] == 'COLOUR'
    guard = guard_with(cfg); guard.deliver(res)
    assert guard.card.shown == [('show', 0)]                                               # after the slice: the full card
    guard = guard_with(cfg); guard.deliver(dict(res), beside=4321)
    assert guard.card.shown == [('show', 0)]                                               # and beside the dialog
    running(monkeypatch, spools((GREEN_PLA, 500), (RED_PLA, 500)))                         # the colour alone, unticked: one line, and nothing beside the dialog
    res = fg.evaluate(fg.parse_plate(gcode()), cfg, 'Benchy'); res['plate_no'] = 1
    guard = guard_with(cfg); guard.deliver(res); guard.deliver(dict(res), beside=4321)
    assert [r['status'] for r in res['rows']] == ['COLOUR', 'OK'] and guard.card.shown == [('show', CFG['ok_seconds'])]

@pytest.mark.windows
def test_not_enough_and_not_loaded_open_the_card_whatever_popup_on_holds(state, monkeypatch):
    """popup_on decides for colour, two spools, tight and unknown. A list written by hand to add UNKNOWN, as the docs
    worded it, replaced the whole default and turned the "not enough" card off; so did names in small letters."""
    running(monkeypatch, spools((BLUE_PLA, 500), (RED_PLA, 2)))
    short = fg.evaluate(fg.parse_plate(gcode()), CFG, 'Benchy')
    running(monkeypatch, spools((BLUE_PLA, 500)))
    missing = fg.evaluate(fg.parse_plate(gcode(types='PLA;PETG;ABS')), CFG, 'Benchy')
    running(monkeypatch, spools((BLUE_PLA, 500), (RED_PLA, 11)))
    tight = fg.evaluate(fg.parse_plate(gcode()), CFG, 'Benchy')
    assert [[r['status'] for r in res['rows']] for res in (short, missing, tight)] == [['OK', 'SHORT'], ['OK', 'MISSING'], ['OK', 'TIGHT']]
    for popup in (['UNKNOWN'], [], ['unknown', 'tight'], None, 'SHORT,UNKNOWN'):
        for res in (short, missing):
            guard = guard_with(dict(CFG, popup_on=popup)); guard.deliver(dict(res))
            assert guard.card.shown == [('show', 0)], popup
    for popup, brief in ((['UNKNOWN'], CFG['ok_seconds']), (['unknown', 'tight'], 0), (['Tight'], 0), ([], CFG['ok_seconds'])):
        guard = guard_with(dict(CFG, popup_on=popup)); guard.deliver(dict(tight))
        assert guard.card.shown == [('show', brief)], popup                                # tight is popup_on's to decide, in any case of letters

@pytest.mark.windows
def test_a_name_in_popup_on_the_guard_does_not_know_is_said_once_and_the_menu_still_ticks(state, monkeypatch):
    """"TIGTH" warns of nothing: the log says so, once in a run of the guard. The menu's items read the list as the guard
    does, and a click writes it back in capitals."""
    running(monkeypatch, spools((BLUE_PLA, 500), (RED_PLA, 11)))
    guard = guard_with(dict(CFG, popup_on=['short', 'TIGTH', 'colour']))
    for _ in range(3): guard.deliver(fg.evaluate(fg.parse_plate(gcode()), guard.cfg, 'Benchy'))
    log = (state / 'guard.log').read_text()
    assert log.count('TIGTH') == 1 and 'popup_on in config.json names "TIGTH", which is no result to warn of' in log
    assert guard.card.shown == [('show', CFG['ok_seconds'])] * 3                           # the tight row is not warned of: one line
    assert guard.popup_on() == ['SHORT', 'TIGTH', 'COLOUR']
    guard.toggle_warning('COLOUR'); assert guard.cfg['popup_on'] == ['SHORT', 'TIGTH'] and fg.load_config()['popup_on'] == ['SHORT', 'TIGTH']
    guard.toggle_warning('UNKNOWN'); assert guard.popup_on() == ['SHORT', 'TIGTH', 'UNKNOWN']

def blue_print(monkeypatch, need, reads, title='Bracket', plate_no=1, folder=None):
    """The answer for a plate that needs `need` g of blue PLA, with a blue spool that reads `reads` g: sliced as plate
    plate_no of the project, into the session folder named (a temporary G-code the guard knows) when one is."""
    running(monkeypatch, spools((BLUE_PLA, reads)))
    res = fg.evaluate(fg.parse_plate(gcode(weights=f'{need:.2f}', used='1')), CFG, title); res['pid'], res['plate_no'] = 1, plate_no
    if folder: res['gcode'] = str(folder / 'Metadata' / f'.1.{plate_no - 1}.gcode')
    return res

def card_for(res, **how):
    """What a guard that has just started shows for an answer: 'full', 'line' or None."""
    guard = guard_with(); guard.deliver(res, **how)
    return {(): None, (('show', 0),): 'full', (('show', CFG['ok_seconds']),): 'line'}[tuple(guard.card.shown)]

@pytest.mark.windows
def test_an_ignored_finding_comes_back_when_it_grows_on_another_plate_or_after_a_week(state, monkeypatch):
    """"Ignore for this project" was kept for ever, per title, as the rows' slots and results: Bracket ignored at 5 g
    short stayed a line at 370 g short, on any plate, weeks later. It is kept per plate, dated, with each row's
    shortfall, and holds for 7 days while the results are the same and no filament is more than 5 g further short."""
    import guard_ui
    monkeypatch.setattr(guard_ui, 'visible', lambda hwnd: True)
    first = blue_print(monkeypatch, 60, 55); guard = guard_with(); guard.deliver(first)
    assert guard.card.shown == [('show', 0)] and first['rows'][0]['note'] == 'short by 5 g'
    guard.act('ignore', first)
    kept = fg.load_state()['ignored']
    assert kept == {'Bracket': {'1': {'at': pytest.approx(time.time(), abs=60), 'rows': [[1, 'SHORT', 5.0]]}}} and guard.card.shown[-1] == ('close',)
    assert card_for(blue_print(monkeypatch, 60, 55)) == 'line' and card_for(blue_print(monkeypatch, 60, 55), beside=4321) is None    # as accepted: one line, nothing at the dialog
    assert card_for(blue_print(monkeypatch, 60, 50.5)) == 'line'                             # 4.5 g further short: within what was accepted
    assert card_for(blue_print(monkeypatch, 60, 49)) == 'full' and card_for(blue_print(monkeypatch, 400, 30), beside=4321) == 'full'   # 6 g further, and 365 g further
    assert card_for(blue_print(monkeypatch, 60, 55, plate_no=3)) == 'full'                   # another plate of the project was not accepted
    assert card_for(blue_print(monkeypatch, 60, 61)) == 'full'                               # another result (tight): the finding has changed
    assert card_for(blue_print(monkeypatch, 60, 58)) == 'line'                               # less short than accepted
    real = time.time
    monkeypatch.setattr(fg.time, 'time', lambda: real() + 7 * 86400 + 60)
    assert card_for(blue_print(monkeypatch, 60, 55)) == 'full'                               # a week on: asked again
    guard = guard_with(); again = blue_print(monkeypatch, 60, 55, plate_no=3); guard.deliver(again); guard.act('ignore', again)
    assert list(fg.load_state()['ignored']['Bracket']) == ['3']                              # and what has lapsed is not kept
    again['ignored'] = True; guard.act('ignore', again)                                      # "Stop ignoring"
    assert not fg.load_state()['ignored'] and guard.card.shown[-1] == ('show', 0)

@pytest.mark.windows
def test_an_ignore_in_a_project_never_saved_does_not_reach_another_unsaved_project(state, tmp_path, monkeypatch):
    """Every project that was never saved is titled "Unsaved project": one ignored at 5 g short muted every later one.
    Such a project is told by the session folder of its G-code; without one there is nothing to keep an ignore under."""
    monday, later = tmp_path / 'bamboo_model' / 'Mon_Nov_23' / '10_00_00#1#5', tmp_path / 'bamboo_model' / 'Tue_Dec_01' / '09_00_00#1#7'
    first = blue_print(monkeypatch, 60, 55, 'Unsaved project', folder=monday); guard = guard_with(); guard.deliver(first); guard.act('ignore', first)
    assert fg.load_state()['ignored'] == {str(monday): {'1': {'at': pytest.approx(time.time(), abs=60), 'rows': [[1, 'SHORT', 5.0]]}}}
    assert card_for(blue_print(monkeypatch, 60, 55, 'Unsaved project', folder=monday)) == 'line'          # the project it was said of, sliced again
    assert card_for(blue_print(monkeypatch, 60, 55, 'Unsaved project', folder=later)) == 'full'           # another unsaved project, the same finding
    loose = blue_print(monkeypatch, 60, 55, 'Unsaved project')                                             # no G-code the guard knows (check --popup)
    assert fg.ignore_key(loose) is None and card_for(loose) == 'full'
    guard = guard_with(); guard.act('ignore', loose); assert list(fg.load_state()['ignored']) == [str(monday)]   # nothing to keep it under
    assert 'Ignore for this project' not in card_shown(monkeypatch, loose)[1] and 'Ignore for this project' in card_shown(monkeypatch, first)[1]

@pytest.mark.windows
def test_an_ignore_kept_by_an_earlier_version_counts_as_lapsed(state, monkeypatch):
    """1.4 kept {title: '[[slot, result]]'}: undated, for every plate. Such an entry ignores nothing, and goes at the next ignore."""
    fg.update_state(lambda st: st.__setitem__('ignored', {'Bracket': '[[1, "SHORT"]]', 'Unsaved project': '[[1, "SHORT"]]'}))
    res = blue_print(monkeypatch, 60, 55)
    assert card_for(res) == 'full' and card_for(blue_print(monkeypatch, 60, 55, 'Unsaved project', folder=state / 'session')) == 'full'
    guard = guard_with(); guard.deliver(res); guard.act('ignore', res)
    assert fg.load_state()['ignored'] == {'Bracket': {'1': {'at': pytest.approx(time.time(), abs=60), 'rows': [[1, 'SHORT', 5.0]]}}}
    fg.update_state(lambda st: st['ignored'].__setitem__('Bracket', {'1': {'at': 'yesterday', 'rows': 5}}))    # an entry no version wrote
    assert card_for(blue_print(monkeypatch, 60, 55)) == 'full'

def project_of(tmp_path, monkeypatch, weights, title='Farm'):
    """A project sliced by one Bambu Studio (process 1), "slice all": one plate for each of weights (the grams of its blue
    and red, or its whole G-code), the first sliced first, a second apart, in place of what was sliced there before.
    Returns their files."""
    meta = tmp_path / 'bamboo_model' / 'Thu_Nov_19' / '01_02_03#1#9' / 'Metadata'; meta.mkdir(parents=True, exist_ok=True)
    (meta.parent / 'origin.txt').write_text(f'C:/prints/{title}.3mf')
    monkeypatch.setattr(fg.tempfile, 'gettempdir', lambda: str(tmp_path)); monkeypatch.setattr(fg, 'BS_DATA', str(tmp_path / 'none'))
    files, now = [], time.time()
    for f in meta.glob('.*.gcode'): f.unlink()
    for n, w in enumerate(weights):
        f = meta / f'.1.{n}.gcode'; f.write_text(w if '\n' in w else gcode(weights=w)); fg.os.utime(f, (now - 600 + n, now - 600 + n)); files.append(f)
    return files

def at_send(guard, pid=1, dialog=4321):
    """"Send print job" opens in Bambu Studio's process pid: the check it starts, run to its end."""
    guard.check_newest(pid, beside=dialog); guard.todo.get(timeout=10)()

FINE, TOO_MUCH = '9.99,10.87', '900.0,10.87'                                               # a plate's blue and red: 900 g of blue is more than any spool here holds

@pytest.mark.windows
def test_every_sliced_plate_is_checked_again_at_send_print_job(state, tmp_path, monkeypatch):
    """The 8 newest were: of 12 plates sliced in one go, the first, 900 g of a 500 g spool, was not among them, and
    nothing showed beside the dialog it was sent from."""
    import guard_ui
    monkeypatch.setattr(guard_ui, 'visible', lambda hwnd: True); running(monkeypatch, spools((BLUE_PLA, 500), (RED_PLA, 500)))
    project_of(tmp_path, monkeypatch, [TOO_MUCH] + [FINE] * 11)
    guard = guard_with(); at_send(guard)
    assert guard.card.shown == [('show', 0)] and guard.card.where == 4321 and guard.card.res['plate_no'] == 1 and 'unchecked' not in guard.card.res
    assert (state / 'guard.log').read_text().count('at "Send print job"') == 12

@pytest.mark.windows
def test_of_more_than_30_plates_the_card_says_how_many_were_not_checked(state, tmp_path, monkeypatch):
    """One reading of the printer serves every plate, but a limit there is: the 30 sliced last are checked, and what is
    beside the dialog says how many older ones were not. With nothing to warn of among the 30 that is a card too:
    silence there means every sliced plate is fine."""
    import guard_ui
    monkeypatch.setattr(guard_ui, 'visible', lambda hwnd: True); running(monkeypatch, spools((BLUE_PLA, 500), (RED_PLA, 500)))
    project_of(tmp_path, monkeypatch, [FINE] * 5 + [TOO_MUCH] + [FINE] * 27)                # 33 plates: plates 1 to 3 are not checked, plate 6 is short
    guard = guard_with(); at_send(guard)
    assert guard.card.shown == [('show', 0)] and guard.card.res['plate_no'] == 6 and guard.card.res['unchecked'] == 3
    assert (state / 'guard.log').read_text().count('at "Send print job"') == 30
    labels, _, _ = card_shown(monkeypatch, guard.card.res)
    assert labels[-1].endswith(' 3 older plates of the project were not checked: at "Send print job" only the 30 sliced last are.')
    project_of(tmp_path, monkeypatch, [TOO_MUCH] + [FINE] * 30)                              # 31 plates: the short one is the one left out
    guard = guard_with(); at_send(guard)
    assert guard.card.shown == [('note', 'Not every plate was checked')] and guard.card.where == 4321
    assert guard.card.said == ['Farm has 31 sliced plates. The 30 sliced last were checked now, with nothing to warn of; the 1 sliced before them was not checked.',
                               'Slice a plate again to have it checked.']
    project_of(tmp_path, monkeypatch, [FINE] * 30)                                           # 30 fine plates: nothing to say
    guard = guard_with(); at_send(guard)
    assert guard.card.shown == []

def sliced_again(f):
    """The plate of this file is sliced again: it is the newest now."""
    fg.os.utime(f, (time.time() - 100, time.time() - 100))

@pytest.mark.windows
def test_at_send_print_job_a_plate_that_shows_nothing_does_not_stand_for_one_that_warns(state, tmp_path, monkeypatch):
    """One answer was chosen as the worst before it was known whether it would show. Plate 1's colour, ignored on
    purpose (or unticked), ranks with plate 2's shortfall and was sliced last: it was chosen, and showed nothing."""
    import guard_ui
    monkeypatch.setattr(guard_ui, 'visible', lambda hwnd: True); running(monkeypatch, spools((GREEN_PLA, 500), (RED_PLA, 2)))
    blue, red = project_of(tmp_path, monkeypatch, [gcode(weights='9.99', used='1'), gcode(weights='10.87', used='3')], 'Case')
    sliced_again(blue)
    first = fg.evaluate(fg.parse_plate(blue.read_text()), CFG, 'Case'); first['pid'], first['plate_no'], first['gcode'] = 1, 1, str(blue)
    assert first['worst'] == 'COLOUR'
    guard = guard_with(); guard.deliver(first); guard.act('ignore', first)
    guard = guard_with(); at_send(guard)
    assert guard.card.shown == [('show', 0)] and guard.card.where == 4321 and (guard.card.res['plate_no'], guard.card.res['worst']) == (2, 'SHORT')
    fg.update_state(lambda st: st.pop('ignored'))
    guard = guard_with(dict(CFG, popup_on=['TIGHT'])); at_send(guard)                        # the colour not ignored, but not one to warn of
    assert guard.card.shown == [('show', 0)] and (guard.card.res['plate_no'], guard.card.res['worst']) == (2, 'SHORT')
    guard = guard_with(); at_send(guard)                                                     # both warn: the newest of the worst, as before
    assert guard.card.shown == [('show', 0)] and (guard.card.res['plate_no'], guard.card.res['worst']) == (1, 'COLOUR')

@pytest.mark.windows
def test_at_send_print_job_a_plate_that_could_not_be_checked_is_not_a_fine_one(state, tmp_path, monkeypatch):
    """An answer that is an error ranked below "enough": with another plate fine, nothing showed for the plate that was
    not checked. It ranks above enough and unknown, below every warning; and the check is tried once more first."""
    import guard_ui
    monkeypatch.setattr(guard_ui, 'visible', lambda hwnd: True); running(monkeypatch, spools((BLUE_PLA, 500), (RED_PLA, 500)))
    project_of(tmp_path, monkeypatch, [FINE, '9.50,10.87', FINE])
    check, tried = fg.evaluate, []
    def evaluate(plate, cfg, title='', times=1, seen=None):
        res = check(plate, cfg, title, times, seen)
        if plate['filaments'][0]['grams'] == 9.5: tried.append(1); res['error'] = "the Bambu Lab H2D's report could not be worked out (KeyError: 'tray')"
        return res
    monkeypatch.setattr(fg, 'evaluate', evaluate)
    guard = guard_with(); at_send(guard)
    assert guard.card.shown == [('show', 0)] and guard.card.where == 4321 and guard.card.res['plate_no'] == 2 and guard.card.res['error'] and len(tried) == 2
    project_of(tmp_path, monkeypatch, [FINE, '9.50,10.87', TOO_MUCH, FINE])                  # a plate that warns comes before it
    guard = guard_with(); at_send(guard)
    assert guard.card.shown == [('show', 0)] and (guard.card.res['plate_no'], guard.card.res['worst'], guard.card.res['error']) == (3, 'SHORT', None)

@pytest.mark.windows
def test_at_send_print_job_a_reading_of_the_printer_that_fails_is_tried_once_more(state, tmp_path, monkeypatch):
    """The first reading timed out, the next worked: the plate read first was dropped as an error, the others were fine,
    and nothing showed for the short one. A printer that stays away is asked twice, not twice for every plate."""
    import guard_ui
    monkeypatch.setattr(guard_ui, 'visible', lambda hwnd: True); running(monkeypatch, spools((BLUE_PLA, 500), (RED_PLA, 500)))
    project_of(tmp_path, monkeypatch, [FINE, FINE, TOO_MUCH])                                # the short plate is the newest: checked first
    calls = []
    def read_status(*a, **k):
        calls.append(1)
        if len(calls) in fails: raise fg.PrinterError('printer did not report its status')
        return spools((BLUE_PLA, 500), (RED_PLA, 500))
    monkeypatch.setattr(fg, 'read_status', read_status)
    fails = {1}; guard = guard_with(); at_send(guard)
    assert guard.card.shown == [('show', 0)] and (guard.card.res['plate_no'], guard.card.res['worst'], guard.card.res['error']) == (3, 'SHORT', None) and len(calls) == 2
    del calls[:]; fails = set(range(1, 99)); guard = guard_with(); at_send(guard)            # the printer is off
    assert guard.card.shown == [('show', 0)] and guard.card.res['error'] == 'printer did not report its status' and guard.card.res['plate_no'] == 3 and len(calls) == 2
    assert (state / 'guard.log').read_text().count('Filament could not be checked: Case') == 0 and (state / 'guard.log').read_text().count('Filament could not be checked: Farm') == 3

class Icon:
    """The icon, as the guard sees it: the colours and tooltips it was given, in order."""
    def __init__(self): self.was = []
    def set(self, tone, text): self.was.append((tone, text))
    def refresh(self): pass

def answer(plate_no, status, title='Bracket', pid=1, error=None, **more):
    """An answer as evaluate gives it, cut down to what the guard reads to show it: one filament with this result."""
    return dict({'title': title, 'plate_no': plate_no, 'pid': pid, 'error': error, 'worst': status, 'rows': [{'slot': 1, 'status': status}],
                 'printer': {'model': 'Bambu Lab H2D', 'state': 'IDLE'}}, **more)

def warned(first, *then, cfg=CFG, **how):
    """A guard that has shown `first`, then got each of `then`: (what its card showed after the first, its icon's
    colours and tooltips after the first, the guard)."""
    guard = guard_with(cfg); guard.tray = Icon(); guard.deliver(first)
    seen, said = len(guard.card.shown), len(guard.tray.was)
    for res in then: guard.deliver(res, **how)
    return guard.card.shown[seen:], guard.tray.was[said:], guard

@pytest.mark.windows
def test_a_warning_stands_until_its_own_plate_a_worse_answer_or_the_user_replaces_it(state, monkeypatch):
    """A fine plate of the same project wiped another plate's warning once 120 s had passed; a fine slice in a second
    Bambu Studio, or of another unsaved project, wiped it at once. The line that took its place went after 8 seconds,
    and the icon read "Enough filament for this print · Bracket"."""
    short, real = answer(1, 'SHORT'), time.time
    shown, icon, guard = warned(short)
    assert guard.card.shown == [('show', 0)] and guard.tray.was == [('danger', 'Not enough filament for this print · Bracket, plate 1')]   # the tooltip names the plate
    monkeypatch.setattr(fg.time, 'time', lambda: real() + 180)                               # plate 2 took three minutes to slice
    shown, icon, guard = warned(short, answer(2, 'OK'))
    assert shown == [] and icon == [] and guard.last is short and guard.last_text() == 'Not enough filament for this print · Bracket, plate 1'
    monkeypatch.setattr(fg.time, 'time', real)
    for other in (answer(1, 'OK', 'Lid', pid=2), answer(1, 'OK', pid=2), answer(1, 'OK', 'Unsaved project', pid=2),   # another Bambu Studio's fine slice
                  answer(2, 'SHORT'), answer(2, 'TIGHT'), answer(2, 'UNKNOWN'),                                     # another plate, no worse
                  answer(2, 'OK', error='the Bambu Lab H2D did not report its status'), answer(2, 'OK', error='no printer', quiet=True)):
        shown, icon, guard = warned(short, other)
        assert shown == [] and icon == [] and guard.last is short, other                     # the card stands, and the icon keeps its colour
    shown, icon, guard = warned(short, answer(1, 'OK'))                                      # its own plate, sliced again and fine: the line takes its place
    assert shown == [('show', CFG['ok_seconds'])] and icon == [('ok', 'Enough filament for this print · Bracket, plate 1')]
    shown, icon, guard = warned(short, answer(1, 'OK'), answer(2, 'OK'))                     # and then no warning stands: another plate's line shows
    assert shown == [('show', CFG['ok_seconds'])] * 2 and guard.last['plate_no'] == 2
    shown, icon, guard = warned(answer(1, 'TIGHT'), answer(2, 'SHORT'))                      # a worse answer
    assert shown == [('show', 0)] and icon == [('danger', 'Not enough filament for this print · Bracket, plate 2')] and guard.card.res['plate_no'] == 2
    shown, icon, guard = warned(answer(1, 'TIGHT'), answer(2, 'SHORT'), answer(1, 'OK'))     # which then stands in its turn
    assert shown == [('show', 0)] and guard.card.res['plate_no'] == 2
    shown, icon, guard = warned(short, answer(2, 'OK'), full=True)                           # the user asked for it
    assert shown == [('show', 0)] and guard.card.res['plate_no'] == 2 and icon[-1][0] == 'ok'
    shown, icon, guard = warned(answer(1, 'OK', error='the Bambu Lab H2D did not report its status'), answer(2, 'OK'))
    assert shown == [] and guard.last['error']                                               # a plate that could not be checked is not wiped by a fine one either
    shown, icon, guard = warned(answer(1, 'OK', error='the Bambu Lab H2D did not report its status'), answer(2, 'TIGHT'))
    assert shown == [('show', 0)] and guard.card.res['plate_no'] == 2                        # but by a warning

@pytest.mark.windows
def test_a_standing_warning_gives_way_at_send_print_job_and_goes_when_its_plate_is_fine_and_quiet_is_asked(state):
    """Every plate was checked again for "Send print job": the warning found there shows beside the dialog whatever
    stands in the corner; a fine answer there shows nothing and changes nothing. With "One line when there is enough"
    off, the plate's own fine answer has no line to put in the warning's place: the card goes."""
    short = answer(1, 'SHORT')
    shown, icon, guard = warned(short, answer(2, 'TIGHT'), beside=4321)
    assert shown == [('show', 0)] and guard.card.where == 4321 and guard.card.res['plate_no'] == 2
    shown, icon, guard = warned(short, answer(2, 'OK'), beside=4321)
    assert shown == [] and icon == [] and guard.last is short
    shown, icon, guard = warned(short, answer(1, 'OK'), beside=4321)                         # its own plate, fine now: nothing beside the dialog, and the card goes
    assert shown == [('close',)] and icon == [('ok', 'Enough filament for this print · Bracket, plate 1')]
    shown, icon, guard = warned(short, answer(1, 'OK'), answer(2, 'OK'), cfg=dict(CFG, ok_seconds=0))
    assert shown == [('close',)] and guard.last['plate_no'] == 2
    shown, icon, guard = warned(short, answer(1, 'OK', error='no printer', quiet=True))      # sliced again for a printer the guard cannot ask
    assert shown == [('close',)]

def no_log(state, monkeypatch):
    """guard.log cannot be written: a folder stands in its place (on Windows: the disk full, or the file held elsewhere)."""
    monkeypatch.setattr(fg, 'LOST', [0], raising=False); (state / 'guard.log').mkdir()

def test_a_log_that_cannot_be_written_raises_nothing_and_says_later_what_it_lost(state, monkeypatch):
    """log() raised whatever opening or writing guard.log raised, into the watcher and the card's loop. It raises
    nothing; the lines lost are counted and said with the next line that is written."""
    no_log(state, monkeypatch)
    for n in range(3): fg.log(f'line {n}')
    (state / 'guard.log').rmdir(); fg.log('written again'); fg.log('and on')
    first, second = (state / 'guard.log').read_text().splitlines()
    assert first.endswith(' (3 lines before this one could not be written) written again') and second.endswith(' and on') and 'lines' not in second
    fg.log('a name that no codec takes: \udcff')                                             # nor does a line that cannot be encoded
    assert (state / 'guard.log').read_text().splitlines()[-1].endswith('a name that no codec takes: \\udcff')

@pytest.mark.windows
def test_with_the_log_unwritable_a_short_slice_still_shows_its_card_and_the_watcher_lives(state, tmp_path, monkeypatch):
    """The answer was logged before it was queued for the card, and the watcher's handlers logged again: one failed
    write to guard.log lost the warning and ended the watcher's thread, for good."""
    f = slice_file(tmp_path, gcode()); plate = fg.parse_plate(f.read_text()); running(monkeypatch, spools((BLUE_PLA, 2), (RED_PLA, 2)))
    class Watcher:
        problems = []
        def __init__(self): self.new = [(str(f), plate)]
        def new_plates(self): new, self.new = self.new, []; return new
    monkeypatch.setattr(fg, 'Watcher', Watcher); no_log(state, monkeypatch)
    guard = guard_with(dict(CFG, poll_seconds=0.05)); watcher = threading.Thread(target=guard.watch_slices, daemon=True); watcher.start()
    try:
        for _ in range(3): guard.todo.get(timeout=5)()                                       # the line, the answer, the end of the line
        time.sleep(0.3)
        assert guard.card.shown[1] == ('show', 0) and fg.headline(guard.card.res) == 'Not enough filament for this print' and watcher.is_alive()
        assert fg.load_state()['slices'][0]['title'] and fg.LOST[0] >= 1                     # and the slice was remembered all the same
    finally: guard.stopping.set(); watcher.join(5)
    assert not watcher.is_alive()

@pytest.mark.windows
def test_an_answer_is_on_its_way_to_the_card_before_anything_of_it_is_logged(state, tmp_path, monkeypatch):
    """After a slice, after "Check again" and after a check from the icon or for "Send print job": whatever becomes of
    the log's line, the card has the answer."""
    import guard_ui
    f = slice_file(tmp_path, gcode()); plate = fg.parse_plate(f.read_text()); running(monkeypatch, spools((BLUE_PLA, 2), (RED_PLA, 2)))
    monkeypatch.setattr(fg, 'newest_gcode', lambda pid=None: str(f)); monkeypatch.setattr(fg, 'temp_gcodes', lambda: [str(f)]); monkeypatch.setattr(guard_ui, 'visible', lambda hwnd: True)
    res = fg.evaluate(plate, CFG, 'Benchy')
    def refuse(msg): raise OSError(28, 'No space left on device')
    def shown_after(start):
        guard = guard_with()
        with monkeypatch.context() as m:
            m.setattr(fg, 'log', refuse)
            try:
                start(guard)
                while True: guard.todo.get(timeout=2)()
            except (OSError, fg.queue.Empty): pass
        run_queued(guard)
        return guard.card.shown
    assert ('show', 0) in shown_after(lambda guard: guard.take_slice(str(f), plate))
    assert ('show', 0) in shown_after(lambda guard: guard.recheck(res))
    assert ('show', 0) in shown_after(lambda guard: guard.check_newest())
    assert ('show', 0) in shown_after(lambda guard: guard.check_newest(1, beside=4321))

@pytest.mark.windows
def test_the_cards_loop_goes_on_whatever_a_request_does_and_starts_a_watcher_that_has_ended(state, monkeypatch):
    """pump asked for its next round as its last step: a request that failed while the log could not be written left
    none, and with it no card, no menu and no Quit. A watcher thread that has ended is started again."""
    no_log(state, monkeypatch)
    guard, rounds, started = guard_with(), [], []
    guard.root = type('Root', (), {'quit': None, 'after': lambda self, ms, then: rounds.append(ms)})()
    guard.todo.put(lambda: 1 / 0); guard.pump()
    assert rounds == [400]
    class Dies(BaseException): pass
    def dies(): raise Dies()
    guard.todo.put(dies)
    with pytest.raises(Dies): guard.pump()
    assert rounds == [400, 400]
    guard.watch_slices = lambda: started.append(threading.current_thread())
    ended = threading.Thread(target=lambda: None); ended.start(); ended.join(); guard.watcher = ended
    guard.ticks = 24; guard.pump()
    for _ in range(50):
        if started: break
        time.sleep(0.02)
    assert len(started) == 1 and guard.watcher is started[0] and rounds == [400] * 3
    quiet = guard_with(); quiet.root = guard.root; quiet.ticks = 24; quiet.pump()            # a guard that never watched (check --popup) starts none
    assert quiet.watcher is None

@pytest.mark.windows
def test_a_pause_between_looks_that_is_no_positive_number_is_the_default(state, monkeypatch):
    """poll_seconds "2" ended the watcher after its first round (the wait raised outside the round's guard); null made it
    wait for ever."""
    for odd in ('2', None, 0, -1, True, float('nan'), [2]):
        assert guard_with(dict(CFG, poll_seconds=odd)).poll_seconds() == 2
    assert [guard_with(dict(CFG, poll_seconds=s)).poll_seconds() for s in (0.5, 3, float('inf'))] == [0.5, 3, threading.TIMEOUT_MAX]
    class Watcher:
        problems, rounds = [], []
        def new_plates(self): self.rounds.append(1); return []
    monkeypatch.setattr(fg, 'Watcher', Watcher)
    guard = guard_with(dict(CFG, poll_seconds='2')); watcher = threading.Thread(target=guard.watch_slices, daemon=True); watcher.start()
    time.sleep(0.3); alive = watcher.is_alive(); guard.stopping.set(); watcher.join(5)
    assert alive and not watcher.is_alive() and Watcher.rounds == [1]

def learned_something(reading=20.0):
    """A history with one spool seen still feeding in C3 at this reading; returns the file's text."""
    gn.keep('SERIAL', 'C3', 'seen', {'at': time.time(), 'reading': reading})
    return open(gn.path()).read()

def test_forgetting_everything_sets_the_history_aside_and_keeps_one_copy(state, capsys):
    """learned.json was overwritten with nothing, from the menu's one click and from `learned --forget`. What a spool
    run dry and weeks of prints taught is kept beside it as learned.json.forgotten: one copy, replaced each time, and
    not by a history that holds nothing."""
    first = learned_something(20.0)
    assert gn.forget() == f"everything learned, kept aside as {gn.path()}.forgotten," and gn.load() == {}
    assert (state / 'learned.json.forgotten').read_text() == first
    gn.forget(); assert (state / 'learned.json.forgotten').read_text() == first             # nothing learned since: the copy stays
    second = learned_something(35.0)
    assert fg.main(['learned', '--forget']) == 0 and gn.load() == {} and (state / 'learned.json.forgotten').read_text() == second != first
    assert capsys.readouterr().out == f'everything learned, kept aside as {gn.path()}.forgotten, is forgotten\n'
    assert fg.main(['learned', '--forget']) == 0 and capsys.readouterr().out == 'everything learned is forgotten\n'
    learned_something(); gn.keep('SERIAL', 'B2', 'seen', {'at': time.time(), 'reading': 5.0})
    gn.forget('B2'); assert (state / 'learned.json.forgotten').read_text() == second and 'C3' in gn.load()['SERIAL']   # one tray forgotten: as before

@pytest.mark.windows
def test_forget_what_was_learned_asks_first_and_says_where_the_copy_is(state, monkeypatch):
    """One slip of the mouse beside Quit emptied the history, and the card said so afterwards."""
    was = learned_something(); guard = guard_with(); guard.forget_learned()
    assert guard.card.shown == [('note', 'Forget what was learned?')] and list(guard.card.buttons) == ['Forget', 'Keep'] and open(gn.path()).read() == was
    guard.card.buttons['Keep'](); assert guard.card.shown[-1] == ('close',) and open(gn.path()).read() == was and not (state / 'learned.json.forgotten').exists()
    guard.forget_learned(); guard.card.buttons['Forget']()
    assert guard.card.shown[-1] == ('note', 'What was learned is forgotten') and gn.load() == {} and (state / 'learned.json.forgotten').read_text() == was
    assert guard.card.said == ['The guard learns again from the next prints it follows.',
                               f"What it had learned is kept as {state / 'learned.json.forgotten'}: one copy, until something is forgotten again."]
    guard.forget_learned(); guard.card.buttons['Forget']()                                   # nothing learned since: no copy was made, and none is promised
    assert guard.card.said == ['The guard learns again from the next prints it follows.']
    was = learned_something(); monkeypatch.setattr(fg, 'write_json', lambda *a, **k: (_ for _ in ()).throw(fg.StateBusy('learned.json could not be written: another program holds it')))
    guard.forget_learned(); guard.card.buttons['Forget']()                                   # the file held elsewhere: said, and nothing lost
    assert guard.card.shown[-1] == ('note', 'What was learned could not be forgotten') and open(gn.path()).read() == was

@pytest.mark.windows
def test_a_note_can_ask_with_buttons(monkeypatch):
    import guard_ui
    made, did = tk_stand_in(monkeypatch), []
    def opened(card, pid=None, beside=None, hold=False):
        card.win, card.t, card.owner, card.beside, card.held, card.closes_after = sys.modules['tkinter'].Toplevel(), guard_ui.THEMES['dark'], None, None, False, 0
        return card.win
    monkeypatch.setattr(guard_ui.Card, '_open', opened); monkeypatch.setattr(guard_ui.Card, '_place', lambda card: None)
    guard_ui.Card(None).note('Forget what was learned?', [(None, 'It is set aside.')], buttons=[('Forget', lambda: did.append('forget')), ('Keep', lambda: did.append('keep'))])
    buttons = {w.k['text']: w.k['command'] for w in made if type(w).__name__ == 'Button'}
    assert list(buttons) == ['Forget', 'Keep'] and [w.k['text'] for w in made if type(w).__name__ == 'Label'] == ['Forget what was learned?', '–  It is set aside.']
    buttons['Keep'](); buttons['Forget'](); assert did == ['keep', 'forget']

@pytest.mark.windows
def test_forget_what_was_learned_is_among_the_settings_away_from_quit(state, monkeypatch):
    import guard_ui
    class Item:
        def __init__(self, text, action=None, **how): self.text, self.action, self.how = text, action, how
    class Menu:
        SEPARATOR = Item('-')
        def __init__(self, *items): self.items = items
    pystray = type(sys)('pystray'); pystray.MenuItem, pystray.Menu = Item, Menu
    pystray.Icon = type('Icon', (), {'__init__': lambda self, name, image, title, menu: setattr(self, 'menu', menu), 'run_detached': lambda self: None})
    monkeypatch.setitem(sys.modules, 'pystray', pystray); monkeypatch.setattr(guard_ui, 'icon_image', lambda tone: None)
    guard = guard_with(dict(CFG, popup_on=['short', 'colour'])); menu = guard_ui.Tray(guard).icon.menu
    top = [i.text for i in menu.items if isinstance(i.text, str)]; settings = next(i.action for i in menu.items if i.text == 'Settings')
    assert top[-4:] == ['-', 'Self-test', 'Open log', 'Quit'] and [i.text for i in settings.items][-1] == 'Forget what was learned…'
    forget = settings.items[-1]; forget.action(None, forget); guard.todo.get_nowait()()
    assert guard.card.shown == [('note', 'Forget what was learned?')]
    ticks = {i.text: i.how['checked'](i) for i in settings.items if i.text.startswith('Warn when')}
    assert ticks == {'Warn when a colour is not loaded': True, "Warn when a spool's amount is unknown": False}   # as the guard reads popup_on: in capitals

def amount_asked(monkeypatch, tray, weigh=False, act=None, card=None, res=None):
    """The window guard_ui opens for a tray's amount, on the stand-in for tkinter, with its parts by name: the two
    fields (scale, spool), the labels of the result and of what is wrong, what Save does, every text in it (texts), the
    buttons by their text, what it told the guard (acts) and whether it has closed. act: what telling the guard does;
    card: the card it belongs to, else one that starts from an empty spool of 200 g."""
    import guard_ui, types
    made, w = tk_stand_in(monkeypatch), types.SimpleNamespace(acts=[])
    def told(name, res, arg=None):
        w.acts.append((name, arg))
        if act: act(name, res, arg)
    if card is None: card = guard_ui.Card(None); card.empty_spool = 200
    card.act, card.t = told, guard_ui.THEMES['dark']
    card.ask_amount(res, tray, weigh)
    w.scale, w.spool = [x.k['textvariable'] for x in made if type(x).__name__ == 'Entry']
    w.result, w.wrong = [x for x in made if type(x).__name__ == 'Label'][-2:]
    w.buttons = {x.k['text']: x.k['command'] for x in made if type(x).__name__ == 'Button'}
    w.save, w.texts, w.closed = w.buttons['Save'], lambda: [x.k['text'] for x in made if type(x).__name__ == 'Label'], lambda: bool(made[0].k.get('destroyed'))
    return w

EXT = {'label': 'Ext', 'name': '', 'type': 'PETG', 'colour': '#101010', 'grams': None}

@pytest.mark.windows
def test_saving_an_amount_does_not_wait_on_the_empty_spools_weight(state, monkeypatch):
    """The empty spool's weight was kept first, in config.json, and the amount after it: with the guard's folder not
    writable, Save did nothing and said nothing. The amount comes first; a weight that cannot be kept is logged and
    used until the guard stops."""
    guard = fg.Guard(dict(CFG), type('Root', (), {'quit': None})(), standing=False); monkeypatch.setattr(guard, 'recheck', lambda res, beside=None: None)
    def refused(cfg): raise PermissionError(13, 'Permission denied', fg.CONFIG)
    monkeypatch.setattr(fg, 'save_config', refused)
    w = amount_asked(monkeypatch, EXT, act=guard.act, card=guard.card, res={'printer': {'serial': 'SERIAL'}})
    assert w.spool.get() == '250'
    w.scale.set('640'); w.spool.set('212'); w.save()
    assert w.acts == [('amount', (EXT, 428.0)), ('spool', 212.0)] and w.closed()
    assert fg.load_state()['spools']['SERIAL']['Ext']['grams'] == 428.0
    assert guard.card.empty_spool == 212.0 and guard.cfg['empty_spool_g'] == 212.0           # for this run
    assert "the empty spool's weight, 212 g, could not be kept in config.json (PermissionError): it is used until the guard stops" in (state / 'guard.log').read_text()

@pytest.mark.windows
def test_an_amount_that_cannot_be_stored_leaves_the_window_open_and_says_so(state, monkeypatch):
    """The window was destroyed before the amount was stored: with the state file held elsewhere the weight typed was
    gone, with a line in the log for all notice."""
    held, stored = [fg.StateBusy('state.json could not be written: another program holds it')], []
    def act(name, res, arg):
        if name != 'spool' and held: raise held[0]
        stored.append((name, arg))
    for weigh, tray, kind in ((False, EXT, 'amount'), (True, dict(EXT, label='C3', grams=420.0, reading=420.0), 'weigh')):
        del stored[:]; held[:] = [fg.StateBusy('state.json could not be written: another program holds it')]
        w = amount_asked(monkeypatch, tray, weigh, act)
        w.scale.set('640'); w.spool.set('230'); w.save()
        assert not w.closed() and stored == [] and w.wrong['text'] == 'Not saved: state.json could not be written: another program holds it. Save again in a moment.'
        assert (w.scale.get(), w.spool.get()) == ('640', '230')                              # what was typed is still there
        del held[:]; w.save()
        assert w.closed() and stored == [(kind, (tray, 410.0)), ('spool', 230.0)]
    held[:] = [PermissionError(13, 'Permission denied')]
    w = amount_asked(monkeypatch, dict(EXT, grams=300.0, entered=1e6), act=act); w.buttons['Forget this amount']()
    assert not w.closed() and w.wrong['text'] == 'Not saved: PermissionError. Save again in a moment.'

@pytest.mark.windows
def test_an_empty_spool_of_nothing_is_said_and_is_not_the_next_windows_start(state, monkeypatch):
    """A refill weighed without its spool left 0 as the start of every later window: 612 typed for a 250 g spool was then
    kept as 612 g of filament, 250 g too much. 0 or blank is not remembered, and while the field holds it the result
    says that nothing is taken off."""
    w = amount_asked(monkeypatch, EXT)
    w.scale.set('612'); assert w.result['text'] == '412 g of filament'
    for nothing in ('', '0', ' ', '0,0'):
        w.spool.set(nothing); assert w.result['text'] == '612 g of filament: nothing taken off for the spool', nothing
    w.save(); assert w.acts == [('amount', (EXT, 612.0))] and w.closed()                     # the amount, and no weight to remember
    guard = guard_with(); guard.card = type('Card', (), {'beside': None, 'empty_spool': 230.0})()
    for nothing in (0, 0.0, None, -5): guard.act('spool', None, nothing)
    assert guard.card.empty_spool == 230.0 and 'empty_spool_g' not in fg.load_config().keys() - fg.DEFAULTS.keys() and fg.load_config()['empty_spool_g'] == 250
    import guard_ui
    for odd in (0, None, '250', -1, True):                                                   # nor does a config.json that holds one start a window with it
        card = guard_ui.Card(None); card.empty_spool = odd
        assert amount_asked(monkeypatch, EXT, card=card).spool.get() == '250', odd

@pytest.mark.windows
def test_the_amount_window_of_a_card_without_a_running_guard_says_the_amount_is_not_counted_down(state, monkeypatch):
    """`check --popup` with no guard running: the amount entered there is kept, and nothing counts it down."""
    import guard_ui
    said = 'The guard is not running: the amount is kept, and counted down only while the guard runs.'
    def texts(standing, running, weigh=False):
        monkeypatch.setattr(fg, 'is_running', lambda: running)
        guard = fg.Guard(dict(CFG), type('Root', (), {'quit': None})(), standing=False); guard.standing = standing; guard.card.alone = not standing
        return ' '.join(amount_asked(monkeypatch, dict(EXT, reading=400.0), weigh, card=guard.card).texts())
    assert said in texts(False, False) and fg.Guard(dict(CFG), type('Root', (), {'quit': None})(), standing=False).card.alone
    assert said not in texts(False, True) and said not in texts(True, False) and said not in texts(False, False, weigh=True)
    assert not guard_ui.Card(None).alone

@pytest.mark.windows
def test_a_warning_shows_though_the_state_file_cannot_be_read_for_what_is_ignored(state, monkeypatch):
    """deliver looked up "ignored" in state.json before it showed anything: with the file held elsewhere the warning was
    a line in the log. What cannot be looked up is not ignored."""
    res = blue_print(monkeypatch, 60, 55)
    for held in (fg.StateBusy('the state folder was held elsewhere for more than 10 s'), PermissionError(13, 'Permission denied')):
        monkeypatch.setattr(fg, 'load_state', lambda held=held: (_ for _ in ()).throw(held))
        guard = guard_with(); guard.deliver(res)
        assert guard.card.shown == [('show', 0)] and res['ignored'] is False
    assert 'Bracket: what is ignored could not be looked up (StateBusy: the state folder was held elsewhere for more than 10 s): shown as not ignored' in (state / 'guard.log').read_text()
    monkeypatch.setattr(fg, 'load_state', lambda: ['not', 'a', 'state'])                      # nor is what cannot be understood
    guard = guard_with(); guard.deliver(res); assert guard.card.shown == [('show', 0)]

@pytest.mark.windows
def test_a_card_made_from_an_old_answer_says_when_it_was_checked_not_now(state, monkeypatch):
    """A full card read "Spools as the Bambu Lab H2D reports them now" whatever the age of its answer: after "Stop
    ignoring", a check that failed, or a warning that had to wait for another to go. An answer more than a minute
    old says when the printer reported its spools, and what ran then, in the past."""
    running(monkeypatch, spools((BLUE_PLA, 500), (RED_PLA, 2))); res = fg.evaluate(fg.parse_plate(gcode()), CFG, 'Benchy')
    guard = guard_with(); guard.deliver(res)
    assert res['at'] == pytest.approx(time.time(), abs=60)                                   # stamped where the check did not say
    assert card_shown(monkeypatch, res)[0][-1] == "Spools as the Bambu Lab H2D reports them now; the amounts are the AMS's estimate."
    def when(ago):
        res['at'] = time.time() - ago; at = time.localtime(res['at'])
        return time.strftime('at %H:%M' if at[:3] == time.localtime()[:3] else 'on %d %b at %H:%M', at)
    for ago in (90, 3 * 86400):
        then = when(ago)
        assert card_shown(monkeypatch, res)[0][-1] == f"Spools as the Bambu Lab H2D reported them {then}; the amounts are the AMS's estimate. \"Check again\" asks the printer now."
    then = when(3 * 86400); assert then.startswith('on ')
    res['printer'].update(state='RUNNING', job='Benchy', layer=11, layers=27, counted=True, beyond={'A2': 40.0})
    assert card_shown(monkeypatch, res)[0][-1] == (f"Spools as the Bambu Lab H2D reported them {then}, less what the print running there then (Benchy, layer 11 of 27) still needed; "
        "the amounts are the AMS's estimate. The print running then needed 40 g more of A2's filament than the loaded spools held. \"Check again\" asks the printer now.")
    res['printer'].update(counted=False, beyond={})
    assert card_shown(monkeypatch, res)[0][-1] == (f"Spools as the Bambu Lab H2D reported them {then}; the amounts are the AMS's estimate. It was printing then (Benchy, layer 11 of 27): "
        "that job was still drawing from these spools, and the guard could not count what it still needed. \"Check again\" asks the printer now.")
    res['at'] = time.time()                                                                  # and as it comes, in the present
    assert card_shown(monkeypatch, res)[0][-1] == ("Spools as the Bambu Lab H2D reports them now; the amounts are the AMS's estimate. It is printing now (Benchy, layer 11 of 27): "
        "that job is still drawing from these spools, and the guard could not count what it still needs.")
    failed = answer(1, 'OK', error='the Bambu Lab H2D did not report its status', at=time.time() - 3 * 86400, times=1, model='Bambu Lab H2D', time='')
    assert card_shown(monkeypatch, failed)[0][-2:] == ['The Bambu Lab H2D did not report its status.',
                                                       time.strftime('Checked on %d %b at %H:%M. "Check again" asks the printer now.', time.localtime(failed['at']))]

@pytest.mark.windows
def test_a_warning_found_for_send_print_job_shows_though_the_dialog_has_closed(state, tmp_path, monkeypatch):
    """The check takes a second or three. A dialog closed meanwhile, which is also what a quick click on Send looks like,
    threw the warning away: no card, the icon as it was. It shows as a card after a slice does."""
    import guard_ui
    monkeypatch.setattr(guard_ui, 'visible', lambda hwnd: False); running(monkeypatch, spools((BLUE_PLA, 500), (RED_PLA, 500)))
    project_of(tmp_path, monkeypatch, [TOO_MUCH, FINE])
    guard = guard_with(); guard.tray = Icon(); at_send(guard)
    assert guard.card.shown == [('show', 0)] and guard.card.where is None and guard.card.res['plate_no'] == 1
    assert guard.tray.was == [('danger', 'Not enough filament for this print · Farm, plate 1')]
    project_of(tmp_path, monkeypatch, [FINE, FINE])                                          # nothing to warn of: nothing shows, as beside the dialog
    guard = guard_with(); guard.tray = Icon(); at_send(guard)
    assert guard.card.shown == [] and guard.tray.was == []

@pytest.mark.windows
def test_the_icon_stays_grey_and_says_paused_while_checks_are_paused(state):
    """"Check newest slice now" while checks were paused turned the icon green, with nothing left to say that no slice is
    being checked. Resumed, the icon is the last answer's again."""
    guard = guard_with(); guard.tray = Icon()
    guard.deliver(answer(1, 'SHORT')); guard.toggle_pause()
    assert guard.tray.was[-1] == ('muted', 'Checks are paused')
    guard.deliver(answer(2, 'OK'), full=True)                                                # asked for from the icon: the answer shows
    assert guard.card.shown[-1] == ('show', 0) and guard.tray.was[-1] == ('muted', 'Checks are paused')
    guard.toggle_pause()
    assert guard.tray.was[-1] == ('ok', 'Enough filament for this print · Bracket, plate 2')
    fresh = guard_with(); fresh.tray = Icon(); fresh.toggle_pause(); fresh.toggle_pause()
    assert fresh.tray.was == [('muted', 'Checks are paused'), ('muted', 'No slice checked yet')]

@pytest.mark.windows
def test_popup_on_decides_for_no_result_but_the_four_it_is_for(state):
    """"OK" in popup_on opened the full card for every plate that was fine. Such a card would now stand as a warning does
    and hold back every other plate's answer: enough is not a result to warn of, and the log says so, once."""
    cfg = dict(CFG, popup_on=['OK', 'SHORT', 'ok'])
    shown, icon, guard = warned(answer(1, 'OK'), answer(2, 'OK'), answer(3, 'TIGHT'), answer(4, 'UNKNOWN'), cfg=cfg)
    assert guard.card.shown == [('show', CFG['ok_seconds'])] * 4 and guard.last['plate_no'] == 4   # a line each, none standing in another's way
    assert guard.opening(answer(1, 'OK')) == [] and guard.rank(answer(1, 'OK')) == (0, 0)
    assert (state / 'guard.log').read_text().count('popup_on in config.json names "OK", which is no result to warn of (UNKNOWN, TIGHT, SPLIT, SHORT, MISSING, COLOUR)') == 1

def icon_menu(monkeypatch, guard):
    """The icon's menu as guard_ui builds it for a guard, on a stand-in for pystray: each item keeps its text, its
    action and how it was made (how); a submenu is the action of its item."""
    import guard_ui
    class Item:
        def __init__(self, text, action=None, **how): self.text, self.action, self.how = text, action, how
    class Menu:
        SEPARATOR = Item('-')
        def __init__(self, *items): self.items = items
    pystray = type(sys)('pystray'); pystray.MenuItem, pystray.Menu = Item, Menu
    pystray.Icon = type('Icon', (), {'__init__': lambda self, name, image, title, menu: setattr(self, 'menu', menu), 'run_detached': lambda self: None})
    monkeypatch.setitem(sys.modules, 'pystray', pystray); monkeypatch.setattr(guard_ui, 'icon_image', lambda tone: None)
    return guard_ui.Tray(guard).icon.menu

def click(guard, menu):
    """A click on the icon: the menu's default item, done as the guard does what its menu asks. Returns the item's text."""
    item = next(i for i in menu.items if i.how.get('default')); item.action(None, item); guard.todo.get_nowait()()
    return item.text

@pytest.mark.windows
def test_the_icons_click_checks_the_last_plate_again(state, monkeypatch):
    """"Show last result", the icon's own click, put the last answer on a full card as it was. A print had taken 120 g
    since: the card still read "Enough filament for this print", "as the Bambu Lab H2D reports them now". The click
    asks the printer again: one check however often it is clicked, the newest slice when nothing was checked yet."""
    reads, asked = [400], []
    running(monkeypatch, None); monkeypatch.setattr(fg, 'read_status', lambda *a, **k: (asked.append(1), spools((BLUE_PLA, reads[0])))[1])
    guard = guard_with(); menu = icon_menu(monkeypatch, guard)
    old = fg.evaluate(fg.parse_plate(gcode(weights='300', used='1')), CFG, 'Vase'); old['pid'], old['plate_no'], old['gcode'] = 1, 2, 'a slice'
    guard.deliver(old); assert old['worst'] == 'OK' and guard.card.shown == [('show', CFG['ok_seconds'])]
    reads[0] = 280; del asked[:]                                                             # a print started at the printer has taken 120 g
    assert click(guard, menu) == 'Check last plate again'; guard.todo.get(timeout=5)()
    assert guard.card.shown[1:] == [('line', 'Checking the spools on the Bambu Lab H2D…', False), ('show', 0)] and len(asked) == 1 and not guard.busy
    new = guard.card.res
    assert new is not old and new['worst'] == 'SHORT' and (new['pid'], new['plate_no'], new['gcode'], new['title']) == (1, 2, 'a slice', 'Vase') and guard.last is new
    assert new['at'] == pytest.approx(time.time(), abs=60)
    release, check = threading.Event(), fg.evaluate                                          # the printer takes its time, and the icon is clicked five times
    monkeypatch.setattr(fg, 'evaluate', lambda *a, **k: (release.wait(5), check(*a, **k))[1])
    del asked[:]; shown = len(guard.card.shown)
    for _ in range(5): click(guard, menu)
    release.set(); guard.todo.get(timeout=5)()
    assert len(asked) == 1 and guard.card.shown[shown:] == [('line', 'Checking the spools on the Bambu Lab H2D…', False), ('show', 0)] and not guard.busy
    fresh, newest = guard_with(), []                                                         # nothing checked yet: the newest slice
    monkeypatch.setattr(fresh, 'check_newest', lambda *a, **k: newest.append(a)); click(fresh, icon_menu(monkeypatch, fresh))
    assert newest == [()]

@pytest.mark.windows
def test_the_amount_window_starts_from_what_the_guard_counts_now_and_saved_untouched_changes_nothing(state, monkeypatch):
    """The window started with the amount of the card's own row. C3 was entered at 400 g and a counted print took 120 g:
    from a card made before that print the window opened at 400 g, and Save as it stood wrote 400 g back over the
    count. It starts from the ledger's entry, read as it opens, and Save with both fields as they opened enters nothing."""
    import guard_ledger as gl
    from helpers import H2D_JOB, SLICE
    c3 = dict(EXT, label='C3'); res = {'printer': {'serial': 'SERIAL'}}
    gl.enter('SERIAL', c3, 400); row = dict(c3, grams=400.0, entered=time.time())           # the row of a card made now
    fg.remember_slice('Benchy', 1, SLICE); watch = gl.JobWatch('SERIAL', CFG)               # then a print runs and is counted: 120 g of C3
    watch.look(dict(H2D_JOB, gcode_state='FINISH', task_id='10')); watch.look(dict(H2D_JOB, gcode_state='PREPARE')); watch.look(H2D_JOB)
    counted = lambda: fg.load_state()['spools']['SERIAL']['C3']['grams']
    assert counted() == 280.0
    w = amount_asked(monkeypatch, row, res=res)
    assert (w.scale.get(), w.spool.get(), w.result['text']) == ('480', '200', '280 g of filament')
    w.save(); assert w.acts == [] and w.closed() and counted() == 280.0                      # as it opened: closed, and nothing entered
    watch.look(dict(H2D_JOB, layer_num=120)); assert counted() == 280.0                      # nor is the running print taken off a second time
    w = amount_asked(monkeypatch, row, res=res); w.spool.set('250'); w.save()               # one field changed: that is an amount
    assert w.acts == [('amount', (row, 230.0)), ('spool', 250.0)] and w.closed()
    w = amount_asked(monkeypatch, row, res=res); w.scale.set('481'); w.scale.set('480'); w.save()
    assert w.acts == [] and w.closed()                                                       # changed and put back is untouched too
    fg.update_state(lambda st: st['spools']['SERIAL']['C3'].__setitem__('stale', True))      # out of date: no figure to start from, and nothing to save unseen
    w = amount_asked(monkeypatch, dict(row, grams=None, stale=1e6), res=res)
    assert (w.scale.get(), w.result['text']) == ('', '') and list(w.buttons) == ['Save', 'Forget this amount']
    w.save(); assert not w.closed() and w.acts == [] and w.wrong['text'].startswith('The weight on the scale')
    for nothing in (lambda: {}, lambda: (_ for _ in ()).throw(fg.StateBusy('the state folder was held elsewhere for more than 10 s'))):
        monkeypatch.setattr(fg, 'load_state', nothing)                                       # no entry, or a state file that cannot be read: empty, not the card's old figure
        assert amount_asked(monkeypatch, row, res=res).scale.get() == ''
    assert amount_asked(monkeypatch, row).scale.get() == ''                                  # nor without a printer to look it up for
