Generate unit tests for the following area of this repository:

${test_target}

Test this code directly, or through the public code that uses it. Do not modify
the application code.

You are working in a disposable container. The current working directory is the
repository root. Keep repository exploration within this directory; do not inspect
parent or system directories. The generated-test directory already exists and may
be empty. Work only from the visible repository snapshot; prior Git history is
intentionally unavailable.

Place new tests and fixtures under `${generated_dir}/`. Do not edit existing files
outside that directory.

The project Python is `${project_python}`. Run the generated tests with:

```sh
${project_python} -m pytest -o addopts= ${generated_dir}
```
