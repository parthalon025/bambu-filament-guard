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
    assert log.count('TIGTH') == 1 and 'popup_on in config.json names "TIGTH", which is no result of a check' in log
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
    """A project sliced by one Bambu Studio (process 1), "slice all": one plate for each of weights, the first sliced
    first, a second apart, in place of what was sliced there before. Returns their files."""
    meta = tmp_path / 'bamboo_model' / 'Thu_Nov_19' / '01_02_03#1#9' / 'Metadata'; meta.mkdir(parents=True, exist_ok=True)
    (meta.parent / 'origin.txt').write_text(f'C:/prints/{title}.3mf')
    monkeypatch.setattr(fg.tempfile, 'gettempdir', lambda: str(tmp_path)); monkeypatch.setattr(fg, 'BS_DATA', str(tmp_path / 'none'))
    files, now = [], time.time()
    for f in meta.glob('.*.gcode'): f.unlink()
    for n, w in enumerate(weights):
        f = meta / f'.1.{n}.gcode'; f.write_text(gcode(weights=w)); fg.os.utime(f, (now - 600 + n, now - 600 + n)); files.append(f)
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
