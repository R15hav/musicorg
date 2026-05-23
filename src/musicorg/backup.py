"""Snapshot + undo machinery for tag rewrites and renames.

The generated undo script is intentionally THIN: in production, inlining
the snapshot body produced 41 MB scripts that were unreviewable and slow
to load. Both the tag snapshot and the ops list live as sibling JSON
files; the script reads them at runtime via paths baked in as string
literals. This keeps the script under a few KB regardless of library size.

CORE-14: ``SnapshotStore`` is the SQLite-backed successor to the path-keyed
JSON snapshot. It keys on ``fingerprint_sha256`` so snapshots survive renames
and tag rewrites, and excludes large binary frames (APIC, covr, GEOB, PRIV,
USLT) by default. The legacy JSON path remains for the CLI; SnapshotStore is
purely additive until Wave 3 migrates the CLI.
"""

from __future__ import annotations

import datetime
import json
import logging
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from . import tags as tags_mod

logger = logging.getLogger(__name__)


DEFAULT_EXCLUDED_FRAMES: frozenset[str] = frozenset({
    "APIC", "covr", "GEOB", "PRIV", "USLT",
})


def _serialize_frame(value: Any) -> dict:
    """Serialize a single mutagen frame to a JSON-safe ``{kind, values}`` dict.

    Multi-value text frames get joined as a flat list of strings rather than
    nested lists. Mirrors the reference in optimization.md §CORE-14.
    """
    if hasattr(value, "text"):
        vals = value.text
    elif isinstance(value, (list, tuple)):
        vals = list(value)
    else:
        vals = [value]
    if not isinstance(vals, (list, tuple)):
        vals = [vals]
    return {"kind": type(value).__name__, "values": [str(x) for x in vals]}


# ID3 text-frame kinds we know how to reconstruct. Anything else is logged
# and skipped on deserialize rather than raising — partial restores beat hard
# failures when one obscure frame trips us up.
_ID3_TEXT_FRAMES = ("TIT2", "TPE1", "TPE2", "TALB", "TDRC", "TYER", "TRCK", "TCON")


def _deserialize_frame(spec: dict) -> Any:
    """Reconstruct an ID3 frame instance from a ``_serialize_frame`` dict.

    Returns ``None`` and logs a warning for unrecognized frame kinds. Caller
    is responsible for assigning the result onto an ID3 tag container.
    For M4A and FLAC, frames are plain key/value pairs and don't need this.
    """
    kind = spec.get("kind", "")
    values = spec.get("values", []) or [""]
    text = values[0] if values else ""
    if kind in _ID3_TEXT_FRAMES:
        from mutagen import id3 as _id3
        cls = getattr(_id3, kind, None)
        if cls is None:
            logger.warning("backup: unknown ID3 frame class %r on deserialize, skipping", kind)
            return None
        try:
            return cls(encoding=3, text=text)
        except Exception as e:
            logger.warning("backup: failed to construct %s frame: %s", kind, e)
            return None
    logger.warning("backup: unrecognized frame kind %r on deserialize, skipping", kind)
    return None


class SnapshotStore:
    """SQLite-backed per-file frame snapshots, keyed by fingerprint_sha256.

    Survives renames and tag rewrites. Excludes large binary frames (APIC, etc.)
    by default to keep snapshot size proportional to the corpus.
    See _organizer/optimization.md §CORE-14.
    """

    SCHEMA = '''
        CREATE TABLE IF NOT EXISTS snapshots (
            fingerprint_sha256 TEXT NOT NULL,
            path TEXT NOT NULL,
            frames_json TEXT NOT NULL,
            tag_format TEXT NOT NULL,
            taken_at TEXT NOT NULL,
            run_id TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (fingerprint_sha256, taken_at)
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_fingerprint ON snapshots(fingerprint_sha256);
        CREATE INDEX IF NOT EXISTS idx_snapshots_run_id ON snapshots(run_id) WHERE run_id != '';
    '''

    def __init__(
        self,
        db_path: Path,
        excluded_frames: frozenset[str] = DEFAULT_EXCLUDED_FRAMES,
    ) -> None:
        self.db_path = Path(db_path)
        self.excluded_frames = excluded_frames
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False so callers can hand the store off across
        # worker threads; concurrent writes are still serialized by SQLite.
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(self.SCHEMA)
        self.conn.commit()

    @staticmethod
    def _tag_format_for(path: Path) -> str:
        ext = path.suffix.lower()
        if ext == ".mp3":
            return "id3"
        if ext == ".m4a":
            return "mp4"
        if ext == ".flac":
            return "flac"
        return ext.lstrip(".") or "unknown"

    @staticmethod
    def _now_iso() -> str:
        # UTC isoformat with microsecond precision — also satisfies the
        # PRIMARY KEY (fingerprint_sha256, taken_at) uniqueness in practice.
        return datetime.datetime.utcnow().isoformat(timespec="microseconds")

    def record(
        self,
        path: Path,
        fingerprint_sha256: str,
        run_id: str = "",
    ) -> None:
        """Read tags from path, serialize non-excluded frames, write to SQLite.

        The caller pre-computes the fingerprint via
        :func:`musicorg.identity.audio_stream_sha256`. SnapshotStore is a pure
        storage layer and does not invoke ffmpeg itself.
        """
        path = Path(path)
        # Lazy import so SnapshotStore is importable in embedders that skip
        # audio deps (the storage layer alone needs no mutagen).
        try:
            from mutagen import File as MutagenFile
        except ImportError:
            logger.error("backup: mutagen not available, cannot snapshot %s", path)
            return

        frames: dict[str, dict] = {}
        try:
            audio = MutagenFile(path)
            if audio is not None and audio.tags:
                for key, value in audio.tags.items():
                    key_str = str(key)
                    # ID3 keys can come as "APIC:cover", "PRIV:something" etc.
                    base = key_str.split(":", 1)[0]
                    if base in self.excluded_frames:
                        continue
                    try:
                        frames[key_str] = _serialize_frame(value)
                    except Exception as e:
                        logger.warning(
                            "backup: failed to serialize frame %s on %s: %s",
                            key_str, path, e,
                        )
        except Exception as e:
            logger.warning("backup: cannot read tags from %s: %s", path, e)

        tag_format = self._tag_format_for(path)
        taken_at = self._now_iso()
        frames_json = json.dumps(frames, separators=(",", ":"))

        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO snapshots"
                    " (fingerprint_sha256, path, frames_json, tag_format, taken_at, run_id)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (fingerprint_sha256, str(path), frames_json, tag_format, taken_at, run_id),
                )
        except sqlite3.IntegrityError:
            # Same fingerprint + identical microsecond timestamp: extremely
            # unlikely outside of synthetic tests, but be deterministic.
            logger.warning(
                "backup: duplicate snapshot key for fingerprint=%s taken_at=%s — skipped",
                fingerprint_sha256, taken_at,
            )
        logger.info(
            "backup: recorded snapshot fingerprint=%s path=%s frames=%d run_id=%s",
            fingerprint_sha256, path, len(frames), run_id or "-",
        )

    def latest(self, fingerprint_sha256: str) -> dict | None:
        """Return the most-recent snapshot for the given fingerprint, or None."""
        cur = self.conn.execute(
            "SELECT fingerprint_sha256, path, frames_json, tag_format, taken_at, run_id"
            " FROM snapshots WHERE fingerprint_sha256 = ?"
            " ORDER BY taken_at DESC LIMIT 1",
            (fingerprint_sha256,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row)

    def history(self, fingerprint_sha256: str) -> list[dict]:
        """Return all snapshots for the given fingerprint, newest first."""
        cur = self.conn.execute(
            "SELECT fingerprint_sha256, path, frames_json, tag_format, taken_at, run_id"
            " FROM snapshots WHERE fingerprint_sha256 = ?"
            " ORDER BY taken_at DESC",
            (fingerprint_sha256,),
        )
        return [dict(row) for row in cur.fetchall()]

    def restore(self, fingerprint_sha256: str, target_path: Path) -> bool:
        """Restore the most-recent snapshot's tags onto ``target_path``.

        Returns ``True`` on success, ``False`` if no snapshot exists for
        that fingerprint or if mutagen cannot be loaded.
        """
        snap = self.latest(fingerprint_sha256)
        if snap is None:
            logger.warning(
                "backup: no snapshot for fingerprint=%s — nothing to restore",
                fingerprint_sha256,
            )
            return False
        target = Path(target_path)
        try:
            frames = json.loads(snap["frames_json"])
        except (TypeError, ValueError) as e:
            logger.error("backup: snapshot frames_json unreadable for %s: %s", fingerprint_sha256, e)
            return False
        tag_format = snap.get("tag_format") or self._tag_format_for(target)
        try:
            if tag_format == "id3":
                self._restore_id3(target, frames)
            elif tag_format == "mp4":
                self._restore_mp4(target, frames)
            elif tag_format == "flac":
                self._restore_flac(target, frames)
            else:
                logger.warning(
                    "backup: unknown tag_format %r for %s — cannot restore",
                    tag_format, target,
                )
                return False
        except Exception as e:
            logger.error("backup: tag restore failed for %s: %s: %s", target, type(e).__name__, e)
            return False
        return True

    @staticmethod
    def _restore_id3(path: Path, frames: dict[str, dict]) -> None:
        from mutagen.id3 import ID3, ID3NoHeaderError
        try:
            audio = ID3(path)
        except ID3NoHeaderError:
            audio = ID3()
        # Wipe the kinds we're about to restore so we don't double up.
        for kind in {spec.get("kind", "") for spec in frames.values()}:
            if kind:
                audio.delall(kind)
        for _key, spec in frames.items():
            frame = _deserialize_frame(spec)
            if frame is None:
                continue
            try:
                audio.add(frame)
            except Exception as e:
                logger.warning("backup: id3 add failed for %s: %s", spec.get("kind"), e)
        audio.save(path, v2_version=4)

    @staticmethod
    def _restore_mp4(path: Path, frames: dict[str, dict]) -> None:
        from mutagen.mp4 import MP4
        audio = MP4(path)
        for key in list(frames.keys()):
            if key in audio:
                del audio[key]
        for key, spec in frames.items():
            values = spec.get("values", [])
            try:
                if key == "trkn":
                    # trkn round-trips as a list of "(track, total)" string tuples;
                    # be defensive about parsing back into ints.
                    raw = values[0] if values else ""
                    parts = str(raw).strip("()").split(",")
                    track_num = int(parts[0].strip()) if parts and parts[0].strip() else 0
                    total = int(parts[1].strip()) if len(parts) > 1 and parts[1].strip() else 0
                    audio["trkn"] = [(track_num, total)]
                else:
                    audio[key] = list(values)
            except Exception as e:
                logger.warning("backup: mp4 set failed for %s: %s", key, e)
        audio.save()

    @staticmethod
    def _restore_flac(path: Path, frames: dict[str, dict]) -> None:
        from mutagen.flac import FLAC
        audio = FLAC(path)
        audio.clear()
        for key, spec in frames.items():
            values = spec.get("values", [])
            try:
                audio[key] = [str(x) for x in values] if values else [""]
            except Exception as e:
                logger.warning("backup: flac set failed for %s: %s", key, e)
        audio.save()

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error as e:
            logger.warning("backup: SnapshotStore close raised %s", e)

    def __enter__(self) -> SnapshotStore:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def snapshot_tags(paths: list[Path], snapshot_path: Path) -> Path:
    """Collect ``tags.snapshot()`` for each path, write a single path-keyed JSON.

    The resulting file maps absolute path strings to per-file snapshot dicts
    (``original_filename``, ``ext``, ``tags``). Any path that cannot be read
    is recorded with an empty ``tags`` dict so the undo step can still attempt
    the rename portion even if tag restore fails.
    """
    snapshot_path = Path(snapshot_path)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    out: dict[str, dict] = {}
    for p in paths:
        p = Path(p)
        try:
            out[str(p)] = tags_mod.snapshot(p)
        except Exception:
            out[str(p)] = {"original_filename": p.name, "ext": p.suffix.lower(), "tags": {}}
    snapshot_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return snapshot_path


# The body of the generated script. The two sentinel placeholders are
# replaced with repr() of absolute path strings so the resulting script is
# fully self-contained and safely shell-quoted.
_UNDO_TEMPLATE = '''#!/usr/bin/env python3
"""Auto-generated undo script for musicorg canonical-apply.

Reverses every rename + tag rewrite recorded in the apply step. Reads the
snapshot and ops list from sibling JSON files (paths baked in below) so the
script itself stays small.

Usage:
    python3 __SCRIPT_NAME__
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

SNAPSHOT_PATH = Path(__SNAPSHOT_PATH__)
OPS_PATH      = Path(__OPS_PATH__)


def _load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ! cannot read {p}: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _restore_mp3(path: Path, tag_map: dict) -> None:
    from mutagen.id3 import ID3, ID3NoHeaderError
    from mutagen.id3 import TIT2, TPE1, TPE2, TALB, TDRC, TYER, TRCK, TCON
    try:
        audio = ID3(path)
    except ID3NoHeaderError:
        audio = ID3()
    for k in ("TIT2", "TPE1", "TPE2", "TALB", "TDRC", "TYER", "TRCK", "TCON"):
        audio.delall(k)
    frame_map = {
        "TIT2": TIT2, "TPE1": TPE1, "TPE2": TPE2, "TALB": TALB,
        "TDRC": TDRC, "TYER": TYER, "TRCK": TRCK, "TCON": TCON,
    }
    for k, v in tag_map.items():
        if isinstance(v, list):
            v = v[0] if v else ""
        v = str(v)
        cls = frame_map.get(k)
        if cls is None:
            continue
        try:
            audio.add(cls(encoding=3, text=v))
        except Exception:
            pass
    audio.save(path, v2_version=4)


def _restore_mp4(path: Path, tag_map: dict) -> None:
    from mutagen.mp4 import MP4
    audio = MP4(path)
    for k in ("\\xa9nam", "\\xa9ART", "aART", "\\xa9alb", "\\xa9day", "\\xa9gen", "trkn"):
        if k in audio:
            del audio[k]
    for k, v in tag_map.items():
        try:
            if k == "trkn":
                if isinstance(v, list) and v:
                    first = v[0]
                    if isinstance(first, (list, tuple)) and len(first) >= 1:
                        audio["trkn"] = [(int(first[0]), int(first[1]) if len(first) > 1 else 0)]
                    else:
                        try:
                            audio["trkn"] = [(int(str(first).split("/")[0]), 0)]
                        except Exception:
                            pass
                continue
            audio[k] = v if isinstance(v, list) else [v]
        except Exception:
            pass
    audio.save()


def _restore_flac(path: Path, tag_map: dict) -> None:
    from mutagen.flac import FLAC
    audio = FLAC(path)
    audio.clear()
    for k, v in tag_map.items():
        try:
            if isinstance(v, list):
                audio[k] = [str(x) for x in v]
            else:
                audio[k] = str(v)
        except Exception:
            pass
    audio.save()


def restore_one(op: dict, snapshot: dict) -> bool:
    cur = Path(op["new_path"])
    orig = Path(op["original_path"])
    if cur.exists() and cur != orig:
        try:
            orig.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(cur), str(orig))
            path = orig
        except Exception as e:
            print(f"  ! rename back failed {cur} -> {orig}: {type(e).__name__}: {e}", file=sys.stderr)
            return False
    elif orig.exists():
        path = orig
    elif cur.exists():
        path = cur
    else:
        print(f"  ! missing both: {cur} / {orig}", file=sys.stderr)
        return False
    rec = snapshot.get(str(orig)) or {}
    tag_map = rec.get("tags") or {}
    ext = path.suffix.lower()
    try:
        if ext == ".mp3":
            _restore_mp3(path, tag_map)
        elif ext == ".m4a":
            _restore_mp4(path, tag_map)
        elif ext == ".flac":
            _restore_flac(path, tag_map)
    except Exception as e:
        print(f"  ! tag restore failed {path}: {type(e).__name__}: {e}", file=sys.stderr)
        return False
    return True


def main() -> int:
    snapshot = _load_json(SNAPSHOT_PATH)
    ops = _load_json(OPS_PATH)
    if snapshot is None or ops is None:
        print("snapshot or ops missing; cannot proceed", file=sys.stderr)
        return 2
    print(f"Restoring {len(ops)} files from {SNAPSHOT_PATH.name}")
    n_ok = n_err = 0
    for op in ops:
        if restore_one(op, snapshot):
            n_ok += 1
        else:
            n_err += 1
    print(f"Restored: {n_ok}  failed: {n_err}")
    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
'''


def _ops_path_for(undo_script_path: Path) -> Path:
    """Sibling ``<undo>.ops.json`` next to the generated script."""
    return undo_script_path.with_suffix(undo_script_path.suffix + ".ops.json")


def generate_undo_script(
    snapshot_path: Path,
    ops: list[dict],
    undo_script_path: Path,
) -> Path:
    """Write a thin Python undo script + sibling ops JSON file.

    The script does not inline the snapshot or ops list. Both are read from
    disk at runtime; only their absolute paths are baked into the script.
    """
    snapshot_path = Path(snapshot_path)
    undo_script_path = Path(undo_script_path)
    undo_script_path.parent.mkdir(parents=True, exist_ok=True)
    ops_path = _ops_path_for(undo_script_path)
    ops_path.write_text(json.dumps(ops, indent=2), encoding="utf-8")
    code = (
        _UNDO_TEMPLATE
        .replace("__SCRIPT_NAME__", undo_script_path.name)
        .replace("__SNAPSHOT_PATH__", repr(str(snapshot_path)))
        .replace("__OPS_PATH__", repr(str(ops_path)))
    )
    undo_script_path.write_text(code, encoding="utf-8")
    undo_script_path.chmod(0o755)
    return undo_script_path


def list_snapshots(state_dir: Path) -> list[dict]:
    """Return metadata for every legacy ``tag_snapshot_*.json`` in ``<state>/backups``.

    Legacy path-keyed JSON snapshots only. For fingerprint-keyed snapshots
    use :class:`SnapshotStore`. Each entry: ``{path, ts, size, ops_count}``.
    ``ts`` is parsed from the filename tail when possible, else file mtime.
    ``ops_count`` is from the sibling undo ops file when present, else 0.
    """
    backups = Path(state_dir) / "backups"
    if not backups.exists():
        return []
    out: list[dict] = []
    for p in sorted(backups.glob("tag_snapshot_*.json")):
        try:
            size = p.stat().st_size
        except OSError:
            continue
        ts = _parse_ts_from_name(p.name)
        if ts is None:
            try:
                ts = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d_%H%M%S")
            except OSError:
                ts = ""
        ops_count = _count_ops_for_snapshot(p)
        out.append({
            "path": str(p),
            "ts": ts,
            "size": size,
            "ops_count": ops_count,
        })
    return out


def _parse_ts_from_name(name: str) -> str | None:
    stem = Path(name).stem
    parts = stem.rsplit("_", 2)
    if len(parts) >= 3:
        cand = "_".join(parts[-2:])
        try:
            datetime.datetime.strptime(cand, "%Y-%m-%d_%H%M%S")
            return cand
        except ValueError:
            return None
    return None


def _count_ops_for_snapshot(snapshot_path: Path) -> int:
    state_dir = snapshot_path.parent.parent
    ts = _parse_ts_from_name(snapshot_path.name)
    if not ts:
        return 0
    for candidate in state_dir.glob(f"undo_*{ts}*.py.ops.json"):
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return len(data)
        except Exception:
            continue
    return 0


def restore_from_snapshot(snapshot_path: Path) -> dict:
    """Python-side runner. Loads snapshot + sibling ops JSON, applies restore.

    Used by ``canonical-undo --latest`` so users do not have to shell out to
    the generated script. Returns ``{restored, failed, missing_ops}`` counts.
    """
    snapshot_path = Path(snapshot_path)
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except Exception:
        return {"restored": 0, "failed": 0, "missing_ops": 1}
    ops_path = _find_ops_for_snapshot(snapshot_path)
    if ops_path is None:
        return {"restored": 0, "failed": 0, "missing_ops": 1}
    try:
        ops = json.loads(ops_path.read_text(encoding="utf-8"))
    except Exception:
        return {"restored": 0, "failed": 0, "missing_ops": 1}
    n_ok = n_err = 0
    for op in ops:
        if _restore_one_inproc(op, snapshot):
            n_ok += 1
        else:
            n_err += 1
    return {"restored": n_ok, "failed": n_err, "missing_ops": 0}


def _find_ops_for_snapshot(snapshot_path: Path) -> Path | None:
    state_dir = snapshot_path.parent.parent
    ts = _parse_ts_from_name(snapshot_path.name)
    if not ts:
        return None
    for candidate in state_dir.glob(f"undo_*{ts}*.py.ops.json"):
        return candidate
    return None


def _restore_one_inproc(op: dict, snapshot: dict) -> bool:
    """Mirror of the generated script's restore_one, used by restore_from_snapshot."""
    cur = Path(op["new_path"])
    orig = Path(op["original_path"])
    if cur.exists() and cur != orig:
        try:
            orig.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(cur), str(orig))
            path = orig
        except Exception:
            return False
    elif orig.exists():
        path = orig
    elif cur.exists():
        path = cur
    else:
        return False
    rec = snapshot.get(str(orig)) or {}
    tag_map = rec.get("tags") or {}
    ext = path.suffix.lower()
    try:
        if ext == ".mp3":
            _inproc_restore_mp3(path, tag_map)
        elif ext == ".m4a":
            _inproc_restore_mp4(path, tag_map)
        elif ext == ".flac":
            _inproc_restore_flac(path, tag_map)
    except Exception:
        return False
    return True


def _inproc_restore_mp3(path: Path, tag_map: dict[str, Any]) -> None:
    from mutagen.id3 import ID3, ID3NoHeaderError
    from mutagen.id3 import TIT2, TPE1, TPE2, TALB, TDRC, TYER, TRCK, TCON
    try:
        audio = ID3(path)
    except ID3NoHeaderError:
        audio = ID3()
    for k in ("TIT2", "TPE1", "TPE2", "TALB", "TDRC", "TYER", "TRCK", "TCON"):
        audio.delall(k)
    frame_map = {
        "TIT2": TIT2, "TPE1": TPE1, "TPE2": TPE2, "TALB": TALB,
        "TDRC": TDRC, "TYER": TYER, "TRCK": TRCK, "TCON": TCON,
    }
    for k, v in tag_map.items():
        if isinstance(v, list):
            v = v[0] if v else ""
        v = str(v)
        cls = frame_map.get(k)
        if cls is None:
            continue
        try:
            audio.add(cls(encoding=3, text=v))
        except Exception:
            pass
    audio.save(path, v2_version=4)


def _inproc_restore_mp4(path: Path, tag_map: dict[str, Any]) -> None:
    from mutagen.mp4 import MP4
    audio = MP4(path)
    for k in ("\xa9nam", "\xa9ART", "aART", "\xa9alb", "\xa9day", "\xa9gen", "trkn"):
        if k in audio:
            del audio[k]
    for k, v in tag_map.items():
        try:
            if k == "trkn":
                if isinstance(v, list) and v:
                    first = v[0]
                    if isinstance(first, (list, tuple)) and len(first) >= 1:
                        audio["trkn"] = [(int(first[0]), int(first[1]) if len(first) > 1 else 0)]
                    else:
                        try:
                            audio["trkn"] = [(int(str(first).split("/")[0]), 0)]
                        except Exception:
                            pass
                continue
            audio[k] = v if isinstance(v, list) else [v]
        except Exception:
            pass
    audio.save()


def _inproc_restore_flac(path: Path, tag_map: dict[str, Any]) -> None:
    from mutagen.flac import FLAC
    audio = FLAC(path)
    audio.clear()
    for k, v in tag_map.items():
        try:
            if isinstance(v, list):
                audio[k] = [str(x) for x in v]
            else:
                audio[k] = str(v)
        except Exception:
            pass
    audio.save()
