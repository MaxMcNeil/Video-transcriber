#!/usr/bin/env bash
# Récupère une ou plusieurs sources dans ./in/NN/ et écrit in/sources.tsv (chemin<TAB>titre)
#
# Syntaxe de SOURCE :
#   (vide)            dernière vidéo déposée dans la Release "videos"
#   tout              toutes les vidéos de la Release "videos" (ordre de dépôt)
#   a ; b ; c         plusieurs sources séparées par ";" (liens et/ou noms de fichiers)
#   lien1 lien2       plusieurs liens séparés par des espaces
# Chaque élément : nom de fichier de la Release, lien direct, ou lien YouTube/Drive/X…
# Si une source échoue, les autres continuent ; échec seulement si aucune n'a marché.
set -uo pipefail
SRC="${1:-}"
REPO="${GITHUB_REPOSITORY:?}"
rm -rf in && mkdir -p in
: > in/sources.tsv
: > in/echecs.txt

trim() { local s="$1"; s="${s#"${s%%[![:space:]]*}"}"; s="${s%"${s##*[![:space:]]}"}"; printf '%s' "$s"; }
est_media() { ffprobe -v error -show_entries stream=codec_type -of csv=p=0 "$1" 2>/dev/null | grep -q audio; }
warn() { echo "::warning::$*"; }

LISTE_RELEASE=""
liste_release() {
  if [ -z "$LISTE_RELEASE" ]; then
    if ! gh release view videos -R "$REPO" >/dev/null 2>&1; then
      gh release create videos -R "$REPO" -t "Vidéos à transcrire" \
        -n "Dépose ici les vidéos à transcrire." >/dev/null 2>&1 || true
      return 1
    fi
    # ordre de dépôt : du plus ancien au plus récent
    LISTE_RELEASE="$(gh api "repos/$REPO/releases/tags/videos" \
      -q '.assets | sort_by(.created_at) | .[].name')"
  fi
  [ -n "$LISTE_RELEASE" ]
}

# $1 = nom demandé (tolère espaces, majuscules, extension absente)
trouver_nom() {
  local src="$1" cherche nom
  cherche="$(printf '%s' "$src" | tr ' ' '.')"
  nom="$(printf '%s\n' "$LISTE_RELEASE" | grep -Fx -- "$src" | head -n1)"
  [ -n "$nom" ] || nom="$(printf '%s\n' "$LISTE_RELEASE" | grep -Fxi -- "$cherche" | head -n1)"
  [ -n "$nom" ] || nom="$(printf '%s\n' "$LISTE_RELEASE" | grep -Fi -- "${cherche%.*}" | head -n1)"
  printf '%s' "$nom"
}

# $1 = nom exact dans la Release, $2 = dossier
depuis_release() {
  local motif
  motif="$(printf '%s' "$1" | sed 's/[][*?\\]/\\&/g')"   # échappe les jokers
  gh release download videos -R "$REPO" -p "$motif" -D "$2" --clobber
}

# $1 = lien, $2 = dossier
par_ytdlp() {
  local url="$1" d="$2"
  local base=(yt-dlp --no-playlist --no-progress -f "bestaudio/best" --retries 10
              --fragment-retries 10 --restrict-filenames -o "$d/%(title).80B.%(ext)s")
  [ -s cookies.txt ] && base+=(--cookies cookies.txt)
  for client in "" "tv,web_safari" "web_embedded,mweb" "ios,android"; do
    rm -f "$d"/*
    if [ -z "$client" ]; then
      "${base[@]}" "$url" && return 0
    else
      echo "yt-dlp : nouvelle tentative (clients $client)"
      "${base[@]}" --extractor-args "youtube:player_client=$client" "$url" && return 0
    fi
    sleep 5
  done
  return 1
}

# $1 = lien, $2 = dossier
par_lien() {
  local url="$1" d="$2" nom
  if [[ "$url" =~ (youtube\.com|youtu\.be|drive\.google|x\.com|twitter\.com|facebook\.com|fb\.watch|instagram\.com|tiktok\.com|vimeo\.com|dailymotion\.com|rumble\.com|odysee\.com|t\.me|twitch\.tv) ]]; then
    par_ytdlp "$url" "$d"; return
  fi
  nom="$(basename "${url%%\?*}")"; nom="${nom:-video}"
  if curl -L --fail --retry 5 --retry-delay 5 -sS -o "$d/$nom" "$url" && est_media "$d/$nom"; then
    return 0
  fi
  echo "Pas un fichier média direct : essai avec yt-dlp."
  par_ytdlp "$url" "$d"
}

# ---- Construction de la liste des éléments ----
SRC="$(trim "$SRC")"
items=()
case "${SRC,,}" in
  "")
    if liste_release; then items+=("$(printf '%s\n' "$LISTE_RELEASE" | tail -n1)")
    else echo "::error::Release 'videos' vide ou absente : dépose une vidéo dedans ou donne un lien."; exit 1; fi ;;
  tout|all|"*")
    if liste_release; then mapfile -t items <<< "$LISTE_RELEASE"
    else echo "::error::Release 'videos' vide ou absente."; exit 1; fi ;;
  *)
    IFS=';' read -ra morceaux <<< "${SRC//$'\n'/;}"
    for m in "${morceaux[@]}"; do
      m="$(trim "$m")"; [ -n "$m" ] || continue
      # plusieurs liens collés avec des espaces dans un même morceau
      if [[ "$m" == http* && "$m" =~ [[:space:]] ]]; then
        for tok in $m; do items+=("$tok"); done
      else
        items+=("$m")
      fi
    done ;;
esac
[ ${#items[@]} -gt 0 ] || { echo "::error::Aucune source."; exit 1; }
echo "Sources demandées : ${#items[@]}"

# ---- Téléchargement de chaque élément ----
n=0
for it in "${items[@]}"; do
  n=$((n + 1))
  d="in/$(printf '%02d' "$n")"; mkdir -p "$d"
  echo "::group::Source $n : $it"
  ok=1
  if [[ "$it" == http* ]]; then
    par_lien "$it" "$d" || ok=0
  else
    if liste_release; then
      nom="$(trouver_nom "$it")"
      if [ -n "$nom" ]; then depuis_release "$nom" "$d" || ok=0
      else warn "« $it » introuvable dans la Release 'videos'. Présents : $(printf '%s\n' "$LISTE_RELEASE" | paste -sd ',' | sed 's/,/, /g')"; ok=0; fi
    else ok=0; fi
  fi
  f="$(find "$d" -type f -size +0 | head -n1)"
  if [ "$ok" = 1 ] && [ -n "$f" ] && est_media "$f"; then
    titre="$(basename "${f%.*}" | tr '._' '  ' | sed 's/  */ /g')"
    printf '%s\t%s\n' "$f" "$titre" >> in/sources.tsv
    ls -lh "$f"
  else
    warn "Source $n ignorée (téléchargement impossible ou pas d'audio) : $it"
    printf '%s\n' "$it" >> in/echecs.txt
    rm -rf "$d"
  fi
  echo "::endgroup::"
done

nb=$(wc -l < in/sources.tsv)
echo "Sources récupérées : $nb / ${#items[@]}"
if [ "$nb" -eq 0 ]; then
  echo "::error::Aucune source exploitable. YouTube bloqué ? Utilise yt2release.sh depuis Termux (voir README) ou le secret YT_COOKIES."
  exit 1
fi
if grep -qE 'youtu' in/echecs.txt 2>/dev/null; then
  warn "YouTube a bloqué au moins un lien : utilise yt2release.sh depuis Termux pour ceux-là."
fi
exit 0
