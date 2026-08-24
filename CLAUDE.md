# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 概述

Windows 单机斗地主（Python + PyQt5），一名玩家对战两名 AI。支持 简单/中等/困难 三级 AI，「困难」级可选接入 DeepSeek 大模型（OpenAI 兼容 `/chat/completions`）。

核心设计：**游戏引擎与 GUI 完全分离**。`doudizhu/game.py` 是纯逻辑、不依赖 Qt，因此可被无头测试和 AI 复用；`doudizhu/gui/` 仅负责界面与回合调度。

## 运行与测试

```bash
pip install -r requirements.txt   # PyQt5>=5.15, requests>=2.25
python main.py                    # 启动游戏（需要图形环境）
python smoke_test.py              # 引擎 + AI + 设置 + DeepSeek 回退，无 GUI，退出码非 0 即失败
```

- 无单独 lint/类型检查配置；`smoke_test.py` 是唯一的自动测试入口。
- DeepSeek 相关测试在离线下运行（`smoke_test.py` 通过 `timeout=2` 触发回退路径），无需连网。
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

### DeepSeek 集成（安全回退）
- `deepseek_ai.py` — `hard_decide(game, hand, player_idx, cfg, heuristic_ai)` 是唯一入口：**模型输出从不被信任**。
  - 模型仅返回 JSON `{"play": [...], "reason": "..."}`；`_resolve_request` 把点数记号映射回**手牌中真实存在的牌**，`parse_play` 校验牌型，再校验必须压过桌面（除非可 lead）。任何非法/超时/断网/未配置都 `raise DeepSeekUnavailable`，`hard_decide` 捕获后退回内置 heuristic AI——**游戏绝不停摆或作弊**。
  - 修改 prompt/校验逻辑时，必须保留这条「先严格校验、失败必回退」的约束（`smoke_test.py` 有回归覆盖）。

### 配置
- `settings.py` — 配置持久化到用户级 `~/.doudizhu/config.ini`（不用仓库内文件，便于 PyInstaller 单文件 exe）。`load_config()` 返回默认值合并后的 dict；`save_config()` 覆盖写入。字段含 `difficulty`、`ai_enabled`、`api_key`、`base_url`、`model`、`player_name`（见 `config.example.ini`）；LLM 部分是 OpenAI 兼容接口，默认指向本地 `http://192.168.76.43:8888/v1`、模型 `deepseek-v4-flash`。

### GUI 层（PyQt5）
- `gui/main_window.py` — `MainWindow`：布局、回合调度。AI 回合用 `QTimer`（900ms）驱动 `_ai_step`，人类回合停表等输入；难度通过 `_set_diff_and_ais` 决定每名 AI 的 `CardAI`；当 `difficulty==hard && deepseek_enabled` 时走 `hard_decide`。
- `gui/card_widget.py` — 手牌区组件与选牌/重排。
- `gui/card_counter.py` — 记牌器（`INITIAL`：普通牌每张 4 张、大小王各 1）。
- `gui/settings_dialog.py` — 设置对话框（难度 + DeepSeek 配置 + 测试连接）。

## 命名约定速查

- 牌型常量定义在 `game.py`（英文标识符），中文名在 `PLAY_TYPE_CN` 与 `rank_name()`；`RANK_CHARS` 索引 `rank-3`。
- 手牌/牌面文本渲染用 `format_cards()`（`game.py`）。
