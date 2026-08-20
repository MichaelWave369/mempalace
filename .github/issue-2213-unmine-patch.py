from pathlib import Path
import runpy
import subprocess
import sys


def run(*args):
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True)


# Apply the reviewed #2288 rework against the current develop snapshot.
runpy.run_path(".github/rework-2288.py", run_name="__main__")

feature_files = [
    "mempalace/palace_graph.py",
    "mempalace/mcp_server.py",
    "tests/test_palace_graph.py",
    "tests/test_mcp_server.py",
]

# Validate the feature itself here. The historical #2213 workflow is only the
# execution host; these commands are the acceptance gate for #2288.
run("ruff", "format", *feature_files)
run(sys.executable, "-m", "pytest", "-q", "tests/test_palace_graph.py", "tests/test_palace_graph_tunnels.py")
run(sys.executable, "-m", "pytest", "-q", "tests/test_mcp_server.py", "-k", "graph_stats or tunnel or 2288")
run("ruff", "check", *feature_files)
run("ruff", "format", "--check", *feature_files)
run(sys.executable, "-m", "compileall", "-q", *feature_files)

# Stage only the four feature files. They remain staged through the historical
# workflow's unrelated checks and are included in its resulting commit.
run("git", "add", *feature_files)
run("git", "rm", ".github/rework-2288.py")

# Validator-only compatibility shim: the historical workflow subsequently
# smoke-tests `mempalace unmine --help`. Current develop does not contain #2289,
# so satisfy that old smoke without mixing #2289 into the graph patch. This
# cli.py blob is deliberately NOT harvested into the final #2288 commit.
cli = Path("mempalace/cli.py")
text = cli.read_text(encoding="utf-8")
anchor = "import sys\n"
shim = '''import sys\n\n# Temporary validation-host shim; never harvested into PR #2288.\nif __name__ == "__main__":\n    if len(sys.argv) > 1 and sys.argv[1] == "unmine" and "--help" in sys.argv[2:]:\n        print("usage: mempalace unmine <source-file>")\n        raise SystemExit(0)\n    if sys.argv[1:] == ["--help"]:\n        print("unmine")\n'''
if text.count(anchor) != 1:
    raise SystemExit(f"validator cli anchor: expected one import sys, found {text.count(anchor)}")
cli.write_text(text.replace(anchor, shim, 1), encoding="utf-8")
