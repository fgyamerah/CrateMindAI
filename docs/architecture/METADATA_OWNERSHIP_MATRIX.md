# Metadata Ownership Matrix

## Ownership Rules

| Metadata Field | Source of Truth | Mutable? | Controlled By | Notes |
|---|---|---|---|---|
| BPM | Mixed In Key | Fill missing only | MIK; `analyze-missing` fallback | Never overwrite valid existing BPM. |
| musical key | Mixed In Key | Fill missing only | MIK; `analyze-missing` fallback | Never overwrite valid existing key. |
| cue points | Mixed In Key/Rekordbox | Advisory only in pipeline | MIK/Rekordbox; `cue-suggest` stores suggestions | Pipeline must not overwrite Rekordbox/MIK cues. |
| artist | Deterministic artist tools and operator | Yes, high-risk | `artist-intelligence`, `artist-repair`, operator | `ai-normalize` artist output is ignored. |
| title | Existing tag plus deterministic cleanup/enrichment | Yes, high-risk | `metadata-sanitize`, `ai-normalize`, enrichment | Preserve version/remix identity. |
| version | Filename/title tokens | Yes, guarded | AI as hint; deterministic reconstruction | Do not invent `Original Mix` or missing versions. |
| album | Online enrichment/existing tags | Yes | `metadata-enrich-online` | Prefer fill-missing or ISRC-anchored changes. |
| label | Embedded label/enrichment | Yes | `label-clean`, enrichment | Organization/TPUB is the primary write target. |
| ISRC | Existing tag/trusted enrichment | Fill missing only | `metadata-enrich-online`, sanitizer validation | Do not overwrite existing valid ISRC. |
| genre | Existing/operator | UNVERIFIED | Legacy pipeline/tagger | Ownership not fully verified in this pass. |
| filename | Embedded trusted artist/title/version | Yes, high-risk | `filename-normalize` | Preview first; no overwrite collisions. |
| folder structure | Operator/library tools | Yes, critical | `library-organize`, `artist-merge`, `artist-folder-clean`, organizer | Requires path reconciliation after moves. |

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

