# Video-transcriber

Transcription 100 % locale d'une vidéo ou d'un audio (2 min à 2 h et plus) en `.txt` et `.srt`, via Whisper (faster-whisper) exécuté sur le runner GitHub. Aucune API, aucun service payant.

## Utilisation
Actions → **Transcribe** → Run workflow :
- `source` :
  - **vide** = la dernière vidéo déposée dans la Release **videos**
  - un **nom de fichier** de la Release **videos** (espaces et majuscules tolérés)
  - un **lien** : YouTube, Google Drive, X, Facebook, Telegram… ou lien direct `.mp4/.mp3`
- `translate` : `non` (par défaut), ou `fr`, `it`, `en`, `ar`
- `model` : laisse `auto`

Résultats dans la Release **transcriptions** (et en artefact du run) ; un aperçu du texte s'affiche dans le résumé du run :
- `nom.txt` / `nom.srt` : transcription dans la langue d'origine
- `nom.<langue>.txt` / `.srt` : traduction, si demandée

## Fonctionnement
- **Langue** : détectée sur 8 extraits de parole, limitée à fr, it, en, ar.
- **Modèle auto** : `large-v3` jusqu'à 15 min, `medium` jusqu'à 75 min, `small` au-delà (limite de 6 h de GitHub).
- **Sûreté** : texte écrit au fil de l'eau et publié même si le job échoue ou est coupé ; filtre anti-boucle (hallucinations) ; messages d'erreur clairs.
- **Traduction** : Argos Translate en local (passe par l'anglais si besoin). Si elle échoue, la transcription est quand même publiée.
- Les modèles sont téléchargés au premier lancement puis mis en cache.

## YouTube bloqué ?
YouTube refuse parfois les serveurs GitHub (« confirm you're not a bot »). Deux solutions :
1. Dépose la vidéo dans la Release **videos** et relance (champ `source` vide).
2. Ajoute tes cookies YouTube : exporte-les au format Netscape (extension « Get cookies.txt LOCALLY »), puis Settings → Secrets and variables → Actions → New secret `YT_COOKIES` en collant le contenu du fichier. Utilise de préférence un compte Google secondaire.

Déclenchement manuel uniquement, aucun cron.
