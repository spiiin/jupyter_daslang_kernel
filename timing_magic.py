"""Timing and runtime-instrumented profiling for notebook fragments."""
import json
from pathlib import Path
import re
import statistics
import time

HELP = {
    "%time": "%time CODE\nExecute once, commit state and show preparation/runtime wall times.",
    "%%time": "%%time\nCODE\nExecute a cell once and show preparation/runtime wall times.",
    "%timeit": "%timeit [-r REPEATS] [-n NUMBER] CODE\nBenchmark isolated copies of the original checkpoint. Defaults: -r 5 -n 1. External side effects still happen.",
    "%%timeit": "%%timeit [-r REPEATS] [-n NUMBER]\nCODE\nBenchmark isolated copies of the original checkpoint; do not commit notebook state.",
    "%%profile": "%%profile [--top N] [--trace PATH]\nCODE\nExecute once and commit state; show function time/allocation statistics and optionally write a Chrome/Perfetto trace.",
}


def parse_timing(code):
    header, _, body = code.partition("\n")
    words = header.strip().split(None, 1)
    if not words or words[0] not in HELP:
        return None
    command = words[0]
    rest = words[1] if len(words) > 1 else ""
    options = {"repeats": 5, "number": 1, "top": 20, "trace": None}
    allowed = {"-r": "repeats", "-n": "number"} if "timeit" in command else (
        {"--top": "top", "--trace": "trace"} if command == "%%profile" else {})
    while allowed and re.match(r"^--?[A-Za-z]", rest):
        match = re.match(r'''(\S+)\s+("[^"]*"|'[^']*'|\S+)(?:\s+|$)''', rest)
        if not match or match[1] not in allowed:
            raise ValueError(HELP[command])
        key, value = allowed[match[1]], match[2]
        if key == "trace":
            value = value[1:-1] if value[:1] in ("\"", "'") else value
            if not value:
                raise ValueError("Trace path cannot be empty")
        else:
            try:
                value = int(value)
            except ValueError:
                raise ValueError(HELP[command]) from None
            if not 1 <= value <= 1000:
                raise ValueError("Timing counts must be between 1 and 1000")
        options[key] = value
        rest = rest[match.end():].lstrip()
    if command.startswith("%%"):
        if rest or not body.strip():
            raise ValueError(HELP[command])
        fragment = "\n" + body  # Keep body diagnostics on the original cell rows.
    else:
        if not rest.strip() or body.strip():
            raise ValueError(HELP[command])
        fragment = rest
    if options["repeats"] * options["number"] > 1000:
        raise ValueError("At most 1000 benchmark executions are allowed")
    return command, options, fragment


PHASES = (("compile_ns", "Compile"), ("simulate_ns", "Simulate/relocate"),
          ("restore_ns", "Validate + restore state"), ("initialize_ns", "Module initialization"),
          ("execute_ns", "Execute"), ("total_ns", "Total session execution"))


def timing_report(timings):
    return "\n".join(f"{label}: {timings[key]/1e6:.3f} ms" for key, label in PHASES if key in timings)


def execute_timing(session, request, cell_label=None):
    command, options, code = request
    if "timeit" in command:
        checkpoint = session.snapshot()
        samples, setup_samples, rounds = [], [], []
        # Keep the original session and its context untouched, even on errors.
        for _ in range(options["repeats"]):
            group = []
            for _ in range(options["number"]):
                trial = type(session)(session.runtime)
                try:
                    started = time.perf_counter_ns()
                    trial.restore(checkpoint)
                    setup_samples.append(time.perf_counter_ns()-started)
                    trial.execute(code, cell_label=cell_label)
                    samples.append(dict(trial.last_timings))
                    group.append(trial.last_timings["execute_ns"])
                finally:
                    trial.close()
            rounds.append(statistics.mean(group))
        means = {key: statistics.mean(sample[key] for sample in samples) for key, _ in PHASES}
        summary = (f"{options['repeats']} repeats x {options['number']} isolated executions\n"
                   f"Runtime: mean {statistics.mean(rounds)/1e6:.3f} ms; "
                   f"stddev {statistics.pstdev(rounds)/1e6:.3f} ms; "
                   f"best repeat {min(rounds)/1e6:.3f} ms\n"
                   f"Checkpoint setup (excluded): {statistics.mean(setup_samples)/1e6:.3f} ms/execution\n"
                   + timing_report(means)
                   + "\nNotebook state unchanged; file/network/module lifecycle effects are not rolled back.")
        return summary, {"timings": means, "samples": samples, "setup_ns": setup_samples}

    profiling = command == "%%profile"
    if profiling and not all(hasattr(session.runtime.bridge, name) for name in ("nb_profile_start", "nb_profile_stop")):
        raise RuntimeError("Rebuild the native adapter to enable %%profile")
    session.profile_requested = profiling
    try:
        result = session.execute(code, cell_label=cell_label)
    finally:
        session.profile_requested = False
    report = timing_report(session.last_timings)
    metadata = {"timings": session.last_timings}
    if profiling:
        profile = session.last_profile
        metadata["profile"] = profile
        rows = sorted(profile["functions"], key=lambda row: row["self_ns"], reverse=True)
        report += "\n\nFunctions (instrumented wall time; inclusive includes callees):\n"
        report += "calls  inclusive ms  self ms  allocated B  own B  function\n"
        for row in rows[:options["top"]]:
            report += (f"{row['calls']:5d}  {row['inclusive_ns']/1e6:12.3f}  {row['self_ns']/1e6:7.3f}  "
                       f"{row['allocated_bytes']:11d}  {row['self_allocated_bytes']:5d}  {row['name']}\n")
        report += "Allocation bytes are requested heap/string bytes, including reallocation sizes; not live memory or peak usage.\n"
        report += "Instrumentation adds overhead. Preparation, restoration and checkpoint serialization are not profiled."
        if profile["truncated"]:
            report += "\nTrace truncated at 100000 completed calls; function totals remain complete."
        if profile["incomplete_frames"]:
            report += "\nWarning: unmatched instrumented calls; totals may be incomplete."
        if options["trace"]:
            path = Path(options["trace"]).expanduser().resolve()
            path.write_text(json.dumps({"traceEvents": profile["traceEvents"]}), encoding="utf-8")
            report += "\nChrome/Perfetto trace: " + str(path)
    return ((str(result)+"\n\n") if result is not None else "") + report, metadata
