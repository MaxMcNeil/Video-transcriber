#!/data/data/com.termux/files/usr/bin/bash
# Installation unique dans Termux :
#   curl -fsSL https://raw.githubusercontent.com/MaxMcNeil/Video-transcriber/main/termux-setup.sh | bash
#
# Ensuite : dans YouTube (ou X, Telegram…) → Partager → Termux. C'est tout.
#   - Entrée (ou 20 s sans rien faire) : lance la transcription
#   - « a » : met le lien en attente ; chaque partage ouvre une nouvelle fenêtre
#     qui affiche toute la liste, Entrée lance tout ensemble.
set -eu
RAW="https://raw.githubusercontent.com/MaxMcNeil/Video-transcriber/main"

curl -fsSL -o ~/yt2release.sh "$RAW/yt2release.sh"
chmod +x ~/yt2release.sh

mkdir -p ~/bin
curl -fsSL -o ~/bin/termux-url-opener "$RAW/termux-url-opener"
chmod +x ~/bin/termux-url-opener

# Raccourci : « yt LIEN1 LIEN2 » au lieu de « bash ~/yt2release.sh … »
grep -q "alias yt=" ~/.bashrc 2>/dev/null || \
  echo "alias yt='termux-wake-lock 2>/dev/null; bash ~/yt2release.sh'" >> ~/.bashrc

echo "Installation terminée."
echo "→ Dans YouTube : Partager → Termux."
echo "→ Ou dans Termux : yt LIEN1 LIEN2"
