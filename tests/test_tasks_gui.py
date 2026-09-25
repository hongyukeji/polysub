"""Task page selection: select all / none, buttons, selection kept across refreshes."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QItemSelectionModel
    from PySide6.QtWidgets import QApplication
except ImportError:  # GUI extra not installed
    QApplication = None

from polysub import config, jobs


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class TaskSelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        # keep the queue away from the real user data dir
        for name, rel in (("STATE", ""), ("QUEUE", "queue.json"), ("LOCK", "queue.lock"),
                          ("WORKER_LOCK", "worker.lock"), ("PAUSE", "paused")):
            p = mock.patch.object(jobs, name, os.path.join(d.name, rel) if rel else d.name)
            p.start(); self.addCleanup(p.stop)
        self.dir = d.name
        from polysub.gui.tasks import TasksPage
        self.page = TasksPage(SimpleNamespace(cfg=config.default_config()))
        self.page.timer.stop()
        self.addCleanup(self.page.deleteLater)

    def add(self, *statuses):
        for s in statuses:
            v = os.path.join(self.dir, f"clip{len(jobs.list_jobs())}.mkv")
            open(v, "w").close()
            j = jobs.add([v], ["zh-Hans"])[0]
            jobs.update(j.id, status=s)
        self.page.refresh()

    def selected_ids(self):
        return [j.id for j in self.page.selected_jobs()]

    def sel_enabled(self):
        return [b.isEnabled() for b in self.page.sel_btns]

    def test_buttons_follow_selection(self):
        self.add("pending", "done")
        self.assertEqual(self.sel_enabled(), [False] * 3)
        self.assertTrue(self.page.select_all_act.isEnabled())
        self.assertFalse(self.page.select_none_act.isEnabled())
        self.page.select_all_act.trigger()
        self.assertEqual(len(self.selected_ids()), 2)
        self.assertEqual(self.sel_enabled(), [True] * 3)
        self.assertTrue(self.page.select_none_act.isEnabled())
        self.page.select_none_act.trigger()
        self.assertEqual(self.selected_ids(), [])
        self.assertEqual(self.sel_enabled(), [False] * 3)

    def test_empty_queue_disables_select_all(self):
        self.assertFalse(self.page.select_all_act.isEnabled())
        self.assertFalse(self.page.select_btns[0].isEnabled())

    def test_shortcuts_are_platform_standard(self):
        from PySide6.QtGui import QKeySequence
        self.assertIn(self.page.select_all_act.shortcut(), QKeySequence.keyBindings(QKeySequence.SelectAll))
        self.assertIn(self.page.select_none_act.shortcut(), QKeySequence.keyBindings(QKeySequence.Cancel))

    def test_selection_kept_on_refresh(self):
        self.add("pending", "done", "failed")
        self.page.table.selectRow(1)
        want = self.selected_ids()
        jobs.update(want[0], notes="changed")
        self.page.refresh()
        self.page.refresh()
        self.assertEqual(self.selected_ids(), want)

    def test_selection_follows_jobs_when_rows_move(self):
        self.add("done", "pending", "failed")
        ids = [j.id for j in jobs.list_jobs()]
        self.page.table.selectRow(2)
        jobs.remove([ids[0]])  # e.g. removed from the command line
        self.page.refresh()
        self.assertEqual(self.selected_ids(), [ids[2]])

    def test_remove_all_keeps_running_and_warns(self):
        self.add("running", "done", "failed")
        running = jobs.list_jobs()[0].id
        self.page.select_all_act.trigger()
        with mock.patch("polysub.gui.tasks.QMessageBox.information") as info:
            self.page.remove_sel()
        info.assert_called_once()
        self.assertEqual([j.id for j in jobs.list_jobs()], [running])
        self.assertEqual(self.selected_ids(), [running])

    def test_removed_rows_do_not_shift_selection(self):
        self.add("running", "done", "failed")
        running, _, failed = [j.id for j in jobs.list_jobs()]
        self.page.table.selectRow(0)
        flags = QItemSelectionModel.Select | QItemSelectionModel.Rows
        self.page.table.selectionModel().select(self.page.table.model().index(1, 0), flags)
        with mock.patch("polysub.gui.tasks.QMessageBox.information"):
            self.page.remove_sel()
        # row 1 now holds the failed job, which the user never selected
        self.assertEqual([j.id for j in jobs.list_jobs()], [running, failed])
        self.assertEqual(self.selected_ids(), [running])


if __name__ == "__main__":
    unittest.main()
