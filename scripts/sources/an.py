import zipfile
from pathlib import Path
import json

import httpx
from lxml import etree

AN_LEGISLATURE = "17"
AN_ZIP_URL = "http://data.assemblee-nationale.fr/static/openData/repository/17/loi/scrutins/Scrutins.xml.zip"
# URL CORRIGÉE : fichier JSON composite avec acteurs + mandats + organes
AN_ACTEURS_URL = "https://data.assemblee-nationale.fr/static/openData/repository/17/amo/deputes_actifs_mandats_actifs_organes/AMO10_deputes_actifs_mandats_actifs_organes.json.zip"


def _cache_dir() -> Path:
    return Path(".cache") / "an"


def _download(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"📥 Téléchargement de {dest.name}...")
    with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    print(f"✅ {dest.name} téléchargé")


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
        "pours": "FOR",
        "contre": "AGAINST",
        "contres": "AGAINST",
        "abstention": "ABSTAIN",
        "abstentions": "ABSTAIN",
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

        if pos is None:
            continue

        votes.append(
            {
                "person_id": pid,
                "position": pos,
                "group": group,
                "constituency": None,
                "name": None,
            }
        )

    # dédoublonnage
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
    Parse un flux XML qui contient UN SEUL scrutin (la racine est le scrutin).
    Chaque fichier XML du ZIP = 1 scrutin complet.
    """
    try:
        tree = etree.parse(fileobj)
        root = tree.getroot()
        
        el = root

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

        return [{
            "id": f"AN-{AN_LEGISLATURE}-{numero}",
            "date": date,
            "title": title,
            "object": None,
            "scrutin_type": scrutin_type,
            "result_status": result_status,
            "counts": counts,
            "source_url": None,
            "votes": votes,
        }]
        
    except Exception as e:
        return []


def fetch_an_acteurs() -> tuple[dict, dict]:
    """
    Télécharge et parse le fichier JSON des acteurs (députés) et organes (groupes).
    Retourne deux dicts:
    - acteurs: {person_id: {name, ...}}
    - organes: {organe_id: {name, acronym}}
    """
    cache = _cache_dir()
    zip_path = cache / "Acteurs.json.zip"
    
    if not zip_path.exists():
        _download(AN_ACTEURS_URL, zip_path)
    
    acteurs = {}
    organes = {}
    
    with zipfile.ZipFile(zip_path, "r") as zf:
        json_files = [n for n in zf.namelist() if n.lower().endswith(".json")]
        if not json_files:
            print("⚠️  Aucun fichier JSON trouvé dans le ZIP acteurs")
            return acteurs, organes
        
        with zf.open(json_files[0]) as f:
            data = json.load(f)
            
            # Parser les acteurs (députés)
            acteurs_list = data.get("acteurs", {}).get("acteur", [])
            if not isinstance(acteurs_list, list):
                acteurs_list = [acteurs_list] if acteurs_list else []
            
            for acteur in acteurs_list:
                # L'UID peut être directement une string ou dans un dict avec #text
                uid = acteur.get("uid")
                if isinstance(uid, dict):
                    uid = uid.get("#text", "")
                uid = uid.strip() if uid else ""
                
                if not uid:
                    continue
                
                etat_civil = acteur.get("etatCivil", {})
                ident = etat_civil.get("ident", {})
                prenom = ident.get("prenom", "")
                nom = ident.get("nom", "")
                full_name = f"{prenom} {nom}".strip()
                
                acteurs[uid] = {
                    "name": full_name or "Inconnu",
                }
            
            # Parser les organes (groupes parlementaires)
            organes_list = data.get("organes", {}).get("organe", [])
            if not isinstance(organes_list, list):
                organes_list = [organes_list] if organes_list else []
                
            for organe in organes_list:
                uid = organe.get("uid", "")
                if isinstance(uid, dict):
                    uid = uid.get("#text", "")
                uid = uid.strip() if uid else ""
                
                if not uid:
                    continue
                
                libelle = organe.get("libelle", "")
                libelle_abrege = organe.get("libelleAbrege", "") or organe.get("libelleAbrev", "")
                
                organes[uid] = {
                    "name": libelle or "Groupe inconnu",
                    "acronym": libelle_abrege or "",
                }
    
    print(f"✅ {len(acteurs)} acteurs chargés")
    print(f"✅ {len(organes)} organes chargés")
    return acteurs, organes


def fetch_an_scrutins(limit: int = 200) -> list[dict]:
    """
    Retourne une liste de scrutins avec votes enrichis (noms des députés et groupes).
    """
    # 1. Charger les acteurs et organes
    print("📥 Chargement des acteurs et organes...")
    acteurs, organes = fetch_an_acteurs()
    
    # 2. Charger les scrutins
    cache = _cache_dir()
    zip_path = cache / "Scrutins.xml.zip"
    if not zip_path.exists():
        _download(AN_ZIP_URL, zip_path)

    scrutins = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        xml_files = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not xml_files:
            raise RuntimeError("Zip AN: aucun fichier XML trouvé")

        print(f"📄 Parsing {len(xml_files)} fichiers XML...")
        for i, name in enumerate(xml_files):
            if i % 500 == 0:
                print(f"   ... {i}/{len(xml_files)}")
            
            with zf.open(name) as f:
                scrutins.extend(_parse_one_xml(f))

    print(f"✅ {len(scrutins)} scrutins parsés")
    
    # 3. Enrichir les votes avec les noms
    print("🔄 Enrichissement des votes avec noms et groupes...")
    for scrutin in scrutins:
        for vote in scrutin.get("votes", []):
            person_id = vote.get("person_id")
            group_id = vote.get("group")
            
            if person_id in acteurs:
                vote["name"] = acteurs[person_id]["name"]
            
            if group_id in organes:
                vote["group_name"] = organes[group_id]["name"]
                vote["group_acronym"] = organes[group_id]["acronym"]

    # Déduplication par id
    by_id = {}
    for s in scrutins:
        by_id[s["id"]] = s
    scrutins = list(by_id.values())

    scrutins.sort(key=lambda s: (s["date"], s["id"]), reverse=True)
    print(f"✅ Retour des {min(limit, len(scrutins))} scrutins les plus récents")
    return scrutins[:limit]