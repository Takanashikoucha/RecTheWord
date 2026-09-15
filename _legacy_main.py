#!/usr/bin/env python3
"""RecTheWord entry point.

Run with::

    python main.py
"""

from __future__ import annotations

import logging
import sys


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> int:
    _setup_logging()
    from PySide6.QtWidgets import QApplication

    from rectheword.config import AppConfig
    from rectheword.ui.main_window import create_main_window

    config = AppConfig.load()
    app = QApplication(sys.argv)
    window = create_main_window(config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
