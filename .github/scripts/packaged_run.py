"""Installs, runs and removes the packaged guard on this computer: python .github/scripts/packaged_run.py <unzipped folder>

For the release workflow, on a Windows runner with no Bambu Studio and no printer: what Install.cmd runs (setup) makes
the Startup shortcut and starts the guard; the guard runs from this folder and says so in its log; the self-test ends
in lines; what Uninstall.cmd runs removes the shortcut and stops the guard. The tests reach none of it: they stand in
for PowerShell, the named mutex, Tk and the icon. Prints what each step said, and the guard's log; fails on the first
thing that is not so."""
import os, sys, time, subprocess

folder = os.path.abspath(sys.argv[1])
py, pyw, app = os.path.join(folder, 'python.exe'), os.path.join(folder, 'pythonw.exe'), os.path.join(folder, 'app', 'filament_guard.py')
state = os.path.join(os.environ['LOCALAPPDATA'], 'BambuFilamentGuard')
lnk = os.path.join(os.environ['APPDATA'], 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup', 'Bambu Filament Guard.lnk')

def said(*args):
    r = subprocess.run([py, app, *args], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
    print(f'> {" ".join(args)}  (exit code {r.returncode})\n{r.stdout}{r.stderr}'.rstrip(), flush=True)
    return r.returncode, r.stdout + r.stderr

def log():
    try: return open(os.path.join(state, 'guard.log'), encoding='utf-8', errors='replace').read()
    except OSError: return ''

def wait(what, test, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if test(): return
        time.sleep(1)
    sys.exit(f'{what}: not within {seconds} s\n--- guard.log ---\n{log()}')

def stop_at(why):
    sys.exit(f'{why}\n--- guard.log ---\n{log()}')

if os.path.exists(lnk): stop_at('a Startup shortcut is there before the install')
if 'watcher not running' not in said('status')[1]: stop_at('a guard runs before the install')
setup = subprocess.Popen([pyw, app, 'setup'])                 # what Install.cmd starts; its card stays until someone closes it
try:
    wait('the guard runs after setup', lambda: 'watcher running' in said('status')[1], 120)
    if 'starts automatically: yes' not in said('status')[1] or not os.path.exists(lnk): stop_at('the Startup shortcut does not start this copy')
    wait('the guard says in its log that it watches', lambda: 'watching for sliced plates' in log(), 30)
    time.sleep(10)                                             # some rounds of its loops: the watcher, the card's pump, the icon
    if setup.poll() is not None: stop_at(f'setup ended by itself, with exit code {setup.returncode}')
    if 'watcher running' not in said('status')[1]: stop_at('the guard did not stay running')
    code, text = said('selftest')                              # nothing to read and no printer here: said in lines, exit code 1, no traceback
    if 'Traceback' in text or code not in (0, 1): stop_at('the self-test did not end in lines')
finally:
    setup.kill()
code, text = said('uninstall')
if 'shortcut removed' not in text or 'stopped' not in text: stop_at('uninstall did not remove this copy')
wait('the guard has stopped', lambda: 'watcher not running' in said('status')[1], 60)
if os.path.exists(lnk): stop_at('the Startup shortcut is still there')
print('--- guard.log ---\n' + log())
bad = [line for line in log().splitlines() if 'has stopped on an error' in line or 'Traceback' in line or 'error in the card' in line or ' error: ' in line or 'install failed' in line]
if bad: sys.exit('the log has errors:\n' + '\n'.join(bad))
print('installed, ran, self-tested and removed')
