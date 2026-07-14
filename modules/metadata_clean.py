"""
modules/metadata_clean.py

Global metadata cleanup — removes URL/promo junk from ALL tag fields
across the entire sorted library.

Tag families covered:
  ID3v2   title, artist, album, albumartist, genre, comment, publisher (TPUB),
          grouping, catalog_number, encoded_by, composer, conductor, copyright,
          disc_number, mood, lyrics (USLT), all standard URL frames
          (WOAR/WOAS/WCOM/WOAF/WORS/WPUB), user-defined URL frames (WXXX),
          GEOB (General Object), all TXXX custom frames
  APEv2   all non-essential APE string keys (Subtitle, Website, Grouping,
          Beatport URL fields, any junk-only key)
  ID3v1   block removed on save (v1=0) — ID3v1 comment junk cannot persist

Usage:
    python pipeline.py metadata-clean --dry-run   # preview, no writes
    python pipeline.py metadata-clean             # apply

Note on TXXX protection: BPM, Key, Camelot, InitialKey are NOT blanket-protected.
sanitize_text() leaves valid values ("8A", "128", "Am") unchanged; only junk
values (URLs, promo phrases) are cleared.  ReplayGain and MusicBrainz frames
ARE unconditionally protected.
"""
import logging
import re
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config
from modules.junk_patterns import load_junk_patterns
from modules.sanitizer import sanitize_text
from modules.textlog import log_action

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_RE_CAMELOT_KEY      = re.compile(r'\b(1[0-2]|[1-9])[AB]\b', re.IGNORECASE)
_RE_BPM_NUMBER_FIRST = re.compile(r'\b\d{2,3}\s*bpm\b', re.IGNORECASE)
_RE_BPM_LABEL_FIRST  = re.compile(r'\bbpm\s*:?\s*\d{2,3}\b', re.IGNORECASE)
_RE_KEY_LABEL        = re.compile(r'\bkey\s*:?\s*', re.IGNORECASE)
_RE_MUSIC_KEY_ONLY   = re.compile(r'^[A-G][#b]?(?:m|maj|min|major|minor)?$', re.IGNORECASE)
_RE_MULTI_SEP        = re.compile(r'(?:\s*[-|–]\s*){2,}')
_RE_MULTI_SPACE      = re.compile(r'  +')
_RE_EDGE_JUNK        = re.compile(r'^[\s\-|,;:]+|[\s\-|,;:]+$')

# Numeric track-number prefix in title: "3 | Title", "01 \| Title", "11/ Title"
_RE_TITLE_NUM_PREFIX = re.compile(r'^\d+\s*(?:\\?\||\\/|/|\\)\s*', re.UNICODE)

_RE_PURE_URL = re.compile(
    r'^(?:https?://\S+|www\.\S+|'
    r'[a-z0-9][\w\-]*\.(?:com|net|org|fm|dj|co|io|info|me|biz|us|tv|cc|to)\S*)$',
    re.IGNORECASE,
)
_RE_CONTAINS_URL = re.compile(
    r'https?://|www\.|[a-z0-9][\w\-]*\.(?:com|net|org|fm|dj|co|io|info|me|biz|us|tv|cc|to)',
    re.IGNORECASE,
)
_RE_Y_DJ_LP = re.compile(
    r'[\[\(]?\s*\by\b\s+\bdj\b\s+l\.?\s*p\.?\s*[\]\)]?', re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Catalog number constants
# ---------------------------------------------------------------------------
_CATALOG_TXXX_DESCS  = ["CATALOGNUMBER", "Catalog Number", "catalog number", "CATALOG"]
_CATALOG_VORBIS_KEYS = ["CATALOGNUMBER", "catalognumber", "catalog number", "CATALOG"]
_CATALOG_M4A_KEY     = "----:com.apple.iTunes:CATALOGNUMBER"

# ---------------------------------------------------------------------------
# Easy-tag fields (mutagen easy=True)
# ---------------------------------------------------------------------------
_EASY_FIELDS = [
    "title", "artist", "album", "albumartist",
    "genre", "comment", "organization", "grouping",
]

# Easy fields cleared when the whole value is a URL/domain
_CLEAR_IF_PURE_URL = {"albumartist", "organization", "catalog_number"}

# ---------------------------------------------------------------------------
# ID3 raw frame mappings
# ---------------------------------------------------------------------------

_ID3_TEXT_FRAMES: Dict[str, str] = {
    "encoded_by":  "TENC",
    "composer":    "TCOM",
    "conductor":   "TPE3",
    "copyright":   "TCOP",
    "disc_number": "TPOS",
    "mood":        "TMOO",
}

_ID3_URL_FRAMES: Dict[str, str] = {
    "url_woar": "WOAR",   # Official Artist/Performer Webpage
    "url_woas": "WOAS",   # Official Audio Source Webpage
    "url_wcom": "WCOM",   # Commercial Information
    "url_woaf": "WOAF",   # Official Audio File Webpage
    "url_wors": "WORS",   # Official Internet Radio Station Homepage
    "url_wpub": "WPUB",   # Publishers Official Webpage
}

# ---------------------------------------------------------------------------
# FLAC/Vorbis extra keys
# ---------------------------------------------------------------------------
_VORBIS_EXTRA_KEYS: Dict[str, List[str]] = {
    "encoded_by":  ["ENCODED-BY", "ENCODEDBY", "ENCODER"],
    "composer":    ["COMPOSER"],
    "conductor":   ["CONDUCTOR"],
    "copyright":   ["COPYRIGHT"],
    "disc_number": ["DISCNUMBER"],
    "mood":        ["MOOD"],
    "lyrics":      ["LYRICS", "UNSYNCEDLYRICS"],
    "url_contact": ["CONTACT", "WEBSITE"],
}

# ---------------------------------------------------------------------------
# M4A atom keys
# ---------------------------------------------------------------------------
_M4A_EXTRA_ATOMS: Dict[str, str] = {
    "composer":  "©wrt",
    "copyright": "cprt",
    "lyrics":    "©lyr",
}

# ---------------------------------------------------------------------------
# Field routing helpers
# ---------------------------------------------------------------------------

# Raw ID3 field names (not handled via easy tags)
_RAW_FIELD_NAMES: frozenset = frozenset(
    list(_ID3_TEXT_FRAMES.keys()) +
    list(_ID3_URL_FRAMES.keys()) +
    ["lyrics", "url_contact"]
)


def _is_raw_field(field_name: str) -> bool:
    return (
        field_name in _RAW_FIELD_NAMES
        or field_name.startswith("txxx:")
        or field_name.startswith("wxxx:")
        or field_name.startswith("geob:")
        or field_name.startswith("comm_extra:")
    )


def _is_ape_field(field_name: str) -> bool:
    return field_name.startswith("ape:")


# ---------------------------------------------------------------------------
# TXXX protection — ReplayGain and MusicBrainz only.
# BPM / Key / Camelot are NOT blanket-protected: sanitize_text() leaves valid
# values ("8A", "128", "Am") untouched; only junk values get cleared.
# ---------------------------------------------------------------------------
_TXXX_PROTECTED_LOWER: frozenset = frozenset({
    "catalognumber", "catalog number", "catalog",
    "replaygain_track_gain", "replaygain_track_peak",
    "replaygain_album_gain", "replaygain_album_peak",
    "musicbrainz track id", "musicbrainz artist id",
    "musicbrainz album id", "musicbrainz album artist id",
    "musicbrainz_trackid", "musicbrainz_artistid",
})

# ---------------------------------------------------------------------------
# APE protection — core identity / replay gain fields only
# ---------------------------------------------------------------------------
_APE_PROTECTED_LOWER: frozenset = frozenset({
    "title", "artist", "album", "albumartist", "genre", "year", "track",
    "replaygain_track_gain", "replaygain_track_peak",
    "replaygain_album_gain", "replaygain_album_peak",
    "catalognumber", "isrc",
})


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FieldChange:
    field_name: str
    original:   str
    cleaned:    str
    reason:     str
    cleared:    bool = False
    tag_family: str  = "ID3v2"   # "ID3v2" | "APEv2" | "easy"


@dataclass
class TrackResult:
    filepath:    str
    changes:     List[FieldChange] = dc_field(default_factory=list)
    is_corrupt:  bool = False

    @property
    def changed(self) -> bool:
        return bool(self.changes)


# ---------------------------------------------------------------------------
# Sanitization helpers
# ---------------------------------------------------------------------------

def _sanitize_comment(text: str) -> str:
    result = sanitize_text(text)
    result = _RE_CAMELOT_KEY.sub('', result)
    result = _RE_KEY_LABEL.sub('', result)
    result = _RE_BPM_NUMBER_FIRST.sub('', result)
    result = _RE_BPM_LABEL_FIRST.sub('', result)
    result = _RE_MULTI_SEP.sub(' - ', result)
    result = _RE_MULTI_SPACE.sub(' ', result)
    result = _RE_EDGE_JUNK.sub('', result)
    result = result.strip()
    if _RE_MUSIC_KEY_ONLY.match(result):
        result = ''
    return result


def _clean_title(text: str) -> str:
    result = _RE_TITLE_NUM_PREFIX.sub('', text)
    return sanitize_text(result)


def _clean_raw_field(field_name: str, value: str) -> str:
    """Sanitize a non-easy-tag field. All such fields are non-essential."""
    if field_name.startswith("comm_extra:"):
        return _sanitize_comment(value)
    return sanitize_text(value)


def _reason_for_change(field_name: str, original: str, cleaned: str) -> str:
    reasons = []
    if not cleaned and original:
        if _RE_Y_DJ_LP.search(original):
            return "promo_junk_y_dj_lp"
        if _RE_PURE_URL.match(original.strip()):
            return "url_watermark_cleared"
        return "promo_junk_cleared"

    if re.search(r'https?://', original, re.IGNORECASE):
        reasons.append("url_stripped")
    elif re.search(r'www\.', original, re.IGNORECASE):
        reasons.append("www_stripped")
    elif _RE_CONTAINS_URL.search(original):
        reasons.append("domain_stripped")

    if field_name in ("comment",) or field_name.startswith("comm_extra:"):
        if _RE_CAMELOT_KEY.search(original):
            reasons.append("camelot_key_stripped")
        if _RE_BPM_NUMBER_FIRST.search(original) or _RE_BPM_LABEL_FIRST.search(original):
            reasons.append("bpm_string_stripped")

    if _RE_Y_DJ_LP.search(original) and not _RE_Y_DJ_LP.search(cleaned):
        reasons.append("promo_junk_y_dj_lp")

    for word in load_junk_patterns().source_junk_substrings:
        if word in original.lower() and word not in cleaned.lower():
            reasons.append("promo_phrase_stripped")
            break

    if field_name == "title" and _RE_TITLE_NUM_PREFIX.match(original):
        reasons.append("numeric_prefix_stripped")

    return ", ".join(reasons) if reasons else "sanitized"


# ---------------------------------------------------------------------------
# Catalog number: format-aware read / write
# ---------------------------------------------------------------------------

def _read_catalog_number(path: Path) -> Optional[str]:
    suffix = path.suffix.lower()
    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=False)
        if audio is None or audio.tags is None:
            return None
        if suffix == ".mp3":
            for desc in _CATALOG_TXXX_DESCS:
                frame = audio.tags.get(f"TXXX:{desc}")
                if frame and frame.text:
                    return str(frame.text[0])
            return None
        elif suffix in (".flac", ".ogg", ".opus"):
            for key in _CATALOG_VORBIS_KEYS:
                val = audio.get(key)
                if val:
                    return val[0] if isinstance(val, list) else str(val)
            return None
        elif suffix in (".m4a", ".mp4"):
            val = audio.get(_CATALOG_M4A_KEY)
            if val:
                v = val[0]
                return v.decode("utf-8", errors="replace") if isinstance(v, bytes) else str(v)
            return None
        return None
    except Exception:
        return None


def _write_catalog_number(path: Path, value: str) -> None:
    suffix = path.suffix.lower()
    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=False)
        if audio is None or audio.tags is None:
            return
        if suffix == ".mp3":
            from mutagen.id3 import TXXX
            existing_desc = None
            for desc in _CATALOG_TXXX_DESCS:
                if f"TXXX:{desc}" in audio.tags:
                    existing_desc = desc
                    break
            if existing_desc is None:
                existing_desc = "CATALOGNUMBER"
            for desc in _CATALOG_TXXX_DESCS:
                try:
                    del audio.tags[f"TXXX:{desc}"]
                except KeyError:
                    pass
            if value:
                audio.tags.add(TXXX(encoding=3, desc=existing_desc, text=[value]))
            audio.save(v2_version=config.ID3_VERSION)
        elif suffix in (".flac", ".ogg", ".opus"):
            matched_key = None
            for key in _CATALOG_VORBIS_KEYS:
                if key in audio:
                    matched_key = key
                    break
            if matched_key:
                if value:
                    audio[matched_key] = [value]
                else:
                    del audio[matched_key]
                audio.save()
        elif suffix in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4FreeForm
            if value:
                audio[_CATALOG_M4A_KEY] = [MP4FreeForm(value.encode("utf-8"))]
            elif _CATALOG_M4A_KEY in audio:
                del audio[_CATALOG_M4A_KEY]
            audio.save()
    except Exception as exc:
        log.debug("Could not write catalog number to %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Easy-tag read
# ---------------------------------------------------------------------------

def _read_tags(path: Path) -> Optional[Dict[str, str]]:
    """
    Read easy-tag fields.
    Returns None if the file is corrupt/unreadable.
    Returns {} (or partial dict) if readable but fields are absent.
    """
    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=True)
        if audio is None:
            return None
        def get(key: str) -> str:
            return (audio.get(key) or [""])[0]
        tags = {f: get(f) for f in _EASY_FIELDS}
        cat = _read_catalog_number(path)
        if cat is not None:
            tags["catalog_number"] = cat
        return tags
    except Exception as exc:
        log.warning("UNREADABLE: %s — %s", path.name, exc)
        return None


# ---------------------------------------------------------------------------
# ID3 raw frame read
# ---------------------------------------------------------------------------

def _read_id3_extra(tags) -> Dict[str, str]:
    """Read extra ID3 frames from an open ID3 tags object."""
    result: Dict[str, str] = {}

    # Standard text frames (TENC, TCOM, TPE3, TCOP, TPOS, TMOO)
    for field_name, frame_id in _ID3_TEXT_FRAMES.items():
        frame = tags.get(frame_id)
        if frame and frame.text:
            val = str(frame.text[0]).strip()
            if val:
                result[field_name] = val

    # Standard URL frames (WOAR, WOAS, WCOM, WOAF, WORS, WPUB)
    for field_name, frame_id in _ID3_URL_FRAMES.items():
        frame = tags.get(frame_id)
        if frame is None:
            continue
        url = getattr(frame, "url", None)
        if url:
            val = str(url).strip()
            if val:
                result[field_name] = val

    # WXXX — User-defined URL frames (shown as "User-defined URL" in Kid3)
    for key in list(tags.keys()):
        if not key.startswith("WXXX:"):
            continue
        desc  = key[5:]
        frame = tags.get(key)
        url   = getattr(frame, "url", None) if frame else None
        if url and str(url).strip():
            result[f"wxxx:{desc}"] = str(url).strip()

    # USLT — unsynced lyrics
    for key in tags.keys():
        if key.startswith("USLT"):
            frame = tags[key]
            text  = getattr(frame, "text", "")
            if text and str(text).strip():
                result["lyrics"] = str(text).strip()
                break

    # GEOB — General Encapsulated Object
    # Check desc + filename for junk; if junk → mark for deletion.
    # The "original" we report is the desc [mime] so dry-run is informative.
    for key in list(tags.keys()):
        if not key.startswith("GEOB:"):
            continue
        desc     = key[5:]
        frame    = tags.get(key)
        if frame is None:
            continue
        fdesc    = (getattr(frame, "desc",     "") or "").strip()
        fname    = (getattr(frame, "filename", "") or "").strip()
        fmime    = (getattr(frame, "mime",     "") or "").strip()
        combined = " ".join(filter(None, [fdesc, fname]))
        if combined:
            result[f"geob:{desc}"] = f"{combined} [{fmime}]" if fmime else combined

    # TXXX custom frames — skip unconditionally-protected descriptions
    for key in list(tags.keys()):
        if not key.startswith("TXXX:"):
            continue
        desc = key[5:]
        if desc.lower() in _TXXX_PROTECTED_LOWER:
            continue
        frame = tags.get(key)
        if frame and frame.text:
            val = str(frame.text[0]).strip()
            if val:
                result[f"txxx:{desc}"] = val

    # Secondary COMM frames (easy tag reads only the "best" one)
    for key in tags.keys():
        if not key.startswith("COMM:"):
            continue
        frame     = tags[key]
        text_list = getattr(frame, "text", [])
        val       = str(text_list[0]).strip() if text_list else ""
        if val:
            result[f"comm_extra:{key[5:]}"] = val

    return result


def _read_vorbis_extra(audio) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for field_name, keys in _VORBIS_EXTRA_KEYS.items():
        for key in keys:
            val = audio.get(key)
            if val:
                v = val[0] if isinstance(val, list) else str(val)
                s = str(v).strip()
                if s:
                    result[field_name] = s
                    break
    return result


def _read_m4a_extra(audio) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for field_name, atom in _M4A_EXTRA_ATOMS.items():
        val = audio.get(atom)
        if not val:
            continue
        v = val[0]
        if isinstance(v, bytes):
            v = v.decode("utf-8", errors="replace")
        elif hasattr(v, "value"):
            raw = v.value
            v = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        else:
            v = str(v)
        s = v.strip()
        if s:
            result[field_name] = s
    return result


def _read_raw_frames(path: Path) -> Dict[str, str]:
    """Read extra tag frames not exposed by mutagen's easy interface."""
    suffix = path.suffix.lower()
    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=False)
        if audio is None or audio.tags is None:
            return {}
        if suffix == ".mp3":
            return _read_id3_extra(audio.tags)
        elif suffix in (".flac", ".ogg", ".opus"):
            return _read_vorbis_extra(audio)
        elif suffix in (".m4a", ".mp4"):
            return _read_m4a_extra(audio)
    except Exception as exc:
        log.debug("Could not read raw frames from %s: %s", path, exc)
    return {}


# ---------------------------------------------------------------------------
# APEv2 read
# ---------------------------------------------------------------------------

def _read_ape_tags(path: Path) -> Dict[str, str]:
    """
    Read APEv2 tags from a file.
    Returns {} if no APE tags are present or the file format doesn't use them.
    APE tags on MP3 files are non-standard; all non-protected keys are scanned.
    """
    try:
        from mutagen.apev2 import APEv2
        ape = APEv2(str(path))
        result: Dict[str, str] = {}
        for key, val in ape.items():
            if key.lower() in _APE_PROTECTED_LOWER:
                continue
            # kind: 0=text, 1=binary, 2=external(URL)
            kind = getattr(val, "kind", 0)
            if kind == 1:  # binary — skip
                continue
            text = str(val).strip()
            if text:
                result[f"ape:{key}"] = text
        return result
    except Exception:
        return {}  # no APE tags or format doesn't support them


# ---------------------------------------------------------------------------
# Tag writing — ID3
# ---------------------------------------------------------------------------

def _write_id3_raw_changes(tags, raw_changes: List[FieldChange]) -> None:
    """Apply raw changes to an open ID3 tags object."""
    for change in raw_changes:
        fn      = change.field_name
        cleaned = change.cleaned

        if fn in _ID3_TEXT_FRAMES:
            frame_id = _ID3_TEXT_FRAMES[fn]
            if cleaned:
                frame = tags.get(frame_id)
                if frame:
                    frame.text = [cleaned]
            else:
                tags.delall(frame_id)

        elif fn in _ID3_URL_FRAMES:
            frame_id = _ID3_URL_FRAMES[fn]
            if cleaned:
                frame = tags.get(frame_id)
                if frame:
                    frame.url = cleaned
            else:
                tags.delall(frame_id)

        elif fn.startswith("wxxx:"):
            target_lower = fn.lower()  # "wxxx:<desc>"
            matching = [
                k for k in list(tags.keys())
                if k.lower().startswith("wxxx:") and
                   "wxxx:" + k[5:].lower() == target_lower
            ]
            if cleaned:
                for k in matching:
                    frame = tags.get(k)
                    if frame:
                        frame.url = cleaned
            else:
                for k in matching:
                    try:
                        del tags[k]
                    except KeyError:
                        pass

        elif fn == "lyrics":
            if cleaned:
                for key in list(tags.keys()):
                    if key.startswith("USLT"):
                        frame = tags[key]
                        if hasattr(frame, "text"):
                            frame.text = cleaned
            else:
                tags.delall("USLT")

        elif fn.startswith("geob:"):
            # GEOB: always delete (binary content can't be meaningfully cleaned)
            desc     = fn[5:]
            geob_key = f"GEOB:{desc}"
            matching = [
                k for k in list(tags.keys())
                if k.lower() == geob_key.lower()
            ]
            for k in matching:
                try:
                    del tags[k]
                except KeyError:
                    pass

        elif fn.startswith("txxx:"):
            target_lower = fn[5:].lower()
            matching = [
                k for k in list(tags.keys())
                if k.lower().startswith("txxx:") and k[5:].lower() == target_lower
            ]
            if cleaned:
                for k in matching:
                    frame = tags.get(k)
                    if frame and frame.text:
                        frame.text = [cleaned]
            else:
                for k in matching:
                    try:
                        del tags[k]
                    except KeyError:
                        pass

        elif fn.startswith("comm_extra:"):
            comm_key = "COMM:" + fn[len("comm_extra:"):]
            if cleaned:
                frame = tags.get(comm_key)
                if frame and frame.text:
                    frame.text = [cleaned]
            else:
                try:
                    del tags[comm_key]
                except KeyError:
                    pass


def _write_vorbis_raw_changes(audio, raw_changes: List[FieldChange]) -> None:
    for change in raw_changes:
        fn      = change.field_name
        cleaned = change.cleaned
        if fn in _VORBIS_EXTRA_KEYS:
            keys        = _VORBIS_EXTRA_KEYS[fn]
            actual_keys = [k for k in keys if k in audio]
            if cleaned:
                target = actual_keys[0] if actual_keys else keys[0]
                audio[target] = [cleaned]
            else:
                for k in actual_keys:
                    try:
                        del audio[k]
                    except KeyError:
                        pass
        elif fn == "url_contact":
            for k in ["CONTACT", "WEBSITE"]:
                if k in audio:
                    if cleaned:
                        audio[k] = [cleaned]
                    else:
                        try:
                            del audio[k]
                        except KeyError:
                            pass


def _write_m4a_raw_changes(audio, raw_changes: List[FieldChange]) -> None:
    from mutagen.mp4 import MP4FreeForm
    for change in raw_changes:
        fn      = change.field_name
        cleaned = change.cleaned
        if fn not in _M4A_EXTRA_ATOMS:
            continue
        atom = _M4A_EXTRA_ATOMS[fn]
        if cleaned:
            audio[atom] = [MP4FreeForm(cleaned.encode("utf-8"))]
        elif atom in audio:
            try:
                del audio[atom]
            except KeyError:
                pass


# ---------------------------------------------------------------------------
# Tag writing — APEv2
# ---------------------------------------------------------------------------

def _write_ape_tags(path: Path, ape_changes: List[FieldChange], dry_run: bool) -> bool:
    """Write APEv2 changes back to the file."""
    if dry_run or not ape_changes:
        return True
    try:
        from mutagen.apev2 import APEv2
        ape = APEv2(str(path))
        for change in ape_changes:
            ape_key = change.field_name[4:]  # strip "ape:"
            if change.cleaned:
                ape[ape_key] = change.cleaned
            else:
                try:
                    del ape[ape_key]
                except KeyError:
                    pass
        ape.save(str(path))
        return True
    except Exception as exc:
        log.warning("Could not write APE tags to %s: %s", path, exc)
        return False


# ---------------------------------------------------------------------------
# Tag writing — ID3v1 strip + raw frame write
# ---------------------------------------------------------------------------

def _strip_mp3_id3v1(path: Path) -> None:
    """Remove the ID3v1 block (128 bytes at EOF). Data is preserved in ID3v2."""
    try:
        from mutagen.id3 import ID3
        tags_obj = ID3(str(path))
        tags_obj.save(str(path), v2_version=config.ID3_VERSION, v1=0)
    except Exception as exc:
        log.debug("Could not strip ID3v1 from %s: %s", path, exc)


def _write_raw_frames(path: Path, raw_changes: List[FieldChange], dry_run: bool) -> bool:
    """Write raw ID3 frame changes back to the audio file."""
    if dry_run or not raw_changes:
        return True
    suffix = path.suffix.lower()
    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=False)
        if audio is None or audio.tags is None:
            return False
        if suffix == ".mp3":
            _write_id3_raw_changes(audio.tags, raw_changes)
            audio.save(v2_version=config.ID3_VERSION, v1=0)  # v1=0: remove ID3v1 block
        elif suffix in (".flac", ".ogg", ".opus"):
            _write_vorbis_raw_changes(audio, raw_changes)
            audio.save()
        elif suffix in (".m4a", ".mp4"):
            _write_m4a_raw_changes(audio, raw_changes)
            audio.save()
        return True
    except Exception as exc:
        log.warning("Could not write raw frames to %s: %s", path, exc)
        return False


def _write_tags(path: Path, changes: List[FieldChange], dry_run: bool) -> bool:
    """
    Dispatch all field changes to the appropriate writer:
      easy fields  → MFile(easy=True)
      catalog      → _write_catalog_number()
      raw ID3/Vorbis/M4A frames → _write_raw_frames()
      APEv2 keys   → _write_ape_tags()
      ID3v1 block  → _strip_mp3_id3v1() (always for MP3, clears ID3v1 comment junk)
    """
    if dry_run:
        return True

    easy_changes = [c for c in changes
                    if c.field_name != "catalog_number"
                    and not _is_raw_field(c.field_name)
                    and not _is_ape_field(c.field_name)]
    cat_change   = next((c for c in changes if c.field_name == "catalog_number"), None)
    raw_changes  = [c for c in changes if _is_raw_field(c.field_name)]
    ape_changes  = [c for c in changes if _is_ape_field(c.field_name)]

    try:
        from mutagen import File as MFile

        if easy_changes:
            audio = MFile(str(path), easy=True)
            if audio is None:
                return False
            for change in easy_changes:
                try:
                    if change.cleaned:
                        audio[change.field_name] = [change.cleaned]
                    else:
                        try:
                            del audio[change.field_name]
                        except KeyError:
                            pass
                except Exception:
                    pass
            audio.save()

        if cat_change is not None:
            _write_catalog_number(path, cat_change.cleaned)

        if raw_changes:
            _write_raw_frames(path, raw_changes, dry_run=False)

        if ape_changes:
            _write_ape_tags(path, ape_changes, dry_run=False)

        # Strip ID3v1 from any MP3 we touch.
        # If _write_raw_frames ran, it already stripped ID3v1 via v1=0.
        # Otherwise the easy-tag save left ID3v1 intact — strip it now.
        if path.suffix.lower() == ".mp3" and not raw_changes:
            _strip_mp3_id3v1(path)

        return True
    except Exception as exc:
        log.warning("Could not write tags to %s: %s", path, exc)
        return False


# ---------------------------------------------------------------------------
# Core scan logic
# ---------------------------------------------------------------------------

def scan_track(path: Path) -> Optional[TrackResult]:
    """
    Scan one track. Returns:
      - None if the file is corrupt/unreadable (already logged)
      - TrackResult with is_corrupt=False and changes=[] if clean
      - TrackResult with is_corrupt=False and changes=[...] if junk found
    """
    tags = _read_tags(path)
    if tags is None:
        # _read_tags() already logged the warning
        return TrackResult(filepath=str(path), is_corrupt=True)

    result = TrackResult(filepath=str(path))

    # --- Easy-tag fields ---
    for field_name, original in tags.items():
        if not original:
            continue

        if field_name == "title":
            cleaned = _clean_title(original)
        elif field_name == "comment":
            cleaned = _sanitize_comment(original)
        elif field_name in _CLEAR_IF_PURE_URL and _RE_PURE_URL.match(original.strip()):
            cleaned = ""
        else:
            cleaned = sanitize_text(original)

        if cleaned == original:
            continue

        result.changes.append(FieldChange(
            field_name=field_name,
            original=original,
            cleaned=cleaned,
            reason=_reason_for_change(field_name, original, cleaned),
            cleared=bool(not cleaned and original),
            tag_family="ID3v2",
        ))

    # --- Raw ID3 / Vorbis / M4A frames ---
    raw_tags = _read_raw_frames(path)
    for field_name, original in raw_tags.items():
        if not original:
            continue
        cleaned = _clean_raw_field(field_name, original)
        if cleaned == original:
            continue
        result.changes.append(FieldChange(
            field_name=field_name,
            original=original,
            cleaned=cleaned,
            reason=_reason_for_change(field_name, original, cleaned),
            cleared=bool(not cleaned and original),
            tag_family="ID3v2",
        ))

    # --- APEv2 tags ---
    ape_tags = _read_ape_tags(path)
    for field_name, original in ape_tags.items():
        if not original:
            continue
        cleaned = sanitize_text(original)
        if cleaned == original:
            continue
        result.changes.append(FieldChange(
            field_name=field_name,
            original=original,
            cleaned=cleaned,
            reason=_reason_for_change(field_name, original, cleaned),
            cleared=bool(not cleaned and original),
            tag_family="APEv2",
        ))

    return result


def scan_library(paths: List[Path]) -> List[TrackResult]:
    """Scan all given paths. Returns one TrackResult per readable file."""
    results = []
    for path in paths:
        if not path.exists():
            continue
        r = scan_track(path)
        if r is not None:
            results.append(r)
    return results


# ---------------------------------------------------------------------------
# Dry-run output
# ---------------------------------------------------------------------------

def print_dry_run_summary(results: List[TrackResult]) -> None:
    """
    Per-file verbose output: tag family, field, old value, action.
    Followed by aggregate statistics.
    """
    corrupt = [r for r in results if r.is_corrupt]
    dirty   = [r for r in results if r.changed and not r.is_corrupt]
    clean   = [r for r in results if not r.changed and not r.is_corrupt]

    if not dirty and not corrupt:
        print("\nmetadata-clean: No junk found — all tags clean.")
        return

    total_fields = sum(len(r.changes) for r in dirty)
    print(
        f"\n=== metadata-clean DRY RUN ===\n"
        f"  Scanned  : {len(results)}\n"
        f"  Will fix : {len(dirty)} track(s) / {total_fields} field change(s)\n"
        f"  Corrupt  : {len(corrupt)} unreadable (skipped)\n"
        f"  Clean    : {len(clean)}\n"
        f"No files modified. Run without --dry-run to apply.\n"
    )

    if corrupt:
        print("Unreadable files (corrupt / unsupported):")
        for r in corrupt:
            print(f"  [CORRUPT] {Path(r.filepath).name}")
        print()

    # Per-file per-change table
    for r in dirty:
        print(f"  FILE  {Path(r.filepath).name}")
        for c in r.changes:
            tag = f"[{c.tag_family}]"
            if c.cleared:
                print(f"  CLEAR {tag:<8} {c.field_name}")
                print(f"        was : {c.original!r}")
                print(f"        now : (removed)")
            else:
                print(f"  CLEAN {tag:<8} {c.field_name}")
                print(f"        was : {c.original!r}")
                print(f"        now : {c.cleaned!r}")
            print(f"        why : {c.reason}")
        print()

    # Aggregate stats
    field_counts:   Dict[str, int] = {}
    cleared_counts: Dict[str, int] = {}
    reason_counts:  Dict[str, int] = {}
    family_counts:  Dict[str, int] = {}

    for r in dirty:
        for c in r.changes:
            field_counts[c.field_name]    = field_counts.get(c.field_name, 0) + 1
            reason_counts[c.reason]       = reason_counts.get(c.reason, 0) + 1
            family_counts[c.tag_family]   = family_counts.get(c.tag_family, 0) + 1
            if c.cleared:
                cleared_counts[c.field_name] = cleared_counts.get(c.field_name, 0) + 1

    print("─" * 60)
    print("Tag families affected:")
    for fam, count in sorted(family_counts.items()):
        print(f"  {fam:<12} {count:>4} field change(s)")

    print("\nFields that will be affected:")
    for fname, count in sorted(field_counts.items()):
        cleared = cleared_counts.get(fname, 0)
        note    = f"  ({cleared} cleared)" if cleared else ""
        print(f"  {fname:<34} {count:>4} track(s){note}")

    print("\nJunk patterns detected:")
    for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
        print(f"  {reason:<38} {count:>4} occurrence(s)")

    print(f"\nTracks affected : {len(dirty)} of {len(results)} scanned")
    print(f"Fields to clean : {total_fields}")
    print(f"\nRun without --dry-run to apply these {total_fields} change(s).")


# ---------------------------------------------------------------------------
# Apply mode
# ---------------------------------------------------------------------------

def _apply_changes(results: List[TrackResult]) -> Tuple[int, int]:
    """Write cleaned tags back to disk. Returns (files_written, fields_cleaned)."""
    files_written  = 0
    fields_cleaned = 0

    for result in results:
        if result.is_corrupt or not result.changed:
            continue

        path = Path(result.filepath)
        if not path.exists():
            log.warning("File no longer exists, skipping: %s", path)
            continue

        ok = _write_tags(path, result.changes, dry_run=False)

        if ok:
            files_written  += 1
            fields_cleaned += len(result.changes)
            for c in result.changes:
                action = "CLEARED" if c.cleared else "CLEANED"
                log.info(
                    "%s [%s] %s | %s | %r → %r | %s",
                    action, c.tag_family, path.name,
                    c.field_name, c.original, c.cleaned, c.reason,
                )
                log_action(
                    f"META-CLEAN [{c.tag_family}]: {path.name} | {c.field_name} | "
                    f"{c.original!r} → {c.cleaned!r} | {c.reason}"
                )
        else:
            log.warning("Failed to write tags: %s", path)

    return files_written, fields_cleaned


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(paths: List[Path], dry_run: bool = False) -> Tuple[int, int, int]:
    """
    Scan all paths and optionally apply metadata cleanup.

    Returns:
        (tracks_scanned, tracks_changed, fields_cleaned)
    """
    mode = "DRY-RUN" if dry_run else "APPLY"
    log_action(f"METADATA-CLEAN {mode} START: {len(paths)} track(s)")

    results      = scan_library(paths)
    corrupt      = [r for r in results if r.is_corrupt]
    dirty        = [r for r in results if r.changed and not r.is_corrupt]
    total_fields = sum(len(r.changes) for r in dirty)

    log.info(
        "metadata-clean: scanned %d  dirty=%d  corrupt=%d  field_changes=%d",
        len(results), len(dirty), len(corrupt), total_fields,
    )

    if dry_run:
        print_dry_run_summary(results)
        log_action(
            f"METADATA-CLEAN DRY-RUN DONE: "
            f"{len(dirty)} tracks, {total_fields} fields would be cleaned, "
            f"{len(corrupt)} unreadable"
        )
        return len(results), len(dirty), total_fields

    files_written, fields_cleaned = _apply_changes(results)

    if dirty:
        log.info(
            "metadata-clean: wrote %d file(s), cleaned %d field(s), %d unreadable",
            files_written, fields_cleaned, len(corrupt),
        )
    else:
        log.info("metadata-clean: no junk found — all tags clean")

    log_action(
        f"METADATA-CLEAN DONE: "
        f"{len(results)} scanned, {files_written} files written, "
        f"{fields_cleaned} fields cleaned, {len(corrupt)} corrupt skipped"
    )
    return len(results), files_written, fields_cleaned
