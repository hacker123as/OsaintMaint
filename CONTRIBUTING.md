# Contributing to OsaintMaint

Thanks for considering a contribution. OsaintMaint is intended to make lawful
public-data research easier to review, reproduce, and verify — and community
contributions are how it improves.

---

## 🏁 Start here

1. Read the [responsible-use guidance](README.md#-responsible-use).
2. Check the [Roadmap](ROADMAP.md) and open issues — your idea may already be tracked.
3. Open an issue or discussion **before** beginning a large change, so the intended scope is clear.
4. Create a focused branch with one coherent improvement.
5. Run the local checks before opening a pull request (see below).

---

## 🟢 Good contributions

- Clear dashboard improvements that work in ordinary Command Prompt window sizes
- Better data normalization, provenance, quality checks, exports, or test fixtures
- Documentation that distinguishes confirmed facts from source-provided claims
- Accessibility and readability improvements for terminal output
- Carefully reviewed public-source catalog additions (see the Tool Manager section below)
- New issue templates, CI improvements, or project health fixes
- macOS / Linux compatibility improvements

Please keep code **dependency-light, predictable, and easy to test**. Add or
update a deterministic test whenever you change behavior.

---

## 🔧 Development setup

```powershell
# Clone and set up
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .

# Run before every pull request
python -m compileall -q src tests
python -m unittest discover -s tests -v
```

---

## 🗂 Adding a Tool Manager source

The Tool Manager is a **selector** for projects that users review and save
themselves — it is not an execution platform. A proposed source should:

- Be publicly available from a reputable, documented project page
- Have a legitimate research, verification, or public-data use case
- Preserve the original repository URL, category, and provenance
- Be safe to list without triggering scripts, package installation, or network activity beyond an explicit source checkout
- Respect law, source terms, rate limits, privacy, and the project's responsible-use boundary

**Do not propose projects** designed to steal credentials, bypass access controls,
exploit systems, access private accounts, target individuals, dox people, or
automate invasive data collection.

Not sure if a tool fits? Open a
[Tool / Feature Suggestion](.github/ISSUE_TEMPLATE/tool_suggestion.yml) issue
to discuss it before submitting a PR.

---

## ✅ Pull request checklist

- Keep the change focused and explain the **user-visible result** in the PR description
- Avoid committing local databases, generated catalog checkouts, credentials, or personal data
- Run `python -m compileall -q src tests` and `python -m unittest discover -s tests -v`
- Update the README when controls, commands, safety boundaries, or setup behavior changes
- Update [ROADMAP.md](ROADMAP.md) if you are closing a tracked item
- Include a screenshot or concise terminal transcript for **visual dashboard changes** when helpful

---

## 🤝 Community expectations

Be respectful, specific, and privacy-minded. Discuss the work, not the person.

When reporting a **security concern**, use the process in
[SECURITY.md](SECURITY.md) rather than publishing exploit details in an issue.

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for the full community expectations.
