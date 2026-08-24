# 斗地主 · 三级难度（Python + PyQt5）

一个面向 **Windows** 的单机斗地主人机对战游戏，三名玩家（你 + 两名 AI），
内置 **简单 / 中等 / 困难** 三级 AI 难度；「困难」级可选接入 **DeepSeek 大模型** 出牌。

## 功能

- 🎴 完整斗地主规则：单张、对子、三张、三带一/二、顺子、连对、飞机（带单/带对）、
  四带二、炸弹、王炸
- 🏠 叫地主（0/1/2/3 分）、地主拿三张底牌
- 🤖 三级 AI 难度
  - **简单**：近随机出牌
  - **中等**：偏好大组合清牌、保留大牌
  - **困难**：启发式评分 + 队友默契；**可选接入 DeepSeek** 提升智能
- 🖥️ 绿色牌桌风格 GUI（PyQt5），点击选牌、提示、不出
- 📦 一键打包为 Windows 单文件 `.exe`

## 运行（开发环境）

```bash
pip install -r requirements.txt
python main.py
```

## Windows 打包

双击 `build_exe.bat`（需已装 Python 与 pip），产物为 `dist/DouDiZhu.exe`：
一个**自包含**的单文件可执行程序，无需安装、无需联网（除 DeepSeek 可选功能外）。

## 使用说明

- **叫地主**：轮到你可以叫 1/2/3 分或不叫；AI 会评估手牌叫牌。
- **出牌**：用鼠标点击选择要出的牌（再次点击取消），点「出牌」；压不过可点「不出」。
- **提示**：自动为你选中建议出牌。
- **设置**：可选三档难度；可填写 DeepSeek API Key 并开启「困难级 AI 调用 DeepSeek」。

## AI 大模型配置（可选，困难级 AI）

在「设置」对话框中：

1. 勾选「启用大模型驱动困难级 AI」
2. 填入 API Key、Base URL、模型名（默认 `http://192.168.76.43:8888/v1` / apikey 占位 / `deepseek-v4-flash`）
3. 点「测试连接」验证后保存

配置会持久化到用户目录 `~/.doudizhu/config.ini`（也可参照 `config.example.ini`）。
启用后「困难」级 AI 会调用该 OpenAI 兼容接口出牌；**断网、无 Key 或模型给出非法牌时，
自动回退到内置困难 AI**，游戏绝不会因此中断或出错。

## 项目结构

```
doudizhu-deepseek/
├── main.py                  # 程序入口
├── requirements.txt
├── build_exe.bat            # Windows 打包脚本
├── config.example.ini       # 配置示例
└── doudizhu/
    ├── game.py              # 牌/牌型识别/叫地主/出牌/胜负 核心逻辑
    ├── ai.py                # 简单/中等/困难 启发式 AI
    ├── deepseek_ai.py       # DeepSeek API 客户端（含校验与回退）
    ├── settings.py          # 配置读写（user-level ini）
    └── gui/
        ├── main_window.py   # 主窗口与回合调度
        ├── card_widget.py   # 卡牌组件与手牌区
        └── settings_dialog.py
```

## 技术栈

- Python 3.8+
- PyQt5 >= 5.15
- requests（仅 DeepSeek 可选功能需要）
- PyInstaller（Windows 打包）
