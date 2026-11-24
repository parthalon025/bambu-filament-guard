"""python build.py  ->  dist/BambuFilamentGuard-<version>.zip: the guard with a Python of its own, for a computer that has none.

To use it: unzip it into a folder of your own where it can stay, open Install.cmd. Remove it with Uninstall.cmd, then
delete the folder.

No program in it is of the guard's own making: it runs on python.org's signed pythonw.exe, exactly as the guard runs
from source. That is on purpose. The first packaging was one self-installing .exe (PyInstaller, --onefile); Windows
Defender removed it at its first start as "Trojan:Win32/Bearfoos.A!ml" (2026-11-19), with the Startup shortcut it had
just written: an unsigned packed program that copies itself and makes itself start with Windows is what that
detection looks for.

What goes in:
  python.org's "embeddable" Python, of the version this script runs on (downloaded, and refused unless its SHA-256 is
  the one kept in EMBED below)
  tkinter and Tcl/Tk from this Python's installation (the embeddable package leaves them out)
  the wheels named in requirements-bundle.txt, by version and SHA-256: paho-mqtt, pystray, Pillow and six (pip, into
  the bundle only)
  filament_guard.py and every guard_*.py, README.md, LICENSE, SECURITY.md and CHANGELOG.md, in app/ (the MIT licence
  asks that it goes with every copy)
  docs/, in app/docs/: the pages and pictures the README links to
  README.txt beside Install.cmd: what this is, what Windows asks at the first start, where the licences are"""
import os, sys, io, glob, shutil, hashlib, zipfile, platform, subprocess, urllib.request
import filament_guard as fg

# The Pythons the zip may be built on, with the SHA-256 of python.org's embeddable package for each. A build takes its
# Python, tkinter and Tcl/Tk from the interpreter it runs on, so a version nobody started the guard on would otherwise
# go into a release unseen. To add one: build and start the zip on it (the release workflow does), then add its line.
EMBED = {'3.13.17': '9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38'}

here = os.path.dirname(os.path.abspath(__file__))
version, tag = '%d.%d.%d' % sys.version_info[:3], 'python%d%d' % sys.version_info[:2]
if version not in EMBED: sys.exit(f'build.py makes the zip on Python {" or ".join(EMBED)}, not {version}: see EMBED in build.py')
if platform.machine() != 'AMD64': sys.exit(f'build.py makes the zip for 64-bit Windows, on a 64-bit Python for it: this one is {platform.machine()}')
out = os.path.join(here, 'dist', 'BambuFilamentGuard')
shutil.rmtree(out, ignore_errors=True); os.makedirs(out)

url = f'https://www.python.org/ftp/python/{version}/python-{version}-embed-amd64.zip'
data = urllib.request.urlopen(url, timeout=120).read()
digest = hashlib.sha256(data).hexdigest()
print(f'{url}: {len(data)} bytes, SHA-256 {digest}')
if digest != EMBED[version]: sys.exit(f'that is not the package this script knows for {version} ({EMBED[version]}): nothing was built')
zipfile.ZipFile(io.BytesIO(data)).extractall(out)

base = sys.base_prefix                                            # tkinter: the parts python.org's installer puts beside the standard library
for name in ('_tkinter.pyd', 'tcl86t.dll', 'tk86t.dll'): shutil.copy2(os.path.join(base, 'DLLs', name), out)   # without one of these there is no card: better no zip
if os.path.exists(os.path.join(base, 'DLLs', 'zlib1.dll')): shutil.copy2(os.path.join(base, 'DLLs', 'zlib1.dll'), out)   # what tcl86t.dll needs, where this Python has it apart
shutil.copytree(os.path.join(base, 'Lib', 'tkinter'), os.path.join(out, 'Lib', 'tkinter'), ignore=shutil.ignore_patterns('__pycache__', 'test'))
for name in ('tcl8.6', 'tk8.6', 'tcl8'):
    shutil.copytree(os.path.join(base, 'tcl', name), os.path.join(out, 'tcl', name), ignore=shutil.ignore_patterns('demos', 'tzdata', 'msgs', 'encoding'))
shutil.copytree(os.path.join(base, 'tcl', 'tcl8.6', 'encoding'), os.path.join(out, 'tcl', 'tcl8.6', 'encoding'))     # Tcl does not start without its encodings

subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '--no-compile', '--only-binary=:all:', '--require-hashes',
                       '--target', os.path.join(out, 'Lib', 'site-packages'), '-r', os.path.join(here, 'requirements-bundle.txt')])

os.makedirs(os.path.join(out, 'app'))
for path in [os.path.join(here, 'filament_guard.py')] + sorted(glob.glob(os.path.join(here, 'guard_*.py'))) + [os.path.join(here, name) for name in ('README.md', 'LICENSE', 'SECURITY.md', 'CHANGELOG.md')]:
    shutil.copy2(path, os.path.join(out, 'app'))
shutil.copytree(os.path.join(here, 'docs'), os.path.join(out, 'app', 'docs'))
# The embeddable Python looks for modules only where this file says: its own library, tkinter, the packages, the guard.
open(os.path.join(out, tag + '._pth'), 'w').write('\n'.join([tag + '.zip', '.', 'Lib', 'Lib\\site-packages', 'app']) + '\n')
# newline='': the line ends are written here as cmd.exe wants them; Windows' text mode would make CR CR LF of each.
# Opened from inside the zip, Explorer runs a .cmd from a temporary folder with nothing beside it: the first line says so.
whole = 'if not exist "%~dp0pythonw.exe" (echo Unzip the whole folder first, then open this file in it. & pause & exit /b 1)\r\n'
open(os.path.join(out, 'Install.cmd'), 'w', newline='').write('@echo off\r\n' + whole + 'start "" "%~dp0pythonw.exe" "%~dp0app\\filament_guard.py" setup\r\n')
open(os.path.join(out, 'Uninstall.cmd'), 'w', newline='').write('@echo off\r\n' + whole + '"%~dp0python.exe" "%~dp0app\\filament_guard.py" uninstall\r\npause\r\n')
open(os.path.join(out, 'README.txt'), 'w', newline='').write('\r\n'.join([
    f'Bambu Filament Guard {fg.VERSION}', '',
    'Warns before a print when the spools on your Bambu Lab printer do not hold enough filament for it.', '',
    'To install: open Install.cmd. The guard starts now and with Windows, and runs from this folder, so the folder has to stay.',
    'Keep it in a folder of your own, inside your user folder: what is in it is started each time you log on, so no other',
    'account of this computer should be able to change it. Move the folder there before you open Install.cmd.',
    'To remove it: open Uninstall.cmd, then delete this folder.', '',
    'What Windows may ask at the first start:',
    '  "Windows protected your PC", when Install.cmd opens: the file came from the internet and is not signed.',
    '  Choose "More info", then "Run anyway". (Or before unzipping: right-click the zip, Properties, Unblock.)',
    '  Windows Firewall, about "Python": allow it on private networks. The guard listens there for your printer',
    '  announcing its address. It makes no internet connection.', '',
    'How it works, its settings and its limits: app\\README.md and app\\docs.',
    'What it reads, sends and keeps: app\\SECURITY.md. What each version changed: app\\CHANGELOG.md.', '',
    "Licences: the guard's own is app\\LICENSE (MIT). Python's is LICENSE.txt in this folder, Tcl/Tk's is",
    'tcl\\tk8.6\\license.terms, and those of paho-mqtt, Pillow, pystray (LGPL-3.0) and six are in their folders under',
    'Lib\\site-packages.', '']))

packed = shutil.make_archive(os.path.join(here, 'dist', f'BambuFilamentGuard-{fg.VERSION}'), 'zip', os.path.join(here, 'dist'), 'BambuFilamentGuard')
print(f'{packed}: {os.path.getsize(packed) / 1e6:.1f} MB')
