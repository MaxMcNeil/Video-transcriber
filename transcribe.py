#!/usr/bin/env python3
"""Transcription 100 % locale d'une vidéo (Whisper via faster-whisper, sans API).

- Langue détectée automatiquement parmi fr / it / en / ar
- Modèle choisi automatiquement selon la durée (ou imposé)
- Traduction optionnelle, locale (Argos Translate), vers fr / it / en / ar
"""
import argparse
import json
import os
import subprocess
import time

LANGS = ("fr", "it", "en", "ar")


def ts(t: float) -> str:
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def duree(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(r.stdout)["format"]["duration"])


def choisir_modele(sec: float) -> str:
    """Le plus précis possible en restant loin de la limite de 6 h de GitHub."""
    minutes = sec / 60
    if minutes <= 15:
        return "large-v3"
    if minutes <= 75:
        return "medium"
    return "small"


def ecrire(segs, base_path):
    with open(base_path + ".txt", "w", encoding="utf-8") as txt, \
         open(base_path + ".srt", "w", encoding="utf-8") as srt:
        for n, (start, end, text) in enumerate(segs, 1):
            txt.write(text + "\n")
            srt.write(f"{n}\n{ts(start)} --> {ts(end)}\n{text}\n\n")
    print(f"Écrit : {base_path}.txt et {base_path}.srt", flush=True)


def preparer_traduction(src: str, dst: str):
    import argostranslate.package
    import argostranslate.translate

    argostranslate.package.update_package_index()
    dispo = argostranslate.package.get_available_packages()

    def installer(f, t):
        for p in dispo:
            if p.from_code == f and p.to_code == t:
                argostranslate.package.install_from_path(p.download())
                return True
        return False

    # Direct si possible, sinon via l'anglais
    if not installer(src, dst):
        if not (installer(src, "en") and installer("en", dst)):
            raise RuntimeError(f"Pas de modèle de traduction {src} -> {dst}")
    trad = argostranslate.translate.get_translation_from_codes(src, dst)
    return trad.translate


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("video")
    p.add_argument("--model", default="auto")
    p.add_argument("--translate", default="non")
    p.add_argument("--out", default="out")
    a = p.parse_args()

    from faster_whisper import WhisperModel

    os.makedirs(a.out, exist_ok=True)
    base = os.path.splitext(os.path.basename(a.video))[0] or "video"
    wav = os.path.join(a.out, base + ".wav")

    # Extraction audio mono 16 kHz (format natif de Whisper)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", a.video,
         "-vn", "-ac", "1", "-ar", "16000", wav],
        check=True,
    )
    total = duree(wav)
    modele = choisir_modele(total) if a.model == "auto" else a.model
    print(f"Durée : {total / 60:.1f} min | Modèle : {modele}", flush=True)

    model = WhisperModel(modele, device="cpu", compute_type="int8",
                         cpu_threads=os.cpu_count() or 2)
    opts = dict(vad_filter=True, beam_size=5, condition_on_previous_text=False)

    # Détection sur plusieurs extraits, restreinte à fr / it / en / ar
    segments, info = model.transcribe(wav, language_detection_segments=4, **opts)
    probas = dict(info.all_language_probs or [])
    langue = max(LANGS, key=lambda l: probas.get(l, 0.0))
    print(f"Langue détectée : {langue} ({probas.get(langue, 0):.0%}) "
          f"[brut : {info.language}]", flush=True)
    if langue != info.language:
        segments, info = model.transcribe(wav, language=langue, **opts)

    segs = []
    last = time.time()
    for seg in segments:
        text = seg.text.strip()
        if text:
            segs.append((seg.start, seg.end, text))
        if time.time() - last > 60:
            print(f"… {seg.end / total:.0%} ({seg.end / 60:.1f} min)", flush=True)
            last = time.time()
    os.remove(wav)
    ecrire(segs, os.path.join(a.out, base))

    cible = a.translate.lower()
    if cible in LANGS and cible != langue:
        print(f"Traduction {langue} -> {cible}…", flush=True)
        trad = preparer_traduction(langue, cible)
        segs_t = [(s, e, trad(t)) for s, e, t in segs]
        ecrire(segs_t, os.path.join(a.out, f"{base}.{cible}"))
    elif cible in LANGS:
        print("Traduction ignorée : la vidéo est déjà dans cette langue.", flush=True)

    print("Terminé.", flush=True)


if __name__ == "__main__":
    main()
