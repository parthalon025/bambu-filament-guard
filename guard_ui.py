"""What the guard shows: the card inside Bambu Studio's window, and its icon in the notification area.

The card is a window of the guard that Bambu Studio's window owns: it is above that window and above nothing else,
moves, minimises and closes with it, follows its dark or light mode, and never takes the keyboard. After a slice it
sits in the bottom right corner of the preview, under the slicing result; when "Send print job" opens with a warning
standing, it sits beside that dialog."""
import os, ctypes, ctypes.wintypes as wt
import filament_guard as fg

# Colour tokens: every colour of the card and the icon is named here once, for Bambu Studio's dark and light mode, and used by name.
THEMES = {
    'dark': {'surface': '#1E1E22', 'raised': '#2D2D31', 'text': '#EFEFF0', 'muted': '#A8A8AD', 'border': '#4C4C55', 'ok': '#00AE42', 'warn': '#F5A623', 'danger': '#F2545B'},
    'light': {'surface': '#FFFFFF', 'raised': '#F3F3F5', 'text': '#262E30', 'muted': '#6B6B70', 'border': '#CECECE', 'ok': '#00893A', 'warn': '#B45309', 'danger': '#C62828'},
}
STATUS_LOOK = {'OK': ('Enough', 'ok'), 'TIGHT': ('Tight', 'warn'), 'SPLIT': ('Needs two spools', 'warn'), 'UNKNOWN': ('Unknown', 'muted'),
               'SHORT': ('Not enough', 'danger'), 'MISSING': ('Not loaded', 'danger'), 'COLOUR': ('Colour not loaded', 'danger')}
SPACE = 6                               # the spacing unit of the card, in pixels at 100 % scaling
CARD_RIGHT, CARD_BOTTOM = 90, 56        # the card's distance from the right and bottom edge of Bambu Studio's window (pixels at 100 %):
                                        # in line with the "Slicing Result" panel above it, clear of the two layer sliders
u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32


def theme():
    """Bambu Studio's own colour mode (Preferences > Dark Mode); Windows' where that cannot be read."""
    try: return THEMES['dark' if str(fg.bs_conf().get('app', {}).get('dark_color_mode')) == '1' else 'light']
    except (OSError, ValueError): pass
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as k:
            return THEMES['light' if winreg.QueryValueEx(k, 'AppsUseLightTheme')[0] else 'dark']
    except OSError: return THEMES['light']

def tone_of(res):
    return 'muted' if res['error'] else {0: 'ok', 1: 'muted', 2: 'warn', 3: 'warn', 4: 'danger'}[fg.SEVERITY[res['worst']]]


# ---------------------------------------------------------------- Bambu Studio's windows

def top_windows():
    """[(handle, process id, class, title)] of the visible top-level windows."""
    out = []
    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def each(h, _):
        if u32.IsWindowVisible(h):
            title, cls, pid = ctypes.create_unicode_buffer(400), ctypes.create_unicode_buffer(80), wt.DWORD()
            u32.GetWindowTextW(h, title, 400); u32.GetClassNameW(h, cls, 80); u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            out.append((h, pid.value, cls.value, title.value))
        return True
    u32.EnumWindows(each, 0)
    return out

def studio_windows(windows=None):
    """{process id: handle of the main window} of the Bambu Studio copies that are running."""
    return {pid: h for h, pid, cls, title in (windows or top_windows()) if cls.startswith('wxWindow') and title.endswith('BambuStudio')}

def studio_window(pid=None):
    """The handle of Bambu Studio's main window (of the copy with this process id, when it has one), or None."""
    found = studio_windows()
    return found.get(pid) or next(iter(found.values()), None)

def visible(hwnd):
    return bool(hwnd) and bool(u32.IsWindow(wt.HWND(hwnd))) and bool(u32.IsWindowVisible(wt.HWND(hwnd)))

def process_path(pid):
    k32.OpenProcess.restype = wt.HANDLE; k32.CloseHandle.argtypes = (wt.HANDLE,)
    k32.QueryFullProcessImageNameW.argtypes = (wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD))
    h = k32.OpenProcess(0x1000, False, pid)                                  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h: return ''
    buf, n = ctypes.create_unicode_buffer(1024), wt.DWORD(1024)
    ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)); k32.CloseHandle(h)
    return buf.value if ok else ''

_send_titles = {}
def send_titles(pid):
    """The title of Bambu Studio's "Send print job" dialog: in English, and in the language Bambu Studio is set to,
    read from its own translation file, so the dialog is found in every language."""
    if pid not in _send_titles:
        titles = {'Send print job'}
        try:
            import gettext
            lang = str(fg.bs_conf().get('app', {}).get('language', ''))
            folder = os.path.join(os.path.dirname(process_path(pid)), 'resources', 'i18n')
            for name in dict.fromkeys((lang, lang.lower(), lang.replace('_', '-'), lang[:2])):
                mo = os.path.join(folder, name, 'BambuStudio.mo')
                if name and os.path.exists(mo):
                    with open(mo, 'rb') as f: titles.add(gettext.GNUTranslations(f).gettext('Send print job'))
                    break
        except (OSError, ValueError): pass
        _send_titles[pid] = titles
    return _send_titles[pid]

def send_dialogs():
    """{handle: process id} of the "Send print job" dialogs of Bambu Studio that are open now."""
    windows = top_windows(); studios = studio_windows(windows)
    return {h: pid for h, pid, cls, title in windows if pid in studios and cls == '#32770' and title in send_titles(pid)}

def work_area(hwnd):
    """The usable rectangle of the screen a window is on."""
    class MONITORINFO(ctypes.Structure):
        _fields_ = [('cbSize', wt.DWORD), ('rcMonitor', wt.RECT), ('rcWork', wt.RECT), ('dwFlags', wt.DWORD)]
    u32.MonitorFromWindow.restype = wt.HANDLE; u32.MonitorFromWindow.argtypes = (wt.HWND, wt.DWORD)
    u32.GetMonitorInfoW.argtypes = (wt.HANDLE, ctypes.POINTER(MONITORINFO))
    mi = MONITORINFO(); mi.cbSize = ctypes.sizeof(MONITORINFO)
    u32.GetMonitorInfoW(u32.MonitorFromWindow(wt.HWND(hwnd), 2), ctypes.byref(mi))       # MONITOR_DEFAULTTONEAREST
    return mi.rcWork


# ---------------------------------------------------------------- the card

class Card:
    """One card at a time; each newer answer replaces it. act(name, res, arg) is told what its buttons ask for:
    'recheck', 'ignore', 'details', and 'amount' with arg (tray, grams or None)."""
    def __init__(self, root, act=None, on_close=None):
        self.root, self.win, self.act, self.on_close = root, None, act or (lambda *a: None), on_close
        self.owner, self.beside, self.spot, self.held = None, None, None, False
        self.one_line = False                                                # the card open is one line, which a new line may replace
        self.closes_after = 0                                                # seconds the open card stays once it is on screen; 0 = until closed

    def is_open(self):
        return bool(self.win and self.win.winfo_exists())

    def close(self):
        if self.is_open(): self.win.destroy()
        self.win = None
        if self.on_close: self.on_close()

    def follow(self):
        """Keeps the card in its place while the window it belongs to moves or resizes; closes it with that window."""
        if not self.is_open() or not self.owner: return
        owner = wt.HWND(self.owner)
        if not visible(self.owner): self.close(); return
        if u32.IsIconic(owner): return
        if self.held: self.held = False; self._attach(); return
        scale = self.win.winfo_fpixels('1i') / 96.0
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        if self.beside:                                                      # beside the dialog: right of it, left of it when the screen ends there
            r, area, gap = wt.RECT(), work_area(self.owner), int(2 * SPACE * scale)
            u32.GetWindowRect(owner, ctypes.byref(r))
            x = r.right + gap if r.right + gap + w <= area.right else r.left - gap - w
            spot = (max(area.left, x), max(area.top, min(r.top, area.bottom - h)))
        else:                                                                # in the bottom right corner of the main window
            r, p = wt.RECT(), wt.POINT(); u32.GetClientRect(owner, ctypes.byref(r)); p.x, p.y = r.right, r.bottom; u32.ClientToScreen(owner, ctypes.byref(p))
            spot = (p.x - int(CARD_RIGHT * scale) - w, p.y - int(CARD_BOTTOM * scale) - h)
        if spot != self.spot: self.spot = spot; self.win.geometry(f'+{spot[0]}+{spot[1]}')

    # -- building blocks
    def _open(self, pid=None, beside=None, hold=False):
        import tkinter as tk
        if self.is_open(): self.win.destroy()
        t = self.t = theme()
        self.beside = beside if visible(beside) else None
        self.owner, self.spot, self.held, self.closes_after = self.beside or studio_window(pid), None, False, 0
        if self.owner and u32.IsIconic(wt.HWND(self.owner)) and not hold: self.owner = None      # Bambu Studio is minimised and this card was asked for: a window of its own
        w = self.win = tk.Toplevel(self.root, bg=t['surface'], padx=3 * SPACE, pady=2 * SPACE, highlightthickness=1, highlightbackground=t['border'])
        w.withdraw(); w.title('Bambu Filament Guard'); w.protocol('WM_DELETE_WINDOW', self.close)
        if self.owner: w.overrideredirect(True)
        else: w.attributes('-topmost', True); w.resizable(False, False)
        head = tk.Frame(w, bg=t['surface']); head.grid(row=0, column=0, sticky='ew'); head.columnconfigure(0, weight=1)
        self._label(head, 'Filament check', 'muted', 9, True, row=0, column=0)
        tk.Button(head, text='✕', command=self.close, bg=t['surface'], fg=t['muted'], activebackground=t['raised'], activeforeground=t['text'], relief='flat', bd=0,
                  font=('Segoe UI', 9), padx=SPACE, pady=0, takefocus=0).grid(row=0, column=1, sticky='e')
        return w

    def _label(self, parent, text, colour='text', size=10, bold=False, wrap=0, **grid):
        import tkinter as tk
        l = tk.Label(parent, text=text, bg=parent['bg'], fg=self.t[colour], font=('Segoe UI', size, 'bold' if bold else 'normal'), justify='left', anchor='w', wraplength=wrap)
        l.grid(sticky='w', **grid); return l

    def _button(self, parent, text, command, **grid):
        import tkinter as tk
        t = self.t
        b = tk.Button(parent, text=text, command=command, bg=t['raised'], fg=t['text'], activebackground=t['border'], activeforeground=t['text'],
                      disabledforeground=t['muted'], relief='flat', bd=0, font=('Segoe UI', 9), padx=2 * SPACE, pady=SPACE // 2, takefocus=0)
        b.grid(**grid); return b

    def _place(self):
        """Puts the finished card on the screen: inside the window that owns it, without activating it."""
        w = self.win; w.update_idletasks()
        if not self.owner:
            w.geometry(f'+{w.winfo_screenwidth() - w.winfo_reqwidth() - 48}+72'); w.deiconify(); self._shown(); return
        if u32.IsIconic(wt.HWND(self.owner)): self.held = True; return     # minimised, and the card can wait (hold): shown when the window is back
        self._attach()

    def _attach(self):
        w = self.win
        self.follow(); w.deiconify(); w.update_idletasks()
        hwnd = wt.HWND(int(w.wm_frame(), 16))
        setlong, getlong = u32.SetWindowLongPtrW, u32.GetWindowLongPtrW
        setlong.argtypes, setlong.restype, getlong.argtypes, getlong.restype = (wt.HWND, ctypes.c_int, ctypes.c_void_p), ctypes.c_void_p, (wt.HWND, ctypes.c_int), ctypes.c_void_p
        setlong(hwnd, -8, self.owner)                                                    # GWLP_HWNDPARENT: Bambu Studio's window owns the card
        setlong(hwnd, -20, (getlong(hwnd, -20) or 0) | 0x08000000 | 0x00000080)          # GWL_EXSTYLE: never activated, no taskbar button
        u32.SetWindowPos(hwnd, wt.HWND(0), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)         # to the top of its owner's windows: no move, no size, no activation
        self._shown()

    def _shown(self):
        """The card is on screen. One that closes itself counts its seconds from now: from when it was made, a card held
        while Bambu Studio was minimised could go before anyone saw it."""
        seconds, self.closes_after, w = self.closes_after, 0, self.win
        if seconds: w.after(int(seconds * 1000), lambda: self.close() if self.win is w else None)

    # -- the cards
    def show(self, res, brief=0, beside=None, hold=False):
        """brief: seconds after which a card of one line closes itself; 0 = the full card, which stays until closed.
        beside: the handle of a dialog to stand next to, instead of the corner of the main window.
        hold: while Bambu Studio is minimised, wait unshown until it is back (the answer to a slice nobody asked to see now)."""
        import tkinter as tk
        w, t = self._open(res.get('pid'), beside, hold), self.t
        self.one_line = bool(brief)
        self._label(w, fg.headline(res), tone_of(res), 12, True, row=1, column=0)
        if brief:
            line = tk.Frame(w, bg=t['surface']); line.grid(row=2, column=0, sticky='ew'); line.columnconfigure(0, weight=1)
            p = res['printer']
            busy = (', less what the print running there still needs' if p.get('counted') else ', which is printing a job the guard could not count') if p['state'] in fg.BUSY else ''
            text = 'Ignored for this project' if res.get('ignored') else f"{len(res['rows'])} filament{'s' if len(res['rows']) != 1 else ''} against the spools on the {p['model']}" + busy
            self._label(line, text, 'muted', 9, row=0, column=0)
            self._button(line, 'Details', lambda: self.act('details', res), row=0, column=1, padx=(3 * SPACE, 0))
            self.closes_after = brief
            return self._place()
        if res['error']:
            self._label(w, res['error'][:1].upper() + res['error'][1:] + '.', wrap=560, row=2, column=0)
        else:
            sub = ' · '.join(x for x in (res['title'] + (f" × {res['times']}" if res['times'] != 1 else ''), f"plate {res['plate_no']}" if res.get('plate_no') else '',
                                         res['model'], f"prints in {res['time']}" if res['time'] and res['times'] == 1 else '') if x)
            self._label(w, sub, 'muted', 9, row=2, column=0, pady=(0, 2 * SPACE))
            table = tk.Frame(w, bg=t['surface']); table.grid(row=3, column=0, sticky='w')
            for col, head_text in enumerate(('', 'Filament', 'Needs', 'Spool', 'Has', 'Result')):
                self._label(table, head_text, 'muted', 9, row=0, column=col, padx=(0, 3 * SPACE), pady=(0, SPACE))
            for i, r in enumerate(res['rows'], start=1):
                tk.Frame(table, bg=r['colour'], width=18, height=18, highlightthickness=1, highlightbackground=t['border']).grid(row=2 * i, column=0, padx=(0, 2 * SPACE), pady=SPACE // 2)
                name = f"{r['slot']}  {r['name'] or r['type']}" + (f"  ({r['nozzle']} nozzle)" if r['nozzle'] else '')
                word, colour = STATUS_LOOK[r['status']]
                for col, (text, c, bold) in enumerate(((name, 'text', False), (f"{r['need']:.1f} g", 'text', False), (fg.tray_text(r), 'text', False),
                                                       (fg.has_text(r), 'text', False), (word, colour, True)), start=1):
                    self._label(table, text, c, 10, bold, row=2 * i, column=col, padx=(0, 3 * SPACE))
                if r['note']: self._label(table, r['note'][:1].upper() + r['note'][1:], 'muted', 9, wrap=600, row=2 * i + 1, column=1, columnspan=5, pady=(0, SPACE // 2))
            p = res['printer']
            if p['state'] in fg.BUSY and p.get('counted'):
                spools = f"Spools as the {p['model']} reports them, less what the print running there{fg.job_text(p)} still needs; the amounts are the AMS's estimate."
            else:
                spools = f"Spools as the {p['model']} reports them now; the amounts are the AMS's estimate." + (
                    f" It is printing now{fg.job_text(p)}: that job is still drawing from these spools, and the guard could not count what it still needs."
                    if p['state'] in fg.BUSY else '')
            ignored = ' Ignored for this project: it shows as one line until the finding changes.' if res.get('ignored') else ''
            self._label(w, spools + ignored, 'muted', 9, wrap=620, row=4, column=0, pady=(2 * SPACE, 0))
        bar = tk.Frame(w, bg=t['surface']); bar.grid(row=5, column=0, sticky='w', pady=(2 * SPACE, 0))
        again = self._button(bar, 'Check again', None, row=0, column=0, padx=(0, SPACE))
        again.configure(command=lambda: (again.configure(text='Checking…', state='disabled'), self.act('recheck', res)))
        col = 1
        if not res['error']:
            if res.get('ignored') or fg.SEVERITY[res['worst']] >= 2:
                self._button(bar, 'Stop ignoring' if res.get('ignored') else 'Ignore for this project', lambda: self.act('ignore', res), row=0, column=col, padx=(0, SPACE)); col += 1
            seen = set()
            for r in res['rows']:
                for tray in r['trays']:
                    if tray['label'] in seen or not (tray['grams'] is None or tray.get('entered')): continue
                    seen.add(tray['label'])
                    self._button(bar, f"Amount in {tray['label']}…", lambda tray=tray: self.ask_amount(res, tray), row=0, column=col, padx=(0, SPACE)); col += 1
        self._place()

    def note(self, headline, lines, tone='muted', pid=None):
        """A card that says something other than a result: the self-test, the welcome after installing."""
        w = self._open(pid); self.one_line = False
        self._label(w, headline, tone, 12, True, row=1, column=0, pady=(0, SPACE))
        for i, (mark, text) in enumerate(lines):
            self._label(w, {True: '✓  ', False: '✗  ', None: '–  '}[mark] + text, {True: 'text', False: 'danger', None: 'muted'}[mark], 9, wrap=620, row=2 + i, column=0)
        self._place()

    def line(self, text, tone='muted', seconds=0, pid=None, hold=False):
        """A card of one line that is not a result: a check under way, the guard's start. seconds: it closes itself
        after them; 0 = it stays until something replaces or closes it. Returns its window."""
        w = self._open(pid, None, hold); self.one_line = True
        self._label(w, text, tone, 12, True, row=1, column=0)
        self.closes_after = seconds
        self._place()
        return w

    def ask_amount(self, res, tray):
        """Asks for the grams left on a spool the printer cannot measure. An ordinary small window: it has to take the keyboard."""
        import tkinter as tk
        t = self.t; d = tk.Toplevel(self.root, bg=t['surface'], padx=3 * SPACE, pady=3 * SPACE)
        d.title('Bambu Filament Guard'); d.attributes('-topmost', True); d.resizable(False, False)
        self._label(d, f"Filament left in {tray['label']} ({tray['name'] or tray['type']}), in grams", size=10, bold=True, row=0, column=0, columnspan=3)
        self._label(d, 'Weigh the spool and take off the empty spool (a Bambu spool: about 250 g). The guard counts down from this with every print it sees start; '
                       'a print it cannot count makes the amount unknown again.', 'muted', 9, wrap=420, row=1, column=0, columnspan=3, pady=(SPACE, 2 * SPACE))
        value = tk.StringVar(value=f"{tray['grams']:.0f}" if tray['grams'] is not None else '')
        entry = tk.Entry(d, textvariable=value, width=10, bg=t['raised'], fg=t['text'], insertbackground=t['text'], relief='flat', font=('Segoe UI', 11),
                         highlightthickness=1, highlightbackground=t['border'], highlightcolor=t['ok'])
        entry.grid(row=2, column=0, sticky='w', ipady=SPACE // 2)
        wrong = self._label(d, '', 'danger', 9, row=3, column=0, columnspan=3)
        def done(grams):
            d.destroy(); self.act('amount', res, (tray, grams))
        def accept(*_):
            try: grams = float(value.get().replace(',', '.').replace('g', '').strip())
            except ValueError: grams = -1
            if not 0 < grams <= 10000: wrong.configure(text='A number of grams between 1 and 10000.'); return
            done(grams)
        self._button(d, 'Save', accept, row=2, column=1, padx=(2 * SPACE, 0))
        if tray.get('entered') or tray.get('stale'): self._button(d, 'Forget this amount', lambda: done(None), row=2, column=2, padx=(SPACE, 0))
        entry.bind('<Return>', accept); d.bind('<Escape>', lambda e: d.destroy())
        d.update_idletasks()
        x, y = (self.spot or (200, 200)); d.geometry(f'+{max(0, x)}+{max(0, y - d.winfo_reqheight() - 48)}')
        d.focus_force(); entry.focus_set(); entry.select_range(0, 'end')


def make_root():
    import tkinter as tk
    try: ctypes.windll.shcore.SetProcessDpiAwareness(1)        # sharp text on a scaled screen
    except (AttributeError, OSError): pass
    root = tk.Tk(); root.withdraw()
    root.report_callback_exception = lambda kind, value, trace: fg.log(f'error in the card: {kind.__name__}: {value}')   # pythonw has no console to show it on
    return root


# ---------------------------------------------------------------- the icon in the notification area

def icon_image(tone):
    """A spool seen from the side, in the colour of the last result."""
    from PIL import Image, ImageDraw
    t = THEMES['dark']; im = Image.new('RGBA', (64, 64), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.ellipse((3, 3, 61, 61), fill=t[tone]); d.ellipse((13, 13, 51, 51), outline=t['surface'], width=4); d.ellipse((24, 24, 40, 40), fill=t['surface'])
    return im

class Tray:
    """The guard's icon, shown while Bambu Studio runs: the last result as its colour and tooltip, and the settings as
    its menu. The menu runs on a thread of its own, so every choice is handed to the guard's `todo` queue."""
    def __init__(self, guard):
        self.guard, self.icon, self.shown = guard, None, None
        try: import pystray
        except ImportError:
            fg.log('no icon in the notification area: the Python package pystray is missing (pip install pystray)'); return
        Item, Menu, g = pystray.MenuItem, pystray.Menu, guard
        def do(fn): return lambda icon, item: g.todo.put(fn)
        def option(text, key, on, off): return Item(text, do(lambda: g.set_option(key, off if g.cfg[key] == on else on)), checked=lambda item: g.cfg[key] == on)
        def warns(text, status): return Item(text, do(lambda: g.toggle_warning(status)), checked=lambda item: status in g.cfg['popup_on'])
        menu = Menu(
            Item(f'Bambu Filament Guard {fg.VERSION}', None, enabled=False),
            Item(lambda item: g.last_text(), None, enabled=False),
            Menu.SEPARATOR,
            Item('Show last result', do(g.show_last), default=True),
            Item('Check newest slice now', do(g.check_newest)),
            Item('Settings', Menu(
                option('One line when there is enough', 'ok_seconds', fg.DEFAULTS['ok_seconds'], 0),
                warns('Warn when a colour is not loaded', 'COLOUR'),
                warns("Warn when a spool's amount is unknown", 'UNKNOWN'),
                option('Check again at "Send print job"', 'at_send', True, False),
                option('Learn from prints, tray by tray', 'learn', True, False),
                Item('Reserve', Menu(*[Item(f'{p} % of the full spool', do(lambda p=p: g.set_option('spool_reserve_pct', p)),
                                            checked=lambda item, p=p: g.cfg['spool_reserve_pct'] == p, radio=True) for p in (2, 3, 5)])),
                Item('Start automatically', do(g.toggle_autostart), checked=lambda item: os.path.exists(fg.STARTUP_LNK)))),
            Item('Pause checks', do(g.toggle_pause), checked=lambda item: g.paused),
            Menu.SEPARATOR,
            Item('Self-test', do(g.run_selftest)),
            Item('Open log', do(g.open_log)),
            Item('Forget what was learned', do(g.forget_learned)),
            Item('Quit', do(g.quit)))
        self.icon = pystray.Icon(fg.APP, icon_image('muted'), 'Bambu Filament Guard', menu)
        self.icon.run_detached()

    def set(self, tone, text):
        if self.icon: self.icon.icon = icon_image(tone); self.icon.title = text[:120]

    def refresh(self):
        if self.icon: self.icon.update_menu()

    def visible(self, on):
        if self.icon and on != self.shown: self.shown = on; self.icon.visible = on

    def stop(self):
        if self.icon: self.icon.stop()
