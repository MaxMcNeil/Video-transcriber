#!/data/data/com.termux/files/usr/bin/bash
# À lancer dans Termux, pour les liens que YouTube refuse aux serveurs GitHub.
# Télécharge l'audio depuis le téléphone, le dépose dans la Release "videos"
# puis lance la transcription.
#
# Usage : bash yt2release.sh LIEN [non|fr|it|en|ar]
set -euo pipefail
URL="${1:-}"
TRAD="${2:-non}"
REPO="MaxMcNeil/Video-transcriber"
[ -n "$URL" ] || { echo "Usage : bash yt2release.sh LIEN [non|fr|it|en|ar]"; exit 1; }

# Outils nécessaires (installés une seule fois)
manque=()
command -v python >/dev/null || manque+=(python)
command -v ffmpeg >/dev/null || manque+=(ffmpeg)
command -v node   >/dev/null || manque+=(nodejs)
command -v gh     >/dev/null || manque+=(gh)
[ ${#manque[@]} -eq 0 ] || pkg install -y "${manque[@]}"
pip install -q -U "yt-dlp[default]"
gh auth status >/dev/null 2>&1 || gh auth login

TMP="$HOME/.yt2release"
rm -rf "$TMP" && mkdir -p "$TMP"

echo "Téléchargement de l'audio…"
yt-dlp --js-runtimes node --no-playlist --restrict-filenames \
  -f "bestaudio[ext=m4a]/bestaudio/best" --retries 10 \
  -o "$TMP/%(title).60B-%(id)s.%(ext)s" "$URL"

f="$(find "$TMP" -type f | head -n1)"
[ -n "$f" ] || { echo "Aucun fichier téléchargé."; exit 1; }
nom="$(basename "$f")"
echo "Envoi dans la Release 'videos' : $nom ($(du -h "$f" | cut -f1))"
gh release view videos -R "$REPO" >/dev/null 2>&1 || \
  gh release create videos -R "$REPO" -t "Vidéos à transcrire" -n "Dépose ici les vidéos à transcrire."
gh release upload videos "$f" -R "$REPO" --clobber

gh workflow run transcribe.yml -R "$REPO" -f source="$nom" -f translate="$TRAD"
rm -rf "$TMP"
echo "Transcription lancée. Suivi : https://github.com/$REPO/actions"
