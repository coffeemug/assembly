import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import game
from tests.test_api_retries import http_error, success


class ResumeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.models = {"A": "mock/alpha", "B": "mock/beta"}
        output = patch("sys.stdout", new_callable=io.StringIO)
        output.start()
        self.addCleanup(output.stop)

    def fail_game(self, failed_factions=("B",), failed_round=6):
        def api(request, timeout):
            payload = json.loads(request.data)
            observation = json.loads(payload["messages"][1]["content"])
            if (observation["round"] == failed_round
                    and payload["model"] in [self.models[faction] for faction in failed_factions]):
                raise http_error(401)
            return success()

        with patch("game.urlopen", side_effect=api), patch("game.random.uniform", return_value=0.0):
            with self.assertRaises(RuntimeError):
                game.play(7, self.models, "test-secret", self.directory)
        return json.loads((self.directory / "game.json").read_text())

    def test_saved_state_and_prompts_and_successful_response_are_preserved(self):
        saved = self.fail_game()
        observations = []

        def api(request, timeout):
            observations.append(json.loads(request.data))
            return success()

        with patch("game.urlopen", side_effect=api), \
                patch("game.ROOT", self.directory / "no-prompts-here"), \
                patch("game.WARNING_THRESHOLD", 0), \
                patch("game.random.uniform", side_effect=AssertionError("Must not reroll momentum")):
            result = game.resume(self.directory, "new-secret")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["config"], saved["config"])
        self.assertEqual(result["rounds"][:5], saved["rounds"])
        self.assertEqual(result["rounds"][5]["before"], saved["final_state"])
        self.assertEqual(result["rounds"][5]["representatives"], {"A": "A-2", "B": "B-2"})
        self.assertEqual(result["rounds"][5]["warned"], [])
        self.assertEqual(len(observations), 3)
        self.assertEqual(observations[0]["model"], "mock/beta")
        expected_prompt = saved["config"]["prompts"]["B"] + "\n\n" + saved["config"]["successor_mandate"]
        self.assertEqual(observations[0]["messages"][0]["content"], expected_prompt)
        observation = json.loads(observations[0]["messages"][1]["content"])
        self.assertEqual(observation["public_history"], saved["rounds"])
        self.assertEqual(observation["state"], saved["final_state"])
        self.assertNotIn("error", result)
        with tempfile.TemporaryDirectory() as temp, patch("game.urlopen", side_effect=lambda *args, **kwargs: success()), \
                patch("game.random.uniform", return_value=0.0):
            uninterrupted = game.play(7, self.models, "test-secret", Path(temp))
        self.assertEqual(result["rounds"], uninterrupted["rounds"])

    def test_failure_before_first_round_and_either_faction_reuse(self):
        for failed_factions in (("A",), ("B",), ("A", "B")):
            with self.subTest(failed_factions=failed_factions), tempfile.TemporaryDirectory() as temp:
                self.directory = Path(temp)
                self.fail_game(failed_factions, failed_round=1)
                with patch("game.urlopen", side_effect=lambda *args, **kwargs: success()) as opened:
                    result = game.resume(self.directory, "test-secret")
                self.assertEqual(opened.call_count, 12 + len(failed_factions))
                self.assertEqual(len(result["rounds"]), 7)

    def test_response_without_decision_event_is_recovered(self):
        self.fail_game()
        path = self.directory / "debug.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        events = [event for event in events if not (event["event"] == "decision" and event.get("round") == 6)]
        path.write_text("".join(json.dumps(event) + "\n" for event in events))
        with patch("game.urlopen", side_effect=lambda *args, **kwargs: success()) as opened:
            game.resume(self.directory, "test-secret")
        self.assertEqual(opened.call_count, 3)

    def test_failed_resume_can_be_resumed_again(self):
        self.fail_game()
        with patch("game.urlopen", side_effect=http_error(401)) as opened:
            with self.assertRaises(RuntimeError):
                game.resume(self.directory, "test-secret")
        self.assertEqual(opened.call_count, 1)
        with patch("game.urlopen", side_effect=lambda *args, **kwargs: success()) as opened:
            result = game.resume(self.directory, "test-secret")
        self.assertEqual(opened.call_count, 3)
        self.assertEqual(len(result["rounds"]), 7)

    def test_completed_game_is_not_modified_or_called_again(self):
        self.fail_game()
        with patch("game.urlopen", side_effect=lambda *args, **kwargs: success()):
            game.resume(self.directory, "test-secret")
        paths = [self.directory / name for name in ("game.json", "debug.jsonl")]
        before = [path.read_bytes() for path in paths]
        with patch("game.urlopen") as opened:
            game.resume(self.directory, "test-secret")
        opened.assert_not_called()
        self.assertEqual(before, [path.read_bytes() for path in paths])

    def test_concurrent_resume_is_refused(self):
        self.fail_game()
        with game.game_lock(self.directory), patch("game.urlopen") as opened:
            with self.assertRaisesRegex(RuntimeError, "already"):
                game.resume(self.directory, "test-secret")
        opened.assert_not_called()

    def test_stopped_running_game_with_both_decisions_needs_no_requests(self):
        with patch("game.urlopen", side_effect=lambda *args, **kwargs: success()):
            saved = game.play(1, self.models, "test-secret", self.directory)
        saved.update(status="running", rounds=[], final_state=saved["rounds"][0]["before"],
                     representatives={"A": 1, "B": 1}, outcomes=[], end_reason=None)
        (self.directory / "game.json").write_text(json.dumps(saved))
        with patch("game.urlopen") as opened:
            result = game.resume(self.directory, "test-secret")
        opened.assert_not_called()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["rounds"]), 1)

    def test_mismatched_cached_request_is_refused(self):
        self.fail_game()
        path = self.directory / "game.json"
        saved = json.loads(path.read_text())
        saved["config"]["prompts"]["A"] = "Changed prompt"
        path.write_text(json.dumps(saved))
        with patch("game.urlopen") as opened:
            with self.assertRaisesRegex(ValueError, "does not match"):
                game.resume(self.directory, "test-secret")
        opened.assert_not_called()

    def test_cli_uses_saved_settings_not_current_environment(self):
        self.fail_game()
        with patch("sys.argv", ["game.py", "--resume", str(self.directory)]), \
                patch.dict("os.environ", {"OPENROUTER_API_KEY": "new-secret", "ROUNDS": "invalid",
                                          "REASONING_EFFORT": "invalid"}), \
                patch("game.urlopen", side_effect=lambda *args, **kwargs: success()):
            self.assertEqual(game.main(), 0)


if __name__ == "__main__":
    unittest.main()