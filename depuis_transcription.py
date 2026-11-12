#!/usr/bin/env python3
"""Reconstruit work/meta.json + work/source_NN.json à partir d'un fichier
« …_transcription.txt » publié, pour refaire le texte prompteur sans retranscrire.

Usage : python depuis_transcription.py FICHIER_transcription.txt [--work work]
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from transcribe import NOMS

INVERSE = {v: k for k, v in NOMS.items()}
ENTETE = re.compile(r"^═+ SOURCE (\d+)(?:/\d+)? : (.*?) \(([^,()]+), [^()]*\) ═+\s*$")
PARA = re.compile(r"^\[(\d+:)?\d{1,2}:\d{2}\] (.*)$")


def secondes(etiquette):
    morceaux = [int(x) for x in etiquette.strip("[]").split(":")]
    total = 0
    for m in morceaux:
        total = total * 60 + m
    return float(total)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("transcription")
    p.add_argument("--work", default="work")
    a = p.parse_args()

    sources, cur = [], None
    with open(a.transcription, encoding="utf-8") as f:
        for ligne in f:
            ligne = ligne.rstrip("\n")
            m = ENTETE.match(ligne)
            if m:
                nom_langue = m.group(3).strip()
                cur = {"n": int(m.group(1)), "titre": m.group(2).strip(),
                       "langue": INVERSE.get(nom_langue, nom_langue), "paragraphes": []}
                sources.append(cur)
                continue
            m = PARA.match(ligne)
            if m and cur is not None:
                cur["paragraphes"].append([secondes(ligne.split("]")[0] + "]"), m.group(2).strip()])

    sources = [s for s in sources if s["paragraphes"]]
    if not sources:
        print(f"::error::Aucun texte reconnu dans {a.transcription} (format inattendu).")
        sys.exit(1)

    os.makedirs(a.work, exist_ok=True)
    base = os.path.basename(a.transcription)
    base = re.sub(r"_transcription(\.[a-z]{2})?\.txt$", "", base)
    prefixe = f"{base}_v{datetime.now(ZoneInfo('Europe/Rome')):%Hh%M}"   # ne remplace pas l'ancien texte
    meta = {"prefixe": prefixe, "modele": "?", "sources": [], "incomplet": False}
    for s in sources:
        chemin = os.path.join(a.work, f"source_{s['n']:02d}.json")
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump({"paragraphes": s["paragraphes"]}, f, ensure_ascii=False)
        mots = sum(len(t.split()) for _, t in s["paragraphes"])
        meta["sources"].append({"n": s["n"], "titre": s["titre"], "langue": s["langue"],
                                "duree": s["paragraphes"][-1][0], "mots": mots, "fichier": chemin})
        print(f"Source {s['n']} : {s['titre']} ({s['langue']}, {mots} mots)")
    with open(os.path.join(a.work, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a") as g:
            g.write(f"PREFIXE={prefixe}\n")
    print(f"Prêt : {len(sources)} source(s), préfixe {prefixe}")


if __name__ == "__main__":
    main()
