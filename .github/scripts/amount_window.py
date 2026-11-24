"""Opens the guard's amount window on this screen and saves pictures of it: python .github/scripts/amount_window.py <folder>

For the release workflow: the window is tkinter on Windows, which the tests drive only through a stand-in, so this
is where it is seen. Prints each picture's size, and the picture itself as base64 between markers, for reading from
the job's log."""
import os, sys, base64
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import guard_ui
from PIL import ImageGrab

folder = sys.argv[1]; os.makedirs(folder, exist_ok=True)
root = guard_ui.make_root()
card = guard_ui.Card(root); card.spot = (300, 500)
SHOTS = (('amount-dark', 'dark', {'label': 'Ext', 'name': '', 'type': 'PETG', 'grams': 412.0, 'entered': 1.0}, False, None, 250),
         ('amount-refused-light', 'light', {'label': 'A2', 'name': 'Generic PLA', 'type': 'PLA', 'grams': None}, False, ('200', '250'), 250),
         ('weigh-dark', 'dark', {'label': 'C3', 'name': 'Bambu PLA Basic', 'type': 'PLA', 'grams': 110.0, 'reading': 110.0}, True, ('292', None), 212.5))
for name, mode, tray, weigh, typed, tare in SHOTS:
    card.t, card.empty_spool = guard_ui.THEMES[mode], tare
    before = set(root.winfo_children())
    card.ask_amount(None, tray, weigh)
    [d] = set(root.winfo_children()) - before
    if typed:
        entries = [w for w in d.winfo_children() if w.winfo_class() == 'Entry']
        for entry, text in zip(entries, typed):
            if text is not None: entry.delete(0, 'end'); entry.insert(0, text)
        if name.endswith('refused-light'): entries[0].event_generate('<Return>')
    for _ in range(5): root.update()
    x, y, w, h = d.winfo_rootx(), d.winfo_rooty(), d.winfo_width(), d.winfo_height()
    path = os.path.join(folder, name + '.png')
    ImageGrab.grab((x - 12, y - 40, x + w + 12, y + h + 12)).save(path, optimize=True)
    print(f'{name}: {w} x {h} at {x}, {y}; widgets: ' + ' | '.join(str(c.cget('text')) for c in d.winfo_children() if c.winfo_class() == 'Label'))
    print(f'-----BEGIN {name}-----\n{base64.b64encode(open(path, "rb").read()).decode()}\n-----END {name}-----')
    d.destroy(); root.update()
