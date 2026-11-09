#!/data/data/com.termux/files/usr/bin/bash
# Installation unique dans Termux :
#   curl -fsSL https://raw.githubusercontent.com/MaxMcNeil/Video-transcriber/main/termux-setup.sh | bash
#
# Ensuite : dans YouTube (ou X, Telegram…) → Partager → Termux. C'est tout.
#   - Entrée (ou 15 s sans rien faire) : lance la transcription
#   - « a » : met le lien en attente pour en ajouter d'autres ; le partage suivant
#     lance tout ensemble, fusionné dans un seul texte prompteur.
set -eu
RAW="https://raw.githubusercontent.com/MaxMcNeil/Video-transcriber/main"

curl -fsSL -o ~/yt2release.sh "$RAW/yt2release.sh"
chmod +x ~/yt2release.sh

mkdir -p ~/bin
cat > ~/bin/termux-url-opener <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
# Appelé par Android quand on partage un lien vers Termux.
RAW="https://raw.githubusercontent.com/MaxMcNeil/Video-transcriber/main"
FILE="$HOME/.yt2release_file"
termux-wake-lock 2>/dev/null || true
curl -fsSL -o ~/yt2release.sh.new "$RAW/yt2release.sh" && mv ~/yt2release.sh.new ~/yt2release.sh || true

lien="$1"
echo "$lien" >> "$FILE"
nb=$(grep -c . "$FILE")
echo "Lien reçu ($nb en attente) : $lien"
echo
echo "Entrée = lancer maintenant   |   a = attendre d'autres liens"
read -r -t 15 choix || choix=""
if [ "$choix" = "a" ] || [ "$choix" = "A" ]; then
  echo "En attente. Partage le lien suivant vers Termux."
  sleep 2
  exit 0
fi
mapfile -t liens < "$FILE"
rm -f "$FILE"
bash ~/yt2release.sh "${liens[@]}"
echo
echo "Terminé. Tu peux fermer Termux (le traitement continue sur GitHub)."
read -r -t 20 _ || true
EOF
chmod +x ~/bin/termux-url-opener

# Raccourci : « yt LIEN1 LIEN2 » au lieu de « bash ~/yt2release.sh … »
grep -q "alias yt=" ~/.bashrc 2>/dev/null || \
  echo "alias yt='termux-wake-lock 2>/dev/null; bash ~/yt2release.sh'" >> ~/.bashrc

echo "Installation terminée."
echo "→ Dans YouTube : Partager → Termux."
echo "→ Ou dans Termux : yt LIEN1 LIEN2"
