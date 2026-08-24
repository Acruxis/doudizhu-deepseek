"""Optional DeepSeek API client to drive the Hard AI.

The model is asked, in natural language, what to play for the current turn. Its
intended action is validated strictly against the real game rules BEFORE being
executed; any illegal, malformed, timed-out or network-failed result falls back
to the strong heuristic AI so the game can never hang or cheat.

The request/response uses the OpenAI-compatible /chat/completions endpoint.
"""
import json
import time

import requests

from .game import parse_play, get_valid_plays, rank_name


class DeepSeekUnavailable(Exception):
    """Raised when the DeepSeek integration cannot be used at all."""


def _rank_token(rank):
    return rank_name(rank)


def _describe(cards):
    return " ".join(_rank_token(c.rank) for c in sorted(cards, key=lambda c: c.rank))


def _build_prompt(game, hand, player_idx, cfg):
    """Compose a concise, unambiguous prompt describing the current turn."""
    role = "地主" if game.is_landlord(player_idx) else "农民 (队友是另一个农民)"
    lines = [
        "你是斗地主游戏中的一名 AI 玩家。请根据当前局面决定这一步出什么牌。",
        f"你的身份：{role}。",
        f"你当前的手牌（空格分隔，每组相同点数的牌可以组合）：{_describe(hand)}",
        f"对手/队友剩余牌数：玩家1 {game.hand_size(1)} 张，玩家2 {game.hand_size(2)} 张。",
    ]
    if game.can_lead() or game.last_play is None:
        lines.append("现在是新一轮轮到你主动出牌，你可以出任意合法牌型，或(极少见)不出。")
    else:
        lines.append(f"桌面上需要你压过的牌是：{game.last_play.play_type} 价值 {game.last_play.value}"
                     f"（实际牌面 value，点数越大越强；炸弹/王炸可压任意牌）。")
    lines.append(
        "请只输出一个 JSON 对象，不要任何其他文字。格式如下：\n"
        '  {"play": ["3","3","4"], "reason": "简短中文说明"}   # 出这些牌\n'
        '  {"play": null, "reason": "简短中文说明"}            # 不出\n'
        "牌面用以下记号：3,4,5,6,7,8,9,10,J,Q,K,A,2,小王,大王。\n"
        "注意：你给出的牌必须包含在你手牌里，并且必须能压过桌面上的牌（如果是你要出牌则可以任意合法牌型）。"
        "不要使用这些记号以外的写法。"
    )
    return "\n".join(lines)


def _resolve_request(play_tokens, hand):
    """Map rank tokens the model chose onto concrete cards still in hand.

    Returns a list of Card objects, or None if impossible.
    """
    tok_map = _token_map()
    counts = {}
    for t in play_tokens:
        r = tok_map.get(str(t))
        if r is None:
            return None
        counts[r] = counts.get(r, 0) + 1
    by_rank = {}
    for c in hand:
        by_rank.setdefault(c.rank, []).append(c)
    chosen = []
    for r, n in counts.items():
        avail = by_rank.get(r, [])
        if len(avail) < n:
            return None
        chosen.extend(avail[:n])
    return chosen


def _token_map():
    names = ["3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A", "2"]
    m = {rank_name(3 + i): 3 + i for i in range(13)}
    m["小王"] = 16
    m["大王"] = 17
    return m


def ask_deepseek(game, hand, player_idx, cfg, timeout=15):
    """Ask DeepSeek which cards to play. Returns a valid Play or None (pass),
    or raises DeepSeekUnavailable / OSError on fatal errors (caller falls back).
    """
    api_key = (cfg.get("api_key") or "").strip()
    if not api_key or str(cfg.get("ai_enabled", "false")).lower() != "true":
        raise DeepSeekUnavailable("AI 未启用或未配置 API Key")

    base = cfg.get("base_url", "http://192.168.76.43:8888/v1").rstrip("/")
    model = cfg.get("model", "deepseek-v4-flash")
    url = f"{base}/chat/completions"

    prompt = _build_prompt(game, hand, player_idx, cfg)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是斗地主专家的出牌助手，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 120,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except requests.RequestException as e:
        raise DeepSeekUnavailable(f"请求失败: {e}") from e
    except (KeyError, IndexError, ValueError) as e:
        raise DeepSeekUnavailable(f"响应解析失败: {e}") from e

    try:
        obj = json.loads(content)
    except json.JSONDecodeError:
        raise DeepSeekUnavailable("模型返回非 JSON")

    play_tok = obj.get("play")
    if play_tok is None:
        return None  # model says pass

    if not isinstance(play_tok, list) or not play_tok:
        raise DeepSeekUnavailable("模型返回的 play 格式不正确")

    chosen = _resolve_request([str(t) for t in play_tok], hand)
    if chosen is None:
        raise DeepSeekUnavailable("模型选了手中没有的牌")

    play = parse_play(chosen)
    if play is None:
        raise DeepSeekUnavailable("模型给的牌型非法")

    # must actually beat the current play (or be a legal lead)
    if not (game.can_lead() or game.last_play is None):
        if not play.beats(game.last_play):
            raise DeepSeekUnavailable("模型的牌压不过桌面上的牌")

    return play


def _probe(cfg, timeout=10):
    """Validate reachability + credentials with a minimal chat request.

    Raises DeepSeekUnavailable on any failure; returns the model's reply text.
    """
    api_key = (cfg.get("api_key") or "").strip()
    if not api_key:
        raise DeepSeekUnavailable("未配置 API Key")
    base = cfg.get("base_url", "http://192.168.76.43:8888/v1").rstrip("/")
    model = cfg.get("model", "deepseek-v4-flash")
    try:
        resp = requests.post(
            f"{base}/chat/completions",
            json={"model": model, "messages": [{"role": "user", "content": "ping"}],
                  "max_tokens": 5},
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=timeout)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except requests.RequestException as e:
        raise DeepSeekUnavailable(f"请求失败: {e}") from e
    except (KeyError, IndexError, ValueError) as e:
        raise DeepSeekUnavailable(f"响应解析失败: {e}") from e


def hard_decide(game, hand, player_idx, cfg, heuristic_ai, timeout=15):
    """Full Hard-AI decision: try DeepSeek, fall back to the heuristic AI.

    Returns a Play object or None (pass) — never raises.
    """
    fallback = heuristic_ai.decide(game, hand, player_idx)
    if str(cfg.get("deepseek_enabled", "false")).lower() == "true":
        try:
            play = ask_deepseek(game, hand, player_idx, cfg, timeout=timeout)
            if play is not None:
                return play
            # Model explicitly passed; only allow pass when it's legal.
            if not (game.can_lead() and game.last_leader == player_idx) or game.last_play is not None:
                return None
            return fallback
        except DeepSeekUnavailable:
            return fallback
        except Exception:
            return fallback
    return fallback
