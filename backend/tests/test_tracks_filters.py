"""Phase C - GET /api/tracks new filters & sorts."""
import os
import requests

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://dj-library-ops.preview.emergentagent.com",
).rstrip("/")

API = f"{BASE_URL}/api/tracks"


def _get(**params):
    r = requests.get(API, params=params, timeout=15)
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]} params={params}"
    return r.json()


def test_has_bpm_false_total_24():
    d = _get(has_bpm="false", limit=1)
    assert d["total"] == 24, d["total"]


def test_has_bpm_true_total_286():
    d = _get(has_bpm="true", limit=1)
    assert d["total"] == 286, d["total"]


def test_key_8A_filter():
    d = _get(key="8A", limit=200)
    assert d["total"] >= 0
    for it in d["items"]:
        # camelot key may be under 'camelot_key' or 'key'
        k = it.get("key_camelot") or it.get("camelot_key") or it.get("key")
        assert k == "8A", f"unexpected key {k} in {it.get('id')}"


def test_folder_filter_scopes_paths():
    d = _get(folder="/app/fixture_library/library/A", limit=500)
    assert d["total"] > 0
    for it in d["items"]:
        p = it.get("filepath") or it.get("path") or ""
        assert p.startswith("/app/fixture_library/library/A"), p


def test_folder_outside_library_ignored():
    d = _get(folder="/etc", limit=1)
    assert d["total"] == 310


def test_sort_bitrate_asc():
    d = _get(sort="bitrate", order="asc", limit=10)
    bitrates = [it.get("bitrate_kbps") or it.get("bitrate") for it in d["items"]]
    bitrates = [b for b in bitrates if b is not None]
    assert bitrates == sorted(bitrates)


def test_sort_duration_desc():
    d = _get(sort="duration", order="desc", limit=10)
    durs = [it.get("duration_sec") or it.get("duration_seconds") or it.get("duration") for it in d["items"]]
    durs = [x for x in durs if x is not None]
    assert durs == sorted(durs, reverse=True)


def test_invalid_sort_falls_back_safely():
    r = requests.get(API, params={"sort": "bogus", "limit": 3}, timeout=15)
    assert r.status_code == 200
    d = r.json()
    assert d["total"] == 310


def test_combined_filters():
    d = _get(genre="House", bpm_min=120, has_bpm="true", limit=5)
    assert d["total"] >= 0
    for it in d["items"]:
        bpm = it.get("bpm")
        assert bpm is not None and bpm >= 120


def test_pagination_totals_stable():
    a = _get(limit=10, offset=0)
    b = _get(limit=10, offset=100)
    assert a["total"] == b["total"] == 310
    ids_a = {it["id"] for it in a["items"]}
    ids_b = {it["id"] for it in b["items"]}
    assert ids_a.isdisjoint(ids_b)
