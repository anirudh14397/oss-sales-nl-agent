"""
Eval runner.

Loads eval/questions.yaml, runs each question through agent.orchestrator.answer(),
and checks the actual behavior against expected_behavior (answer/clarify/refuse)
and, where applicable, expected_metric.

Exit code non-zero on failure, so this fails CI when the agent regresses.
"""

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.orchestrator import answer

QUESTIONS_PATH = Path(__file__).parent / "questions.yaml"


def main():
    questions = yaml.safe_load(QUESTIONS_PATH.read_text())
    print(f"Loaded {len(questions)} eval questions.\n")

    failures = []
    for case in questions:
        question = case["question"]
        expected_behavior = case["expected_behavior"]
        expected_metric = case.get("expected_metric")

        response = answer(question)

        ok = response.type == expected_behavior
        if ok and expected_metric:
            ok = response.metric_used == expected_metric

        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {question}")
        print(f"       expected: behavior={expected_behavior} metric={expected_metric}")
        print(f"       actual:   behavior={response.type} metric={response.metric_used}")

        if not ok:
            failures.append({"question": question, "expected_behavior": expected_behavior, "expected_metric": expected_metric, "actual_behavior": response.type, "actual_metric": response.metric_used})

    print(f"\n{len(questions) - len(failures)}/{len(questions)} passed")
    if failures:
        print(f"{len(failures)} failures:")
        for f in failures:
            print(f"  - \"{f['question']}\" — expected {f['expected_behavior']}/{f['expected_metric']}, got {f['actual_behavior']}/{f['actual_metric']}")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
