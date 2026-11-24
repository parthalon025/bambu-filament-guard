"""The text of a GitHub release: python .github/scripts/release_notes.py <zip>  ->  stdout

The version's section of CHANGELOG.md, how to install, the zip's SHA-256, and what the build was run on."""
import os, re, sys, hashlib, platform
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import filament_guard as fg

here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
log = open(os.path.join(here, 'CHANGELOG.md'), encoding='utf-8').read()
found = re.search(r'^## \[%s\][^\n]*\n(.*?)(?=^## \[|^\[[^\]]+\]: )' % re.escape(fg.VERSION), log, re.S | re.M)
if not found or not found.group(1).strip(): sys.exit(f'CHANGELOG.md has no section for {fg.VERSION}')
changes = re.sub(r'^### ', '## ', found.group(1).strip(), flags=re.M)
name = os.path.basename(sys.argv[1])
sys.stdout.reconfigure(encoding='utf-8', newline='\n')
print(f"""{changes}

The changes of every version are in [CHANGELOG.md](https://github.com/parthalon025/bambu-filament-guard/blob/v{fg.VERSION}/CHANGELOG.md).

## Install, without Python

1. Download `{name}` below and unzip it where it can stay.
2. Open `Install.cmd`. The guard starts now and with Windows; `Uninstall.cmd` removes it.

Updating: quit the old copy from its icon's menu, unzip the new one, and open its `Install.cmd`. Settings, amounts entered by hand and what was learned are kept: from 1.5.0 they are in `%LOCALAPPDATA%\\BambuFilamentGuard`. Settings made with 1.4.0 or earlier are in `app\\config.json` of the old folder; `Install.cmd` brings them over when the old copy was quit, not uninstalled, and otherwise that file can be copied to `%LOCALAPPDATA%\\BambuFilamentGuard`. The README's "Updating" has the steps.

SHA-256 of `{name}`:
`{hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest()}`

Built by this repository's release workflow on {platform.system()} {platform.release()} with Python {platform.python_version()}, after its tests passed there. This build was not run against a printer before release. Licence: MIT; the bundled parts keep their own licences, which are in the zip.""")
