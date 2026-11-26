"""Library-local alpha PNG review guides; not timeline or export media."""
from dataclasses import dataclass
from contextlib import closing
import hashlib
from io import BytesIO
from pathlib import Path
import sqlite3
from PIL import Image

MAX_BYTES = 4 * 1024 * 1024
MAX_PIXELS = 8_388_608  # includes 2160x3840 when within byte budget
MAX_EDGE = 4096


@dataclass(frozen=True)
class Guide:
    png: bytes
    digest: str
    width: int
    height: int


def encode_rgba(image):
    blank = Image.new('RGBA', image.size)
    blank.paste(image)
    buf = BytesIO()
    blank.save(buf, 'PNG')
    return buf.getvalue()


def ingest(raw, canvas):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("guide PNG byte limit")
    try:
        with Image.open(BytesIO(raw), formats=['PNG']) as metadata:
            w, h = metadata.size
            if (not 2 <= w <= MAX_EDGE or not 2 <= h <= MAX_EDGE
                    or w*h > MAX_PIXELS or metadata.n_frames != 1):
                raise ValueError("guide must be a bounded single PNG")
            cw, ch = canvas
            if w*ch != h*cw:
                raise ValueError("guide must have the canvas aspect")
            metadata.verify()
        with Image.open(BytesIO(raw), formats=['PNG']) as image:
            rgba = image.convert('RGBA')
            minimum, maximum = rgba.getchannel('A').getextrema()
            if minimum == 255 or maximum == 0:
                raise ValueError("guide needs visible marks and transparent areas")
            normalized = encode_rgba(rgba)
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("invalid PNG guide") from exc
    if len(normalized) > MAX_BYTES:
        raise ValueError("normalized guide PNG byte limit")
    return Guide(normalized, hashlib.sha256(normalized).hexdigest(), w, h)


def derivative(clean_png, guide):
    if guide is None:
        return clean_png
    with Image.open(BytesIO(clean_png), formats=['PNG']) as clean:
        if not (2 <= clean.width <= 1920 and 2 <= clean.height <= 3840):
            raise ValueError("preview dimensions out of range")
        with Image.open(BytesIO(guide.png), formats=['PNG']) as overlay:
            # Source geometry was checked against the actual sequence. Only the
            # already-rendered preview raster can have one-pixel rounding here.
            layer = overlay.resize(clean.size, Image.Resampling.BILINEAR)
            return encode_rgba(Image.alpha_composite(clean.convert('RGBA'), layer))


def derivative_key(underlying_png, guide):
    return hashlib.sha256(("agent-review-png-v1\0" + hashlib.sha256(underlying_png).hexdigest()
                           + "\0" + guide.digest).encode('ascii')).hexdigest()


class ReviewGuides:
    """Persist one small review-only image per project and sequence."""
    def __init__(self, library_dir):
        self.database = Path(library_dir) / '.review-guides.sqlite'

    def _connect(self):
        if any(Path(str(self.database) + suffix).is_symlink()
               for suffix in ('', '-journal', '-wal', '-shm')):
            raise ValueError("invalid review guide store")
        return sqlite3.connect(self.database, timeout=2)

    def get(self, project_id, sequence):
        if not self.database.is_file():
            return None
        with closing(self._connect()) as db:
            # A writer creates the schema and first value in one transaction;
            # a concurrent reader can see a newly-created empty DB.
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='guides'").fetchone():
                return None
            row = db.execute(
                """SELECT png, digest, width, height FROM guides
                   WHERE project_id=? AND sequence_id=? AND length(png)<=?""",
                (project_id, sequence.id, MAX_BYTES)).fetchone()
        if row is None:
            return None
        png, digest, width, height = row
        if (type(width) is not int or type(height) is not int
                or not 2 <= width <= MAX_EDGE or not 2 <= height <= MAX_EDGE
                or width * height > MAX_PIXELS
                or width * sequence.height != height * sequence.width
                or type(png) is not bytes or not png
                or hashlib.sha256(png).hexdigest() != digest):
            return None
        return Guide(png, digest, width, height)

    def set(self, project_id, sequence, guide):
        if guide is None and not self.database.is_file():
            return
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS guides (
                       project_id TEXT NOT NULL, sequence_id TEXT NOT NULL,
                       png BLOB NOT NULL, digest TEXT NOT NULL, width INTEGER NOT NULL,
                       height INTEGER NOT NULL, PRIMARY KEY(project_id, sequence_id))""")
            if guide is None:
                db.execute("DELETE FROM guides WHERE project_id=? AND sequence_id=?",
                           (project_id, sequence.id))
            else:
                db.execute("INSERT OR REPLACE INTO guides VALUES (?,?,?,?,?,?)",
                           (project_id, sequence.id, guide.png, guide.digest, guide.width, guide.height))
