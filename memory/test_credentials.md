# Test credentials

CrateMindAI is trusted-local-only software with **no authentication by design**
(documented in AGENTS.md §8). There are no user accounts or credentials.

- Frontend preview: https://dj-library-ops.preview.emergentagent.com
- Backend API: same origin under /api (internally localhost:8001)
- Fixture library root: /app/fixture_library (deterministic seed, 310 tracks)
- Re-seed: `python3 scripts/seed_fixture_library.py --force`
