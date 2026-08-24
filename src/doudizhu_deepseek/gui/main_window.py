"""Main game window: layout, turn orchestration, and AI scheduling."""

import copy
import random
from collections import Counter

from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QPushButton, QFrame, QMessageBox)

from ..game import (DouDiZhuGame, GAME_IDLE, GAME_BIDDING, GAME_GRAB, GAME_PLAY,
                    GAME_OVER, BOMB, ROCKET, parse_play, rank_name,
                    PLAY_TYPE_CN, format_cards)
from ..ai import CardAI
from .. import settings
from ..deepseek_ai import AIDecisionOutcome, ai_decide_with_hard_fallback
from .card_widget import HandWidget
from .card_counter import CardCounter, INITIAL
from .settings_dialog import SettingsDialog
from .replay_dialog import ReplayDialog, HistoryDialog

PLAYER_NAMES = ["你", "小北", "小美"]   # [0]=你, [1]=下家, [2]=上家

DIFF_CN = {"easy": "简单", "medium": "中等", "hard": "困难", "ai": "AI（大模型）"}


def player_role(game, idx):
    return "地主" if game.is_landlord(idx) else "农民"


_ASYNC = object()   # sentinel: a background (network) decision is in flight


class _MoveWorker(QThread):
    """Runs a network-bound model decision off the GUI
    thread and reports the resulting Play (or None = pass) back to the main
    thread. This keeps the window responsive even when the endpoint is slow or
    unreachable, instead of freezing on a synchronous request."""
    done = pyqtSignal(int, object, object)  # (player_idx, play | None, error | None)

    def __init__(self, player_idx, fn, parent=None):
        super().__init__(parent)
        self.player_idx = player_idx
        self.fn = fn

    def run(self):
        try:
            result = self.fn()
            error = None
        except Exception as exc:
            result = None
            error = str(exc) or exc.__class__.__name__
        self.done.emit(self.player_idx, result, error)


class PlayerPanel(QFrame):
    """Left/right opponent panel: name, card count, and their last action."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.title = title
        lay = QVBoxLayout(self)
        self.name_lbl = QLabel(title)
        self.name_lbl.setAlignment(Qt.AlignCenter)
        self.name_lbl.setStyleSheet("font-size:15px; font-weight:bold; color:#fff;")
        self.count_lbl = QLabel("17 张")
        self.count_lbl.setAlignment(Qt.AlignCenter)
        self.count_lbl.setStyleSheet("color:#eee; font-size:13px;")
        self.play_lbl = QLabel("—")
        self.play_lbl.setAlignment(Qt.AlignCenter)
        self.play_lbl.setWordWrap(True)
        self.play_lbl.setStyleSheet("color:#ffe9a8; font-size:13px; min-height:60px;")
        self.reveal_lbl = QLabel("")
        self.reveal_lbl.setAlignment(Qt.AlignCenter)
        self.reveal_lbl.setWordWrap(True)
        self.reveal_lbl.setStyleSheet("color:#cde8ff; font-size:12px; min-height:40px;")
        lay.addWidget(self.name_lbl)
        lay.addWidget(self.count_lbl)
        lay.addWidget(self.play_lbl, 1)
        lay.addWidget(self.reveal_lbl)
        self.setFixedWidth(215)

    def update_panel(self, game, idx, recent):
        self.count_lbl.setText(f"{game.hand_size(idx)} 张 · {player_role(game, idx)}")
        self.name_lbl.setText(self.title)
        self.play_lbl.setText(recent_text(recent) if recent else "—")

    def set_reveal(self, text):
        """对局结束公开对手手牌：text 为空则隐藏该区域。"""
        self.reveal_lbl.setText(f"手牌: {text}" if text else "")
        self.reveal_lbl.setVisible(bool(text))

    def set_score(self, score):
        """在名字旁显示累计总分。"""
        self.name_lbl.setText(f"{self.title} · {score}分")


def recent_text(recent):
    kind, info = recent
    if kind == "none":
        return "—"
    if kind == "pass":
        return "不出"
    return f"{PLAY_TYPE_CN.get(info.play_type, info.play_type)}\n{format_cards(info.cards)}"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("斗地主 · 四档难度")
        self.resize(1200, 800)
        self.config = settings.load_config()
        self.game = DouDiZhuGame()
        self.ais = [CardAI("hard"), CardAI("hard"), CardAI("hard")]
        self._diff = "hard"
        # per-player last action: tuple (kind, info) with info = Play | None
        self.recent = [("none", None), ("none", None), ("none", None)]
        self.history = []              # 出牌历史: list of (player_idx, Play | None)
        self.ai_failures = []          # 模型策略失败及困难策略接管记录
        self._ai_failure_count = 0
        self.bid_flow = []             # 叫分/抢地主流程: list of (player_idx, text)
        self.played_count = Counter()  # 全场已出的各点数张数（记牌器用）
        self.saved_games = []          # 已结束对局存档（不设上限）
        self.scores = [0, 0, 0]        # 你/小北/小美 三家累计积分（零和）
        self._last_scores = [0, 0, 0]  # 三家本局积分变动
        self._last_score = 0           # 你上一局的积分变动（= _last_scores[0]）
        self._bomb_count = 0           # 本局打出的炸弹/王炸数（结算倍数 2^N）
        self._ref_hands = [[], [], []] # 本局初始手牌（回放用）
        self._ref_bottom = []          # 本局底牌（回放用）
        self._auto_play = False        # 托管：把「你」交给最高难度人机
        self.auto_bot = CardAI("hard") # 托管使用的最高难度人机
        self._workers = set()          # 持有后台决策线程的引用，防止被 GC
        self._decision_in_flight = False  # 同一回合只允许一个模型请求
        self._game_serial = 0          # 新对局自增，作废过期的异步结果
        # hint cycling state: rotate among candidate plays on repeated clicks
        self._hint_sig = None
        self._hint_plays = []
        self._hint_idx = -1
        self._build_ui()
        self._apply_theme()
        self.new_game()

    # -- UI construction -----------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 8)

        # top: opponents + center (bottom / trick / status)
        top = QHBoxLayout()
        self.left_panel = PlayerPanel(PLAYER_NAMES[1])
        self.right_panel = PlayerPanel(PLAYER_NAMES[2])
        center = QVBoxLayout()
        self.bottom_label = QLabel("")
        self.bottom_label.setAlignment(Qt.AlignCenter)
        self.bottom_label.setStyleSheet(
            "font-size:15px; color:#fff6e0; background:rgba(0,0,0,80);"
            "border-radius:8px; padding:6px;")
        self.trick_label = QLabel("")
        self.trick_label.setAlignment(Qt.AlignCenter)
        self.trick_label.setWordWrap(True)
        self.trick_label.setMinimumHeight(64)
        self.trick_label.setStyleSheet(
            "font-size:14px; color:#fff; background:rgba(0,0,0,70);"
            "border-radius:8px; padding:6px;")
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size:15px; font-weight:bold; color:#fff;")
        self.ai_failure_label = QLabel("")
        self.ai_failure_label.setAlignment(Qt.AlignCenter)
        self.ai_failure_label.setWordWrap(True)
        self.ai_failure_label.setStyleSheet(
            "font-size:12px; color:#ffd27f; background:rgba(90,45,0,90);"
            "border-radius:6px; padding:4px;"
        )
        self.ai_failure_label.setVisible(False)
        center.addWidget(self.bottom_label)
        center.addWidget(self.trick_label)
        center.addWidget(self.status_label)
        center.addWidget(self.ai_failure_label)
        center.addStretch(1)
        top.addWidget(self.left_panel)
        top.addLayout(center, 1)
        top.addWidget(self.right_panel)
        root.addLayout(top)

        # 记牌器 strip
        self.counter = CardCounter()
        root.addWidget(self.counter)

        # human info
        human = QHBoxLayout()
        self.human_role_lbl = QLabel("你")
        self.human_role_lbl.setStyleSheet("font-size:15px; color:#fff; font-weight:bold;")
        self.human_count_lbl = QLabel("")
        self.human_count_lbl.setStyleSheet("color:#eee; font-size:13px;")
        self.human_last_lbl = QLabel("")
        self.human_last_lbl.setStyleSheet("color:#ffe9a8; font-size:13px;")
        human.addWidget(self.human_role_lbl)
        human.addWidget(self.human_count_lbl)
        human.addSpacing(20)
        human.addWidget(self.human_last_lbl)
        human.addStretch(1)
        root.addLayout(human)

        # buttons (above the hand)
        btns = QHBoxLayout()
        self.btn_new = QPushButton("新一局")
        self.btn_set = QPushButton("设置")
        self.btn_history = QPushButton("历史/回放")
        self.btn_auto = QPushButton("托管")
        self.btn_discard = QPushButton("不出")
        self.btn_hint = QPushButton("提示")
        self.btn_play = QPushButton("出牌")
        self.btn_bid1 = QPushButton("1分")
        self.btn_bid2 = QPushButton("2分")
        self.btn_bid3 = QPushButton("3分")
        self.btn_nobid = QPushButton("不叫")
        self.btn_grab = QPushButton("抢地主")
        self.btn_nograb = QPushButton("不抢")
        for b in (self.btn_new, self.btn_set, self.btn_history, self.btn_auto,
                  self.btn_play, self.btn_discard, self.btn_hint,
                  self.btn_bid1, self.btn_bid2, self.btn_bid3, self.btn_nobid,
                  self.btn_grab, self.btn_nograb):
            b.setCursor(Qt.PointingHandCursor)
        for b in (self.btn_play, self.btn_discard, self.btn_hint):
            b.setEnabled(False)
        for b in (self.btn_bid1, self.btn_bid2, self.btn_bid3, self.btn_nobid,
                  self.btn_grab, self.btn_nograb):
            b.setVisible(False)

        self.btn_new.clicked.connect(self.new_game)
        self.btn_set.clicked.connect(self.open_settings)
        self.btn_history.clicked.connect(self.open_history)
        self.btn_auto.clicked.connect(self.toggle_auto)
        self.btn_play.clicked.connect(self.human_play)
        self.btn_discard.clicked.connect(self.human_pass)
        self.btn_hint.clicked.connect(self.human_hint)
        self.btn_bid1.clicked.connect(lambda: self.human_bid(1))
        self.btn_bid2.clicked.connect(lambda: self.human_bid(2))
        self.btn_bid3.clicked.connect(lambda: self.human_bid(3))
        self.btn_nobid.clicked.connect(lambda: self.human_bid(0))
        self.btn_grab.clicked.connect(lambda: self.human_grab(True))
        self.btn_nograb.clicked.connect(lambda: self.human_grab(False))

        btns.addWidget(self.btn_new)
        btns.addWidget(self.btn_set)
        btns.addWidget(self.btn_history)
        btns.addWidget(self.btn_auto)
        btns.addStretch(1)
        btns.addWidget(self.btn_bid1)
        btns.addWidget(self.btn_bid2)
        btns.addWidget(self.btn_bid3)
        btns.addWidget(self.btn_nobid)
        btns.addWidget(self.btn_grab)
        btns.addWidget(self.btn_nograb)
        btns.addWidget(self.btn_hint)
        btns.addWidget(self.btn_discard)
        btns.addWidget(self.btn_play)
        root.addLayout(btns)

        # hand (takes remaining vertical space; fan is centered horizontally)
        self.hand_widget = HandWidget()
        self.hand_widget.setStyleSheet("background:transparent;")
        self.hand_widget.selection_edited.connect(self._on_hand_edited)
        root.addWidget(self.hand_widget, 1)

        self._ai_timer = QTimer(self)
        self._ai_timer.setInterval(900)
        self._ai_timer.timeout.connect(self._ai_step)

    def _apply_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #1e6f3c; }
            #panel { background: rgba(0,0,0,90); border-radius:10px; }
            #counter { background: rgba(0,0,0,70); border-radius:8px; }
            QPushButton {
                background:#e8e3d3; color:#222; border:1px solid #b9b09a;
                border-radius:8px; padding:8px 16px; font-size:14px; font-weight:bold;
            }
            QPushButton:hover { background:#ffffff; }
            QPushButton:disabled { background:#9a988d; color:#cfcfc7; }
            QPushButton:focus { outline: none; }
        """)

    # -- game lifecycle ------------------------------------------------------

    def _set_diff_and_ais(self):
        diff = self.config.get("difficulty", "hard")
        self._diff = diff
        local_diff = "hard" if diff == "ai" else diff
        self.ais[1] = CardAI(local_diff)
        self.ais[2] = CardAI(local_diff)
        self.ais[0] = CardAI("hard" if local_diff == "hard" else "medium")

    def new_game(self):
        self._game_serial += 1          # 作废任何仍在途的异步决策
        self._decision_in_flight = False
        self.game.start_new_game()
        self._set_diff_and_ais()
        self.recent = [("none", None), ("none", None), ("none", None)]
        self.history = []
        self.ai_failures = []
        self._ai_failure_count = 0
        self.ai_failure_label.clear()
        self.ai_failure_label.setVisible(False)
        self.bid_flow = []
        self.played_count = Counter()
        self._ref_hands = [list(self.game.hands[i]) for i in range(3)]
        self._last_score = 0
        self._last_scores = [0, 0, 0]
        self._bomb_count = 0
        self._ref_bottom = list(self.game.bottom)
        self._begin_bidding()

    def _begin_bidding(self):
        self.game.phase = GAME_BIDDING
        self.game.current_player = random.randrange(3)
        self.game.bid_values = [0, 0, 0]
        self.game.bid_calls = 0
        self._update_status("叫地主阶段…")
        self._refresh()
        self._schedule_next()

    def _refresh(self):
        g = self.game
        self.hand_widget.set_hand(g.hands[0])
        self.left_panel.update_panel(g, 1, self.recent[1])
        self.right_panel.update_panel(g, 2, self.recent[2])
        # 对局结束：公开两名对手的手牌
        reveal = g.phase == GAME_OVER
        self.left_panel.set_reveal(format_cards(g.hands[1]) if reveal else "")
        self.right_panel.set_reveal(format_cards(g.hands[2]) if reveal else "")
        self.human_count_lbl.setText(f"{g.hand_size(0)} 张 · {player_role(g, 0)}")
        self.left_panel.set_score(self.scores[1])
        self.right_panel.set_score(self.scores[2])
        self.human_role_lbl.setText(
            f"你 · 总分 {self.scores[0]}（难度: {DIFF_CN.get(self._diff, self._diff)}）")
        self.human_last_lbl.setText("最近: " + recent_text(self.recent[0])
                                    if self.recent[0][0] != "none" else "")

        # bottom cards are revealed only after the landlord is decided
        if g.phase == GAME_PLAY and g.landlord >= 0 and g.bottom:
            self.bottom_label.setText(
                f"底牌（{PLAYER_NAMES[g.landlord]}·地主）: {format_cards(g.bottom)}")
        elif g.phase == GAME_OVER and g.bottom:
            self.bottom_label.setText(f"底牌: {format_cards(g.bottom)}")
        else:
            self.bottom_label.setText("叫地主阶段 · 底牌将在确定地主后公开")

        # the trick to beat
        self._update_trick()

        # 记牌器: 外面剩余张数 = 该点总张数 - 全场已出 - 自己(人类)手里的张数
        own = Counter(c.rank for c in g.hands[0])
        remaining = {r: INITIAL[r] - self.played_count.get(r, 0) - own.get(r, 0)
                     for r in range(3, 18)}
        self.counter.set_counts(remaining)

    TRICK_STYLE = (
        "font-size:14px; color:#fff; background:rgba(0,0,0,70);"
        "border-radius:8px; padding:6px;")

    def _settle_score(self):
        """结算本局积分（三家零和）：基础分 = 地主的叫分。地主胜时地主得
        2 倍、每名农民各失 1 倍；农民胜则反过来。本局每打出 1 个炸弹/王炸，
        所有分数翻倍（多个炸弹为 2^N 倍率）。重新发牌不计分。"""
        g = self.game
        self._last_scores = [0, 0, 0]
        self._last_score = 0
        if not g.result or g.landlord < 0 or g.bid_values[g.landlord] <= 0:
            return
        bid = g.bid_values[g.landlord]
        mult = 2 ** self._bomb_count
        lr = g.landlord
        if g.result == "地主胜利":
            self._last_scores[lr] = bid * 2 * mult
            for i in range(3):
                if i != lr:
                    self._last_scores[i] = -bid * mult
        else:                                # 农民胜利
            self._last_scores[lr] = -bid * 2 * mult
            for i in range(3):
                if i != lr:
                    self._last_scores[i] = bid * mult
        self._last_score = self._last_scores[0]
        for i in range(3):
            self.scores[i] += self._last_scores[i]

    def _bid_progress_text(self):
        """叫分/抢地主进度展示：把已发生的叫分(或抢地主)逐条列出。"""
        parts = [("你" if p == 0 else PLAYER_NAMES[p]) + t
                 for p, t in self.bid_flow]
        if self.game.phase == GAME_GRAB:
            who = self.game.current_player
            parts.append(("你" if who == 0 else PLAYER_NAMES[who]) + "？")
        return "  ·  ".join(parts) if parts else "叫地主阶段…"

    def _update_trick(self):
        g = self.game
        self.trick_label.setStyleSheet(self.TRICK_STYLE)
        if g.phase in (GAME_BIDDING, GAME_GRAB):
            self.trick_label.setText(self._bid_progress_text())
            return
        if g.phase != GAME_PLAY:
            self.trick_label.setText("")
            return
        if g.can_lead() or g.last_play is None:
            self.trick_label.setText(">> 新一轮 · 出任意合法牌型")
            return
        lp = g.last_play
        who = PLAYER_NAMES[g.last_leader]
        self.trick_label.setText(
            f"{who} 出了【{PLAY_TYPE_CN.get(lp.play_type, lp.play_type)}】\n{format_cards(lp.cards)}")

    def _update_status(self, text):
        self.status_label.setText(text)

    # -- orchestrating turns -------------------------------------------------

    def _whose_bid_turn(self):
        return self.game.current_player

    def _schedule_next(self):
        g = self.game
        if g.phase == GAME_OVER:
            return
        if g.phase == GAME_IDLE:
            self._update_status("无人叫地主，重新发牌")
            self.new_game()
            return
        if g.phase == GAME_BIDDING:
            p = self._whose_bid_turn()
            if g.bid_values[p] != 0:
                g.current_player = (p + 1) % 3
                self._schedule_next()
                return
            if p == 0 and not self._auto_play:
                self._show_bid_buttons(True)
            else:
                self._show_bid_buttons(False)
                self._ai_timer.start()
            return
        if g.phase == GAME_GRAB:
            if g.current_player == 0 and not self._auto_play:
                self._ai_timer.stop()
                self._show_grab_buttons(True)
                self._update_status("抢地主：选择是否抢地主")
            else:
                self._show_grab_buttons(False)
                self._ai_timer.start()
            return
        # GAME_PLAY
        self._show_grab_buttons(False)
        self._show_bid_buttons(False)
        if g.current_player == 0 and not self._auto_play:
            self._ai_timer.stop()
            self._enable_human(g)
        else:
            self._ai_timer.start()

    def _ai_step(self):
        g = self.game
        if g.phase == GAME_OVER:
            self._ai_timer.stop()
            return
        if g.phase == GAME_GRAB:
            p = g.current_player
            if p == 0 and not self._auto_play:
                self._show_grab_buttons(True)
                self._ai_timer.stop()
                return
            take = self.ais[p].grab(g, p)
            g.grab(p, take)
            self.bid_flow.append((p, '抢地主' if take else '不抢'))
            self._update_status(f"{PLAYER_NAMES[p]} {'抢地主！' if take else '不抢'}")
            self._refresh()
            self._schedule_next()
            return
        if g.phase == GAME_BIDDING:
            p = self._whose_bid_turn()
            if p == 0 and not self._auto_play:
                self._show_bid_buttons(True)
                self._ai_timer.stop()
                return
            bid = self.ais[p].bid(g, p)
            g.place_bid(p, bid)
            self.bid_flow.append((p, '不叫' if bid == 0 else f'叫{bid}分'))
            self._update_status(f"{PLAYER_NAMES[p]} {'不叫' if bid == 0 else str(bid) + '分'}")
            if g.phase == GAME_OVER:
                self._on_phase_change()
                return
            self._refresh()
            self._schedule_next()
            return

        # GAME_PLAY: an AI player's turn, or "你" under 托管
        p = g.current_player
        if p == 0 and not self._auto_play:
            self._ai_timer.stop()
            self._enable_human(g)
            return
        play = self._auto_turn(g) if p == 0 else self._ai_turn(g, p)
        if play is _ASYNC:
            self._ai_timer.stop()        # 后台决策在途，等 _async_done 继续
            return
        self._apply_ai_result(p, play)

    def _ai_turn(self, g, p):
        """Use the model only for the standalone AI difficulty."""
        if self._diff == "ai":
            game_snapshot = copy.deepcopy(g)
            hand = list(game_snapshot.hands[p])
            history = list(self.history)
            return self._spawn_decision(
                p, lambda: ai_decide_with_hard_fallback(
                    game_snapshot, hand, p, self.config, self.ais[p],
                    history=history
                )
            )
        return self.ais[p].decide(g, g.hands[p], p)

    def _auto_turn(self, g):
        """Decide for the human under 托管 — always the highest-difficulty bot
        or the model when the standalone AI difficulty is selected."""
        p = 0
        if self._diff == "ai":
            game_snapshot = copy.deepcopy(g)
            hand = list(game_snapshot.hands[p])
            history = list(self.history)
            return self._spawn_decision(
                p, lambda: ai_decide_with_hard_fallback(
                    game_snapshot, hand, p, self.config, self.auto_bot,
                    history=history
                )
            )
        return self.auto_bot.decide(g, g.hands[0], 0)

    def _spawn_decision(self, p, fn):
        """Run `fn` in a worker thread; returns the _ASYNC sentinel. The result
        is applied later on the GUI thread by _async_done."""
        if self._decision_in_flight:
            return _ASYNC
        self._decision_in_flight = True
        w = _MoveWorker(p, fn)
        serial = self._game_serial
        w.done.connect(
            lambda idx, pl, err, s=serial: self._async_done(idx, pl, err, s))
        w.finished.connect(lambda ww=w: (self._workers.discard(ww), ww.deleteLater()))
        self._workers.add(w)
        w.start()
        return _ASYNC

    def _async_done(self, p, play, error, serial):
        if serial != self._game_serial:
            return                       # 已在后台决策期间开了新对局，丢弃
        self._decision_in_flight = False
        if error:
            self._ai_timer.stop()
            self._update_status(f"AI 调用失败：{error}")
            QMessageBox.critical(
                self,
                "AI 调用失败",
                f"大模型无法完成本次决策：\n{error}\n\n"
                "当前对局已暂停，不会改用本地人机策略。请检查设置后重试或开始新一局。",
            )
            return
        model_failure = None
        if isinstance(play, AIDecisionOutcome):
            model_failure = play.model_failure
            play = play.play
        self._apply_ai_result(p, play, model_failure)

    def _apply_ai_result(self, p, play, model_failure=None):
        """Execute a decided move (play or pass) for player `p` and advance."""
        g = self.game
        if play is None:
            ok, msg = g.pass_turn(p)
            if not ok:
                self._update_status(f"AI 决策无法执行：{msg}")
                return
            self.recent[p] = ("pass", None)
            self._append_history(p, None)
            status = f"{PLAYER_NAMES[p]} 不出"
        else:
            ok, msg = g.play_cards(p, play.cards)
            if ok:
                self.recent[p] = ("play", play)
                self._append_history(p, play)
                self.played_count.update(c.rank for c in play.cards)
                if play.play_type in (BOMB, ROCKET):
                    self._bomb_count += 1
                status = (
                    f"{PLAYER_NAMES[p]} 出了【"
                    f"{PLAY_TYPE_CN.get(play.play_type, play.play_type)}】"
                )
            else:
                self._update_status(f"AI 决策无法执行：{msg}")
                return
        if model_failure:
            self._record_ai_failure(p, model_failure)
            status += f" · 模型策略失败，已由困难策略接管（本局 {self._ai_failure_count} 次）"
        self._update_status(status)
        if g.phase == GAME_OVER:
            self._on_phase_change()
            return
        self._refresh()
        self._schedule_next()

    # -- bidding buttons -----------------------------------------------------

    def _show_bid_buttons(self, show):
        for b in (self.btn_bid1, self.btn_bid2, self.btn_bid3, self.btn_nobid):
            b.setVisible(show)
            b.setEnabled(show)
        for b in (self.btn_grab, self.btn_nograb):
            b.setVisible(False)
        for b in (self.btn_play, self.btn_discard, self.btn_hint):
            b.setEnabled(False)

    def _show_grab_buttons(self, show):
        """Show the 抢地主 / 不抢 buttons during the final grab round."""
        for b in (self.btn_grab, self.btn_nograb):
            b.setVisible(show)
            b.setEnabled(show)
        for b in (self.btn_bid1, self.btn_bid2, self.btn_bid3, self.btn_nobid):
            b.setVisible(False)
        for b in (self.btn_play, self.btn_discard, self.btn_hint):
            b.setEnabled(False)

    def human_bid(self, value):
        self._show_bid_buttons(False)
        ok = self.game.place_bid(0, value)
        if not ok:
            self._schedule_next()
            return
        self.bid_flow.append((0, '不叫' if value == 0 else f'叫{value}分'))
        self._update_status(f"你 {'不叫' if value == 0 else str(value) + '分'}")
        if self.game.phase == GAME_OVER:
            self._on_phase_change()
            return
        self._refresh()
        self._schedule_next()

    def human_grab(self, take):
        self._show_grab_buttons(False)
        self.game.grab(0, take)
        self.bid_flow.append((0, '抢地主' if take else '不抢'))
        self._update_status(f"你 {'抢地主！' if take else '不抢'}")
        if self.game.phase == GAME_PLAY:
            self._refresh()
            self._schedule_next()
            return
        self._refresh()
        self._schedule_next()

    # -- play controls -------------------------------------------------------

    def toggle_auto(self):
        """托管开关：把「你」交给最高难度人机代打（含叫分/抢地主/出牌）。"""
        self._auto_play = not self._auto_play
        self.btn_auto.setText("取消托管" if self._auto_play else "托管")
        self.btn_auto.setStyleSheet("background:#ffc400; color:#000;"
                                    if self._auto_play else "")
        if not self._auto_play:
            return
        # 若此刻正轮到「你」行动，立即改由人机接管
        g = self.game
        if g.phase in (GAME_BIDDING, GAME_GRAB, GAME_PLAY):
            self._schedule_next()

    def _enable_human(self, g):
        self.btn_hint.setEnabled(True)
        self.btn_play.setEnabled(True)
        self.btn_discard.setEnabled(not g.can_lead())
        self._update_status("轮到你出牌…" if g.phase == GAME_PLAY else "")

    def human_play(self):
        g = self.game
        cards = self.hand_widget.selected_cards()
        if not cards:
            self._update_status("请先选择要出的牌（鼠标点击选中，可多选）")
            return
        ok, msg = g.play_cards(0, cards)
        if not ok:
            self._update_status("无效出牌：" + msg)
            return
        play = parse_play(cards)
        self.recent[0] = ("play", play)
        self._append_history(0, play)
        self.played_count.update(c.rank for c in cards)
        if play.play_type in (BOMB, ROCKET):
            self._bomb_count += 1
        self.hand_widget.clear_selection()
        self._after_human(g, f"你出了【{PLAY_TYPE_CN.get(play.play_type, play.play_type)}】")

    def human_pass(self):
        g = self.game
        if g.can_lead():
            self._update_status("新一轮必须出牌")
            return
        ok, _ = g.pass_turn(0)
        if not ok:
            self._update_status("当前不能不出")
            return
        self.recent[0] = ("pass", None)
        self._append_history(0, None)
        self._after_human(g, "你不出")

    def _append_history(self, idx, play):
        """Record a play (or pass, play=None) by player `idx` for the history."""
        self.history.append((idx, play))

    def _record_ai_failure(self, idx, reason):
        """Persist one model failure after its hard-AI fallback move completed."""
        self._ai_failure_count += 1
        fallback = recent_text(self.recent[idx]).replace("\n", " ")
        self.ai_failures.append({
            "move": len(self.history),
            "player": idx,
            "reason": reason,
            "fallback": fallback,
        })
        self.ai_failure_label.setText(
            f"AI 模型策略失败：本局 {self._ai_failure_count} 次 · 最近 {PLAYER_NAMES[idx]}："
            f"{reason}（困难策略：{fallback}）"
        )
        self.ai_failure_label.setVisible(True)

    def open_history(self):
        """Unified 回放 button: pick the in-progress game or one of the last 3
        archived finished games, then browse/replay any of them."""
        from ..game import GAME_PLAY
        current = self.history if self.game.phase == GAME_PLAY else []
        current_failures = self.ai_failures if current else []
        HistoryDialog(
            PLAYER_NAMES, current, self.bid_flow, current_failures,
            self.saved_games, self
        ).exec_()

    def _on_hand_edited(self):
        """Player manually changed the selection (click / drag / clear) ->
        restart the 提示 rotation from the initial best suggestion."""
        self._hint_sig = None
        self._hint_plays = []
        self._hint_idx = -1

    def human_hint(self):
        g = self.game
        hand = g.hands[0]
        target = None if (g.can_lead() or g.last_play is None) else g.last_play
        from ..game import get_valid_plays
        plays = get_valid_plays(hand, target)
        if not plays:
            self._update_status("没有能出的牌，选择「不出」")
            return
        # Dedupe by (type, value, length) so cycling shows distinct choices
        # (suit variants of the same combo collapse into one suggestion).
        seen, uniq = set(), []
        for p in plays:
            k = (p.play_type, p.value, len(p.cards))
            if k not in seen:
                seen.add(k)
                uniq.append(p)
        from collections import Counter
        counts = Counter(c.rank for c in hand)

        def _split(p):
            """True if this play pulls cards out of a bigger group of the same
            rank held in hand (i.e. it dismantles a pair/triple/bomb)."""
            t, v = p.play_type, p.value
            if t == 'Single':
                return counts.get(v, 0) >= 2
            if t == 'Pair':
                return counts.get(v, 0) >= 3
            if t == 'Triple':
                return counts.get(v, 0) >= 4
            if t in ('Bomb', 'Rocket'):
                return False                      # penalized separately below
            return False

        def _key_follow(p):
            """压牌 (there is a play to beat): prefer the cheapest non-breaking
            answer, so the cycle starts small→large and never reaches for the
            王炸 first:
               0. normal same-type plays, value small→large (不拆牌)
               1. plays that break a pair/triple/bomb (拆对/拆三/拆炸，实在没牌才用)
               2. bombs / rocket (大牌留在最后，王炸绝不优先选)
            """
            if p.play_type == 'Rocket':
                group = 3
            elif p.play_type == 'Bomb':
                group = 2
            else:
                group = 1 if _split(p) else 0
            return (group, p.value, len(p.cards))

        def _key_lead(p):
            """自己出牌 (nothing to beat): 优先选含牌量最多的组合；含牌量相同时
            再偏向不拆牌、走小牌，炸弹/王炸仍靠后。"""
            if p.play_type == 'Rocket':
                group = 3
            elif p.play_type == 'Bomb':
                group = 2
            else:
                group = 1 if _split(p) else 0
            return (-len(p.cards), group, p.value)

        if target is None:
            uniq.sort(key=_key_lead)
        else:
            uniq.sort(key=_key_follow)
        # Reset the rotation whenever the situation changed (hand or card to
        # beat) or the player manually edited the selection (so the next hint
        # always starts from the best suggestion again).
        sig = (tuple(sorted(c.rank for c in hand)),
               None if target is None
               else (target.play_type, target.value, len(target.cards)))
        if self._hint_sig != sig:
            self._hint_plays = uniq
            self._hint_idx = -1
            self._hint_sig = sig
        # Rotate to the next suggestion on each click, wrapping around.
        self._hint_idx = (self._hint_idx + 1) % len(self._hint_plays)
        pick = self._hint_plays[self._hint_idx]
        self._select_hint(pick.cards)
        self._update_status(
            f"提示 {self._hint_idx + 1}/{len(self._hint_plays)} · "
            f"{PLAY_TYPE_CN.get(pick.play_type, pick.play_type)}：已选中建议出牌")

    def _select_hint(self, cards):
        self.hand_widget.clear_selection(reset_reflow=False)
        for c in cards:
            w = self.hand_widget.cards.get(c)
            if w:
                w.set_selected_silent(True)
        self.hand_widget._reflow()

    def _after_human(self, g, note):
        self._update_status(note)
        if g.phase == GAME_OVER:
            self._on_phase_change()
            return
        self._refresh()
        for b in (self.btn_play, self.btn_discard, self.btn_hint):
            b.setEnabled(False)
        self._schedule_next()

    # -- finish --------------------------------------------------------------

    def _on_phase_change(self):
        g = self.game
        self._ai_timer.stop()
        for b in (self.btn_play, self.btn_discard, self.btn_hint):
            b.setEnabled(False)
        self._show_bid_buttons(False)
        self._settle_score()
        self._refresh()
        # archive this finished game (历史存满即可无限累积；每局只几十 KB)
        self.saved_games.append({
            "hands": self._ref_hands,
            "bottom": self._ref_bottom,
            "landlord": g.landlord,
            "moves": list(self.history),
            "bidflow": list(self.bid_flow),   # 叫分/抢地主博弈也一并存档
            "ai_failures": list(self.ai_failures),
        })
        if g.result:
            # 胜负结果直接在出牌区醒目显示，另附本局得分与累计分；
            # 不弹窗、不询问是否回放；需要看回放随时点「历史/回放」。
            self.trick_label.setStyleSheet(
                "font-size:22px; font-weight:bold; color:#ffe066;"
                "background:rgba(0,0,0,80); border-radius:8px; padding:12px;")
            if g.result == "无人叫地主，重新发牌":
                self.trick_label.setText(g.result)
            else:
                sign = "+" if self._last_score >= 0 else ""
                bomb_line = f"  ×{2 ** self._bomb_count}" if self._bomb_count else ""
                self.trick_label.setText(
                    f"{g.result}\n你本局 {sign}{self._last_score} 分{bomb_line}")
            self._update_status(
                f"对局结束 · 累计 {self.scores[0]} 分 · 点「新一局」开新局，"
                f"或「历史/回放」看存档")

    # -- settings ------------------------------------------------------------

    def open_settings(self):
        old_config = dict(self.config)
        dlg = SettingsDialog(self, dict(self.config))
        if dlg.exec_():
            self.config = dlg.config
            if self.config == old_config:
                return
            # Any in-flight result was created from the old difficulty/API
            # config. Invalidate it immediately and let the current turn be
            # scheduled once with the new settings.
            self._game_serial += 1
            self._decision_in_flight = False
            self._ai_timer.stop()
            self._set_diff_and_ais()
            self.human_role_lbl.setText(
                f"你 · 总分 {self.scores[0]}（难度: "
                f"{DIFF_CN.get(self._diff, self._diff)}）"
            )
            self._schedule_next()
