# Video-transcriber

Transcription 100 % locale d'une ou plusieurs vidéos/audios (2 min à plusieurs heures), puis **texte prompteur prêt à lire face caméra** écrit par une IA locale. Aucune API, aucun service payant : tout tourne sur le runner GitHub.

---

## ▶️ COMMENT LANCER (mode d'emploi)

### 1. Vidéo YouTube → depuis le téléphone
1. Dans l'appli **YouTube**, ouvre la vidéo → **Partager** → **Termux**.
2. Termux affiche la **liste des vidéos en attente**, puis :
   - **Entrée** quand tu as fini (ou 20 secondes sans rien faire) → Termux te **demande la langue du texte prompteur** :
     tape **`ar`**, **`fr`**, **`it`** ou **`en`** puis Entrée (Entrée seule = langue majoritaire des vidéos), et c'est lancé ;
   - **`a` + Entrée** → garde la vidéo et attend la suivante : retourne sur YouTube et partage la vidéo suivante ;
   - **`x` + Entrée** → vide la liste sans rien lancer.
3. Quand Termux affiche **✅ Transcription lancée**, tu peux fermer Termux.

> 💡 **Chaque partage ouvre une NOUVELLE fenêtre Termux : c'est normal.** Les vidéos partagées avant ne sont pas perdues, la nouvelle fenêtre les affiche toutes dans la liste.

> ⚠️ Ne mets **pas** de lien YouTube dans le formulaire GitHub : YouTube bloque les serveurs GitHub.
> Installation à refaire uniquement sur un nouveau téléphone ou après réinstallation de Termux :
> `curl -fsSL https://raw.githubusercontent.com/MaxMcNeil/Video-transcriber/main/termux-setup.sh | bash`

### 2. Fichier vidéo, ou lien non YouTube (Drive, X, Telegram…) → depuis GitHub
1. (Pour un fichier) **Releases → videos → Edit** → ajoute le fichier → **Update release**.
2. **Actions → Transcribe → Run workflow**.
3. Champ **source** : laisse **VIDE**. Le workflow prend tout seul **toutes les vidéos nouvelles** de la Release (jamais transcrites), ensemble dans un seul texte. S'il n'y a rien de nouveau, il reprend le **dernier lot déposé**.
   - Pour un lien non YouTube : colle-le dans source (plusieurs : sépare par **`;`**).
   - `tout` = toute la Release, même déjà transcrite.
4. Laisse les autres champs tels quels → **Run workflow**.

### 3. Récupérer le résultat (≈ 30 min à 2 h selon la durée)
**Releases → transcriptions**, fichiers commençant par la date et l'heure :
- **`…_prompteur.txt`** → à coller dans le prompteur
- `…_fiche.txt` → les faits utilisés (à vérifier avant le live)
- `…_transcription.txt` → tout ce qui a été dit

### 4. Refaire seulement le texte prompteur (sans retranscrire)
Autre langue, autre longueur, ou run coupé avant le prompteur :
**Actions → Prompteur seul → Run workflow** → laisse **transcription** vide (= la plus récente), choisis la **langue** → Run.
Environ 30 à 60 min, quelle que soit la durée des vidéos (les fiches sont préparées sur 4 serveurs en parallèle).

### 5. Si le run est rouge
- Rouge **sans aucune étape** (« startup failure ») → ouvre le run → **Re-run all jobs**.
- Rouge sur une étape → envoie-moi la capture ou le lien du run.

---

## Utilisation
Actions → **Transcribe** → Run workflow :

| Champ | Valeurs |
|---|---|
| `source` | vide = toutes les vidéos **nouvelles** de la Release **videos** (sinon le dernier lot) · `tout` = toute la Release · ou une liste séparée par `;` de liens (YouTube, Drive, X, Telegram, lien direct…) et/ou de noms de fichiers de la Release |
| `prompteur` | `auto` (langue des sources) · `fr` · `ar` · `it` · `en` · `non` |
| `duree_prompteur` | `auto` · `court` (~3 min) · `moyen` (~7 min) · `long` (~13 min) |
| `ia` | `qwen3-8b` (défaut) · `mistral-nemo-12b` — l'autre sert de secours automatique |
| `translate` | traduction de la transcription complète : `non` · `fr` · `it` · `en` · `ar` |
| `model` | laisse `auto` |

Exemple de `source` : `https://youtu.be/xxx ; interview.mp4 ; https://x.com/.../status/123`

## Résultats (Release **transcriptions**, préfixe date + titre)
- `…_transcription.txt` : **toutes les sources dans un seul fichier**, paragraphes horodatés
- `…_prompteur.txt` : **le texte à coller dans le prompteur**
- `…_fiche.txt` : la fiche de faits sur laquelle l'IA s'est appuyée (pour vérifier avant le live)
- `…_transcription.<langue>.txt` : traduction, si demandée
- `…_sous-titres_NN.srt` : sous-titres de chaque source

Le résumé du run affiche le début du prompteur et de la transcription.

## Le texte prompteur
- Les sources sont fusionnées en **une seule histoire fluide**, regroupée par idées.
- Interviews et débats deviennent **ton propre propos**, comme si tu parlais en live.
- Musique, chansons, jingles, intros/outros, pubs, présentations d'invités : ignorés.
- Ouverture par une **citation célèbre** choisie selon le sujet, tirée d'une banque de citations **vérifiées** (`citations.json`, en fr/ar/it/en) : jamais de citation inventée.
- Structure rétention : citation → **hook ultra-choc** → promesse → blocs avec relances et **décryptage stratégique** (qui gagne, qui perd, rapports de force) → appel à liker → **scénario de la suite** (présenté comme une analyse) → chute + abonnement.
- Ton : **alarme et indignation** + **stratège géopolitique** + **humour noir** en contrepoint ; phrases courtes, aucune autocensure ; aucun fait inventé.
- Texte brut : pas de titres, pas de markdown, un paragraphe par idée.
- Nom de la chaîne cité dans l'appel à l'abonnement selon la langue : modifiable dans `config.json` (ajouté automatiquement si l'IA l'oublie).
- **Arabe** : l'IA prépare la fiche et écrit le texte **en anglais** (sa langue la plus forte), puis le **traduit en arabe standard** paragraphe par paragraphe (noms en écriture arabe, contrôle automatique). La version anglaise est aussi publiée (`…_prompteur.en.txt`). Liste des langues concernées : `langues_pivot` dans `config.json`.

## Fiabilité
- Une source qui échoue n'arrête pas les autres ; la liste des échecs apparaît dans le résumé.
- Transcription écrite au fil de l'eau et **publiée même si une étape suivante échoue** ou si le job est coupé.
- Filtres anti-hallucinations Whisper (« Amara.org », « ♪ », boucles).
- IA : si l'une refuse ou échoue, bascule automatique sur l'autre. Moteur llama.cpp officiel, avec compilation de secours.
- Transcription : chaque vidéo est découpée en tranches d'environ 25 min (coupées sur des silences), transcrites **en parallèle** sur des serveurs séparés avec le modèle le plus précis (`large-v3`), puis recollées. Une tranche en échec est signalée dans le texte sans bloquer le reste.

## YouTube bloqué ?
YouTube refuse souvent les serveurs GitHub (« confirm you're not a bot »). Solutions :
1. **Depuis le téléphone (recommandé, pour tout lien YouTube)** — installation unique dans Termux :
   ```bash
   curl -fsSL https://raw.githubusercontent.com/MaxMcNeil/Video-transcriber/main/termux-setup.sh | bash
   ```
   Ensuite, dans YouTube : **Partager → Termux**. C'est tout.
   - Entrée (ou 20 s) : lance la transcription de toute la liste affichée.
   - `a` : garde le lien en attente ; chaque partage ouvre une nouvelle fenêtre qui montre toute la liste.
   - `x` : vide la liste.
   - Termux reste actif écran éteint (wake-lock), et le script se met à jour tout seul à chaque partage.
   - Raccourci clavier possible : `yt LIEN1 LIEN2` (options : `PROMPTEUR=ar DUREE=court yt LIEN`).
2. Dépose toi-même la vidéo dans la Release **videos** et lance avec `source` vide.
3. Ajoute tes cookies YouTube : exporte-les au format Netscape (extension « Get cookies.txt LOCALLY »), puis Settings → Secrets and variables → Actions → secret `YT_COOKIES`. Utilise de préférence un compte Google secondaire.

Déclenchement manuel uniquement, aucun cron.
