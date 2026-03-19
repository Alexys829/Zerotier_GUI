from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from resources import SVG_OFFLINE, SVG_ONLINE, svg_to_qicon
from zerotier_client import ServiceState, ZtNetwork, ZtPeer, ZtStatus


class TrayIcon(QSystemTrayIcon):
    join_requested = pyqtSignal(str)  # network_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._icon_online = svg_to_qicon(SVG_ONLINE)
        self._icon_offline = svg_to_qicon(SVG_OFFLINE)

        self.setIcon(self._icon_offline)
        self.setToolTip("ZeroTier Manager")

        self._prev_online: bool | None = None  # None = first call, skip notifications
        self._prev_network_ids: set[str] = set()

        self._build_menu()
        self.activated.connect(self._on_activated)

    def _build_menu(self):
        menu = QMenu()

        self._action_status = QAction("Status: unknown")
        self._action_status.setEnabled(False)
        menu.addAction(self._action_status)

        self._menu_networks = menu.addMenu("Networks")
        self._menu_networks.addAction("(none)").setEnabled(False)

        menu.addSeparator()

        self._action_open = QAction("Open Manager")
        menu.addAction(self._action_open)

        menu.addSeparator()

        self._action_start = QAction("Start Service")
        menu.addAction(self._action_start)
        self._action_stop = QAction("Stop Service")
        menu.addAction(self._action_stop)

        menu.addSeparator()

        self._action_quit = QAction("Quit")
        menu.addAction(self._action_quit)

        self.setContextMenu(menu)

    # --- Public access to actions for signal wiring ---

    @property
    def action_open(self) -> QAction:
        return self._action_open

    @property
    def action_start(self) -> QAction:
        return self._action_start

    @property
    def action_stop(self) -> QAction:
        return self._action_stop

    @property
    def action_quit(self) -> QAction:
        return self._action_quit

    # --- Update from refresh data ---

    def update_state(
        self,
        status: ZtStatus,
        networks: list[ZtNetwork],
        saved: dict[str, str] | None = None,
        peers: list[ZtPeer] | None = None,
    ):
        # Icon
        if status.online and status.service_state == ServiceState.ACTIVE:
            self.setIcon(self._icon_online)
        else:
            self.setIcon(self._icon_offline)

        # Status text
        if status.service_state != ServiceState.ACTIVE:
            self._action_status.setText("Status: Service not running")
        elif status.online:
            self._action_status.setText(f"Status: ONLINE ({status.address})")
        else:
            self._action_status.setText("Status: OFFLINE")

        # Service actions
        is_active = status.service_state == ServiceState.ACTIVE
        self._action_start.setEnabled(not is_active)
        self._action_stop.setEnabled(is_active)

        # Networks submenu
        self._menu_networks.clear()
        if networks:
            for net in networks:
                ips = ", ".join(net.assigned_addresses) if net.assigned_addresses else "no IP"
                label = f"{net.name or net.id} — {ips}"
                self._menu_networks.addAction(label).setEnabled(False)
                # Detail row
                detail = f"  Type: {net.type or '?'}  MTU: {net.mtu}  Device: {net.device or '?'}"
                detail_action = self._menu_networks.addAction(detail)
                detail_action.setEnabled(False)
        else:
            self._menu_networks.addAction("(none)").setEnabled(False)

        # Saved networks (not currently connected)
        if saved:
            active_ids = {net.id for net in networks}
            inactive = {nid: name for nid, name in saved.items() if nid not in active_ids}
            if inactive:
                self._menu_networks.addSeparator()
                self._menu_networks.addAction("— Saved —").setEnabled(False)
                for nid, name in inactive.items():
                    label = f"Join: {name or nid}"
                    action = self._menu_networks.addAction(label)
                    action.triggered.connect(
                        lambda checked, n=nid: self.join_requested.emit(n)
                    )

        # Peer counter
        if peers is not None:
            self._menu_networks.addSeparator()
            total = len(peers)
            leaf_count = sum(1 for p in peers if p.role == "LEAF")
            self._menu_networks.addAction(
                f"Peers: {total} total, {leaf_count} leaf"
            ).setEnabled(False)

        # Desktop notifications
        current_online = status.online and status.service_state == ServiceState.ACTIVE
        current_ids = {net.id for net in networks}

        if self._prev_online is not None:  # skip first refresh
            if current_online and not self._prev_online:
                self.showMessage(
                    "ZeroTier", "Service is online",
                    QSystemTrayIcon.MessageIcon.Information, 3000,
                )
            elif not current_online and self._prev_online:
                self.showMessage(
                    "ZeroTier", "Service is offline",
                    QSystemTrayIcon.MessageIcon.Warning, 3000,
                )

            joined = current_ids - self._prev_network_ids
            left = self._prev_network_ids - current_ids

            # Build name lookup from current + previous networks
            net_names = {net.id: (net.name or net.id) for net in networks}
            for nid in left:
                net_names.setdefault(nid, nid)

            for nid in joined:
                self.showMessage(
                    "ZeroTier", f"Joined network: {net_names[nid]}",
                    QSystemTrayIcon.MessageIcon.Information, 3000,
                )
            for nid in left:
                self.showMessage(
                    "ZeroTier", f"Left network: {net_names[nid]}",
                    QSystemTrayIcon.MessageIcon.Warning, 3000,
                )

        self._prev_online = current_online
        self._prev_network_ids = current_ids

    # --- Double-click ---

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._action_open.trigger()
