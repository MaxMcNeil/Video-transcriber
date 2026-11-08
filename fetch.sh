#!/usr/bin/env bash
# Récupère la vidéo à transcrire dans ./in
#   - vide            : dernière vidéo déposée dans la Release "videos"
#   - nom de fichier  : ce fichier dans la Release "videos" (tolère espaces/majuscules)
#   - lien direct     : téléchargé tel quel (mp4, mp3, wav…)
#   - autre lien      : YouTube, Drive, X, Facebook… via yt-dlp (audio seul)
set -uo pipefail
SRC="${1:-}"
SRC="$(echo "$SRC" | xargs)"   # enlève les espaces parasites
mkdir -p in
REPO="${GITHUB_REPOSITORY:?}"

fail() { echo "::error::$*"; exit 1; }
est_media() { ffprobe -v error -show_entries stream=codec_type -of csv=p=0 "$1" 2>/dev/null | grep -q audio; }

depuis_release() {
  gh release view videos -R "$REPO" >/dev/null 2>&1 || {
    gh release create videos -R "$REPO" -t "Vidéos à transcrire" -n "Dépose ici les vidéos à transcrire." >/dev/null
    fail "La Release 'videos' n'existait pas : je l'ai créée. Dépose ta vidéo dedans puis relance."
  }
  local liste nom cherche
  liste="$(gh api "repos/$REPO/releases/tags/videos" -q '.assets | sort_by(.updated_at) | reverse | .[].name')"
  [ -n "$liste" ] || fail "La Release 'videos' est vide : dépose une vidéo dedans ou donne un lien."
  if [ -z "$SRC" ]; then
    nom="$(echo "$liste" | head -n1)"
    echo "Aucune source donnée : dernière vidéo déposée → $nom"
  else
    # GitHub remplace les espaces par des points dans les noms de fichiers
    cherche="$(echo "$SRC" | tr ' ' '.')"
    nom="$(echo "$liste" | grep -Fx -- "$SRC" | head -n1)"
    [ -n "$nom" ] || nom="$(echo "$liste" | grep -Fxi -- "$cherche" | head -n1)"
    [ -n "$nom" ] || nom="$(echo "$liste" | grep -Fi -- "${cherche%.*}" | head -n1)"
    [ -n "$nom" ] || fail "Fichier « $SRC » introuvable dans la Release 'videos'. Fichiers présents : $(echo "$liste" | paste -sd ',' | sed 's/,/, /g')"
  fi
  local motif; motif="$(printf '%s' "$nom" | sed 's/[][*?\\]/\\&/g')"   # échappe les jokers
  gh release download videos -R "$REPO" -p "$motif" -D in --clobber || fail "Téléchargement de « $nom » impossible."
}

par_ytdlp() {
  local base=(yt-dlp --no-playlist --no-progress -f "bestaudio/best" --retries 10 --fragment-retries 10
              --restrict-filenames -o "in/%(title).80B [%(id)s].%(ext)s")
  [ -s cookies.txt ] && base+=(--cookies cookies.txt) && echo "Cookies YouTube utilisés."
  for client in "" "tv,web_safari" "web_embedded,mweb" "ios,android"; do
    rm -f in/*
    if [ -z "$client" ]; then
      echo "yt-dlp : tentative standard"
      "${base[@]}" "$SRC" && return 0
    else
      echo "yt-dlp : nouvelle tentative (clients $client)"
      "${base[@]}" --extractor-args "youtube:player_client=$client" "$SRC" && return 0
    fi
    sleep 5
  done
  fail "Impossible de télécharger ce lien (YouTube bloque souvent les serveurs GitHub).
Solutions : 1) dépose la vidéo dans la Release 'videos' et lance avec son nom ;
2) ajoute tes cookies YouTube dans le secret YT_COOKIES (voir README)."
}

if [[ -z "$SRC" || "$SRC" != http* ]]; then
  depuis_release
elif [[ "$SRC" =~ (youtube\.com|youtu\.be|drive\.google|x\.com|twitter\.com|facebook\.com|instagram\.com|tiktok\.com|vimeo\.com|dailymotion\.com|rumble\.com|odysee\.com|t\.me) ]]; then
  par_ytdlp
else
  nom="$(basename "${SRC%%\?*}")"; nom="${nom:-video}"
  echo "Lien direct : $nom"
  curl -L --fail --retry 5 --retry-delay 5 -o "in/$nom" "$SRC" || fail "Téléchargement du lien impossible."
  if ! est_media "in/$nom"; then
    echo "Ce lien n'est pas un fichier média direct : essai avec yt-dlp."
    par_ytdlp
  fi
fi

f="$(find in -type f -size +0 | head -n1)"
[ -n "$f" ] || fail "Aucun fichier récupéré."
est_media "$f" || fail "« $(basename "$f") » ne contient pas d'audio exploitable (page web, fichier corrompu ?)."
ls -lh in
echo "FICHIER=$f" >> "$GITHUB_ENV"
