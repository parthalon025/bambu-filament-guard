"""Bambu Filament Guard: warns when the spools on the printer do not hold enough filament for a sliced print.

Bambu Studio has no plugin interface, so this runs beside it. It watches the folder where Bambu Studio writes the
G-code of every plate it slices (any project, any printer preset), reads from that G-code how many grams each
filament needs, asks the printer what is left on every spool, and shows the answer as a card inside Bambu Studio's
window, under the slicing result: one line that goes away when there is enough, the full table when a filament falls
short. The card comes back beside the "Send print job" dialog while a warning stands.

  filament_guard.py install            start automatically (Startup shortcut) and start now
  filament_guard.py uninstall          remove the shortcut and stop
  filament_guard.py status | stop      is the guard running / stop it
  filament_guard.py check [FILE]       check one file now: .gcode or .gcode.3mf; no FILE = the newest slice of the
                                       running Bambu Studio. Options: --times N (N prints of it), --popup, --json.
                                       A file with several plates: each plate, then all its plates together.
                                       Exit code 0 enough, 1 tight / unknown, 2 short, 3 not checked (also a file
                                       or a command line that cannot be read: one line says why).
  filament_guard.py trays              the spools every known printer reports
  filament_guard.py learned [--forget [TRAY]] what was learned from previous prints, per tray (--forget: start again,
                                       for one tray only when named; --forget-weigh TRAY: take back that tray's latest
                                       weigh-in; --printer names the printer where two have the tray)
  filament_guard.py selftest           can the guard read this Bambu Studio and reach the printers? Exit code 1 = no
  filament_guard.py watch              the guard itself (what the shortcut runs; --no-start-line: setup's card says it)
  filament_guard.py setup              install, and say so on a card (what Install.cmd of the packaged guard runs)
Run with "python" in front. build.py packs the guard with a Python of its own, for a computer that has none.

What it reads, and from where:
  need      G-code header "total filament weight [g]" per used filament (purge and prime tower included); type,
            colour, filament id and nozzle from the G-code's own settings block.
  printers  the serials that have a LAN access code in %APPDATA%/BambuStudio/BambuStudio.conf; the model of each from
            its serial prefix (%APPDATA%/BambuStudio/printers/*.json); the IP from the printer's own LAN announcement.
  left      the printer's status over MQTT (read only, one "report everything" request): grams left per AMS tray.
            For a spool the printer cannot measure: the amount entered on the card, counted down (guard_ledger.py).
  learned   per tray, from the prints the guard followed: how far its reading can be trusted (guard_learn.py).
  running   the job the printer runs or prepares, from the same status (name, file, layer, trays), matched to a slice
            the guard saw: what it still needs, by that slice's grams per layer (read from the whole G-code, kept in
            %LOCALAPPDATA%/BambuFilamentGuard/profiles/), is taken off the spools first. The answer is for the moment
            the checked print could start.

The access code goes from Bambu Studio's configuration to the printer and nowhere else: not into the log, the state
file, a window or the output. Settings: the icon's menu, or config.json (see DEFAULTS). Log and state:
%LOCALAPPDATA%/BambuFilamentGuard/. The card and the icon are in guard_ui.py."""
import sys, os, re, json, ssl, time, glob, math, ctypes, hashlib, itertools, contextlib, zipfile, socket, struct, threading, queue, subprocess, argparse, tempfile

APP = 'BambuFilamentGuard'
VERSION = '1.5.0'
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(os.environ.get('LOCALAPPDATA') or HERE, APP)
CONFIG = os.path.join(HERE, 'config.json')
BS_DATA = os.path.join(os.environ.get('APPDATA', ''), 'BambuStudio')
MUTEX = 'Local\\' + APP
STARTUP_LNK = os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup', 'Bambu Filament Guard.lnk')

DEFAULTS = {
    # "Enough" asks for the need plus a reserve, because the AMS's figure is an estimate: the larger of margin_pct of
    # the need and spool_reserve_pct of a spool's full weight (30 g on a 1 kg spool), the largest spool's of those the
    # filament could be printed from. The AMS counts what it feeds, so its error grows with what the spool has given,
    # and is largest when the spool is nearly empty.
    # Measured 2026-11-19 (H2D): a 1 kg spool reported at 105 g ran dry after 84-85 g of a print that needed 85.3 g.
    'margin_pct': 10,
    'spool_reserve_pct': 3,
    'colour_tolerance': 40,      # how far a spool's colour may be from the project's and still count as that colour (0..765;
                                 # Bambu's PLA Basic red and PLA Silk+ red are 31 apart, black and dark grey 190)
    # Which results open the full card, which stays until closed. Add "UNKNOWN" to be told about spools without a
    # remaining amount; remove "COLOUR" when the colours of your projects are not the colours you print in. SHORT and
    # MISSING open it whether named here or not (Guard.opening).
    'popup_on': ['SHORT', 'MISSING', 'COLOUR', 'SPLIT', 'TIGHT'],
    'ok_seconds': 8,             # a result that is not in popup_on shows as one line for this long; 0 = shows nothing
    'at_send': True,             # check again when "Send print job" opens, and show a warning beside that dialog
    'poll_seconds': 2,
    'printers': {},              # optional, per serial: {"ip": "192.168.1.50"} when the printer is not announced on this LAN
    # Learn from previous prints, per tray, how far its reading can be trusted (guard_learn.py): the guard follows a
    # printer it has checked while it prints (read only, as for entered amounts), and a check counts a tray with less
    # where the tray's history shows its reading runs high, and a print with more where it shows prints take more. Off:
    # nothing is followed or corrected for it; what was learned is kept.
    'learn': True,
    # Experimental, with learning on: once the printer has enough scored prints and readings at empty, the reserve is
    # measured from the guard's own past forecast errors instead of the fixed one above, and may be smaller (never below
    # 5 g or 2 % of the need). Off unless turned on: it loosens a check on evidence that cannot see a change in what
    # spools read at empty (docs/learning.md, "What the measured reserve cannot see"), and nothing of the learning has
    # yet followed a real print. Off, no answer is looser than the fixed reserve and 1.3.0's rules give.
    'measured_reserve': False,
    'empty_spool_g': 250,        # the empty spool's weight the amount window starts with: the last one entered there (a Bambu spool: about 250 g)
}
SEVERITY = {'OK': 0, 'UNKNOWN': 1, 'TIGHT': 2, 'SPLIT': 3, 'SHORT': 4, 'MISSING': 4, 'COLOUR': 4}
BUSY = ('RUNNING', 'PAUSE', 'PREPARE')                    # a job is on the printer: it draws, or is about to draw, on the spools
STATE_LOCK = threading.RLock()                            # where Windows' named mutexes are not to be had (see state_lock)
LINK_NUMBER = itertools.count(1)


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG, encoding='utf-8') as f: cfg.update(json.load(f))
    except FileNotFoundError: pass
    except ValueError as e: log(f'config.json cannot be read ({e}): the defaults are used')
    return cfg

def typed_grams(text):
    """The grams typed into a field ('1 012,5 g'), or None when that is not a number."""
    try: g = float(str(text).replace(',', '.').replace('g', '').replace(' ', ''))
    except ValueError: return None
    return g if math.isfinite(g) else None

def filament_on(scale, spool):
    """(grams of filament, None) for what the scale showed and what the empty spool weighs, both as typed; (None, what
    is wrong) when that is not an amount. An empty spool left blank is 0: the filament was weighed alone."""
    gross, tare = typed_grams(scale), typed_grams(spool) if str(spool).strip() else 0.0
    if gross is None or not 0 < gross <= 15000: return None, 'The weight on the scale: a number of grams between 1 and 15000.'
    if tare is None or not 0 <= tare <= 5000: return None, 'The empty spool: a number of grams between 0 and 5000.'
    if gross <= tare: return None, 'The scale shows no more than the empty spool weighs: no filament would be left.'
    if gross - tare > 10000: return None, 'More than 10000 g of filament: not a spool.'
    return gross - tare, None

def weigh_doubt(grams, reading):
    """'' when a spool weighed at `grams` of filament is within what a gauge can be out of the printer's `reading`, either
    way (guard_learn.DRY_MOST); else the line the weigh-in window shows in place of saving. A slipped key, another spool
    on the scale or a reading that has moved on since would be kept as what the tray reads at empty, and count every
    tray of the printer lower. Saving again takes the weight as right."""
    import guard_learn
    apart = abs(grams - reading)
    if apart <= guard_learn.DRY_MOST: return ''
    return f'{grams:.0f} g weighed, {reading:.0f} g read by the printer: {apart:.0f} g apart. Save again if the weight is right.'

def save_config(cfg):
    """Only what differs from DEFAULTS goes into config.json: a full copy would pin today's defaults for good."""
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    json.dump({k: v for k, v in cfg.items() if DEFAULTS.get(k) != v}, open(CONFIG, 'w', encoding='utf-8'), indent=1)

LOST = [0]                                                # the lines guard.log did not take since the last one it took
CONTROL = re.compile(r'[\x00-\x1f\x7f-\x9f\u2028\u2029]')   # line breaks and the other control characters: none of them goes into the log

def log(msg):
    """One line into guard.log, with its time. It raises nothing: a log that could not be written (the disk full, the file
    held by another program) once cost a warning its card, and ended the watcher and the card's loop for good. The lines
    lost are counted, and the next line that is written says how many. A message is one line whatever it holds: a job
    or project name with a line break in it, from the printer or a file, forged lines of a log users paste into reports."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        path, lost = os.path.join(STATE_DIR, 'guard.log'), LOST[0]
        try:
            if os.path.getsize(path) > 1_000_000: os.replace(path, path + '.1')
        except OSError: pass
        with open(path, 'a', encoding='utf-8', errors='backslashreplace') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + (f"({lost} line{'s' if lost != 1 else ''} before this one could not be written) " if lost else '') + CONTROL.sub(' ', f'{msg}') + '\n')
        LOST[0] -= lost
    except (OSError, ValueError): LOST[0] += 1

def packaged():
    """Whether this is the packaged guard: build.py puts the scripts in app/, beside a Python and a Tcl/Tk of their own."""
    return os.path.basename(HERE) == 'app'

def missing(package):
    """What to say of a Python package the guard cannot import. Run from source: how to install it. The packaged guard
    has no pip and a Python of its own: there its folder is incomplete, and unzipping it again is what helps."""
    return f"the guard's folder is incomplete ({package} is missing): unzip the guard again" if packaged() else f'the Python package {package} is missing (pip install {package})'

class StateBusy(OSError):
    """The state folder's files could not be had: another holder kept them past the wait, or a program outside the guard
    held a file past the retries of a write."""

_K32, _MUTEXES = [], {}

def _kernel32():
    if not _K32:
        try:
            k = ctypes.WinDLL('kernel32', use_last_error=True)
            k.CreateMutexW.restype, k.CreateMutexW.argtypes = ctypes.c_void_p, (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
            k.WaitForSingleObject.restype, k.WaitForSingleObject.argtypes = ctypes.c_uint32, (ctypes.c_void_p, ctypes.c_uint32)
            k.ReleaseMutex.restype, k.ReleaseMutex.argtypes = ctypes.c_int, (ctypes.c_void_p,)
        except (AttributeError, OSError): k = None
        _K32.append(k)
    return _K32[0]

@contextlib.contextmanager
def state_lock(wait=10.0):
    """Sole use of the state folder's files (state.json, learned.json, profiles/) for a read, or a read and its write:
    one holder at a time across the threads of this process and across processes (the watcher, a command beside it,
    whose read-modify-writes would otherwise undo each other). A Windows named mutex, named after the folder: the thread
    that holds it may take it again, and it is let go when its holder ends, even by a crash. StateBusy after `wait`."""
    k32 = _kernel32()
    if k32 is None:
        with STATE_LOCK: yield
        return
    name = 'Local\\' + APP + '-files-' + hashlib.sha1(os.path.abspath(STATE_DIR).lower().encode('utf-8')).hexdigest()[:16]
    handle = _MUTEXES.get(name) or _MUTEXES.setdefault(name, k32.CreateMutexW(None, False, name))
    if not handle: raise StateBusy(f'no lock could be made for the state folder (error {ctypes.get_last_error()})')
    if k32.WaitForSingleObject(handle, int(wait * 1000)) not in (0, 0x80):        # 0x80: its last holder ended without letting go: ours now
        raise StateBusy(f'the state folder was held elsewhere for more than {wait:.0f} s')
    try: yield
    finally: k32.ReleaseMutex(handle)

def read_json(path, empty):
    """A JSON file of the state folder, read under state_lock: `empty` when there is none. A program outside the guard
    holding it is waited for, then StateBusy. A damaged file is set aside (and logged), and `empty` is returned: kept
    in place, it would make every later read fail."""
    with state_lock():
        for attempt in range(40):
            try:
                with open(path, encoding='utf-8') as f: return json.load(f)
            except FileNotFoundError: return empty
            except PermissionError: time.sleep(0.05)
            except ValueError:
                try: os.replace(path, path + '.damaged')
                except OSError: pass
                log(f'{os.path.basename(path)} was damaged: set aside as {os.path.basename(path)}.damaged, starting empty')
                return empty
        raise StateBusy(f'{os.path.basename(path)} is held by another program')

def write_json(path, data, **how):
    """Writes a JSON file of the state folder whole (call under state_lock): to a temp file of this process and thread,
    put in place by a replace, which a reader never sees half done. Windows refuses the replace while a program outside
    the guard holds the file: it is tried again for two seconds, then StateBusy, and the file is as it was."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = f'{path}.{os.getpid()}.{threading.get_ident()}.new'
    with open(temp, 'w', encoding='utf-8') as f: json.dump(data, f, **how)
    for attempt in range(40):
        try: os.replace(temp, path); return
        except PermissionError: time.sleep(0.05)
    try: os.remove(temp)
    except OSError: pass
    raise StateBusy(f'{os.path.basename(path)} could not be written: another program holds it')

def load_state():
    """The state file ({} when there is none; see read_json)."""
    return read_json(os.path.join(STATE_DIR, 'state.json'), {})

def save_state(state):
    with state_lock(): write_json(os.path.join(STATE_DIR, 'state.json'), state, indent=1)

def update_state(change):
    """Reads the state file, lets change(state) alter it, and writes it when it differs: one step, which no other thread
    or process can come between. Returns what change returns. StateBusy when the file cannot be had or written: the
    state is then as it was, and the caller is the one to try again (a count-down is not taken as done until written)."""
    with state_lock():
        state = load_state(); before = json.dumps(state, sort_keys=True)
        out = change(state)
        if json.dumps(state, sort_keys=True) != before: save_state(state)
        return out


# ---------------------------------------------------------------- what the print needs: the sliced G-code

def read_head(stream, limit=4 << 20):
    """The text of a G-code file up to the end of its settings block (about 70 kB of a file that runs to 100 MB)."""
    buf = b''
    while len(buf) < limit and b'; CONFIG_BLOCK_END' not in buf:
        chunk = stream.read(1 << 16)
        if not chunk: break
        buf += chunk
    return buf.decode('utf-8', 'replace')

def split_list(value):
    """Bambu Studio writes text lists with ';' and number lists with ','."""
    value = value.strip()
    parts = value.split(';') if (';' in value or value.startswith('"')) else value.split(',')
    return [p.strip().strip('"') for p in parts]

def parse_plate(text):
    """{'printer_model', 'time', 'layers', 'filaments': [{slot, type, fid, colour, name, grams, nozzle, density}]} from the
    head of a Bambu Studio G-code. None while the file is not complete (Bambu Studio writes the totals last). grams is
    None for a filament in use that Bambu Studio gives no weight: how much it needs cannot be told."""
    if '; HEADER_BLOCK_END' not in text or '; CONFIG_BLOCK_END' not in text: return None
    head, _, body = text.partition('; HEADER_BLOCK_END')
    def header(key):
        m = re.search(r'^; ' + re.escape(key) + r'[ \t]*:[ \t]*(.*)$', head, re.M)       # not \s: across a line end, a line without a value takes the next line for it
        return m.group(1).strip() if m else None
    def setting(key):
        m = re.search(r'^; ' + re.escape(key) + r' = (.*)$', body, re.M)
        return split_list(m.group(1)) if m and m.group(1).strip() else []
    weights = header('total filament weight [g]')
    if weights is None: return None
    totals, weights = weights, [float(w) for w in weights.split(',') if w.strip()]
    if not all(math.isfinite(w) for w in weights): raise ValueError(f'a filament total that is not an amount: "{totals}"')    # float() takes "nan" and "inf"
    types, colours, fids, names = setting('filament_type'), setting('filament_colour'), setting('filament_ids'), setting('filament_settings_id')
    used = header('filament')                      # the slots the totals belong to, e.g. "1,3,4"
    if used: slots = [int(s) for s in used.split(',') if s.strip()]
    elif len(weights) == len(types): slots = list(range(1, len(types) + 1))
    else: raise ValueError(f'{len(weights)} filament totals for {len(types)} filaments and no "; filament:" line to place them')
    if len(slots) != len(weights): raise ValueError(f'{len(weights)} filament totals for the {len(slots)} filaments in use')
    # Two nozzles (H2D, H2C): filament_map says which nozzle prints each slot, 1 = left, 2 = right. A spool can only
    # be used by the nozzle its AMS is plumbed to, so the nozzle is part of what "enough" means.
    nozzle_of = setting('filament_map') if len(setting('nozzle_diameter')) > 1 else []
    def at(lst, slot, default=''): return lst[slot - 1] if 0 < slot <= len(lst) else default
    def numbers(key):
        try: return [float(v) for v in (header(key) or '').split(',') if v.strip()]
        except ValueError: return []
    lengths, densities = numbers('total filament length [mm]'), numbers('filament_density')    # for each filament in use, like the totals; for each slot
    if len(lengths) != len(weights): lengths = []
    filaments = []
    for n, (slot, grams) in enumerate(zip(slots, weights), start=1):
        if grams <= 0:
            # 0.00 g is also what Bambu Studio writes for a filament that is printed, when its profile has density 0 (a
            # profile made by hand, until one is entered). Dropped as not in use, a two-colour plate once read "enough" on
            # the other colour alone. In use: named by "; filament:", and with a length, or a density of 0.
            if not used or not (at(lengths, n, 0.0) > 0 or at(densities, slot, None) == 0): continue
            grams = None
        filaments.append({'slot': slot, 'type': at(types, slot, '?'), 'fid': at(fids, slot), 'colour': at(colours, slot, '#808080')[:7].upper(),
                          'name': re.sub(r'\s*@.*$', '', at(names, slot)), 'grams': grams,
                          'nozzle': {'1': 'left', '2': 'right'}.get(at(nozzle_of, slot)), 'density': at(densities, slot, None)})
    time_line = re.search(r'total estimated time: ([^;\n]+)', head)
    layers = header('total layer number')
    return {'printer_model': (setting('printer_model') or [''])[0], 'time': time_line.group(1).strip() if time_line else '',
            'layers': int(layers) if layers and layers.isdigit() else None, 'filaments': filaments}

def plates_of_file(path):
    """[(label, plate)] of a .gcode file or of every sliced plate in a .gcode.3mf; the plate is None for a plate of a 3MF
    whose G-code is not complete, which the caller has to say: left out, the rest would pass for the whole file.
    ValueError, with the reason, for a file that is not one or cannot be read: a 3MF that is damaged, cut short or
    encrypted included."""
    with open(path, 'rb') as f: zipped = f.read(2) == b'PK'  # a 3MF by how it begins: cut short, is_zipfile no longer knows it (Python 3.15), and it was then called an incomplete G-code file
    if zipped or zipfile.is_zipfile(path):
        import zlib
        out = []
        try:
            with zipfile.ZipFile(path) as z:
                names = sorted((n for n in z.namelist() if re.fullmatch(r'Metadata/plate_\d+\.gcode', n)), key=lambda n: int(re.findall(r'\d+', n)[0]))
                for n in names:
                    with z.open(n) as f: out.append(('plate ' + re.findall(r'\d+', n)[0], parse_plate(read_head(f))))
        except (zipfile.BadZipFile, zlib.error, RuntimeError, EOFError) as e: raise ValueError(f'this 3MF cannot be read ({e})')
        if not out: raise ValueError('no sliced plate in this 3MF: slice it first (a project file holds no G-code)')
        return out
    with open(path, 'rb') as f: plate = parse_plate(read_head(f))
    if not plate: raise ValueError('not a complete Bambu Studio G-code file')
    return [('', plate)]

def plates_together(plates):
    """One plate that needs what the plates of a file need together, for "Print all plates": the grams of each filament
    added up over the plates. A filament is one material, colour, product and nozzle, whichever slot it is in; without
    a weight on one plate (parse_plate) it has none in all."""
    sums = {}
    for plate in plates:
        for f in plate['filaments']:
            key = (f['type'].upper(), f['colour'], f['fid'], f['nozzle'])
            if key in sums: sums[key]['grams'] = None if None in (sums[key]['grams'], f['grams']) else sums[key]['grams'] + f['grams']
            else: sums[key] = dict(f)
    return {'printer_model': plates[0]['printer_model'], 'time': '', 'layers': None, 'filaments': list(sums.values())}

def lines_of(stream, size=1 << 20):
    """The lines of a binary stream, read in large pieces: a 3MF member's own readline is too slow for 4 million lines."""
    rest = b''
    while True:
        chunk = stream.read(size)
        if not chunk: break
        lines = (rest + chunk).split(b'\n'); rest = lines.pop()
        yield from lines
    if rest: yield rest

# The lines that move filament or say where it goes, indented or not: a move or "G92" with an E, read only up to a
# comment, and an X or Y before or after the E when there is one (a move along X or Y, not of the filament alone); a
# filament selected; the start of a layer; absolute or relative extrusion. The X or Y is caught as one letter, not cut
# out of the line: a 100 MB file has two and a half million such moves.
GCODE_STEP = re.compile(rb'[ \t]*(?:G([0-3]|92)[ \t](?:[^;E\n]*?([XY]))?[^;E\n]*E([-+]?(?:\d+\.?\d*|\.\d+))(?:[^;\n]*?([XY]))?|T(\d+)|M73 L(\d+)|M8([23])(?![\d.]))')
PURGE_MARKS = (b'; FLUSH_START', b'; FLUSH_END', b'; VFLUSH_START', b'; VFLUSH_END', b';VG1')
VG1_E = re.compile(rb';VG1[^;\n]*?[ \t]E([-+]?(?:\d+\.?\d*|\.\d+))')
PROFILE_TOLERANCE = (0.02, 0.002)            # grams, and share of the total: how far a filament's sum may be from the header's

def layer_profile(stream, plate, why=None):
    """{'layers': {slot: [grams in layer 0, layer 1, ..., the last layer]}, 'purge': {slot: [of those, the purge]},
    'pushed': {slot: [of the purge, the nozzle's content charged to this filament (+) or taken off it (-)]}} for each
    filament of the plate, from its whole G-code (a 100 MB file in about two seconds), or None when the G-code is not
    understood; why, a list, then gets the reasons. The printed grams are layers less purge. A layer starts at "M73 L<n>", the number the printer reports as
    layer_num; layer 0 is what comes before. "T<n>" selects slot n + 1 (T255 and above are not filaments).
    Counted as Bambu Studio counts its header totals, measured on its 02.09.01.70 slices for the A1, P1, X1, H2, P2S,
    A2L, X2D and H3C families (each filament within 0.005 g):
      - the moves along X or Y that extrude (a positive E), anywhere in the file;
      - the purge at each filament change: the extruding moves inside "; FLUSH_START" .. "; FLUSH_END" (A1, P1, X1),
        or the ";VG1 E<n>" comments inside "; VFLUSH_START" .. "; VFLUSH_END" (the printer purges by itself: H2, P2S,
        A2L, X2D, H3C). Of a change's purge, as much as the nozzle holds (nozzle_volume, of that filament's nozzle) is
        the filament it pushes out, the rest the filament loaded; a nozzle's first filament pushes nothing out.
    Not counted: moves of the filament alone outside a purge (retract, unretract, load, prime), and an E on any other
    command. A filament whose sum is further from the header's total than PROFILE_TOLERANCE is not understood; within it,
    its lists are scaled to the total, so they add up to it exactly. None also for a file that is not the one the plate
    was read from (its totals differ), that ends before its first or its last layer, whose layers are out of order, or
    that lacks a filament's density or diameter, or the weight of a filament in use (parse_plate)."""
    def refuse(reason):
        if why is not None: why.append(reason)
    head, lines = {}, lines_of(stream)
    for line in lines:
        if line.startswith(b'; HEADER_BLOCK_END'): break
        key, colon, value = line[2:].partition(b':')
        if colon and line.startswith(b'; '): head[key.strip()] = value.strip()
    else: return refuse('it has no header')
    for line in lines:                                 # the settings block: each slot's nozzle, and what a nozzle holds
        if line.startswith(b'; CONFIG_BLOCK_END'): break
        key, equals, value = line[2:].partition(b' = ')
        if equals and key in (b'filament_map', b'nozzle_diameter', b'nozzle_volume'): head[key] = value.strip()
    else: return refuse('it has no settings block')
    def numbers(key):
        try: return [float(v) for v in head.get(key, b'').split(b',') if v.strip()]
        except ValueError: return []
    if any(f['grams'] is None for f in plate['filaments']): return refuse('a filament of it has no weight in the header')
    if [w for w in numbers(b'total filament weight [g]') if w > 0] != [f['grams'] for f in plate['filaments']]: return refuse('its totals are not the plate\'s: the file has changed')
    density, diameter, holds = numbers(b'filament_density'), numbers(b'filament_diameter'), numbers(b'nozzle_volume')
    nozzle_of = [int(z) - 1 for z in numbers(b'filament_map')] if len(numbers(b'nozzle_diameter')) > 1 else []
    mm = {}                                            # (slot, layer): [mm printed, mm purged]
    changes, change, held, since = [], None, {}, 0.0   # filament changes, (pushed out, loaded, nozzle, layer, mm purged); each nozzle's filament
    tool, layer, run, purged, absolute, at, flushing, venting = None, 0, 0.0, 0.0, False, 0.0, False, False
    step_of = GCODE_STEP.match
    for line in lines:
        if line[:1] == b';':
            if not line.startswith(PURGE_MARKS): continue
            if line.startswith(b';VG1'):
                m = VG1_E.match(line)
                if venting and m and float(m.group(1)) > 0: purged += float(m.group(1)); since += float(m.group(1))
            elif line.startswith(b'; FLUSH_'): flushing = line.startswith(b'; FLUSH_START')
            else: venting = line.startswith(b'; VFLUSH_START')
            continue
        m = step_of(line)
        if m is None: continue
        move, x_or_y, e, y_or_x, t, new_layer, mode = m.groups()
        if e is not None:
            if move == b'92': at = float(e); continue
            step = float(e)
            if absolute: step, at = step - at, step
            if step <= 0: continue
            if flushing: purged += step; since += step
            elif x_or_y or y_or_x: run += step
        elif t is not None:
            if int(t) < 255 and int(t) + 1 != tool:
                if run or purged: cell = mm.setdefault((tool, layer), [0.0, 0.0]); cell[0] += run; cell[1] += purged; run = purged = 0.0
                if change: changes.append(change + (since,))
                tool, since = int(t) + 1, 0.0
                z = nozzle_of[tool - 1] if tool <= len(nozzle_of) else 0
                change = (held[z], tool, z, layer) if held.get(z) not in (None, tool) else None; held[z] = tool
        elif new_layer is not None:
            if int(new_layer) < layer: return refuse('its layers are out of order')     # "from this layer to the end" would mean nothing
            if run or purged: cell = mm.setdefault((tool, layer), [0.0, 0.0]); cell[0] += run; cell[1] += purged; run = purged = 0.0
            layer = int(new_layer)
        else: absolute = mode == b'2'
    if run or purged: cell = mm.setdefault((tool, layer), [0.0, 0.0]); cell[0] += run; cell[1] += purged
    if change: changes.append(change + (since,))
    last = layer
    if not last: return refuse('it ends before its first layer')
    if plate.get('layers') and last != plate['layers']: return refuse(f"it ends at layer {last} of {plate['layers']}")
    if len(plate['filaments']) == 1:                  # one filament: what runs before any "T" is that one
        for (slot, n), (p, q) in list(mm.items()):
            if slot is None: cell = mm.setdefault((plate['filaments'][0]['slot'], n), [0.0, 0.0]); cell[0] += p; cell[1] += q
    for f in plate['filaments']:
        slot = f['slot']
        if slot > min(len(density), len(diameter)) or density[slot - 1] <= 0 or diameter[slot - 1] <= 0: return refuse(f'filament {slot} has no density or diameter')
    area = lambda slot: math.pi * (diameter[slot - 1] / 2) ** 2
    # The header charges a nozzle's content to the filament it held, at the change, while the spool of the filament
    # loaded supplies the whole purge. The profile follows the header (its totals are the guard's measure of the need),
    # and keeps that share apart, as 'pushed', so that the spools' own view can be had back from it.
    pushed = {}                                        # (slot, layer): mm3 of the purge that is the filament pushed out
    for out_of, into, z, n, purge_mm in changes:
        if max(out_of, into) > min(len(density), len(diameter)): continue
        volume = min(holds[z] if z < len(holds) else 0.0, purge_mm * area(into))
        pushed[out_of, n] = pushed.get((out_of, n), 0.0) + volume; pushed[into, n] = pushed.get((into, n), 0.0) - volume
    out, off = {'layers': {}, 'purge': {}, 'pushed': {}}, []
    for f in plate['filaments']:
        slot, total = f['slot'], f['grams']
        per_mm3 = density[slot - 1] / 1000
        printed = [mm.get((slot, n), (0.0, 0.0))[0] * area(slot) * per_mm3 for n in range(last + 1)]
        purge = [(mm.get((slot, n), (0.0, 0.0))[1] * area(slot) + pushed.get((slot, n), 0.0)) * per_mm3 for n in range(last + 1)]
        counted = sum(printed) + sum(purge)
        if abs(counted - total) > max(PROFILE_TOLERANCE[0], total * PROFILE_TOLERANCE[1]) or not counted:
            off.append(f'filament {slot} adds up to {counted:.3f} g, the header says {total:.2f} g ({counted - total:+.3f} g)'); continue
        out['layers'][slot] = [(p + q) * total / counted for p, q in zip(printed, purge)]
        out['purge'][slot] = [q * total / counted for q in purge]
        out['pushed'][slot] = [pushed.get((slot, n), 0.0) * per_mm3 * total / counted for n in range(last + 1)]
    if off:                                            # the accounting of another Bambu Studio version would show here, with a number
        for reason in off: refuse(reason)
        return None
    return out

def temp_roots():
    """The folders Bambu Studio may write its temporary G-code under, each once. Its temp folder is the one Windows
    gives a program (GetTempPath: %TMP%, else %TEMP%), not always the one Python goes by (%TEMP% before %TMP%, and
    TMPDIR before both): with only one of the two moved, the guard would watch a folder Bambu Studio never writes to.
    So that one, both variables', Python's, and Bambu Studio's own data folder."""
    roots, k32 = [], _kernel32()
    if k32:
        buf = ctypes.create_unicode_buffer(1024)
        if 0 < k32.GetTempPathW(len(buf), buf) < len(buf): roots.append(buf.value)
    roots += [os.environ.get('TMP'), os.environ.get('TEMP'), tempfile.gettempdir(), os.path.join(BS_DATA, 'tmp')]
    distinct = {}
    for root in roots:
        if root: distinct.setdefault(os.path.normcase(os.path.realpath(root)), os.path.abspath(root))    # one folder under two spellings would show each slice twice
    return list(distinct.values())

def temp_gcodes():
    """The G-code of every plate the running Bambu Studio copies have sliced: <temp>/bamboo_model/<day>/<time>#<pid>#<id>/
    Metadata/.<pid>.<n>.gcode (PartPlate::get_tmp_gcode_path, v02.09.01.70). With under 2 GB free in the temp folder
    Bambu Studio uses its own data folder instead. The folder's own path is taken as it is spelt (glob.escape): a
    "[" in it would otherwise be read as part of the pattern, and nothing found."""
    out = []
    for root in temp_roots():
        out += glob.glob(os.path.join(glob.escape(root), 'bamboo_model', '*', '*', 'Metadata', '.*.gcode'))
    return out

def project_title(gcode_path):
    """The project a temporary G-code belongs to: Bambu Studio notes the opened file in origin.txt of the session folder."""
    try:
        origin = open(os.path.join(os.path.dirname(os.path.dirname(gcode_path)), 'origin.txt'), encoding='utf-8', errors='replace').read().strip()
        if origin: return re.sub(r'(\.gcode)?\.3mf$', '', os.path.basename(origin), flags=re.I)
    except OSError: pass
    return 'Unsaved project'


# ---------------------------------------------------------------- what is left: the printer

class PrinterError(Exception): pass

def bs_conf():
    s = open(os.path.join(BS_DATA, 'BambuStudio.conf'), encoding='utf-8', errors='replace').read()
    return json.loads(s[:s.rfind('}') + 1])                    # a checksum line follows the JSON

def model_by_serial_prefix():
    out = {}
    for path in glob.glob(os.path.join(BS_DATA, 'printers', '*.json')):
        try: d = json.load(open(path, encoding='utf-8'))
        except (OSError, ValueError): continue
        for v in (d.values() if isinstance(d, dict) else []):
            if isinstance(v, dict) and v.get('sn_prefix') and v.get('display_name'): out[v['sn_prefix']] = v['display_name']
    return out

def known_printers(conf):
    """[{'serial', 'model'}] for every printer Bambu Studio holds a LAN access code for; the one selected there first."""
    models = model_by_serial_prefix()
    last = conf.get('app', {}).get('user_last_selected_machine')
    serials = sorted(conf.get('access_code', {}), key=lambda s: s != last)
    return [{'serial': s, 'model': models.get(s[:3], 'unknown model')} for s in serials]

def discover(seconds=8.0, want=None):
    """{serial: ip} of the Bambu printers announcing themselves on the LAN (SSDP notices on UDP 2021 / 1990)."""
    socks, found = [], {}
    for port in (2021, 1990):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('', port))
            try: s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, struct.pack('4sl', socket.inet_aton('239.255.255.250'), socket.INADDR_ANY))
            except OSError: pass
            s.settimeout(0.3); socks.append(s)
        except OSError: pass
    t0 = time.time()
    while socks and time.time() - t0 < seconds and not (want and set(want) <= set(found)):
        for s in socks:
            try: data, addr = s.recvfrom(4096)
            except OSError: continue
            h = {k.strip().lower(): v.strip() for k, v in (l.split(':', 1) for l in data.decode('utf-8', 'replace').split('\r\n') if ':' in l)}
            if h.get('usn'): found[h['usn']] = h.get('location') or addr[0]
    for s in socks: s.close()
    return found

def printer_ip(serial, cfg, refresh=False):
    """The printer's IP: config.json, else the last one seen, else its LAN announcement (asked for at most every 10 minutes)."""
    fixed = cfg.get('printers', {}).get(serial, {}).get('ip')
    if fixed: return fixed
    state = load_state(); ips = state.get('ips', {})
    if serial in ips and not refresh: return ips[serial]
    if time.time() - state.get('discovered_at', 0) > (0 if serial not in ips else 600):
        found = discover(want=[serial])
        def change(st): st.setdefault('ips', {}).update(found); st['discovered_at'] = time.time(); return st['ips'].get(serial)
        return update_state(change)
    return ips.get(serial)

def merge(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict): merge(a[k], v)
        else: a[k] = v

def printer_link(ip, serial, code, client_id, on_report, on_refused=None):
    """A read-only link to the printer: it subscribes to the printer's status reports and asks once, at each
    connection, for all of them (pushall). Everything else on this channel needs Developer Mode and is not used.
    on_report gets the "print" part of every report, on the link's own thread. Returns the client; loop_stop() and
    disconnect() end it."""
    try: import paho.mqtt.client as mqtt
    except ImportError: raise PrinterError(missing('paho-mqtt'))
    def on_connect(c, userdata, flags, rc, *rest):
        if rc.is_failure if hasattr(rc, 'is_failure') else rc != 0:
            if on_refused: on_refused(str(rc))
            return
        c.subscribe(f'device/{serial}/report')
        c.publish(f'device/{serial}/request', json.dumps({'pushing': {'sequence_id': '0', 'command': 'pushall'}}))
    def on_message(c, userdata, msg):
        try: d = json.loads(msg.payload)
        except ValueError: return
        if isinstance(d.get('print'), dict): on_report(d['print'])
    try: c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except AttributeError: c = mqtt.Client(client_id=client_id)
    c.username_pw_set('bblp', code)
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE      # the printer signs its own certificate
    c.tls_set_context(ctx); c.on_connect = on_connect; c.on_message = on_message
    try: c.connect(ip, 8883, keepalive=30)
    except OSError as e: raise PrinterError(f'no connection to {ip} ({e.__class__.__name__})')
    c.loop_start()
    return c

def read_status(ip, serial, code, wait=10.0):
    """The printer's full status, read once."""
    state, lock, full, refused = {}, threading.Lock(), threading.Event(), []
    def on_report(d):
        with lock: merge(state, d)
        if 'gcode_state' in state: full.set()
    # a name of its own for every link: a printer drops the older of two links with one name, and checks do overlap
    c = printer_link(ip, serial, code, f'bambu-filament-guard-{os.getpid()}-{next(LINK_NUMBER)}', on_report, lambda rc: (refused.append(rc), full.set()))
    try:
        if not full.wait(wait): raise PrinterError(f'{ip} did not report its status')
        if refused: raise PrinterError(f'{ip} refused the access code Bambu Studio holds for it')
        for tenth in range(30):                           # the H2D sends its report in pieces: a second at least, three at most, for the spools
            time.sleep(0.1)
            with lock:
                if tenth >= 9 and 'ams' in state: break
        with lock: return json.loads(json.dumps(state))
    finally:
        c.loop_stop(); c.disconnect()

def grams_left(t):
    """Grams on a spool, or None when the printer does not know. Newer firmware reports grams (remain_g); older reports
    a percentage of the spool weight; a spool without a tag and without a measured amount reports -1 or 0 of 0."""
    def num(key):
        try: return float(t.get(key))
        except (TypeError, ValueError): return -1.0
    g, pct, weight = num('remain_g'), num('remain'), num('tray_weight')
    if g > 0: return g
    if weight > 0 and pct >= 0: return weight * pct / 100.0
    return None

def spool_id(t):
    """A spool's own id (its RFID tag) from a tray of the printer's status, or None: blank or all zeros is no tag."""
    for key in ('tray_uuid', 'tag_uid'):
        v = str(t.get(key) or '').strip()
        if v and set(v) != {'0'}: return v
    return None

def trays_of(status):
    """([{'label', 'type', 'fid', 'colour', 'name', 'grams', 'weight', 'nozzle', 'spool'}], two_nozzles) for every loaded
    spool. weight is the full spool's filament in grams (tray_weight), None when the printer does not know it; spool the
    spool's tag (spool_id).
    label is the tray as Bambu Studio shows it (A1..D4, HT-A, Ext). nozzle is the one the tray's AMS feeds, where the
    printer has two: the low half of the top byte of the AMS 'info' word, 1 = left, 0 = right (H2D, firmware 01.04);
    the tray each nozzle has loaded confirms it. None = not known, and then the tray counts for either nozzle."""
    extruders = status.get('device', {}).get('extruder', {}).get('info', []) or []
    two = len(extruders) > 1
    side = {}
    for a in status.get('ams', {}).get('ams', []) or []:
        try: side[int(a['id'])] = 'left' if (int(a.get('info', ''), 16) >> 24) & 0xF == 1 else 'right'
        except (ValueError, KeyError): pass
    for e in extruders:
        for key in ('snow', 'spre'):
            v = e.get(key)
            if isinstance(v, int) and (v >> 8) < 0xFE and (v & 0xFF) < 0x10: side[v >> 8] = 'right' if e.get('id') == 0 else 'left'
    out = []
    def add(label, t, nozzle):
        if not t.get('tray_type'): return
        try: weight = float(t.get('tray_weight')) or None
        except (TypeError, ValueError): weight = None
        out.append({'label': label, 'type': t['tray_type'], 'fid': t.get('tray_info_idx', ''), 'colour': '#' + (t.get('tray_color') or '808080')[:6].upper(),
                    'name': t.get('tray_sub_brands', ''), 'grams': grams_left(t), 'weight': weight, 'nozzle': nozzle if two else None, 'spool': spool_id(t)})
    for a in status.get('ams', {}).get('ams', []) or []:
        aid = int(a.get('id', 0))
        for t in a.get('tray', []) or []:
            label = f'HT-{chr(65 + aid - 128)}' if aid >= 128 else f"{chr(65 + aid)}{int(t.get('id', 0)) + 1}"
            add(label, t, side.get(aid))
    external = status.get('vir_slot') or ([status['vt_tray']] if isinstance(status.get('vt_tray'), dict) else [])
    for i, t in enumerate(external): add('Ext' if i == 0 else f'Ext{i + 1}', t, None)
    return out, two


# ---------------------------------------------------------------- need against left

def colour_distance(a, b):
    """0 (same) .. 765: the "redmean" distance, which follows the eye closer than plain RGB distance."""
    try: r1, g1, b1 = (int(a[i:i + 2], 16) for i in (1, 3, 5)); r2, g2, b2 = (int(b[i:i + 2], 16) for i in (1, 3, 5))
    except ValueError: return 765.0
    rm = (r1 + r2) / 2.0
    return (((512 + rm) * (r1 - r2) ** 2) / 256 + 4 * (g1 - g2) ** 2 + ((767 - rm) * (b1 - b2) ** 2) / 256) ** 0.5

def assess(fil, trays, cfg, times=1):
    """One filament of the print against the loaded spools.
    OK       one spool holds the need plus the reserve (see DEFAULTS)
    TIGHT    one spool holds the need, without the reserve
    SPLIT    no single spool holds it; matching spools that AMS auto refill goes on between do together (same_filament,
             both in an AMS)
    SHORT    the matching spools together hold less than the need, or hold it only with a spool the AMS does not go
             on to by itself (another product or shade, the external spool): those are named, with their amounts
    MISSING  no spool of this material is loaded where the print's nozzle can reach it
    COLOUR   the material is loaded, this colour of it is not: the nearest spool is named, and counts for nothing
    UNKNOWN  the matching spool reports no remaining amount, or Bambu Studio gives the filament no weight (parse_plate)
    Matching: same material type, on the nozzle that prints it, of the project's colour. Of the project's own product
    while a spool of it is loaded; otherwise of any product, since that spool can be picked at "Send print job", and
    the note then names it, whatever the answer."""
    need = fil['grams'] * times if fil['grams'] is not None else None
    row = {'slot': fil['slot'], 'type': fil['type'], 'name': fil['name'], 'colour': fil['colour'], 'nozzle': fil['nozzle'], 'need': need,
           'trays': [], 'best': None, 'total': None, 'note': ''}
    same = [t for t in trays if t['type'].upper() == fil['type'].upper()]
    reach = [t for t in same if not fil['nozzle'] or t['nozzle'] in (None, fil['nozzle'])]
    if not reach:
        row['status'] = 'MISSING'
        row['note'] = (f"{fil['type']} is loaded only for the other nozzle ({', '.join(t['label'] for t in same)})" if same else f"no {fil['type']} is loaded")
        return row
    near = [t for t in reach if colour_distance(t['colour'], fil['colour']) <= cfg['colour_tolerance']]
    if not near:                                             # the nearest spool does not stand in: blue once read "enough" on a terracotta spool
        t = min(reach, key=lambda t: colour_distance(t['colour'], fil['colour']))
        row['status'] = 'COLOUR'
        row['note'] = (f"no {fil['type']} of this colour is loaded; the nearest is {t['label']} {t['name'] or t['type']} ({t['colour']})"
                       + (f", {t['grams']:.0f} g" if t['grams'] is not None else ''))
        return row
    if need is None:                                         # nothing to hold against the spools: not "enough", whatever they hold
        row['status'] = 'UNKNOWN'
        row['note'] = 'Bambu Studio gives no weight for this filament' + (" (its profile's density is 0)" if fil.get('density') == 0 else '')
        return row
    group = [t for t in near if fil['fid'] and t['fid'] == fil['fid']] or near
    group.sort(key=lambda t: -(t['grams'] or 0))
    row['sliced'] = need                                     # what the slicer says; the need below never falls under it
    # What was learned of the trays (guard_learn): the largest rate and loss before layer 1 of the matching spools, as
    # which of them the print draws on first cannot be read; so learning another tray first never lowers the need.
    rho, pre = max(group, key=lambda t: t.get('rho') or 0), max(group, key=lambda t: t.get('pre') or 0)
    need = row['need'] = need * max(1.0, rho.get('rho') or 1.0) + (pre.get('pre') or 0) * times
    row['trays'] = [{'label': t['label'], 'grams': t['grams'], 'colour': t['colour'], 'name': t['name'], 'type': t['type'], 'entered': t.get('entered'),
                     'stale': t.get('stale'), 'reads': t.get('reads'), 'reserved': t.get('reserved'), 'credited': t.get('credited'), 'evidence': t.get('evidence'),
                     'pre': t.get('pre'), 'owes': t.get('owes'), 'refills': t.get('refills'), 'beyond': t.get('beyond'), 'reading': t.get('reading'),
                     'spool': t.get('spool'), 'fid': t.get('fid'), 'weight': t.get('weight')} for t in group]
    known = [t['grams'] for t in group if t['grams'] is not None]
    unknown = [t['label'] for t in group if t['grams'] is None]
    other = [t for t in group if fil['fid'] and t['fid'] and t['fid'] != fil['fid']]     # no spool of the project's product is loaded: these count in its place (a spool that names no product is not called another)
    stands_in = (' and '.join(f"{t['label']} is {t['name'] or t['type']}" for t in other) + f", another product than the project's {fil['name'] or fil['type']}") if other else ''
    if not known:
        stale = [t['label'] for t in group if t.get('stale')]
        row['status'] = 'UNKNOWN'
        row['note'] = (f"the amount entered for {', '.join(stale)} is out of date: a print the guard could not count has drawn on it" if stale
                       else f"{', '.join(unknown)} reports no remaining amount") + (f'; {stands_in}' if stands_in else '')
        return row
    row['best'], row['total'] = max(known), sum(known)
    # The reserve is a share of the largest full weight among the spools the filament could be printed from, not of the
    # fullest spool's: which spool is the fullest changes with what was learned of the trays, and the reserve must never
    # be smaller for that. (A 1 kg spool counted lower by another tray's run-dry once left a 250 g spool the fullest,
    # with 7.5 g for a reserve where 1.3.0 asked 30 g.) The spools are the same whatever was learned, so their largest is.
    # A spool whose full weight the printer does not report counts as a 1 kg spool, and so does one whose amount was
    # entered by hand: that figure rests on a kitchen scale and a typed empty-spool weight, and its "full weight" is
    # only the amount first entered. Either had nothing of the spool for a reserve, or 3 % of what was typed.
    full = max(1000.0 if t.get('entered') or not t.get('weight') else t['weight'] for t in group)
    reserve = max(need * cfg['margin_pct'] / 100.0, full * cfg['spool_reserve_pct'] / 100.0)
    # Measured from the guard's own past forecasts (guard_learn), within its floor. In use ('margin': the setting on, and
    # earned) it replaces the fixed reserve and may be smaller. Otherwise ('floor') it can only raise the fixed one:
    # where past prints fell further short than the fixed reserve covers, the answer must not be the looser of the two.
    fixed, floors = reserve, [t['floor'] for t in group if t.get('floor') is not None]
    if group[0].get('margin') is not None or floors:
        import guard_learn
        floor_g, floor_share = guard_learn.RESERVE_FLOOR
        measure = lambda q: max(floor_g, floor_share * need, q * (row['sliced'] + guard_learn.MARGIN_K))
        reserve = measure(group[0]['margin']) if group[0].get('margin') is not None else max(fixed, measure(max(floors)))
    row['reserve'], row['measured'], row['raised'] = reserve, group[0].get('margin') is not None, group[0].get('margin') is None and reserve > fixed
    # Two spools hold it together only where the AMS goes on from one to the other by itself: the spools of one filament
    # (same_filament), both in an AMS. The sum of any others once read "AMS auto refill must be on" across an AMS tray
    # and the external spool, and across two products 31 apart in colour.
    in_ams = lambda t: t['grams'] is not None and not t['label'].startswith('Ext')
    chain = max(([t] + [o for o in group if in_ams(t) and in_ams(o) and same_filament(t, o)] for t in group if t['grams'] is not None), key=lambda c: sum(t['grams'] for t in c))
    reach, apart = sum(t['grams'] for t in chain), [t for t in group if t['grams'] and t not in chain]
    if row['best'] >= need + reserve: row['status'] = 'OK'
    elif row['best'] >= need: row['status'] = 'TIGHT'; row['note'] = (f"{row['best'] - need:.0f} g to spare, and " + (
        f"past prints put the figure up to {reserve:.0f} g out" if row['measured'] or row['raised'] else f"the AMS's figure can be {reserve:.0f} g out"))
    elif reach >= need:
        row['status'] = 'SPLIT'
        row['note'] = f"only across {'these spools' if len(chain) == len(group) else ' + '.join(t['label'] for t in chain)}: AMS auto refill must be on"
    elif row['total'] >= need:
        row['status'] = 'SHORT'
        row['note'] = (f"short by {need - reach:.0f} g; AMS auto refill does not go on from {' + '.join(t['label'] for t in chain)} to "
                       + ', '.join(f"{t['label']} ({t['grams']:.0f} g)" for t in apart))
    else: row['status'] = 'SHORT'; row['note'] = f"short by {need - row['total']:.0f} g"
    if unknown and row['status'] in ('SHORT', 'SPLIT', 'TIGHT'): row['note'] += f"; {', '.join(unknown)} holds an unknown amount more"
    said = [x for x in (tray_note(t) for t in group) if x]
    if rho.get('rho'): said.append(f"{rho['label']}: {rho['rho_note']}, which the need is counted by")
    if pre.get('pre'): said.append(f"{pre['label']} {pre['pre_note']}, which is added to the need")
    if said: row['note'] += ('; ' if row['note'] else '') + '; '.join(said)
    entered = [t for t in group if t.get('entered')]         # a spool the printer cannot measure: the amount is the user's, less the prints since
    if entered:
        row['note'] += ('; ' if row['note'] else '') + ', '.join(f"{t['label']} as entered on {time.strftime('%d %b', time.localtime(t['entered']))}" for t in entered) + ', less the prints since'
    alike = [t for t in near if t not in group]              # the same colour to the eye, another product: the user's call, so named, not counted
    if alike and row['status'] in ('SHORT', 'SPLIT', 'TIGHT'):
        row['note'] += '; similar colour: ' + ', '.join(f"{t['label']} {t['name'] or t['type']}" + (f" {t['grams']:.0f} g" if t['grams'] is not None else '') for t in alike)
    if stands_in: row['note'] += ('; ' if row['note'] else '') + stands_in
    return row

def tray_note(t):
    """What became of a tray's amount before it was judged, in words: counted lower for what was learned of the tray, the
    running print's share taken off, what that print needs beyond the tray and where it comes from. Under 1 g it goes
    unsaid."""
    learned = t.get('credited') is not None and round(t['credited']) < round(t['reads'])
    owes, own, given = t.get('owes') or 0.0, t.get('reserved') or 0.0, sum(g for _, g in t.get('refills') or [])
    if not (learned or round(owes) or round(given)): return ''
    text = f"{t['label']} " + (f"holds {t['reads']:.0f} g as entered" if t.get('entered') else f"reads {t['reads']:.0f} g")
    if learned: text += f", counted as {t['credited']:.0f} g: {t['evidence']}"
    if round(owes - own):                                    # the running print needs more than the tray holds: the AMS goes on from another spool
        took = [(label, g) for label, g in t.get('taken') or [] if round(g)]
        text += (f"; the running print still needs {owes:.0f} g: {owes - own:.0f} g more than it holds"
                 + (', taken off ' + ' and '.join(f'{label}' if len(took) == 1 else f'{label} ({g:.0f} g)' for label, g in took) if took else '')
                 + (f"; {t['beyond']:.0f} g more than the other spools of this filament hold" if took and round(t.get('beyond') or 0) else
                    ', and no other spool of this filament is loaded' if round(t.get('beyond') or 0) else ''))
    elif round(own): text += (f"; the running print still needs {own:.0f} g of it" if learned else f", of which the running print still needs {own:.0f} g")
    if round(given):
        text += f"{'; and' if learned or round(owes) else ', of which'} the running print takes {given:.0f} g once " + ' and '.join(label for label, _ in t['refills']) + ' runs out'
    return text

def same_filament(a, b):
    """Two loaded spools the AMS goes on with, one after the other, when one runs out (auto refill): the same material
    and colour, the same product where both name one, for the same nozzle (or one that may feed either). Used for
    what the running print needs beyond its own tray; for the print checked, assess's looser match decides."""
    return (a['label'] != b['label'] and a['type'].upper() == b['type'].upper() and a['colour'] == b['colour']
            and (not a['fid'] or not b['fid'] or a['fid'] == b['fid']) and (a.get('nozzle') is None or b.get('nozzle') is None or a['nozzle'] == b['nozzle']))

def credit(trays, owed, learned=None, running=None, order=None):
    """What each tray is counted with, for the moment the checked print could start: its reading, corrected by what was
    learned of it (learned: guard_learn.corrections; only ever lower, and never an amount entered by hand), less what the
    print running now still needs of it (owed).
      - An amount entered by hand has the running print taken off it like a reading until guard_ledger has done so
        itself (the entry's 'accounted' job is the running one, `running`, and nothing was 'missed').
      - What the running print needs beyond a tray's amount comes, with AMS auto refill, from the loaded spools of the
        same filament (same_filament), the fullest first: it is taken off them ('taken' on the tray, 'refills' on
        theirs); what none of them holds is 'beyond' (assess and the card say so).
    Keeps the amount before any of this ('reads') whenever something was corrected or taken off. The spools a print
    goes on with are taken in the order they would be without learning (`order`), so that no tray is ever counted
    higher for what was learned of another."""
    if learned: order = {t['label']: t.get('left', t['grams']) for t in credit([dict(t) for t in trays], owed, None, running)}
    for t in trays:
        if t['grams'] is None or t.get('stale'): continue
        raw, share, fix = t['grams'], owed.get(t['label'], 0.0), {}
        if not t.get('entered'): fix = (learned or {}).get(t['label'], {})
        elif not running or (t.get('accounted') == running and t.get('missed') != running): share = 0.0
        base = fix.get('credited', raw)
        t['reading'] = raw
        if 'credited' in fix: t['credited'], t['evidence'] = base, fix['evidence']
        for key in ('pre', 'rho', 'margin', 'floor'):
            if fix.get(key) is not None: t[key], t[key + '_note'] = fix[key], fix[key + '_note']
        if share or base < raw: t['reads'] = raw
        if share: t['owes'], t['reserved'] = share, min(base, share)
        t['grams'] = t['left'] = base - t.get('reserved', 0.0)
    order = order or {t['label']: t['left'] for t in trays if 'left' in t}
    for t in trays:
        excess = (t.get('owes') or 0.0) - (t.get('reserved') or 0.0)
        if excess <= 1e-6: continue
        for o in sorted((o for o in trays if 'left' in o and same_filament(t, o)), key=lambda o: -order.get(o['label'], 0.0)):
            g = min(o['grams'], excess)
            if g <= 0: continue
            o.setdefault('reads', o['grams'])                         # not yet set: nothing was taken off it, so this is its amount
            o['grams'] -= g; excess -= g
            o.setdefault('refills', []).append((t['label'], g)); t.setdefault('taken', []).append((o['label'], g))
            if excess <= 1e-6: break
        if excess > 1e-6: t['beyond'] = excess
    return trays

def pick_printer(model, printers):
    """The printer a plate is checked against: of the known printers of the model it was sliced for, the one selected
    in Bambu Studio (known_printers lists it first)."""
    return next((p for p in printers if p['model'] == model), None)

def share_spools(rows, filaments, trays, cfg, times):
    """Filaments that can draw on a spool in common are judged on what they need together: each alone can fit a spool
    that does not hold both. Two slots of one colour share all their spools; one spool is enough to share (a spool that
    stands in for another product, the external spool for both nozzles, two shades within the tolerance of one spool).
    Which of them the print takes from the shared spool cannot be read, so each filament is judged against its own
    spools with the need of every filament it shares one with added."""
    labels = [{t['label'] for t in row['trays']} for row in rows]
    for i, (row, fil) in enumerate(zip(rows, filaments)):
        with_it = [n for n in range(len(rows)) if n == i or labels[n] & labels[i]]
        if len(with_it) < 2: continue
        total = sum(filaments[n]['grams'] for n in with_it)
        joint = assess(dict(fil, grams=total), trays, cfg, times)
        shared = [t['label'] for t in row['trays'] if any(t['label'] in labels[n] for n in with_it if n != i)]
        if SEVERITY[joint['status']] > SEVERITY[row['status']]: row['status'] = joint['status']
        row['note'] = (f"filaments {', '.join(str(rows[n]['slot']) for n in with_it)} share {' + '.join(shared)} and need {total * times:.0f} g together"
                       + (f": {joint['note']}" if joint['note'] else ''))

def mark_looked(serial):
    """Notes in the state file that a check looked at the printer (learning follows it, guard_ledger.followed); not again
    within a minute, which would change nothing. A state file that cannot be written now only delays the following."""
    try:
        if time.time() - load_state().get('looked', {}).get(serial, 0) > 60: update_state(lambda st: st.setdefault('looked', {}).__setitem__(serial, time.time()))
    except OSError: pass

def evaluate(plate, cfg, title='', times=1, seen=None):
    """{'title', 'model', 'time', 'times', 'plate', 'printer', 'rows', 'worst', 'error'}: the whole answer for one sliced plate.
    seen: a dict that carries the printers' status from one call to the next, for several plates checked in one go."""
    res = {'title': title, 'model': plate['printer_model'], 'time': plate['time'], 'times': times, 'plate': plate, 'printer': None, 'rows': [], 'worst': 'OK', 'error': None}
    if all(f['grams'] is None for f in plate['filaments']):  # no row to judge: the worst of none once read "Enough filament", exit code 0
        res['error'] = 'this plate uses no filament the guard can weigh'
        return res
    try:
        conf = bs_conf()
        printer = pick_printer(plate['printer_model'], known_printers(conf))
        if not printer:                      # sliced for a printer that is not on this LAN (or not a Bambu printer): nothing to ask, so no card
            res['quiet'] = True
            raise PrinterError(f"Bambu Studio holds no LAN access code for a {plate['printer_model'] or 'printer of this kind'}")
        status = (seen or {}).get(printer['serial'])
        if status is None:
            ip = printer_ip(printer['serial'], cfg)
            if not ip: raise PrinterError(f"the {printer['model']} is not announcing itself on this network (is it on?)")
            code = conf['access_code'][printer['serial']]
            try: status = read_status(ip, printer['serial'], code)
            except PrinterError:
                new_ip = printer_ip(printer['serial'], cfg, refresh=True)       # the printer may have a new address
                if not new_ip or new_ip == ip: raise
                status = read_status(new_ip, printer['serial'], code)
            if seen is not None: seen[printer['serial']] = status
        if cfg.get('learn'): mark_looked(printer['serial'])
    except (PrinterError, OSError, ValueError, KeyError) as e:
        res['error'] = str(e) if isinstance(e, PrinterError) else f'{e.__class__.__name__}: {e}'
        return res
    try:
        import guard_ledger
        trays, _ = trays_of(status)
        guard_ledger.apply(trays, printer['serial'], 'ams' in status)   # spools the printer cannot measure get the amount entered for them
        # The answer is for the moment this print could start: what the print running now still needs is not there for
        # it (see credit: an entered amount the ledger has not yet taken it off included).
        owed, counted = guard_ledger.owed(status, load_state().get('slices', []))
        running = guard_ledger.job_key(status) if status.get('gcode_state') in BUSY else None
        learned = reserve = unlearned = None
        if cfg.get('learn'):
            import guard_learn
            try: history = guard_learn.load(); learned = guard_learn.corrections(printer['serial'], trays, history, cfg)
            except Exception as e:           # what was learned may make a check stricter, never cost it: the check goes on as with learning off, and says so
                unlearned = f'what was learned from previous prints could not be used ({e.__class__.__name__}): this check is as without it'
                log(f'learning: {e.__class__.__name__}: {e}: this check is as with learning off')
        credit(trays, owed, learned, running)
        rows = [assess(f, trays, cfg, times) for f in plate['filaments']]
        share_spools(rows, plate['filaments'], trays, cfg, times)
        if cfg.get('learn') and not unlearned:
            reserve = guard_learn.reserve_words(printer['serial'], rows, history, cfg) or None
            offer = guard_learn.weigh_offer(printer['serial'], rows, cfg, busy=status.get('gcode_state') in BUSY)
            if offer: res['weigh'] = offer
    except Exception as e:                   # a status the guard does not understand must end as a card that says so, not as a line in the log
        res['error'] = f"the {printer['model']}'s report could not be worked out ({e.__class__.__name__}: {e})"
        return res
    res['printer'] = {'model': printer['model'], 'serial': printer['serial'], 'state': status.get('gcode_state', '?'), 'job': status.get('subtask_name', ''),
                      'percent': status.get('mc_percent'), 'layer': status.get('layer_num'), 'layers': status.get('total_layer_num'), 'counted': counted,
                      'beyond': {t['label']: t['beyond'] for t in trays if round(t.get('beyond') or 0)}, 'reserve': reserve, 'unlearned': unlearned}
    res['rows'] = rows
    res['worst'] = max((r['status'] for r in rows), key=SEVERITY.get, default='OK')
    return res

def headline(res):
    if res['error']: return 'Filament could not be checked'
    sev = SEVERITY[res['worst']]
    if sev == 4 and not any(r['status'] == 'SHORT' for r in res['rows']): return 'A filament of this print is not loaded'
    return {0: 'Enough filament for this print', 1: 'Enough filament could not be confirmed', 2: 'Filament is tight for this print',
            3: 'No single spool holds enough filament', 4: 'Not enough filament for this print'}[sev]

STATUS_WORD = {'OK': 'enough', 'TIGHT': 'tight', 'SPLIT': 'two spools', 'SHORT': 'NOT ENOUGH', 'MISSING': 'NOT LOADED', 'COLOUR': 'COLOUR NOT LOADED', 'UNKNOWN': 'unknown'}

def tray_text(row):
    return ' + '.join(t['label'] for t in row['trays']) or '-'

def need_text(row):
    return f"{row['need']:.1f} g" if row['need'] is not None else '?'

def has_text(row):
    if row['best'] is None: return '?' if row['trays'] else '-'
    return f"{row['best']:.0f} g" + (f" ({row['total']:.0f} g in all)" if row['total'] > row['best'] else '')

def job_text(p):
    """The printer's job and where it is, in words: " (Benchy, layer 11 of 27)"; "preparing" when the layer shown may be
    the job before's; the percentage without a layer; the name alone without either; '' without a name too."""
    if p['state'] == 'PREPARE': stage = 'preparing'
    elif isinstance(p.get('layer'), int) and p['layer'] >= 1: stage = f"layer {p['layer']}" + (f" of {p['layers']}" if p.get('layers') else '')
    else: stage = f"{p['percent']} %" if p.get('percent') is not None else ''
    words = ', '.join(x for x in (p.get('job'), stage) if x)
    return f' ({words})' if words else ''

def beyond_text(p):
    """The running print's own shortfall, in words: what it needs beyond every loaded spool of a tray's filament."""
    return ''.join(f"; it needs {g:.0f} g more of {label}'s filament than the loaded spools hold" for label, g in (p.get('beyond') or {}).items())

def report_text(res):
    lines = [headline(res) + (f": {res['title']}" if res['title'] else '') + (f" x {res['times']}" if res['times'] != 1 else '')]
    if res['error']: return lines[0] + '\n  ' + res['error']
    p, busy = res['printer'], ''
    if p['state'] in BUSY:
        busy = f"{job_text(p)}: " + ('the running print is counted: what it still needs is taken off its spools' if p.get('counted') else
                                                    "its spools are still being used, and the guard could not count that print: the amounts are the spools' amounts now")
        busy += beyond_text(p)
    lines.append(f"  {p['model']}, {p['state']}" + busy + ''.join(f'; {p[key]}' for key in ('reserve', 'unlearned') if p.get(key)))
    for r in res['rows']:
        lines.append(f"  {r['slot']:>2} {r['type']:<8} {r['colour']} {r['name'][:22]:<22} needs {need_text(r):>9}   {tray_text(r):<10} has {has_text(r):<22} {STATUS_WORD[r['status']]}"
                     + (f"  ({r['note']})" if r['note'] else ''))
    return '\n'.join(lines)

def exit_code(res):
    if res['error']: return 3
    return {0: 0, 1: 1, 2: 1, 3: 1, 4: 2}[SEVERITY[res['worst']]]


# ---------------------------------------------------------------- the watcher

def temp_name(gcode_path):
    return re.match(r'\.(\d+)\.(\d+)\.gcode$', os.path.basename(gcode_path))

def studio_pid(gcode_path):
    """The process id of the Bambu Studio copy that sliced a temporary G-code: it is in the file's name, .<pid>.<n>.gcode."""
    m = temp_name(gcode_path)
    return int(m.group(1)) if m else None

def plate_number(gcode_path):
    """The plate a temporary G-code belongs to, counted from 1 as Bambu Studio shows it (the name counts from 0)."""
    m = temp_name(gcode_path)
    return int(m.group(2)) + 1 if m else None

def newest_gcode(pid=None):
    """The temporary G-code written last (by the Bambu Studio copy with this process id, when one is given), or None."""
    files = [p for p in temp_gcodes() if pid is None or studio_pid(p) == pid]
    try: return max(files, key=os.path.getmtime) if files else None
    except OSError: return None

def plate_at(path):
    """The plate of a G-code file, or None while it cannot be read."""
    try:
        with open(path, 'rb') as f: return parse_plate(read_head(f))
    except (OSError, ValueError): return None

def printer_for(plate):
    """The printer a plate is checked against, as Bambu Studio's configuration knows it (the printer is not asked), or None."""
    try: return pick_printer(plate['printer_model'], known_printers(bs_conf())) if plate else None
    except (OSError, ValueError, KeyError): return None

def checking_text(printer):
    return f"Checking the spools on the {printer['model']}…" if printer else 'Checking the spools…'

def studio_version():
    try: return str(bs_conf().get('app', {}).get('version', ''))
    except (OSError, ValueError): return ''

def profile_name(title, plate_no, plate):
    """The file that holds a slice's per-layer profile, named from the slice: its title, plate and grams."""
    key = json.dumps([title, plate_no, [[f['slot'], round(f['grams'], 2)] for f in plate['filaments']]])
    return hashlib.sha1(key.encode('utf-8')).hexdigest()[:16] + '.json'

def load_profile(entry, part='layers'):
    """One part of a remembered slice's profile (see layer_profile), by slot: 'layers' [grams by layer], 'purge' or
    'pushed'. None: it has none, or the file is gone."""
    if not entry or not entry.get('profile'): return None
    try: return {int(k): v for k, v in read_json(os.path.join(STATE_DIR, 'profiles', os.path.basename(entry['profile'])), {})[part].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError): return None

def remember_slice(title, plate_no, plate, profile=None):
    """Notes a slice that was read: which Bambu Studio version wrote it (the self-test reports that), what it needs
    (guard_ledger counts a print down from it when the printer starts a job of that name) and, when given, its per-layer
    profile (what a print of it still needs while it runs). A profile is a file of its own in profiles/, because the
    state file is read and written whole at every change: its parts as layer_profile gives them, by slot and layer.
    It goes with its slice, when a re-slice of the same plate replaces it or it drops out of the 30 kept."""
    version, folder = studio_version(), os.path.join(STATE_DIR, 'profiles')
    name = profile_name(title, plate_no, plate) if profile else None
    with state_lock():
        if name:
            try:
                kept = {part: {str(s): [round(g, 4) for g in grams] for s, grams in by_slot.items()} for part, by_slot in profile.items()}
                write_json(os.path.join(folder, name), kept, separators=(',', ':'))
            except OSError as e:                                     # another program holding the old file: the slice counts, without a profile
                log(f'{title}, plate {plate_no}: its per-layer profile could not be kept ({e.__class__.__name__}): a print of it is not counted while it runs'); name = None
        def change(st):
            st['verified'] = {'version': version, 'at': time.time()}
            mine = {'title': title, 'plate': plate_no, 'layers': plate.get('layers'), 'at': time.time(),
                    'filaments': [{'slot': f['slot'], 'grams': f['grams']} for f in plate['filaments']]}
            if name: mine['profile'] = name
            st['slices'] = ([mine] + [s for s in st.get('slices', []) if (s['title'], s.get('plate')) != (title, plate_no)])[:30]
            return {s.get('profile') for s in st['slices']}
        kept = update_state(change)
        for old in (os.listdir(folder) if os.path.isdir(folder) else []):
            if old.endswith('.json') and old not in kept:
                try: os.remove(os.path.join(folder, old))
                except OSError: pass

class Watcher:
    """Finds each newly sliced plate once: a temporary G-code that has stopped changing and carries its totals.
    problems collects (path, why) for files Bambu Studio has finished and the guard cannot read."""
    def __init__(self, skip_existing=True):
        self.done, self.waiting, self.problems = {}, {}, []
        if skip_existing:
            for p in temp_gcodes(): self.done[p] = self.key(p)
    @staticmethod
    def key(path):
        try: st = os.stat(path); return (st.st_mtime_ns, st.st_size)
        except OSError: return None
    def new_plates(self):
        out = []
        for path in temp_gcodes():
            key = self.key(path)
            if key is None or self.done.get(path) == key: continue
            if self.waiting.get(path) != key or time.time() - key[0] / 1e9 < 2.0:
                self.waiting[path] = key; continue                               # still being written: look again next round
            try:
                with open(path, 'rb') as f: plate = parse_plate(read_head(f))
            except OSError: continue                                             # Bambu Studio has it open: look again next round
            except ValueError as e:
                self.done[path] = key; self.problems.append((path, str(e))); continue
            if plate is None:                                                    # totals not written yet; after three quiet minutes they will not come
                if time.time() - key[0] / 1e9 > 180: self.done[path] = key; self.problems.append((path, 'it carries no filament totals'))
                continue
            self.done[path] = key; self.waiting.pop(path, None)
            out.append((path, plate))
        return out


SEND_MOST = 30                                            # the plates of a project checked again when "Send print job" opens: the ones sliced last
WEIGH_MOVED = 2.0                                         # grams a tray's reading may have moved since its card for a weigh-in to be kept against it
IGNORE_DAYS, IGNORE_GROWTH = 7, 5.0                       # "Ignore for this project" ends after as many days, or with a filament as many grams further short
UNSAVED = 'Unsaved project'                               # what project_title calls every project that was never saved

def plate_key(res):
    """The plate an answer is for: Bambu Studio's process, the project's title, the plate's number."""
    return (res.get('pid'), res['title'], res.get('plate_no'))

def written(path):
    """When a file was last written; 0 for one that has gone meanwhile."""
    try: return os.path.getmtime(path)
    except OSError: return 0.0

def ignore_key(res):
    """(project, plate) under which "Ignore for this project" is kept for an answer, both as the state file names them;
    None when there is nothing to tell the project by. A project that was never saved shares its title with every other
    such project (one ignored at 5 g short muted them all): it is told by the session folder of its G-code, and not at
    all when the answer comes from no temporary G-code (`check --popup`)."""
    project = res['title'] if res['title'] != UNSAVED else os.path.dirname(os.path.dirname(res['gcode'])) if res.get('gcode') else None
    return (project, str(res.get('plate_no'))) if project else None

def shortfalls(res):
    """What an ignore accepts of an answer: per row its slot, its result and the grams it falls short by (the need less
    what its spools hold; 0 when they hold it)."""
    return [[r['slot'], r['status'], round(max(0.0, (r['need'] or 0.0) - (r['total'] or 0.0)), 1)] for r in res['rows']]   # no need: a filament without a weight (parse_plate)

def ignore_lasts(entry):
    """Whether an ignore kept in the state file ({'at', 'rows': shortfalls}) is still in its IGNORE_DAYS. 1.4 kept a bare
    string, undated: that has lapsed."""
    return isinstance(entry, dict) and isinstance(entry.get('at'), (int, float)) and 0 <= time.time() - entry['at'] <= IGNORE_DAYS * 86400

def ignore_covers(entry, res):
    """Whether a kept ignore covers an answer: it lasts, the rows and their results are the ones accepted, and no row is
    more than IGNORE_GROWTH further short than it was then."""
    if not ignore_lasts(entry): return False
    was, now = entry['rows'], shortfalls(res)
    return [r[:2] for r in was] == [r[:2] for r in now] and all(new[2] - old[2] <= IGNORE_GROWTH for old, new in zip(was, now))


class Guard:
    """The running guard: it shows every answer, and does what the card and the icon ask for. Everything that
    touches the card runs on the Tk thread, through `todo`; asking the printer takes seconds and runs on threads of
    its own. standing = the watcher (icon, Send dialog, entered amounts); otherwise one card, for `check --popup`."""
    def __init__(self, cfg, root, standing=True):
        import guard_ui
        self.cfg, self.root, self.standing = cfg, root, standing
        self.todo, self.stopping = queue.Queue(), threading.Event()
        self.last, self.paused, self.ticks, self.sending, self.jobs = None, False, 0, set(), {}
        self.warning = None                                   # the warning on the full card, while it stands: see standing_warning
        self.held, self.icon_shows = {}, None                 # the warnings that wait for that card to go, by plate (hold); what the icon was last given
        self.busy, self.greeted = {}, False                   # the checks someone waits for, by what started them: (their line, its text)
        self.told = set()                                     # what was said in the log once, and is not said again in this run
        self.watcher = None                                   # the thread that watches for slices, once it runs (watch_slices)
        self.card = guard_ui.Card(root, act=self.act, on_close=None if standing else root.quit)
        self.card.empty_spool = cfg['empty_spool_g']
        self.card.alone = not standing                        # no watcher behind this card: its amount window looks whether one runs
        self.tray = guard_ui.Tray(self) if standing else None

    # -- showing an answer
    def ignored(self, res):
        """Whether the answer's finding is ignored for its plate. What cannot be looked up is not: a state file held
        elsewhere at that moment once left a warning as a line in the log, with no card."""
        key = ignore_key(res)
        if not key: return False
        try:
            plates = load_state().get('ignored', {}).get(key[0])
            return isinstance(plates, dict) and ignore_covers(plates.get(key[1]), res)
        except OSError as e: log(f"{res['title']}: what is ignored could not be looked up ({e.__class__.__name__}: {e}): shown as not ignored"); return False
        except (TypeError, LookupError, AttributeError): return False   # a state, or an entry, no version of the guard wrote ignores nothing

    def popup_on(self):
        """The results popup_on names, in capitals: as the guard reads the list, and as the menu ticks it. A name that is
        no result to warn of is said in the log, once in a run; so is a value that is no list, read as the default."""
        on, known = self.cfg.get('popup_on'), [s for s in SEVERITY if s != 'OK']
        names = [str(s).upper() for s in on] if isinstance(on, list) else list(DEFAULTS['popup_on'])
        odd = ([f'names "{s}", which is no result to warn of ({", ".join(known)})' for s in names if s not in known] if isinstance(on, list)
               else ['is not a list of results: the default is used'])
        for what in odd:
            if what not in self.told: self.told.add(what); log(f'popup_on in config.json {what}')
        return names

    def opening(self, res):
        """The results among an answer's rows that open the full card. SHORT and MISSING do whatever popup_on holds: a list
        written by hand to add UNKNOWN replaced the whole default, and turned the "not enough" card off. The others do
        when popup_on names them; OK never does (its card would stand like a warning's, in every other answer's way).
        Any such row opens the card, not the worst row alone: COLOUR ranks with SHORT, and a COLOUR row that was not to
        be warned of hid the SHORT row after it."""
        on = (set(self.popup_on()) | {'SHORT', 'MISSING'}) - {'OK'}
        return [r['status'] for r in res['rows'] if r['status'] in on]

    def rank(self, res):
        """How much an answer asks to be seen, for choosing among a project's plates: (2, the severity of its worst row
        that opens the card) when it opens the card by itself, which an ignored one ('ignored', as set on it) does not;
        (1, 0) for a plate that could not be checked, which is not a fine one; (0, its severity) for the rest, a line at
        most, and (0, -1) for a plate sliced for a printer the guard has none to ask about."""
        if res['error']: return (0, -1) if res.get('quiet') else (1, 0)
        opening = [] if res.get('ignored') else self.opening(res)
        return (2, max(SEVERITY[s] for s in opening)) if opening else (0, SEVERITY[res['worst']])

    def on_icon(self):
        """The answer the icon stands for: the last one shown, or a warning that still waits to be shown (held) when that
        is the worse. So the icon is green only when no plate that was checked has a warning that was neither shown nor
        ignored."""
        waits = max(self.held.values(), key=self.rank, default=None)
        return waits if waits and (not self.last or self.rank(waits) > self.rank(self.last)) else self.last

    def last_text(self):
        res = self.on_icon()
        if self.paused: return 'Checks are paused'
        if not res: return 'No slice checked yet'
        return f"{headline(res)} · {res['title']}" + (f", plate {res['plate_no']}" if res.get('plate_no') else '')

    def show_icon(self):
        """The icon's colour and tooltip: those of the answer it stands for (on_icon); grey, and saying so, while checks
        are paused (an answer asked for from the icon then turned it green, as if slices were being checked)."""
        import guard_ui
        res = self.on_icon(); now = ('muted' if self.paused or not res else guard_ui.tone_of(res), self.last_text())
        if self.tray and now != self.icon_shows: self.icon_shows = now; self.tray.set(*now)
        if self.tray: self.tray.refresh()

    def standing_warning(self):
        """The warning whose card still stands: (its plate: Bambu Studio's process, the project, the plate number; its
        rank; its card's window), or None."""
        return self.warning if self.warning and self.card.is_open() and self.card.win is self.warning[2] else None

    def hold(self, res):
        """An answer that may not take a standing warning's place. A warning is kept, the latest of its plate, and shows
        when that card has gone (next_held): held back and dropped, it was never shown at all. A fine answer ends what
        was kept of its plate."""
        if self.rank(res)[0]: self.held[plate_key(res)] = res
        else: self.held.pop(plate_key(res), None)
        self.show_icon()

    def next_held(self):
        """Shows the worst of the warnings that waited, as the answer it is; its card says how old it is."""
        return self.deliver(self.held.pop(max(self.held, key=lambda plate: self.rank(self.held[plate]))))

    def show_held(self):
        """Every 0.4 s on the Tk thread: with no card in the way (the standing one closed by the user) and no check under
        way, the next warning that waited shows."""
        if self.held and not self.card.is_open() and not self.busy: self.next_held()

    def deliver(self, res, full=False, beside=None):
        """One answer: the full card for a warning (and whenever it is asked for), one line that goes away otherwise.
        beside: the "Send print job" dialog, next to which only a warning is worth a card. True when the full card shows.
        A warning's card that stands (a plate that could not be checked counts as one) gives way to a new answer for its
        own plate of its own Bambu Studio, to a worse answer, to a warning beside "Send print job" (every plate was
        checked again for it) and to what the user asks for; to nothing else. Another plate's fine answer, or another
        Bambu Studio's, once put a line that went after 8 seconds in its place and turned the icon green: now it does
        not show, and the icon keeps the warning's colour. Another plate's warning that may not take its place waits
        (hold), and shows when the standing card has gone."""
        res.setdefault('at', time.time())                     # when it was checked, where the check did not say (`check --popup`): a card that shows it later says so
        if not res['error']: res['ignored'] = self.ignored(res)
        rank, plate, stands = self.rank(res), plate_key(res), self.standing_warning()
        own = bool(stands) and stands[0] == plate
        if stands and not full and not own and (not rank[0] if beside else rank <= stands[1]): return self.hold(res)
        self.held.pop(plate, None); self.last = res; self.show_icon()
        if full or rank[0]:
            self.card.show(res, beside=beside, hold=not full)
            self.warning = (plate, rank, self.card.win) if rank[0] else None
            return True
        if own and self.held: self.card.close(); return self.next_held()   # its plate is fine now: the worst that waited behind it shows, not this plate's line
        if not beside and self.cfg['ok_seconds'] and not res['error']: self.card.show(res, brief=self.cfg['ok_seconds'], hold=True)
        elif own: self.card.close()                           # its plate is fine now and no line takes the card's place: it must not go on saying otherwise
        if own or not beside: self.warning = None

    # -- what the card and the icon ask for
    def later(self, work, then, otherwise=None):
        """work() on a thread of its own, then(its result) on the Tk thread; otherwise() there when work failed."""
        def run():
            try: out = work()
            except Exception as e:
                if otherwise: self.todo.put(otherwise)
                log(f'error: {e.__class__.__name__}: {e}')
                return
            self.todo.put(lambda: then(out))
        threading.Thread(target=run, daemon=True).start()

    def act(self, name, res, arg=None):
        import guard_ledger
        if name == 'recheck': self.recheck(res, self.card.beside)
        elif name == 'details': self.deliver(res, full=True)
        elif name == 'ignore':
            on, key = not res.get('ignored'), ignore_key(res)
            if not key: return
            def change(st):                                   # what has lapsed goes with every change: a session folder is never seen again
                kept = {p: {n: e for n, e in plates.items() if ignore_lasts(e)} for p, plates in st.get('ignored', {}).items() if isinstance(plates, dict)}
                if on: kept.setdefault(key[0], {})[key[1]] = {'at': time.time(), 'rows': shortfalls(res)}
                else: kept.get(key[0], {}).pop(key[1], None)
                st['ignored'] = {p: plates for p, plates in kept.items() if plates}
            update_state(change)
            log(f"{res['title']}" + (f", plate {res['plate_no']}" if res.get('plate_no') else '') + ': this finding is '
                + (f'ignored for {IGNORE_DAYS} days, unless it changes or grows by more than {IGNORE_GROWTH:g} g' if on else 'no longer ignored'))
            if on: self.card.close()
            else: self.deliver(res, full=True, beside=self.card.beside)
        elif name == 'spool':                                 # the empty spool's weight typed in the amount window: the next one starts with it
            if not arg or arg <= 0: return                    # 0 is the filament weighed alone, not a spool: as the next window's start it put 250 g too much on the next spool
            self.card.empty_spool = arg
            if arg != self.cfg.get('empty_spool_g'):
                try: self.keep('empty_spool_g', arg)          # the amount is stored by now, and a folder that cannot be written must not cost it
                except OSError as e: log(f"the empty spool's weight, {arg:g} g, could not be kept in config.json ({e.__class__.__name__}): it is used until the guard stops")
        elif name == 'amount':
            tray, grams = arg
            guard_ledger.enter(res['printer']['serial'], tray, grams); self.recheck(res, self.card.beside)
        elif name == 'asked':                                 # asked from within the card as it shows: a state file that cannot be written now must not
            import guard_learn                                # cost the card. The offer then may come again.
            try: guard_learn.asked(res['printer']['serial'], arg['label'])
            except OSError as e: log(f"{arg['label']}: that a weigh-in was offered could not be noted ({e.__class__.__name__}: {e}): it may be offered again")
        elif name == 'weigh': self.weigh_in(res, *arg)

    def weigh_in(self, res, tray, grams):
        """A weigh-in saved in its window: kept only against a reading the printer gives now. It was set against the reading
        of the card it came from, however old: C3 at 105 g on the card, a print, the spool weighed at 20 g, and the tray
        was kept as one that reads 105 g with 20 g on it, which counted every tray of the printer 85 g lower. The printer
        is asked again; the weigh-in is kept, against the reading it gives, when no job is on the printer and the tray
        holds the same spool (tag, material, colour) at a reading within WEIGH_MOVED of the one the window showed.
        Otherwise nothing is kept, and a card says why and offers the new check to weigh from."""
        import guard_ui, guard_learn
        label, beside = tray['label'], self.card.beside
        where = lambda: beside if guard_ui.visible(beside) else None
        def refuse(why):
            self.card.note('Weigh-in not saved', [(False, f'{why}: weigh-in not saved. Check again, then weigh from the new card.')], 'warn', res.get('pid'), where(),
                           buttons=[('Check again', lambda: self.recheck(res, where()))])
            log(f'{label}: weigh-in not saved: {why}')
        def then(new):
            now = None if new['error'] else next((t for r in new['rows'] for t in r['trays'] if t['label'] == label), None)
            if new['error']: why = f"The printer could not be asked what {label} reads now ({new['error']})"
            elif new['printer']['state'] in BUSY: why = f"The {new['printer']['model']} is printing, and {label}'s reading is moving"
            elif not now or now.get('reading') is None or (now.get('spool'), now['type'].upper(), now['colour']) != (tray.get('spool'), tray['type'].upper(), tray['colour']):
                why = f'{label} no longer holds the spool that card showed'
            elif abs(now['reading'] - tray['reading']) > WEIGH_MOVED: why = f"{label} read {tray['reading']:.0f} g on that card and reads {now['reading']:.0f} g now"
            else:
                try: guard_learn.weigh(res['printer']['serial'], label, now['reading'], grams,
                                       {'id': now.get('spool'), 'type': now['type'], 'colour': now['colour'], 'fid': now.get('fid'), 'weight': now.get('weight')})
                except OSError as e: why = f'The weigh-in of {label} could not be written ({e.__class__.__name__})'
                else: return self.recheck(res, where())
            refuse(why)
        self.later(lambda: self.again(res), then, lambda: refuse(f'The printer could not be asked what {label} reads now'))

    def again(self, res):
        """The plate of an answer, checked again now: the new answer, with what tells its plate carried over. An answer
        for a G-code that could not be read (not_read) has no plate: the file is read again, and the answer stands
        while it still cannot be."""
        plate = res['plate'] or (plate_at(res['gcode']) if res.get('gcode') else None)
        new = evaluate(plate, self.cfg, res['title'], res['times']) if plate else dict(res)
        new.update(pid=res.get('pid'), plate_no=res.get('plate_no'), gcode=res.get('gcode'), at=time.time()); return new

    @staticmethod
    def not_read(path, why):
        """The answer for a sliced plate whose G-code could not be read: one that could not be checked, which is not a
        fine one (rank), with nothing of the plate in it but where it is."""
        return {'title': project_title(path), 'model': '', 'time': '', 'times': 1, 'plate': None, 'printer': None, 'rows': [], 'worst': 'OK', 'error': why,
                'pid': studio_pid(path), 'plate_no': plate_number(path), 'gcode': path, 'at': time.time()}

    def recheck(self, res, beside=None):
        import guard_ui
        where = lambda: beside if guard_ui.visible(beside) else None
        def then(new):                                        # the card first, here as after a slice: nothing of the log may hold back an answer
            try: self.deliver(new, full=True, beside=where())
            finally: log('checked again: ' + report_text(new).replace('\n', ' | '))
        self.later(lambda: self.again(res), then, lambda: self.deliver(res, full=True, beside=where()))

    def check_newest(self, pid=None, beside=None):
        """From the icon: the newest slice, as a full card. At "Send print job" (beside): every plate of the project the
        sending Bambu Studio has sliced, because which of them is being sent cannot be read; the answer that most asks
        to be seen (rank) shows, when it is a warning, and names its plate. Of more than SEND_MOST plates the ones
        sliced last are checked, and what shows says how many were not: silence beside the dialog means every sliced
        plate is fine."""
        import guard_ui
        key = None if beside else 'icon'                      # beside the dialog only a warning shows: no line while it is checked
        if key in self.busy: self.checking(key, self.busy[key][1]); return     # asked again while its check runs: it says so, and starts nothing
        newest = newest_gcode(pid)
        if not newest:
            if not beside: self.card.note('No sliced plate found', [(None, 'Slice a plate in Bambu Studio first.')])
            return
        folder = os.path.dirname(newest)
        plates = sorted((p for p in temp_gcodes() if os.path.dirname(p) == folder), key=written, reverse=True) if beside else [newest]
        paths, older = plates[:SEND_MOST], len(plates[SEND_MOST:])
        if key: self.checking(key, checking_text(printer_for(plate_at(newest))), studio_pid(newest))
        def work():
            seen, out, unread, failed = {}, [], [], {}
            for path in paths:
                try:
                    with open(path, 'rb') as f: plate = parse_plate(read_head(f))
                except (OSError, ValueError) as e:                       # one plate that cannot be read does not cost the others their check
                    log(f'cannot read the slice {path}: {e.__class__.__name__}: {e}'); unread.append(f'it cannot be read ({e})')
                    if beside: out.append(self.not_read(path, f'the G-code of plate {plate_number(path)} cannot be read ({e})'))   # at the dialog it is a plate that was not checked,
                    continue                                                                                                      # and shows as one: silence there means every plate is fine
                if plate is None:
                    unread.append('Bambu Studio is still writing it')
                    if beside: out.append(self.not_read(path, f'Bambu Studio is still writing the G-code of plate {plate_number(path)}'))
                    continue
                if not plate['filaments']: continue
                title, model = project_title(path), plate['printer_model']
                # A check that fails is tried once more: one reading that timed out once dropped the plate being sent. A
                # printer that gave no reading either time is not asked again for each plate (30 plates, 10 s a try):
                # its other plates get that answer.
                res = dict(failed[model], plate=plate, time=plate['time']) if model in failed else evaluate(plate, self.cfg, title, seen=seen)
                if res['error'] and not res.get('quiet') and model not in failed:
                    res = evaluate(plate, self.cfg, title, seen=seen)
                    if res['error'] and not seen: failed[model] = res
                res['pid'], res['plate_no'], res['gcode'], res['at'] = studio_pid(path), plate_number(path), path, time.time()
                if not res['error']: res['ignored'] = self.ignored(res)
                out.append(res)
            return (max(out, key=self.rank) if out else None), unread, out        # the newest of those that most ask to be seen
        def then(found):
            res, unread, out = found
            try:
                for r in out:                                                     # what waited of these plates is as old as their last slice: this is their answer now
                    if plate_key(r) in self.held: self.hold(r)
                if res is not None and older:
                    res['unchecked'] = older; log(f"{res['title']}: {len(plates)} sliced plates, of which the {older} sliced first were not checked when \"Send print job\" opened")
                if beside and not guard_ui.visible(beside):                       # the dialog has closed meanwhile, as after a quick click on Send:
                    if res is not None and self.rank(res)[0]: self.deliver(res)   # a warning found is still one, and shows as after a slice
                elif res is not None and self.deliver(res, full=not beside, beside=beside): pass
                elif res is not None and older and not self.standing_warning():   # nothing to warn of among those checked, and no warning standing: the rest still has to be said
                    self.card.note('Not every plate was checked', [
                        (None, f"{res['title']} has {len(plates)} sliced plates. The {SEND_MOST} sliced last were checked now, with nothing to warn of; "
                               f"the {older} sliced before them {'were' if older != 1 else 'was'} not checked."),
                        (None, 'Slice a plate again to have it checked.')], 'warn', studio_pid(newest), beside)
                elif res is None and unread and not beside:                       # asked for from the icon: an answer, not a line that goes
                    self.card.note('Filament could not be checked', [(False, f'The newest slice ({project_title(newest)}): {unread[0]}.'),
                                                                     (None, 'Slice it again, or check again in a moment. The log has the file.')], 'danger', studio_pid(newest))
            finally:
                self.checked(key)
                for r in out: log(('at "Send print job": ' if beside else 'checked on request: ') + report_text(r).replace('\n', ' | '))
        self.later(work, then, lambda: self.checked(key))

    def checking(self, key, text, pid=None, asked=True):
        """Says at once that a check someone waits for is under way, in a line the answer replaces. key: what started
        it. asked: the user asked for it, and its answer is a full card; otherwise (a new slice) the line is held while
        Bambu Studio is minimised, and a full card that stands is left alone, since the answer may leave it standing."""
        shown = self.busy.get(key, (None,))[0]
        if shown is not None and self.card.win is shown and self.card.is_open(): return
        if not asked and self.card.is_open() and not self.card.one_line: self.busy[key] = (None, text); return
        self.busy[key] = (self.card.line(text, pid=pid, hold=not asked), text)

    def checked(self, key):
        """The check key started has ended: its line goes, unless an answer has taken its place."""
        shown = self.busy.pop(key, (None,))[0]
        if shown is not None and self.card.win is shown and self.card.is_open(): self.card.close()

    def greet(self):
        """Once in a run of the guard, the first time Bambu Studio runs: a line saying that the guard is running, which
        goes by itself (held, like every such line, while Bambu Studio is minimised). Not when one line for "enough" is
        turned off: quiet was asked for. greeted is set from the start when setup started the guard: its card says so."""
        import guard_ui
        pid = None if self.greeted else next(iter(guard_ui.studio_windows()), None)
        if pid is None: return
        self.greeted = True
        if self.cfg['ok_seconds'] and not self.card.is_open():
            self.card.line(f'Bambu Filament Guard {VERSION} is watching for sliced plates', 'ok', self.cfg['ok_seconds'], pid, hold=True)

    def forget_learned(self, sure=False):
        """From the icon's menu. It asks first, on a card, and forgets when "Forget" is chosen there (sure): one slip of the
        mouse beside Quit once emptied what a spool run dry and weeks of prints had taught. The card that confirms says
        where guard_learn.forget has kept the history as it was."""
        import guard_learn
        if not sure:
            self.card.note('Forget what was learned?', [
                (None, 'What the guard learned of every tray, from the prints it followed, is set aside, and it starts again from nothing.'),
                (None, 'Until it has learned again, a tray whose reading runs high is counted as it reads.')],
                buttons=[('Forget', lambda: self.forget_learned(True)), ('Keep', self.card.close)])
            return
        aside = guard_learn.path() + '.forgotten'
        try: kept = aside in guard_learn.forget()
        except OSError as e:
            log(f'what was learned could not be forgotten: {e.__class__.__name__}: {e}')
            self.card.note('What was learned could not be forgotten',
                           [(False, f'Its file could not be written ({e.__class__.__name__}): nothing was changed. Try again in a moment.')], 'danger'); return
        self.card.note('What was learned is forgotten', [(None, 'The guard learns again from the next prints it follows.')]
                       + ([(None, f'What it had learned is kept as {aside}: one copy, until something is forgotten again.')] if kept else []))

    def check_last(self):
        """The icon's click: the plate the icon names (on_icon) is checked again, and the fresh answer shows. The answer as it was
        is not shown again: hours old, after a print that had taken 120 g, its card read "Enough", "as the printer
        reports them now". A click while the check runs says so again and starts no second one; with no answer yet,
        the newest slice is checked."""
        res = self.on_icon()
        if not res or not res.get('plate'): self.check_newest(); return
        if 'icon' in self.busy: self.checking('icon', self.busy['icon'][1]); return
        self.checking('icon', checking_text(printer_for(res['plate'])), res.get('pid'))
        def then(new):
            try: self.deliver(new, full=True)
            finally: self.checked('icon'); log('checked again: ' + report_text(new).replace('\n', ' | '))
        self.later(lambda: self.again(res), then, lambda: self.checked('icon'))

    def run_selftest(self):
        def then(lines):
            bad = any(mark is False for mark, _ in lines)
            self.card.note('Self-test found a problem' if bad else 'Self-test: the guard works', lines, 'danger' if bad else 'ok')
        self.later(lambda: selftest(self.cfg), then)

    def keep(self, key, value):
        """Puts a setting in force and writes config.json. OSError when that cannot be written: the setting is in force all
        the same, until the guard stops."""
        self.cfg[key] = value; save_config(self.cfg); log(f'setting {key} = {value}')

    def set_option(self, key, value):
        """A setting changed from the icon's menu. With config.json not writable (the guard's folder read-only, the file
        held elsewhere) it raised out of the menu: a line in the log, and nothing to say that the next start forgets it.
        Nothing is raised; the log and a card say that it was not kept."""
        try: self.keep(key, value)
        except OSError as e:
            log(f'setting {key} = {value}: not kept, config.json could not be written ({e.__class__.__name__}): in force until the guard stops')
            self.card.note('This setting was not kept', [(False, f'config.json could not be written ({e.__class__.__name__}): is the folder the guard runs from read-only?'),
                                                         (None, 'The setting is in force until the guard stops; at its next start it is as config.json has it.')], 'warn')
        if self.tray: self.tray.refresh()

    def toggle_warning(self, status):
        on = self.popup_on()
        self.set_option('popup_on', [s for s in on if s != status] if status in on else on + [status])

    def toggle_autostart(self):
        if os.path.exists(STARTUP_LNK): os.remove(STARTUP_LNK)
        else: make_shortcut()
        if self.tray: self.tray.refresh()

    def toggle_pause(self):
        self.paused = not self.paused; log('checks paused' if self.paused else 'checks resumed'); self.show_icon()

    def open_log(self):
        os.startfile(os.path.join(STATE_DIR, 'guard.log'))

    def quit(self):
        self.stopping.set()

    # -- the two loops
    def take_slice(self, path, plate):
        """A newly sliced plate: checked and handed to the Tk thread, then remembered with its per-layer profile, read
        from the same file. The check comes first: the card does not wait for the profile, and a re-slice of the plate
        that is printing would otherwise replace the slice, and the profile, that the running print is counted from."""
        title, plate_no, profile, waiting = project_title(path), plate_number(path), None, False
        try:
            if not self.paused and plate['filaments']:
                printer = printer_for(plate)
                if printer and self.cfg['ok_seconds']:                          # no line for a check that ends in silence (a printer not on this LAN), nor for a quiet guard
                    waiting = True; self.todo.put(lambda: self.checking('slice', checking_text(printer), studio_pid(path), asked=False))
                res = evaluate(plate, self.cfg, title); res['pid'], res['plate_no'], res['gcode'], res['at'] = studio_pid(path), plate_no, path, time.time()
                self.todo.put(lambda: self.deliver(res))
                log(report_text(res).replace('\n', ' | '))
        finally:                                                                 # the slice is remembered whatever became of the check: guard_ledger counts from it
            if waiting: self.todo.put(lambda: self.checked('slice'))
            if plate['filaments']:
                try:
                    why = []
                    with open(path, 'rb') as f: profile = layer_profile(f, plate, why)
                    if profile is None: log(f"{title}, plate {plate_no}: no per-layer profile ({'; '.join(why)}): a print of it is not counted while it runs")
                except Exception as e:                                           # the profile is extra: whatever happens to the file, the slice is remembered
                    log(f'{title}, plate {plate_no}: no per-layer profile ({e.__class__.__name__}: {e}): a print of it is not counted while it runs')
            remember_slice(title, plate_no, plate, profile)

    def poll_seconds(self):
        """The watcher's pause between two looks: poll_seconds, or the default when that is no positive number ("2" ended
        the watcher after its first round, null made it wait for ever)."""
        s = self.cfg.get('poll_seconds')
        return min(s, threading.TIMEOUT_MAX) if isinstance(s, (int, float)) and not isinstance(s, bool) and s > 0 else DEFAULTS['poll_seconds']

    def watch_slices(self):
        """The watcher's own thread: every newly sliced plate is read, checked and handed to the Tk thread. Nothing that
        happens in a round ends it, its pause included: without this thread no slice is checked and the stop flag is
        never seen, while the icon stays. It notes itself as `watcher`, and pump starts another should it end all the same."""
        self.watcher = threading.current_thread()
        watcher, stop_flag, told = None, os.path.join(STATE_DIR, 'stop'), set()
        while not self.stopping.is_set():
            try:
                try:
                    if os.path.exists(stop_flag): self.stopping.set(); break
                    watcher = watcher or Watcher()
                    for path, plate in watcher.new_plates():
                        try: self.take_slice(path, plate)
                        except Exception as e: log(f'error: {e.__class__.__name__}: {e}')   # one plate that fails does not cost the others of this round their check
                    for path, why in watcher.problems:
                        version = studio_version(); log(f'cannot read the slice {path} of Bambu Studio {version}: {why}')
                        if version not in told:                                  # said once for each Bambu Studio version, logged every time
                            told.add(version)
                            lines = [(False, f'Bambu Studio {version} wrote a slice the guard cannot read: {why}.'),
                                     (None, 'Its prints are not checked until the guard is updated. The log has the file.')]
                            self.todo.put(lambda lines=lines, pid=studio_pid(path): self.card.note('Filament is not being checked', lines, 'danger', pid))
                    watcher.problems.clear()
                finally: self.stopping.wait(self.poll_seconds())                 # after a round that failed too (no loop without a pause); at once when stopping
            except Exception as e:                                               # the watcher outlives any one bad file or network hiccup
                log(f'error: {e.__class__.__name__}: {e}')

    def look_around(self):
        """Every 0.4 s on the Tk thread: has "Send print job" opened? Is Bambu Studio running (then the icon shows)?
        Does a printer with entered amounts need following?"""
        import guard_ui, guard_ledger
        dialogs = guard_ui.send_dialogs()
        for handle, pid in dialogs.items():
            if handle not in self.sending and self.cfg['at_send'] and not self.paused: self.check_newest(pid, beside=handle)
        self.sending = set(dialogs)
        if self.ticks % 5 == 0 and self.tray: self.tray.visible(bool(guard_ui.studio_windows()))
        if self.ticks % 5 == 0: self.greet()
        if self.ticks % 25 == 1:
            for serial in guard_ledger.followed(self.cfg):
                if serial not in self.jobs or not self.jobs[serial].is_alive():
                    self.jobs[serial] = guard_ledger.JobWatch(serial, self.cfg); self.jobs[serial].start()

    def pump(self):
        if self.stopping.is_set(): self.root.quit(); return
        try:
            self.ticks += 1
            while True:
                try: job = self.todo.get_nowait()
                except queue.Empty: break
                try: job()
                except Exception as e: log(f'error: {e.__class__.__name__}: {e}')    # one failing request does not end the guard
            for step in (self.card.follow, self.show_held, self.look_around if self.standing else None):
                try: step and step()
                except Exception as e:                                           # nor does one failing step stop the other; said once a minute at most
                    if self.ticks % 150 == 1: log(f'error: {e.__class__.__name__}: {e}')
            if self.watcher and not self.watcher.is_alive() and self.ticks % 25 == 0:   # looked at every 10 s: a watcher that ended is one that is started again
                log('the watcher had ended: started again'); self.watcher = threading.Thread(target=self.watch_slices, daemon=True); self.watcher.start()
        finally: self.root.after(400, self.pump)                                 # whatever happened above: without the next round there is no card, no menu and no Quit

    def loop(self):
        try: self.root.after(400, self.pump); self.root.mainloop()
        finally:                                              # whatever ended the loop: the icon's thread is no daemon, and left running it kept a dead guard looking alive
            if self.tray: self.tray.stop()
            for job in self.jobs.values(): job.stopped.set()


def watch(start_line=True):
    import ctypes, guard_ui
    k32 = ctypes.WinDLL('kernel32', use_last_error=True); k32.CreateMutexW.restype = ctypes.c_void_p
    handle = k32.CreateMutexW(None, False, MUTEX)
    if ctypes.get_last_error() == 183: return 0                                   # ERROR_ALREADY_EXISTS: one guard is enough
    os.makedirs(STATE_DIR, exist_ok=True)
    stop_flag = os.path.join(STATE_DIR, 'stop')
    if os.path.exists(stop_flag): os.remove(stop_flag)
    with open(os.path.join(STATE_DIR, 'guard.pid'), 'w') as f: f.write(str(os.getpid()))
    guard = Guard(load_config(), guard_ui.make_root()); guard.greeted = not start_line
    log(f'watching for sliced plates (pid {os.getpid()}, guard {VERSION}, Bambu Studio {studio_version() or "not found"})')
    threading.Thread(target=guard.watch_slices, daemon=True).start()
    guard.loop()
    log('stopped'); k32.CloseHandle(ctypes.c_void_p(handle))
    return 0

def is_running():
    import ctypes
    h = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, MUTEX)                # SYNCHRONIZE
    if h: ctypes.windll.kernel32.CloseHandle(h)
    return bool(h)

def stop():
    if not is_running(): return False
    open(os.path.join(STATE_DIR, 'stop'), 'w').close()
    for _ in range(40):
        if not is_running(): return True
        time.sleep(0.25)
    return not is_running()


# ---------------------------------------------------------------- self-test

def selftest(cfg):
    """[(True = works, False = does not, None = cannot be told now, text)]: can the guard read this Bambu Studio and
    reach its printers? Run it after a Bambu Studio update."""
    import guard_ui, guard_ledger
    try: conf = bs_conf()
    except (OSError, ValueError) as e:
        return [(False, f"Bambu Studio's configuration cannot be read ({e.__class__.__name__}): is Bambu Studio installed for this Windows user?")]
    version = str(conf.get('app', {}).get('version', '?'))
    out = [(True, f'Bambu Studio {version}: its configuration is readable')]
    verified, newest = load_state().get('verified') or {}, newest_gcode()
    if newest:
        try:
            with open(newest, 'rb') as f: plate = parse_plate(read_head(f))
            if plate: out.append((True, f"Newest slice ({project_title(newest)}): {len(plate['filaments'])} filament(s), {sum(f['grams'] or 0 for f in plate['filaments']):.0f} g"))
            else: out.append((None, 'Newest slice: Bambu Studio is still writing it'))
        except (OSError, ValueError) as e: out.append((False, f'Newest slice cannot be read: {e}'))
    elif verified.get('version') == version:
        out.append((True, f"Slices of this version have been read, last on {time.strftime('%d %b %Y', time.localtime(verified['at']))}"))
    else: out.append((None, 'No slice of this Bambu Studio version has been read yet: slice any plate to test it'))
    printers = known_printers(conf)
    if not printers: out.append((False, 'Bambu Studio holds no LAN access code for a printer: open the printer once on its Device tab'))
    for p in printers:
        try:
            ip = printer_ip(p['serial'], cfg)
            if not ip: raise PrinterError('it is not announcing itself on this network (is it on?)')
            status = read_status(ip, p['serial'], conf['access_code'][p['serial']])
            trays, _ = trays_of(status); guard_ledger.apply(trays, p['serial'], 'ams' in status)
            out.append((True, f"{p['model']}: {len(trays)} spool(s), {sum(t['grams'] is not None for t in trays)} with a known amount"))
        except PrinterError as e: out.append((False if 'refused' in str(e) else None, f"{p['model']}: {e}"))
    import guard_learn
    history = guard_learn.load()
    jobs = [j for trays in history.values() for h in trays.values() for j in h.get('jobs', [])]
    out.append((True if cfg.get('learn') else None, f"Learning from prints is {'on' if cfg.get('learn') else 'off'}: {len(jobs)} tray record(s) of past prints, "
                f"{sum(1 for j in jobs if j.get('sound'))} sound (\"learned\" says more)"))
    out.append((True, 'Bambu Studio is running: the card shows in its window') if guard_ui.studio_windows() else (None, 'Bambu Studio is not running: the card shows when it is'))
    out.append((True, 'The guard is running') if is_running() else (False, 'The guard is not running: "install" starts it'))
    out.append((True, 'It starts automatically') if os.path.exists(STARTUP_LNK) else (None, 'It does not start automatically'))
    return out


# ---------------------------------------------------------------- installing

def pythonw():
    exe = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
    return exe if os.path.exists(exe) else sys.executable

def own_command(*args):
    """The command line that runs this guard without a console: pythonw (the one beside the running Python) and this script."""
    return [pythonw(), os.path.abspath(__file__), *args]

def start(start_line=True):
    if is_running(): return
    subprocess.Popen(own_command('watch', *([] if start_line else ['--no-start-line'])), cwd=HERE, close_fds=True, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)

def make_shortcut():
    """The Startup shortcut. The guard starts with Windows and waits; its icon shows while Bambu Studio runs."""
    cmd = own_command('watch')
    env = dict(os.environ, FG_LNK=STARTUP_LNK, FG_EXE=cmd[0], FG_ARGS=subprocess.list2cmdline(cmd[1:]), FG_DIR=HERE)
    subprocess.run(['powershell', '-NoProfile', '-Command',
                    "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:FG_LNK); $s.TargetPath=$env:FG_EXE; $s.Arguments=$env:FG_ARGS; "
                    "$s.WorkingDirectory=$env:FG_DIR; $s.Description='Warns when the loaded spools do not hold enough filament for a sliced print'; $s.Save()"],
                   env=env, check=True, creationflags=subprocess.CREATE_NO_WINDOW)

def install(start_line=True):
    """Makes the guard start automatically, from where it is, and starts it. start_line: the guard says so on a card of
    its own, once Bambu Studio is open; setup, which says it on its card, turns that off."""
    make_shortcut()
    print('starts automatically:', STARTUP_LNK)
    try:
        conf, cfg = bs_conf(), load_config()
        for p in known_printers(conf):
            ip = printer_ip(p['serial'], cfg, refresh=True)
            print(f"  {p['model']}: " + (ip or 'not announcing itself on this network now (it is looked for again when a plate is sliced)'))
    except (OSError, ValueError): print("  Bambu Studio's configuration was not found: the guard waits for it")
    if stop(): time.sleep(0.5)                                                   # a running copy may be an older version
    start(start_line); time.sleep(1.5)
    print('watcher running' if is_running() else 'watcher did NOT start: run it with "watch" to see why')

def uninstall():
    if os.path.exists(STARTUP_LNK): os.remove(STARTUP_LNK)
    print('stopped' if stop() else 'was not running'); print('shortcut removed')
    print(f'settings and log stay in {STATE_DIR} and {CONFIG}: delete them, and this folder, to remove every trace')

def setup():
    """Install, and say so on a card: for someone who started the packaged guard with a double click and sees no console."""
    import guard_ui
    if sys.stdout is None: sys.stdout = sys.stderr = open(os.devnull, 'w')       # pythonw has no console to print to
    install(start_line=False)                                                    # this card says it, in the same corner as the guard's start line would
    root = guard_ui.make_root(); card = guard_ui.Card(root, on_close=root.quit)
    card.note('Bambu Filament Guard is installed' if is_running() else 'Bambu Filament Guard did not start', [
        (True, "It checks every plate you slice in Bambu Studio and shows the answer in Bambu Studio's window."),
        (True, 'Its icon, a spool, is in the notification area while Bambu Studio runs: settings, self-test, quit.'),
        (None, f'It runs from this folder, so the folder has to stay: {os.path.dirname(HERE) if os.path.basename(HERE) == "app" else HERE}'),
        (None, 'To remove it: Uninstall.cmd in that folder, then delete the folder.')], 'ok' if is_running() else 'danger')
    def tick(): card.follow(); root.after(400, tick)
    tick(); root.mainloop()
    return 0


def number_of_prints(text):
    """--times: 1 or more. 0 and -1 were taken as given, and read "enough" for a plate one print of which is short."""
    try: n = int(text)
    except ValueError: n = 0
    if n < 1: raise argparse.ArgumentTypeError(f'{text!r} is not a number of prints (1 or more)')
    return n

def check(a, cfg):
    """The `check` command: the answer for each plate of a file (and for all its plates together), or for the newest
    slice, as text or as JSON. Returns the exit code: the worst of the answers, 3 with one line of why (with --json,
    {"error": ...}) when there is nothing to check."""
    def not_checked(why): print(json.dumps({'error': why}) if a.json else why); return 3
    def unread(title, why): return {'title': title, 'model': '', 'time': '', 'times': a.times, 'plate': None, 'printer': None, 'rows': [], 'worst': 'OK', 'error': why}
    cut = []                                                 # the plates of the file whose G-code is not complete
    if a.file:
        try: found = plates_of_file(a.file)
        except (OSError, ValueError) as e: return not_checked(f'{a.file}: {e}')
        plates, cut = [(os.path.basename(a.file) + (' ' + label if label else ''), plate) for label, plate in found], [label for label, plate in found if not plate]
    else:
        newest = newest_gcode()
        if not newest: return not_checked('no sliced plate found: slice in Bambu Studio first, or name a .gcode / .gcode.3mf file')
        try:
            with open(newest, 'rb') as f: plate = parse_plate(read_head(f))
        except (OSError, ValueError) as e: return not_checked(f'the newest slice ({project_title(newest)}) cannot be read: {e}')
        if not plate: return not_checked('Bambu Studio is still writing its newest slice')
        plates = [(project_title(newest), plate)]
    seen = {}
    results = [evaluate(plate, cfg, title, a.times, seen) if plate else unread(title, 'the G-code of this plate is not complete in the file') for title, plate in plates]
    # "Print all plates" needs the sum: each plate alone can fit a spool that does not hold them all, and exit code 0
    # for such a file once sent a script on. The same reading of the printer serves every answer (seen). A plate
    # that could not be read has no part in the sum, so there is none.
    if len(plates) > 1:
        together = os.path.basename(a.file) + ' all plates together'
        results.append(unread(together, f"the G-code of {', '.join(cut)} is not complete in the file: what all plates need together cannot be told") if cut
                       else evaluate(plates_together([plate for _, plate in plates]), cfg, together, a.times, seen))
    if not a.file: results[0]['pid'], results[0]['plate_no'] = studio_pid(newest), plate_number(newest)
    print(json.dumps(results, indent=1) if a.json else '\n'.join(report_text(r) for r in results))
    if a.popup:
        import guard_ui
        guard = Guard(cfg, guard_ui.make_root(), standing=False)
        guard.deliver(max(results, key=exit_code), full=True); guard.loop()
    return max(exit_code(r) for r in results)

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0]); sub = ap.add_subparsers(dest='cmd')
    c = sub.add_parser('check'); c.add_argument('file', nargs='?'); c.add_argument('--times', type=number_of_prints, default=1)
    c.add_argument('--popup', action='store_true'); c.add_argument('--json', action='store_true')
    sub.add_parser('watch').add_argument('--no-start-line', action='store_true')
    learned = sub.add_parser('learned'); forgets = learned.add_mutually_exclusive_group()
    forgets.add_argument('--forget', nargs='?', const='', metavar='TRAY', help='forget what was learned: everything, or one tray (e.g. C3)')
    forgets.add_argument('--forget-weigh', metavar='TRAY', help="take back that tray's latest weigh-in (e.g. C3): the rest of what was learned stays")
    learned.add_argument('--printer', help='with --forget TRAY or --forget-weigh TRAY: the printer, by serial or model, when more than one has that tray')
    for name in ('install', 'uninstall', 'setup', 'status', 'stop', 'trays', 'selftest'): sub.add_parser(name)
    try: a = ap.parse_args(argv)
    except SystemExit as e: raise SystemExit(3 if e.code == 2 else e.code)        # argparse ends a command line it cannot read with 2, which is "not enough" to a script that gates on `check`
    if a.cmd == 'watch': return watch(start_line=not a.no_start_line)
    if a.cmd == 'install': return install()
    if a.cmd == 'uninstall': return uninstall()
    if a.cmd == 'setup': return setup()
    if a.cmd == 'stop': print('stopped' if stop() else 'was not running'); return 0
    if a.cmd == 'status':
        print('watcher running' if is_running() else 'watcher not running', '| starts automatically:', 'yes' if os.path.exists(STARTUP_LNK) else 'no', '| log:', os.path.join(STATE_DIR, 'guard.log'))
        return 0
    cfg = load_config()
    if a.cmd == 'selftest':
        lines = selftest(cfg)
        for mark, text in lines: print({True: '[ok]', False: '[!!]', None: '[--]'}[mark], text)
        return 1 if any(mark is False for mark, _ in lines) else 0
    if a.cmd == 'learned':
        import guard_learn
        try: names = {p['serial']: p['model'] for p in known_printers(bs_conf())}
        except (OSError, ValueError): names = {}
        if a.forget is not None or a.forget_weigh:
            try: print(guard_learn.forget_weigh(a.forget_weigh, a.printer, names) if a.forget_weigh else guard_learn.forget(a.forget or None, a.printer, names), 'is forgotten'); return 0
            except ValueError as e: print(e); return 1
        try: lines = guard_learn.report(guard_learn.load(), names, cfg.get('learn'), cfg)
        except Exception as e:               # a history that cannot be shown must still say so, and how to start again
            log(f'learned: {e.__class__.__name__}: {e}')
            print(f'What was learned cannot be shown ({e.__class__.__name__}: {e}). "learned --forget" starts again from nothing.'); return 1
        print('\n'.join(lines)); return 0
    if a.cmd == 'trays':
        import guard_ledger
        conf = bs_conf()
        for p in known_printers(conf):
            ip = printer_ip(p['serial'], cfg)
            try:
                if not ip: raise PrinterError('not announcing itself on this network')
                status = read_status(ip, p['serial'], conf['access_code'][p['serial']])
            except PrinterError as e: print(f"{p['model']}: {e}"); continue
            trays, two = trays_of(status); guard_ledger.apply(trays, p['serial'], 'ams' in status)
            print(f"{p['model']} ({ip}), {status.get('gcode_state')}")
            for t in trays:
                amount = 'entered amount out of date' if t.get('stale') else 'amount unknown' if t['grams'] is None else f"{t['grams']:.0f} g" + (' (entered by hand)' if t.get('entered') else '')
                print(f"  {t['label']:<5} {t['type']:<8} {t['name'][:18]:<18} {t['colour']}  {amount}" + (f"  {t['nozzle']} nozzle" if two and t['nozzle'] else ''))
        return 0
    if a.cmd == 'check':
        # Whatever happens, a script gets one line and "not checked" (3): a traceback ends Python with 1, which is
        # "tight". And the line is printed whatever it holds: through a pipe the output is in the ANSI code page, where
        # a project named outside it once ended the check in UnicodeEncodeError; such characters show as "?".
        if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(errors='replace')
        try: return check(a, cfg)
        except Exception as e:
            why = ' '.join(f'the check failed ({e.__class__.__name__}: {e})'.split())
            print(json.dumps({'error': why}) if a.json else why); return 3
    ap.print_help(); return 0

if __name__ == '__main__':
    import filament_guard                 # run as the module the other files import, so there is one copy of its state and its lock
    sys.exit(filament_guard.main())
