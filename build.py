"""python build.py  ->  dist/BambuFilamentGuard-<version>.zip: the guard with a Python of its own, for a computer that has none.

To use it: unzip it where it can stay, open Install.cmd. Remove it with Uninstall.cmd, then delete the folder.

No program in it is of the guard's own making: it runs on python.org's signed pythonw.exe, exactly as the guard runs
from source. That is on purpose. The first packaging was one self-installing .exe (PyInstaller, --onefile); Windows
Defender removed it at its first start as "Trojan:Win32/Bearfoos.A!ml" (2026-11-19), with the Startup shortcut it had
just written: an unsigned packed program that copies itself and makes itself start with Windows is what that
detection looks for.

What goes in:
  python.org's "embeddable" Python, of the version this script runs on (downloaded; its SHA-256 is printed)
  tkinter and Tcl/Tk from this Python's installation (the embeddable package leaves them out)
  the packages in requirements.txt: paho-mqtt, pystray and Pillow (pip, into the bundle only)
  filament_guard.py, guard_ui.py, guard_ledger.py, README.md and LICENSE, in app/ (the MIT licence asks that it goes with every copy)"""
import os, sys, io, shutil, hashlib, zipfile, subprocess, urllib.request
import filament_guard as fg

here = os.path.dirname(os.path.abspath(__file__))
version, tag = '%d.%d.%d' % sys.version_info[:3], 'python%d%d' % sys.version_info[:2]
out = os.path.join(here, 'dist', 'BambuFilamentGuard')
shutil.rmtree(out, ignore_errors=True); os.makedirs(out)

url = f'https://www.python.org/ftp/python/{version}/python-{version}-embed-amd64.zip'
data = urllib.request.urlopen(url, timeout=120).read()
print(f'{url}: {len(data)} bytes, SHA-256 {hashlib.sha256(data).hexdigest()}')
zipfile.ZipFile(io.BytesIO(data)).extractall(out)

base = sys.base_prefix                                            # tkinter: the parts python.org's installer puts beside the standard library
for name in ('_tkinter.pyd', 'tcl86t.dll', 'tk86t.dll', 'zlib1.dll'):
    if os.path.exists(os.path.join(base, 'DLLs', name)): shutil.copy2(os.path.join(base, 'DLLs', name), out)
shutil.copytree(os.path.join(base, 'Lib', 'tkinter'), os.path.join(out, 'Lib', 'tkinter'), ignore=shutil.ignore_patterns('__pycache__', 'test'))
for name in ('tcl8.6', 'tk8.6', 'tcl8'):
    shutil.copytree(os.path.join(base, 'tcl', name), os.path.join(out, 'tcl', name), ignore=shutil.ignore_patterns('demos', 'tzdata', 'msgs', 'encoding'))
shutil.copytree(os.path.join(base, 'tcl', 'tcl8.6', 'encoding'), os.path.join(out, 'tcl', 'tcl8.6', 'encoding'))     # Tcl does not start without its encodings

subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '--no-compile', '--only-binary=:all:',
                       '--target', os.path.join(out, 'Lib', 'site-packages'), '-r', os.path.join(here, 'requirements.txt')])

os.makedirs(os.path.join(out, 'app'))
for name in ('filament_guard.py', 'guard_ui.py', 'guard_ledger.py', 'README.md', 'LICENSE'): shutil.copy2(os.path.join(here, name), os.path.join(out, 'app'))
# The embeddable Python looks for modules only where this file says: its own library, tkinter, the packages, the guard.
open(os.path.join(out, tag + '._pth'), 'w').write('\n'.join([tag + '.zip', '.', 'Lib', 'Lib\\site-packages', 'app']) + '\n')
open(os.path.join(out, 'Install.cmd'), 'w').write('@echo off\r\nstart "" "%~dp0pythonw.exe" "%~dp0app\\filament_guard.py" setup\r\n')
open(os.path.join(out, 'Uninstall.cmd'), 'w').write('@echo off\r\n"%~dp0python.exe" "%~dp0app\\filament_guard.py" uninstall\r\npause\r\n')

packed = shutil.make_archive(os.path.join(here, 'dist', f'BambuFilamentGuard-{fg.VERSION}'), 'zip', os.path.join(here, 'dist'), 'BambuFilamentGuard')
print(f'{packed}: {os.path.getsize(packed) / 1e6:.1f} MB')
