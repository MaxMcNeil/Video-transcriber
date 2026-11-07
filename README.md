# Video-transcriber

Transcription 100 % locale d'une vidéo (2 min à 2 h et plus) en `.txt` et `.srt`, via Whisper (faster-whisper) exécuté sur le runner GitHub. Aucune API, aucun service payant.

## Utilisation
1. Dépose ta vidéo dans la Release **videos** (Releases → videos → Edit → glisser le fichier, 2 Go max).
2. Actions → **Transcribe** → Run workflow :
   - `source` : le nom exact du fichier (ex. `live.mp4`) ou un lien direct `http...`
   - `translate` : `non` (par défaut), ou `fr`, `it`, `en`, `ar`
   - `model` : laisse `auto`
3. Récupère les fichiers dans la Release **transcriptions** (ou en artefact du run) :
   - `nom.txt` / `nom.srt` : transcription dans la langue d'origine
   - `nom.<langue>.txt` / `.srt` : traduction, si demandée

## Fonctionnement
- **Langue** : détectée automatiquement parmi fr, it, en, ar.
- **Modèle auto** : `large-v3` jusqu'à 15 min, `medium` jusqu'à 75 min, `small` au-delà (pour rester sous la limite de 6 h de GitHub).
- **Traduction** : Argos Translate, en local sur le runner (passe par l'anglais si besoin).
- Les modèles sont téléchargés au premier lancement puis mis en cache.

Déclenchement manuel uniquement, aucun cron.
