"""DeepSeek API client for the standalone model-backed AI difficulty.

The model is asked, in natural language, what to play for the current turn. Its
intended action is validated strictly against the real game rules BEFORE being
executed. Illegal, malformed, timed-out or network-failed results raise a clear
error; the model-backed difficulty never falls back to a local strategy.

The request/response uses the OpenAI-compatible /chat/completions endpoint.
"""
import json

import requests

from .game import parse_play, rank_name


class DeepSeekUnavailable(Exception):
    """Raised when the DeepSeek integration cannot be used at all."""


def _rank_token(rank):
    return rank_name(rank)


def _describe(cards):
    return " ".join(_rank_token(c.rank) for c in sorted(cards, key=lambda c: c.rank))


def _build_prompt(game, hand, player_idx, cfg):
    """Compose a concise, unambiguous prompt describing the current turn."""
    role = "地主" if game.is_landlord(player_idx) else "农民 (队友是另一个农民)"
    others = "、".join(
        f"玩家{i} {game.hand_size(i)} 张" for i in range(3) if i != player_idx
    )
    lines = [
        "你是斗地主游戏中的一名 AI 玩家。请根据当前局面决定这一步出什么牌。",
        f"你的身份：{role}。",
        f"你当前的手牌（空格分隔，每组相同点数的牌可以组合）：{_describe(hand)}",
        f"其他玩家剩余牌数：{others}。",
    ]
    if game.can_lead() or game.last_play is None:
        lines.append("现在是新一轮轮到你主动出牌，必须选择一种合法牌型，不能不出。")
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
    or raises DeepSeekUnavailable when the model cannot make a valid decision.
    """
    api_key = (cfg.get("api_key") or "").strip()
    if not api_key:
        raise DeepSeekUnavailable("未配置 API Key")

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
    except (json.JSONDecodeError, TypeError):
        raise DeepSeekUnavailable("模型返回非 JSON")
    if not isinstance(obj, dict):
        raise DeepSeekUnavailable("模型返回的 JSON 必须是对象")

    play_tok = obj.get("play")
    if play_tok is None:
        if game.can_lead():
            raise DeepSeekUnavailable("模型选择不出，但新一轮必须出牌")
        return None

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


def ai_decide(game, hand, player_idx, cfg, timeout=15):
    """Return the model decision without any local-strategy fallback."""
    return ask_deepseek(game, hand, player_idx, cfg, timeout=timeout)
