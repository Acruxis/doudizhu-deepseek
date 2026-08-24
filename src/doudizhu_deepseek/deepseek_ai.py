"""DeepSeek API client for the standalone model-backed AI difficulty.

The model is asked, in natural language, what to play for the current turn. Its
intended action is validated strictly against the real game rules BEFORE being
executed. Malformed or illegal decisions can be handed to the local hard
strategy with an explicit failure record; connectivity failures still raise.

The request/response uses the OpenAI-compatible /chat/completions endpoint.
"""
import json
from collections import Counter
from dataclasses import dataclass

import requests

from .game import parse_play, rank_name


_SYSTEM_PROMPT = (
    "You are a Dou Dizhu move engine. Choose the best legal move for your side; "
    "farmers should cooperate. Trust only the current game snapshot. Reply with "
    'exactly one JSON object: {"play":["3"]} or {"play":null}. Use only cards '
    "in hand with exact multiplicities. Valid ranks: 3,4,5,6,7,8,9,10,J,Q,K,A,2,"
    "小王,大王. On a lead, play must be non-empty and legal. When following, play "
    "must legally beat the table or be null. Never add explanations or Markdown."
)

_DECK_COUNTS = {rank: (1 if rank >= 16 else 4) for rank in range(3, 18)}


class DeepSeekUnavailable(Exception):
    """Raised when the DeepSeek integration cannot be used at all."""


class DeepSeekDecisionError(DeepSeekUnavailable):
    """Raised when the service replied but its decision cannot be executed."""


@dataclass(frozen=True)
class AIDecisionOutcome:
    """A model play, optionally replaced by the local hard strategy."""

    play: object
    model_failure: str | None = None


def _rank_token(rank):
    return rank_name(rank)


def _describe(cards):
    counts = Counter(c.rank for c in cards)
    return ",".join(
        f"{_rank_token(rank)}x{count}" if count > 1 else _rank_token(rank)
        for rank, count in sorted(counts.items())
    )


def _describe_counts(counts):
    return ",".join(
        f"{_rank_token(rank)}x{count}" if count > 1 else _rank_token(rank)
        for rank, count in sorted(counts.items()) if count > 0
    ) or "none"


def _describe_history(history):
    if not history:
        return "none"
    items = []
    for turn, (idx, play) in enumerate(history, 1):
        if play is None:
            items.append(f"{turn}:P{idx}:pass")
            continue
        items.append(
            f"{turn}:P{idx}:{play.play_type}[{_describe(play.cards)}]"
        )
    return "|".join(items)


def _build_prompt(game, hand, player_idx, history=None):
    """Compose a complete, engine-sourced snapshot of the current turn."""
    history = list(history or [])
    if game.is_landlord(player_idx):
        role = f"P{player_idx}=landlord"
    else:
        teammate = next(
            (i for i in range(3) if i not in (player_idx, game.landlord)), "?"
        )
        role = (
            f"P{player_idx}=farmer,landlord=P{game.landlord},"
            f"teammate=P{teammate}"
        )
    remaining = ",".join(
        f"P{i}:{game.hand_size(i)}" for i in range(3) if i != player_idx
    )
    played_counts = Counter(
        card.rank
        for _, play in history if play is not None
        for card in play.cards
    )
    own_counts = Counter(card.rank for card in hand)
    unseen_counts = {
        rank: max(0, total - played_counts[rank] - own_counts[rank])
        for rank, total in _DECK_COUNTS.items()
    }
    if game.can_lead() or game.last_play is None:
        table = "lead"
        reminder = (
            "FINAL REMINDER: Return JSON only. play must be a non-empty legal "
            "subset of hand; null is forbidden on a lead."
        )
    else:
        table = (
            f"beat={game.last_play.play_type}"
            f"[{_describe(game.last_play.cards)}]"
        )
        reminder = (
            "FINAL REMINDER: Return JSON only. play must be an exact legal "
            "subset of hand that beats table, or null to pass."
        )
    return (
        f"STATE: self={role}; hand={_describe(hand)}; public_bottom={_describe(game.bottom)}; "
        f"cards_left={remaining}; table={table}; played={_describe_counts(played_counts)}; "
        f"unseen_in_opponents={_describe_counts(unseen_counts)}; "
        f"history={_describe_history(history)}. Choose the best move for your side. "
        f"{reminder}"
    )


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
    m = {rank_name(3 + i): 3 + i for i in range(13)}
    m["小王"] = 16
    m["大王"] = 17
    return m


def _parse_model_json(content):
    """Extract the decision object from common chat-response wrappers.

    Some OpenAI-compatible models ignore ``response_format`` and wrap their
    JSON in Markdown, explanatory text, or ``<think>`` output. We tolerate
    those wrappers, but still require a JSON object containing ``play``.
    """
    if isinstance(content, dict):
        obj = content
        if "play" in obj:
            return obj
        raise DeepSeekDecisionError("模型返回的 JSON 缺少 play 字段")

    if isinstance(content, list):
        chunks = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if isinstance(text, str):
                    chunks.append(text)
        content = "".join(chunks)

    if not isinstance(content, str) or not content.strip():
        raise DeepSeekDecisionError("模型返回内容为空")

    text = content.strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        obj = None
    if isinstance(obj, dict) and "play" in obj:
        return obj
    if obj is not None:
        raise DeepSeekDecisionError("模型返回的 JSON 必须是包含 play 字段的对象")

    # Find an embedded JSON object without relying on greedy regular
    # expressions, which break when strings or nested objects contain braces.
    decoder = json.JSONDecoder()
    for pos, char in enumerate(text):
        if char != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(text[pos:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "play" in candidate:
            return candidate

    preview = " ".join(text.split())[:160]
    raise DeepSeekDecisionError(f"模型响应中未找到出牌 JSON：{preview}")


def ask_deepseek(game, hand, player_idx, cfg, history=None, timeout=15):
    """Ask DeepSeek which cards to play. Returns a valid Play or None (pass),
    or raises DeepSeekUnavailable when the model cannot make a valid decision.
    """
    api_key = (cfg.get("api_key") or "").strip()
    if not api_key:
        raise DeepSeekUnavailable("未配置 API Key")

    base = cfg.get("base_url", "http://192.168.76.43:8888/v1").rstrip("/")
    model = cfg.get("model", "deepseek-v4-flash")
    url = f"{base}/chat/completions"

    prompt = _build_prompt(game, hand, player_idx, history)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 128,
        "thinking": {"type": "disabled"},
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

    obj = _parse_model_json(content)

    play_tok = obj["play"]
    if play_tok is None:
        if game.can_lead():
            raise DeepSeekDecisionError("模型选择不出，但新一轮必须出牌")
        return None

    if not isinstance(play_tok, list) or not play_tok:
        raise DeepSeekDecisionError("模型返回的 play 格式不正确")

    chosen = _resolve_request([str(t) for t in play_tok], hand)
    if chosen is None:
        raise DeepSeekDecisionError("模型选了手中没有的牌")

    play = parse_play(chosen)
    if play is None:
        raise DeepSeekDecisionError("模型给的牌型非法")

    # must actually beat the current play (or be a legal lead)
    if not (game.can_lead() or game.last_play is None):
        if not play.beats(game.last_play):
            raise DeepSeekDecisionError("模型的牌压不过桌面上的牌")

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
                  "max_tokens": 2, "thinking": {"type": "disabled"}},
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=timeout)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except requests.RequestException as e:
        raise DeepSeekUnavailable(f"请求失败: {e}") from e
    except (KeyError, IndexError, ValueError) as e:
        raise DeepSeekUnavailable(f"响应解析失败: {e}") from e


def ai_decide(game, hand, player_idx, cfg, history=None, timeout=15):
    """Return the model decision without any local-strategy fallback."""
    return ask_deepseek(
        game, hand, player_idx, cfg, history=history, timeout=timeout
    )


def ai_decide_with_hard_fallback(
        game, hand, player_idx, cfg, hard_ai, history=None, timeout=15):
    """Use local hard AI only when the model response is an invalid decision.

    Connectivity and configuration failures intentionally continue to raise.
    """
    try:
        play = ai_decide(
            game, hand, player_idx, cfg, history=history, timeout=timeout
        )
        return AIDecisionOutcome(play)
    except DeepSeekDecisionError as exc:
        fallback = hard_ai.decide(game, hand, player_idx)
        return AIDecisionOutcome(fallback, str(exc))
