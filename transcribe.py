#!/usr/bin/env python3
"""Transcription 100 % locale d'une vidéo ou d'un audio (Whisper via faster-whisper).

- Langue détectée automatiquement parmi fr / it / en / ar
- Modèle choisi automatiquement selon la durée (ou imposé)
- Écriture au fil de l'eau : rien n'est perdu si le job est coupé
- Traduction optionnelle, locale (Argos Translate), vers fr / it / en / ar
"""
import argparse
import json
import os
import subprocess
import sys
import time

LANGS = ("fr", "it", "en", "ar")


def log(msg: str) -> None:
    print(msg, flush=True)


def erreur(msg: str, code: int = 1) -> None:
    print(f"::error::{msg}", flush=True)
    sys.exit(code)


def ts(t: float) -> str:
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def sonde(path: str) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type", "-of", "json", path],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        erreur(f"Fichier illisible par ffmpeg ({os.path.basename(path)}). "
               f"Ce n'est probablement pas une vidéo : {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


def choisir_modele(sec: float) -> str:
    """Le plus précis possible en restant loin de la limite de 6 h de GitHub."""
    minutes = sec / 60
    if minutes <= 15:
        return "large-v3"
    if minutes <= 75:
        return "medium"
    return "small"


class Sortie:
    """Écrit .txt et .srt segment par segment (flush immédiat)."""

    def __init__(self, base_path: str):
        self.base = base_path
        self.txt = open(base_path + ".txt", "w", encoding="utf-8")
        self.srt = open(base_path + ".srt", "w", encoding="utf-8")
        self.n = 0

    def ajouter(self, start: float, end: float, text: str) -> None:
        self.n += 1
        self.txt.write(text + "\n")
        self.srt.write(f"{self.n}\n{ts(start)} --> {ts(end)}\n{text}\n\n")
        self.txt.flush()
        self.srt.flush()

    def fermer(self) -> None:
        self.txt.close()
        self.srt.close()
        log(f"Écrit : {self.base}.txt et {self.base}.srt ({self.n} segments)")


def preparer_traduction(src: str, dst: str):
    import argostranslate.package
    import argostranslate.translate

    argostranslate.package.update_package_index()
    dispo = argostranslate.package.get_available_packages()
    installes = {(p.from_code, p.to_code)
                 for p in argostranslate.package.get_installed_packages()}

    def installer(f, t):
        if (f, t) in installes:
            return True
        for p in dispo:
            if p.from_code == f and p.to_code == t:
                log(f"Téléchargement du modèle de traduction {f} -> {t}")
                argostranslate.package.install_from_path(p.download())
                return True
        return False

    # Direct si possible, sinon via l'anglais
    if not installer(src, dst):
        if not (installer(src, "en") and installer("en", dst)):
            raise RuntimeError(f"Aucun modèle de traduction {src} -> {dst}")
    return argostranslate.translate.get_translation_from_codes(src, dst).translate


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("video")
    p.add_argument("--model", default="auto")
    p.add_argument("--translate", default="non")
    p.add_argument("--out", default="out")
    a = p.parse_args()

    if not os.path.isfile(a.video) or os.path.getsize(a.video) == 0:
        erreur(f"Fichier d'entrée absent ou vide : {a.video}")

    info_fichier = sonde(a.video)
    if not any(s.get("codec_type") == "audio" for s in info_fichier.get("streams", [])):
        erreur("Ce fichier ne contient aucune piste audio : rien à transcrire.")

    os.makedirs(a.out, exist_ok=True)
    base = os.path.splitext(os.path.basename(a.video))[0] or "video"
    wav = os.path.join(a.out, ".audio.wav")

    log("Extraction de l'audio…")
    r = subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-loglevel", "error", "-i", a.video,
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        erreur(f"Échec de l'extraction audio : {r.stderr.strip()[:500]}")

    from faster_whisper import WhisperModel, decode_audio

    audio = decode_audio(wav)
    os.remove(wav)
    total = len(audio) / 16000
    if total < 1:
        erreur("Audio trop court (moins d'une seconde).")

    modele = choisir_modele(total) if a.model == "auto" else a.model
    log(f"Durée : {total / 60:.1f} min | Modèle : {modele} | CPU : {os.cpu_count()}")

    t0 = time.time()
    model = WhisperModel(modele, device="cpu", compute_type="int8",
                         cpu_threads=os.cpu_count() or 2)
    log(f"Modèle chargé en {time.time() - t0:.0f} s")

    # Détection sur plusieurs extraits de parole, restreinte à fr / it / en / ar
    _, _, probas = model.detect_language(
        audio, vad_filter=True,
        language_detection_segments=8, language_detection_threshold=0.9,
    )
    probas = dict(probas or [])
    langue = max(LANGS, key=lambda l: probas.get(l, 0.0))
    brut = max(probas, key=probas.get) if probas else "?"
    log(f"Langue retenue : {langue} ({probas.get(langue, 0):.0%}) [détection brute : {brut}]")

    segments, _ = model.transcribe(
        audio,
        language=langue,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        beam_size=5,
        condition_on_previous_text=False,   # évite les boucles sur les longues vidéos
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.4,
    )

    sortie = Sortie(os.path.join(a.out, base))
    segs = []
    precedent, repetitions = None, 0
    last = time.time()
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        # Filtre anti-hallucination : même phrase répétée en boucle
        repetitions = repetitions + 1 if text == precedent else 0
        precedent = text
        if repetitions >= 2:
            continue
        sortie.ajouter(seg.start, seg.end, text)
        segs.append((seg.start, seg.end, text))
        if time.time() - last > 60:
            ecoule = time.time() - t0
            fait = max(seg.end / total, 1e-3)
            log(f"… {fait:.0%} ({seg.end / 60:.1f}/{total / 60:.1f} min) "
                f"| reste estimé ≈ {ecoule / fait * (1 - fait) / 60:.0f} min")
            last = time.time()
    sortie.fermer()
    log(f"Transcription terminée en {(time.time() - t0) / 60:.1f} min")

    if not segs:
        erreur("Aucune parole détectée dans ce fichier.")

    cible = a.translate.lower()
    if cible not in LANGS:
        return
    if cible == langue:
        log("Traduction ignorée : la vidéo est déjà dans cette langue.")
        return

    log(f"Traduction {langue} -> {cible}…")
    try:
        trad = preparer_traduction(langue, cible)
        sortie_t = Sortie(os.path.join(a.out, f"{base}.{cible}"))
        for i, (s, e, t) in enumerate(segs, 1):
            sortie_t.ajouter(s, e, trad(t))
            if i % 200 == 0:
                log(f"… traduction {i}/{len(segs)}")
        sortie_t.fermer()
    except Exception as exc:  # la transcription est déjà sauvée
        erreur(f"La transcription est OK mais la traduction a échoué : {exc}", 3)


if __name__ == "__main__":
    main()
