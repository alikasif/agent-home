# Project structure

When you propose a layout (in a design, a plan, or while creating code), group source files into subpackages by responsibility. Show the directory tree before listing any task or file, and regroup it if it fails the checks below.

- No directory holds more than about 5 modules or mixes responsibilities.
- Name directories for what they do (`client`, `sandbox`, `billing`), never `utils`, `helpers`, `common` or `misc`.
- Use a src layout. Entry points (`cli.py`, `main.py`) stay at the package top level and hold no logic.
- Tests mirror the source tree.
- Every package has `__init__.py`; use absolute imports.
- State the allowed dependency direction between packages, with no cycles. Only the entry point may import everything.
- Give each variant (server, provider, adapter) its own folder under a registry package, so adding one changes no core code.
- A tiny project (a few modules, one responsibility) may stay flat.
