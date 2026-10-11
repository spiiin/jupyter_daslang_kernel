"""Run notebook-style cells in a terminal before connecting Jupyter."""
import argparse
import os
from pathlib import Path
if __package__:
    from .session import Runtime, NotebookSession, CompileError, ExecutionError
else:
    from session import Runtime, NotebookSession, CompileError, ExecutionError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdk", default=os.environ.get("DAS_SDK", "C:/src/daScript"))
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    runtime = Runtime(args.sdk)
    session = NotebookSession(runtime)
    try:
        if args.demo:
            for cell in ["var x : int = 10", "x += 5", "var xs : array<int>",
                         "xs |> push(x)", "def twice() : int { return x * 2 }"]:
                print(f">>> {cell}", flush=True)
                session.execute(cell)
            print("x =", session.evaluate("x"))
            print("xs[0] =", session.evaluate("xs[0]"))
            print("twice() =", session.evaluate("twice()"))
            return
        print("daslang session. End a cell with an empty line. :eval EXPR, :reset, :quit")
        while True:
            lines = []
            try:
                first = input(">>> ")
                if first == ":quit":
                    break
                if first == ":reset":
                    session.close()
                    continue
                if first.startswith(":eval "):
                    print(session.evaluate(first[6:]))
                    continue
                if not first:
                    continue
                lines.append(first)
                while (line := input("... ")):
                    lines.append(line)
                session.execute("\n".join(lines))
            except (CompileError, ExecutionError, ValueError) as error:
                print(error)
            except EOFError:
                break
    finally:
        session.close()
        runtime.close()


if __name__ == "__main__":
    main()
