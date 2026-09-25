"""Main window: sidebar pages, menu actions, settings saved on change, editor."""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # GUI extra not installed
    QApplication = None

from polysub import config, jobs


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Window(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = d.name
        patches = [mock.patch.dict(os.environ, {"POLYSUB_CONFIG": os.path.join(d.name, "c.toml")}),
                   mock.patch.object(jobs, "start_background", lambda *a, **k: None)]
        for name, rel in (("STATE", ""), ("QUEUE", "queue.json"), ("LOCK", "queue.lock"),
                          ("WORKER_LOCK", "worker.lock"), ("PAUSE", "paused")):
            patches.append(mock.patch.object(jobs, name, os.path.join(d.name, rel) if rel else d.name))
        for p in patches:
            p.start(); self.addCleanup(p.stop)
        from polysub.gui.app import MainWindow
        self.w = MainWindow()
        self.w.tasks.timer.stop()
        self.addCleanup(self.w.deleteLater)

    def test_sidebar_switches_pages(self):
        for i in range(self.w.nav.count()):
            self.w.nav.setCurrentRow(i)
            self.assertEqual(self.w.stack.currentIndex(), i)
        self.w.show_page("settings")
        self.assertEqual(self.w.stack.currentWidget().widget(), self.w.settings)

    def test_settings_saved_on_change(self):
        s = self.w.settings
        s.second.setCurrentIndex(s.second.findData("all"))
        s.quality.setCurrentIndex(s.quality.findData("standard"))
        s.apply()  # what the debounce timer calls
        c = config.load(self.w.cfg.path)
        self.assertEqual((c.asr.two_pass, c.asr.second_pass), (True, "all"))
        self.assertEqual((c.translate.think, c.translate.think_budget), ("low", 1024))
        self.assertEqual(self.w.tasks.quality.currentData(), "standard")  # tasks page follows
        self.assertTrue(s._save_timer.isSingleShot())

    def test_change_starts_debounced_save(self):
        s = self.w.settings
        self.assertFalse(s._save_timer.isActive())
        s.bilingual.setChecked(not s.bilingual.isChecked())
        self.assertTrue(s._save_timer.isActive())

    def test_queue_actions_follow_state(self):
        t = self.w.tasks
        self.assertFalse(t.pause_act.isEnabled())       # empty queue
        self.assertFalse(any(b.isVisibleTo(t) for b in t.sel_btns))
        v = os.path.join(self.dir, "a.mkv"); open(v, "w").close()
        jobs.add([v], ["zh-Hans"])
        t.refresh()
        self.assertTrue(t.pause_act.isEnabled())
        self.assertTrue(all(b.isVisibleTo(t) for b in t.sel_btns))
        self.assertFalse(t.remove_act.isEnabled())
        t.select_all_act.trigger()
        self.assertTrue(t.remove_act.isEnabled())
        from polysub.gui.tasks import PERCENT
        self.assertEqual(t.table.item(0, 3).data(PERCENT), 0)  # drawn by the progress delegate

    def test_endpoints_add_and_delete(self):
        e = self.w.endpoints
        n = e.list.count()
        e.add_ep("ollama")
        self.assertEqual(e.list.count(), n + 1)
        e.delete_ep()
        self.assertEqual(e.list.count(), n)

    def test_editor_opens(self):
        from types import SimpleNamespace
        from polysub import subtitle
        from polysub.asr import Cue
        from polysub.gui.editor import SubtitleEditor
        srt = os.path.join(self.dir, "a.zh-Hans.srt")
        subtitle.write([Cue(1.0, 2.0, "你好")], srt, "srt")
        job = SimpleNamespace(outputs={"zh-Hans": srt}, video=os.path.join(self.dir, "a.mkv"), cache=self.dir)
        ed = SubtitleEditor(self.w, job, "zh-Hans")
        self.addCleanup(ed.deleteLater)
        self.assertEqual(ed.table.rowCount(), 1)
        self.assertEqual(ed.table.item(0, 3).text(), "你好")


if __name__ == "__main__":
    unittest.main()
