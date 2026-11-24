"""Run by the packaged guard's own python.exe: <unzipped folder>/python.exe .github/scripts/packaged_card.py <folder for the picture>

For the release workflow: proves that the zip, unpacked, holds everything the guard imports (tkinter with its Tcl/Tk,
pystray, Pillow, paho-mqtt, and the guard's own modules), all of it from that folder and none from this computer's
Python, and that it opens the guard's own card on this screen. --help, which the workflow ran before, imports none of
them. Saves a picture of the card and prints what it found."""
import os, sys
home = os.path.dirname(os.path.abspath(sys.executable))
import tkinter, _tkinter, six, pystray, paho.mqtt.client as mqtt
from PIL import Image, ImageGrab
import filament_guard as fg, guard_ui, guard_ledger, guard_learn, guard_stats

for m in (tkinter, _tkinter, six, pystray, mqtt, Image, fg, guard_ui, guard_ledger, guard_learn, guard_stats):
    assert os.path.abspath(m.__file__).startswith(home), f'{m.__name__} came from {m.__file__}, not from {home}'
for name in ('Install.cmd', 'Uninstall.cmd', 'README.txt'):
    data = open(os.path.join(home, name), 'rb').read()
    assert b'\r\n' in data and b'\r\r' not in data, f'{name}: line ends'
for name in ('README.md', 'LICENSE', 'SECURITY.md', 'CHANGELOG.md', 'docs'): assert os.path.exists(os.path.join(home, 'app', name)), f'app/{name} is not in the zip'
for name in ('LICENSE.txt', os.path.join('tcl', 'tk8.6', 'license.terms')): assert os.path.exists(os.path.join(home, name)), f'{name} is not in the zip'
held = sorted(d[:-len('.dist-info')] for d in os.listdir(os.path.join(home, 'Lib', 'site-packages')) if d.endswith('.dist-info'))
mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='ci')            # the call printer_link makes

folder = sys.argv[1]; os.makedirs(folder, exist_ok=True)
root = guard_ui.make_root()
card = guard_ui.Card(root)
card.note(f'Bambu Filament Guard {fg.VERSION}', [(True, f'Python {sys.version.split()[0]}, Tcl/Tk {root.tk.call("info", "patchlevel")}'), (True, ', '.join(held)), (None, home)], 'ok')
for _ in range(5): root.update()
assert card.win.winfo_viewable(), 'the card is not on the screen'
x, y, w, h = card.win.winfo_rootx(), card.win.winfo_rooty(), card.win.winfo_width(), card.win.winfo_height()
path = os.path.join(folder, 'packaged-card.png')
ImageGrab.grab((x - 12, y - 12, x + w + 12, y + h + 12)).save(path, optimize=True)
print(f'{home}: the card is {w} x {h} at {x}, {y}; holds {", ".join(held)}; TCL_LIBRARY in the environment: {os.environ.get("TCL_LIBRARY")!r}; picture {path}')
