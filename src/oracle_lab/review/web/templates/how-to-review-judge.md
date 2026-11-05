## How to review a generation judgment

You rate the judge's answers about one agent's generated tests. You do not answer
the questions yourself.

1. Review the problem. On the left, the **Problem** tab has the issue, the golden
   patch (the fix), and the reference tests.
2. Review the run. In the middle:
   - **Overview**: each generated test's result on the buggy and fixed code.
   - **Agent trace**: every step the agent took. The **Legend** tab on the left
     explains the colors and tags, and the **Event** tab shows the selected step
     in full.
   - **Test files**: each version of the tests the agent wrote.
   - **Files** and **Evidence**: the code and the files the judge read.
3. Review the rubric. On the left, the **Stage rubric** tab defines every
   question, answer, and failure mode.
4. Rate each answer. On the right, read the judge's answer and its
   justification, then choose:

${ratings}

   Each disagree needs a short reason: what the right answer is, and why. Rate
   each answer on its own. If an earlier answer is wrong, rate the later ones as
   if it were right.
5. Rate each failure mode. The judge decided whether each one is present in the
   agent's trace. Rate every decision, absent ones too. Present ones cite trace
   steps; their links open the step. Each disagree needs a reason here too.

### Keys

- `1` and `2` agree or disagree with the selected item.
- `j` and `k` move between items.
- On the Agent trace tab, `↑` and `↓` move between events, and `Esc` clears the
  selection.
- `Ctrl` `P` finds a file.
