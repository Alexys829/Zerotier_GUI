# ZeroTier GUI

A lightweight desktop GUI for managing [ZeroTier](https://www.zerotier.com/) networks on Linux.

![Python](https://img.shields.io/badge/Python-3.12-blue)
![PyQt6](https://img.shields.io/badge/PyQt6-GUI-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

## Features

- **Network Management** — Join, leave, and monitor ZeroTier networks
- **Peer Monitoring** — View connected peers with latency, roles, and paths
- **Service Control** — Start/stop ZeroTier service with polkit authentication
- **System Tray** — Minimize to tray with quick actions and notifications
- **Saved Networks** — Automatically remember networks for easy reconnection
- **Dark Theme** — Modern dark UI inspired by VS Code
- **Single Instance** — Prevents multiple instances from running

## Screenshot

The application features three tabs: **Networks**, **Peers**, and **Settings**, with a system tray icon for quick access.

## Installation

### AppImage (recommended)

Download the latest AppImage from [Releases](../../releases), then:

```bash
chmod +x ZeroTier_GUI-x86_64.AppImage
./ZeroTier_GUI-x86_64.AppImage
```

The AppImage automatically installs a `.desktop` file and icon for your application menu.

### DEB Package

```bash
bash packaging/build-deb.sh
sudo dpkg -i packaging/build/zerotier-gui_*.deb
```

### From Source

```bash
git clone https://github.com/Alexys829/Zerotier_GUI.git
cd Zerotier_GUI
pip install -r requirements.txt
python main.py
```

## Requirements

- **ZeroTier** installed via DEB package (`apt install zerotier-one`)
- **Python 3.12+** (included in AppImage)
- **PyQt6** (included in AppImage)

## Architecture

```
main.py              → Entry point, single-instance manager
main_window.py       → Main GUI window (Networks, Peers, Settings tabs)
zerotier_client.py   → ZeroTier HTTP API client (localhost:9993)
tray_icon.py         → System tray icon with context menu
database.py          → SQLite database (saved networks, settings)
resources.py         → Embedded SVG icons
style.qss            → Dark theme stylesheet
packaging/           → Build scripts (AppImage, DEB), polkit helper
```

- Communicates with ZeroTier daemon via local HTTP API on port `9993`
- Auth token read from `/var/lib/zerotier-one/authtoken.secret`
- Privileged operations (start/stop service) via `pkexec` + polkit helper
- Data stored in `~/.local/share/zerotier-gui/zerotier-gui.db` (SQLite)
- Auto-refresh every 2 seconds

## License

MIT
