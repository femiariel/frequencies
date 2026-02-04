import zipfile
from pathlib import Path

import httpx
from lxml import etree

AN_LEGISLATURE = "17"
AN_ZIP_URL = "http://data.assemblee-nationale.fr/static/openData/repository/17/loi/scrutins/Scrutins.xml.zip"


def _cache_dir() -> Path:
    return Path(".cache") / "an"


def _download(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)


def _date_only(s: str | None) -> str | None:
    if not s:
        return None
    return s.strip().split("T")[0]


def _norm_result(s: str | None) -> str | None:
    if not s:
        return None
    low = s.strip().lower()
    if "adopt" in low:
        return "adopted"
    if "rejet" in low:
        return "rejected"
    return low


def _first_text(node, local_name: str) -> str | None:
    """
    Retourne le texte du premier élément dont local-name() == local_name,
    sans se soucier des namespaces.
    """
    v = node.xpath(f"string(.//*[local-name()='{local_name}'][1])")
    v = v.strip() if isinstance(v, str) else ""
    return v or None


def _extract_votes(scrutin_node) -> list[dict]:
    """
    Extraction robuste et namespace-agnostic :
    - prend tous les acteurRef du scrutin
    - déduit la position en remontant les parents (pour/contre/abstention/nonVotant)
    - récupère organeRef (groupe) si présent dans les parents
    """
    votes = []

    bucket_to_pos = {
        "pour": "FOR",
        "contre": "AGAINST",
        "abstention": "ABSTAIN",
        "nonVotant": "NONVOTING",
        "nonvotant": "NONVOTING",
        "nonVotants": "NONVOTING",
    }

    actor_nodes = scrutin_node.xpath(".//*[local-name()='acteurRef']")
    for a in actor_nodes:
        pid = (a.text or "").strip()
        if not pid:
            continue

        pos = None
        group = None

        cur = a
        for _ in range(0, 30):
            cur = cur.getparent()
            if cur is None:
                break

            lname = cur.tag.split("}")[-1] if isinstance(cur.tag, str) else ""

            if pos is None and lname in bucket_to_pos:
                pos = bucket_to_pos[lname]

            if group is None:
                g = cur.xpath("string(.//*[local-name()='organeRef'][1])")
                g = g.strip() if isinstance(g, str) else ""
                if g:
                    group = g

            if pos is not None and group is not None:
                break

        # Si on n'arrive pas à déterminer la position, on ignore (sinon bruit)
        if pos is None:
            continue

        votes.append(
            {
                "person_id": pid,     # ex: PAxxxx
                "position": pos,      # FOR/AGAINST/ABSTAIN/NONVOTING
                "group": group,       # peut être None
                "constituency": None,
                "name": None,
            }
        )

    # dédoublonnage (au cas où)
    seen = set()
    uniq = []
    for v in votes:
        key = (v["person_id"], v["position"], v.get("group"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(v)

    return uniq


def _parse_one_xml(fileobj) -> list[dict]:
    """
    Parse un flux XML et retourne les scrutins trouvés.
    Namespace-agnostic : on garde local-name()='scrutin'.
    """
    out = []

    context = etree.iterparse(fileobj, events=("end",), recover=True, huge_tree=True)
    for _, el in context:
        if not isinstance(el.tag, str):
            continue

        # local-name strict (évite de matcher <scrutins>)
        if el.tag.split("}")[-1] != "scrutin":
            continue

        numero = _first_text(el, "numero") or "UNKNOWN"
        date = _date_only(_first_text(el, "dateScrutin")) or "1970-01-01"
        title = _first_text(el, "objet") or "(sans titre)"

        scrutin_type = (
            el.xpath("string(.//*[local-name()='typeScrutin']//*[local-name()='libelle'][1])").strip()
            or None
        )
        if scrutin_type is None:
            scrutin_type = _first_text(el, "typeScrutin")

        result_status = _norm_result(
            el.xpath("string(.//*[local-name()='syntheseVote']//*[local-name()='resultat'][1])").strip()
            or None
        )

        # totaux (si dispo)
        counts = {}

        def count_of(name: str):
            v = el.xpath(f"string(.//*[local-name()='decompte']//*[local-name()='{name}'][1])")
            v = v.strip() if isinstance(v, str) else ""
            return int(v) if v.isdigit() else None

        for key, lname in [
            ("for", "pour"),
            ("against", "contre"),
            ("abstention", "abstention"),
            ("nonvoting", "nonVotant"),
        ]:
            cv = count_of(lname)
            if cv is not None:
                counts[key] = cv

        if not counts:
            counts = None

        votes = _extract_votes(el)

        out.append(
            {
                "id": f"AN-{AN_LEGISLATURE}-{numero}",
                "date": date,
                "title": title,
                "object": None,
                "scrutin_type": scrutin_type,
                "result_status": result_status,
                "counts": counts,
                "source_url": None,
                "votes": votes,
            }
        )

        # nettoyage mémoire
        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]

    return out


def fetch_an_scrutins(limit: int = 200) -> list[dict]:
    """
    Lit le ZIP.
    - Si le zip contient plusieurs XML : on les parcourt tous.
    - Si un seul gros XML : ça marche aussi.
    """
    cache = _cache_dir()
    zip_path = cache / "Scrutins.xml.zip"
    if not zip_path.exists():
        _download(AN_ZIP_URL, zip_path)

    scrutins = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        xml_files = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not xml_files:
            raise RuntimeError("Zip AN: aucun fichier XML trouvé")

        for name in xml_files:
            with zf.open(name) as f:
                scrutins.extend(_parse_one_xml(f))

    # dédoublonnage par id (au cas où)
    by_id = {}
    for s in scrutins:
        by_id[s["id"]] = s
    scrutins = list(by_id.values())

    scrutins.sort(key=lambda s: (s["date"], s["id"]), reverse=True)
    return scrutins[:limit]