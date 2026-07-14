"""
Duplicate detection — finds byte-identical files across inbox + library.

Strategy:
    1. rmlint finds byte-identical files (fast, reliable).
    2. Results are written to the DB and a quarantine report.
    3. Duplicates are MOVED to /music/duplicates/ (never deleted automatically).
    4. The pipeline caller decides whether to prompt for manual review.

Dry-run: detects and reports but does not move anything.
"""
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Tuple

import config
import db

log = logging.getLogger(__name__)

# rmlint outputs a shell script; we parse it to extract (original, duplicate) pairs
_RMLINT_ORIGINAL_RE = re.compile(r'^# Keeping: (.+)$', re.MULTILINE)
_RMLINT_REMOVE_RE   = re.compile(r'^remove_cmd "(.+)"', re.MULTILINE)

# Simpler: rmlint --output=csv gives us CSV — easier to parse
# Format: type,size,name,path,inode,mtime,is_original,prehash
_CSV_HEADER = "type,size,name,path,inode,mtime,is_original,prehash"


def _run_rmlint(scan_dirs: List[Path], output_dir: Path) -> Path:
    """Run rmlint over scan_dirs, write output to output_dir. Returns CSV path."""
    csv_path = output_dir / "rmlint.csv"
    sh_path  = output_dir / "rmlint.sh"   # must be a real path, not /dev/null
    cmd = [
        config.RMLINT_BIN,
        "--types=duplicates",       # only duplicates, not other lint
        "--hidden",                  # scan hidden files too
        "--no-followlinks",
        f"--output=csv:{csv_path}",
        f"--output=sh:{sh_path}",
        "--",
    ] + [str(d) for d in scan_dirs if d.exists()]

    log.debug("rmlint cmd: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode not in (0, 1):  # rmlint returns 1 when dupes found
        raise RuntimeError(f"rmlint failed (rc={result.returncode}): {result.stderr[:500]}")

    return csv_path


def _parse_csv(csv_path: Path) -> List[Tuple[str, str]]:
    """
    Parse rmlint CSV and return list of (original_path, duplicate_path) tuples.
    rmlint marks the file to keep with is_original=1.
    """
    if not csv_path.exists():
        return []

    groups: dict = {}   # prehash -> list of (path, is_original)
    with open(csv_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line == _CSV_HEADER:
                continue
            parts = line.split(",")
            if len(parts) < 8:
                continue
            rtype, size, name, path, inode, mtime, is_orig, prehash = (
                parts[0], parts[1], parts[2], parts[3], parts[4],
                parts[5], parts[6], parts[7],
            )
            if rtype != "duplicate_file":
                continue
            groups.setdefault(prehash, []).append((path, is_orig == "1"))

    pairs: List[Tuple[str, str]] = []
    for prehash, entries in groups.items():
        originals  = [p for p, is_o in entries if is_o]
        duplicates = [p for p, is_o in entries if not is_o]
        if not originals:
            # rmlint picks the original — fall back to first entry
            originals  = [entries[0][0]]
            duplicates = [p for p, _ in entries[1:]]
        orig = originals[0]
        for dup in duplicates:
            pairs.append((orig, dup))
    return pairs


def _quarantine(dup_path: Path, dry_run: bool) -> None:
    dest = config.DUPLICATES / dup_path.name
    log.info("DUPLICATE → quarantine: %s", dup_path.name)
    if not dry_run:
        config.DUPLICATES.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = config.DUPLICATES / f"{dup_path.stem}__{dup_path.stat().st_ino}{dup_path.suffix}"
        shutil.move(str(dup_path), str(dest))


def run(files: List[Path], run_id: int, dry_run: bool = False) -> List[Path]:
    """
    Find duplicates across inbox files AND existing library.
    Quarantine duplicates, return list of files that are not duplicates.
    """
    # Fast-path: can't have duplicates with a single file
    if len(files) <= 1:
        log.info("Dedupe: single file — skipping rmlint")
        return files

    scan_dirs = [config.INBOX, config.SORTED]

    with tempfile.TemporaryDirectory(prefix="djtoolkit_rmlint_") as tmpdir:
        try:
            csv_path = _run_rmlint(scan_dirs, Path(tmpdir))
            pairs    = _parse_csv(csv_path)
        except FileNotFoundError:
            log.error(
                "rmlint not found at '%s'. Install: sudo apt install rmlint",
                config.RMLINT_BIN,
            )
            return files
        except RuntimeError as exc:
            log.error("rmlint error: %s", exc)
            return files

    if not pairs:
        log.info("Dedupe: no duplicates found")
        return files

    quarantined: set = set()
    for original, duplicate in pairs:
        log.info("DUPE: %s  (original: %s)", Path(duplicate).name, Path(original).name)
        db.log_duplicate(run_id, original, duplicate, reason="byte-identical")
        db.upsert_track(duplicate, status="duplicate", error_msg=f"duplicate of {original}")
        dup_path = Path(duplicate)
        if dup_path.exists():
            _quarantine(dup_path, dry_run)
            quarantined.add(str(dup_path))

    surviving = [f for f in files if str(f) not in quarantined]
    log.info("Dedupe: %d duplicates quarantined, %d files remain", len(quarantined), len(surviving))
    return surviving
