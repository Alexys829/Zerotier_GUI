from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from database import Database
from zerotier_client import (
    ServiceState,
    ZeroTierClient,
    ZtAuthDismissed,
    ZtCommandError,
    ZtNetwork,
    ZtPeer,
    ZtStatus,
)

REFRESH_INTERVAL_MS = 2_000
NETWORK_ID_RE = re.compile(r"^[0-9a-fA-F]{16}$")
AUTOSTART_DIR = Path.home() / ".config" / "autostart"
AUTOSTART_PATH = AUTOSTART_DIR / "zerotier-gui.desktop"
DESKTOP_FILE_PATH = Path.home() / ".local" / "share" / "applications" / "zerotier-gui.desktop"
ICON_FILE_PATH = (
    Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "zerotier-gui.svg"
)


class RefreshWorker(QThread):
    finished = pyqtSignal(object, list, list)  # (ZtStatus | None, list[ZtNetwork], list[ZtPeer])
    error = pyqtSignal(str, bool)  # (message, is_auth_dismissed)

    def __init__(self, client: ZeroTierClient, parent=None):
        super().__init__(parent)
        self._client = client

    def run(self):
        try:
            service_state = self._client.get_service_state()
            if service_state != ServiceState.ACTIVE:
                status = ZtStatus(
                    address="",
                    version="",
                    online=False,
                    service_state=service_state,
                )
                self.finished.emit(status, [], [])
                return

            status = self._client.get_status()
            networks = self._client.list_networks()
            peers = self._client.list_peers()
            self.finished.emit(status, networks, peers)
        except ZtAuthDismissed:
            self.error.emit("Authentication cancelled", True)
        except ZtCommandError as exc:
            self.error.emit(str(exc), False)


class MainWindow(QMainWindow):
    refresh_completed = pyqtSignal(object, list, list)  # forwarded to tray icon

    def __init__(self, client: ZeroTierClient, parent=None):
        super().__init__(parent)
        self._client = client
        self._db = Database.instance()
        self._worker: Optional[RefreshWorker] = None
        self._auto_refresh_paused = False
        self._saved: dict[str, str] = self._db.get_saved_networks()
        self._active_network_ids: set[str] = set()
        self._current_networks: list[ZtNetwork] = []
        self._setting_programmatic = False

        self.setWindowTitle("ZeroTier Manager")
        self.setMinimumSize(750, 500)
        self._build_ui()
        self._setup_timer()

    # --- UI construction ---

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Status area
        status_layout = QHBoxLayout()
        self._lbl_node = QLabel("Node: —")
        self._lbl_version = QLabel("Version: —")
        self._lbl_online = QLabel("Status: —")
        self._lbl_service = QLabel("Service: —")
        for lbl in (self._lbl_node, self._lbl_version, self._lbl_online, self._lbl_service):
            lbl.setObjectName("statusLabel")
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            status_layout.addWidget(lbl)
        layout.addLayout(status_layout)

        # Service controls
        svc_layout = QHBoxLayout()
        self._btn_start = QPushButton("Start Service")
        self._btn_start.setObjectName("btnStart")
        self._btn_stop = QPushButton("Stop Service")
        self._btn_stop.setObjectName("btnStop")
        self._btn_start.clicked.connect(self._on_start_service)
        self._btn_stop.clicked.connect(self._on_stop_service)
        svc_layout.addWidget(self._btn_start)
        svc_layout.addWidget(self._btn_stop)
        svc_layout.addStretch()
        layout.addLayout(svc_layout)

        # Tab widget
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_networks_tab(), "Networks")
        self._tabs.addTab(self._build_peers_tab(), "Peers")
        self._tabs.addTab(self._build_settings_tab(), "Settings")
        layout.addWidget(self._tabs)

        # Refresh button (outside tabs)
        bottom_layout = QHBoxLayout()
        self._btn_refresh = QPushButton("Refresh")
        self._btn_refresh.clicked.connect(self.do_refresh)
        bottom_layout.addStretch()
        bottom_layout.addWidget(self._btn_refresh)
        layout.addLayout(bottom_layout)

    def _build_networks_tab(self) -> QWidget:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)

        # Networks table
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Name", "Network ID", "Status", "Assigned IPs"])
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(
            lambda pos: self._show_copy_menu(self._table, pos)
        )
        self._table.selectionModel().selectionChanged.connect(self._on_network_selected)
        tab_layout.addWidget(self._table)

        # Leave + Details buttons
        btn_layout = QHBoxLayout()
        self._btn_leave = QPushButton("Leave Selected Network")
        self._btn_leave.setObjectName("btnDanger")
        self._btn_leave.clicked.connect(self._on_leave)
        btn_layout.addWidget(self._btn_leave)
        self._btn_details = QPushButton("Network Details...")
        self._btn_details.setEnabled(False)
        self._btn_details.clicked.connect(self._on_show_details)
        btn_layout.addWidget(self._btn_details)
        btn_layout.addStretch()
        tab_layout.addLayout(btn_layout)

        # Network details panel (hidden by default)
        self._details_box = QGroupBox("Network Details")
        self._details_box.setVisible(False)
        details_layout = QFormLayout(self._details_box)

        self._lbl_detail_type = QLabel("—")
        self._lbl_detail_bridge = QLabel("—")
        self._lbl_detail_mac = QLabel("—")
        self._lbl_detail_mtu = QLabel("—")
        self._lbl_detail_device = QLabel("—")
        self._lbl_detail_routes = QLabel("—")
        self._lbl_detail_broadcast = QLabel("—")
        self._lbl_detail_revision = QLabel("—")

        details_layout.addRow("Type:", self._lbl_detail_type)
        details_layout.addRow("Bridge:", self._lbl_detail_bridge)
        details_layout.addRow("MAC:", self._lbl_detail_mac)
        details_layout.addRow("MTU:", self._lbl_detail_mtu)
        details_layout.addRow("Device:", self._lbl_detail_device)
        details_layout.addRow("Routes:", self._lbl_detail_routes)
        details_layout.addRow("Broadcast:", self._lbl_detail_broadcast)
        details_layout.addRow("Config Revision:", self._lbl_detail_revision)

        # Settings checkboxes
        self._chk_managed = QCheckBox("Allow Managed Routes")
        self._chk_global = QCheckBox("Allow Global IP Assignment")
        self._chk_default = QCheckBox("Allow Default Route Override")
        self._chk_dns = QCheckBox("Allow DNS Configuration")

        for chk in (self._chk_managed, self._chk_global, self._chk_default, self._chk_dns):
            chk.stateChanged.connect(self._on_network_setting_changed)
            details_layout.addRow(chk)

        tab_layout.addWidget(self._details_box)

        # Saved networks section
        self._lbl_saved = QLabel("Saved Networks")
        self._lbl_saved.setObjectName("sectionTitle")
        tab_layout.addWidget(self._lbl_saved)

        self._saved_table = QTableWidget(0, 4)
        self._saved_table.setHorizontalHeaderLabels(["Name", "Network ID", "", ""])
        self._saved_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._saved_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        saved_header = self._saved_table.horizontalHeader()
        saved_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        saved_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        saved_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        saved_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._saved_table.verticalHeader().setVisible(False)
        self._saved_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._saved_table.customContextMenuRequested.connect(
            lambda pos: self._show_copy_menu(self._saved_table, pos)
        )
        tab_layout.addWidget(self._saved_table)

        # Join area
        join_layout = QHBoxLayout()
        join_layout.addWidget(QLabel("Network ID:"))
        self._input_join = QLineEdit()
        self._input_join.setPlaceholderText("16-digit hex ID")
        self._input_join.setMaxLength(16)
        join_layout.addWidget(self._input_join)
        self._btn_join = QPushButton("Join")
        self._btn_join.setObjectName("btnJoin")
        self._btn_join.clicked.connect(self._on_join)
        self._input_join.returnPressed.connect(self._on_join)
        join_layout.addWidget(self._btn_join)
        tab_layout.addLayout(join_layout)

        return tab

    _ROLE_ORDER = {"PLANET": 0, "ROOT": 1, "UPSTREAM": 2, "LEAF": 3}
    _ROLE_COLORS = {
        "PLANET": QColor("#5b9bd5"),   # blue
        "ROOT": QColor("#ed7d31"),     # orange
        "UPSTREAM": QColor("#a855f7"), # purple
        "LEAF": QColor("#70ad47"),     # green
    }

    def _build_peers_tab(self) -> QWidget:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)

        self._peers_table = QTableWidget(0, 6)
        self._peers_table.setHorizontalHeaderLabels(
            ["Role", "Address", "Version", "Latency", "Preferred Path", "Paths"]
        )
        self._peers_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._peers_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._peers_table.setSortingEnabled(True)
        self._peers_table.setAlternatingRowColors(True)
        peers_header = self._peers_table.horizontalHeader()
        peers_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        peers_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        peers_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        peers_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        peers_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        peers_header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self._peers_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._peers_table.customContextMenuRequested.connect(
            lambda pos: self._show_copy_menu(self._peers_table, pos)
        )
        tab_layout.addWidget(self._peers_table)

        self._lbl_peers_count = QLabel("Peers: 0")
        tab_layout.addWidget(self._lbl_peers_count)

        return tab

    def _build_settings_tab(self) -> QWidget:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)

        # Application group
        app_group = QGroupBox("Application")
        app_layout = QVBoxLayout(app_group)
        self._chk_autostart_app = QCheckBox("Start at login")
        self._chk_autostart_app.setChecked(AUTOSTART_PATH.exists())
        self._chk_autostart_app.toggled.connect(self._on_autostart_app_toggled)
        app_layout.addWidget(self._chk_autostart_app)

        self._btn_remove_menu = QPushButton("Remove from application menu")
        self._btn_remove_menu.clicked.connect(self._on_remove_from_menu)
        self._btn_remove_menu.setVisible(DESKTOP_FILE_PATH.exists())
        app_layout.addWidget(self._btn_remove_menu)

        tab_layout.addWidget(app_group)

        # Service group
        svc_group = QGroupBox("Service")
        svc_layout = QVBoxLayout(svc_group)
        self._chk_autostart_svc = QCheckBox("Start ZeroTier at boot")
        self._chk_autostart_svc.setChecked(self._client.get_service_startup())
        self._chk_autostart_svc.toggled.connect(self._on_autostart_svc_toggled)
        svc_layout.addWidget(self._chk_autostart_svc)
        tab_layout.addWidget(svc_group)

        tab_layout.addStretch()
        return tab

    def _on_autostart_app_toggled(self, checked: bool) -> None:
        if checked:
            appimage = os.environ.get("APPIMAGE")
            exec_line = appimage if appimage else "zerotier-gui"
            AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
            AUTOSTART_PATH.write_text(
                f"[Desktop Entry]\n"
                f"Type=Application\n"
                f"Name=ZeroTier GUI\n"
                f"Exec={exec_line}\n"
                f"Icon=zerotier-gui\n"
                f"Terminal=false\n"
                f"X-GNOME-Autostart-enabled=true\n",
                encoding="utf-8",
            )
            self.statusBar().showMessage("Autostart enabled", 3000)
        else:
            try:
                AUTOSTART_PATH.unlink()
            except FileNotFoundError:
                pass
            self.statusBar().showMessage("Autostart disabled", 3000)

    def _on_remove_from_menu(self) -> None:
        for path in (DESKTOP_FILE_PATH, ICON_FILE_PATH):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        self._btn_remove_menu.setEnabled(False)
        self.statusBar().showMessage("Removed from application menu", 3000)

    def _on_autostart_svc_toggled(self, checked: bool) -> None:
        try:
            self._client.set_service_autostart(checked)
            label = "enabled" if checked else "disabled"
            self.statusBar().showMessage(f"Service autostart {label}", 3000)
        except ZtCommandError as exc:
            QMessageBox.critical(self, "Error", f"Failed to change service autostart: {exc}")
            # Revert checkbox
            self._chk_autostart_svc.blockSignals(True)
            self._chk_autostart_svc.setChecked(not checked)
            self._chk_autostart_svc.blockSignals(False)

    # --- Timer ---

    def _setup_timer(self):
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer)
        self._timer.start(REFRESH_INTERVAL_MS)
        # Initial refresh
        self.do_refresh()

    def _on_timer(self):
        if not self._auto_refresh_paused:
            self.do_refresh()

    # --- Refresh ---

    def do_refresh(self):
        if self._worker is not None and self._worker.isRunning():
            return
        self._btn_refresh.setEnabled(False)
        self._worker = RefreshWorker(self._client, self)
        self._worker.finished.connect(self._on_refresh_done)
        self._worker.error.connect(self._on_refresh_error)
        self._worker.start()

    def _on_refresh_done(self, status: ZtStatus, networks: list[ZtNetwork], peers: list[ZtPeer]):
        self._auto_refresh_paused = False
        self._btn_refresh.setEnabled(True)
        self._current_networks = networks
        self._update_status(status)
        self._update_table(networks)
        self._sync_saved_names(networks)
        self._update_saved_table(networks)
        self._update_peers_table(peers)
        self._update_network_details()
        self.refresh_completed.emit(status, networks, peers)

    def _on_refresh_error(self, message: str, is_auth_dismissed: bool):
        self._btn_refresh.setEnabled(True)
        if is_auth_dismissed:
            self._auto_refresh_paused = True
        self.statusBar().showMessage(f"Error: {message}", 5000)

    # --- UI updates ---

    def _update_status(self, status: ZtStatus):
        self._lbl_node.setText(f"Node: {status.address or '—'}")
        self._lbl_version.setText(f"Version: {status.version or '—'}")

        if status.service_state != ServiceState.ACTIVE:
            self._lbl_online.setText("Status: Service not running")
            self._lbl_online.setStyleSheet("color: #f48771;")
        elif status.online:
            self._lbl_online.setText("Status: ONLINE")
            self._lbl_online.setStyleSheet("color: #89d185;")
        else:
            self._lbl_online.setText("Status: OFFLINE")
            self._lbl_online.setStyleSheet("color: #f48771;")

        self._lbl_service.setText(f"Service: {status.service_state.value}")

        is_active = status.service_state == ServiceState.ACTIVE
        self._btn_start.setEnabled(not is_active)
        self._btn_stop.setEnabled(is_active)

    def _update_table(self, networks: list[ZtNetwork]):
        self._table.setRowCount(len(networks))
        for row, net in enumerate(networks):
            self._table.setItem(row, 0, QTableWidgetItem(net.name or "(unnamed)"))
            self._table.setItem(row, 1, QTableWidgetItem(net.id))
            self._table.setItem(row, 2, QTableWidgetItem(net.status.value))
            self._table.setItem(row, 3, QTableWidgetItem(", ".join(net.assigned_addresses)))

    def _update_peers_table(self, peers: list[ZtPeer]):
        # Sort: PLANET → ROOT → UPSTREAM → LEAF, then by address
        peers_sorted = sorted(
            peers,
            key=lambda p: (self._ROLE_ORDER.get(p.role, 99), p.address),
        )

        self._peers_table.setSortingEnabled(False)
        self._peers_table.setRowCount(len(peers_sorted))

        for row, peer in enumerate(peers_sorted):
            # Role (colored)
            role_item = QTableWidgetItem(peer.role)
            role_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            role_color = self._ROLE_COLORS.get(peer.role)
            if role_color:
                role_item.setForeground(QBrush(role_color))
                font = role_item.font()
                font.setBold(True)
                role_item.setFont(font)
            self._peers_table.setItem(row, 0, role_item)

            # Address
            self._peers_table.setItem(row, 1, QTableWidgetItem(peer.address))

            # Version
            self._peers_table.setItem(row, 2, QTableWidgetItem(peer.version or "—"))

            # Latency (colored)
            if peer.latency < 0:
                lat_item = QTableWidgetItem("—")
                lat_item.setForeground(QBrush(QColor("#888888")))
            else:
                lat_item = QTableWidgetItem(f"{peer.latency} ms")
                lat_item.setData(Qt.ItemDataRole.UserRole, peer.latency)
                if peer.latency < 50:
                    lat_item.setForeground(QBrush(QColor("#70ad47")))   # green
                elif peer.latency < 200:
                    lat_item.setForeground(QBrush(QColor("#ed7d31")))   # orange
                else:
                    lat_item.setForeground(QBrush(QColor("#e04040")))   # red
            lat_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._peers_table.setItem(row, 3, lat_item)

            # Preferred path
            active_paths = [p for p in peer.paths if p.active]
            preferred = next((p for p in active_paths if p.preferred), None)
            pref_text = preferred.address if preferred else ("—" if not active_paths else active_paths[0].address)
            self._peers_table.setItem(row, 4, QTableWidgetItem(pref_text))

            # Paths count (active / total)
            total_paths = len(peer.paths)
            active_count = len(active_paths)
            paths_item = QTableWidgetItem(f"{active_count}/{total_paths}")
            paths_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if active_count == 0 and total_paths > 0:
                paths_item.setForeground(QBrush(QColor("#e04040")))
            self._peers_table.setItem(row, 5, paths_item)

        self._peers_table.setSortingEnabled(True)

        # Summary breakdown
        counts: dict[str, int] = {}
        for p in peers:
            counts[p.role] = counts.get(p.role, 0) + 1
        parts = []
        for role in ("PLANET", "ROOT", "UPSTREAM", "LEAF"):
            if role in counts:
                parts.append(f"{counts[role]} {role.capitalize()}")
        self._lbl_peers_count.setText(f"Peers: {len(peers)} total — {', '.join(parts)}")

    def _on_network_selected(self):
        has_selection = self._table.currentRow() >= 0
        self._btn_details.setEnabled(has_selection)
        if self._details_box.isVisible():
            self._update_network_details()

    def _on_show_details(self):
        visible = self._details_box.isVisible()
        self._details_box.setVisible(not visible)
        if not visible:
            self._update_network_details()

    def _update_network_details(self):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._current_networks):
            return

        net = self._current_networks[row]
        self._lbl_detail_type.setText(net.type or "—")
        self._lbl_detail_bridge.setText("Yes" if net.bridge else "No")
        self._lbl_detail_mac.setText(net.mac or "—")
        self._lbl_detail_mtu.setText(str(net.mtu) if net.mtu else "—")
        self._lbl_detail_device.setText(net.device or "—")

        if net.routes:
            route_strs = []
            for r in net.routes:
                target = r.get("target", "")
                via = r.get("via")
                route_strs.append(f"{target} via {via}" if via else target)
            self._lbl_detail_routes.setText(", ".join(route_strs))
        else:
            self._lbl_detail_routes.setText("—")

        self._lbl_detail_broadcast.setText("Yes" if net.broadcast_enabled else "No")
        self._lbl_detail_revision.setText(str(net.netconf_revision))

        # Update checkboxes with guard to avoid triggering POST
        self._setting_programmatic = True
        self._chk_managed.setChecked(net.allow_managed)
        self._chk_global.setChecked(net.allow_global)
        self._chk_default.setChecked(net.allow_default)
        self._chk_dns.setChecked(net.allow_dns)
        self._setting_programmatic = False

    def _on_network_setting_changed(self):
        if self._setting_programmatic:
            return
        row = self._table.currentRow()
        if row < 0 or row >= len(self._current_networks):
            return

        net = self._current_networks[row]
        try:
            self._client.set_network_settings(
                net.id,
                allow_managed=self._chk_managed.isChecked(),
                allow_global=self._chk_global.isChecked(),
                allow_default=self._chk_default.isChecked(),
                allow_dns=self._chk_dns.isChecked(),
            )
            self.statusBar().showMessage(f"Settings updated for {net.name or net.id}", 3000)
        except ZtCommandError as exc:
            QMessageBox.critical(self, "Error", f"Failed to update settings: {exc}")
            # Revert checkboxes to current state
            self._update_network_details()

    def _sync_saved_names(self, networks: list[ZtNetwork]) -> None:
        for net in networks:
            if net.id in self._saved and net.name and self._saved[net.id] != net.name:
                self._saved[net.id] = net.name
                self._db.update_network_name(net.id, net.name)

    def _update_saved_table(self, networks: list[ZtNetwork] | None = None) -> None:
        if networks is not None:
            self._active_network_ids = {net.id for net in networks}
        inactive = [
            (nid, name)
            for nid, name in self._saved.items()
            if nid not in self._active_network_ids
        ]
        self._saved_table.setRowCount(len(inactive))
        for row, (nid, name) in enumerate(inactive):
            self._saved_table.setItem(row, 0, QTableWidgetItem(name or "(unnamed)"))
            self._saved_table.setItem(row, 1, QTableWidgetItem(nid))

            btn_join = QPushButton("Join")
            btn_join.clicked.connect(lambda checked, n=nid: self._on_join_saved(n))
            self._saved_table.setCellWidget(row, 2, btn_join)

            btn_remove = QPushButton("Remove")
            btn_remove.clicked.connect(lambda checked, n=nid: self._on_remove_saved(n))
            self._saved_table.setCellWidget(row, 3, btn_remove)

        self._lbl_saved.setVisible(bool(inactive))
        self._saved_table.setVisible(bool(inactive))

    # --- Context menu ---

    def _show_copy_menu(self, table: QTableWidget, pos):
        item = table.itemAt(pos)
        if not item or not item.text():
            return
        menu = QMenu(self)
        action = menu.addAction(f"Copy: {item.text()}")
        action.triggered.connect(lambda: QApplication.clipboard().setText(item.text()))
        menu.exec(table.viewport().mapToGlobal(pos))

    # --- Actions ---

    def _on_join(self):
        nid = self._input_join.text().strip()
        if not NETWORK_ID_RE.match(nid):
            QMessageBox.warning(self, "Invalid ID", "Network ID must be 16 hex digits.")
            return
        try:
            self._client.join_network(nid)
            self._input_join.clear()
            if nid not in self._saved:
                self._saved[nid] = ""
                self._db.save_network(nid)
            self.statusBar().showMessage(f"Joined network {nid}", 3000)
            self.do_refresh()
        except ZtAuthDismissed:
            pass
        except ZtCommandError as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _on_join_saved(self, nid: str) -> None:
        try:
            self._client.join_network(nid)
            self.statusBar().showMessage(f"Joined network {nid}", 3000)
            self.do_refresh()
        except ZtAuthDismissed:
            pass
        except ZtCommandError as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _on_remove_saved(self, nid: str) -> None:
        self._saved.pop(nid, None)
        self._db.remove_network(nid)
        self._update_saved_table()

    def _on_leave(self):
        row = self._table.currentRow()
        if row < 0:
            QMessageBox.information(self, "No selection", "Select a network first.")
            return
        nid = self._table.item(row, 1).text()
        name = self._table.item(row, 0).text()
        reply = QMessageBox.question(
            self,
            "Leave Network",
            f"Leave network {name} ({nid})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            save_name = name if name != "(unnamed)" else ""
            if nid not in self._saved:
                self._saved[nid] = save_name
                self._db.save_network(nid, save_name)
            self._client.leave_network(nid)
            self.statusBar().showMessage(f"Left network {nid}", 3000)
            self.do_refresh()
        except ZtAuthDismissed:
            pass
        except ZtCommandError as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _on_start_service(self):
        try:
            self._client.start_service()
            self.statusBar().showMessage("Service started", 3000)
            self.do_refresh()
        except ZtAuthDismissed:
            pass
        except ZtCommandError as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _on_stop_service(self):
        try:
            self._client.stop_service()
            self.statusBar().showMessage("Service stopped", 3000)
            self.do_refresh()
        except ZtAuthDismissed:
            pass
        except ZtCommandError as exc:
            QMessageBox.critical(self, "Error", str(exc))

    # --- Window behavior ---

    def closeEvent(self, event):
        event.ignore()
        self.hide()
