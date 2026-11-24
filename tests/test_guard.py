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
    order: each keeps what it was made and configured with (k) and the keys bound to it (keys). A window is 300 by
    100 pixels on a screen at 100 %, and keeps where it was put (k['geometry'])."""
    made = []
    class Widget:
        def __init__(self, *a, **k): self.k, self.keys, self.parent = dict(k), {}, (a[0] if a else None); made.append(self)
        def configure(self, **k): self.k.update(k)
        def bind(self, key, then): self.keys[key] = then
        def destroy(self): self.k['destroyed'] = True
        def winfo_exists(self): return not self.k.get('destroyed')
        def lift(self): self.k['lifted'] = self.k.get('lifted', 0) + 1
        def geometry(self, where=None): self.k['geometry'] = where
        def winfo_reqheight(self): return 100
        def winfo_reqwidth(self): return 300
        def winfo_fpixels(self, what): return 96.0
        def wm_frame(self): return '0x1'
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
    assert log.count('at "Send print job": Enough filament for this print') == 1 and f'cannot read the slice {bad}: ValueError' in log
    assert guard.last['plate_no'] == 2 and guard.last['error']                              # and the plate that was not read is what shows: it was not checked

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
    guard.act('weigh', res, (res['weigh'], 92.0)); guard.todo.get(timeout=5)()              # kept once the printer was asked again: see the weigh-in's own test
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
    guard.toggle_warning('COLOUR'); assert guard.cfg['popup_on'] == ['SHORT'] and fg.load_config()['popup_on'] == ['SHORT']     # the name that is none is not written back
    guard.toggle_warning('UNKNOWN'); assert guard.popup_on() == ['SHORT', 'UNKNOWN'] and '_wrong' not in fg.load_config()

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
    return dict({'title': title, 'plate_no': plate_no, 'pid': pid, 'error': error, 'worst': status, 'times': 1, 'rows': [{'slot': 1, 'status': status}],
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
    buttons by their text, what it told the guard (acts), whether it has closed, and its widgets as made. act: what telling the guard does;
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
    w.made = made
    return w

EXT = {'label': 'Ext', 'name': '', 'type': 'PETG', 'colour': '#101010', 'grams': None}

@pytest.mark.windows
def test_saving_an_amount_does_not_wait_on_the_empty_spools_weight(state, monkeypatch):
    """The empty spool's weight was kept first, in config.json, and the amount after it: with the guard's folder not
    writable, Save did nothing and said nothing. The amount comes first; a weight that cannot be kept is logged and
    used until the guard stops."""
    guard = fg.Guard(dict(CFG), type('Root', (), {'quit': None})(), standing=False); monkeypatch.setattr(guard, 'recheck', lambda res, beside=None: None)
    def refused(cfg, key=None): raise PermissionError(13, 'Permission denied', fg.CONFIG)
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
        (False, 'margin_pct in config.json is "25", not a number from 0 to 1000: the default is in use.'),
        (None, f'The file is {fg.CONFIG}. The guard reads it when it starts.')], 'danger')])
    (state / 'config.json').write_text('{"spool_reserve_pct": 5}')
    assert started(monkeypatch) == (0, [])                                                # nothing wrong: no card

def no_studio():
    raise FileNotFoundError('BambuStudio.conf')

@pytest.mark.windows
def test_the_self_test_fails_on_a_setting_that_is_not_in_use(state, monkeypatch):
    """What config.json holds and the guard cannot use is the self-test's first failed line, with Bambu Studio found or not."""
    import guard_ui
    (state / 'config.json').write_text('{"margin_pct": "25"}'); wrong = (False, 'margin_pct in config.json is "25", not a number from 0 to 1000: the default is in use')
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
    """The menu's click saves its one setting: a margin written into config.json since the guard started stays."""
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

def set_up(monkeypatch, mutex=7, shown=None):
    """setup() on stand-ins for Tk and for the mutex it holds while it installs (None: another setup holds it), after
    install_on. The stand-in's main loop turns, as Tk's does, until the card that ends the install is shown, and that
    card is closed at once. Returns what was shown, in order: ('line', text), ('mutex let go', it), and the card as
    (headline, lines, tone)."""
    import guard_ui
    shown = [] if shown is None else shown
    class Root:
        def __init__(self): self.todo, self.over = [], False
        def after(self, ms, then): self.todo.append(then)
        def quit(self): self.over = True
        def mainloop(self):
            turn = threading.Event()
            for _ in range(2000):                                                          # ten seconds at most
                if self.over: return
                for then in self.todo[:]: self.todo.remove(then); then()
                turn.wait(0.005)
    root = Root()
    monkeypatch.setattr(guard_ui, 'make_root', lambda: root); monkeypatch.setattr(fg, 'sole_guard', lambda name=None: mutex)
    monkeypatch.setattr(fg, 'let_go', lambda handle: shown.append(('mutex let go', handle)))
    monkeypatch.setattr(guard_ui.Card, 'line', lambda card, text, *a, **k: shown.append(('line', text)))
    monkeypatch.setattr(guard_ui.Card, 'note', lambda card, headline, lines, tone='muted', **k: (shown.append((headline, lines, tone)), root.quit()))
    assert fg.setup() == 0
    return shown

def no_powershell():
    raise fg.subprocess.CalledProcessError(1, ['powershell', '-NoProfile'], stderr='Cannot create type. Only core types are supported in this language mode.\n')

@pytest.mark.windows
def test_install_starts_the_guard_and_says_so_when_the_startup_shortcut_cannot_be_made(state, monkeypatch):
    """PowerShell in Constrained Language Mode, blocked by a policy or not found: the shortcut cannot be made. Install.cmd
    then did nothing at all: no card, no line in the log, and no guard for this session either."""
    done = install_on(monkeypatch); monkeypatch.setattr(fg, 'make_shortcut', no_powershell)
    headline, lines, tone = set_up(monkeypatch)[-1]
    assert done == ['shortcut read', 'stop', 'start'] and (headline, tone) == ('Bambu Filament Guard runs, but is not fully installed', 'warn')
    assert (False, 'It runs now but will not start with Windows: the Startup shortcut could not be made (PowerShell ended with code 1).') in lines
    assert 'the Startup shortcut could not be made (PowerShell ended with code 1): Cannot create type. Only core types are supported in this language mode.' in (state / 'guard.log').read_text()

@pytest.mark.windows
def test_setup_says_on_its_card_whatever_else_kept_it_from_installing(state, monkeypatch):
    """Install.cmd runs without a console: an error that left install as an error was seen by nobody."""
    install_on(monkeypatch); monkeypatch.setattr(fg, 'start', lambda *a, **k: 1 / 0)
    headline, lines, tone = set_up(monkeypatch)[-1]
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
    """A piece of a report: the self-test said "0 spool(s)" of it and ended the entered amounts; it says what is missing."""
    import guard_ui, guard_ledger as gl
    running(monkeypatch, {'gcode_state': 'IDLE', 'ams': {'tray_now': '255', 'version': 4}}); gl.enter('SERIAL', {'label': 'A1', 'type': 'PLA', 'colour': '#0A2989'}, 640)
    monkeypatch.setattr(fg, 'newest_gcode', lambda pid=None: None); monkeypatch.setattr(guard_ui, 'studio_windows', lambda *a: {})
    lines = fg.selftest(CFG)
    assert (None, 'Bambu Lab H2D: it did not report its spools') in lines and fg.load_state()['spools']['SERIAL']['A1']['grams'] == 640

def three_printers(monkeypatch, selected, status=ENOUGH):
    """Bambu Studio knows two P1S and an H2D, in this order, and has `selected` selected. Returns the serials asked."""
    asked = []
    monkeypatch.setattr(fg, 'bs_conf', lambda: {'app': {'user_last_selected_machine': selected}, 'access_code': {'P1S-ONE-0001': 'code', 'P1S-TWO-0002': 'code', 'H2D-ONE-0003': 'code'}})
    monkeypatch.setattr(fg, 'model_by_serial_prefix', lambda: {'P1S': 'Bambu Lab P1S', 'H2D': 'Bambu Lab H2D'})
    monkeypatch.setattr(fg, 'printer_ip', lambda serial, *a, **k: 'printer'); monkeypatch.setattr(fg, 'read_status', lambda ip, serial, code: (asked.append(serial), status)[1])
    return asked

P1S_PLATE = dict(BLUE, printer_model='Bambu Lab P1S')

def test_with_two_printers_of_one_model_the_answer_names_the_one_asked(state, monkeypatch):
    """Two P1S: one holds a full spool, the job goes to the other. The answer said "Bambu Lab P1S" and nothing more, in
    the report, the log and on the card: nothing showed that the other printer had answered."""
    asked = three_printers(monkeypatch, 'P1S-TWO-0002'); res = fg.evaluate(P1S_PLATE, CFG, 'Vase')
    assert asked == ['P1S-TWO-0002'] and res['printer']['name'] == 'Bambu Lab P1S (…0002)' and res['printer']['model'] == 'Bambu Lab P1S'
    assert '\n  Bambu Lab P1S (…0002), IDLE' in fg.report_text(res) and fg.checking_text(fg.printer_for(P1S_PLATE)) == 'Checking the spools on the Bambu Lab P1S (…0002)…'
    asked = three_printers(monkeypatch, 'H2D-ONE-0003'); res = fg.evaluate(P1S_PLATE, CFG, 'Vase')   # the printer selected is of another model:
    assert asked == ['P1S-ONE-0001'] and res['printer']['name'] == 'Bambu Lab P1S (…0001)'           # the first P1S of Bambu Studio's configuration
    res = fg.evaluate(BLUE, CFG, 'Benchy')
    assert asked[-1] == 'H2D-ONE-0003' and res['printer']['name'] == 'Bambu Lab H2D' and '\n  Bambu Lab H2D, IDLE' in fg.report_text(res)   # the only one of its model: the model says it
    monkeypatch.setattr(fg, 'printer_ip', lambda *a, **k: None)
    assert fg.evaluate(P1S_PLATE, CFG, 'Vase')['error'].startswith('the Bambu Lab P1S (…0001) is not announcing itself')

@pytest.mark.windows
def test_the_card_names_the_printer_asked_where_two_are_of_one_model(state, monkeypatch):
    """The one-line card and the line under the full card's table, idle and printing, name the printer that answered."""
    three_printers(monkeypatch, 'P1S-TWO-0002'); res = fg.evaluate(P1S_PLATE, CFG, 'Vase')
    assert card_shown(monkeypatch, res, brief=8)[0][-1] == '1 filament against the spools on the Bambu Lab P1S (…0002)'
    assert card_shown(monkeypatch, res)[0][-1].startswith('Spools as the Bambu Lab P1S (…0002) reports them now;')
    busy = dict(ENOUGH, gcode_state='RUNNING', subtask_name='Cube'); three_printers(monkeypatch, 'P1S-TWO-0002', busy)
    assert 'Spools as the Bambu Lab P1S (…0002) reports them now' in card_shown(monkeypatch, fg.evaluate(P1S_PLATE, CFG, 'Vase'))[0][-1]

def knows(monkeypatch, *serials):
    """Bambu Studio holds an access code for these printers, and its list of printers names the P1S and the H2D."""
    monkeypatch.setattr(fg, 'bs_conf', lambda: {'access_code': {s: 'code' for s in serials}})
    monkeypatch.setattr(fg, 'model_by_serial_prefix', lambda: {'P1S': 'Bambu Lab P1S', 'H2D': 'Bambu Lab H2D'})
    monkeypatch.setattr(fg, 'printer_ip', lambda *a, **k: 'printer'); monkeypatch.setattr(fg, 'read_status', lambda *a, **k: ENOUGH)

def test_a_printer_whose_model_cannot_be_told_is_a_fault_that_shows(state, monkeypatch):
    """Bambu Studio's list of printers has no entry for the serial of the user's printer (a model newer than the list):
    no plate matched it, every check ended quiet, with no card, and the log said, untruly, that Bambu Studio holds no
    access code for the plate's model."""
    knows(monkeypatch, 'NEW-ONE-0001'); res = fg.evaluate(BLUE, CFG, 'Benchy')
    assert not res.get('quiet') and fg.headline(res) == 'Filament could not be checked'
    assert res['error'] == ("the guard cannot tell which model your printer …0001 is (Bambu Studio's list of printers does not name its serial number), "
                            "so this plate, sliced for a Bambu Lab H2D, was not checked")
    knows(monkeypatch, 'NEW-ONE-0001', 'H2D-ONE-0003')
    assert fg.evaluate(BLUE, CFG, 'Benchy')['printer']['name'] == 'Bambu Lab H2D'              # a printer of the plate's model is known: that one is asked

def test_a_plate_sliced_for_another_model_than_the_one_printer_known_is_answered_in_one_line(state, monkeypatch):
    """One printer, and a plate sliced with another model's preset: nothing showed at all, which is what "enough" with
    the one line turned off looks like. With several printers known and none of the plate's model it stays quiet."""
    knows(monkeypatch, 'P1S-ONE-0001'); res = fg.evaluate(BLUE, CFG, 'Benchy')
    assert res['quiet'] and res['line'] == 'Not checked: sliced for a Bambu Lab H2D, your printer is a Bambu Lab P1S' and fg.exit_code(res) == 3
    assert res['error'] == 'not checked: sliced for a Bambu Lab H2D, your printer is a Bambu Lab P1S'
    knows(monkeypatch, 'P1S-ONE-0001', 'P1S-TWO-0002'); res = fg.evaluate(BLUE, CFG, 'Benchy')
    assert res['quiet'] and 'line' not in res and res['error'] == 'Bambu Studio holds no LAN access code for a Bambu Lab H2D'
    knows(monkeypatch); res = fg.evaluate(BLUE, CFG, 'Benchy')
    assert res['quiet'] and 'line' not in res

@pytest.mark.windows
def test_the_one_line_for_a_plate_that_is_not_checked_shows_as_the_enough_line_does(state, tmp_path, monkeypatch):
    """After a slice, held while Bambu Studio is minimised; not beside "Send print job"; not with the one line turned
    off; and the card with its reason when the answer is asked for."""
    f = slice_file(tmp_path, gcode()); plate = fg.parse_plate(f.read_text()); knows(monkeypatch, 'P1S-ONE-0001')
    guard = guard_with(); guard.take_slice(str(f), plate); run_queued(guard)
    assert guard.card.shown == [('line', 'Not checked: sliced for a Bambu Lab H2D, your printer is a Bambu Lab P1S', True)]
    res = guard.last; guard = guard_with(); guard.deliver(res, beside=4321)
    assert guard.card.shown == []                                                              # beside "Send print job" only a warning is worth a card
    guard.deliver(res, full=True); assert guard.card.shown == [('show', 0)]                    # asked for: the card, with the reason
    guard = guard_with(dict(CFG, ok_seconds=0)); guard.deliver(res)
    assert guard.card.shown == []                                                              # quiet was asked for

@pytest.mark.windows
def test_the_self_test_fails_on_a_printer_whose_model_cannot_be_told(state, monkeypatch):
    """It read that printer's spools and called it working, under the name "unknown model"; the other printer is still asked."""
    import guard_ui
    knows(monkeypatch, 'NEW-ONE-0001', 'H2D-ONE-0003'); monkeypatch.setattr(fg, 'newest_gcode', lambda pid=None: None); monkeypatch.setattr(guard_ui, 'studio_windows', lambda *a: {})
    lines = fg.selftest(CFG)
    assert (False, "The printer …0001: the guard cannot tell which model it is (Bambu Studio's list of printers does not name its serial number): plates sliced for it are not checked") in lines
    assert (True, 'Bambu Lab H2D: 2 spool(s), 2 with a known amount') in lines and not any('unknown model' in text for mark, text in lines)

def announcements(monkeypatch, want, *datagrams):
    """discover() asked for the serials `want`, on a stand-in for the two UDP sockets it listens on: these datagrams
    arrive on port 2021, one a look."""
    waiting = list(datagrams)
    class Socket:
        def __init__(self, *a): self.port = None
        def setsockopt(self, *a): pass
        def settimeout(self, seconds): pass
        def close(self): pass
        def bind(self, where): self.port = where[1]
        def recvfrom(self, size):
            if self.port != 2021 or not waiting: raise OSError('timed out')
            return waiting.pop(0), ('192.0.2.99', 2021)
    monkeypatch.setattr(fg.socket, 'socket', Socket)
    return fg.discover(want, seconds=0.05)

def notice(usn, location, nt):
    return f'NOTIFY * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nServer: UPnP/1.0\r\nLocation: {location}\r\nNT: {nt}\r\nUSN: {usn}\r\n\r\n'.encode()

def test_only_a_bambu_printers_announcement_is_taken_for_a_printer(monkeypatch):
    """Every datagram with a USN line was taken: a television or a router announcing itself on the same ports was kept
    in the state file as a printer's address."""
    found = announcements(monkeypatch, ['SERIAL', 'uuid:a-television'], notice('uuid:a-television', '192.0.2.9', 'urn:schemas-upnp-org:device:MediaRenderer:1'),
                          notice('SERIAL', '192.0.2.50', 'urn:bambulab-com:device:3dprinter'), b'not an announcement at all')
    assert found == {'SERIAL': '192.0.2.50'}

def test_a_printer_that_is_not_heard_is_not_only_asked_whether_it_is_on(state, monkeypatch, capsys):
    """Windows Firewall keeping the printer's announcements from the guard gives the same silence as a printer that is
    off: the answer names both, and the address that can be given in config.json, whose shape no document showed."""
    running(monkeypatch, ENOUGH); monkeypatch.setattr(fg, 'printer_ip', lambda *a, **k: None)
    said = ('is not announcing itself on this network. Is it on? If it is, Windows Firewall may keep its announcements from the guard: allow the guard on private networks, '
            'or give the printer\'s address in config.json: {"printers": {"<serial>": {"ip": "<address>"}}}. A printer with a public address must be named there: '
            'an announcement is taken for a local address only')
    assert fg.evaluate(BLUE, CFG, 'Benchy')['error'] == f'the Bambu Lab H2D {said}'
    assert fg.main(['trays']) == 0 and f'Bambu Lab H2D: {said[3:]}' in capsys.readouterr().out

def a_link(monkeypatch, heard):
    """printer_link on a stand-in for paho-mqtt: the client it made, which keeps what it sent; `heard` gets every report."""
    mods = {name: type(sys)(name) for name in ('paho', 'paho.mqtt', 'paho.mqtt.client')}
    class Client:
        def __init__(self, *a, **k): self.sent = []
        def subscribe(self, topic): self.sent.append(('subscribe', topic))
        def publish(self, topic, payload): self.sent.append(('publish', topic, payload))
        def __getattr__(self, name): return lambda *a, **k: None
    mods['paho'].mqtt, mods['paho.mqtt'].client, mods['paho.mqtt.client'].Client = mods['paho.mqtt'], mods['paho.mqtt.client'], Client
    mods['paho.mqtt.client'].CallbackAPIVersion = type('CallbackAPIVersion', (), {'VERSION2': 2})
    for name, mod in mods.items(): monkeypatch.setitem(sys.modules, name, mod)
    return fg.printer_link('printer', 'SERIAL', 'code', 'a-test', heard.append)

def test_a_message_that_is_json_but_no_object_does_not_end_the_link(monkeypatch):
    """A payload such as [1, 2] raised AttributeError in the link's own thread, which ended it: the read then failed
    with "did not report its status" although the whole report followed."""
    heard = []; link = a_link(monkeypatch, heard); message = lambda payload: type('Message', (), {'payload': payload})()
    for payload in (b'[1, 2]', b'null', b'7', b'"text"', b'not json', b'{"print": [1]}', b'{"info": {}}'): link.on_message(link, None, message(payload))
    link.on_message(link, None, message(b'{"print": {"gcode_state": "IDLE"}}'))
    assert heard == [{'gcode_state': 'IDLE'}]
    link.on_connect(link, None, {}, 0)
    assert link.sent == [('subscribe', 'device/SERIAL/report'), ('publish', 'device/SERIAL/request', '{"pushing": {"sequence_id": "0", "command": "pushall"}}')]   # and it sends the printer nothing else

def refusing(monkeypatch, why):
    """read_status against a printer that refuses the connection, for the reason paho names (paho 2) or numbers (paho 1)."""
    def link(ip, serial, code, client_id, on_report, on_refused=None):
        on_refused(why); return type('Client', (), {'loop_stop': lambda self: None, 'disconnect': lambda self: None})()
    monkeypatch.setattr(fg, 'printer_link', link)
    with pytest.raises(fg.PrinterError) as e: fg.read_status('printer', 'SERIAL', 'code')
    return str(e.value)

def test_a_refusal_that_is_not_about_the_access_code_is_not_called_one(monkeypatch):
    """A printer that is still starting refuses with "server unavailable". Every refusal was reported as "refused the
    access code Bambu Studio holds for it", which sends the user to an access code that is right."""
    for why in ('Bad user name or password', 'Not authorized', '4', '5'):
        assert refusing(monkeypatch, why) == 'printer refused the access code Bambu Studio holds for it'
    assert refusing(monkeypatch, 'Server unavailable') == 'printer refused the connection (Server unavailable)'
    assert refusing(monkeypatch, '3') == 'printer refused the connection (3)'

def test_trays_ends_in_words_whatever_it_meets(state, monkeypatch, capsys):
    """"trays" where Bambu Studio was never installed ended in a FileNotFoundError traceback; with no access code in its
    configuration it printed nothing at all; and one printer's error cost the printers after it their answer."""
    monkeypatch.setattr(fg, 'bs_conf', no_studio)
    assert fg.main(['trays']) == 1 and capsys.readouterr().out == "Bambu Studio's configuration cannot be read (FileNotFoundError): is Bambu Studio installed for this Windows user?\n"
    knows(monkeypatch)
    assert fg.main(['trays']) == 1 and capsys.readouterr().out == 'Bambu Studio holds no LAN access code for a printer: open the printer once on its Device tab\n'
    knows(monkeypatch, 'P1S-ONE-0001', 'H2D-ONE-0003')
    def read(ip, serial, code):
        if serial.startswith('P1S'): raise RuntimeError('a report the guard did not foresee')
        return ENOUGH
    monkeypatch.setattr(fg, 'read_status', read)
    assert fg.main(['trays']) == 0
    out = capsys.readouterr().out
    assert 'Bambu Lab P1S: RuntimeError: a report the guard did not foresee\n' in out and 'Bambu Lab H2D (printer), IDLE\n  A1 ' in out

@pytest.mark.windows
def test_the_self_test_ends_in_lines_whatever_it_meets_and_fails_when_no_printer_answered(state, monkeypatch, capsys):
    """The self-test from the icon runs without a console: an error it did not expect of one printer showed nothing at
    all. And with no printer reached it said "the guard works" and exited 0, against "Exit code 1 = no"."""
    import guard_ui
    monkeypatch.setattr(fg, 'newest_gcode', lambda pid=None: None); monkeypatch.setattr(guard_ui, 'studio_windows', lambda *a: {})
    knows(monkeypatch, 'P1S-ONE-0001', 'H2D-ONE-0003')
    def read(ip, serial, code):
        if serial.startswith('P1S'): raise RuntimeError('a report the guard did not foresee')
        return ENOUGH
    monkeypatch.setattr(fg, 'read_status', read); lines = fg.selftest(CFG)
    assert (False, 'Bambu Lab P1S: RuntimeError: a report the guard did not foresee') in lines and (True, 'Bambu Lab H2D: 2 spool(s), 2 with a known amount') in lines
    assert not any('No known printer answered' in text for mark, text in lines) and lines[-1][1].startswith('It ')        # one answered; and the self-test went on to its end
    monkeypatch.setattr(fg, 'printer_ip', lambda *a, **k: None); lines = fg.selftest(CFG)                                     # both are off
    assert [mark for mark, text in lines if 'not announcing itself' in text] == [None, None]
    assert (False, 'No known printer answered: a plate cannot be checked until one does') in lines and fg.main(['selftest']) == 1
    assert '[!!] No known printer answered' in capsys.readouterr().out
    monkeypatch.setattr(fg, 'load_state', lambda: 1 / 0); lines = fg.selftest(CFG)
    assert lines[-1] == (False, 'The self-test could not finish (ZeroDivisionError: division by zero)') and lines[0][0] is True

def test_install_starts_the_guard_whatever_becomes_of_its_list_of_printers(state, monkeypatch, capsys):
    """Install prints where each known printer is, before it starts the guard. An error there other than a missing
    Bambu Studio (a configuration of another shape, a state file held elsewhere) ended the install before the guard
    was started."""
    done = install_on(monkeypatch); monkeypatch.setattr(fg, 'bs_conf', lambda: ['not', 'a', 'configuration'])
    assert fg.install() == (True, []) and done[-1] == 'start'
    assert 'the printers could not be listed (AttributeError)' in capsys.readouterr().out

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

def c3_reads(grams, tag='A' * 32, colour='0A2989', state='IDLE'):
    """The H2D with one blue PLA spool in C3, tagged, at this reading; another tag or colour is another spool."""
    tray = {'id': '2', 'tray_type': 'PLA', 'tray_color': colour + 'FF', 'tray_info_idx': 'GFA00', 'remain_g': grams, 'tray_weight': '1000', 'tray_uuid': tag}
    return {'gcode_state': state, 'subtask_name': 'Benchy', 'layer_num': 3, 'total_layer_num': 27, 'ams': {'ams': [{'id': '2', 'tray': [tray]}]}}

@pytest.mark.windows
def test_a_weigh_in_is_kept_against_a_reading_asked_for_anew_or_not_at_all(state, monkeypatch):
    """A weigh-in was set against the reading of the card it came from, however old. C3 read 105 g on the card; the
    print ran; the spool, weighed at 20 g, was kept as one that reads 105 g with 20 g on it, and every tray of the
    printer was counted 85 g lower from then on. The printer is asked again when a weigh-in is saved."""
    now = [c3_reads(105)]
    running(monkeypatch, None); monkeypatch.setattr(fg, 'read_status', lambda *a, **k: now[0] if isinstance(now[0], dict) else (_ for _ in ()).throw(now[0]))
    res = fg.evaluate(BLUE, CFG, 'Benchy'); tray = res['weigh']
    assert (res['rows'][0]['status'], tray['label'], tray['reading']) == ('TIGHT', 'C3', 105)
    kept = lambda: gn.load().get('SERIAL', {}).get('C3', {}).get('weighs', [])
    def saved(status, grams=20.0):
        """Save in the weigh-in window, with the printer as it is by then: (the card's headline, what it says)."""
        now[0] = status; guard = guard_with(); again = []
        monkeypatch.setattr(guard, 'recheck', lambda res, beside=None: again.append(res['title']))
        guard.act('weigh', res, (tray, grams)); guard.todo.get(timeout=5)()
        assert bool(again) != bool(guard.card.shown) and list(getattr(guard.card, 'buttons', {'Check again': 1})) == ['Check again']   # a new check, or a card that offers one
        return guard.card.shown[-1][1:] + tuple(guard.card.said) if guard.card.shown else again
    after = ': weigh-in not saved. Check again, then weigh from the new card.'
    assert saved(c3_reads(20)) == ('Weigh-in not saved', 'C3 read 105 g on that card and reads 20 g now' + after) and kept() == []
    assert saved(c3_reads(108))[1] == 'C3 read 105 g on that card and reads 108 g now' + after and kept() == []
    assert saved(c3_reads(105, state='RUNNING'))[1] == "The Bambu Lab H2D is printing, and C3's reading is moving" + after
    assert saved(c3_reads(105, tag='B' * 32))[1] == 'C3 no longer holds the spool that card showed' + after
    assert saved(c3_reads(105, colour='C12E1F'))[1] == 'C3 no longer holds the spool that card showed' + after
    assert saved(fg.PrinterError('printer did not report its status'))[1] == 'The printer could not be asked what C3 reads now (printer did not report its status)' + after
    assert kept() == [] and 'C3: weigh-in not saved: C3 read 105 g on that card and reads 20 g now' in (state / 'guard.log').read_text()
    with monkeypatch.context() as m:
        m.setattr(gn, 'weigh', lambda *a, **k: (_ for _ in ()).throw(fg.StateBusy('learned.json could not be written: another program holds it')))
        assert saved(c3_reads(105))[1] == 'The weigh-in of C3 could not be written (StateBusy)' + after and kept() == []
    assert saved(c3_reads(104), 86.0) == ['Benchy']                                          # within 2 g, the same spool, no job: kept, against what it reads now
    [w] = kept(); assert (w['reading'], w['weight'], w['spool']['id'], w['spool']['colour']) == (104, 86.0, 'A' * 32, '#0A2989')

@pytest.mark.windows
def test_the_amount_window_opens_on_the_screen_its_card_is_on(monkeypatch):
    """The window was held to x and y of 0 or more: with Bambu Studio on a screen left of the primary one, or above it,
    it opened at the edge of the primary screen, a screen away from the card that was clicked. It is held to the work
    area of the monitor the card is on, 48 pixels above the card's corner at 100 %."""
    import guard_ui, types
    asked = []
    def placed(spot, area, scale=1.0, owner=77):
        monkeypatch.setattr(guard_ui, 'work_area', lambda hwnd: (asked.append(hwnd), types.SimpleNamespace(left=area[0], top=area[1], right=area[2], bottom=area[3]))[1])
        made = tk_stand_in(monkeypatch); monkeypatch.setattr(sys.modules['tkinter'].Toplevel, 'winfo_fpixels', lambda self, what: 96.0 * scale, raising=False)
        card = guard_ui.Card(None); card.t, card.spot, card.owner = guard_ui.THEMES['dark'], spot, owner
        card.ask_amount(None, EXT)
        return made[0].k['geometry']
    assert placed((1160, 440), (0, 0, 1920, 1040)) == '+1160+292' and asked == [77]           # on the primary screen: above the card, the monitor asked for by the card's owner
    assert placed((-760, 440), (-1920, 0, 0, 1040)) == '+-760+292'                           # Bambu Studio on a screen left of the primary one
    assert placed((1160, -640), (0, -1080, 1920, -40)) == '+1160+-788'                       # and on one above it
    assert placed((1800, 60), (0, 0, 1920, 1040)) == '+1620+0'                               # held inside that screen: its right edge, its top
    assert placed((1160, 440), (0, 0, 1920, 1040), scale=1.5) == '+1160+268'                 # the gap grows with the screen's scaling: 72 pixels at 150 %
    del asked[:]; assert placed((1160, 440), (0, 0, 1920, 1040), owner=None) == '+1160+292' and asked == [1]   # a card with no window of Bambu Studio's: by a window of its own

@pytest.mark.windows
def test_asking_for_a_screens_work_area_makes_no_new_type_each_time():
    """work_area defined its MONITORINFO structure inside the function: a class for every call, and ctypes keeps the
    pointer type of each for good. 6 kB a call, 2.5 calls a second while a card stands beside a dialog."""
    import ctypes, gc, guard_ui
    structures = lambda: (gc.collect(), sum(1 for c in ctypes.Structure.__subclasses__() if (c.__module__, c.__name__) == ('guard_ui', 'MONITORINFO')))[1]   # guard_ui's own: another package may have one of that name
    guard_ui.work_area(1); before = structures()
    for _ in range(20): guard_ui.work_area(1)
    assert structures() == before == 1

def card_widgets(monkeypatch, res, brief=0, theme='dark'):
    """The widgets of the card guard_ui makes for an answer, on the stand-in for tkinter, in the order they were made."""
    import guard_ui
    made = tk_stand_in(monkeypatch)
    def opened(card, pid=None, beside=None, hold=False):
        card.win, card.t, card.owner, card.beside, card.held, card.closes_after = sys.modules['tkinter'].Toplevel(), guard_ui.THEMES[theme], None, None, False, 0
        return card.win
    monkeypatch.setattr(guard_ui.Card, '_open', opened); monkeypatch.setattr(guard_ui.Card, '_place', lambda card: None)
    guard_ui.Card(None).show(res, brief)
    return made

@pytest.mark.windows
def test_a_colour_tk_cannot_take_gives_a_neutral_swatch(state, monkeypatch):
    """The square before a row took the G-code's colour text as it came. One Tk refuses (five digits from a hand-made
    project, a name it does not know) raised inside show(), after the window was made: that warning never showed."""
    import guard_ui
    running(monkeypatch, spools((BLUE_PLA, 500), (RED_PLA, 2)))
    for colours, squares in (('#0A2989;#FFFFFF;#C12E1F', ['#0A2989', '#C12E1F']), ('#12345;#FFFFFF;sunset', [guard_ui.THEMES['light']['raised']] * 2), (';;#C12E1FFF', [guard_ui.THEMES['light']['raised'], '#C12E1F'])):
        res = fg.evaluate(fg.parse_plate(gcode(colours=colours)), CFG, 'Benchy')
        assert [w.k['bg'] for w in card_widgets(monkeypatch, res, theme='light') if type(w).__name__ == 'Frame' and w.k.get('width') == 18] == squares, colours

@pytest.mark.windows
def test_the_one_line_cards_text_wraps_like_the_others(state, monkeypatch):
    """With a print running the line under "Enough filament for this print" is some 190 characters, about 1100 pixels at
    100 %: wider than a small Bambu Studio window, so its left part was off the screen."""
    running(monkeypatch, dict(spools((BLUE_PLA, 500), (RED_PLA, 500)), gcode_state='RUNNING', subtask_name='Benchy'))
    res = fg.evaluate(fg.parse_plate(gcode()), CFG, 'Benchy')
    text = next(w for w in card_widgets(monkeypatch, res, brief=8) if type(w).__name__ == 'Label' and 'against the spools' in w.k['text'])
    assert text.k['text'].endswith('which is printing a job the guard could not count') and text.k['wraplength'] == 520

@pytest.mark.windows
def test_the_footnote_says_whose_the_amounts_are_for_the_rows_shown(state, monkeypatch):
    """"The amounts are the AMS's estimate" stood under every table: also under an amount entered by hand, and for a
    printer that has no AMS."""
    import guard_ledger as gl
    black = {'tray_type': 'PETG', 'tray_color': '101010FF', 'remain': -1}                    # the external spool: PETG, no reading
    petg, both = gcode(weights='10', used='2', colours='#0A2989;#101010;#C12E1F'), gcode(weights='9.99,10', used='1,2', colours='#0A2989;#101010;#C12E1F')
    def footnote(status, text):
        running(monkeypatch, status); res = fg.evaluate(fg.parse_plate(text), CFG, 'Benchy')
        return card_shown(monkeypatch, res)[0][-1]
    start = 'Spools as the Bambu Lab H2D reports them now'
    assert footnote({'gcode_state': 'IDLE', 'ams': {'ams': []}, 'vt_tray': black}, petg) == start + '.'                                                # no amount at all: nothing to say of amounts
    gl.enter('SERIAL', {'label': 'Ext', 'type': 'PETG', 'colour': '#101010'}, 400)
    assert footnote({'gcode_state': 'IDLE', 'ams': {'ams': []}, 'vt_tray': black}, petg) == start + '; the amounts are the ones entered by hand, less the prints since.'
    assert footnote(dict(spools((BLUE_PLA, 500)), vt_tray=black), both) == start + "; the amounts are the AMS's estimate, but for Ext: entered by hand, less the prints since."
    assert footnote(spools((BLUE_PLA, 500), (RED_PLA, 500)), gcode()) == start + "; the amounts are the AMS's estimate."            # as before, where it is true
    assert footnote({'gcode_state': 'IDLE', 'ams': {'ams': []}, 'vt_tray': dict(black, remain_g=300)}, petg) == start + "; the amounts are the printer's estimate."   # a reading from no AMS

@pytest.mark.windows
def test_the_amount_windows_buttons_can_be_reached_and_pressed_from_the_keyboard(state, monkeypatch):
    """Tab moved between the two fields only: "Forget this amount" had no way to it without the mouse. The window's
    buttons take the focus, and Return presses the one that has it. The card's own buttons still never do."""
    tray = dict(EXT, grams=300.0, entered=1e6); w = amount_asked(monkeypatch, tray)
    save, forget = [x for x in w.made if type(x).__name__ == 'Button']
    assert (save.k['text'], forget.k['text'], save.k['takefocus'], forget.k['takefocus']) == ('Save', 'Forget this amount', 1, 1)
    forget.keys['<Return>'](None); assert w.acts == [('amount', (tray, None))] and w.closed()
    running(monkeypatch, spools((BLUE_PLA, 500), (RED_PLA, 2))); res = fg.evaluate(fg.parse_plate(gcode()), CFG, 'Benchy')
    assert {x.k['takefocus'] for x in card_widgets(monkeypatch, res) if type(x).__name__ == 'Button'} == {0}

@pytest.mark.windows
def test_one_amount_window_at_a_time_and_it_goes_with_its_card(state, monkeypatch):
    """The amount window belonged to nothing: every click on "Amount in Ext…" opened another, and each stayed topmost
    over every program after the card and Bambu Studio had gone."""
    import guard_ui
    monkeypatch.setattr(guard_ui, 'theme', lambda: guard_ui.THEMES['dark']); monkeypatch.setattr(guard_ui, 'studio_window', lambda pid=None: None)
    monkeypatch.setattr(guard_ui.Card, '_place', lambda card: None)
    card = guard_ui.Card(None); w = amount_asked(monkeypatch, EXT, card=card); window, made = w.made[0], len(w.made)
    w.scale.set('640'); card.ask_amount(None, EXT)                                           # the button again: the window that is open, brought forward
    assert len(w.made) == made and window.k.get('lifted') == 1 and w.scale.get() == '640' and not w.closed()
    card.close(); assert w.closed()                                                          # the card closed: its window goes with it
    w = amount_asked(monkeypatch, EXT, card=card); assert not w.closed()                     # and a new one can be opened
    card.note('Self-test: the guard works', []); assert w.closed()                           # another card in its place: the same
    w = amount_asked(monkeypatch, EXT, card=card); w.scale.set('640'); w.save()              # saved and closed by itself: the next click opens one
    again = amount_asked(monkeypatch, EXT, card=card); assert w.closed() and not again.closed() and again.made[0] is not w.made[0]

@pytest.mark.windows
def test_an_ampersand_in_a_projects_title_shows_as_one_in_the_icons_menu(state, monkeypatch):
    """A Windows menu takes "&" as "underline the next letter": "Black & White" showed as "Black _White"."""
    guard = guard_with(); menu = icon_menu(monkeypatch, guard); guard.deliver(answer(1, 'OK', 'Black & White'))
    line = next(i for i in menu.items if callable(i.text))
    assert line.text(line) == 'Enough filament for this print · Black && White, plate 1' and guard.last_text() == 'Enough filament for this print · Black & White, plate 1'

@pytest.mark.windows
def test_buttons_have_a_border_from_the_theme(state, monkeypatch):
    """In the light theme a button's background is 1.11 to 1 against the card's surface (1.21 to 1 in the dark one), and
    buttons had no border: they read as plain text. One pixel of the theme's own border colour, in both themes: a
    frame of that colour around the button, because Windows draws a flat button no highlight ring (the release
    workflow looks at the real picture)."""
    import guard_ui
    running(monkeypatch, spools((BLUE_PLA, 500), (RED_PLA, 2))); res = fg.evaluate(fg.parse_plate(gcode()), CFG, 'Benchy')
    edged = lambda w: (type(w.parent).__name__, w.parent.k.get('bg'), w.k.get('highlightthickness'))
    for theme in ('light', 'dark'):
        buttons = [w for w in card_widgets(monkeypatch, res, theme=theme) if type(w).__name__ == 'Button']
        assert [w.k['text'] for w in buttons] == ['Check again', 'Ignore for this project']
        assert {edged(w) for w in buttons} == {('Frame', guard_ui.THEMES[theme]['border'], 0)}
    assert {edged(w) for w in amount_asked(monkeypatch, EXT).made if type(w).__name__ == 'Button'} == {('Frame', guard_ui.THEMES['dark']['border'], 0)}

@pytest.mark.windows
def test_the_icon_is_stopped_whatever_ends_the_cards_loop(state):
    """The icon runs on a thread of pystray's that is no daemon: when the Tk loop ended by an exception, the icon was not
    stopped, the process stayed, and a dead guard looked alive (its icon there, "status" saying it runs)."""
    def broken(): raise RuntimeError('the Tk loop has ended')
    guard, stopped = guard_with(), []
    guard.root = type('Root', (), {'after': lambda self, ms, then: None, 'mainloop': lambda self: broken()})()
    guard.tray = type('Icon', (), {'stop': lambda self: stopped.append('icon')})(); guard.jobs['SERIAL'] = type('Job', (), {'stopped': threading.Event()})()
    with pytest.raises(RuntimeError): guard.loop()
    assert stopped == ['icon'] and guard.jobs['SERIAL'].stopped.is_set()

def test_a_message_is_one_line_of_the_log_whatever_it_holds(state):
    """A job's or a project's name comes from the printer or from a file and can hold a line break. Logged as it came, it
    made a line of its own, with any time and text in it: in a log users are asked to paste into public reports."""
    fg.log('job "Benchy\n2026-11-24 10:00:00 watching for sliced plates (forged)\r\nnext\x1b[2J end\x85" counted'); fg.log('the next line')
    first, second = (state / 'guard.log').read_text(encoding='utf-8').splitlines()
    assert first.endswith(' job "Benchy 2026-11-24 10:00:00 watching for sliced plates (forged)  next [2J end " counted') and second.endswith(' the next line')

@pytest.mark.windows
def test_the_packaged_guard_starts_tk_without_another_programs_tcl_library(monkeypatch):
    """TCL_LIBRARY or TK_LIBRARY in the environment, set by another program for its own Tcl, sends the packaged guard's
    Tcl to a library that is not its own: its first window never comes, and under pythonw nothing says why. A run from
    source keeps them: there the Python and its Tcl are the user's."""
    import guard_ui
    tk_stand_in(monkeypatch); seen = []
    monkeypatch.setattr(guard_ui.ctypes.windll.shcore, 'SetProcessDpiAwareness', lambda how: None, raising=False)   # not for the process that runs the tests
    sys.modules['tkinter'].Tk = type('Tk', (), {'__init__': lambda self: seen.append({k: fg.os.environ.get(k) for k in ('TCL_LIBRARY', 'TK_LIBRARY')}), 'withdraw': lambda self: None})
    for here, kept in (('app', None), ('bambu-filament-guard', 'elsewhere')):
        monkeypatch.setenv('TCL_LIBRARY', 'elsewhere'); monkeypatch.setenv('TK_LIBRARY', 'elsewhere')
        monkeypatch.setattr(fg, 'HERE', fg.os.path.join('C:', 'BambuFilamentGuard', here)); guard_ui.make_root()
        assert seen[-1] == {'TCL_LIBRARY': kept, 'TK_LIBRARY': kept}, here

PACKAGED, FROM_SOURCE = fg.os.path.join('C:', 'BambuFilamentGuard', 'app'), fg.os.path.join('C:', 'src', 'bambu-filament-guard')   # where the scripts are: build.py puts them in app/

def test_a_missing_package_is_told_as_what_can_be_done_about_it(state, monkeypatch):
    """"pip install paho-mqtt" was the advice on the packaged guard's card too. That guard has no pip, and its Python is
    its own: there the folder is incomplete, and unzipping it again is what helps."""
    monkeypatch.setitem(sys.modules, 'paho', None)                                           # no paho-mqtt to import
    for here, said in ((FROM_SOURCE, 'the Python package paho-mqtt is missing (pip install paho-mqtt)'),
                       (PACKAGED, "the guard's folder is incomplete (paho-mqtt is missing): unzip the guard again")):
        monkeypatch.setattr(fg, 'HERE', here)
        with pytest.raises(fg.PrinterError) as refused: fg.printer_link('192.0.2.1', 'SERIAL', 'code', 'guard', lambda report: None)
        assert str(refused.value) == said

@pytest.mark.windows
def test_a_missing_pystray_is_logged_as_what_can_be_done_about_it(state, monkeypatch):
    import guard_ui
    monkeypatch.setitem(sys.modules, 'pystray', None)
    for here, said in ((FROM_SOURCE, 'no icon in the notification area: the Python package pystray is missing (pip install pystray)'),
                       (PACKAGED, "no icon in the notification area: the guard's folder is incomplete (pystray is missing): unzip the guard again")):
        monkeypatch.setattr(fg, 'HERE', here); assert guard_ui.Tray(guard_with()).icon is None
        assert (state / 'guard.log').read_text().splitlines()[-1].endswith(' ' + said)

@pytest.mark.windows
def test_at_send_print_job_a_plate_that_cannot_be_read_is_one_that_was_not_checked(state, tmp_path, monkeypatch):
    """A plate whose G-code could not be read, or was still being written, when "Send print job" opened was a line in the
    log: with the other plates fine, nothing showed beside the dialog, which there means every sliced plate is fine."""
    import guard_ui
    monkeypatch.setattr(guard_ui, 'visible', lambda hwnd: True); running(monkeypatch, spools((BLUE_PLA, 500), (RED_PLA, 500)))
    _, odd, writing = project_of(tmp_path, monkeypatch, [FINE, gcode(used=None), gcode(complete=False)])   # plate 2: totals that cannot be placed; plate 3: not all there yet
    guard = guard_with(); at_send(guard); res = guard.card.res
    assert guard.card.shown == [('show', 0)] and guard.card.where == 4321 and guard.rank(res) == (1, 0)
    assert (res['plate_no'], res['title'], res['error']) == (3, 'Farm', 'Bambu Studio is still writing the G-code of plate 3')   # the newest of the two
    writing.unlink(); guard = guard_with(); at_send(guard); res = guard.card.res
    assert (res['plate_no'], res['error']) == (2, 'the G-code of plate 2 cannot be read (2 filament totals for 3 filaments and no "; filament:" line to place them)')
    log = (state / 'guard.log').read_text()
    assert log.count('at "Send print job": Enough filament for this print: Farm') == 2 and 'at "Send print job": Filament could not be checked: Farm |   the G-code of plate 2 cannot be read' in log
    odd.write_text(gcode(weights=TOO_MUCH)); again = []                                      # sliced again, and "Check again" on that card: the file is read anew
    monkeypatch.setattr(guard, 'deliver', lambda new, **how: again.append(new)); guard.recheck(res); guard.todo.get(timeout=5)()
    assert (again[0]['plate_no'], again[0]['error'], again[0]['worst']) == (2, None, 'SHORT')
    project_of(tmp_path, monkeypatch, [gcode(used=None), TOO_MUCH]); guard = guard_with(); at_send(guard)   # a warning on another plate comes before it
    assert (guard.card.res['plate_no'], guard.card.res['worst'], guard.card.res['error']) == (2, 'SHORT', None)

@pytest.mark.windows
def test_a_warning_held_back_shows_when_the_standing_one_has_gone(state):
    """While a warning's card stood, another plate's warning that was no worse got no card, and none later: when the
    standing plate was sliced again and fine, the icon went green over a plate that fell short. The latest answer of
    each such plate is kept, and the worst of them shows when the standing card goes: by its own plate turning fine,
    or closed by the user."""
    shown, icon, guard = warned(answer(1, 'SHORT'), answer(2, 'OK'), answer(3, 'TIGHT'), answer(4, 'SHORT'), answer(3, 'SPLIT'), answer(5, 'TIGHT'), answer(5, 'OK'))
    assert shown == [] and icon == [] and guard.card.res['plate_no'] == 1                    # as before: the card stands, the icon keeps its colour
    guard.deliver(answer(1, 'OK'))                                                           # plate 1 sliced again, and fine: not its line, but the worst that waited
    assert guard.card.shown[-1] == ('show', 0) and (guard.card.res['plate_no'], guard.card.res['worst']) == (4, 'SHORT')
    assert guard.tray.was[-1] == ('danger', 'Not enough filament for this print · Bracket, plate 4') and guard.last is guard.card.res
    guard.card.close(); guard.show_held()                                                    # closed by the user: the next, in its latest answer
    assert guard.card.shown[-1] == ('show', 0) and (guard.card.res['plate_no'], guard.card.res['worst']) == (3, 'SPLIT')
    assert guard.tray.was[-1] == ('warn', 'No single spool holds enough filament · Bracket, plate 3')
    guard.card.close(); seen = len(guard.card.shown); guard.show_held()                      # plate 5 was fine in the end, plate 2 always: nothing waits
    assert guard.card.shown[seen:] == [] and guard.held == {}

@pytest.mark.windows
def test_the_icon_is_not_green_while_a_warning_waits_to_be_shown(state, tmp_path, monkeypatch):
    """The icon is green only when no plate that was checked has a warning that was neither shown nor ignored: an answer
    asked for meanwhile does not turn it green, the icon's click checks the plate it names, and the check for "Send
    print job" brings what waits up to date."""
    import guard_ui
    shown, icon, guard = warned(answer(1, 'SHORT'), answer(2, 'TIGHT'))
    guard.deliver(answer(7, 'OK'), full=True)                                                # "Check newest slice now", say: a fine plate, shown because it was asked for
    assert guard.card.res['plate_no'] == 7 and guard.tray.was[-1] == ('warn', 'Filament is tight for this print · Bracket, plate 2') == ('warn', guard.last_text())
    checked = []; monkeypatch.setattr(guard, 'again', lambda res: checked.append(res['plate_no']) or res); monkeypatch.setattr(fg, 'printer_for', lambda plate: None)
    waiting = guard.held[(1, 'Bracket', 2)]; waiting['plate'] = {'filaments': []}
    with monkeypatch.context() as m:
        m.setattr(fg, 'report_text', lambda res: 'the answer')                               # the answers here are cut down to what a card reads
        guard.check_last(); guard.todo.get(timeout=5)()                                      # the click on the icon: the plate the icon names
    assert checked == [2] and guard.card.res is waiting and guard.held == {}
    monkeypatch.setattr(guard_ui, 'visible', lambda hwnd: True); running(monkeypatch, spools((BLUE_PLA, 500), (RED_PLA, 500)))
    project_of(tmp_path, monkeypatch, [TOO_MUCH, FINE, FINE])
    guard = guard_with(); guard.tray = Icon(); guard.deliver(answer(9, 'SHORT', 'Other'))    # another project's warning stands
    guard.held[(1, 'Farm', 2)], guard.held[(1, 'Farm', 1)] = answer(2, 'TIGHT', 'Farm', ignored=False), answer(1, 'TIGHT', 'Farm', ignored=False)
    at_send(guard)                                                                           # every plate of Farm checked again: plate 2 is fine now, plate 1 short
    assert (guard.card.res['plate_no'], guard.card.res['worst'], guard.card.where) == (1, 'SHORT', 4321) and guard.held == {}

@pytest.mark.windows
def test_a_setting_that_cannot_be_kept_is_in_force_and_said_not_to_be_kept(state, monkeypatch):
    """With config.json not writable (the guard's folder read-only, the file held elsewhere) a setting changed in the
    icon's menu raised out of the menu: a line in the log, nothing on screen, and no word that the next start forgets it."""
    def refused(cfg, key=None): raise PermissionError(13, 'Permission denied', fg.CONFIG)
    monkeypatch.setattr(fg, 'save_config', refused)
    guard = guard_with(); menu = icon_menu(monkeypatch, guard); settings = next(i.action for i in menu.items if i.text == 'Settings')
    for text in ("Warn when a spool's amount is unknown", 'One line when there is enough'):
        item = next(i for i in settings.items if i.text == text); item.action(None, item); guard.todo.get_nowait()()   # does not raise
        assert item.how['checked'](item) == (text != 'One line when there is enough')        # in force, and the menu shows it
    assert 'UNKNOWN' in guard.cfg['popup_on'] and guard.cfg['ok_seconds'] == 0
    assert guard.card.shown == [('note', 'This setting was not kept')] * 2
    assert guard.card.said == ['config.json could not be written (PermissionError): is the folder the guard runs from read-only?',
                               'The setting is in force until the guard stops; at its next start it is as config.json has it.']
    assert 'setting ok_seconds = 0: not kept, config.json could not be written (PermissionError): in force until the guard stops' in (state / 'guard.log').read_text()

@pytest.mark.windows
def test_the_icon_goes_grey_when_slices_cannot_be_read(state, tmp_path, monkeypatch):
    """"Filament is not being checked" was a card, once for a Bambu Studio version; the icon kept the colour and the
    tooltip of the last answer, green as may be, while no slice was checked. It goes grey and says so, until a slice
    is read again."""
    running(monkeypatch, spools((BLUE_PLA, 500), (RED_PLA, 500)))
    class Watcher:
        problems = []
        def new_plates(self): guard.stopping.set(); self.problems.append((str(tmp_path / '.1.0.gcode'), 'it carries no filament totals')); return []
    monkeypatch.setattr(fg, 'Watcher', Watcher)
    guard = guard_with(); guard.tray = Icon(); guard.deliver(answer(1, 'OK')); guard.card.close()
    assert guard.tray.was == [('ok', 'Enough filament for this print · Bracket, plate 1')]
    guard.watch_slices(); run_queued(guard)
    assert guard.card.shown[-1] == ('note', 'Filament is not being checked') and guard.tray.was[-1] == ('muted', 'Filament is not being checked')
    assert guard.last_text() == 'Filament is not being checked'
    guard.card.close(); notes = guard.card.shown.count(('note', 'Filament is not being checked'))
    f = slice_file(tmp_path, gcode()); guard.take_slice(str(f), fg.parse_plate(f.read_text())); run_queued(guard)   # a slice that reads: checked again
    assert guard.tray.was[-1][0] == 'ok' and guard.last_text().startswith('Enough filament for this print') and guard.card.shown.count(('note', 'Filament is not being checked')) == notes

@pytest.mark.windows
def test_a_stop_asked_for_during_slice_all_does_not_wait_for_every_plate(state, monkeypatch):
    """The stop flag was looked at between rounds only: with "slice all" under way, each plate a check of seconds, an
    install, an update or a stop gave up waiting after 10 s while the old guard went on to the last plate."""
    guard, taken = guard_with(), []
    class Watcher:
        problems = []
        def new_plates(self): return [('plate 1', {}), ('plate 2', {}), ('plate 3', {})]
    def take(path, plate):
        taken.append(path); (state / 'stop').write_text('')                                  # "stop", asked for while the first plate is checked
    monkeypatch.setattr(fg, 'Watcher', Watcher); guard.take_slice = take
    guard.watch_slices()
    assert taken == ['plate 1'] and guard.stopping.is_set()

@pytest.mark.windows
def test_another_trays_amount_window_takes_the_open_ones_place(state, monkeypatch):
    """One amount window at a time: the button of the tray it is for brings it forward, but the button of another tray,
    or "Weigh", asks for another window, and gets it in the open one's place."""
    import guard_ui
    card = guard_ui.Card(None); w = amount_asked(monkeypatch, EXT, card=card); first, made = w.made[0], len(w.made)
    other = dict(EXT, label='A3', reading=300.0)
    card.ask_amount(None, other)
    assert first.k.get('destroyed') and not first.k.get('lifted') and len(w.made) > made and 'A3' in w.made[made + 1].k['text']
    second = w.made[made]; card.ask_amount(None, other, weigh=True)                           # the same tray, to weigh: another window again
    assert second.k.get('destroyed') and card.asking is not second and not card.asking.k.get('destroyed')

@pytest.mark.windows
def test_a_card_that_is_no_answer_does_not_end_a_standing_warning(state, monkeypatch):
    """There is one card at a time, and a card of the guard's own took a warning's place for good: "Filament is not being
    checked" for a slice it could not read, "This setting was not kept", the self-test's lines. The warning waits
    behind such a card, and shows again when it has gone."""
    short = answer(1, 'SHORT')
    for say in (lambda guard: guard.not_checking([(False, 'Bambu Studio 02.10 wrote a slice the guard cannot read.')]),
                lambda guard: (monkeypatch.setattr(fg, 'save_config', lambda cfg, key=None: (_ for _ in ()).throw(PermissionError(13, 'Permission denied'))), guard.set_option('ok_seconds', 8)),
                lambda guard: guard.forget_learned()):
        shown, icon, guard = warned(short); say(guard)
        assert guard.card.shown[-1][0] == 'note' and guard.held == {(1, 'Bracket', 1): short}
        guard.show_held(); assert guard.card.shown[-1][0] == 'note'                          # while that card is open: nothing moves
        guard.card.close(); guard.show_held()
        assert guard.card.shown[-1] == ('show', 0) and guard.card.res is short and guard.held == {} and guard.standing_warning()
    shown, icon, guard = warned(short); guard.forget_learned(); guard.deliver(answer(1, 'OK'))   # its plate fine meanwhile: nothing waits any more
    assert guard.held == {} and guard.card.shown[-1] == ('show', CFG['ok_seconds'])

BAMBU = 'urn:bambulab-com:device:3dprinter'

def an_internet_address():
    """An IPv4 address of the internet at large, made from the documentation address by arithmetic: none is written here."""
    import ipaddress
    return next(str(a) for a in (ipaddress.ip_address('192.0.2.50') + n * 2 ** 24 for n in range(1, 9)) if a.is_global)

def test_an_announcement_names_the_printers_address_only_when_it_is_a_local_one(monkeypatch):
    """Announcements are not authenticated: whoever sends one with the printer's serial names the address the access
    code is then sent to. Its Location was taken as it came: a host name, an address of the internet, this computer
    itself. Only an IPv4 address a printer on a local network can have is taken; anything else is dropped."""
    for location in ('printer.example.net', an_internet_address(), 'localhost', '127.0.0.1', '0.0.0.0', '239.255.255.250', '255.255.255.255',
                     '192.0.2.50:8883', 'http://192.0.2.50/', '::1', 'fe80::1'):
        assert announcements(monkeypatch, ['SERIAL'], notice('SERIAL', location, BAMBU)) == {}, location
    for location in ('192.0.2.50', '192.168.1.50', '10.0.0.7', '172.20.3.4', '169.254.10.2'):
        assert announcements(monkeypatch, ['SERIAL'], notice('SERIAL', location, BAMBU)) == {'SERIAL': location}
    assert announcements(monkeypatch, ['SERIAL'], notice('SERIAL', 'printer.example.net', BAMBU), notice('SERIAL', '192.0.2.50', BAMBU)) == {'SERIAL': '192.0.2.50'}   # the forged one first: the real one is still heard
    assert announcements(monkeypatch, ['SERIAL'], notice('ANOTHER', '192.0.2.51', BAMBU)) == {}                  # a printer that was not asked for

def test_the_address_kept_for_a_printer_is_the_one_asked_for_and_a_local_one(state, monkeypatch):
    """printer_ip kept every announcement it heard in state.json, and used whatever address was kept there, for good: a
    forged one outlasted the guard's restarts. It keeps the address of the serial it asked for alone, and an address
    kept by an earlier version that is no local IPv4 address is not used: the printer is looked for again."""
    monkeypatch.setattr(fg, 'bs_conf', lambda: {'access_code': {'SERIAL': 'code', 'SERIAL2': 'code'}}); heard = {'SERIAL': '192.0.2.50', 'SERIAL2': '192.0.2.51'}
    monkeypatch.setattr(fg, 'discover', lambda want, seconds=8.0: dict(heard))             # whatever was heard, of whichever printer
    assert fg.printer_ip('SERIAL', CFG) == '192.0.2.50' and fg.load_state()['ips'] == {'SERIAL': '192.0.2.50'}
    fg.update_state(lambda st: st.update(ips={'SERIAL': 'printer.example.net'}, discovered_at=0)); heard.clear()
    assert fg.printer_ip('SERIAL', CFG) is None and fg.load_state()['ips'] == {}                                   # kept by 1.4.0 from a forged announcement: not used, and gone
    fg.update_state(lambda st: st.update(ips={'SERIAL': an_internet_address()}, discovered_at=0)); heard['SERIAL'] = '192.0.2.50'
    assert fg.printer_ip('SERIAL', CFG) == '192.0.2.50'
    assert fg.printer_ip('SERIAL', dict(CFG, printers={'SERIAL': {'ip': 'printer.example.net'}})) == 'printer.example.net'   # named in config.json: the user's own word, taken as it is
    assert [fg.local_address(a) for a in (3232235826, None, ['192.0.2.50'], '')] == [None] * 4                    # and nothing but text is an address

def test_what_is_kept_for_no_printer_of_bambu_studio_goes_at_the_next_look_for_an_address(state, monkeypatch):
    """Every USN that was heard was kept in state.json, which is never pruned, read whole every 3 seconds and rewritten
    at every change: a burst of junk announcements made it hundreds of MB, for good. Only the serials Bambu Studio
    knows are kept now, and a file bloated before heals at the next look for a printer's address, also where that
    address is at hand and nothing is listened for. A USN or an address longer than one can be is refused."""
    monkeypatch.setattr(fg, 'bs_conf', lambda: {'access_code': {'SERIAL': 'code', 'SERIAL2': 'code'}}); listened = []
    monkeypatch.setattr(fg, 'discover', lambda want, seconds=8.0: listened.append(want) or {})
    junk = {f'{n:07d}' + 'A' * 1900: '192.0.2.9' for n in range(300)}
    fg.update_state(lambda st: st.update(ips=dict(junk, SERIAL='192.0.2.50', SERIAL2='192.0.2.51'), discovered_at=fg.time.time(), spools={'SERIAL': {'A1': {'grams': 400.0}}}))
    assert (state / 'state.json').stat().st_size > 500_000
    assert fg.printer_ip('SERIAL', CFG) == '192.0.2.50' and listened == []
    st = fg.load_state()
    assert st['ips'] == {'SERIAL': '192.0.2.50', 'SERIAL2': '192.0.2.51'} and st['spools'] == {'SERIAL': {'A1': {'grams': 400.0}}} and (state / 'state.json').stat().st_size < 1000
    fg.update_state(lambda st: st.update(ips=['not', 'a', 'dict']))                                               # and what is no such list at all
    assert fg.printer_ip('SERIAL', CFG, refresh=True) is None and fg.load_state()['ips'] == {}
    assert announcements(monkeypatch, ['SERIAL'], notice('SERIAL' + 'A' * 1900, '192.0.2.50', BAMBU), notice('SERIAL', '192.0.2.50' + '0' * 1900, BAMBU)) == {}

def test_the_startup_shortcut_is_made_and_read_by_windows_own_powershell(tmp_path, monkeypatch):
    """`powershell` was started by its bare name. Windows looks for that in the folder of the running Python and in the
    current folder before System32: a powershell.exe lying where `install` is run from was what ran, at every install.
    It is started by its full path under %SystemRoot%, and by its bare name only where that file is not."""
    ran = []
    monkeypatch.setattr(fg.subprocess, 'run', lambda cmd, **how: ran.append(cmd[0]) or type('Done', (), {'stdout': ''})())
    monkeypatch.setattr(fg.subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    lnk = tmp_path / 'Bambu Filament Guard.lnk'; lnk.write_text(''); monkeypatch.setattr(fg, 'STARTUP_LNK', str(lnk))
    monkeypatch.setenv('SystemRoot', str(tmp_path / 'Windows'))
    fg.make_shortcut(); fg.shortcut_folder()
    assert ran == ['powershell', 'powershell']                                             # no such file here: the name, as before
    own = tmp_path / 'Windows' / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'; own.parent.mkdir(parents=True); own.write_text('')
    del ran[:]; fg.make_shortcut(); fg.shortcut_folder()
    assert ran == [str(own), str(own)]

NO_AMS = {'gcode_state': 'IDLE', 'vt_tray': {'id': '254', 'tray_type': 'PLA', 'tray_color': '0A2989FF', 'remain': 0, 'tray_weight': '0'}}

def entered(monkeypatch, status):
    """The printer reports `status`; 640 g were entered for its AMS tray A1 and 400 g for its external spool."""
    import guard_ledger as gl
    running(monkeypatch, status)
    gl.enter('SERIAL', {'label': 'A1', 'type': 'PLA', 'colour': '#0A2989'}, 640); gl.enter('SERIAL', {'label': 'Ext', 'type': 'PLA', 'colour': '#0A2989'}, 400)
    return lambda: {label: e['grams'] for label, e in fg.load_state()['spools']['SERIAL'].items()}

def test_a_printer_without_an_ams_is_judged_on_its_external_spool(state, monkeypatch, capsys):
    """A status is judged in two cases: it has the list of its AMS units, or it has no "ams" at all and names an external
    spool, which is a printer with no AMS: such a one was answered "did not report its spools" at every check. With
    "ams" there and no list in it, it is the report in pieces, and with neither there is nothing to judge: not judged.
    No amount entered for an AMS tray ends on a status without the list."""
    kept = entered(monkeypatch, NO_AMS); res = fg.evaluate(BLUE, CFG, 'Benchy')
    assert res['error'] is None and res['worst'] == 'OK' and [(t['label'], t['grams']) for t in res['rows'][0]['trays']] == [('Ext', 400.0)]
    assert kept() == {'A1': 640.0, 'Ext': 400.0}                                             # A1 is not in this status: its amount is left alone
    assert fg.main(['trays']) == 0 and 'Bambu Lab H2D (printer), IDLE\n  Ext   PLA ' in capsys.readouterr().out and kept() == {'A1': 640.0, 'Ext': 400.0}
    slots = {'gcode_state': 'IDLE', 'vir_slot': [dict(NO_AMS['vt_tray'])]}                   # the external holders as newer firmware names them
    assert fg.can_judge(slots) and fg.can_judge(ENOUGH) and fg.evaluate(BLUE, CFG, 'Benchy', seen={'SERIAL': slots})['worst'] == 'OK'
    for status in (dict(NO_AMS, ams={'tray_now': '255', 'version': 4}), {'gcode_state': 'IDLE'}, {'gcode_state': 'IDLE', 'vir_slot': []}, dict(NO_AMS, ams=None)):
        kept = entered(monkeypatch, status)
        assert not fg.can_judge(status) and fg.evaluate(BLUE, CFG, 'Benchy')['error'] == 'the Bambu Lab H2D did not report its spools'
        assert fg.main(['trays']) == 0 and 'Bambu Lab H2D: did not report its spools' in capsys.readouterr().out and kept() == {'A1': 640.0, 'Ext': 400.0}
    entered(monkeypatch, dict(NO_AMS, ams={'ams': []}))                                      # the list, with no AMS in it: judged, as before
    assert fg.evaluate(BLUE, CFG, 'Benchy')['worst'] == 'OK'

@pytest.mark.windows
def test_the_self_test_counts_the_external_spool_of_a_printer_without_an_ams(state, monkeypatch):
    """The same rule as a check: no "ams" at all and an external spool is a printer the guard can read; "ams" without its
    list is not. Neither ends an amount entered for an AMS tray."""
    import guard_ui
    monkeypatch.setattr(fg, 'newest_gcode', lambda pid=None: None); monkeypatch.setattr(guard_ui, 'studio_windows', lambda *a: {})
    kept = entered(monkeypatch, NO_AMS)
    assert (True, 'Bambu Lab H2D: 1 spool(s), 1 with a known amount') in fg.selftest(CFG) and kept() == {'A1': 640.0, 'Ext': 400.0}
    kept = entered(monkeypatch, dict(NO_AMS, ams={'tray_now': '255'}))
    assert (None, 'Bambu Lab H2D: it did not report its spools') in fg.selftest(CFG) and kept() == {'A1': 640.0, 'Ext': 400.0}

@pytest.mark.windows
def test_setup_says_at_once_that_it_installs_and_a_second_one_meanwhile_does_nothing(state, monkeypatch):
    """Install.cmd showed nothing until the install was over: up to a minute of waiting for a guard that runs to go, and
    eight seconds of listening for each printer that is off. A second double click in that time started a second
    install beside the first. A line now says at once that the install is under way, the install runs behind it, and
    the card comes as before; while it runs, a second setup finds the first one's mutex and leaves."""
    done = install_on(monkeypatch); shown = set_up(monkeypatch, shown=done)
    assert shown[:2] == [('line', 'Installing Bambu Filament Guard…'), 'shortcut read'] and 'start' in shown           # the line, before anything of the install
    assert shown[-2] == ('mutex let go', 7) and shown[-1][0] == 'Bambu Filament Guard is installed'                    # the next double click installs again
    done = install_on(monkeypatch); assert set_up(monkeypatch, mutex=None, shown=done) == []                           # one is at work: nothing is shown, nothing installed
    def no_tk(): raise RuntimeError('no display')
    import guard_ui
    done = install_on(monkeypatch); shown = set_up(monkeypatch, shown=done); del shown[:]
    monkeypatch.setattr(guard_ui, 'make_root', no_tk)
    assert fg.setup() == 0 and shown[:1] == ['shortcut read'] and 'start' in shown and shown[-1] == ('mutex let go', 7)   # no card to be had: installed all the same
    assert 'setup: its card cannot be shown (RuntimeError: no display): the install goes on without it' in (state / 'guard.log').read_text()

@pytest.mark.windows
def test_the_self_test_says_whether_it_is_this_copy_that_starts_with_windows(state, tmp_path, monkeypatch, capsys):
    """A Startup shortcut that exists read "It starts automatically", also when it starts another copy of the guard, in
    another folder: an update unzipped beside this one, or this one never installed. The self-test and "status" say
    so only of a shortcut that points into this copy's folder, and name the folder of one that points elsewhere."""
    import guard_ui
    monkeypatch.setattr(fg, 'bs_conf', lambda: {'app': {'version': '02.09.01.70'}}); monkeypatch.setattr(fg, 'newest_gcode', lambda pid=None: None)
    monkeypatch.setattr(guard_ui, 'studio_windows', lambda *a: {}); monkeypatch.setattr(fg, 'is_running', lambda: True)
    lnk, points = tmp_path / 'Bambu Filament Guard.lnk', [str(tmp_path / 'other' / 'app')]
    monkeypatch.setattr(fg, 'STARTUP_LNK', str(lnk)); monkeypatch.setattr(fg, 'shortcut_folder', lambda: points[0])
    assert fg.selftest(CFG)[-1] == (None, 'It does not start automatically') and fg.main(['status']) == 0 and '| starts automatically: no |' in capsys.readouterr().out
    lnk.write_text('')
    assert fg.selftest(CFG)[-1] == (None, f'It starts another copy, in {points[0]}')
    assert fg.main(['status']) == 0 and f'| starts automatically: another copy, in {points[0]} |' in capsys.readouterr().out
    points[0] = fg.HERE
    assert fg.selftest(CFG)[-1] == (True, 'It starts automatically') and fg.main(['status']) == 0 and '| starts automatically: yes |' in capsys.readouterr().out
    points[0] = ''                                                                           # PowerShell could not read the shortcut
    assert fg.selftest(CFG)[-1] == (None, 'A Startup shortcut is there, but where it points cannot be read')

@pytest.mark.windows
def test_the_menus_start_automatically_leaves_the_shortcut_of_another_copy_alone(state, tmp_path, monkeypatch):
    """"Start automatically" in the icon's menu removed the Startup shortcut whenever there was one: also the one of a
    newer copy installed from another folder, which then no longer started with Windows, and nothing said so. It
    removes only a shortcut that points into its own folder, and says on a card what it left alone."""
    lnk, points, made = tmp_path / 'Bambu Filament Guard.lnk', [str(tmp_path / 'new' / 'app')], []
    monkeypatch.setattr(fg, 'STARTUP_LNK', str(lnk)); monkeypatch.setattr(fg, 'shortcut_folder', lambda: points[0]); monkeypatch.setattr(fg, 'make_shortcut', lambda: made.append(1))
    guard = guard_with(); lnk.write_text(''); guard.toggle_autostart()
    assert lnk.exists() and guard.card.shown == [('note', 'The Startup shortcut was left alone')]
    assert guard.card.said == [f'It starts another copy of the guard, in {points[0]}: that copy\'s own menu, or its Uninstall.cmd, removes it.', 'To have this copy start with Windows in its place, install it: Install.cmd in its folder, or "install".']
    points[0] = ''; guard = guard_with(); guard.toggle_autostart()                           # PowerShell cannot read it: whose it is cannot be told
    assert lnk.exists() and guard.card.said == ['Where it points could not be read, so it was not removed.']
    points[0] = fg.HERE; guard = guard_with(); guard.toggle_autostart()
    assert not lnk.exists() and guard.card.shown == [] and made == []                         # this copy's own: removed, as before
    guard.toggle_autostart(); assert made == [1] and guard.card.shown == []                   # and none there: made

def test_one_printer_found_does_not_keep_another_from_being_looked_for(state, monkeypatch):
    """A printer whose address is kept is listened for again at most every ten minutes, when it does not answer there.
    That was one timer for all printers: within ten minutes of listening for any of them, a second printer that had
    a new address was not looked for, and its checks went on failing at the old one. Each printer has its own."""
    monkeypatch.setattr(fg, 'bs_conf', lambda: {'access_code': {'SERIAL': 'code', 'SERIAL2': 'code'}}); listened, now = [], [1000.0]
    monkeypatch.setattr(fg, 'discover', lambda want, seconds=8.0: listened.append(want) or {'SERIAL2': '192.0.2.61'}); monkeypatch.setattr(fg.time, 'time', lambda: now[0])
    fg.update_state(lambda st: st.update(ips={'SERIAL': '192.0.2.50', 'SERIAL2': '192.0.2.51'}, discovered_at=990.0))   # as 1.4.0 kept it: one time for all
    assert fg.printer_ip('SERIAL', CFG, refresh=True) == '192.0.2.50' and listened == [['SERIAL']]                # not heard: the address kept stands
    assert fg.printer_ip('SERIAL2', CFG, refresh=True) == '192.0.2.61' and listened == [['SERIAL'], ['SERIAL2']]  # the other is looked for all the same, and found
    now[0] += 300
    assert fg.printer_ip('SERIAL', CFG, refresh=True) == '192.0.2.50' and fg.printer_ip('SERIAL2', CFG, refresh=True) == '192.0.2.61' and len(listened) == 2   # each within its own ten minutes
    now[0] += 400
    assert fg.printer_ip('SERIAL', CFG, refresh=True) == '192.0.2.50' and listened[2:] == [['SERIAL']]
    st = fg.load_state()
    assert st['listened'] == {'SERIAL': 1700.0, 'SERIAL2': 1000.0} and 'discovered_at' not in st
