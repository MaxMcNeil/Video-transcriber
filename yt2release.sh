#!/data/data/com.termux/files/usr/bin/bash
# À lancer dans Termux, pour les liens que YouTube refuse aux serveurs GitHub.
# Télécharge l'audio de chaque lien depuis le téléphone, le dépose dans la
# Release "videos", puis lance UNE transcription qui fusionne toutes les sources.
#
# Installation (une seule fois) :
#   curl -fsSL -o ~/yt2release.sh https://raw.githubusercontent.com/MaxMcNeil/Video-transcriber/main/yt2release.sh
# Usage : bash ~/yt2release.sh LIEN [LIEN2 …]
# Options (variables) : PROMPTEUR=auto|fr|ar|it|en|non  DUREE=auto|court|moyen|long
set -uo pipefail

# Garde Termux actif écran éteint (sinon Android coupe les longs téléchargements)
if command -v termux-wake-lock >/dev/null; then
  termux-wake-lock
  trap 'termux-wake-unlock 2>/dev/null' EXIT
fi
REPO="MaxMcNeil/Video-transcriber"
# tient aussi à jour le script de partage Android (installé par termux-setup.sh)
if [ -d "$HOME/bin" ]; then
  curl -fsSL -o "$HOME/bin/termux-url-opener.new" \
    "https://raw.githubusercontent.com/$REPO/main/termux-url-opener" 2>/dev/null \
    && chmod +x "$HOME/bin/termux-url-opener.new" \
    && mv "$HOME/bin/termux-url-opener.new" "$HOME/bin/termux-url-opener" || rm -f "$HOME/bin/termux-url-opener.new"
fi
PROMPTEUR="${PROMPTEUR:-auto}"
DUREE="${DUREE:-auto}"
[ $# -gt 0 ] || { echo "Usage : bash yt2release.sh LIEN [LIEN2 …]"; exit 1; }

# Outils nécessaires (installés une seule fois)
manque=()
command -v python >/dev/null || manque+=(python)
command -v ffmpeg >/dev/null || manque+=(ffmpeg)
command -v node   >/dev/null || manque+=(nodejs)
command -v gh     >/dev/null || manque+=(gh)
[ ${#manque[@]} -eq 0 ] || pkg install -y "${manque[@]}"
pip install -q -U "yt-dlp[default]"
gh auth status >/dev/null 2>&1 || gh auth login

gh release view videos -R "$REPO" >/dev/null 2>&1 || \
  gh release create videos -R "$REPO" -t "Vidéos à transcrire" -n "Dépose ici les vidéos à transcrire."

TMP="$HOME/.yt2release"
noms=()
n=0
for URL in "$@"; do
  n=$((n + 1))
  rm -rf "$TMP" && mkdir -p "$TMP"
  echo "[$n/$#] Téléchargement de l'audio : $URL"
  if ! yt-dlp --js-runtimes node --no-playlist --restrict-filenames \
       -f "bestaudio[ext=m4a]/bestaudio/best" --retries 10 \
       -o "$TMP/yt-%(title).50B-%(id)s.%(ext)s" "$URL"; then
    echo "⚠️  Échec pour ce lien, je passe au suivant."
    continue
  fi
  f="$(find "$TMP" -type f | head -n1)"
  [ -n "$f" ] || { echo "⚠️  Aucun fichier pour ce lien."; continue; }
  nom="$(basename "$f")"
  echo "Envoi dans la Release 'videos' : $nom ($(du -h "$f" | cut -f1))"
  if gh release upload videos "$f" -R "$REPO" --clobber; then
    noms+=("$nom")
  else
    echo "⚠️  Envoi impossible pour $nom."
  fi
done
rm -rf "$TMP"

[ ${#noms[@]} -gt 0 ] || { echo "Aucune source envoyée : rien à lancer."; exit 1; }
liste="$(printf '%s ; ' "${noms[@]}")"
liste="${liste% ; }"
# Lancement + vérification que GitHub a vraiment démarré le run
# (en cas de rejet au démarrage — « startup failure » — relance automatique)
lancer() {
  gh workflow run transcribe.yml -R "$REPO" -f source="$liste" \
    -f prompteur="$PROMPTEUR" -f duree_prompteur="$DUREE"
}
avant="$(gh run list -R "$REPO" -w transcribe.yml -L 1 --json databaseId -q '.[0].databaseId' 2>/dev/null)"
for essai in 1 2 3; do
  if ! lancer; then
    echo "⚠️  Lancement refusé (essai $essai), nouvel essai dans 20 s…"; sleep 20; continue
  fi
  id=""
  for _ in $(seq 1 12); do
    sleep 5
    id="$(gh run list -R "$REPO" -w transcribe.yml -L 1 --json databaseId -q '.[0].databaseId' 2>/dev/null)"
    [ -n "$id" ] && [ "$id" != "$avant" ] && break
  done
  if [ -z "$id" ] || [ "$id" = "$avant" ]; then
    echo "⚠️  Run introuvable (essai $essai), nouvel essai…"; continue
  fi
  sleep 20
  etat="$(gh run view "$id" -R "$REPO" --json status,conclusion -q '.status+" "+.conclusion' 2>/dev/null)"
  case "$etat" in
    *startup_failure*)
      echo "⚠️  GitHub a rejeté le run au démarrage ($etat), relance automatique…"
      avant="$id"; sleep 15; continue ;;
  esac
  echo "✅ Transcription lancée pour ${#noms[@]} source(s) : https://github.com/$REPO/actions/runs/$id"
  exit 0
done
echo "❌ GitHub refuse de démarrer le run après 3 essais. Réessaie plus tard (les fichiers sont déjà dans la Release 'videos' :"
echo "   lance « Transcribe » depuis Actions avec source = $liste)"
exit 1
