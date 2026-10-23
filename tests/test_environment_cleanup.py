from __future__ import annotations

import subprocess
import sys
import unittest

from lstest.scripts.environment_cleanup import KillOnCloseJob


@unittest.skipUnless(sys.platform == "win32", "Windows Job Object only")
class KillOnCloseJobTests(unittest.TestCase):
    def test_close_kills_assigned_process(self):
        job = KillOnCloseJob()
        if not job.create():
            self.skipTest("unable to create job object")
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            if not job.assign(process):
                self.skipTest("unable to assign process to job object")
            job.close()
            process.wait(timeout=10)
            self.assertIsNotNone(process.poll())
        finally:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=5)
            except Exception:
                pass
            job.close()


if __name__ == "__main__":
    unittest.main()
