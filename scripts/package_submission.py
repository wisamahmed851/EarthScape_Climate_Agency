"""Build dist/earthscape_submission.zip from the project files, leaving out secrets, environments, data and temporary files."""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {".venv", "venv", ".git", "data", "artifacts", "logs", "backups", "dist", "__pycache__", ".ipynb_checkpoints", ".pytest_cache", ".idea"}
EXCLUDE_NAMES = {".env", "Thumbs.db", ".DS_Store", "Wisam.md"}   # Wisam.md is a personal to-do guide, not a deliverable
EXCLUDE_SUFFIXES = {".pyc", ".log", ".pem", ".key", ".p12", ".jks", ".hdf", ".part", ".mp4", ".zip"}


def included(path):
    rel = path.relative_to(ROOT)
    if any(p in EXCLUDE_DIRS for p in rel.parts[:-1]) or path.name in EXCLUDE_NAMES or path.suffix in EXCLUDE_SUFFIXES:
        return False
    return not (path.name.startswith(".env") and path.name != ".env.example")


def main():
    out = ROOT / "dist" / "earthscape_submission.zip"
    out.parent.mkdir(exist_ok=True)
    files = [p for p in sorted(ROOT.rglob("*")) if p.is_file() and included(p)]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, Path("earthscape") / p.relative_to(ROOT))
    print(f"{out}: {len(files)} files, {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
