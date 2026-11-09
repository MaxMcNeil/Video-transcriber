# Video-transcriber

Transcription 100 % locale d'une ou plusieurs vidéos/audios (2 min à plusieurs heures), puis **texte prompteur prêt à lire face caméra** écrit par une IA locale. Aucune API, aucun service payant : tout tourne sur le runner GitHub.

## Utilisation
Actions → **Transcribe** → Run workflow :

| Champ | Valeurs |
|---|---|
| `source` | vide = dernière vidéo de la Release **videos** · `tout` = toute la Release · ou une liste séparée par `;` de liens (YouTube, Drive, X, Telegram, lien direct…) et/ou de noms de fichiers de la Release |
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
- Structure rétention : **hook choc** → promesse → blocs avec relances → appel à liker → chute + abonnement.
- Ton moqueur, humour noir, phrases courtes, aucune autocensure ; aucun fait inventé.
- Texte brut : pas de titres, pas de markdown, un paragraphe par idée.
- Nom de la chaîne cité dans l'appel à l'abonnement selon la langue : modifiable dans `config.json`.

## Fiabilité
- Une source qui échoue n'arrête pas les autres ; la liste des échecs apparaît dans le résumé.
- Transcription écrite au fil de l'eau et **publiée même si une étape suivante échoue** ou si le job est coupé.
- Filtres anti-hallucinations Whisper (« Amara.org », « ♪ », boucles).
- IA : si l'une refuse ou échoue, bascule automatique sur l'autre. Moteur llama.cpp officiel, avec compilation de secours.
- Modèle de transcription adapté à la durée totale : `large-v3` ≤ 40 min, `large-v3-turbo` ≤ 2 h 30, `small` au-delà.

## YouTube bloqué ?
YouTube refuse souvent les serveurs GitHub (« confirm you're not a bot »). Solutions :
1. **Depuis le téléphone (recommandé)**, dans Termux :
   ```bash
   curl -fsSLO https://raw.githubusercontent.com/MaxMcNeil/Video-transcriber/main/yt2release.sh
   bash yt2release.sh "LIEN_YOUTUBE"
   ```
   Le téléphone télécharge l'audio seul, le dépose dans la Release **videos** et lance le workflow.
2. Dépose toi-même la vidéo dans la Release **videos** et lance avec `source` vide.
3. Ajoute tes cookies YouTube : exporte-les au format Netscape (extension « Get cookies.txt LOCALLY »), puis Settings → Secrets and variables → Actions → secret `YT_COOKIES`. Utilise de préférence un compte Google secondaire.

Déclenchement manuel uniquement, aucun cron.
