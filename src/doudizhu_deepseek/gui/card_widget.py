"""Card widgets: a polished single card and a fan-style hand container.

The hand container places cards with manual geometry so the overlap is adaptive
(heavy overlap only when many cards) and selected cards "jump up", giving a
clean, readable hand. Selection is multi-select.
"""

from PyQt5.QtCore import Qt, QEvent, QTimer, pyqtSignal
from PyQt5.QtWidgets import QFrame, QLabel, QHBoxLayout, QVBoxLayout, QWidget

from ..game import RANK_CHARS

RED_SUITS = {1, 2}   # hearts, diamonds
CARD_W = 62
CARD_H = 90
OVERHANG = 18        # extra vertical room so a selected card can rise


def rank_short(rank):
    if rank == 16:
        return "小王"
    if rank == 17:
        return "大王"
    return RANK_CHARS[rank - 3]


def suite_char_for(rank, suit):
    """No suit is drawn on the face — only the rank (the suit glyph shows up
    as tofu/[] on some setups, and it is irrelevant to play anyway)."""
    return ""


def card_color(suit, rank):
    if rank >= 16:
        return "#c2185b" if rank == 17 else "#5e35b1"
    return "#d32f2f" if suit in RED_SUITS else "#202020"


class CardWidget(QFrame):
    """A polished fixed-size card. It is purely visual: selection and all mouse
    interaction (click / drag / right-click) are handled centrally by the hand
    container (HandWidget) via an event filter."""

    def __init__(self, card, parent=None):
        super().__init__(parent)
        self.card = card
        self.selected = False
        self.setFixedSize(CARD_W, CARD_H)
        self.setCursor(Qt.PointingHandCursor)
        self._build()

    def _build(self):
        self.setObjectName("card")
        color = card_color(self.card.suit, self.card.rank)
        suit_sym = suite_char_for(self.card.rank, self.card.suit)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)

        top = QHBoxLayout()
        rank_lbl = QLabel(rank_short(self.card.rank))
        rank_lbl.setStyleSheet(f"background:transparent;color:{color};"
                               f"font-size:20px;font-weight:bold;")
        suit_top = QLabel(suit_sym)
        suit_top.setStyleSheet(f"background:transparent;color:{color};font-size:13px;")
        top.addWidget(rank_lbl)
        top.addStretch(1)
        top.addWidget(suit_top, 0, Qt.AlignTop)
        lay.addLayout(top)

        lay.addStretch(1)

        bot = QHBoxLayout()
        bot.addStretch(1)
        suit_bot = QLabel(suit_sym)
        suit_bot.setStyleSheet(f"background:transparent;color:{color};font-size:13px;")
        bot.addWidget(suit_bot, 0, Qt.AlignBottom)
        lay.addLayout(bot)

        self._apply_style()

    def _apply_style(self):
        base = ("background:#fdfcf8; border:1px solid #9a9282; border-radius:6px;")
        if self.selected:
            self.setStyleSheet(f"{base} border:3px solid #ffc400;")
        else:
            self.setStyleSheet(base)

    def set_selected(self, sel):
        self.selected = sel
        self._apply_style()

    def set_selected_silent(self, sel):
        self.selected = sel
        self._apply_style()


class HandWidget(QWidget):
    """Fan hand with adaptive overlap and multi-select. Cards are positioned
    manually so heavy overlap only happens with many cards, and a selected card
    rises out of the fan (multi-select: any number of cards).

    Emits `selection_edited` whenever the user manually clicks or drags to
    change the selection (not when the app drives it programmatically, e.g. the
    提示 button). Consumers use it to reset their own recommendation state.
    """

    selection_edited = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cards = {}            # Card -> CardWidget
        self._n = 0
        self.setMinimumHeight(CARD_H + OVERHANG + 4)
        self.setMouseTracking(True)
        # drag/interaction state
        self._press_pos = None
        self._press_card = None
        self._dragging = False
        # cards the mouse has touched during a drag (滑动选牌), and the selection
        # captured when the drag started (anchor) so drags continue a combo.
        self._drag_cards = []
        self._drag_anchor = []
        self._drag_last = None          # cache of last applied selection (skip no-op repaints)
        self._drag_pending = False
        # 合帧拖拽：高频 MouseMove 只置 pending，真正到 flush 才重算+重绘一次。
        self._drag_timer = QTimer(self)
        self._drag_timer.setSingleShot(True)
        self._drag_timer.timeout.connect(self._drag_flush)
        self.installEventFilter(self)   # so the widget's own background also handles events

    # -- population ----------------------------------------------------------

    def set_hand(self, cards):
        # 手牌没变时保留当前状态（含玩家预选的牌），不重建 widget：
        # 自己出完一手、轮到别人、再轮到自己时，之前选中的牌不应被清空。
        # （真正出了牌手牌会变少，此处自然走重建分支、清空选牌。）
        cur = sorted((c.rank, c.suit) for c in self.cards)
        new = sorted((c.rank, c.suit) for c in cards)
        if cur == new:
            return
        for w in self.cards.values():
            w.deleteLater()
        self.cards = {}
        for c in sorted(cards, key=lambda c: (c.rank, c.suit)):
            w = CardWidget(c)
            w.setParent(self)
            w.installEventFilter(self)
            w.show()
            self.cards[c] = w
        self.clear_selection(reset_reflow=False)
        self._reflow()

    # -- selection -----------------------------------------------------------

    def _on_card_clicked(self, card):
        """Plain single-card toggle on click.

        Clicking toggles ONLY the one card — auto-combination (smart selection)
        is reserved for drag ('滑动选牌') and the 提示 button, so manual selection
        is never hijacked. `card` is None (click on empty space) clears all.
        """
        if card is None:
            self.clear_selection()
            self.selection_edited.emit()
            return
        w = self.cards.get(card)
        if w is None:
            return
        w.set_selected(not w.selected)
        self._reflow()
        self.selection_edited.emit()

    def _group_by_rank(self, cards):
        by = {}
        for c in cards:
            by.setdefault(c.rank, []).append(c)
        return by

    def _longest_consec(self, ranks, min_len):
        """Longest consecutive sub-run of `ranks` with length >= min_len."""
        ranks = sorted(set(ranks))
        best = []
        cur = []
        for r in ranks:
            if cur and r == cur[-1] + 1:
                cur.append(r)
            else:
                cur = [r]
            if len(cur) >= min_len and len(cur) > len(best):
                best = list(cur)
        return best

    def _run_containing(self, ranks, target):
        """Longest consecutive run inside `ranks` that contains `target`."""
        if target not in ranks:
            return []
        lo = hi = target
        while lo - 1 in ranks:
            lo -= 1
        while hi + 1 in ranks:
            hi += 1
        return list(range(lo, hi + 1))

    def _best_combo(self, cards):
        """Best self-contained combo fully inside `cards`, preferring the one
        that uses the most cards; ties favor 连对 > 顺子 > 飞机 > 三带二/三带一.
        A drag over a 三带一 thus pulls in the wing single too."""
        by = self._group_by_rank(cards)
        pair_ranks = [r for r, cs in by.items() if len(cs) >= 2 and r <= 14]
        single_ranks = [r for r in by if r <= 14]
        trip_ranks = [r for r, cs in by.items() if len(cs) >= 3 and r <= 14]
        cands = []   # (card_count, priority, cards); lower priority wins ties

        # 连对: >=3 consecutive ranks, each holding a pair
        run = self._longest_consec(pair_ranks, 3)
        if run:
            s = []
            for r in run:
                s += by[r][:2]
            cands.append((len(s), 0, s))

        # 顺子: >=5 consecutive single ranks
        run = self._longest_consec(single_ranks, 5)
        if run:
            cands.append((len(run), 1, [by[r][0] for r in run]))

        # 飞机: >=2 consecutive trips (no wings)
        run = self._longest_consec(trip_ranks, 2)
        if run:
            s = []
            for r in run:
                s += by[r][:3]
            cands.append((len(s), 2, s))

        # 王炸: 大小王都被拖动到（或预选）时，作为独立候选（2 张，胜过单张）
        jokers = [c for c in cards if c.rank >= 16]
        if len(jokers) == 2:
            cands.append((2, 0, jokers))

        # biggest same-rank group with optional wings:
        #   四带二 / 三带二 (pair wing) or 三带一 (single wing)
        base = max(by.values(), key=len, default=[])
        if base:
            rest = [c for c in cards if c not in base]
            rest_by = self._group_by_rank(rest)
            pw = next((r for r, cs in rest_by.items() if len(cs) >= 2), None)
            cand = list(base)
            pri = 3
            if len(base) >= 3 and pw is not None:
                cand += rest_by[pw][:2]    # 三带二 / 四带二
                pri = 4 if len(base) == 4 else 3
            elif len(base) == 3 and rest:
                cand += [rest[0]]          # 三带一
            cands.append((len(cand), pri, cand))

        return max(cands, key=lambda t: (t[0], -t[1]))[2]

    def _smart_combo(self, anchor, in_range):
        """Combine the player's existing selection (anchor) with the dragged
        range, so drags can continue a combo instead of restarting one:

        - anchor = triple -> attach a pair (三带二) or single (三带一) from range,
        - anchor = pair   -> extend to 连对, or use as the wing of a range triple,
        - otherwise       -> best self-contained combo from the dragged range only.
        """
        by = self._group_by_rank(in_range)

        # anchor = clean pair
        if len(anchor) == 2 and len({c.rank for c in anchor}) == 1:
            pr = anchor[0].rank
            pair_ranks = sorted({pr} | {r for r, cs in by.items() if len(cs) >= 2})
            run = self._run_containing(pair_ranks, pr)
            if len(run) >= 3:      # 连对 extension (选了对子选连对)
                sel = list(anchor)
                for r in run:
                    if r != pr and len(by[r]) >= 2:
                        sel += by[r][:2]
                return sel
            triples = sorted(r for r, cs in by.items() if len(cs) >= 3 and r != pr)
            if triples:            # 三带二: range triple + anchor pair as wing
                return by[triples[0]][:3] + list(anchor)
            return list(anchor)

        # anchor = clean triple
        if len(anchor) == 3 and len({c.rank for c in anchor}) == 1:
            tr = anchor[0].rank
            pairs = sorted(r for r, cs in by.items() if len(cs) >= 2 and r != tr)
            if pairs:              # 三带二 (选了三带接下来选对子)
                return list(anchor) + by[pairs[0]][:2]
            singles = sorted(r for r in by if r != tr)
            if singles:            # 三带一
                return list(anchor) + [by[singles[0]][0]]
            return list(anchor)

        # otherwise: best self-contained combo from the dragged cards only
        return self._best_combo(in_range)

    # -- mouse interaction (click toggles smart groups, drag selects a range) --

    def _card_at(self, pos):
        for c, w in self.cards.items():
            if w.geometry().adjusted(-4, -4, 4, 4).contains(pos):
                return c
        return None

    def _apply_drag_selection(self):
        """Recompute the smart combo over every card the drag has touched (plus
        the anchor captured at drag start), and live-apply the selection.

        The recommendation only ever draws from the cards the mouse-sweep has
        physically touched (their hit-boxes), never from a start->cursor span,
        so sloppy sweeps still pull in exactly what was pointed at. Purely local
        — it never consults the 提示 sequence.
        """
        touched = self._drag_cards
        if not touched:
            return
        chosen = self._smart_combo(self._drag_anchor, touched)
        # skip the repaint entirely when the recommended combo hasn't changed —
        # the biggest win is not re-flowing on every jitter frame.
        key = frozenset((cc.rank, cc.suit) for cc in chosen)
        if key == self._drag_last:
            return
        self._drag_last = key
        self.clear_selection(reset_reflow=False)
        for cc in chosen:
            self.cards[cc].set_selected(True)
        self._reflow()
        self.selection_edited.emit()

    def _drag_flush(self):
        """Coalesced recompute after a burst of MouseMove events."""
        self._drag_pending = False
        self._apply_drag_selection()

    def _drag_start(self):
        self._dragging = True
        self._drag_last = None
        self._drag_pending = False
        self._apply_drag_selection()

    def eventFilter(self, obj, event):
        t = event.type()
        if t not in (QEvent.MouseButtonPress, QEvent.MouseMove, QEvent.MouseButtonRelease):
            return super().eventFilter(obj, event)
        pt = obj.mapTo(self, event.pos()) if obj is not self else event.pos()
        if t == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                self._press_pos = pt
                self._press_card = self._card_at(pt)
                self._dragging = False
                self._drag_cards = []
                self._drag_pending = False
                # snapshot the pre-drag selection so a drag can extend a combo
                self._drag_anchor = list(self.selected_cards())
            elif event.button() == Qt.RightButton:
                self.clear_selection()
            return True
        if t == QEvent.MouseMove:
            if self._press_pos is None:
                return True
            if not self._dragging:
                # start the drag only after real movement, seeding the touched
                # set with the very first card that was pressed.
                if (pt - self._press_pos).manhattanLength() > 6:
                    self._drag_cards = [self._press_card] if self._press_card else []
                    self._drag_start()
            if self._dragging:
                c = self._card_at(pt)
                if c is not None and c not in self._drag_cards:
                    self._drag_cards.append(c)
                    # only a newly-touched card can change the combo, so only
                    # then schedule a flush — pure jitter does no work.
                    self._drag_pending = True
                    self._drag_timer.start(0)   # merge same-frame moves into one pass
            return True
        # release
        if event.button() == Qt.LeftButton and self._press_pos is not None:
            if self._dragging:
                self._drag_timer.stop()
                self._apply_drag_selection()
            else:
                self._on_card_clicked(self._press_card)
        self._press_pos = None
        self._press_card = None
        self._dragging = False
        self._drag_cards = []
        self._drag_last = None
        self._drag_pending = False
        return True

    def selected_cards(self):
        return [c for c, w in self.cards.items() if w.selected]

    def clear_selection(self, reset_reflow=True):
        for w in self.cards.values():
            w.set_selected_silent(False)
        if reset_reflow:
            self._reflow()

    # -- layout --------------------------------------------------------------

    def resizeEvent(self, event):
        self._reflow()
        super().resizeEvent(event)

    def _reflow(self):
        items = sorted(self.cards.items(), key=lambda kv: (kv[0].rank, kv[0].suit))
        n = len(items)
        if n == 0:
            return
        avail = max(CARD_W, self.width() - 10)
        # adaptive spacing: spread when few cards, overlap when many
        if n * CARD_W <= avail:
            gap = min(8, (avail - n * CARD_W) // max(1, n - 1))
        else:
            gap = (avail - n * CARD_W) // (n - 1)   # negative -> overlap
        # center the whole fan horizontally inside the widget
        total = n * CARD_W + (n - 1) * gap
        x = (self.width() - total) // 2 if total < self.width() else 5
        for c, w in items:
            y = OVERHANG if not w.selected else 2   # selected rises
            w.setGeometry(int(x), int(y), CARD_W, CARD_H)
            x += CARD_W + gap
