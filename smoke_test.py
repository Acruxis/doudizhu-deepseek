"""Quick correctness smoke test for the game engine, AI and GUI construction.

Run:  python smoke_test.py
Exits non-zero if anything fails.
"""
import random
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

from doudizhu_deepseek.game import (Card, parse_play, Play, get_valid_plays,
                                    DouDiZhuGame, _is_run, rank_name, make_deck)
from doudizhu_deepseek.ai import CardAI
from doudizhu_deepseek import settings
import doudizhu_deepseek.deepseek_ai as ds


def mk(ranks):
    m = {"3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "10": 10,
         "J": 11, "Q": 12, "K": 13, "A": 14, "2": 15, "小王": 16, "大王": 17}
    out = []
    for r in ranks:
        rr = m.get(str(r), r)
        out.append(Card(4 if rr >= 16 else 0, rr))
    return out


PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {name}")


def expect_type(ranks, expected):
    p = parse_play(mk(ranks))
    check(f"{ranks} -> {expected}", p is not None and p.play_type == expected)


def main():
    # --- hand-type recognition ---
    expect_type(["3"], "Single")
    expect_type(["3", "3"], "Pair")
    expect_type(["3", "3", "3"], "Triple")
    expect_type(["3", "3", "3", "4"], "Triple1")
    expect_type(["3", "3", "3", "4", "4"], "Triple2")
    expect_type(["3", "4", "5", "6", "7"], "Straight")
    expect_type(["10", "J", "Q", "K", "A"], "Straight")
    expect_type(["3", "3", "4", "4", "5", "5"], "DoubleStraight")
    expect_type(["3", "3", "3", "4", "4", "4"], "Plane")
    expect_type(["3", "3", "3", "4", "4", "4", "5", "6"], "PlaneSingle")
    expect_type(["3", "3", "3", "4", "4", "4", "5", "5", "6", "6"], "PlanePair")
    expect_type(["3", "3", "3", "3"], "Bomb")
    expect_type(["3", "3", "3", "3", "4", "5"], "Four2")
    expect_type(["3", "3", "3", "3", "4", "4", "5", "5"], "Four2Pair")
    expect_type(["小王", "大王"], "Rocket")
    # illegal
    check("3,3,4 illegal", parse_play(mk(["3", "3", "4"])) is None)
    check("4,5,6 illegal", parse_play(mk(["4", "5", "6"])) is None)
    check("3,3,3,4,5 illegal (=三带两单)", parse_play(mk(["3", "3", "3", "4", "5"])) is None)

    # --- beats ---
    s7 = Play([Card(0, 7)], "Single", 7)
    s8 = Play([Card(0, 8)], "Single", 8)
    bomb = Play([Card(0, 8)] * 4, "Bomb", 8)
    rkt = Play([Card(4, 16), Card(4, 17)], "Rocket", 17)
    check("s8 beats s7", s8.beats(s7))
    check("bomb beats s7", bomb.beats(s7))
    check("rkt beats bomb", rkt.beats(bomb))
    check("bomb beats rkt == False", not bomb.beats(rkt))
    # straights must have equal length to be comparable
    st5 = parse_play(mk(["4", "5", "6", "7", "8"]))            # 5-long, v8
    st7_lo = parse_play(mk(["3", "4", "5", "6", "7", "8", "9"]))  # 7-long, v9
    st7_hi = parse_play(mk(["4", "5", "6", "7", "8", "9", "10"])) # 7-long, v10
    check("longer straight cannot beat shorter (45678 vs 3456789)", not st5.beats(st7_lo))
    check("shorter straight cannot beat longer (3456789 vs 45678)", not st7_lo.beats(st5))
    check("same-length higher straight beats", st7_hi.beats(st7_lo))

    # --- get_valid_plays ---
    hand = mk(["3", "3", "4", "4", "5", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "2", "2"])
    beats7 = get_valid_plays(hand, s7)
    small = min(p.value for p in beats7 if p.play_type == "Single")
    check("smallest single beating 7 is 8", small == 8)

    # --- hard AI farmer cooperation ---
    g = DouDiZhuGame()
    g.start_new_game()
    g.phase = "play"
    g.landlord = 1                      # 0 and 2 are farmers
    g.last_leader = 2                   # partner (farmer 2) holds the trick
    g.current_player = 0
    g.last_play = Play([Card(0, 7)], "Single", 7)
    g.hands[2] = [Card(0, r) for r in range(3, 13)]        # 10 cards: not sprinting
    hand0 = [Card(0, r) for r in range(9, 16)]             # singles 9..A,2
    g.hands[0] = hand0
    hard = CardAI("hard")
    p = hard.decide(g, hand0, 0)
    check("farmer covers non-sprinting partner (not blind pass)",
          p is not None and p.play_type == "Single")
    g.hands[2] = [Card(0, r) for r in range(3, 8)]         # 5 cards: sprinting
    p2 = hard.decide(g, hand0, 0)
    check("farmer lets sprinting partner run", p2 is None)

    # --- full AI self-play, no deadlock / rule errors ---
    def play_one(diff):
        g = DouDiZhuGame()
        g.start_new_game()
        ais = [CardAI(diff), CardAI(diff), CardAI(diff)]
        guard = 0
        while g.phase == "bidding":
            g.place_bid(g.current_player, ais[g.current_player].bid(g, g.current_player))
            guard += 1
            if guard > 10:
                return "bid-loop"
        while g.phase == "grab":
            p = g.current_player
            g.grab(p, ais[p].grab(g, p))
            guard += 1
            if guard > 10:
                return "grab-loop"
        if g.phase != "play":
            return "redeal"
        guard = 0
        while g.phase == "play":
            p = g.current_player
            play = ais[p].decide(g, g.hands[p], p)
            if play is None:
                ok, _ = g.pass_turn(p)
            else:
                ok, _ = g.play_cards(p, play.cards)
            if not ok:
                return "rule-error"
            guard += 1
            if guard > 400:
                return "deadlock"
        return "ok"
    random.seed(7)
    results = {}
    for diff in ("easy", "medium", "hard"):
        rc = {}
        for _ in range(40):
            k = play_one(diff)
            rc[k] = rc.get(k, 0) + 1
        results[diff] = rc
        check(f"self-play {diff} completes cleanly", rc.get("ok", 0) > 0 and
              rc.get("deadlock", 0) == 0 and rc.get("rule-error", 0) == 0)

    # --- settings roundtrip ---
    cfg = {"difficulty": "ai", "api_key": "sk-x"}
    with TemporaryDirectory() as cfg_dir, \
            patch.object(settings, "_config_dir", return_value=cfg_dir):
        defaults = settings.load_config()
        settings.save_config(cfg)
        loaded = settings.load_config()
    check("default difficulty is local hard", defaults["difficulty"] == "hard")
    check("settings roundtrip", loaded["difficulty"] == "ai")
    check("settings roundtrip key defaults", loaded["base_url"] == "http://192.168.76.43:8888/v1"
          and loaded["model"] == "deepseek-v4-flash" and loaded["api_key"] == "sk-x")

    # --- model-backed difficulty: strict validation and no local fallback ---
    g = DouDiZhuGame()
    g.start_new_game()
    g.phase = "play"
    g.current_player = 0
    offline = {"api_key": "", "base_url": "x", "model": "x"}
    try:
        ds.ai_decide_with_hard_fallback(
            g, g.hands[0], 0, offline, CardAI("hard"), timeout=2
        )
        no_fallback = False
    except ds.DeepSeekUnavailable:
        no_fallback = True
    check("AI connectivity failure still raises without fallback", no_fallback)

    class FakeResponse:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": self.content}}]}

    api_cfg = {"api_key": "sk-test", "base_url": "https://example.test/v1",
               "model": "test-model"}
    token = rank_name(g.hands[0][0].rank)
    history_card = next(card for card in make_deck() if card not in g.hands[0])
    model_history = [
        (1, Play([history_card], "Single", history_card.rank)),
        (2, None),
    ]
    valid_response = FakeResponse('{"play": ["' + token + '"], "reason": "test"}')
    with patch.object(ds.requests, "post", return_value=valid_response) as post:
        model_play = ds.ai_decide(
            g, g.hands[0], 0, api_cfg, history=model_history
        )
    messages = post.call_args.kwargs["json"]["messages"]
    snapshot = messages[1]["content"]
    check("AI valid response is accepted", model_play is not None and
          len(model_play.cards) == 1 and model_play.cards[0].rank == g.hands[0][0].rank)
    check("AI receives fixed hard-rule system prompt",
          "On a lead, play must be non-empty" in messages[0]["content"] and
          "Trust only the current game snapshot" in messages[0]["content"])
    check("AI receives complete current-game snapshot",
          all(field in snapshot for field in (
              "hand=", "public_bottom=", "cards_left=", "played=",
              "unseen_in_opponents=", "history=", "FINAL REMINDER:"
          )) and "P1:Single" in snapshot and "P2:pass" in snapshot and
          snapshot.endswith("null is forbidden on a lead."))

    wrapped_response = FakeResponse(
        '<think>先分析牌局</think>\n```json\n'
        '{"play": ["' + token + '"], "reason": "test"}\n```'
    )
    with patch.object(ds.requests, "post", return_value=wrapped_response):
        wrapped_play = ds.ai_decide(g, g.hands[0], 0, api_cfg)
    check("AI JSON in reasoning/Markdown wrapper is accepted",
          wrapped_play is not None and wrapped_play.cards[0].rank == g.hands[0][0].rank)

    text_parts_response = FakeResponse([
        {"type": "text", "text": "出牌结果："},
        {"type": "text", "text": '{"play": ["' + token + '"], "reason": "test"}'},
    ])
    with patch.object(ds.requests, "post", return_value=text_parts_response):
        parts_play = ds.ai_decide(g, g.hands[0], 0, api_cfg)
    check("AI structured text content is accepted",
          parts_play is not None and parts_play.cards[0].rank == g.hands[0][0].rank)

    plain_text_response = FakeResponse("我建议出最小的一张牌")
    with patch.object(ds.requests, "post", return_value=plain_text_response):
        try:
            ds.ai_decide(g, g.hands[0], 0, api_cfg)
            rejected_plain_text = False
        except ds.DeepSeekUnavailable:
            rejected_plain_text = True
    check("AI response without JSON is rejected", rejected_plain_text)
    with patch.object(ds.requests, "post", return_value=plain_text_response):
        fallback_outcome = ds.ai_decide_with_hard_fallback(
            g, g.hands[0], 0, api_cfg, CardAI("hard")
        )
    check("AI invalid response falls back to local hard strategy",
          fallback_outcome.model_failure is not None and
          fallback_outcome.play is not None)

    invalid_pass = FakeResponse('{"play": null, "reason": "test"}')
    with patch.object(ds.requests, "post", return_value=invalid_pass):
        try:
            ds.ai_decide(g, g.hands[0], 0, api_cfg)
            rejected_pass = False
        except ds.DeepSeekUnavailable:
            rejected_pass = True
    check("AI cannot pass while leading", rejected_pass)

    print(f"\nPASS={PASS}  FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
