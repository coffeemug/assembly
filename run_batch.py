"""Run 30 Fable/Astra games concurrently, with separate logs for every game."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
FABLE = "anthropic/claude-fable-5.1"
ASTRA = "openai/gpt-6-astra"
MATCHUPS = (
    ("fable-self", FABLE, FABLE, 10),
    ("astra-self", ASTRA, ASTRA, 10),
    ("astra-v-fable", ASTRA, FABLE, 5),
    ("fable-v-astra", FABLE, ASTRA, 5),
)


def run_batch(output, env_file):
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="fable-astra-batch-", dir=output)).resolve()
    print(f"Batch logs: {directory}", flush=True)
    processes = []
    failed = []
    try:
        for label, model_a, model_b, count in MATCHUPS:
            for number in range(1, count + 1):
                name = f"{label}-{number:02d}"
                game_directory = directory / name
                game_directory.mkdir()
                environment = os.environ.copy()
                environment.update(OPENROUTER_MODEL_A=model_a, OPENROUTER_MODEL_B=model_b)
                with (game_directory / "stdout.log").open("w") as log:
                    process = subprocess.Popen(
                        [sys.executable, "-u", str(ROOT / "game.py"),
                         "--env", str(env_file.resolve()), "--output", str(game_directory)],
                        env=environment, stdout=log, stderr=subprocess.STDOUT,
                    )
                processes.append((name, process))
                print(f"Started {name} (PID {process.pid})", flush=True)
        print(f"All {len(processes)} games launched; waiting for results.", flush=True)
        for name, process in processes:
            exit_code = process.wait()
            print(f"{name}: exit {exit_code}", flush=True)
            if exit_code:
                failed.append(name)
    finally:
        for _, process in processes:
            if process.poll() is None:
                process.kill()
        for _, process in processes:
            process.wait()
    print(f"Finished: {len(processes) - len(failed)} succeeded, {len(failed)} failed.", flush=True)
    if failed:
        print("Failed games: " + ", ".join(failed), flush=True)
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--output", type=Path, default=ROOT / "runs")
    args = parser.parse_args()
    try:
        return run_batch(args.output, args.env)
    except KeyboardInterrupt:
        print("Interrupted; remaining games stopped. Partial logs retained.", file=sys.stderr)
        return 130
    except OSError as exc:
        print(f"Batch failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())