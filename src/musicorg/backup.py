"""Snapshot + undo machinery for tag rewrites and renames.

The generated undo script is intentionally THIN: in production, inlining
the snapshot body produced 41 MB scripts that were unreviewable and slow
to load. Both the tag snapshot and the ops list live as sibling JSON
files; the script reads them at runtime via paths baked in as string
literals. This keeps the script under a few KB regardless of library size.
"""

from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path
from typing import Any

from . import tags as tags_mod


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
    """Return metadata for every ``tag_snapshot_*.json`` in ``<state>/backups``.

    Each entry: ``{path, ts, size, ops_count}``. ``ts`` is parsed from the
    filename tail (``..._<YYYY-MM-DD_HHMMSS>.json``) when possible, else file
    mtime. ``ops_count`` is looked up from the sibling undo's ops file if
    present, else 0.
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
