"""Core Doudizhu game logic.

GUI-free so it can be unit-tested and reused by AI agents. Implements cards,
deck creation, hand-type detection, comparison rules, bidding, play flow and
win detection.

Card model: rank 3..17 (3=3 ... 15=2, 16=small joker, 17=big joker).
Suit 0..3 for normal cards; jokers use suit 4 (rank 16/17).
"""
import random
from collections import Counter, defaultdict

SUIT_CHARS = ["♠", "♥", "♦", "♣"]
RANK_CHARS = ["3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A", "2"]

# Hand type identifiers
SINGLE = "Single"
PAIR = "Pair"
TRIPLE = "Triple"
TRIPLE1 = "Triple1"            # 三带一
TRIPLE2 = "Triple2"            # 三带二(一对)
STRAIGHT = "Straight"          # ≥5 连单
DOUBLE_STRAIGHT = "DoubleStraight"  # ≥3 连对
PLANE = "Plane"                # 飞机不带
PLANE_SINGLE = "PlaneSingle"   # 飞机带单
PLANE_PAIR = "PlanePair"       # 飞机带对
FOUR_2 = "Four2"               # 四带二单
FOUR_2PAIR = "Four2Pair"       # 四带二对
BOMB = "Bomb"
ROCKET = "Rocket"

NO_JOKER_MAX = 15              # 2 是普通牌最大点数(15)；大小王 16/17
WING_ORDER = [TRIPLE1, TRIPLE2, PLANE_SINGLE, PLANE_PAIR, FOUR_2, FOUR_2PAIR]

PLAY_TYPE_CN = {
    SINGLE: "单张", PAIR: "对子", TRIPLE: "三张", TRIPLE1: "三带一",
    TRIPLE2: "三带二", STRAIGHT: "顺子", DOUBLE_STRAIGHT: "连对",
    PLANE: "飞机", PLANE_SINGLE: "飞机带单", PLANE_PAIR: "飞机带对",
    FOUR_2: "四带二", FOUR_2PAIR: "四带二对", BOMB: "炸弹", ROCKET: "王炸",
}


def format_cards(cards):
    """Render a list of cards as readable text. Only the rank is shown (no
    suit glyph), so it displays reliably on any font/system and never mixes up
    suits — the suit is irrelevant to play."""
    return " ".join(c.name() for c in sorted(cards, key=lambda c: (c.rank, c.suit)))


class Card:
    """A single playing card."""

    __slots__ = ("suit", "rank")

    def __init__(self, suit, rank):
        self.suit = suit
        self.rank = rank

    def value(self):
        return self.rank

    def is_joker(self):
        return self.rank >= 16

    def __eq__(self, other):
        return isinstance(other, Card) and self.suit == other.suit and self.rank == other.rank

    def __hash__(self):
        return self.suit * 100 + self.rank

    def __repr__(self):
        return self.name()

    def name(self):
        if self.rank == 16:
            return "小王"
        if self.rank == 17:
            return "大王"
        return RANK_CHARS[self.rank - 3]


def make_deck():
    """Return a fresh unshuffled 54-card deck."""
    deck = []
    for suit in range(4):
        for rank in range(3, 16):  # 3..2 => 13 ranks
            deck.append(Card(suit, rank))
    deck.append(Card(4, 16))  # 小王
    deck.append(Card(4, 17))  # 大王
    random.shuffle(deck)
    return deck


def rank_name(rank):
    if rank == 16:
        return "小王"
    if rank == 17:
        return "大王"
    return RANK_CHARS[rank - 3]


class Play:
    """A combination of cards played on the table."""

    __slots__ = ("cards", "play_type", "value")

    def __init__(self, cards, play_type, value):
        self.cards = sorted(cards, key=lambda c: c.rank)
        self.play_type = play_type
        self.value = value

    def beats(self, other):
        """True if this play beats `other` (None means leading a new round)."""
        if other is None:
            return True
        if self.play_type == ROCKET:
            return True
        if self.play_type == BOMB:
            if other.play_type == BOMB:
                return self.value > other.value
            if other.play_type == ROCKET:
                return False
            return True
        if other.play_type in (BOMB, ROCKET):
            return False
        if self.play_type != other.play_type:
            return False
        # Same-type combos must have equal length to be comparable: straights /
        # double-straights / planes come in multiple lengths, and a longer run
        # of the same type must NOT beat a shorter one (e.g. 45678 vs 3456789).
        if len(self.cards) != len(other.cards):
            return False
        return self.value > other.value

    def __repr__(self):
        return f"Play({self.play_type}, v={self.value})"


def _counter(cards):
    return Counter(c.rank for c in cards)


def parse_play(cards):
    """Classify `cards` into a Play, or return None if illegal.

    The returned Play.value is the comparison key: for straights/planes it is the
    highest rank of the run, otherwise the dominant rank.
    """
    n = len(cards)
    if n == 0:
        return None
    nc = _counter(cards)
    vals = sorted(nc.keys())
    cnts = sorted(nc.values(), reverse=True)

    # Rocket 王炸
    if n == 2 and 16 in nc and 17 in nc:
        return Play(cards, ROCKET, 17)

    # Single / Pair / Triple / Bomb
    if n == 1:
        return Play(cards, SINGLE, vals[0])
    if len(vals) == 1:
        c = nc[vals[0]]
        if c == 2:
            return Play(cards, PAIR, vals[0])
        if c == 3:
            return Play(cards, TRIPLE, vals[0])
        if c == 4:
            return Play(cards, BOMB, vals[0])

    # Triple with wings
    if cnts[0] == 3:
        triple_rank = next(r for r, c in nc.items() if c == 3)
        if triple_rank > NO_JOKER_MAX:
            return None
        if n == 4:
            return Play(cards, TRIPLE1, triple_rank)
        if n == 5 and sorted(nc.values()) == [2, 3]:
            return Play(cards, TRIPLE2, triple_rank)

    # Four with wings
    if cnts[0] == 4:
        four_rank = next(r for r, c in nc.items() if c == 4)
        if n == 6:
            return Play(cards, FOUR_2, four_rank)
        if n == 8 and sorted(nc.values()) == [2, 2, 4]:
            return Play(cards, FOUR_2PAIR, four_rank)

    # Sequential types use ranks 3..A only (<=14)
    usable_ok = vals[-1] <= 14

    # Straight 顺子
    if usable_ok and n >= 5 and cnts == [1] * n and _is_run(vals):
        return Play(cards, STRAIGHT, vals[-1])

    # Double straight 连对
    if usable_ok and n >= 6 and n % 2 == 0 and cnts == [2] * (n // 2) and _is_run(vals):
        return Play(cards, DOUBLE_STRAIGHT, vals[-1])

    # Plane 飞机
    trip_ranks = sorted(r for r, c in nc.items() if c >= 3 and r <= 14)
    if len(trip_ranks) >= 2:
        run = [trip_ranks[0]]
        for r in trip_ranks[1:]:
            if r == run[-1] + 1:
                run.append(r)
            else:
                break
        if len(run) >= 2:
            wings = n - 3 * len(run)
            if wings == 0:
                return Play(cards, PLANE, run[-1])
            if wings == len(run):
                return Play(cards, PLANE_SINGLE, run[-1])
            if wings == 2 * len(run):
                return Play(cards, PLANE_PAIR, run[-1])

    return None


def _is_run(ranks):
    return len(ranks) >= 2 and all(ranks[i + 1] == ranks[i] + 1 for i in range(len(ranks) - 1))


class HandAnalyzer:
    """Builds candidate Plays from concrete cards, so returned plays' cards are
    real sub-groups of the hand and can be removed directly."""

    def __init__(self, hand):
        self.hand = list(hand)
        self.by_rank = defaultdict(list)
        for c in self.hand:
            self.by_rank[c.rank].append(c)
        self.ranks = sorted(self.by_rank.keys())

    def collect(self, spec):
        """spec: iterator over (rank, count); returns real cards for the ranks."""
        cards = []
        for rank, count in spec:
            cards.extend(self.by_rank[rank][:count])
        return cards

    def all_plays(self):
        out = []
        ranks = self.ranks

        # Rocket
        if 16 in ranks and 17 in ranks:
            out.append(Play(self.collect([(16, 1), (17, 1)]), ROCKET, 17))

        # singles
        for r in ranks:
            out.append(Play(self.collect([(r, 1)]), SINGLE, r))

        # pairs
        for r in ranks:
            if len(self.by_rank[r]) >= 2:
                out.append(Play(self.collect([(r, 2)]), PAIR, r))

        # triples (+ wings)
        for r in ranks:
            if len(self.by_rank[r]) >= 3:
                out.append(Play(self.collect([(r, 3)]), TRIPLE, r))
                for k in ranks:
                    if k != r:
                        out.append(Play(self.collect([(r, 3), (k, 1)]), TRIPLE1, r))
                        break
                for k in ranks:
                    if k != r and len(self.by_rank[k]) >= 2:
                        out.append(Play(self.collect([(r, 3), (k, 2)]), TRIPLE2, r))
                        break

        # bombs + four with wings
        for r in ranks:
            if len(self.by_rank[r]) >= 4:
                out.append(Play(self.collect([(r, 4)]), BOMB, r))
                rest = [k for k in ranks if k != r]
                if len(rest) >= 2:
                    out.append(Play(self.collect([(r, 4), (rest[0], 1), (rest[1], 1)]), FOUR_2, r))
                pairs_rest = [k for k in rest if len(self.by_rank[k]) >= 2]
                if len(pairs_rest) >= 2:
                    out.append(Play(self.collect([(r, 4), (pairs_rest[0], 2), (pairs_rest[1], 2)]),
                                    FOUR_2PAIR, r))

        # straights
        self._straights(out)
        self._double_straights(out)
        self._planes(out)
        return out

    def _straights(self, out):
        u = [r for r in self.ranks if r <= 14]
        if len(u) < 5:
            return
        for i in range(len(u)):
            for j in range(i + 4, len(u)):
                seg = u[i:j + 1]
                if _is_run(seg):
                    out.append(Play(self.collect([(r, 1) for r in seg]), STRAIGHT, seg[-1]))
                else:
                    break

    def _double_straights(self, out):
        u = [r for r in self.ranks if r <= 14 and len(self.by_rank[r]) >= 2]
        if len(u) < 3:
            return
        for i in range(len(u)):
            for j in range(i + 2, len(u)):
                seg = u[i:j + 1]
                if _is_run(seg):
                    out.append(Play(self.collect([(r, 2) for r in seg]), DOUBLE_STRAIGHT, seg[-1]))
                else:
                    break

    def _planes(self, out):
        trip_ranks = [r for r in self.ranks if r <= 14 and len(self.by_rank[r]) >= 3]
        if len(trip_ranks) < 2:
            return
        for i in range(len(trip_ranks)):
            for j in range(i + 1, len(trip_ranks)):
                seg = trip_ranks[i:j + 1]
                if not _is_run(seg):
                    break
                base = [(r, 3) for r in seg]
                out.append(Play(self.collect(base), PLANE, seg[-1]))
                # wings single
                avail = [r for r in self.ranks if r not in seg]
                if len(avail) >= len(seg):
                    out.append(Play(self.collect(base + [(avail[k], 1) for k in range(len(seg))]),
                                    PLANE_SINGLE, seg[-1]))
                # wings pair
                availp = [r for r in self.ranks if r not in seg and len(self.by_rank[r]) >= 2]
                if len(availp) >= len(seg):
                    out.append(Play(self.collect(base + [(availp[k], 2) for k in range(len(seg))]),
                                    PLANE_PAIR, seg[-1]))


def get_valid_plays(hand, other=None):
    """All legal Plays from `hand` that beat `other` (or all lead types if None)."""
    plays = HandAnalyzer(hand).all_plays()
    if other is None:
        return plays
    return [p for p in plays if p.beats(other)]


# ---------------------------------------------------------------------------
# Game controller
# ---------------------------------------------------------------------------

GAME_IDLE = "idle"
GAME_BIDDING = "bidding"
GAME_GRAB = "grab"          # 抢地主 final round when the top bid is tied
GAME_PLAY = "play"
GAME_OVER = "over"

BID_PASS = 0


class DouDiZhuGame:
    """Three-player Doudizhu controller. Player 0 is the human, 1 & 2 are AI."""

    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.hands = [[], [], []]       # 20 cards each + landlord gets 3 extra
        self.bottom = []                # 3 bottom cards
        self.landlord = -1
        self.current_player = 0
        self.bid_values = [0, 0, 0]     # recorded bid (0..3)
        self.bid_calls = 0
        self.bid_order = []             # players in the order they bid
        self.grab_order = []            # tied top-bidders still deciding (抢地主)
        self.grab_landlord = -1         # landlord so far during 抢地主 round
        self.last_leader = -1           # who made the current (undercut) lead
        self.last_play = None           # current Play to beat (or None=leader may lead)
        self.pass_count = 0
        self.phase = GAME_IDLE
        self.winner = -1
        self.result = ""

    # -- setup ---------------------------------------------------------------

    def start_new_game(self):
        deck = make_deck()
        # 51 cards to players (17 each), 3 bottom
        self.hands = [[], [], []]
        for i in range(51):
            self.hands[i % 3].append(deck[i])
        self.bottom = deck[51:54]
        for h in self.hands:
            h.sort(key=lambda c: (c.rank, c.suit))
        self.landlord = -1
        self.current_player = random.randrange(3)
        self.bid_values = [0, 0, 0]
        self.bid_calls = 0
        self.bid_order = []
        self.grab_order = []
        self.grab_landlord = -1
        self.last_leader = -1
        self.last_play = None
        self.pass_count = 0
        self.winner = -1
        self.result = ""
        self.phase = GAME_BIDDING

    # -- bidding -------------------------------------------------------------

    def get_bidder_after_pass(self):
        """Return the next player still eligible to bid (bid==0 = passed)."""
        for step in range(1, 4):
            p = (self.current_player + step) % 3
            if self.bid_values[p] == 0:
                return p
        return -1

    def place_bid(self, player, value):
        """Accept a bid (0=pass, 1..3 points). Returns True if accepted."""
        if self.phase != GAME_BIDDING:
            return False
        if self.bid_values[player] != 0:
            return False
        if value < 0 or value > 3:
            return False
        self.bid_values[player] = value
        self.bid_calls += 1
        self.bid_order.append(player)
        self.current_player = (player + 1) % 3
        if self.bid_calls >= 3:
            self._finalize_bidding()
        return True

    def _finalize_bidding(self):
        """Determine landlord after all three have bid. If the highest bid is
        tied by more than one player, enter a final 抢地主 round instead of
        awarding by call order."""
        scores = {p: v for p, v in enumerate(self.bid_values)}
        best = max(scores.values())
        if best == 0:
            # everyone passed -> redeal
            self.phase = GAME_IDLE
            self.result = "无人叫地主，重新发牌"
            return
        contenders = [p for p, v in scores.items() if v == best]  # tied leaders
        if len(contenders) == 1:
            self.landlord = contenders[0]
            self._begin_game_play()
            return
        # 抢地主: 顶分平手时，按叫分顺序（第一个叫到顶分的人开始）逐人询问
        # 是否抢地主——第一个同意 抢 的人立刻成为地主。
        self.grab_order = [p for p in self.bid_order if p in contenders] or contenders
        self.grab_landlord = self.grab_order[0]  # 兜底：若无人愿意抢，回到第一个顶分者
        self.current_player = self.grab_order[0]
        self.result = ""
        self.phase = GAME_GRAB

    def grab(self, player, take):
        """抢地主 round: 顶分平手时按叫分顺序逐人询问。take=True 的**第一个**
        人立即成为地主；都拒绝时回到第一个顶分者（grab_landlord）当 landlord。"""
        if self.phase != GAME_GRAB:
            return False
        if player != self.current_player or player not in self.grab_order:
            return False
        if take:
            self.landlord = player
            self._begin_game_play()
            return True
        rest = [p for p in self.grab_order if p != player]
        if rest:
            self.grab_order = rest
            self.current_player = rest[0]
        else:
            self.landlord = self.grab_landlord
            self._begin_game_play()
        return True

    def _begin_game_play(self):
        """Promote the chosen landlord: merge bottom cards and start play."""
        self.hands[self.landlord].extend(self.bottom)
        self.hands[self.landlord].sort(key=lambda c: (c.rank, c.suit))
        self.current_player = self.landlord
        self.last_leader = self.landlord
        self.last_play = None
        self.pass_count = 0
        self.phase = GAME_PLAY

    def open_bid(self):
        """Called by GUI when ready to start bidding: pick first bidder."""
        if self.phase == GAME_IDLE:
            self.phase = GAME_BIDDING
            self.bid_calls = 0
            self.bid_values = [0, 0, 0]
            self.bid_order = []
            self.grab_order = []
            self.grab_landlord = -1

    # -- play ----------------------------------------------------------------

    def is_landlord(self, player):
        return player == self.landlord

    def can_lead(self):
        """True if current player may lead a fresh round (nobody to beat)."""
        return self.last_play is None or self.last_leader == self.current_player

    def play_cards(self, player, cards):
        """Attempt a play. Returns (ok, msg). Mutates state on success."""
        if self.phase != GAME_PLAY:
            return False, "不在出牌阶段"
        if player != self.current_player:
            return False, "还没轮到你"
        if not cards or len(cards) == 0:
            return False, "不能出空牌"
        if not all(c in self.hands[player] for c in cards):
            return False, "包含不在手牌的牌"
        play = parse_play(cards)
        if play is None:
            return False, "无效牌型"

        if not self.can_lead():
            if not play.beats(self.last_play):
                # in leading-normal rules the play must beat the last; but leader of a
                # new round may play anything
                return False, "压不过上一手牌"

        # remove cards
        for c in cards:
            self.hands[player].remove(c)
        self.last_leader = player
        self.last_play = play
        self.pass_count = 0

        if not self.hands[player]:
            self._finish(player)
        else:
            self.advance_turn()
        return True, ""

    def pass_turn(self, player):
        """Pass. Cannot pass if leading a fresh round with no one to beat."""
        if self.phase != GAME_PLAY:
            return False, "不在出牌阶段"
        if player != self.current_player:
            return False, "还没轮到你"
        if self.can_lead():
            return False, "新一轮必须出牌"
        self.pass_count += 1
        if self.pass_count >= 2:
            # both other players passed -> the last leader leads a fresh round
            leader = self.last_leader
            self.last_play = None
            self.last_leader = leader
            self.current_player = leader
            self.pass_count = 0
        else:
            self.advance_turn()
        return True, ""

    def advance_turn(self):
        self.current_player = (self.current_player + 1) % 3

    def _finish(self, player):
        self.phase = GAME_OVER
        self.winner = player
        if self.landlord == player:
            self.result = "地主胜利"
        else:
            self.result = "农民胜利"

    def ai_bid(self, player):
        """Heuristic bid decision for AI player. Returns 0..3."""
        hand = self.hands[player]
        return eval_bid_strength(hand)

    # -- info helpers --------------------------------------------------------

    def hand_size(self, player):
        return len(self.hands[player])


def eval_bid_strength(hand):
    """Return a recommended bid (0..3) based on hand strength."""
    nc = Counter(c.rank for c in hand)
    score = 0
    has_rocket = 16 in nc and 17 in nc
    bombs = sum(1 for r, c in nc.items() if c == 4)
    big = [r for r, c in nc.items() if r >= 13]  # K, A, 2, jokers
    score += len(big) * 2
    score += bombs * 6
    if has_rocket:
        score += 8
    if 17 in nc:
        score += 3
    if 16 in nc:
        score += 2
    score += sum(1 for r, c in nc.items() if c >= 3)  # triples
    if score >= 18:
        return 3
    if score >= 10:
        return 2
    if score >= 4:
        return 1
    return random.choice([0, 0, 1]) if score >= 2 else 0
