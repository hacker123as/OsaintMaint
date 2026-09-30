# OsaintMaint Roadmap

This file tracks planned improvements, open ideas, and community suggestions for OsaintMaint.
It is a living document — items move, get added, and sometimes get dropped.

> [!NOTE]
> Want to influence what gets built next? Open a
> [Tool / Feature Suggestion](.github/ISSUE_TEMPLATE/tool_suggestion.yml) issue.
> Items tagged `suggestion` in the issue tracker are the best place to vote and discuss.

---

## ✅ Already shipped (v0.1.x)

- Interactive keyboard dashboard with filter, scroll, and context help
- OSINT Tool Manager with catalog sync from two upstream indexes (~549 tools)
- Favorites system — pin tools to the top of every list, persist across removals
- Public-data workspace with normalized local SQLite schema
- Watchdog runner with bounded requests, rate limits, and run history
- Watch rules with query, category, severity, and geo-bounding-box filters
- Quality scan surfacing missing fields and uncertain severity
- Audit trail for key local operations
- Site workspace for trusted public HTTP(S) source bookmarks
- JSON, CSV, and self-contained HTML export
- One-click Windows launcher (`osaint.bat`) with virtual-environment bootstrap
- MIT license, contribution guide, security policy, code of conduct, CI workflow

---

## 🔜 Near-term (v0.2.x)

- [ ] **Expanded catalog sources** — additional curated OSINT index feeds beyond the current two
- [ ] **GeoJSON export** — export event data as GeoJSON for use in mapping tools
- [ ] **Markdown report format** — export a formatted Markdown summary alongside HTML
- [ ] **Watch rule notifications** — optional local desktop notification when a rule fires
- [ ] **Improved quality scoring** — weighted provenance score visible on each record
- [ ] **Config validation command** — `osaint config check` to validate connector JSON before use
- [ ] **macOS / Linux launcher script** — shell equivalent of `osaint.bat`

---

## 🧭 Medium-term ideas

- [ ] **Court and property record workspace** — normalized schema for publicly-available civic data
- [ ] **PDF export** — self-contained portable report with embedded styles
- [ ] **Catalog category browser** — browse tools grouped by category, not just text search
- [ ] **Diff watchdog** — detect new records since the last watchdog run and surface only deltas
- [ ] **Plugin/connector documentation template** — make it easier to add a new public-data source shape
- [ ] **Integration with local LLMs** — optional summarization of search results using a local model

---

## 💡 Community suggestions

Ideas raised in issues that need discussion, design, or a champion:

| Idea | Issue | Status |
| --- | --- | --- |
| *(Add your suggestion by opening an issue!)* | — | Open for discussion |

---

## ❌ Out of scope

OsaintMaint will **never** include:

- Access-control bypasses, credential collection, or private-system scraping
- Identity inference, doxxing, person targeting, or tactical tracking
- Automatic execution or dependency installation of saved third-party tools
- Data claims that exceed what the original public source actually supports

---

<p align="center">
  <sub>Roadmap items are not commitments or release dates — they are shared intent and community input.</sub>
</p>
