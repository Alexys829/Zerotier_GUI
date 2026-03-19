import os
import sys
from pathlib import Path

from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication

from database import Database
from main_window import MainWindow
from resources import SVG_ICON, SVG_ONLINE, svg_to_qicon
from tray_icon import TrayIcon
from zerotier_client import ZeroTierClient

_SOCKET_NAME = "zerotier-gui-instance"

_DESKTOP_ENTRY = """\
[Desktop Entry]
Type=Application
Name=ZeroTier GUI
Comment=Manage ZeroTier networks
Exec={exec_path}
Icon={icon_path}
Terminal=false
Categories=Network;System;
Keywords=zerotier;vpn;network;
"""


def _install_desktop_integration() -> None:
    """Install .desktop file and icon when running as AppImage."""
    appimage = os.environ.get("APPIMAGE")
    if not appimage:
        return

    desktop_dir = Path.home() / ".local" / "share" / "applications"
    desktop_path = desktop_dir / "zerotier-gui.desktop"
    icon_dir = Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps"
    icon_path = icon_dir / "zerotier-gui.svg"

    expected_desktop = _DESKTOP_ENTRY.format(exec_path=appimage, icon_path=icon_path)

    # Install/update .desktop if missing or AppImage path changed
    if not desktop_path.exists() or desktop_path.read_text(encoding="utf-8") != expected_desktop:
        desktop_dir.mkdir(parents=True, exist_ok=True)
        desktop_path.write_text(expected_desktop, encoding="utf-8")

    # Install icon if missing
    if not icon_path.exists():
        icon_dir.mkdir(parents=True, exist_ok=True)
        icon_path.write_text(SVG_ICON, encoding="utf-8")


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("ZeroTier Manager")
    app.setWindowIcon(svg_to_qicon(SVG_ONLINE))
    app.setStyle("Fusion")

    # Load stylesheet
    qss_path = Path(__file__).parent / "style.qss"
    if qss_path.exists():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))

    # Initialize database
    Database.instance().connect()

    # Single-instance check
    socket = QLocalSocket()
    socket.connectToServer(_SOCKET_NAME)
    if socket.waitForConnected(500):
        socket.write(b"show")
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        sys.exit(0)

    # We are the first instance — start server
    QLocalServer.removeServer(_SOCKET_NAME)
    server = QLocalServer()
    server.listen(_SOCKET_NAME)

    _install_desktop_integration()

    client = ZeroTierClient()
    window = MainWindow(client)
    tray = TrayIcon()

    # Tray "Open Manager" -> show window
    tray.action_open.triggered.connect(window.show)
    tray.action_open.triggered.connect(window.raise_)
    tray.action_open.triggered.connect(window.activateWindow)

    # Tray service controls -> same as window buttons
    tray.action_start.triggered.connect(window._on_start_service)
    tray.action_stop.triggered.connect(window._on_stop_service)

    # Tray "Quit" -> exit app
    tray.action_quit.triggered.connect(app.quit)

    # Window refresh -> update tray (include saved networks + peers)
    window.refresh_completed.connect(
        lambda status, networks, peers: tray.update_state(
            status, networks, window._saved, peers
        )
    )

    # Tray "Join saved" -> join via window
    tray.join_requested.connect(window._on_join_saved)

    def _on_new_connection():
        conn = server.nextPendingConnection()
        if conn:
            conn.waitForReadyRead(1000)
            window.show()
            window.raise_()
            window.activateWindow()
            conn.deleteLater()

    server.newConnection.connect(_on_new_connection)

    tray.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
