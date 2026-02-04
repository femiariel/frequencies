from datetime import datetime, timezone
from pathlib import Path

from sources.an import fetch_an_scrutins
from normalize import normalize_an
from themes import load_themes, assign_themes
from export import export_all


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def main():
    generated_at = datetime.now(timezone.utc).isoformat()

    cfg = load_themes(DATA_DIR / "themes.json")

    raw_an = fetch_an_scrutins(limit=100)
    scrutins = normalize_an(raw_an)

    scrutins = assign_themes(scrutins, cfg)

    export_all(DATA_DIR, scrutins, generated_at)

    print(f"OK: {len(scrutins)} scrutins AN exportés.")


if __name__ == "__main__":
    main()