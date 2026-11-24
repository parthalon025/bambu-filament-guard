"""Opens the guard's amount window on this screen and saves pictures of it: python .github/scripts/amount_window.py <folder>

For the release workflow: the window is tkinter on Windows, which the tests drive only through a stand-in, so this
is where it is seen. Prints each picture's size, and the picture itself as base64 between markers, for reading from
the job's log."""
import os, sys, base64
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import guard_ui
from PIL import ImageGrab

def within(widget):
    """A window's widgets, at any depth."""
    for child in widget.winfo_children(): yield child; yield from within(child)

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
    for _ in range(5): root.update()
    save = next(b for b in within(d) if b.winfo_class() == 'Button' and b.cget('text') == 'Save')
    if name.endswith('refused-light'):                                  # Save, by the button's own command: a key event sent to a window without the focus goes nowhere
        save.invoke(); root.update()
        assert any('no more than the empty spool' in str(c.cget('text')) for c in d.winfo_children() if c.winfo_class() == 'Label'), 'the window did not say why it refused'
    x, y, w, h = d.winfo_rootx(), d.winfo_rooty(), d.winfo_width(), d.winfo_height()
    path = os.path.join(folder, name + '.png')
    shot = ImageGrab.grab((x - 12, y - 40, x + w + 12, y + h + 12)); shot.save(path, optimize=True)
    # A button's border is drawn, in the theme's colour: looked for in the picture itself, at the corner of the frame
    # around Save (the tests' stand-in for tkinter cannot tell what Windows draws).
    edge = save.master; corner = shot.convert('RGB').getpixel((edge.winfo_rootx() - (x - 12), edge.winfo_rooty() - (y - 40)))
    border = tuple(int(card.t['border'][i:i + 2], 16) for i in (1, 3, 5))
    assert max(abs(a - b) for a, b in zip(corner, border)) <= 8, f"{name}: no border at Save's corner: {corner} is not {border}"
    print(f'{name}: {w} x {h} at {x}, {y}; widgets: ' + ' | '.join(str(c.cget('text')) for c in d.winfo_children() if c.winfo_class() == 'Label'))
    print(f'-----BEGIN {name}-----\n{base64.b64encode(open(path, "rb").read()).decode()}\n-----END {name}-----')
    d.destroy(); root.update()
