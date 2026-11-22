"""Save local JSON artifacts without exposing partially written files."""

import json
import tempfile
import time
from pathlib import Path


def write_json(path, value, *, default=None):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.stem}-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, default=default)
        for delay in (0.01, 0.02, 0.04, 0.08, 0.16, 0.32, None):
            try:
                temporary.replace(path)
                return
            except PermissionError as error:
                # Windows can deny replacement while another request holds a read handle.
                if delay is None or getattr(error, "winerror", None) not in (5, 32, 33):
                    raise
                time.sleep(delay)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
