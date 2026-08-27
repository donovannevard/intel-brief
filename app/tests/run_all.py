"""Run every test script in this directory, reporting pass/fail per file."""
import pathlib, subprocess, sys

here = pathlib.Path(__file__).parent
failures = []
for path in sorted(here.glob("test_*.py")):
    result = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)
    last = [l for l in result.stdout.strip().split("\n") if l.strip()]
    status = "PASS" if result.returncode == 0 else "FAIL"
    if result.returncode != 0:
        failures.append(path.name)
    print(f"{status}  {path.name:24s} {last[-1] if last else ''}")
    if result.returncode != 0:
        print("      " + (result.stderr.strip().split("\n")[-1] if result.stderr.strip() else ""))

print()
print("all passed" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
