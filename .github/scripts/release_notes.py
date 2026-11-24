"""The text of a GitHub release: python .github/scripts/release_notes.py <zip>  ->  stdout

The version's section of CHANGELOG.md, how to install and update, the zip's SHA-256, what the zip holds and what the
build was run on. Stops, with the reason, on a changelog that would give the wrong notes."""
import os, re, sys, hashlib, platform
here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, here)
import filament_guard as fg

def section(log, version):
    """The lines under '## [version]', up to the next '## ' heading, without the link definitions a changelog ends with."""
    heads = [(m.start(), m.end(), m.group(1)) for m in re.finditer(r'^## \[([^\]]+)\][^\n]*\n', log, re.M)]
    mine = [i for i, h in enumerate(heads) if h[2] == version]
    if len(mine) != 1: sys.exit(f'CHANGELOG.md has {len(mine)} sections for {version}: there must be one')
    def body(i):
        text = log[heads[i][1]:heads[i + 1][0] if i + 1 < len(heads) else len(log)]
        lines = text.rstrip().split('\n')
        while lines and re.match(r'\[[^\]]+\]: \S+$|\s*$', lines[-1]): lines.pop()     # the links at the foot of the file, where this is its last section
        return '\n'.join(lines).strip()
    waiting = [body(i) for i, h in enumerate(heads) if h[2].lower() == 'unreleased']
    if any(waiting): sys.exit(f'CHANGELOG.md still has entries under [Unreleased]: they belong under [{version}], or the version is not this one')
    if not body(mine[0]): sys.exit(f'CHANGELOG.md has nothing under {version}')
    return body(mine[0])

def bundle():
    """'paho-mqtt 2.2.0, ...': what requirements-bundle.txt pins, which is what build.py put into the zip."""
    lines = [l.split()[0] for l in open(os.path.join(here, 'requirements-bundle.txt'), encoding='utf-8') if l.strip() and not l.startswith('#')]
    return ', '.join(l.replace('==', ' ') for l in lines)

if __name__ == '__main__':
    changes = re.sub(r'^### ', '## ', section(open(os.path.join(here, 'CHANGELOG.md'), encoding='utf-8').read(), fg.VERSION), flags=re.M)
    name = os.path.basename(sys.argv[1])
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    print(f"""{changes}

The changes of every version are in [CHANGELOG.md](https://github.com/parthalon025/bambu-filament-guard/blob/v{fg.VERSION}/CHANGELOG.md).

## Install, without Python

1. Download `{name}` below and unzip it where it can stay: the guard runs from that folder.
2. Open `Install.cmd`. The guard starts now and with Windows; `Uninstall.cmd` removes it.

Windows may ask twice at the first start. "Windows protected your PC": the file came from the internet and is not signed; choose "More info", then "Run anyway". Windows Firewall, about "Python": allow it on private networks; the guard listens there for your printer announcing its address, and makes no internet connection.

## Updating

1. Quit the guard you have: "Quit" in its icon's menu.
2. Unzip the new folder and open its `Install.cmd`.
3. Delete the old folder. Do not run the old folder's `Uninstall.cmd` after installing the new one: up to 1.4.0 it removes the new guard's start with Windows.

Your settings, the amounts you entered by hand and what was learned are kept.

SHA-256 of `{name}`:
`{hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest()}`

The zip holds Python {platform.python_version()} with Tcl/Tk, and {bundle()}. It was built by this repository's release workflow on {platform.system()} {platform.release()}, after the tests passed there and the guard, started from the unzipped folder, opened its window there. This build was not run against a printer before release. Licence: MIT; the bundled parts keep their own licences, which are in the zip (`README.txt` says where).""")
