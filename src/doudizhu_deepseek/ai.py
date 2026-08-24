"""Local opponent AI with three difficulty levels: Easy, Medium, Hard.

Easy plays near-random legal options. Medium prefers clearing combinations and
saving big cards. Hard uses scoring heuristics plus partner cooperation. The
separate model-backed difficulty is implemented in deepseek_ai.py and never
falls back to these local strategies.
"""
import random

from .game import get_valid_plays, BOMB, ROCKET


class CardAI:
    """A configurable AI player. `decide` returns a Play object or None for pass."""

    def __init__(self, difficulty="hard", rng=None):
        self.difficulty = difficulty
        self.rng = rng or random.Random()

    # -- entry point ---------------------------------------------------------

    def decide(self, game, hand, player_idx):
        """Return a Play to make (cards drawn from hand), or None to pass."""
        is_lead = game.can_lead() and game.last_leader == player_idx
        if is_lead or game.last_play is None:
            return self._decide_lead(game, hand, player_idx)
        return self._decide_follow(game, hand, player_idx)

    def bid(self, game, player_idx):
        """Return recommended bid 0..3."""
        from .game import eval_bid_strength
        base = eval_bid_strength(game.hands[player_idx])
        if self.difficulty == "easy":
            return min(base, 1)
        if self.difficulty == "medium":
            return base
        # Hard: push up a strong hand with bottom-cards potential
        return min(3, base + 1 if base >= 2 else max(base, 1))

    def grab(self, game, player_idx):
        """抢地主 decision during the final tied-top round. Returns bool: 抢 or not."""
        from .game import eval_bid_strength
        if self.difficulty == "easy":
            return self.rng.random() < 0.4          # weak: rarely commits to 抢
        s = eval_bid_strength(game.hands[player_idx])
        if self.difficulty == "medium":
            return s >= 3
        return True                                  # hard always 抢s a strong top bid

    # -- lead ----------------------------------------------------------------

    def _decide_lead(self, game, hand, idx):
        plays = get_valid_plays(hand)
        if not plays:
            return None
        if self.difficulty == "easy":
            return self._easy_lead(plays)
        if self.difficulty == "medium":
            return self._medium_lead(plays)
        return self._hard_lead(game, hand, plays, idx)

    def _easy_lead(self, plays):
        # easy hardly plans: always lead the smallest, simplest play (tiny single)
        plays.sort(key=lambda p: (1 if p.play_type not in ('Single', 'Pair') else 0,
                                  len(p.cards), p.value))
        return plays[0]

    def _medium_lead(self, plays):
        # prefer a multi-card combination built from lower ranks; else smallest single
        combos = sorted(
            [p for p in plays if len(p.cards) >= 3 and p.play_type not in (BOMB, ROCKET)],
            key=lambda p: (len(p.cards), -p.value), reverse=True)
        if combos:
            return max(combos, key=lambda p: (len(p.cards), -p.value))
        singles = [p for p in plays if p.play_type == 'Single']
        return min(singles, key=lambda p: p.value) if singles else plays[0]

    def _hard_lead(self, game, hand, plays, idx):
        # near-empty: try to go out fast
        if len(hand) <= 5:
            no_bomb = [p for p in plays if p.play_type not in (BOMB, ROCKET)] or plays
            return max(no_bomb, key=lambda p: (len(p.cards), -p.value))
        no_bomb = [p for p in plays if p.play_type not in (BOMB, ROCKET)] or plays
        return max(no_bomb, key=lambda p: self._eval_lead(game, hand, idx, p))

    @staticmethod
    def _eval_lead(game, hand, idx, p):
        score = len(p.cards) * 2.0 - min(p.value, 10) * 0.5
        if p.play_type in ('Straight', 'DoubleStraight', 'Plane', 'PlaneSingle', 'PlanePair'):
            score += 6.0
        return score

    # -- follow --------------------------------------------------------------

    def _decide_follow(self, game, hand, idx):
        target = game.last_play
        if target is None:
            return self._decide_lead(game, hand, idx)
        valid = get_valid_plays(hand, target)
        if not valid:
            return None

        if self.difficulty == "easy":
            return self._easy_follow(valid)
        if self.difficulty == "medium":
            return self._medium_follow(valid)
        return self._hard_follow(game, hand, idx, valid)

    def _easy_follow(self, valid):
        # easy only chases with a cheap normal play; it never spends a bomb
        non_bomb = [v for v in valid if v.play_type not in (BOMB, ROCKET)]
        if not non_bomb:
            return None
        cheap = [v for v in non_bomb if v.play_type in ('Single', 'Pair')]
        return random.choice(cheap or non_bomb)

    def _medium_follow(self, valid):
        non_bomb = [v for v in valid if v.play_type not in (BOMB, ROCKET)]
        if non_bomb:
            return min(non_bomb, key=lambda p: (1 if p.play_type == 'Single' else 0, p.value))
        return min(valid, key=lambda p: p.value)

    def _hard_follow(self, game, hand, idx, valid):
        target = game.last_play
        leader = game.last_leader
        n = len(hand)

        # 1) 走完优先（主流斗地主算法最先检查的）：只要有一手能一口气出光
        #    你的全部手牌（哪怕要用炸弹/王炸），立刻出、绝不犹豫。否则很可能
        #    出现“自己剩一张能走，却因为想给队友让牌而 pass 错过直接获胜”这种
        #    明显反直觉的局面。
        for v in valid:
            if len(game.hands[idx]) == len(v.cards):
                return v

        # 2) 农民队友合作——放在“能走完”之后：只有自己并不差一步就走完时，
        #    才考虑给队友让牌/接牌，避免牺牲自己的胜机去成全别人。
        partner_leading = (not game.is_landlord(idx) and not game.is_landlord(leader)
                           and leader != idx)
        if partner_leading:
            partner = len(game.hands[leader])
            if partner <= 5:   # teammate is about to go out -> let them run
                return None
            # teammate is still developing: cover with a low play if possible,
            # so the team keeps the lead without burning strong cards
            cheap = [v for v in valid
                     if v.play_type not in (BOMB, ROCKET) and v.value <= 12]
            if cheap:
                return min(cheap, key=lambda p: (len(p.cards), p.value))
            return None

        # 3) 正常跟牌：不拆牌挑最小（让牌逻辑之外的普通对局）。
        non_bomb = [v for v in valid if v.play_type not in (BOMB, ROCKET)]
        if non_bomb:
            return min(non_bomb, key=lambda p: p.value)

        # Only bombs/rockets can beat; use only when critical or as landlord.
        if n <= 8 or game.is_landlord(idx):
            return min(valid, key=lambda p: p.value)
        return None
