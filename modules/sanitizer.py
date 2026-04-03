"""
Tag sanitizer — removes URL/promo junk from metadata fields before tagging.

Design principles:
  - sanitize_text() is a pure function with no I/O (unit testable in isolation)
  - sanitize_metadata() operates on a plain dict (also unit testable)
  - run() handles file I/O and DB updates for the pipeline
  - Conservative: only removes things that are clearly garbage
  - Fast path: returns input unchanged if SANITIZE_TAGS=False or nothing matched
  - Never crashes on missing/None fields

Fields sanitized: title, artist, album, genre, comment
Fields never touched: BPM, key, duration, bitrate, filepath

Pipeline position: after organizer, before analyzer.
"""
import logging
import re
from typing import Dict, List, Optional, Tuple

import config
import db
from modules.junk_patterns import load_junk_patterns
from modules.textlog import log_action

log = logging.getLogger(__name__)

# Fields processed by the sanitizer (maps our key → mutagen easy tag name)
# organization = TPUB (publisher/label) in ID3; also cleaned to catch URL watermarks
_SANITIZE_FIELDS = ["title", "artist", "album", "genre", "comment", "organization"]

# ---------------------------------------------------------------------------
# Pattern library
# Each entry is either a compiled regex or (regex, replacement) tuple.
# Order matters — bracketed junk is removed before general URL removal.
# ---------------------------------------------------------------------------

# URL-like strings where :// has been replaced with underscores — e.g.
# https___electronicfresh.com, http__djpool.net
# Reason: filenames cannot contain : or / on most filesystems, so DJ pools
# sometimes embed their URL watermark with underscores replacing those chars.
# Must run BEFORE _RE_URL_PROTOCOL so the whole token is consumed at once.
_RE_URL_UNDERSCORE = re.compile(
    r'\bhttps?_+[\w][\S]*',
    re.IGNORECASE,
)

# Concatenated URLs — protocol run directly into domain with no separator at all
# e.g. httpsheydj.pro#, HTTPSTATION, httpdjpool
# Any token starting with http/https at a word boundary is URL junk.
_RE_URL_CONCATENATED = re.compile(
    r'\bhttps?\S+',
    re.IGNORECASE,
)

# Full URLs with protocol — e.g. https://fordjonly.com/track
# Reason: download sites embed their URL as a "watermark" in tags
_RE_URL_PROTOCOL = re.compile(
    r'https?://\S+|ftp://\S+',
    re.IGNORECASE,
)

# www. URLs without protocol — e.g. www.djcity.com
# Reason: some taggers add www. links without the https:// prefix
_RE_URL_WWW = re.compile(
    r'\bwww\.\S+',
    re.IGNORECASE,
)

# Bracketed domains/URLs — e.g. [fordjonly.com] or (djcity.com)
# Reason: promo pools often wrap their domain in brackets in comment/title
# This runs BEFORE plain domain removal so the brackets go too
_RE_BRACKETED_JUNK = re.compile(
    r'[\[\(]\s*'
    r'(?:https?://\S+?|www\.\S+?|[a-z0-9][\w\-]*\.[a-z]{2,6}(?:/\S*?)?)'
    r'\s*[\]\)]',
    re.IGNORECASE,
)

# Plain domain names — e.g. fordjonly.com, beatsource.net
# TLD list is loaded from config/junk_patterns.json at module init time.
# Conservative: requires word boundary on both sides, known TLDs only
# Does NOT match things like "something.org" in the middle of a word
def _build_plain_domain_re() -> re.Pattern:
    tlds = load_junk_patterns().domain_tlds
    tld_group = "|".join(re.escape(t) for t in tlds) if tlds else "com|net|org"
    return re.compile(
        r'(?<![/\w])'
        r'[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?'
        r'\.'
        r'(?:' + tld_group + r')'
        r'(?:/[^\s,;|]*)?'
        r'(?![.\w])',
        re.IGNORECASE,
    )

_RE_PLAIN_DOMAIN = _build_plain_domain_re()

# Trademark, copyright, and currency symbols — embedded by DJ pools or YouTube
# auto-tagging; meaningless and sometimes folder-name-unsafe.
# ™ ® © ℗ $ € £ ¥ ¢
_RE_SYMBOLS = re.compile(r'[™®©℗$€£¥¢]', re.UNICODE)

# Promo / source phrase patterns — loaded from config/junk_patterns.json.
# Order matters (e.g. "buy on beatport" must precede standalone "beatport").
# The JSON array preserves insertion order, matching the original hardcoded list.
_PROMO_PHRASES: List[Tuple[re.Pattern, str]] = load_junk_patterns().phrase_patterns

# ---------------------------------------------------------------------------
# Artifact cleanup patterns (run after content removal)
# ---------------------------------------------------------------------------

# Empty bracket pairs left behind after content removal: [] () [  ]
_RE_EMPTY_BRACKETS = re.compile(r'[\[\(]\s*[\]\)]')

# Two or more consecutive separator characters (dash, pipe) possibly with spaces
# e.g. " - - " or " | - " → " - "
_RE_MULTI_SEPARATOR = re.compile(r'(?:\s*[-|]\s*){2,}')

# Multiple consecutive spaces
_RE_MULTI_SPACE = re.compile(r'  +')

# Leading or trailing dashes, pipes, commas, colons left after content removal
_RE_EDGE_JUNK = re.compile(r'^[\s\-|,;:]+|[\s\-|,;:]+$')


# ---------------------------------------------------------------------------
# Core pure functions (no I/O — unit testable)
# ---------------------------------------------------------------------------

def sanitize_text(text: str) -> str:
    """
    Remove URL/promo junk from a single text string.

    Pure function — no I/O, no side effects.
    Returns the cleaned string. Returns the original string unchanged if
    nothing matched (fast path via early equality check at the end).

    >>> sanitize_text("Track Title [fordjonly.com]")
    'Track Title'
    >>> sanitize_text("Artist - Title (Original Mix)")
    'Artist - Title (Original Mix)'
    >>> sanitize_text("Title www.djcity.com For DJ Only")
    'Title'
    """
    if not text:
        return text

    result = text

    # Step 1 — Underscore-encoded URLs (https___domain.com style)
    result = _RE_URL_UNDERSCORE.sub('', result)

    # Step 2 — Bracketed URLs/domains (remove brackets + content together)
    #          Must run before the bare-URL steps so brackets are consumed as a unit.
    result = _RE_BRACKETED_JUNK.sub('', result)

    # Step 3 — Full URLs with protocol (https://, ftp://)
    result = _RE_URL_PROTOCOL.sub('', result)

    # Step 4 — www. URLs
    result = _RE_URL_WWW.sub('', result)

    # Step 5 — Plain domain names
    result = _RE_PLAIN_DOMAIN.sub('', result)

    # Step 6 — Concatenated URLs with no separator (httpsheydj.pro#, HTTPSTATION)
    #          Runs last so bracketed and protocol forms are already gone; this
    #          catches only the remaining bare https?… tokens.
    result = _RE_URL_CONCATENATED.sub('', result)

    # Step 7 — Promo/source phrases
    for pattern, replacement in _PROMO_PHRASES:
        result = pattern.sub(replacement, result)

    # Step 8 — Trademark/copyright/currency symbols (™ ® © $ etc.)
    result = _RE_SYMBOLS.sub('', result)

    # Step 9 — Artifact cleanup
    result = _RE_EMPTY_BRACKETS.sub('', result)    # remove empty ()  []
    result = _RE_MULTI_SEPARATOR.sub(' - ', result) # collapse -- or |-
    result = _RE_MULTI_SPACE.sub(' ', result)       # collapse spaces
    result = _RE_EDGE_JUNK.sub('', result)          # strip leading/trailing junk

    return result.strip()


def sanitize_metadata(fields: Dict[str, Optional[str]]) -> Tuple[Dict[str, str], List[str]]:
    """
    Sanitize a dict of tag fields.

    Args:
        fields: dict with keys from _SANITIZE_FIELDS, values may be None.

    Returns:
        (sanitized_dict, changes) where changes is a list of human-readable
        descriptions of what changed (empty list = no changes).

    Pure function — no I/O, no side effects.

    >>> result, changes = sanitize_metadata({"title": "Track [djcity.com]", "artist": "Artist"})
    >>> result["title"]
    'Track'
    >>> len(changes)
    1
    """
    sanitized: Dict[str, str] = {}
    changes:   List[str]      = []

    for field in _SANITIZE_FIELDS:
        original = fields.get(field) or ''
        if not original:
            sanitized[field] = original
            continue

        cleaned = sanitize_text(original)
        sanitized[field] = cleaned

        if cleaned != original:
            changes.append(f"{field}: {original!r} → {cleaned!r}")

    return sanitized, changes


# ---------------------------------------------------------------------------
# Pipeline integration (file I/O)
# ---------------------------------------------------------------------------

def _read_tags(path) -> Dict[str, str]:
    """Read sanitizable tag fields from a file using mutagen easy tags."""
    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=True)
        if audio is None:
            return {}
        get = lambda key: (audio.get(key) or [''])[0]
        return {
            'title':        get('title'),
            'artist':       get('artist'),
            'album':        get('album'),
            'genre':        get('genre'),
            'comment':      get('comment'),
            'organization': get('organization'),   # TPUB / label
        }
    except Exception as exc:
        log.debug("Could not read tags from %s for sanitization: %s", path, exc)
        return {}


def _write_tags(path, fields: Dict[str, str], dry_run: bool) -> bool:
    """Write sanitized fields back to the file using mutagen easy tags."""
    if dry_run:
        return True
    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=True)
        if audio is None:
            return False
        for field, value in fields.items():
            try:
                if value:
                    audio[field] = [value]
                elif field == 'organization':
                    # Explicit empty means the label was junk — delete the tag
                    try:
                        del audio[field]
                    except KeyError:
                        pass
            except Exception:
                pass  # some formats don't support all easy fields
        audio.save()
        return True
    except Exception as exc:
        log.warning("Could not write sanitized tags to %s: %s", path, exc)
        return False


def run(files, run_id: int, dry_run: bool = False):
    """
    Sanitize metadata on all files. Updates DB and file tags where changed.
    Returns the file list unchanged (sanitization never rejects a file).
    """
    if not getattr(config, 'SANITIZE_TAGS', True):
        log.info("Sanitizer: disabled via SANITIZE_TAGS=False")
        return files

    changed_count = 0

    for path in files:
        if not path.exists():
            continue

        current_tags = _read_tags(path)
        if not current_tags:
            continue

        sanitized, changes = sanitize_metadata(current_tags)

        if not changes:
            # Fast path — nothing changed
            continue

        changed_count += 1
        for change in changes:
            log.info("SANITIZE %s — %s", path.name, change)
            log_action(f"CLEAN: {change} [{path.name}]")

        if not dry_run:
            # Write back changed fields to the file.
            # organization (label) is included even when cleaned to empty so that
            # junk watermark labels (e.g. "TraxCrate.com") are cleared from the tag.
            write_fields = {k: v for k, v in sanitized.items() if v}
            org_orig    = current_tags.get('organization', '')
            org_cleaned = sanitized.get('organization', '')
            if org_orig and not org_cleaned:
                # Label was fully junk — explicitly clear the tag
                write_fields['organization'] = ''
            _write_tags(path, write_fields, dry_run)

            # Update DB fields we track
            db_update = {}
            if sanitized.get('artist') != current_tags.get('artist'):
                db_update['artist'] = sanitized['artist']
            if sanitized.get('title') != current_tags.get('title'):
                db_update['title'] = sanitized['title']
            if sanitized.get('genre') != current_tags.get('genre'):
                db_update['genre'] = sanitized['genre']
            if db_update:
                db.upsert_track(str(path), **db_update)

            # Update track history with the post-sanitization snapshot
            db.update_track_history_cleaned(str(path), sanitized)

    if changed_count:
        log.info("Sanitizer: cleaned tags on %d file(s)", changed_count)
    else:
        log.info("Sanitizer: no junk found — all tags clean")

    return files
