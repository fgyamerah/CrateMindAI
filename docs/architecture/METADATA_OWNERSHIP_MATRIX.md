# Metadata Ownership Matrix

## Ownership Rules

| Metadata Field | Source of Truth | Mutable? | Controlled By | Notes |
|---|---|---|---|---|
| filepath | Filesystem/current `tracks` table | Yes, critical | Filesystem moves plus `update_track_path_references()` | Current path identity must come from disk and canonical `tracks`; stale `processed_state` rows are historical only. |
| filename | Filesystem-derived | Yes, high-risk | `filename-normalize`, `library_organize.py` | Filename follows filesystem state; avoid deriving canonical metadata solely from filename when trusted tags exist. |
| artist | Controlled pipeline normalization | Yes, high-risk | `artist-intelligence`, `artist-repair`, operator | Deterministic validation required; `ai-normalize` artist output is ignored. |
| title | Controlled pipeline normalization | Yes, high-risk | `metadata-sanitize`, deterministic cleanup, enrichment with validation | Preserve numeric titles, version/remix identity, and valid DJ metadata. |
| version | Filename/title tokens | Yes, guarded | AI as hint; deterministic reconstruction | Do not invent `Original Mix` or missing versions. |
| album | Enrichment, cautious | Yes, guarded | `metadata-enrich-online` | Prefer fill-missing, ISRC-anchored, or high-confidence changes; log provenance. |
| label | Enrichment, cautious | Yes, guarded | `label-clean`, enrichment | Organization/TPUB is the primary write target; preserve manual corrections. |
| genre | Enrichment/manual, cautious | Yes, guarded | Operator, enrichment, future taxonomy tools | Genre ownership remains cautious because local DJ taxonomy may override online sources. |
| ISRC | Enrichment, only if missing | Fill missing only | `metadata-enrich-online`, sanitizer validation | Do not overwrite existing valid ISRC. |
| BPM | Mixed In Key/Rekordbox | Never overwrite | MIK/Rekordbox; analysis fallback only for missing values | Valid existing BPM is owned by MIK/Rekordbox. |
| key | Mixed In Key | Never overwrite | MIK; analysis fallback only for missing values | Valid existing musical/Camelot key is owned by MIK. |
| cues | Mixed In Key/Rekordbox | Never overwrite | MIK/Rekordbox; `cue-suggest` advisory only | Pipeline cue suggestions are not authoritative cue writes. |
| folder structure | Operator/library tools | Yes, critical | `library_organize.py`, `artist-merge`, `artist-folder-clean` | Prefer Phase 3-safe modules; `modules/organizer.py` is legacy/deprecated. |

## Mixed In Key Boundaries

- Mixed In Key owns BPM, musical key, and cue data.
- Pipeline analysis may fill missing BPM/key only.
- Pipeline cue suggestions are advisory DB/sidecar data and must not be treated as authoritative Rekordbox cues.

## Rekordbox Assumptions

- ID3v2.3 is preferred for Rekordbox compatibility.
- Rekordbox XML/cue ownership belongs to Rekordbox plus Mixed In Key.
- Playlist/export generation should not mutate source audio tags.

## AI Mutation Restrictions

- AI must not write BPM, key, cue points, filenames, or folder structure.
- AI artist proposals are not trusted in `ai-normalize`.
- AI title/version/label suggestions require confidence gates and deterministic validation.
- Online enrichment ISRC matches are strong but should still log provenance and source conflicts.

## Metadata Mutation Rules

- Metadata mutation commands should default to dry-run/preview.
- Apply mode must be explicit and should require confirmation (`--yes` or `--force`) for destructive or broad writes.
- Destructive writes must be logged with enough detail to review intended/applied changes.
- AI output cannot directly mutate files without deterministic validation and ownership checks.
- BPM, key, and cue fields must not be overwritten when valid values already exist.
- Filepath changes must update canonical DB references through `update_track_path_references()` where Phase 3 path safety applies.
- Stale `processed_state` rows are historical records and must not be rewritten by path updates.
