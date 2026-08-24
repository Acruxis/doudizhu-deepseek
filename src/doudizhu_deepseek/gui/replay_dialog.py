"""Replay dialog: step through a finished game from any player's perspective.

Rebuilds each player's hand by replaying the recorded moves over the initial
deal, so you can watch — for example — 下家/上家's own hand and verify the
logic behind what they decided to play.
"""
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QComboBox,
                             QLabel, QPushButton, QListWidget)

from ..game import PLAY_TYPE_CN, format_cards
from .card_widget import HandWidget


def replay_frames(hands, landlord, bottom, moves):
    """Simulate the recorded moves over the initial hands, snapshotting every
    step (the landlord's hand includes the 3 bottom cards). The first frame is
    the untouched opening deal (player = -1)."""
    cur = [list(h) for h in hands]
    if landlord is not None and landlord >= 0 and bottom:
        cur[landlord] = sorted(cur[landlord] + list(bottom),
                               key=lambda c: (c.rank, c.suit))
    frames = [{"player": -1, "play": None, "hands": [list(x) for x in cur]}]
    for idx, play in moves:
        if play is not None:
            for c in play.cards:
                cur[idx].remove(c)
        frames.append({"player": idx, "play": play,
                       "hands": [list(x) for x in cur]})
    return frames


class ReplayDialog(QDialog):
    """Modal replay with a perspective selector and step-by-step navigation."""

    def __init__(self, names, rec, title="回放", parent=None):
        super().__init__(parent)
        self.names = names              # ["你", 下家名, 上家名]
        self.rec = rec
        self.failures_by_move = {
            item.get("move"): item for item in rec.get("ai_failures", [])
        }
        self.frames = replay_frames(rec["hands"], rec.get("landlord"),
                                    rec.get("bottom") or [], rec["moves"])
        self.step = 0                   # start at the opening deal
        self.persp = 0
        self.setWindowTitle(title)
        self.resize(820, 620)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("视角："))
        self.persp_combo = QComboBox()
        for i, n in enumerate(self.names):
            self.persp_combo.addItem("你自己" if i == 0 else n)
        self.persp_combo.currentIndexChanged.connect(self._on_persp)
        top.addWidget(self.persp_combo)
        top.addStretch(1)
        self.info_lbl = QLabel("")
        self.info_lbl.setStyleSheet("font-size:13px; color:#444;")
        top.addWidget(self.info_lbl)
        top.addStretch(1)
        self.btn_prev = QPushButton("上一手 <")
        self.btn_next = QPushButton("> 下一手")
        self.btn_prev.clicked.connect(lambda: self._step(-1))
        self.btn_next.clicked.connect(lambda: self._step(1))
        top.addWidget(self.btn_prev)
        top.addWidget(self.btn_next)
        root.addLayout(top)

        self.hand = HandWidget()
        self.hand.setMinimumHeight(140)
        root.addWidget(self.hand, 1)

        self.action_lbl = QLabel("")
        self.action_lbl.setWordWrap(True)
        self.action_lbl.setStyleSheet("font-size:13px; color:#222; padding:4px;")
        root.addWidget(self.action_lbl)
        self._render()

    def _step(self, d):
        n = len(self.frames)
        if n == 0:
            return
        self.step = max(0, min(n - 1, self.step + d))
        self._render()

    def _on_persp(self, i):
        self.persp = i
        self._render()

    def _render(self):
        n = len(self.frames)
        if n == 0:
            self.hand.set_hand([])
            self.info_lbl.setText("（无出牌记录）")
            self.action_lbl.setText("")
            return
        self.step = max(0, min(self.step, n - 1))
        f = self.frames[self.step]
        self.hand.set_hand(f["hands"][self.persp])
        if f["player"] == -1:
            act = "（开局，无人出牌）"
        elif f["play"] is None:
            act = f"{self.names[f['player']]}　不出"
        else:
            act = (f"{self.names[f['player']]}　出【"
                   f"{PLAY_TYPE_CN.get(f['play'].play_type, f['play'].play_type)}】　"
                   f"{format_cards(f['play'].cards)}")
        lr = self.rec.get("landlord")
        lr = lr if lr in (0, 1, 2) else None
        self.info_lbl.setText(f"第 {self.step + 1}/{n} 步 · 地主：{self.names[lr] if lr is not None else '—'}")
        detail = (
            f"本步：{act}\n当前视角：{self.names[self.persp]} 的手牌"
            f"{'（含底牌）' if self.persp == lr else ''}"
        )
        failure = self.failures_by_move.get(self.step)
        if failure:
            detail += (
                f"\n⚠ AI 模型策略失败：{failure.get('reason', '未知原因')}；"
                f"困难策略接管：{failure.get('fallback', '—')}"
            )
        self.action_lbl.setText(detail)
        self.btn_prev.setEnabled(self.step > 0)
        self.btn_next.setEnabled(self.step < n - 1)


def _game_label(names, rec):
    lr = rec.get("landlord", -1)
    failures = len(rec.get("ai_failures", []))
    failure_text = f" · 模型失败 {failures} 次" if failures else ""
    return (f"{len(rec['moves'])} 手 · 地主："
            f"{names[lr] if lr in (0, 1, 2) else '—'}{failure_text}")


class HistoryDialog(QDialog):
    """A single 历史回放 button: pick among the in-progress game and the last
    3 archived games, read every play, and 回放 any archived game."""

    def __init__(self, names, current_moves, current_bidflow,
                 current_ai_failures, saved_games, parent=None):
        super().__init__(parent)
        self.names = names
        self.current_moves = current_moves
        self.current_bidflow = current_bidflow
        self.current_ai_failures = current_ai_failures
        self.saved_games = saved_games
        self._replay_index = -1
        # entries: (kind, index) — kind "current" or "arch"
        self._entries = []
        self.setWindowTitle("历史回放")
        self.resize(560, 620)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("对局："))
        self.combo = QComboBox()
        if self.current_moves:
            self._entries.append(("current", 0))
            self.combo.addItem("本局（进行中）")
        # 倒序：最近结束的局排最上面，但编号保留原始「第 N 局」
        # （最新那局显示为第 n 局，往下是第 n-1 局 … 第 1 局）。
        n = len(self.saved_games)
        for i in range(n - 1, -1, -1):
            self._entries.append(("arch", i))
            self.combo.addItem(
                f"已存档第 {i + 1} 局 · {_game_label(self.names, self.saved_games[i])}")
        self.combo.currentIndexChanged.connect(self._on_pick)
        top.addWidget(self.combo, 1)
        root.addLayout(top)

        self.list = QListWidget(self)
        root.addWidget(self.list, 1)

        row = QHBoxLayout()
        self.btn_replay = QPushButton("回放此局")
        self.btn_replay.clicked.connect(self._replay)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        row.addWidget(self.btn_replay)
        row.addStretch(1)
        row.addWidget(close)
        root.addLayout(row)

        if not self._entries:
            self.list.addItem("（暂无对局记录）")
            self.btn_replay.setEnabled(False)
        else:
            self.combo.setCurrentIndex(0)
            self._on_pick(0)

    def _on_pick(self, _i):
        i = self.combo.currentIndex()
        if i < 0 or i >= len(self._entries):
            return
        kind, idx = self._entries[i]
        if kind == "current":
            moves = self.current_moves
            bidflow = self.current_bidflow
            ai_failures = self.current_ai_failures
            self.btn_replay.setEnabled(False)
        else:
            rec = self.saved_games[idx]
            moves = rec["moves"]
            bidflow = rec.get("bidflow", [])
            ai_failures = rec.get("ai_failures", [])
            self.btn_replay.setEnabled(True)
            self._replay_index = idx
        self.list.clear()
        # 叫分/抢地主博弈（若有）先列出，再列出牌
        for p, text in bidflow:
            self.list.addItem(f"    · {self.names[p]}　{text}")
        if not moves:
            if not bidflow:
                self.list.addItem("（无记录）")
        failures_by_move = {}
        for item in ai_failures:
            failures_by_move.setdefault(item.get("move"), []).append(item)
        for n, (p, play) in enumerate(moves, 1):
            if play is None:
                self.list.addItem(f"{n:>3}. {self.names[p]}　不出")
            else:
                self.list.addItem(
                    f"{n:>3}. {self.names[p]}　"
                    f"{PLAY_TYPE_CN.get(play.play_type, play.play_type)}　"
                    f"{format_cards(play.cards)}")
            for failure in failures_by_move.get(n, []):
                self.list.addItem(
                    f"     ⚠ AI 模型策略失败：{failure.get('reason', '未知原因')}；"
                    f"困难策略接管：{failure.get('fallback', '—')}"
                )

    def _replay(self):
        if self._replay_index < 0 or self._replay_index >= len(self.saved_games):
            return
        ReplayDialog(self.names, self.saved_games[self._replay_index], "回放", self).exec_()
