"""Draw the PolySub icon with Qt and turn it into an .icns (macOS)."""
import os
import subprocess
import sys
import tempfile

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath


def draw(size=1024) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    m = size * 0.09                       # macOS icon grid margin
    r = QRectF(m, m, size - 2 * m, size - 2 * m)
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, QColor("#4F46E5")); g.setColorAt(1, QColor("#0EA5E9"))
    path = QPainterPath(); path.addRoundedRect(r, size * 0.18, size * 0.18)
    p.fillPath(path, g)
    # "文A" glyphs
    p.setPen(QColor("white"))
    f = QFont("PingFang SC"); f.setPixelSize(int(size * 0.30)); f.setWeight(QFont.Bold)
    p.setFont(f)
    p.drawText(QRectF(r.left(), r.top() + size * 0.10, r.width(), size * 0.38), Qt.AlignCenter, "文A")
    # two subtitle bars
    p.setPen(Qt.NoPen)
    for y, w, a in ((0.62, 0.62, 255), (0.73, 0.42, 190)):
        c = QColor(255, 255, 255, a)
        bar = QRectF((size - size * w) / 2, size * y, size * w, size * 0.075)
        p.setBrush(c); p.drawRoundedRect(bar, size * 0.037, size * 0.037)
    p.end()
    return img


def main(out: str):
    app = QGuiApplication(sys.argv)  # noqa: F841 - fonts need a GUI app
    base = draw()
    with tempfile.TemporaryDirectory() as d:
        iconset = os.path.join(d, "PolySub.iconset")
        os.makedirs(iconset)
        for px in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                n = px * scale
                base.scaled(n, n, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).save(
                    os.path.join(iconset, f"icon_{px}x{px}{'@2x' if scale == 2 else ''}.png"))
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", out], check=True)
    base.save(os.path.splitext(out)[0] + ".png")
    print(out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "PolySub.icns")
