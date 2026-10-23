"""A run's agent trace as typed, annotated events, for the review page's trace tab.

It reads saved runs and writes nothing. It annotates facts only (where the agent
was, what it saw, what happened) and never labels an event as a failure mode.

`build` turns a run directory into a `RunTrace`. `shell` and `facts` do the
classification and annotation in between.
"""
