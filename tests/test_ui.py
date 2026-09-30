import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
from unittest.mock import patch

from osaintmaint.ui import COMPACT_EYE, LARGE_EYE, STANDARD_EYE, Dashboard, _eye_for


class DashboardTests(unittest.TestCase):
    def test_dashboard_renders_identity_and_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            dashboard = Dashboard(db_path=str(Path(directory) / "dashboard.db"))
            try:
                output = StringIO()
                with redirect_stdout(output):
                    dashboard.render()
                text = output.getvalue()
                self.assertIn("O5A1NTM4INT", text)
                self.assertIn("Watchdog", text)
                self.assertIn("OSINT Tool Manager", text)
                self.assertIn("Sync OSINT Catalog", text)
                self.assertIn("My OSINT Tools", text)
                self.assertIn("Favorite OSINT Tools", [tool.label for tool in dashboard.tools()])
                self.assertIn("UNHIDE WHAT'S PUBLIC", text)
                self.assertNotIn("\x1b[38;5", text)
                self.assertGreaterEqual(len(dashboard.tools()), 10)
            finally:
                dashboard.close()

    def test_eye_and_menu_are_responsive_to_a_small_console(self):
        self.assertIs(_eye_for(120, 40), LARGE_EYE)
        self.assertIs(_eye_for(80, 24), STANDARD_EYE)
        self.assertIs(_eye_for(60, 18), COMPACT_EYE)
        with tempfile.TemporaryDirectory() as directory:
            dashboard = Dashboard(db_path=str(Path(directory) / "small.db"))
            try:
                output = StringIO()
                with patch("osaintmaint.ui.shutil.get_terminal_size", return_value=os.terminal_size((80, 22))):
                    with redirect_stdout(output):
                        dashboard.render()
                lines = output.getvalue().splitlines()
                self.assertTrue(all(len(line) <= 80 for line in lines))
                self.assertNotIn("â", output.getvalue())
            finally:
                dashboard.close()

    def test_osint_manager_exposes_sync_install_and_saved_tool_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            dashboard = Dashboard(db_path=str(Path(directory) / "manager.db"))
            try:
                output = StringIO()
                with patch.object(dashboard, "prompt", return_value=""):
                    with redirect_stdout(output):
                        dashboard.tool_manager()
                text = output.getvalue()
                self.assertIn("Sync curated catalogs", text)
                self.assertIn("Discover & install a source tool", text)
                self.assertIn("My saved tools", text)
                self.assertIn("Favorite tools", text)
                self.assertIn("Ctrl+C cancels", text)
            finally:
                dashboard.close()

    def test_two_ctrl_c_events_leave_the_manager_without_closing_the_dashboard(self):
        with tempfile.TemporaryDirectory() as directory:
            dashboard = Dashboard(db_path=str(Path(directory) / "interrupts.db"))
            try:
                with redirect_stdout(StringIO()):
                    with patch.object(dashboard, "prompt", side_effect=[KeyboardInterrupt(), KeyboardInterrupt()]):
                        with patch("osaintmaint.ui.time.monotonic", side_effect=[10.0, 11.0]):
                            dashboard.tool_manager()
                self.assertTrue(dashboard.running)
            finally:
                dashboard.close()


if __name__ == "__main__":
    unittest.main()
