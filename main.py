import sys
from pathlib import Path

from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication

from database import Database
from main_window import MainWindow
from resources import SVG_ONLINE, svg_to_qicon
from tray_icon import TrayIcon
from zerotier_client import ZeroTierClient

_SOCKET_NAME = "zerotier-gui-instance"


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
