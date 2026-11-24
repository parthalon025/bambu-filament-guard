# Security

## What the guard touches

- **The LAN access code.** The guard reads it from Bambu Studio's own configuration (`%APPDATA%\BambuStudio\BambuStudio.conf`) at each check. It sends it only to the printer, on the local network, over the printer's MQTT port (8883, TLS). It is not written to the log, the state file or any window.
- **The printer.** The guard subscribes to the printer's status reports and sends one status request ("report everything") per link. While it follows a printer (for an entered amount, or for learning) it keeps that link open and reads it every 3 seconds. It sends no command that changes the printer: no print, pause, stop, temperature or motion command.
- **The printer's certificate is not verified.** The printer signs its own certificate, so the guard accepts it without checking it. The link is encrypted, but a device on your local network that impersonates the printer could receive the access code. Use the guard only on a network you trust.
- **The local network.** To find a printer's IP address the guard listens for the printer's own announcements (multicast, UDP ports 2021 and 1990). It does not scan. You can name an address in `config.json` instead.
- **The internet.** The guard makes no internet connection. The one exception is `build.py`, which downloads Python from python.org to make the packaged zip, and pip, which fetches the wheels `requirements-bundle.txt` names. Both are refused unless their SHA-256 is the one kept in the repository.
- **Files.** The guard reads the G-code Bambu Studio writes under `%TEMP%\bamboo_model`. Its log and state are in `%LOCALAPPDATA%\BambuFilamentGuard`, with `learned.json`, the history learning keeps: per printer serial and tray, the spools' RFID tag ids, their readings during past prints, the names of those print jobs, the guard's forecasts for those prints, the lowest reading each spool was seen still feeding at, the weights you entered for spools the printer measures, and the printer's error and HMS codes when a spool ran dry. It stays on this computer; `learned --forget` empties it. The guard and a command run beside it write these files one at a time (a Windows named mutex), so neither undoes the other's change. Its settings are in `config.json` beside the script. Starting automatically adds a shortcut to your Startup folder. The log and state can hold printer serial numbers and IP addresses.

## Report a vulnerability

Use GitHub's private vulnerability reporting: open this repository's **Security** tab and choose **Report a vulnerability**. Please do not open a public issue for a vulnerability.

Say what you found, how to reproduce it, and which version of the guard it affects. Never include a real access code or serial number in a report.

Only the latest release is supported.
