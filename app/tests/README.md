# Tests

Plain scripts, run directly — no pytest dependency, because the app has none
and these need to be runnable against a live-ish database with one command.

    cd app && PYTHONPATH=$PWD .venv/bin/python tests/run_all.py

They were originally written as throwaway scratch files during development and
lost when /tmp was cleared, having already caught: a scope classifier that put
Florida primaries in the Local tab, a selection rule that gave one feed the
entire daily budget, a market-brief that invented a 208% currency move, and a
CSS extraction that broke every page's stylesheet. They live in the repo now.
