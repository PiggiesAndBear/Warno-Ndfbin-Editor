import sys, shutil, tempfile
sys.path.insert(0, '.')
sys.path.insert(0, 'tests')
from pathlib import Path
import _env
from PySide6.QtWidgets import QApplication
from ndfbin_decks.ndfedit.gui import MainWindow, _pick_cjk_font

SRC = _env.gfx() / 'Unit.ndfbin'
tmp = Path(tempfile.mkdtemp(prefix='shotcls_'))
dst = tmp / 'Unit.ndfbin'
shutil.copy2(SRC, dst)
app = QApplication([])
_pick_cjk_font(app)
win = MainWindow()
win.resize(1400, 820)
win.load_path(dst)
win.show()
for _ in range(8):
    app.processEvents()
win.grab().save('build_0.1/class-search-all.png')
print(f'全部：{win.cls.visible_count} / {win.cls.count} 类')

win.class_search.setText('Descriptor')
for _ in range(6):
    app.processEvents()
win.grab().save('build_0.1/class-search-filtered.png')
print(f'过滤 Descriptor：{win.cls.visible_count} / {win.cls.count} 类，'
      f'当前类 {win._current_class}')

win.class_search.setText('Weapon')
for _ in range(6):
    app.processEvents()
win.grab().save('build_0.1/class-search-weapon.png')
print(f'过滤 Weapon：{win.cls.visible_count} 类；状态栏 {win.status.text()[:70]}')
win.close()
win.deleteLater()
app.processEvents()
shutil.rmtree(tmp, ignore_errors=True)
