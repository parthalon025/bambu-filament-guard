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
    """The card, as the guard sees it: what it was asked to show, in order."""
    def __init__(self): self.win, self.one_line, self.shown, self.beside = None, False, [], None
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

def tk_stand_in(monkeypatch):
    """Puts a stand-in in tkinter's place (there is none where no screen is) and returns the widgets made on it, in
    order: each keeps what it was made and configured with (k) and the keys bound to it (keys)."""
    made = []
    class Widget:
        def __init__(self, *a, **k): self.k, self.keys = dict(k), {}; made.append(self)
        def configure(self, **k): self.k.update(k)
        def bind(self, key, then): self.keys[key] = then
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
    save(); assert acts == [('spool', 230.0), ('amount', (tray, 720.0))]
    entered = dict(tray, grams=412.4, entered=1e6)
    scale, spool, result, wrong, save, buttons, acts = amount_window(monkeypatch, entered)
    assert (scale.get(), spool.get(), result['text'], buttons) == ('612', '200', '412 g of filament', ['Save', 'Forget this amount'])
    spool.set(''); scale.set('300'); save()                                                        # the filament weighed alone
    assert acts == [('spool', 0), ('amount', (entered, 300.0))]
    measured = dict(tray, label='C3', grams=110.0, reading=110.0)
    scale, spool, result, wrong, save, buttons, acts = amount_window(monkeypatch, measured, weigh=True)
    assert (scale.get(), buttons) == ('', ['Save'])                                                # a weigh-in is never guessed from the reading
    scale.set('292'); save(); assert acts == [('spool', 200.0), ('weigh', (measured, 92.0))]

@pytest.mark.windows
def test_a_weigh_in_far_from_the_reading_is_saved_only_when_saved_again(monkeypatch):
    """The review's slips: 8 typed for 86, another spool on the scale, a spool weighed after the running print had taken
    60 g of it. Each was kept as what the tray reads at empty and counted every tray of the printer lower."""
    c3 = {'label': 'C3', 'name': '', 'type': 'PLA', 'grams': 110.0, 'reading': 110.0}
    scale, spool, result, wrong, save, buttons, acts = amount_window(monkeypatch, c3, weigh=True)
    scale.set('208'); save()                                                                       # 8 g of filament, where 86 was meant
    assert acts == [] and wrong['text'] == '8 g weighed, 110 g read by the printer: 102 g apart. Save again if the weight is right.'
    scale.set('286'); assert wrong['text'] == '' and result['text'] == '86 g of filament'          # put right: the line goes
    save(); assert acts == [('spool', 200.0), ('weigh', (c3, 86.0))]                               # 24 g apart: saved at the first Save
    scale, spool, result, wrong, save, buttons, acts = amount_window(monkeypatch, c3, weigh=True)
    scale.set('208'); save(); assert acts == []
    save(); assert acts == [('spool', 200.0), ('weigh', (c3, 8.0))]                                # saved again with the same numbers: taken as right
    scale, spool, result, wrong, save, buttons, acts = amount_window(monkeypatch, c3, weigh=True)
    scale.set('208'); save(); spool.set('190'); save()                                             # a number changed in between: asked anew
    assert acts == [] and wrong['text'].startswith('18 g weighed, 110 g read by the printer: 92 g apart')
    scale.set('370'); save(); assert acts == [] and '180 g weighed' in wrong['text'] and '70 g apart' in wrong['text']   # more than it reads, too
    scale.set('350'); save(); assert acts == [('spool', 190.0), ('weigh', (c3, 160.0))]            # 50 g apart is within what a gauge can be out
    ext = {'label': 'Ext', 'name': '', 'type': 'PETG', 'grams': 400.0, 'entered': 1e6, 'reading': 400.0}
    scale, spool, result, wrong, save, buttons, acts = amount_window(monkeypatch, ext)
    scale.set('205'); save(); assert acts == [('spool', 200.0), ('amount', (ext, 5.0))]            # an amount entered for a spool the printer cannot measure has no reading to doubt it by

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


def test_a_failure_before_the_printer_answers_is_an_answer_with_its_reason(state, monkeypatch):
    """Whatever goes wrong while the printer is chosen and asked ends as "Filament could not be checked" with its reason,
    and is never quiet. An error the first part of evaluate did not list left as a raised error: a line in the log, the
    "Checking…" line closed, and no card."""
    running(monkeypatch, ENOUGH); monkeypatch.setattr(fg, 'printer_ip', lambda *a, **k: {}.missing)
    res = fg.evaluate(BLUE, CFG, 'Benchy')
    assert res['error'] == "AttributeError: 'dict' object has no attribute 'missing'" and not res.get('quiet')
    assert fg.headline(res) == 'Filament could not be checked' and fg.exit_code(res) == 3

def test_a_printers_address_given_as_plain_text_in_the_settings_is_used(state, monkeypatch):
    """{"printers": {"<serial>": "address"}}, the shape a user writes from the one-line hint: every check ended in an
    AttributeError that only the log saw."""
    (state / 'config.json').write_text('{"printers": {"SERIAL": "192.0.2.50"}}'); asked = []
    monkeypatch.setattr(fg, 'bs_conf', lambda: {'access_code': {'SERIAL': 'code'}}); monkeypatch.setattr(fg, 'model_by_serial_prefix', lambda: {'SER': 'Bambu Lab H2D'})
    monkeypatch.setattr(fg, 'read_status', lambda ip, serial, code: (asked.append(ip), ENOUGH)[1])
    res = fg.evaluate(BLUE, fg.load_config(), 'Benchy')
    assert asked == ['192.0.2.50'] and res['error'] is None and res['worst'] == 'OK'

def started(monkeypatch):
    """watch() run from its start to its end, on stand-ins for the mutex, Tk, the icon and the watcher's thread:
    (what it returned, the cards it showed as (headline, lines, tone))."""
    import guard_ui
    notes = []
    monkeypatch.setattr(fg, 'sole_guard', lambda: 0); monkeypatch.setattr(guard_ui, 'make_root', lambda: None); monkeypatch.setattr(guard_ui, 'Tray', lambda guard: None)
    monkeypatch.setattr(guard_ui.Card, 'note', lambda card, headline, lines, tone='muted', pid=None: notes.append((headline, lines, tone)))
    monkeypatch.setattr(fg.Guard, 'watch_slices', lambda guard: None); monkeypatch.setattr(fg.Guard, 'loop', run_queued)
    return fg.watch(), notes

@pytest.mark.windows
def test_the_guard_says_at_its_start_which_settings_are_not_in_use(state, monkeypatch):
    """A value in config.json the guard cannot use: it starts on the default for that setting, and a card says which."""
    (state / 'config.json').write_text('{"margin_pct": "25", "spool_reserve_pct": 5}')
    assert started(monkeypatch) == (0, [('Settings in config.json are not in use', [
        (False, 'margin_pct in config.json is "25", not a number from 0 to 100: the default is in use.'),
        (None, f'The file is {fg.CONFIG}. The guard reads it when it starts.')], 'danger')])
    (state / 'config.json').write_text('{"spool_reserve_pct": 5}')
    assert started(monkeypatch) == (0, [])                                                # nothing wrong: no card

def no_studio():
    raise FileNotFoundError('BambuStudio.conf')

@pytest.mark.windows
def test_the_self_test_fails_on_a_setting_that_is_not_in_use(state, monkeypatch):
    import guard_ui
    (state / 'config.json').write_text('{"margin_pct": "25"}'); wrong = (False, 'margin_pct in config.json is "25", not a number from 0 to 100: the default is in use')
    monkeypatch.setattr(fg, 'bs_conf', lambda: {'app': {'version': '02.09.01.70'}}); monkeypatch.setattr(fg, 'newest_gcode', lambda pid=None: None)
    monkeypatch.setattr(guard_ui, 'studio_windows', lambda *a: {})
    assert fg.selftest(fg.load_config())[0] == wrong
    monkeypatch.setattr(fg, 'bs_conf', no_studio)
    assert fg.selftest(fg.load_config())[0] == wrong                                      # also where Bambu Studio's configuration is not found

@pytest.mark.windows
def test_the_guard_says_at_its_start_that_its_settings_cannot_be_read(state, monkeypatch):
    """A comma too many in config.json: the defaults are in use, and the first card says so, where one line in the log did."""
    (state / 'config.json').write_text('{"margin_pct": 25,}')
    result, [(headline, [(mark, line), where], tone)] = started(monkeypatch)
    assert (result, headline, mark, tone) == (0, 'Settings in config.json are not in use', False, 'danger')
    assert line.startswith('config.json cannot be read (') and 'line 1 column 1' in line and line.endswith('): the defaults are in use.')

@pytest.mark.windows
def test_a_setting_changed_from_the_menu_leaves_what_was_edited_by_hand_meanwhile(state):
    (state / 'config.json').write_text('{"learn": false}'); guard = guard_with(fg.load_config())
    (state / 'config.json').write_text('{"learn": false, "margin_pct": 25}')            # by hand, while the guard runs
    guard.toggle_warning('UNKNOWN')
    assert fg.json.load(open(fg.CONFIG)) == {'learn': False, 'margin_pct': 25, 'popup_on': fg.DEFAULTS['popup_on'] + ['UNKNOWN']}

def install_on(monkeypatch, shortcut=''):
    """install() on stand-ins for Windows: the Startup shortcut points into the folder `shortcut` ('' = there is none),
    no Bambu Studio, and no guard runs until install starts its own. Returns what was done, in order."""
    done = []
    monkeypatch.setattr(fg, 'shortcut_folder', lambda: (done.append('shortcut read'), shortcut)[1])
    monkeypatch.setattr(fg, 'make_shortcut', lambda: done.append('shortcut made')); monkeypatch.setattr(fg, 'bs_conf', no_studio)
    monkeypatch.setattr(fg, 'is_running', lambda: 'start' in done)
    monkeypatch.setattr(fg, 'stop', lambda *a, **k: (done.append('stop'), 'not running')[1]); monkeypatch.setattr(fg, 'start', lambda *a, **k: done.append('start'))
    monkeypatch.setattr(fg.time, 'sleep', lambda s: None)
    return done

def test_install_brings_the_settings_over_from_the_folder_of_the_copy_installed_before(state, tmp_path, monkeypatch, capsys):
    """An update unzipped into a new folder: the Startup shortcut still points into the old one, whose config.json
    (up to 1.4.0, beside the script) holds the settings. They are brought over before the shortcut is made to point
    here, and the install card says so. They stayed behind, and the new guard ran on the defaults without a word."""
    old = tmp_path / 'old' / 'app'; old.mkdir(parents=True); (old / 'config.json').write_text('{"spool_reserve_pct": 5, "measured_reserve": false}')
    done = install_on(monkeypatch, shortcut=str(old)); ours, said = fg.install()
    assert done[:2] == ['shortcut read', 'shortcut made'] and fg.load_config()['spool_reserve_pct'] == 5
    assert ours and said == [(True, f'Your settings are kept: config.json was brought over from {old}.')] and f'brought over from {old}' in capsys.readouterr().out
    (old / 'config.json').write_text('{"spool_reserve_pct": 2}'); install_on(monkeypatch, shortcut=str(old))
    assert fg.install() == (True, []) and fg.load_config()['spool_reserve_pct'] == 5         # settings are there now: not again

def set_up(monkeypatch):
    """setup() on stand-ins for Tk, after install_on: the card it showed, as (headline, lines, tone)."""
    import guard_ui
    notes = []
    monkeypatch.setattr(guard_ui, 'make_root', lambda: type('Root', (), {'quit': None, 'after': lambda *a: None, 'mainloop': lambda *a: None})())
    monkeypatch.setattr(guard_ui.Card, 'note', lambda card, headline, lines, tone='muted', pid=None: notes.append((headline, lines, tone)))
    assert fg.setup() == 0 and len(notes) == 1
    return notes[0]

def no_powershell():
    raise fg.subprocess.CalledProcessError(1, ['powershell', '-NoProfile'], stderr='Cannot create type. Only core types are supported in this language mode.\n')

@pytest.mark.windows
def test_install_starts_the_guard_and_says_so_when_the_startup_shortcut_cannot_be_made(state, monkeypatch):
    """PowerShell in Constrained Language Mode, blocked by a policy or not found: the shortcut cannot be made. Install.cmd
    then did nothing at all: no card, no line in the log, and no guard for this session either."""
    done = install_on(monkeypatch); monkeypatch.setattr(fg, 'make_shortcut', no_powershell)
    headline, lines, tone = set_up(monkeypatch)
    assert done == ['shortcut read', 'stop', 'start'] and (headline, tone) == ('Bambu Filament Guard runs, but is not fully installed', 'warn')
    assert (False, 'It runs now but will not start with Windows: the Startup shortcut could not be made (PowerShell ended with code 1).') in lines
    assert 'the Startup shortcut could not be made (PowerShell ended with code 1): Cannot create type. Only core types are supported in this language mode.' in (state / 'guard.log').read_text()

@pytest.mark.windows
def test_setup_says_on_its_card_whatever_else_kept_it_from_installing(state, monkeypatch):
    """Install.cmd runs without a console: an error that left install as an error was seen by nobody."""
    install_on(monkeypatch); monkeypatch.setattr(fg, 'start', lambda *a, **k: 1 / 0)
    headline, lines, tone = set_up(monkeypatch)
    assert (headline, tone) == ('Bambu Filament Guard did not start', 'danger') and (False, f"It could not be installed (ZeroDivisionError: division by zero). The log is {state / 'guard.log'}.") in lines
    assert 'install failed: ZeroDivisionError: division by zero' in (state / 'guard.log').read_text()

@pytest.mark.windows
def test_a_guard_that_fails_while_it_starts_says_so_in_the_log(state, monkeypatch):
    """The guard runs without a console (pythonw): Tk, the icon or a stop file that cannot be removed ended it at its
    start with nothing anywhere."""
    import guard_ui
    def no_screen(): raise RuntimeError('no display')
    monkeypatch.setattr(fg, 'sole_guard', lambda: 0); monkeypatch.setattr(guard_ui, 'make_root', no_screen)
    assert fg.watch() == 1 and 'the guard has stopped on an error: RuntimeError: no display (test_guard.py, line ' in (state / 'guard.log').read_text()

def uninstall_on(monkeypatch, tmp_path, shortcut, guard):
    """uninstall() where the Startup shortcut points into the folder `shortcut` and a guard runs that was started from
    the folder `guard` ('' = a guard up to 1.4.0, which did not note it; None = no guard runs): (is the shortcut still
    there?, was the guard told to stop?)."""
    lnk, stopped = tmp_path / 'Bambu Filament Guard.lnk', []
    lnk.write_text(''); monkeypatch.setattr(fg, 'STARTUP_LNK', str(lnk)); monkeypatch.setattr(fg, 'shortcut_folder', lambda: shortcut)
    (tmp_path / 'guard.pid').write_text('4242' if not guard else f'4242\n{guard}\n')
    monkeypatch.setattr(fg, 'is_running', lambda: guard is not None and not stopped); monkeypatch.setattr(fg, 'stop', lambda *a, **k: (stopped.append(1), 'stopped')[1])
    fg.uninstall()
    return lnk.exists(), bool(stopped)

def test_uninstall_leaves_the_shortcut_and_the_guard_of_a_copy_in_another_folder(state, tmp_path, monkeypatch, capsys):
    """An update unzipped beside the old copy and installed: the one Startup shortcut now points into the new folder,
    and the guard that runs is the new one. Uninstall.cmd in the old folder removed that shortcut and stopped that
    guard, and said "stopped, shortcut removed": no guard ran from then on, nor after the next login."""
    new = str(tmp_path / 'new' / 'app')
    assert uninstall_on(monkeypatch, tmp_path, new, new) == (True, False)
    out = capsys.readouterr().out
    assert f'the Startup shortcut starts the guard in {new}, not this copy: left alone' in out and f'the running guard was started from {new}, not from this folder: left alone' in out
    assert uninstall_on(monkeypatch, tmp_path, fg.HERE, fg.HERE) == (False, True)           # this folder's own: both go, as before
    out = capsys.readouterr().out
    assert 'shortcut removed' in out and 'stopped' in out and 'left alone' not in out
    assert uninstall_on(monkeypatch, tmp_path, fg.HERE, None) == (False, False) and 'was not running' in capsys.readouterr().out
    assert uninstall_on(monkeypatch, tmp_path, '', '') == (True, False)                       # neither can be told: neither is touched, and it says so
    out = capsys.readouterr().out
    assert 'the Startup shortcut could not be read: left alone' in out and 'it is not known from which folder the running guard was started: left alone' in out

@pytest.mark.windows
def test_the_guard_notes_the_folder_it_was_started_from(state, monkeypatch):
    """For uninstall, which stops only the guard of its own folder: beside its process id in guard.pid, which nothing read."""
    assert fg.guard_folder() == ''
    started(monkeypatch)
    assert (state / 'guard.pid').read_text() == f'{fg.os.getpid()}\n{fg.HERE}\n' and fg.guard_folder() == fg.HERE

def old_guard(monkeypatch, state, stops_after):
    """A guard that is already running, on a clock that only time.sleep moves: once the stop flag is there it stops
    when the clock reaches `stops_after` seconds (None: never: its watcher hangs). install's shortcut and Bambu Studio
    are stood in for. Returns (the clock, the times at which a new guard was started)."""
    clock, started, flag = [0.0], [], state / 'stop'
    monkeypatch.setattr(fg.time, 'sleep', lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(fg, 'is_running', lambda: bool(started) or stops_after is None or clock[0] < stops_after or not flag.exists())
    monkeypatch.setattr(fg, 'start', lambda *a, **k: started.append(clock[0]))
    monkeypatch.setattr(fg, 'shortcut_folder', lambda: ''); monkeypatch.setattr(fg, 'make_shortcut', lambda: None); monkeypatch.setattr(fg, 'bs_conf', no_studio)
    return clock, started

def test_stopping_the_guard_tells_stopped_from_still_running_and_not_running(state, monkeypatch, capsys):
    """A guard in the middle of a check reads the stop flag when that check ends. After 10 s, "stop" and "uninstall" took
    one that was still running for one that was not, and said "was not running"."""
    clock, _ = old_guard(monkeypatch, state, 4.0)
    assert fg.stop() == 'stopped' and 4.0 <= clock[0] < 5.0 and (state / 'stop').exists()
    assert fg.stop() == 'not running'
    (state / 'stop').unlink(); clock, _ = old_guard(monkeypatch, state, 25.0)
    assert fg.stop() == 'still running' and 10.0 <= clock[0] < 11.0 and (state / 'stop').exists()      # the flag stands: it stops when its check ends
    (state / 'stop').unlink(); (state / 'guard.pid').write_text(f'4242\n{fg.HERE}\n'); old_guard(monkeypatch, state, 25.0)
    assert fg.main(['stop']) == 0 and capsys.readouterr().out.strip() == 'still running: it stops when its current check ends'
    (state / 'stop').unlink(); old_guard(monkeypatch, state, 25.0); fg.uninstall()
    assert 'still running: it stops when its current check ends' in capsys.readouterr().out

def test_install_waits_for_the_guard_that_runs_to_go_before_it_starts_its_own(state, monkeypatch, capsys):
    """Updating while the old guard checks a printer that is off ("slice all": some 13 s a plate): install gave up
    waiting after 10 s, started nothing because a guard was running, and said "watcher running". Seconds later the
    old guard read the stop flag and went: no guard ran until the next login."""
    clock, started = old_guard(monkeypatch, state, 40.0)
    assert fg.main(['install']) == 0 and len(started) == 1 and 40.0 <= started[0] < 41.0 and 'watcher running' in capsys.readouterr().out

def test_install_says_which_guard_runs_when_the_one_that_ran_does_not_go(state, monkeypatch, capsys):
    """A guard whose watcher hangs never reads the stop flag. Install waits a minute, then leaves it running (the flag
    is taken back: it must not stop later with nothing in its place), says that it is still the one that runs, and
    exits 1, as it does when no guard runs at its end."""
    (state / 'guard.pid').write_text('4242\nC:\\old\\app\n'); clock, started = old_guard(monkeypatch, state, None)
    ours, said = fg.install()
    assert (ours, started) == (False, []) and clock[0] >= 60.0 and not (state / 'stop').exists()
    assert said == [(False, "The guard that was already running (from C:\\old\\app) has not stopped, and is still the one that runs. Quit it from its icon's menu, then open Install.cmd again.")]
    (state / 'stop').unlink(missing_ok=True); old_guard(monkeypatch, state, None)
    assert fg.main(['install']) == 1 and 'has not stopped' in capsys.readouterr().out
    monkeypatch.setattr(fg, 'is_running', lambda: False)                                     # no guard before, and its own does not come up
    ours, said = fg.install()
    assert not ours and said == [(False, f"The guard did not start. The log may say why: {state / 'guard.log'}.")] and fg.main(['install']) == 1
    assert 'watcher did NOT start' in capsys.readouterr().out

def test_a_status_without_its_spools_is_not_judged(state, monkeypatch, capsys):
    """A piece of the printer's report taken for the whole: every spool read as not loaded, the card said "not loaded",
    and each amount entered by hand for an AMS tray was deleted as if the tray were empty. Such a status is answered
    "did not report its spools", by a check and by "trays", and no entry ends on it."""
    import guard_ledger as gl
    running(monkeypatch, {'gcode_state': 'IDLE', 'ams': {'tray_now': '255', 'version': 4}})
    gl.enter('SERIAL', {'label': 'A1', 'type': 'PLA', 'colour': '#0A2989'}, 640)
    res = fg.evaluate(BLUE, CFG, 'Benchy')
    assert res['error'] == 'the Bambu Lab H2D did not report its spools' and res['rows'] == [] and fg.exit_code(res) == 3
    assert fg.load_state()['spools']['SERIAL']['A1']['grams'] == 640
    assert fg.main(['trays']) == 0 and 'Bambu Lab H2D: did not report its spools' in capsys.readouterr().out
    assert fg.load_state()['spools']['SERIAL']['A1']['grams'] == 640

@pytest.mark.windows
def test_the_self_test_does_not_count_the_spools_of_a_status_without_them(state, monkeypatch):
    import guard_ui, guard_ledger as gl
    running(monkeypatch, {'gcode_state': 'IDLE', 'ams': {'tray_now': '255', 'version': 4}}); gl.enter('SERIAL', {'label': 'A1', 'type': 'PLA', 'colour': '#0A2989'}, 640)
    monkeypatch.setattr(fg, 'newest_gcode', lambda pid=None: None); monkeypatch.setattr(guard_ui, 'studio_windows', lambda *a: {})
    lines = fg.selftest(CFG)
    assert (None, 'Bambu Lab H2D: it did not report its spools') in lines and fg.load_state()['spools']['SERIAL']['A1']['grams'] == 640
