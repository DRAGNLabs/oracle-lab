"""A standalone viewer for one run's agent trace, for judging traces before open coding.

Not a pipeline stage: it reads saved runs and writes nothing. It annotates facts
only (where the agent was, what it saw, what happened) and never labels an event
as a failure mode. See `docs/features/trace-viewer/spec.md`.

`build` turns a run directory into a `RunTrace`; `server` serves it to the page in
`static/`. `shell` and `facts` do the classification and annotation in between.
"""
