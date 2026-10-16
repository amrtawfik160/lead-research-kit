# Agent guide

- For lead research operations, follow `skills/lead-research/SKILL.md`
- Keep network work dry by default and use synthetic offline fixtures for tests
- Preserve exact budget accounting, source labels, holds and failure history when changing behavior
- Keep account data and contact workspaces outside this source tree; run the release checker before packaging
- Use `PYTHONPATH=src python3 -m unittest discover -s tests -v` after changes
