"""Lets you run `python -m oracle_lab`. The `oracle-lab` command skips this."""

from oracle_lab.cli import main

raise SystemExit(main())
