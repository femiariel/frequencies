import json
from pathlib import Path
from collections import defaultdict


def _write_json(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def export_all(data_dir: Path, scrutins: list[dict], generated_at: str):
    """
    Ecrit:
    - data/index.json (liste filtrable)
    - data/people.json (référentiel minimal)
    - data/scrutins/YYYY.json (détails + votes)
    """
    # index léger
    index_items = []
    for s in scrutins:
        index_items.append(
            {
                "id": s["id"],
                "chamber": s["chamber"],
                "date": s["date"],
                "title": s["title"],
                "scrutin_type": s.get("scrutin_type"),
                "result_status": s.get("result_status"),
                "counts": s.get("counts"),
                "themes": s.get("themes", []),
                "source_url": s.get("source_url"),
            }
        )
    index_items.sort(key=lambda x: (x["date"], x["id"]), reverse=True)
    _write_json(data_dir / "index.json", {"generated_at": generated_at, "scrutins": index_items})

    # people minimal (on enrichira plus tard)
    people_map = {}
    for s in scrutins:
        for v in s.get("votes", []):
            pid = v["person_id"]
            if pid not in people_map:
                people_map[pid] = {"person_id": pid, "name": v.get("name"), "chamber": s["chamber"]}
            if v.get("group"):
                people_map[pid]["group"] = v["group"]
            if v.get("constituency"):
                people_map[pid]["constituency"] = v["constituency"]
            if v.get("name"):
                people_map[pid]["name"] = v["name"]

    people_list = sorted(people_map.values(), key=lambda p: ((p.get("name") or ""), p["person_id"]))
    _write_json(data_dir / "people.json", {"generated_at": generated_at, "people": people_list})

    # détails par année
    by_year = defaultdict(list)
    for s in scrutins:
        year = int(s["date"][:4])
        by_year[year].append(s)

    for year, items in by_year.items():
        items.sort(key=lambda x: (x["date"], x["id"]), reverse=True)
        _write_json(data_dir / "scrutins" / f"{year}.json", {"year": year, "scrutins": items})