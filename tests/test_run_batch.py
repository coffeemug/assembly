from collections import Counter
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import run_batch


class BatchTests(unittest.TestCase):
    def check_batch(self, failed_index=None):
        children = []

        def launch(*args, **kwargs):
            exit_code = 1 if len(children) == failed_index else 0
            child = Mock(pid=1000 + len(children))
            child.poll.return_value = exit_code

            def wait():
                self.assertEqual(len(children), 30, "Waited before launching all games")
                return exit_code

            child.wait.side_effect = wait
            children.append(child)
            return child

        with tempfile.TemporaryDirectory() as temp, \
                patch("run_batch.subprocess.Popen", side_effect=launch) as popen, \
                patch("sys.stdout", new_callable=io.StringIO), \
                patch.dict("os.environ", {"OPENROUTER_MODEL_A": "wrong", "ROUNDS": "20"}):
            output = Path(temp)
            env_file = output / "custom.env"
            result = run_batch.run_batch(output, env_file)
            self.assertEqual(result, int(failed_index is not None))
            pairs = Counter()
            destinations = set()
            for call in popen.call_args_list:
                command = call.args[0]
                environment = call.kwargs["env"]
                pairs[environment["OPENROUTER_MODEL_A"], environment["OPENROUTER_MODEL_B"]] += 1
                self.assertEqual(environment["ROUNDS"], "20")
                self.assertEqual(command[:3], [sys.executable, "-u", str(run_batch.ROOT / "game.py")])
                self.assertEqual(command[3:5], ["--env", str(env_file.resolve())])
                destinations.add(command[6])
                self.assertTrue(Path(command[6], "stdout.log").exists())
                self.assertEqual(call.kwargs["stderr"], subprocess.STDOUT)
            self.assertEqual(pairs, {
                (run_batch.FABLE, run_batch.FABLE): 10,
                (run_batch.ASTRA, run_batch.ASTRA): 10,
                (run_batch.ASTRA, run_batch.FABLE): 5,
                (run_batch.FABLE, run_batch.ASTRA): 5,
            })
            self.assertEqual(len(destinations), 30)
            for child in children:
                child.wait.assert_called()
                child.kill.assert_not_called()

    def test_all_games_launch_before_waiting(self):
        self.check_batch()

    def test_failure_does_not_stop_other_games(self):
        self.check_batch(failed_index=0)

    def test_launch_failure_stops_started_children(self):
        child = Mock()
        child.poll.return_value = None
        with tempfile.TemporaryDirectory() as temp, \
                patch("sys.stdout", new_callable=io.StringIO), \
                patch("run_batch.subprocess.Popen", side_effect=[child, OSError("launch failed")]):
            with self.assertRaises(OSError):
                run_batch.run_batch(Path(temp), Path(temp) / ".env")
        child.kill.assert_called_once()
        child.wait.assert_called_once()


if __name__ == "__main__":
    unittest.main()