# -*- coding: utf-8 -*-
"""通用 .ndfbin 编辑器 —— 入口。

用法::

    python run_weapon_editor.py
    python run_weapon_editor.py "D:\\...\\Gen\\NDF\\GFX\\Weapon.ndfbin"

文件名沿用第一版（双击 ``启动武器编辑器.bat`` 会调它），但功能已经扩展：
现在可以打开**任意** ``.ndfbin``，不再局限于 Weapon.ndfbin。

界面是三栏数据驱动的：类列表 → 对象列表 → 字段面板，
可编辑的字段由文件自带的 PROP 表自动发现，不需要为每个文件写白名单。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    from ndfbin_decks.ndfedit.gui import main as gui_main
    try:
        return gui_main()
    except Exception:                                  # noqa: BLE001
        tb = traceback.format_exc()
        sys.stderr.write(tb)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.critical(None, "编辑器出错",
                                  f"发生了未预期的错误：\n\n{tb[-1800:]}")
        except Exception:                             # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())