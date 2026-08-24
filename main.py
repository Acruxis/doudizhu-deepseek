"""斗地主 (Dou Di Zhu) — programme entry point (Windows desktop game)."""

import sys

from PyQt5.QtWidgets import QApplication

from doudizhu import settings


def main():
    settings.save_defaults(force=False)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("斗地主")
    app.setOrganizationName("doudizhu-deepseek")

    from doudizhu.gui.main_window import MainWindow
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
