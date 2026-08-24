"""记牌器 (card counter): shows how many of each rank have NOT been played yet.

Ranks 3..A,2 (4 each) plus 小王/大王 (1 each). A dimmed cell means that rank is
entirely gone from play; a highlighted cell means a lot of that rank is still out.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QGridLayout, QVBoxLayout, QLabel
from collections import Counter

from ..game import RANK_CHARS, rank_name

INITIAL = {r: (1 if r >= 16 else 4) for r in range(3, 18)}


class CardCounter(QFrame):
    """A single-row panel of per-rank remaining counts."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("counter")
        self._cells = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(8, 4, 8, 4)
        grid.setHorizontalSpacing(8)
        ranks = list(range(3, 18))
        for i, r in enumerate(ranks):
            cell = QVBoxLayout()
            cell.setSpacing(0)
            rl = QLabel(rank_name(r))
            rl.setAlignment(Qt.AlignCenter)
            cl = QLabel("4")
            cl.setAlignment(Qt.AlignCenter)
            cell.addWidget(rl)
            cell.addWidget(cl)
            frame = QFrame()
            frame.setLayout(cell)
            grid.addWidget(frame, 0, i)
            self._cells[r] = (rl, cl)
        tip = QLabel("记牌器 · 各点数剩余张数")
        tip.setStyleSheet("color:#cfe8d6; font-size:11px;")
        grid.addWidget(tip, 1, 0, 1, len(ranks))

    def set_counts(self, remaining):
        """remaining: dict rank -> int still to be played. A single uniform
        colour (a deep goldenrod, one of the earlier palettes) for every rank —
        strong enough to read clearly on the dark-green panel from the start."""
        for r in range(3, 18):
            n = remaining.get(r, INITIAL.get(r, 0))
            rl, cl = self._cells[r]
            cl.setText(str(n))
            rl.setStyleSheet("color:#b8860b; font-size:12px; font-weight:bold;")
            cl.setStyleSheet("color:#b8860b; font-size:16px; font-weight:bold;")
