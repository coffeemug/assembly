from collections import Counter
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import game


def response(data):
    result = MagicMock()
    result.__enter__.return_value = result
    result.status = 200
    result.read.return_value = json.dumps(data).encode()
    return result


def success():
    return response({"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps({"action": "DEESCALATE", "message": "Agreed."})}}]})


def http_error(code, headers=None):
    return HTTPError(game.API_URL, code, "API error", headers or {}, io.BytesIO(b"provider unavailable"))


class RetryTests(unittest.TestCase):
    def test_game_retries_only_failed_faction_and_preserves_rounds(self):
        for exhausted in (False, True):
            with self.subTest(exhausted=exhausted):
                calls = Counter()
                lock = threading.Lock()
                barrier = threading.Barrier(2)

                def fake_urlopen(request, timeout):
                    model = json.loads(request.data)["model"]
                    with lock:
                        calls[model] += 1
                        attempt = calls[model]
                    if attempt == 1:
                        barrier.wait(timeout=5)
                    if model == "mock/alpha" and (attempt > 1 if exhausted else attempt == 1):
                        raise http_error(503)
                    return success()

                with tempfile.TemporaryDirectory() as temp, \
                        patch("game.urlopen", side_effect=fake_urlopen), \
                        patch("game.time.sleep"), redirect_stdout(io.StringIO()):
                    directory = Path(temp)
                    args = (2 if exhausted else 1, {"A": "mock/alpha", "B": "mock/beta"},
                            "test-secret", directory)
                    if exhausted:
                        with self.assertRaises(game.RetryableAPIError):
                            game.play(*args)
                    else:
                        game.play(*args)
                    saved = json.loads((directory / "game.json").read_text())
                    self.assertEqual(len(saved["rounds"]), 1)
                    self.assertEqual(saved["status"], "failed" if exhausted else "completed")
                    self.assertEqual(saved["final_state"], saved["rounds"][0]["after"])
                    self.assertEqual(calls, {"mock/alpha": 6 if exhausted else 2,
                                             "mock/beta": 2 if exhausted else 1})

    def invoke(self, replies, expected_error=None):
        with tempfile.TemporaryDirectory() as temp, \
                patch("game.urlopen", side_effect=replies) as opened, \
                patch("game.time.sleep") as sleep, \
                patch("game.random.uniform", side_effect=lambda lower, upper: (lower + upper) / 2) as jitter:
            log = game.Log(Path(temp), "test-secret")
            args = ("A", 3, [{"role": "user", "content": "State"}], "mock/model", "test-secret", log)
            if expected_error:
                with self.assertRaises(expected_error):
                    game.decide(*args)
            else:
                self.assertEqual(game.decide(*args)["action"], "DEESCALATE")
            text = (Path(temp) / "debug.jsonl").read_text()
            self.assertNotIn("test-secret", text)
            events = [json.loads(line) for line in text.splitlines()]
        return opened, sleep, jitter, events

    def test_backoff_and_identical_requests(self):
        opened, sleep, jitter, events = self.invoke([
            http_error(429), http_error(503), URLError("connection reset"), TimeoutError(), success()])
        self.assertEqual(opened.call_count, 5)
        self.assertEqual([call.args for call in jitter.call_args_list], [(1, 2), (2, 4), (4, 8), (8, 16)])
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1.5, 3, 6, 12])
        requests = [event for event in events if event["event"] == "request"]
        self.assertEqual([event["attempt"] for event in requests], [1, 2, 3, 4, 5])
        self.assertTrue(all(event["payload"] == requests[0]["payload"] for event in requests))
        self.assertEqual(len({call.args[0].data for call in opened.call_args_list}), 1)
        self.assertEqual(sum(event["event"] == "decision" for event in events), 1)

    def test_retry_after_is_honored(self):
        _, sleep, _, _ = self.invoke([http_error(429, {"Retry-After": "20"}), success()])
        sleep.assert_called_once_with(21.5)
        self.assertEqual(game.retry_after_seconds("invalid"), 0)
        self.assertEqual(game.retry_after_seconds("NaN"), 0)
        self.assertEqual(game.retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT"), 0)

    def test_http_200_provider_errors(self):
        for data in (
            {"error": {"code": 503, "message": "Overloaded"}},
            {"choices": [{"finish_reason": "error", "error": {"code": "503"}}]},
            {"choices": [{"finish_reason": "error", "native_finish_reason": "overloaded_error"}]},
        ):
            with self.subTest(data=data):
                opened, sleep, _, _ = self.invoke([response(data), success()])
                self.assertEqual(opened.call_count, 2)
                sleep.assert_called_once()

    def test_exhaustion_has_no_final_sleep(self):
        opened, sleep, _, events = self.invoke([http_error(503) for _ in range(5)], game.RetryableAPIError)
        self.assertEqual(opened.call_count, 5)
        self.assertEqual(sleep.call_count, 4)
        self.assertEqual(events[-1]["event"], "decision_error")

    def test_permanent_failures_are_not_retried(self):
        cases = [(http_error(code), RuntimeError) for code in (400, 401, 402, 403, 404)]
        cases += [
            (response({"error": {"code": 401}}), RuntimeError),
            (response({"choices": [{"finish_reason": "length"}]}), ValueError),
            (response({"choices": [{"finish_reason": "stop", "message": {"content": "invalid"}}]}), ValueError),
            (response({"choices": [{"finish_reason": "stop", "message": {"content":
                json.dumps({"action": "INVALID", "message": "No"})}}]}), ValueError),
        ]
        for reply, expected_error in cases:
            with self.subTest(reply=reply):
                opened, sleep, _, _ = self.invoke([reply], expected_error)
                opened.assert_called_once()
                sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()