"""Script de test Google Maps — à lancer en local pour diagnostiquer.

Usage :
    python test_maps.py "TA_CLE_API"
"""

import sys
import urllib.request
import urllib.parse
import json

def test_distance_matrix(api_key: str, adresses: dict[str, str], mode: str = "transit"):
    """Test l'API Google Distance Matrix et affiche TOUT le debug."""
    noms = list(adresses.keys())
    addrs_encoded = [urllib.parse.quote(adresses[n]) for n in noms]
    origins = "|".join(addrs_encoded)
    destinations = "|".join(addrs_encoded)

    url = (
        f"https://maps.googleapis.com/maps/api/distancematrix/json"
        f"?origins={origins}&destinations={destinations}"
        f"&mode={mode}&key={api_key}"
    )

    print(f"\n{'='*60}")
    print(f"Lieux : {noms}")
    print(f"Mode  : {mode}")
    print(f"URL   : {url[:120]}...")
    print(f"{'='*60}\n")

    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        print("RÉPONSE COMPLÈTE :")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        print()

        status = data.get("status", "?")
        print(f"Status API : {status}")
        if status != "OK":
            print(f"❌ ÉCHEC : {data.get('error_message', 'pas de message')}")
            return

        for i, nom_a in enumerate(noms):
            row = data["rows"][i]
            for j, nom_b in enumerate(noms):
                if j <= i:
                    continue
                elem = row["elements"][j]
                if elem["status"] == "OK":
                    duree = elem["duration"]["text"]
                    dist = elem["distance"]["text"]
                    print(f"✅ {nom_a} ↔ {nom_b} : {duree} ({dist})")
                else:
                    print(f"❌ {nom_a} ↔ {nom_b} : {elem['status']}")

    except Exception as e:
        print(f"💥 Exception : {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python test_maps.py TA_CLE_API")
        sys.exit(1)

    api_key = sys.argv[1]

    # Adresses de test (modifie selon tes vrais lieux)
    adresses = {
        "Maison Strasbourg": "12 rue des Frères Lumière, 67201 Eckbolsheim",
        "Fac": "4 rue Blaise Pascal, 67000 Strasbourg",
        "Magasin": "6 rue du Marché, 67000 Strasbourg",
        "Gare": "20 Place de la Gare, 67000 Strasbourg",
    }

    test_distance_matrix(api_key, adresses)

    # Test aussi avec moins de lieux pour isoler
    print(f"\n{'='*60}")
    print("TEST AVEC 2 LIEUX SEULEMENT :")
    print(f"{'='*60}")
    test_distance_matrix(api_key, {"A": list(adresses.values())[0], "B": list(adresses.values())[1]})
