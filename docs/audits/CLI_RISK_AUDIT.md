# CLI Risk Audit

**Source:** `docs/generated/cli_command_index.md`, `docs/generated/dangerous_operations_index.md`
**Date:** 2026-05-03

---

## Classification Key

| Symbol | Meaning |
|--------|---------|
| ✅ | safe — read-only |
| 🏷 | writes metadata (audio file tags) |
| 📛 | renames files on disk |
| 📦 | moves files to a different directory |
| 🗑 | deletes/removes files or DB rows |
| 🔒 | requires `--apply` (preview is default) |
| ⚠️ | missing dry-run protection (writes without explicit gate) |
| 🔁 | needs rollback logging (writes are not reversible via CLI) |

---

## Master Table

| Command | Safe | Writes Tags | Renames | Moves | Deletes | `--apply` gate | Missing protection | Needs rollback |
|---------|:----:|:-----------:|:-------:|:-----:|:-------:|:--------------:|:-----------------:|:--------------:|
| `metadata-sanitize` | | 🏷 | | | | 🔒 | | — rollback exists |
| `metadata-sanitize-rollback` | | 🏷 | | | | 🔒 | | — inverse of above |
| `title-number-recover` | | 🏷 | | | | 🔒 | | 🔁 |
| `artist-repair` | | 🏷 | | 📦¹ | | 🔒 | | 🔁 |
| `artist-repair-review` | | 🏷 | | | | 🔒² | | 🔁 |
| `artist-intelligence` | | 🏷 | | | | 🔒 | | 🔁 |
| `ai-normalize` | | 🏷 | | | | 🔒 | | 🔁 |
| `metadata-enrich-online` | | 🏷 | | 📦³ | | 🔒 | | 🔁 |
| `metadata-clean` | | 🏷 | | | | | ⚠️ | 🔁 |
| `label-clean` | | 🏷⁴ | | | | | | 🔁 |
| `analyze-missing` | | 🏷 | | 📦⁵ | | | ⚠️ | 🔁 |
| `tag-normalize` | | 🏷 | | | | | ⚠️ | 🔁 |
| `audit-quality` | ✅⁶ | 🏷⁷ | | 📦⁸ | | | | 🔁⁸ |
| `filename-normalize` | | | 📛 | 📦⁹ | | 🔒 | | 🔁 |
| `library-organize` | | | | 📦 | | 🔒 | | 🔁 |
| `artist-merge` | | | | 📦 | 🗑¹⁰ | 🔒 | | 🔁 |
| `artist-folder-clean` | | | | 📦 | 🗑¹⁰ | 🔒 | | 🔁 |
| `dedupe` | | | | 📦 | | 🔒 | | 🔁 |
| `convert-audio` | | | | 📦¹¹ | 🗑¹² | 🔒¹³ | | 🔁 |
| `review-queue` | | 🏷 | | | | | ⚠️¹⁴ | 🔁 |
| `cue-suggest` | ✅¹⁵ | | | | | | ⚠️¹⁶ | — |
| `set-builder` | ✅ | | | | | | | — |
| `harmonic-suggest` | ✅ | | | | | | | — |
| `playlists` | ✅¹⁷ | | | | | | | — |
| `rekordbox-export` | ✅¹⁷ | | | | | | | — |
| `label-intel` | ✅ | | | | | | | — |
| `analyze-missing` (--dry-run) | ✅ | | | | | | | — |
| `audit-quality` (default) | ✅ | | | | | | | — |
| `db-prune-stale` | ✅ | | | | | | | — |
| `build-fewshot` | ✅¹⁸ | | | | | | | — |
| `generate-docs` | ✅¹⁹ | | | | | | | — |
| `validate-docs` | ✅ | | | | | | | — |
| `orphan-scan` | ✅ | | | | | | | — |

---

## Footnotes

1. `artist-repair --move-artist-review --apply` moves MEDIUM/LOW-confidence files to `.BIN/ARTIST_REVIEW/`
2. `artist-repair-review` gate is `--apply-approved`, not `--apply`
3. `metadata-enrich-online --move-ignored --apply` moves blocked files to `.BIN/IGNORED/`
4. `label-clean` requires `--write-tags` to write; read-only by default — effectively safe without the flag
5. `analyze-missing` has `shutil.move` for relocating files; check whether this is gated
6. `audit-quality` default mode: probe + classify only; no tag writes, no moves
7. `audit-quality --write-tags` writes `TXXX:QUALITY` / Vorbis `QUALITY` to file tags
8. `audit-quality --move-low-quality DIR` moves LOW-tier files; no rollback command exists
9. `filename-normalize --move-artist-review --apply` moves unsafe-artist files to `CHKARTISTNAMES`
10. `artist-merge` and `artist-folder-clean` delete DB rows (`DELETE FROM tracks`) for moved source paths — DB change is permanent; files are moved not deleted
11. `convert-audio` archives source `.m4a` to `--archive` dir after successful conversion (move, not delete)
12. `convert-audio` calls `dst.unlink()` on failed conversion to clean up broken output — the broken output only, never the source
13. `convert-audio` preview mode is `--dry-run`; no explicit `--apply` flag — writes happen by default unless `--dry-run` is passed
14. `review-queue` is an interactive CLI loop; pressing `a` applies enrichment tags immediately with no `--apply` flag and no dry-run escape
15. `cue-suggest --dry-run` is read-only; without `--dry-run` it writes cue points to the DB (not audio file tags)
16. `cue-suggest` has no `--apply` gate — DB writes happen unless `--dry-run` is passed explicitly
17. `playlists` and `rekordbox-export` write M3U / XML files to output directories; they do not modify audio file tags or move/rename audio files
18. `build-fewshot` writes only `fewshot_examples.jsonl` — no audio files touched
19. `generate-docs` writes `COMMANDS.txt`, `COMMANDS.html`, and splices `README.md` — no audio files touched

---

## Risk Summary

### High Risk — writes without explicit gate

| Command | Default behaviour | Gap |
|---------|------------------|-----|
| `metadata-clean` | Applies changes unless `--dry-run` is passed | Inverted safety model vs. rest of pipeline |
| `analyze-missing` | Writes BPM/key to DB and audio tags unless `--dry-run` | No `--apply` gate |
| `tag-normalize` | Writes ID3 format changes; no `--apply` gate visible | No `--apply` gate |
| `review-queue` | Interactive `a` keystroke writes tags immediately | No `--apply` equivalent; no undo |
| `cue-suggest` | Writes cue points to DB without `--apply` | `--dry-run` required to stay safe |

### Medium Risk — moves files with no rollback command

| Command | What moves | Rollback available? |
|---------|-----------|---------------------|
| `library-organize` | Files to letter/artist sorted folders | ❌ |
| `artist-merge` | Files to canonical artist folder; DB rows deleted | ❌ |
| `artist-folder-clean` | Files to renamed/merged folder; DB rows deleted | ❌ |
| `filename-normalize` | Files renamed in place | ❌ |
| `audit-quality --move-low-quality` | LOW files to a quarantine dir | ❌ |
| `metadata-enrich-online --move-ignored` | Blocked files to `.BIN/IGNORED/` | ❌ |

### Rollback coverage

| Command | Rollback mechanism |
|---------|-------------------|
| `metadata-sanitize` | ✅ `metadata-sanitize-rollback` + `--output-json` log |
| All others | ❌ None. Only `run_logger` processed_state (`--reset-stage` re-runs, does not revert) |

---

## Consistency Notes

- **`metadata-clean` default is apply** — inconsistent with every other write command in the pipeline. Should require `--apply` or be inverted to `--dry-run` default like `metadata-sanitize`.
- **`convert-audio` default is apply** — `--dry-run` must be explicitly passed to preview. Inconsistent with `metadata-sanitize` / `artist-repair` pattern.
- **`review-queue` has no safety gate** — the only command where a keypress writes to audio files with no confirmation or `--apply` flag.
- **`artist-merge` / `artist-folder-clean` delete DB rows** — these are the only commands that permanently remove records from the `tracks` table. `db-prune-stale` by contrast only marks rows stale.
- **Rollback gap is wide** — `metadata-sanitize-rollback` is the only CLI undo command. Tag writes from `ai-normalize`, `artist-repair`, `artist-intelligence`, `metadata-enrich-online`, `label-clean`, and `metadata-clean` are permanent once applied.
