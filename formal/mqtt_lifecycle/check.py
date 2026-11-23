#!/usr/bin/env python3
"""Model-check the AWS IoT MQTT connection lifecycle with TLC.

Runs every case in cases/ and compares the result with the `expect:` line of the case.
Run `python3 formal/mqtt_lifecycle/check.py --help` for the options.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

TLA_TOOLS_VERSION = "1.7.4"
TLA_TOOLS_URL = f"https://github.com/tlaplus/tlaplus/releases/download/v{TLA_TOOLS_VERSION}/tla2tools.jar"
TLA_TOOLS_SHA256 = "936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SPEC = HERE / "MqttLifecycle.tla"
CASES = HERE / "cases"
LOCK = HERE / "covered.json"
SOURCE = REPO / "custom_components" / "salus_it600_cloud" / "mqtt.py"
SOURCE_NAME = "mqtt.py"

REFERENCE = re.compile(rf"{re.escape(SOURCE_NAME)}:(\d+)(?:-(\d+))? (\w+)")
EXPECT = re.compile(r"^\\\* expect: (pass|violates (\w+))$", re.MULTILINE)
VIOLATED = re.compile(r"^Error: Invariant (\w+) is violated\.", re.MULTILINE)
STATE = re.compile(r"^State \d+: <(\w+)", re.MULTILINE)
ASSIGNMENT = re.compile(r"^/\\ (\w+) = ", re.MULTILINE)


class CheckError(Exception):
    """The check cannot give a verdict; the message says what to do."""


@dataclass(frozen=True)
class Function:
    first: int
    last: int
    digest: str


def source_functions() -> dict[str, Function]:
    text = SOURCE.read_text(encoding="utf-8")
    lines = text.splitlines()
    functions: dict[str, Function] = {}
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            body = "\n".join(line.rstrip() for line in lines[node.lineno - 1 : node.end_lineno])
            functions[node.name] = Function(
                node.lineno, node.end_lineno or node.lineno, hashlib.sha256(body.encode()).hexdigest()
            )
    return functions


def check_references(spec: str, functions: dict[str, Function]) -> set[str]:
    """Return the functions the model refers to, after checking that every reference still fits."""
    problems: list[str] = []
    covered: set[str] = set()
    for number, line in enumerate(spec.splitlines(), start=1):
        matches = list(REFERENCE.finditer(line))
        if line.count(f"{SOURCE_NAME}:") != len(matches):
            problems.append(f"{SPEC.name}:{number}: write references as `{SOURCE_NAME}:<first>-<last> <function>`")
        for match in matches:
            first = int(match[1])
            last = int(match[2] or match[1])
            name = match[3]
            function = functions.get(name)
            if function is None:
                problems.append(f"{SPEC.name}:{number}: {SOURCE_NAME} has no function {name}")
            elif not function.first <= first <= last <= function.last:
                problems.append(
                    f"{SPEC.name}:{number}: {match[0]} is outside {name}, "
                    f"which is now at lines {function.first}-{function.last}"
                )
            else:
                covered.add(name)
    if problems:
        raise CheckError(
            "The model points at lines that are no longer in the function it names. Read the action next to each "
            f"reference, make it describe {SOURCE_NAME} as it is now and correct the line numbers:\n      "
            + "\n      ".join(problems)
        )
    return covered


def read_lock() -> dict[str, dict[str, object]]:
    return json.loads(LOCK.read_text(encoding="utf-8")) if LOCK.exists() else {}


def moved_functions(functions: dict[str, Function]) -> dict[str, int]:
    """Return how many lines each confirmed function moved without changing."""
    return {
        name: functions[name].first - int(entry["first_line"])
        for name, entry in read_lock().items()
        if name in functions
        and functions[name].digest == entry["sha256"]
        and functions[name].first != entry["first_line"]
    }


def shift_references(spec: str, moved: dict[str, int]) -> str:
    def shifted(match: re.Match[str]) -> str:
        delta = moved.get(match[3], 0)
        last = f"-{int(match[2]) + delta}" if match[2] else ""
        return f"{SOURCE_NAME}:{int(match[1]) + delta}{last} {match[3]}"

    return REFERENCE.sub(shifted, spec)


def changed_functions(functions: dict[str, Function], covered: set[str]) -> list[str]:
    """Return the functions whose code differs from what the model was last confirmed against."""
    locked = read_lock()
    return sorted(
        name
        for name in covered | locked.keys()
        if name not in covered or name not in locked or functions[name].digest != locked[name]["sha256"]
    )


def write_lock(functions: dict[str, Function], covered: set[str]) -> None:
    lock = {name: {"first_line": functions[name].first, "sha256": functions[name].digest} for name in sorted(covered)}
    LOCK.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tla_tools() -> Path:
    override = os.environ.get("TLA2TOOLS_JAR")
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "salus-it600-cloud"
    jar = Path(override) if override else cache / f"tla2tools-{TLA_TOOLS_VERSION}.jar"
    if not jar.exists():
        if override:
            raise CheckError(f"TLA2TOOLS_JAR points at {jar}, which does not exist")
        jar.parent.mkdir(parents=True, exist_ok=True)
        partial = jar.with_suffix(".partial")
        print(f"Downloading {TLA_TOOLS_URL}", flush=True)
        with urllib.request.urlopen(TLA_TOOLS_URL, timeout=120) as response, partial.open("wb") as target:
            shutil.copyfileobj(response, target)
        if sha256(partial) != TLA_TOOLS_SHA256:
            partial.unlink()
            raise CheckError(f"{TLA_TOOLS_URL} does not have the pinned SHA-256 {TLA_TOOLS_SHA256}")
        partial.replace(jar)
    if sha256(jar) != TLA_TOOLS_SHA256:
        raise CheckError(f"{jar} does not have the pinned SHA-256 {TLA_TOOLS_SHA256}; delete it and rerun")
    return jar


@dataclass(frozen=True)
class Result:
    violated: str | None
    steps: list[tuple[str, dict[str, tuple[str, str]]]]
    states: str


def parse_trace(output: str) -> list[tuple[str, dict[str, tuple[str, str]]]]:
    """Return the action of every step of the counterexample with the variables it changed."""
    headers = list(STATE.finditer(output))
    steps: list[tuple[str, dict[str, tuple[str, str]]]] = []
    previous: dict[str, str] = {}
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(output)
        block = output[header.end() : end].split("\n\n", 1)[0]
        names = list(ASSIGNMENT.finditer(block))
        values = {
            name[1]: " ".join(block[name.end() : (names[i + 1].start() if i + 1 < len(names) else len(block))].split())
            for i, name in enumerate(names)
        }
        if index:
            changed = {
                name: (previous[name], value)
                for name, value in values.items()
                if previous.get(name) != value and name != "queue"
            }
            steps.append((header[1], changed))
        previous = values
    return steps


def run_tlc(jar: Path, config: Path, workers: str) -> Result:
    java = shutil.which("java")
    if java is None:
        raise CheckError("TLC needs Java 11 or newer on PATH")
    with tempfile.TemporaryDirectory(prefix="mqtt-lifecycle-") as scratch:
        command = [java, "-XX:+UseParallelGC", "-cp", str(jar), "tlc2.TLC", "-deadlock", "-workers", workers]
        command += ["-metadir", scratch, "-config", str(config), str(SPEC)]
        output = subprocess.run(command, cwd=scratch, capture_output=True, text=True, check=False).stdout
    violated = VIOLATED.search(output)
    if violated is None and "Model checking completed. No error has been found." not in output:
        raise CheckError(f"TLC gave no verdict for {config.name}:\n{output}")
    states = re.findall(r"(\d+) distinct states found", output)
    return Result(violated[1] if violated else None, parse_trace(output), states[-1] if states else "?")


def print_trace(result: Result) -> None:
    for number, (action, changed) in enumerate(result.steps, start=1):
        changes = ", ".join(f"{name}: {old} -> {new}" for name, (old, new) in changed.items())
        print(f"      {number:2}. {action}  [{changes}]")


def run_case(jar: Path, config: Path, workers: str, show_trace: bool) -> bool:
    expectation = EXPECT.search(config.read_text(encoding="utf-8"))
    if expectation is None:
        raise CheckError(f"{config.name} needs a line `\\* expect: pass` or `\\* expect: violates <Invariant>`")
    expected = expectation[2]
    # TLC reports the shortest path to a break only with a single worker.
    result = run_tlc(jar, config, "1" if expected else workers)
    name = config.stem
    if result.violated == expected:
        outcome = f"rejected, {expected} breaks after {len(result.steps)} steps" if expected else "holds"
        print(f"ok    {name}: {outcome} ({result.states} states)", flush=True)
        if expected and show_trace:
            print_trace(result)
        return True
    if result.violated is None:
        print(f"FAIL  {name}: expected {expected} to break, and every invariant holds ({result.states} states).")
        if name.startswith("finding-"):
            print(
                f"      The finding no longer reproduces. If {SOURCE_NAME} was fixed, add {expected} to the\n"
                "      INVARIANTS of cases/current.cfg and remove the xfail marker from its test in tests/test_mqtt.py.\n"
                f"      Then add a switch to {SPEC.name} whose FALSE is the old behaviour and rename this case to\n"
                "      bug-<commit>-..., so that the invariant stays proven able to fail."
            )
        else:
            print("      The model lost the ability to catch this broken variant; a check that cannot fail is void.")
    else:
        print(f"FAIL  {name}: {result.violated} breaks, expected {expected or 'every invariant to hold'}.")
        print("      Shortest path to the break, one step of the code per line:")
        print_trace(result)
        print("      .claude/skills/mqtt-lifecycle-model/SKILL.md tells a bug in mqtt.py from an error in the model.")
    sys.stdout.flush()
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cases", nargs="*", help="names of cases in cases/ without .cfg (default: all)")
    parser.add_argument("--trace", action="store_true", help="print the counterexample of rejected variants too")
    parser.add_argument("--workers", default="auto", help="TLC worker threads for cases that must hold (default: auto)")
    parser.add_argument(
        "--update-lock",
        action="store_true",
        help=f"record that the model describes the covered functions of {SOURCE_NAME} as they are now, "
        "after shifting the line references of functions that only moved",
    )
    arguments = parser.parse_args()
    try:
        functions = source_functions()
        moved = moved_functions(functions)
        spec = shift_references(SPEC.read_text(encoding="utf-8"), moved)
        covered = check_references(spec, functions)
        if arguments.update_lock:
            SPEC.write_text(spec, encoding="utf-8")
            write_lock(functions, covered)
            print(f"Recorded {len(covered)} covered functions in {LOCK.relative_to(REPO)}")
            return 0
        configs = [CASES / f"{name}.cfg" for name in arguments.cases] or sorted(CASES.glob("*.cfg"))
        jar = tla_tools()
        results = [run_case(jar, config, arguments.workers, arguments.trace) for config in configs]
    except CheckError as err:
        print(f"FAIL  {err}")
        return 1
    if moved:
        print(
            f"FAIL  These functions of {SOURCE_NAME} moved without changing: {', '.join(sorted(moved))}.\n"
            f"      Do not edit their line references in {SPEC.name} by hand. Once nothing else fails, run\n"
            "        python3 formal/mqtt_lifecycle/check.py --update-lock"
        )
    unconfirmed = changed_functions(functions, covered)
    if unconfirmed:
        print(
            f"FAIL  These functions of {SOURCE_NAME} changed since the model was last confirmed against them: "
            f"{', '.join(unconfirmed)}.\n"
            f"      The results above are for the model, which may no longer describe the code. Follow\n"
            f"      .claude/skills/mqtt-lifecycle-model/SKILL.md: make the actions in {SPEC.name} that refer to these\n"
            "      functions describe the new code, rerun, and only then record the new state with\n"
            "        python3 formal/mqtt_lifecycle/check.py --update-lock"
        )
    return 0 if all(results) and not moved and not unconfirmed else 1


if __name__ == "__main__":
    sys.exit(main())
