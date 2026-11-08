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
NOMS = {"fr": "français", "it": "italien", "en": "anglais", "ar": "arabe"}
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


def choisir_modele(total_sec):
    """Le plus précis possible dans le temps disponible (mesuré : small ≈ 8× temps réel)."""
    m = total_sec / 60
    if m <= 40:
        return "large-v3"
    if m <= 150:
        return "large-v3-turbo"
    return "small"


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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sources", default="in/sources.tsv")
    p.add_argument("--model", default="auto")
    p.add_argument("--translate", default="non")
    p.add_argument("--out", default="out")
    p.add_argument("--work", default="work")
    p.add_argument("--choisir", action="store_true",
                   help="affiche seulement le modèle qui sera utilisé (pour le cache)")
    a = p.parse_args()

    sources = []
    with open(a.sources, encoding="utf-8") as f:
        for ligne in f:
            if "\t" in ligne:
                chemin, titre = ligne.rstrip("\n").split("\t", 1)
                sources.append({"path": chemin, "titre": titre.strip() or "source"})
    if not sources:
        print("::error::Aucune source à transcrire.")
        sys.exit(1)

    for s in sources:
        s["duree"] = duree_fichier(s["path"])
    total = sum(s["duree"] for s in sources)
    modele = choisir_modele(total) if a.model == "auto" else a.model
    if a.choisir:
        print(modele)
        return

    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)
    maintenant = datetime.now(ZoneInfo("Europe/Rome"))
    prefixe = f"{maintenant:%Y-%m-%d_%Hh%M}_{slug(sources[0]['titre'], 35)}"
    if len(sources) > 1:
        prefixe += f"_+{len(sources) - 1}"
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a") as g:
            g.write(f"PREFIXE={prefixe}\n")

    log(f"{len(sources)} source(s) | durée totale {duree_txt(total)} | modèle {modele} | CPU {os.cpu_count()}")

    from faster_whisper import WhisperModel, decode_audio

    t0 = time.time()
    model = WhisperModel(modele, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 2)
    log(f"Modèle chargé en {time.time() - t0:.0f} s")

    chemin_txt = os.path.join(a.out, f"{prefixe}_transcription.txt")
    txt = open(chemin_txt, "w", encoding="utf-8")
    txt.write(f"TRANSCRIPTION — {len(sources)} source(s) — {duree_txt(total)} — "
              f"{maintenant:%d/%m/%Y %H:%M}\n")
    txt.flush()

    meta = {"prefixe": prefixe, "modele": modele, "sources": [], "incomplet": False}
    deja_fait = 0.0
    for i, s in enumerate(sources, 1):
        if (time.time() - DEBUT_JOB) / 60 > LIMITE_JOB_MIN:
            log(f"::warning::Temps limite proche : sources {i} à {len(sources)} non transcrites.")
            meta["incomplet"] = True
            break
        log(f"── Source {i}/{len(sources)} : {s['titre']} ({duree_txt(s['duree'])})")
        wav = os.path.join(a.work, "audio.wav")
        ok, err = extraire_audio(s["path"], wav)
        if not ok:
            log(f"::warning::Source {i} ignorée, extraction audio impossible : {err}")
            continue
        audio = decode_audio(wav)
        os.remove(wav)
        dur = len(audio) / 16000
        if dur < 1:
            log(f"::warning::Source {i} ignorée : audio vide.")
            continue

        _, _, probas = model.detect_language(audio, vad_filter=True, language_detection_segments=8,
                                             language_detection_threshold=0.9)
        probas = dict(probas or [])
        langue = max(LANGS, key=lambda l: probas.get(l, 0.0))
        log(f"Langue : {langue} ({probas.get(langue, 0):.0%})")

        segments, _ = model.transcribe(
            audio, language=langue, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            beam_size=5, condition_on_previous_text=False,
            no_speech_threshold=0.6, compression_ratio_threshold=2.4,
        )
        segs, prec, rep, last = [], None, 0, time.time()
        srt_path = os.path.join(a.out, f"{prefixe}_sous-titres_{i:02d}.srt")
        with open(srt_path, "w", encoding="utf-8") as srt:
            for seg in segments:
                t = seg.text.strip()
                if not t or fantome(t):
                    continue
                rep = rep + 1 if t == prec else 0
                prec = t
                if rep >= 2:          # boucle d'hallucination
                    continue
                segs.append((seg.start, seg.end, t))
                srt.write(f"{len(segs)}\n{ts(seg.start)} --> {ts(seg.end)}\n{t}\n\n")
                srt.flush()
                if time.time() - last > 60:
                    fait = deja_fait + seg.end
                    ecoule = time.time() - t0
                    log(f"… {fait / total:.0%} | reste ≈ {ecoule / max(fait, 1) * (total - fait) / 60:.0f} min")
                    last = time.time()
                if (time.time() - DEBUT_JOB) / 60 > LIMITE_JOB_MIN:
                    log("::warning::Temps limite proche : transcription arrêtée ici.")
                    meta["incomplet"] = True
                    break
        deja_fait += dur

        paras = paragraphes(segs)
        txt.write(f"\n\n════════ SOURCE {i}/{len(sources)} : {s['titre']} "
                  f"({NOMS[langue]}, {duree_txt(dur)}) ════════\n")
        for debut, texte in paras:
            txt.write(f"\n[{hhmm(debut)}] {texte}\n")
        txt.flush()

        nb_mots = sum(len(t.split()) for _, _, t in segs)
        log(f"Source {i} : {len(segs)} segments, {nb_mots} mots")
        seg_path = os.path.join(a.work, f"source_{i:02d}.json")
        with open(seg_path, "w", encoding="utf-8") as f:
            json.dump({"paragraphes": paras}, f, ensure_ascii=False)
        meta["sources"].append({"n": i, "titre": s["titre"], "langue": langue, "duree": dur,
                                "mots": nb_mots, "fichier": seg_path})
        if meta["incomplet"]:
            break

    txt.close()
    with open(os.path.join(a.work, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    log(f"Transcription écrite : {chemin_txt} ({(time.time() - t0) / 60:.1f} min)")

    if not any(s["mots"] for s in meta["sources"]):
        print("::error::Aucune parole détectée.")
        sys.exit(1)

    # ---- Traduction optionnelle (par paragraphes entiers = meilleure qualité) ----
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


if __name__ == "__main__":
    main()
