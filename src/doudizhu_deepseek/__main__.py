"""Application entry point for the Doudizhu desktop game."""

import sys

from PyQt5.QtWidgets import QApplication

from doudizhu_deepseek import settings


def main() -> None:
    settings.save_defaults(force=False)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("斗地主")
    app.setOrganizationName("doudizhu-deepseek")

    from doudizhu_deepseek.gui.main_window import MainWindow

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
