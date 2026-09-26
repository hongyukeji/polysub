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


class _WindowCase(unittest.TestCase):
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


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Window(_WindowCase):

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

    def test_task_quality_keeps_pending_settings_edit(self):
        self.w.settings.bilingual.setChecked(True)
        self.assertTrue(self.w.settings._save_timer.isActive())
        t = self.w.tasks.quality
        t.setCurrentIndex(t.findData("standard"))
        saved = config.load(self.w.cfg.path)
        self.assertTrue(saved.general.bilingual)
        self.assertEqual((saved.translate.think, saved.translate.think_budget), ("low", 1024))

    def test_watch_scan_runs_off_gui_thread_and_ignores_old_folder(self):
        self.w.cfg.general.watch_dir = self.dir
        with mock.patch("polysub.gui.app.run_async") as run, mock.patch.object(self.w.tasks, "add_paths") as add:
            self.w.scan_watch_dir()
            run.assert_called_once()
            self.w.scan_watch_dir()
            run.assert_called_once()  # do not overlap two scans of the same state file
            _, done, _ = run.call_args.args
            self.w.cfg.general.watch_dir = os.path.join(self.dir, "new")
            done(["old-folder-video.mkv"])
            add.assert_not_called()
            self.w.scan_watch_dir()
            self.assertEqual(run.call_count, 2)

    def test_old_environment_check_cannot_replace_new_results(self):
        e = self.w.environment
        with mock.patch("polysub.gui.environment.run_async") as run, \
                mock.patch("polysub.gui.environment._dir_size", side_effect=AssertionError("GUI thread")), \
                mock.patch.object(e, "_set_rows") as rows:
            e.run_checks()
            e.run_checks()
            rows.reset_mock()
            first_done = run.call_args_list[0].args[1]
            second_done = run.call_args_list[1].args[1]
            first_done(([(True, "old", "")], None, 0))
            rows.assert_not_called()
            second_done(([(True, "new", "")], None, 0))
            rows.assert_called_once_with([(True, "new", "")])

    def test_model_list_ignores_old_service_and_keeps_typed_model(self):
        from polysub.gui.settings import ModelBox
        first, second = config.Endpoint("one"), config.Endpoint("two")
        endpoint = [first]
        box = ModelBox(lambda: endpoint[0], "original")
        self.addCleanup(box.deleteLater)
        with mock.patch("polysub.gui.settings.run_async") as run:
            box.fetch()
            old_done = run.call_args.args[1]
            endpoint[0] = second
            box.endpoint_changed()
            old_done(["stale-model"])
            self.assertEqual(box.value(), "original")
            self.assertTrue(box.btn.isEnabled())
            box.fetch()
            box.combo.setEditText("typed-while-loading")
            run.call_args.args[1](["new-model"])
            self.assertEqual(box.value(), "typed-while-loading")
            self.assertEqual(box.combo.itemText(0), "new-model")

    def test_endpoints_reflow_at_narrow_width(self):
        from PySide6.QtWidgets import QBoxLayout
        self.w.resize(800, 600)
        with mock.patch("polysub.gui.app.needs_welcome", return_value=False):
            self.w.show()
        self.w.show_page("endpoints")
        self.app.processEvents()
        e = self.w.endpoints
        self.assertEqual(e._body.direction(), QBoxLayout.TopToBottom)
        self.assertGreater(e._detail_panel.y(), e._list_panel.y())
        self.assertLessEqual(e.url.mapTo(e, e.url.rect().topRight()).x(), e.width())
        self.w.resize(1060, 600)
        self.app.processEvents()
        self.assertEqual(e._body.direction(), QBoxLayout.LeftToRight)

    def test_drop_on_settings_page_adds_media_and_shows_queue(self):
        from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
        from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
        video = os.path.join(self.dir, "clip.mkv")
        with open(video, "w"):
            pass
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(video)])
        self.w.show_page("settings")
        enter = QDragEnterEvent(QPoint(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        move = QDragMoveEvent(QPoint(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        drop = QDropEvent(QPointF(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        with mock.patch.object(self.w.tasks, "add_paths") as add:
            self.w.dragEnterEvent(enter)
            self.w.dragMoveEvent(move)
            self.w.dropEvent(drop)
        self.assertTrue(enter.isAccepted())
        self.assertTrue(move.isAccepted())
        self.assertTrue(drop.isAccepted())
        add.assert_called_once_with([video])
        self.assertEqual(self.w.stack.currentIndex(), 0)

    def test_endpoint_rename_updates_all_references(self):
        cfg = self.w.cfg
        cfg.translate.fallback_endpoint = "DeepSeek"
        cfg.mine.asr_endpoint = cfg.mine.translate_endpoint = "DeepSeek"
        cfg.mine.translate_model = "deepseek-chat"
        self.w.endpoints.load()
        e = self.w.endpoints
        e.list.setCurrentRow([x.name for x in e.eps].index("DeepSeek"))
        e.name.setText("My DeepSeek")
        with mock.patch.object(self.w.environment, "run_checks"):
            e.apply()
        saved = config.load(cfg.path)
        self.assertEqual(saved.translate.fallback_endpoint, "My DeepSeek")
        self.assertEqual(saved.mine.asr_endpoint, "My DeepSeek")
        self.assertEqual(saved.mine.translate_endpoint, "My DeepSeek")
        self.assertIsNotNone(saved.find_endpoint("My DeepSeek"))

    def test_endpoint_save_keeps_pending_settings_edit(self):
        self.w.settings.bilingual.setChecked(True)
        e = self.w.endpoints
        e.add_ep("custom")
        with mock.patch.object(self.w.environment, "run_checks"):
            e.apply()
        saved = config.load(self.w.cfg.path)
        self.assertTrue(saved.general.bilingual)
        self.assertEqual(len(saved.endpoints), len(e.eps))

    def test_endpoint_delete_checks_fallback_and_my_models(self):
        cfg = self.w.cfg
        cfg.translate.fallback_endpoint = "DeepSeek"
        e = self.w.endpoints
        e.list.setCurrentRow([x.name for x in e.eps].index("DeepSeek"))
        count = e.list.count()
        with mock.patch("polysub.gui.endpoints.QMessageBox.information") as info:
            e.delete_ep()
        info.assert_called_once()
        self.assertEqual(e.list.count(), count)
        cfg.translate.fallback_endpoint = ""
        cfg.mine.translate_endpoint = "DeepSeek"
        with mock.patch("polysub.gui.endpoints.QMessageBox.information") as info:
            e.delete_ep()
        info.assert_called_once()
        self.assertEqual(e.list.count(), count)

    def test_endpoint_reload_discards_unsaved_form(self):
        e = self.w.endpoints
        original = e.eps[0].name
        e.name.setText("unsaved name")
        e.load()
        self.assertEqual(e.eps[0].name, original)

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


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class BuiltinGui(_WindowCase):
    """Welcome dialog, Models page, "my models", re-translating with another level."""

    def test_welcome_picks_tier_and_downloads(self):
        from polysub.engine import manifest
        from polysub.gui.welcome import WelcomeDialog, needs_welcome
        self.w.cfg = config.default_config("standard")
        with mock.patch.dict(os.environ, {"POLYSUB_MODELS": self.dir}):
            self.assertTrue(needs_welcome(self.w.cfg))
            d = WelcomeDialog(self.w)
            self.addCleanup(d.deleteLater)
            light = next(b for b in d.group.buttons() if b.tier == "light")
            light.setChecked(True)
            with mock.patch.object(self.w.downloads, "start") as start:
                d.start()
            start.assert_called_once_with(list(manifest.TIERS["light"]))
            self.assertEqual(self.w.cfg.translate.model, manifest.TIERS["light"][1])
            self.assertEqual(config.load(self.w.cfg.path).translate.model, manifest.TIERS["light"][1])

    def test_welcome_probes_omlx_in_background(self):
        from polysub.gui.welcome import WelcomeDialog
        with mock.patch("polysub.gui.welcome.run_async") as async_, \
                mock.patch("polysub.gui.welcome.omlx_choice", side_effect=AssertionError("blocking probe")):
            d = WelcomeDialog(self.w)
        self.addCleanup(d.deleteLater)
        async_.assert_called_once()
        d._offer_omlx(("本机 oMLX", "asr-model", "mt-model"))
        self.assertEqual(d.omlx, ("本机 oMLX", "asr-model", "mt-model"))

    def test_models_page_rows_follow_downloads(self):
        from polysub.engine import manifest
        with mock.patch.dict(os.environ, {"POLYSUB_MODELS": self.dir}):
            page = self.w.environment.builtin_models
            page.refresh()
            _, state, btn = page.rows["mt-4b"]
            self.assertEqual(btn.text(), "下载")
            open(manifest.path_of("mt-4b"), "w").write("x")
            page.refresh()
            self.assertEqual((state.text(), btn.text()), ("已下载", "删除"))

    def test_mine_needs_configuring_first(self):
        t = self.w.tasks
        with mock.patch("polysub.gui.tasks.QMessageBox.information") as info:
            t.quality.setCurrentIndex(t.quality.findData("mine"))
        info.assert_called_once()
        self.assertNotEqual(t.quality.currentData(), "mine")
        c = self.w.cfg
        c.mine.translate_endpoint, c.mine.translate_model = "DeepSeek", "deepseek-chat"
        t.quality.setCurrentIndex(t.quality.findData("mine"))
        self.assertTrue(config.load(c.path).general.use_mine)
        eff = config.load(c.path).effective()
        self.assertEqual((eff.translate.endpoint, eff.translate.model), ("DeepSeek", "deepseek-chat"))

    def test_requeue_with_other_quality(self):
        v = os.path.join(self.dir, "a.mkv"); open(v, "w").close()
        j = jobs.add([v], ["zh-Hans"])[0]
        jobs.update(j.id, status="done")
        self.w.tasks.refresh()
        self.w.tasks.table.selectRow(0)
        self.w.tasks.requeue("fine")
        j = jobs.list_jobs()[0]
        self.assertEqual((j.status, j.quality, j.overwrite), ("pending", "fine", True))
        self.assertIn("精细", self.w.tasks.table.item(0, 1).text())
