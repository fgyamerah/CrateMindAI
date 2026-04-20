# CrateMindAI Commands

A reference for the CrateMindAI intelligence pipeline CLI.

Version 2.0.0 &nbsp;·&nbsp; Updated 2026-04-20

---

## Table of Contents

- [Core Pipeline](#core-pipeline)
- [Operational States](#operational-states)
- [Safety Guarantees](#safety-guarantees)
- [metadata-sanitize](#metadata-sanitize)
- [ai-normalize](#ai-normalize)
- [artist-intelligence](#artist-intelligence)
- [metadata-enrich-online](#metadata-enrich-online)
- [review-queue](#review-queue)

---

## Core Pipeline

`metadata-sanitize` → `ai-normalize` → `artist-intelligence` → `metadata-enrich-online`

Each stage is standalone. Run one, or compose the full pipeline.  
Preview by default — nothing writes without `--apply`.

---

## Operational States

Applies to `metadata-enrich-online` results:

| State | Condition | Action |
|---|---|---|
| **APPLY** | conf ≥ 0.80, all safety rules pass | Written with `--apply` |
| **REVIEW** | 0.70 ≤ conf < 0.80 | Added to review queue |
| **SKIP** | Hard safety block fires | Moved to IGNORED with `--move-ignored` |

---

## Safety Guarantees

- Artist field: **never proposed or modified**
- BPM, key, and cues: **never modified** — Mixed In Key owns these
- Version mismatch: conflicting version tokens → confidence capped at 0.74
- Low artist similarity (< 0.90, no ISRC anchor): confidence capped at 0.74
- ISRC exact match: overrides all formula limits → confidence 0.98
- Preview by default on every command — nothing writes without `--apply`

---

## metadata-sanitize

Deterministic offline cleaning of all metadata fields. Removes URL watermarks, promo artifacts, DJ pool tags, malformed ISRCs, and BPM/key comment noise.

> Idempotent — re-running a clean file produces no further changes.
> Safe to run before any AI or enrichment step.

### Purpose

- Strips URL watermarks, promo tags, and DJ pool artifacts from every metadata field
- Removes malformed ISRCs and BPM/key noise embedded in comment fields
- Runs fully offline — no network, no AI, no external dependencies
- Safe to repeat — already-clean files produce no further changes

### Common usage

```bash
python3 pipeline.py metadata-sanitize --input ~/Music/inbox --apply
```

### Flags

| Flag | Description |
|---|---|
| `--input DIR` | Directory of audio files to process. |
| `--apply` | Write changes to files. Without this flag, preview only. |
| `--verbose` | Enable debug logging. |

### Examples

```bash
python3 pipeline.py metadata-sanitize --input ~/Music/inbox
python3 pipeline.py metadata-sanitize --input ~/Music/inbox --apply
```

---

## ai-normalize

Local AI (Ollama) metadata proposals for artist, title, version, label, remixers, and featured artists. Preview by default; --apply to write. BPM, key, and cues are never touched.

> Min confidence: 0.75 — proposals below threshold are skipped, not applied.
> --pre-sanitize: runs metadata-sanitize before inference (recommended).

### Purpose

- Proposes improved artist, title, version, label, and remixer values via a local LLM
- Uses Ollama — all inference runs on your machine, no data sent externally
- Skips proposals below 0.75 confidence; BPM, key, and cues are never touched
- Use `--pre-sanitize` to clean fields before inference in a single pass

### Common usage

```bash
python3 pipeline.py ai-normalize --input ~/Music/inbox --pre-sanitize --apply
```

### Flags

| Flag | Description |
|---|---|
| `--input DIR` | Directory of audio files to process. |
| `--apply` | Write accepted proposals to files. |
| `--pre-sanitize` | Run metadata-sanitize before AI inference. |
| `--min-confidence 0.75` | Minimum confidence to accept a proposal. |
| `--model MODEL` | Ollama model to use. Default: OLLAMA_DEFAULT_MODEL env. |
| `--verbose` | Enable debug logging. |

### Examples

```bash
python3 pipeline.py ai-normalize --input ~/Music/inbox
python3 pipeline.py ai-normalize --input ~/Music/inbox --apply
python3 pipeline.py ai-normalize --input ~/Music/inbox --pre-sanitize --apply
python3 pipeline.py ai-normalize --input ~/Music/inbox --min-confidence 0.80 --apply
```

---

## artist-intelligence

Deterministic artist normalization, alias resolution, and identity consistency across the library. Builds an alias store for consistent downstream processing.

> Package: intelligence/artist/

### Purpose

- Resolves artist name variants to a single canonical form across the library
- Stores aliases persistently for consistent cross-run identity resolution
- Handles collab/feat suffixes without corrupting the primary artist name
- Deterministic — same input always produces the same output

### Common usage

```bash
python3 pipeline.py artist-intelligence --input ~/Music/inbox --apply
```

### Flags

| Flag | Description |
|---|---|
| `--input DIR` | Directory or library path to process. |
| `--apply` | Write normalized artist tags to files. |
| `--verbose` | Enable debug logging. |

### Examples

```bash
python3 pipeline.py artist-intelligence --input ~/Music/inbox
python3 pipeline.py artist-intelligence --input ~/Music/inbox --apply
```

---

## metadata-enrich-online

Fill missing album, label, and ISRC via Spotify + Deezer matching with confidence scoring. Preview by default; --apply to write. Artist field is never proposed.

> Operational states per track:
>   APPLY   conf >= 0.80; all safety rules pass -> written with --apply
>   REVIEW  0.70 <= conf < 0.80 -> added to review queue
>   SKIP    hard safety block fires -> moved to IGNORED with --move-ignored
> 
> IGNORED path: /home/koolkatdj/Music/music/IGNORED/

### Purpose

- Queries Spotify, Deezer, and Traxsource to fill missing album, label, and ISRC
- Routes each result to APPLY, REVIEW, or SKIP based on confidence and safety rules
- Artist field is never proposed; version mismatches block auto-apply
- Use `--move-ignored` to quarantine unresolvable files automatically

### Common usage

```bash
python3 pipeline.py metadata-enrich-online --input ~/Music/inbox --apply --move-ignored
```

### Flags

| Flag | Description |
|---|---|
| `--input DIR` | Directory of audio files to enrich. |
| `--apply` | Write APPLY-state changes to files. |
| `--min-confidence 0.80` | Minimum confidence to apply. Default: 0.80. |
| `--move-ignored` | Move all hard-rejected files to the IGNORED quarantine directory. |
| `--verbose` | Enable debug logging. |

### Examples

```bash
python3 pipeline.py metadata-enrich-online --input ~/Music/inbox
python3 pipeline.py metadata-enrich-online --input ~/Music/inbox --apply
python3 pipeline.py metadata-enrich-online --input ~/Music/inbox --apply --move-ignored
python3 pipeline.py metadata-enrich-online --input ~/Music/inbox --min-confidence 0.85
```

---

## review-queue

Review and resolve medium-confidence enrichment results interactively. Reads entries populated by metadata-enrich-online (REVIEW state: 0.70 <= conf < 0.80).

> Queue file: data/intelligence/enrichment_review_queue.json
> Actions: a=apply  s=skip  d=delete  n=next  q=quit

### Purpose

- Opens an interactive session to resolve REVIEW-state enrichment results
- Each entry shows proposed changes with before/after field values
- Accepted entries are written immediately; skipped entries stay in the queue
- Use `--list-only` to audit the queue without making any changes

### Common usage

```bash
python3 pipeline.py review-queue
```

### Flags

| Flag | Description |
|---|---|
| `--list-only` | Print all pending entries without entering interactive mode. |

### Examples

```bash
python3 pipeline.py review-queue
python3 pipeline.py review-queue --list-only
```

---
