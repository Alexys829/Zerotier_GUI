from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtSvg import QSvgRenderer

# ZeroTier logo shape - simplified circle with "ZT" mark
# Online: orange (#FFB000), Offline: gray (#888888)

_SVG_TEMPLATE = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <circle cx="32" cy="32" r="30" fill="{color}" stroke="{stroke}" stroke-width="2"/>
  <text x="32" y="40" text-anchor="middle" font-family="sans-serif"
        font-size="24" font-weight="bold" fill="white">ZT</text>
</svg>"""

SVG_ONLINE = _SVG_TEMPLATE.format(color="#FFB000", stroke="#E09800")
SVG_OFFLINE = _SVG_TEMPLATE.format(color="#888888", stroke="#666666")

# Static icon SVG for desktop integration (same as packaging/zerotier-gui.svg)
SVG_ICON = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <circle cx="32" cy="32" r="30" fill="#FFB000" stroke="#E09800" stroke-width="2"/>
  <text x="32" y="40" text-anchor="middle" font-family="sans-serif"
        font-size="24" font-weight="bold" fill="white">ZT</text>
</svg>"""


def svg_to_qicon(svg_data: str) -> QIcon:
    renderer = QSvgRenderer(QByteArray(svg_data.encode()))
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    from PyQt6.QtGui import QPainter
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)
