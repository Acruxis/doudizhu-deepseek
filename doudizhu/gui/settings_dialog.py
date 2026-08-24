"""Settings dialog: difficulty and optional DeepSeek API configuration."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
                             QLineEdit, QComboBox, QLabel, QPushButton,
                             QGroupBox, QCheckBox, QMessageBox)

from .. import settings


class SettingsDialog(QDialog):
    """Modal dialog to edit and persist settings."""

    def __init__(self, parent=None, cfg=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setModal(True)
        self.config = cfg or settings.load_config()
        self._build()

    def _build(self):
        root = QVBoxLayout(self)

        # Difficulty
        diff_box = QGroupBox("AI 难度")
        diff_lay = QVBoxLayout(diff_box)
        self.diff_combo = QComboBox()
        for key, label in [("easy", "简单"), ("medium", "中等"), ("hard", "困难")]:
            self.diff_combo.addItem(label, key)
        idx = self.diff_combo.findData(self.config.get("difficulty", "medium"))
        self.diff_combo.setCurrentIndex(max(0, idx))
        diff_lay.addWidget(self.diff_combo)
        root.addWidget(diff_box)

        # LLM API (OpenAI-compatible, drives the hard AI)
        ds_box = QGroupBox("AI 大模型（可选，困难级 AI）")
        ds_lay = QVBoxLayout(ds_box)
        self.enabled = QCheckBox("启用大模型驱动困难级 AI")
        self.enabled.setChecked(str(self.config.get("ai_enabled", "false")).lower() == "true")
        ds_lay.addWidget(self.enabled)

        form = QFormLayout()
        self.key_edit = QLineEdit(self.config.get("api_key", "apikey"))
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("apikey")
        self.base_edit = QLineEdit(self.config.get("base_url",
                                                   "http://192.168.76.43:8888/v1"))
        self.model_edit = QLineEdit(self.config.get("model", "deepseek-v4-flash"))
        form.addRow("API Key：", self.key_edit)
        form.addRow("Base URL：", self.base_edit)
        form.addRow("模型：", self.model_edit)
        ds_lay.addLayout(form)
        tip = QLabel("OpenAI 兼容接口；离线、无 Key 或返回非法牌时自动回退到内置困难 AI。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#777; font-size:11px;")
        ds_lay.addWidget(tip)
        root.addWidget(ds_box)

        buttons = QHBoxLayout()
        self.test_btn = QPushButton("测试连接")
        self.ok_btn = QPushButton("确定")
        self.cancel_btn = QPushButton("取消")
        self.test_btn.clicked.connect(self._test)
        self.ok_btn.clicked.connect(self._save_and_close)
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.test_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.ok_btn)
        buttons.addWidget(self.cancel_btn)
        root.addLayout(buttons)

    def _test(self):
        from ..deepseek_ai import ask_deepseek, DeepSeekUnavailable
        from ..game import make_deck
        tmp_cfg = {
            "ai_enabled": "true",
            "api_key": self.key_edit.text().strip(),
            "base_url": self.base_edit.text().strip(),
            "model": self.model_edit.text().strip(),
        }
        if not tmp_cfg["api_key"]:
            QMessageBox.warning(self, "测试连接", "请先填写 API Key。")
            return
        self.test_btn.setEnabled(False)
        self.test_btn.setText("连接中…")
        try:
            # minimal probe: just validate reachability/view via a tiny call
            probe = {
                "ai_enabled": "true", "api_key": tmp_cfg["api_key"],
                "base_url": tmp_cfg["base_url"], "model": tmp_cfg["model"]}
            from ..deepseek_ai import _probe
            _probe(probe, timeout=10)
            QMessageBox.information(self, "测试连接", "连接成功")
        except DeepSeekUnavailable as e:
            QMessageBox.warning(self, "测试连接", f"连接失败：{e}")
        except Exception as e:  # pragma: no cover
            QMessageBox.warning(self, "测试连接", f"连接失败：{e}")
        finally:
            self.test_btn.setEnabled(True)
            self.test_btn.setText("测试连接")

    def _save_and_close(self):
        self.config["difficulty"] = self.diff_combo.currentData()
        self.config["ai_enabled"] = "true" if self.enabled.isChecked() else "false"
        self.config["api_key"] = self.key_edit.text().strip()
        self.config["base_url"] = self.base_edit.text().strip()
        self.config["model"] = self.model_edit.text().strip()
        settings.save_config(self.config)
        self.accept()
