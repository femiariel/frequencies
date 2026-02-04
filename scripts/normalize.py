def normalize_an(raw_scrutins: list[dict]) -> list[dict]:
    """
    raw_scrutins: liste de dicts sortis par scripts/sources/an.py
    Retour: liste de scrutins au format commun export.
    """
    out = []
    for s in raw_scrutins:
        out.append(
            {
                "id": s["id"],                  # ex: AN-17-1234
                "chamber": "AN",
                "date": s["date"],
                "title": s["title"],
                "object": s.get("object"),
                "scrutin_type": s.get("scrutin_type"),
                "result_status": s.get("result_status"),
                "counts": s.get("counts"),
                "themes": [],                   # rempli ensuite
                "source_url": s.get("source_url"),
                "votes": s.get("votes", []),
            }
        )
    return out