## What changed?

Describe the **user-visible result** and why it is useful. If this closes a
tracked issue or roadmap item, reference it here (e.g. `Closes #42`).

---

## Verification

- [ ] I ran `python -m compileall -q src tests` — no errors.
- [ ] I ran `python -m unittest discover -s tests -v` — all tests pass.
- [ ] I updated tests and documentation where behavior changed.
- [ ] I did not commit credentials, personal data, local databases, or managed source checkouts.
- [ ] I updated [ROADMAP.md](ROADMAP.md) if this closes a tracked item.

---

## Responsible-use review

- [ ] This change does not bypass access controls, collect credentials, or target private people.
- [ ] Any Tool Manager change preserves source provenance and does not auto-execute third-party code.
- [ ] Any new command or workspace produces only locally-stored, explicitly-requested results.

---

## Screenshots or terminal output (if applicable)

<!-- Paste a sanitized screenshot or terminal transcript for visual or behavioral changes. -->
