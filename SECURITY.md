# Security Policy

## Supported versions

Security fixes are applied to the latest released `0.1.x` version and the
current default branch.

| Version | Supported |
| --- | --- |
| 0.1.x (latest) | ✅ |
| Earlier versions | ❌ |

---

## 🔒 Reporting a vulnerability

> [!CAUTION]
> Please do **not** publish sensitive vulnerability details in a public issue.

When this repository is published on GitHub, use the repository's
**"Report a vulnerability"** / private security advisory feature if it is enabled.

If private reporting is unavailable, open a **minimal** public issue that asks a
maintainer for a secure contact method — do not include vulnerability details in
that issue.

### What to include in a private report

A useful private report includes:

- A clear description of the issue and the affected version or commit
- Reproduction steps or a minimal proof of concept
- The realistic impact and any conditions needed to trigger it
- Suggested mitigation, if you have one

Please allow maintainers time to **investigate and release a fix** before any
public disclosure.

---

## 🔭 Scope notes

OsaintMaint is designed to keep research local and to avoid automatic third-party
execution. Security reports are especially valuable for:

- Unsafe handling of paths, source URLs, archives, or managed checkouts
- Missing validation around local database, configuration, or export inputs
- Secrets or private data accidentally written to logs, exports, or diagnostics
- Dashboard or launcher behavior that could unexpectedly execute code
- Injection risks in search, watch rules, or audit output

Reports about third-party projects merely **listed** in the Tool Manager should
normally go to the maintainers of those projects. If OsaintMaint itself handles a
listed source unsafely, include that behavior in your report.
