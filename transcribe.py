#!/usr/bin/env python3
"""Transcription 100 % locale d'une ou plusieurs sources (Whisper via faster-whisper).

Entrée : in/sources.tsv (chemin<TAB>titre), dans l'ordre voulu.
Sorties (préfixe P = date + titre) :
  out/P_transcription.txt          toutes les sources dans UN fichier, paragraphes horodatés
  out/P_transcription.<lg>.txt     traduction (optionnelle, locale, Argos Translate)
  out/P_sous-titres_NN.srt         sous-titres de chaque source
  work/meta.json                   données pour prompteur.py
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

LANGS = ("fr", "it", "en", "ar")
NOMS = {"fr": "français", "it": "italien", "en": "anglais", "ar": "arabe", "es": "espagnol", "pt": "portugais",
        "de": "allemand", "fa": "persan", "ur": "ourdou", "tr": "turc", "he": "hébreu", "ru": "russe",
        "uk": "ukrainien", "zh": "chinois", "hi": "hindi", "nl": "néerlandais"}
DEBUT_JOB = float(os.environ.get("JOB_START", time.time()))
LIMITE_JOB_MIN = 300  # arrêt propre bien avant les 6 h de GitHub

# Phrases fantômes connues de Whisper (silence, musique, génériques) :
# appliquées seulement aux segments courts, pour ne jamais couper une vraie phrase.
HALLU_MARQUE = re.compile(
    r"amara\.org|sous-titr(es|age) (réalisés |faits )?(par|:)|sous-titrage st|sottotitoli (creati|e revisione)"
    r"|subtitles by|subtitled by|transcri(bed|ption) by|nancy ?qanqar|نانسي قنقر|ترجمة نانسي",
    re.IGNORECASE,
)
HALLU_SEUL = re.compile(
    r"^(merci|thank you|thanks|grazie)( beaucoup| so much| mille)?"
    r"( d'avoir regardé( cette vidéo)?| for watching| per la visione)?[ .!]*$"
    r"|^[\s♪♫🎵*.,!?…-]*$"
    r"|^[\[(]?(musique|music|musica|موسيقى|applaudissements|applause|rires|laughs|silence)[\])]?\.?$",
    re.IGNORECASE,
)


def fantome(t):
    return bool(HALLU_SEUL.match(t)) or (len(t) < 90 and bool(HALLU_MARQUE.search(t)))


def log(msg):
    print(msg, flush=True)


def ts(t):
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def hhmm(t):
    h, m, s = int(t // 3600), int(t % 3600 // 60), int(t % 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def duree_txt(sec):
    m = round(sec / 60)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{max(m, 1)} min"


def slug(texte, n=40):
    t = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    t = re.sub(r"[^A-Za-z0-9]+", "-", t).strip("-")
    return (t[:n].rstrip("-") or "video")


def duree_fichier(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", path], capture_output=True, text=True)
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


SEG_CIBLE = 1500          # tranches d'environ 25 min (≈ 18 min de calcul avec large-v3)
SEG_MAX_SEUL = 2100      # en dessous de 35 min, une source reste en un seul morceau


def choisir_modele(duree_sec):
    """Modèle pour UNE tranche (≤ ~35 min) : toujours le plus précis qui tient dans le temps."""
    return "large-v3" if duree_sec <= 2700 else "large-v3-turbo"


def silences(path):
    """Milieux des silences (s) détectés par ffmpeg, pour couper sans couper un mot."""
    r = subprocess.run(["ffmpeg", "-nostdin", "-i", path, "-vn", "-af", "silencedetect=noise=-30dB:d=0.4",
                        "-f", "null", "-"], capture_output=True, text=True)
    debuts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    fins = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return [(d + f) / 2 for d, f in zip(debuts, fins)]


def points_de_coupe(path, duree):
    if duree <= SEG_MAX_SEUL:
        return [0.0, duree]
    nb = max(2, round(duree / SEG_CIBLE))
    cible = duree / nb
    milieux = silences(path)
    coupes = [0.0]
    for k in range(1, nb):
        t = k * cible
        proches = [m for m in milieux if abs(m - t) <= 120 and m > coupes[-1] + 60]
        coupes.append(min(proches, key=lambda m: abs(m - t)) if proches else t)
    coupes.append(duree)
    return coupes


def extraire_audio(src, wav):
    r = subprocess.run(["ffmpeg", "-y", "-nostdin", "-loglevel", "error", "-i", src, "-vn",
                        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav],
                       capture_output=True, text=True)
    return r.returncode == 0, r.stderr.strip()[:400]


def paragraphes(segs):
    """Regroupe les segments en paragraphes lisibles (pause, longueur, fin de phrase)."""
    out, cur, debut, fin_prec = [], [], 0.0, None
    for s, e, t in segs:
        longueur = sum(len(x) for x in cur)
        coupe = cur and (
            (fin_prec is not None and s - fin_prec >= 1.8)
            or (longueur >= 650 and re.search(r"[.!?…؟]$", cur[-1]))
            or longueur >= 1100
        )
        if coupe:
            out.append((debut, " ".join(cur)))
            cur = []
        if not cur:
            debut = s
        cur.append(t)
        fin_prec = e
    if cur:
        out.append((debut, " ".join(cur)))
    return out


def preparer_traduction(src, dst):
    import argostranslate.package
    import argostranslate.translate

    argostranslate.package.update_package_index()
    dispo = argostranslate.package.get_available_packages()
    installes = {(p.from_code, p.to_code) for p in argostranslate.package.get_installed_packages()}

    def installer(f, t):
        if (f, t) in installes:
            return True
        for p in dispo:
            if p.from_code == f and p.to_code == t:
                log(f"Modèle de traduction {f} -> {t}")
                argostranslate.package.install_from_path(p.download())
                installes.add((f, t))
                return True
        return False

    if not installer(src, dst) and not (installer(src, "en") and installer("en", dst)):
        raise RuntimeError(f"aucun modèle de traduction {src} -> {dst}")
    return argostranslate.translate.get_translation_from_codes(src, dst).translate


def lire_sources(chemin):
    sources = []
    with open(chemin, encoding="utf-8") as f:
        for ligne in f:
            if "\t" in ligne:
                p, titre = ligne.rstrip("\n").split("\t", 1)
                sources.append({"path": p, "titre": titre.strip() or "source"})
    return sources


def ecrire_sortie_github(cle, valeur):
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as g:
            g.write(f"{cle}={valeur}\n")


# ─────────── Étape 1 : plan des tranches (coupées sur des silences) ───────────
def etape_plan(a):
    sources = lire_sources(a.sources)
    if not sources:
        print("::error::Aucune source à transcrire.")
        sys.exit(1)
    os.makedirs(a.work, exist_ok=True)
    os.makedirs("segments", exist_ok=True)
    maintenant = datetime.now(ZoneInfo("Europe/Rome"))
    prefixe = f"{maintenant:%Y-%m-%d_%Hh%M}_{slug(sources[0]['titre'], 35)}"
    if len(sources) > 1:
        prefixe += f"_+{len(sources) - 1}"
    plan = {"prefixe": prefixe, "date": f"{maintenant:%d/%m/%Y %H:%M}", "sources": [], "segments": []}
    for n, s in enumerate(sources, 1):
        duree = duree_fichier(s["path"])
        coupes = points_de_coupe(s["path"], duree)
        plan["sources"].append({"n": n, "titre": s["titre"], "duree": duree})
        for d, f in zip(coupes, coupes[1:]):
            k = len(plan["segments"])
            fichier = os.path.join("segments", f"seg_{k:03d}.flac")
            r = subprocess.run(["ffmpeg", "-y", "-nostdin", "-loglevel", "error", "-ss", f"{d:.3f}", "-i", s["path"],
                                "-t", f"{f - d:.3f}", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "flac", fichier],
                               capture_output=True, text=True)
            if r.returncode != 0 or not os.path.exists(fichier):
                log(f"::warning::Source {n} : tranche {d / 60:.0f}-{f / 60:.0f} min illisible : {r.stderr.strip()[:200]}")
                continue
            plan["segments"].append({"id": k, "n": n, "debut": d, "fin": f, "fichier": fichier})
        log(f"Source {n} : {s['titre']} — {duree_txt(duree)} → {len(coupes) - 1} tranche(s)")
    if not plan["segments"]:
        print("::error::Aucune tranche audio exploitable.")
        sys.exit(1)
    with open(os.path.join(a.work, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    ids = [g["id"] for g in plan["segments"]]
    ecrire_sortie_github("ids", json.dumps(ids))
    log(f"{len(ids)} tranche(s) à transcrire en parallèle.")
    return plan


# ─────────── Étape 2 : transcription d'UNE tranche ───────────
_MODELES = {}


def etape_segment(a, ident, plan=None):
    if plan is None:
        with open(os.path.join(a.work, "plan.json"), encoding="utf-8") as f:
            plan = json.load(f)
    seg = next(g for g in plan["segments"] if g["id"] == ident)
    duree = seg["fin"] - seg["debut"]
    modele = choisir_modele(duree) if a.model == "auto" else a.model
    from faster_whisper import WhisperModel, decode_audio
    if modele not in _MODELES:
        t0 = time.time()
        _MODELES[modele] = WhisperModel(modele, device="cpu", compute_type="int8",
                                        cpu_threads=os.cpu_count() or 2)
        log(f"Modèle {modele} chargé en {time.time() - t0:.0f} s")
    model = _MODELES[modele]
    t0 = time.time()
    audio = decode_audio(seg["fichier"])
    log(f"Tranche {ident} : source {seg['n']}, {seg['debut'] / 60:.0f}→{seg['fin'] / 60:.0f} min, modèle {modele}")

    _, _, probas = model.detect_language(audio, vad_filter=True, language_detection_segments=8,
                                         language_detection_threshold=0.9)
    probas = dict(probas or [])
    brut = max(probas, key=probas.get) if probas else "fr"
    p_brut = probas.get(brut, 0.0)
    # fr/it/en/ar en priorité ; une autre langue n'est gardée que si Whisper en est sûr
    # (l'arabe du Golfe est parfois pris pour du persan/ourdou : seuil plus strict pour eux)
    seuil = 0.9 if brut in ("fa", "ur", "ps", "ku") else 0.6
    langue = brut if (brut in LANGS or p_brut >= seuil) else max(LANGS, key=lambda l: probas.get(l, 0.0))
    log(f"Langue : {langue} ({probas.get(langue, 0):.0%}) [détection brute : {brut} {p_brut:.0%}]")

    segments, _ = model.transcribe(
        audio, language=langue, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        beam_size=5, condition_on_previous_text=False,
        no_speech_threshold=0.6, compression_ratio_threshold=2.4,
    )
    res, prec, rep, last = [], None, 0, time.time()
    for s_ in segments:
        t = s_.text.strip()
        if not t or fantome(t):
            continue
        rep = rep + 1 if t == prec else 0
        prec = t
        if rep >= 2:                      # boucle d'hallucination
            continue
        res.append((seg["debut"] + s_.start, seg["debut"] + s_.end, t))
        if time.time() - last > 60:
            fait = s_.end / max(duree, 1)
            log(f"… {fait:.0%} | reste ≈ {(time.time() - t0) / max(fait, 0.01) * (1 - fait) / 60:.0f} min")
            last = time.time()
    os.makedirs(os.path.join(a.work, "segs"), exist_ok=True)
    with open(os.path.join(a.work, "segs", f"seg_{ident:03d}.json"), "w", encoding="utf-8") as f:
        json.dump({"id": ident, "langue": langue, "duree": duree, "modele": modele, "segments": res},
                  f, ensure_ascii=False)
    log(f"Tranche {ident} : {len(res)} segments, {sum(len(t.split()) for *_, t in res)} mots "
        f"en {(time.time() - t0) / 60:.1f} min")


# ─────────── Étape 3 : assemblage, fichiers finaux, traduction ───────────
def etape_assembler(a):
    with open(os.path.join(a.work, "plan.json"), encoding="utf-8") as f:
        plan = json.load(f)
    os.makedirs(a.out, exist_ok=True)
    prefixe = plan["prefixe"]
    ecrire_sortie_github("prefixe", prefixe)
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a") as g:
            g.write(f"PREFIXE={prefixe}\n")
    total = sum(s["duree"] for s in plan["sources"])
    chemin_txt = os.path.join(a.out, f"{prefixe}_transcription.txt")
    meta = {"prefixe": prefixe, "modele": "tranches", "sources": [], "incomplet": False}
    manquantes = 0
    with open(chemin_txt, "w", encoding="utf-8") as txt:
        txt.write(f"TRANSCRIPTION — {len(plan['sources'])} source(s) — {duree_txt(total)} — {plan['date']}\n")
        for src in plan["sources"]:
            i = src["n"]
            segs, langues, trous = [], {}, []
            for g in [g for g in plan["segments"] if g["n"] == i]:
                chemin = os.path.join(a.work, "segs", f"seg_{g['id']:03d}.json")
                if not os.path.exists(chemin):
                    manquantes += 1
                    trous.append((g["debut"], g["fin"]))
                    continue
                with open(chemin, encoding="utf-8") as f:
                    d = json.load(f)
                segs += [tuple(x) for x in d["segments"]]
                langues[d["langue"]] = langues.get(d["langue"], 0) + d["duree"]
            if not segs and not trous:
                continue
            langue = max(langues, key=langues.get) if langues else "fr"
            segs.sort(key=lambda x: x[0])
            with open(os.path.join(a.out, f"{prefixe}_sous-titres_{i:02d}.srt"), "w", encoding="utf-8") as srt:
                for k, (d_, f_, t) in enumerate(segs, 1):
                    srt.write(f"{k}\n{ts(d_)} --> {ts(f_)}\n{t}\n\n")
            paras = paragraphes(segs)
            txt.write(f"\n\n════════ SOURCE {i}/{len(plan['sources'])} : {src['titre']} "
                      f"({NOMS.get(langue, langue)}, {duree_txt(src['duree'])}) ════════\n")
            lignes = [(d_, t) for d_, t in paras] + [(d_, f"[… passage {hhmm(d_)}–{hhmm(f_)} non transcrit …]")
                                                     for d_, f_ in trous]
            for d_, t in sorted(lignes, key=lambda x: x[0]):
                txt.write(f"\n[{hhmm(d_)}] {t}\n")
            nb_mots = sum(len(t.split()) for *_, t in segs)
            chemin_src = os.path.join(a.work, f"source_{i:02d}.json")
            with open(chemin_src, "w", encoding="utf-8") as f:
                json.dump({"paragraphes": paras}, f, ensure_ascii=False)
            meta["sources"].append({"n": i, "titre": src["titre"], "langue": langue, "duree": src["duree"],
                                    "mots": nb_mots, "fichier": chemin_src})
            log(f"Source {i} : {NOMS.get(langue, langue)}, {nb_mots} mots" + (f", {len(trous)} trou(s)" if trous else ""))
    if manquantes:
        meta["incomplet"] = True
        log(f"::warning::{manquantes} tranche(s) non transcrite(s) : signalées dans le texte.")
    with open(os.path.join(a.work, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    log(f"Transcription écrite : {chemin_txt}")
    if not any(s["mots"] for s in meta["sources"]):
        print("::error::Aucune parole détectée.")
        sys.exit(1)
    traduire(a, meta, prefixe)


def traduire(a, meta, prefixe):
    cible = a.translate.lower()
    if cible not in LANGS:
        return
    log(f"Traduction vers {NOMS[cible]}…")
    chemin_t = os.path.join(a.out, f"{prefixe}_transcription.{cible}.txt")
    try:
        trads = {}
        with open(chemin_t, "w", encoding="utf-8") as out:
            out.write(f"TRANSCRIPTION TRADUITE ({NOMS[cible]}) — {len(meta['sources'])} source(s)\n")
            for s in meta["sources"]:
                with open(s["fichier"], encoding="utf-8") as f:
                    paras = json.load(f)["paragraphes"]
                out.write(f"\n\n════════ SOURCE {s['n']} : {s['titre']} ════════\n")
                if s["langue"] == cible:
                    for d, t in paras:
                        out.write(f"\n[{hhmm(d)}] {t}\n")
                    continue
                if s["langue"] not in trads:
                    trads[s["langue"]] = preparer_traduction(s["langue"], cible)
                for k, (d, t) in enumerate(paras, 1):
                    out.write(f"\n[{hhmm(d)}] {trads[s['langue']](t)}\n")
                    out.flush()
                    if k % 50 == 0:
                        log(f"… source {s['n']} : {k}/{len(paras)} paragraphes")
        log(f"Traduction écrite : {chemin_t}")
    except Exception as exc:
        if os.path.exists(chemin_t):
            os.remove(chemin_t)          # pas de fichier à moitié traduit
        print(f"::warning::La transcription est OK mais la traduction a échoué : {exc}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--etape", default="tout", choices=["tout", "plan", "segment", "assembler"])
    p.add_argument("--id", type=int, default=0, help="numéro de tranche (étape segment)")
    p.add_argument("--sources", default="in/sources.tsv")
    p.add_argument("--model", default="auto")
    p.add_argument("--translate", default="non")
    p.add_argument("--out", default="out")
    p.add_argument("--work", default="work")
    a = p.parse_args()
    if a.etape == "plan":
        etape_plan(a)
    elif a.etape == "segment":
        etape_segment(a, a.id)
    elif a.etape == "assembler":
        etape_assembler(a)
    else:                                   # tout, en séquence (usage local)
        plan = etape_plan(a)
        for g in plan["segments"]:
            etape_segment(a, g["id"], plan)
        etape_assembler(a)


if __name__ == "__main__":
    main()
