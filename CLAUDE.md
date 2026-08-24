# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 概述

Windows 单机斗地主（Python + PyQt5），一名玩家对战两名 AI。支持简单/中等/困难三档本地策略，以及独立的 AI（大模型）难度（OpenAI 兼容 `/chat/completions`）。

核心设计：**游戏引擎与 GUI 完全分离**。`src/doudizhu_deepseek/game.py` 是纯逻辑、不依赖 Qt，因此可被无头测试和 AI 复用；`src/doudizhu_deepseek/gui/` 仅负责界面与回合调度。

## 运行与测试

```bash
uv sync                           # 创建 .venv 并同步锁定依赖
uv run python main.py             # 启动游戏（需要图形环境）
uv run python smoke_test.py       # 引擎 + AI + 设置 + 模型严格校验，无 GUI，退出码非 0 即失败
```

- 无单独 lint/类型检查配置；`smoke_test.py` 是唯一的自动测试入口。
- DeepSeek 相关测试使用模拟响应，无需联网，并验证策略错误时困难策略接管、联通错误时抛错暂停。
- Windows 打包：`build_exe.bat`（PyInstaller，产物 `dist/DouDiZhu.exe`）。

## 架构

### 引擎层（无 GUI，核心逻辑）
- `game.py` — 一切游戏规则：
  - `Card`：`suit` 0..3（花色），`rank` 3..17（3=A3 … 15=2，16=小王，17=大王）；大小王 `suit=4`。
  - `parse_play(cards)` — 把一组牌归类为某种牌型（`Play`），非法返回 `None`。牌型常量如 `SINGLE/PAIR/TRIPLE1/STRAIGHT/DOUBLE_STRAIGHT/PLANE/PLANE_SINGLE/PLANE_PAIR/FOUR_2/FOUR_2PAIR/BOMB/ROCKET`；`Play.value` 是比较键（顺子/飞机取链内最大 rank，其余取主点数）。
  - `HandAnalyzer` / `get_valid_plays(hand, other)` — 从**真实手牌**枚举所有合法出法（返回的 `Play.cards` 是手牌子集，可直接移除），可选 `other` 过滤出能压过它的。
  - `DouDiZhuGame` — 三玩家控制器，玩家 0=人类、1/2=AI。状态机 `phase`：`idle → bidding → play → over`。关键字段：`hands`、`bottom`、`landlord`、`current_player`、`last_leader`、`last_play`（当前要压的牌，`None` 表示可自由开新轮）、`pass_count`。`eval_bid_strength(hand)` 提供叫分启发式。
  - **回合流转要点**：没人出牌时可 `lead` 任意牌型；`pass_count >= 2` 时由 `last_leader` 重新 `lead`。
- `ai.py` — `CardAI`，按难度 `easy/medium/hard` 区分策略；`decide()` 根据 `game.last_play is None`/可否 lead 分流到 `_decide_lead` / `_decide_follow`；hard 含队友默契（农民让牌）。

### DeepSeek 集成（严格失败）
- `deepseek_ai.py` — `ai_decide(game, hand, player_idx, cfg, history)` 是独立 AI 难度的严格入口；GUI 使用 `ai_decide_with_hard_fallback` 区分策略错误和联通错误。
  - 模型仅返回 JSON `{"play": [...]}` 或 `{"play": null}`；`_resolve_request` 把点数记号映射回**手牌中真实存在的牌**，`parse_play` 校验牌型，再校验必须压过桌面（除非可 lead）。非法、超时、断网或未配置都抛出 `DeepSeekUnavailable`，GUI 暂停当前回合并报错。
  - 每次调用是无状态快照：固定 system prompt + 当前手牌、公开底牌、各家余牌、已出/未出统计、完整出牌历史和桌面待压牌；不回传模型旧回复。
  - 修改 prompt/校验逻辑时，必须保留“严格校验；仅 `DeepSeekDecisionError` 可由困难策略接管；联通/配置错误必须抛出”的约束（`smoke_test.py` 有回归覆盖）。

### 配置
- `settings.py` — 开发环境配置持久化到项目根目录 `config.ini`，PyInstaller 版本保存到 exe 同目录；该文件含 Key，必须保持 Git ignore。`load_config()` 返回默认值合并后的 dict；字段含 `difficulty`、`api_key`、`base_url`、`model`、`player_name`。

### GUI 层（PyQt5）
- `gui/main_window.py` — `MainWindow`：布局、回合调度。AI 回合用 `QTimer`（900ms）驱动 `_ai_step`；`difficulty==ai` 时在线程中调用模型，策略错误由困难 AI 接管并计数/写历史，联通错误暂停弹窗。
- `gui/card_widget.py` — 手牌区组件与选牌/重排。
- `gui/card_counter.py` — 记牌器（`INITIAL`：普通牌每张 4 张、大小王各 1）。
- `gui/settings_dialog.py` — 设置对话框（难度 + DeepSeek 配置 + 测试连接）。

## 命名约定速查

- 牌型常量定义在 `game.py`（英文标识符），中文名在 `PLAY_TYPE_CN` 与 `rank_name()`；`RANK_CHARS` 索引 `rank-3`。
- 手牌/牌面文本渲染用 `format_cards()`（`game.py`）。
