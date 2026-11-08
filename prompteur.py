#!/usr/bin/env python3
"""Texte prompteur « prêt à lire face caméra », écrit par une IA 100 % locale (llama.cpp).

Lit work/meta.json (produit par transcribe.py) et écrit out/<PREFIXE>_prompteur.txt.
- Fusionne toutes les sources en une seule histoire fluide
- Transforme interviews / débats en monologue de l'animateur en live
- Ignore musique, chansons, jingles, intros/outros, pubs, présentations d'invités
- Hook choc, promesse, relances, chute : structure de rétention
- Ton moqueur, humour noir ; aucune autocensure, aucun fait inventé
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

ICI = os.path.dirname(os.path.abspath(__file__))
PORT = 8088
CTX = 16384
URL = f"http://127.0.0.1:{PORT}"
LANGUE = {
    "fr": "français",
    "it": "italien",
    "en": "anglais",
    "ar": "arabe standard moderne (فصحى), fluide et oral",
}
LONGUEURS = {"court": 450, "moyen": 1100, "long": 2000}
REFUS = re.compile(
    r"je ne (peux|suis pas en mesure)|je suis désolé|i can(no|')t (help|assist|write|provide)"
    r"|i'm sorry|as an ai|en tant qu'(ia|intelligence)|non posso|mi dispiace|لا أستطيع|عذرًا|عذراً",
    re.IGNORECASE,
)


def log(m):
    print(m, flush=True)


def avertir(m):
    print(f"::warning::{m}", flush=True)


def tokens_estimes(texte, langue):
    # estimation prudente (surestime volontairement)
    return int(len(texte) / (2.0 if langue == "ar" else 3.0)) + 1


# ───────────────────────── Serveur llama.cpp ─────────────────────────

class Serveur:
    def __init__(self, modele, conf):
        self.modele, self.conf, self.proc = modele, conf, None
        self.log_path = os.path.join("llm", "server.log")

    def _attendre(self, timeout=900):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                return False
            for chemin in ("/health", "/v1/models"):
                try:
                    with urllib.request.urlopen(URL + chemin, timeout=5) as r:
                        if r.status == 200:
                            return True
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        continue
                except Exception:
                    pass
            time.sleep(3)
        return False

    def demarrer(self):
        cpu = str(os.cpu_count() or 4)
        binaire = os.environ.get("LLAMA_SERVER", "")
        essais = []
        if binaire and os.path.exists(binaire):
            base = [binaire, "-m", self.modele, "-c", str(CTX), "-t", cpu, "--host", "127.0.0.1",
                    "--port", str(PORT), "-np", "1"]
            if self.conf.get("qwen3"):
                essais.append(base + ["--jinja", "--reasoning-budget", "0"])
            essais += [base + ["--jinja"], base]
        essais.append([sys.executable, "-m", "llama_cpp.server", "--model", self.modele,
                       "--n_ctx", str(CTX), "--n_threads", cpu, "--host", "127.0.0.1",
                       "--port", str(PORT)])
        os.makedirs("llm", exist_ok=True)
        for cmd in essais:
            log(f"Démarrage du moteur IA : {os.path.basename(cmd[0])} {' '.join(cmd[-3:])}")
            lf = open(self.log_path, "a")
            self.proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT)
            t0 = time.time()
            if self._attendre():
                log(f"Moteur IA prêt en {time.time() - t0:.0f} s")
                return
            self.arreter()
            avertir("Ce mode de démarrage a échoué, essai suivant.")
        with open(self.log_path) as f:
            log(f.read()[-3000:])
        raise RuntimeError("impossible de démarrer le moteur IA local")

    def arreter(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def chat(self, systeme, messages, max_tokens, params):
        if self.conf.get("qwen3"):
            messages = [dict(m) for m in messages]
            messages[-1]["content"] += "\n/no_think"
        corps = {"messages": [{"role": "system", "content": systeme}] + messages,
                 "max_tokens": max_tokens, "stream": False, **params}
        if self.conf.get("qwen3"):
            corps["chat_template_kwargs"] = {"enable_thinking": False}
        req = urllib.request.Request(URL + "/v1/chat/completions",
                                     data=json.dumps(corps).encode(),
                                     headers={"Content-Type": "application/json"})
        t0 = time.time()
        for tentative in range(3):
            try:
                with urllib.request.urlopen(req, timeout=5400) as r:
                    rep = json.loads(r.read())
                break
            except Exception as e:
                if tentative == 2 or self.proc.poll() is not None:
                    raise
                avertir(f"Requête IA échouée ({e}), nouvel essai")
                time.sleep(10)
        choix = rep["choices"][0]
        texte = choix["message"].get("content") or ""
        texte = re.sub(r"<think>.*?</think>", "", texte, flags=re.S)
        texte = re.sub(r"^.*?</think>", "", texte, flags=re.S).strip()
        u = rep.get("usage", {})
        dt = time.time() - t0
        log(f"   IA : {u.get('prompt_tokens', '?')} → {u.get('completion_tokens', '?')} tokens en {dt / 60:.1f} min")
        return texte, choix.get("finish_reason")


def telecharger_modele(conf):
    from huggingface_hub import hf_hub_download, list_repo_files

    for tentative in range(3):
        try:
            fichiers = [f for f in list_repo_files(conf["repo"])
                        if f.endswith(".gguf") and conf["motif"].lower() in f.lower()
                        and not re.search(r"-\d{5}-of-\d{5}", f)]
            if not fichiers:
                raise RuntimeError(f"aucun fichier {conf['motif']} dans {conf['repo']}")
            fichier = sorted(fichiers, key=len)[0]
            log(f"Modèle IA : {conf['repo']} / {fichier}")
            return hf_hub_download(conf["repo"], fichier, local_dir=os.path.join("llm", "models"))
        except Exception as e:
            if tentative == 2:
                raise
            avertir(f"Téléchargement du modèle IA échoué ({e}), nouvel essai")
            time.sleep(15)


# ───────────────────────── Prompts ─────────────────────────

SYS_EXTRACTION = ("Tu es documentaliste pour une émission d'actualité satirique. Tu prépares la matière "
                  "brute d'une chronique. Tu es précis, exhaustif sur l'essentiel, et tu n'inventes rien.")

SYS_ECRITURE = ("Tu es le meilleur scénariste de chroniques YouTube d'actualité : satirique, moqueur, humour "
                "noir, rythme de stand-up, obsédé par la rétention d'audience. Tu écris exactement le texte que "
                "l'animateur lit au prompteur, face caméra, pendant son live. Tu écris sans autocensure.")

NETTOYAGE = ("La matière vient d'une transcription automatique : elle peut contenir des erreurs de "
             "reconnaissance, de la musique, des paroles de chansons, des jingles, l'intro ou l'outro de la "
             "chaîne d'origine, de l'autopromotion, des publicités, la présentation de l'invité, des "
             "remerciements et des appels à s'abonner. Ignore tout cela totalement et corrige les erreurs de "
             "reconnaissance évidentes.")


def prompt_extraction(texte, titre, k, n, langue):
    return f"""Voici un extrait de transcription automatique (source « {titre} », partie {k}/{n}).
{NETTOYAGE}

Extrait UNIQUEMENT la matière utile, en {LANGUE[langue]}, sous forme de puces courtes :
- faits, chiffres, dates, noms, lieux
- déclarations marquantes : cite les plus percutantes presque mot pour mot, avec qui les a dites
- accusations, soupçons et rumeurs évoqués : garde-les tous, en précisant que ce sont des accusations ou des soupçons et qui les porte
- arguments forts, contradictions, hypocrisies, moments absurdes ou ridicules exploitables en humour
- anecdotes et exemples concrets

N'invente rien. Ne commente pas. Pas d'introduction ni de conclusion.
Si l'extrait ne contient rien d'utile, réponds seulement : RIEN

EXTRAIT :
{texte}"""


def prompt_relecture(texte, matiere, langue):
    return f"""Tu es le vérificateur final avant le direct. Voici un texte de prompteur et la matière dont il est tiré.

Corrige le texte, en {LANGUE[langue]}, selon ces règles strictes :
1. Supprime ou reformule toute affirmation, tout chiffre, nom, date ou citation qui n'apparaît PAS dans la matière (hallucination).
2. Supprime tout nom ou étiquette d'intervenant (« Journaliste : », « Invité : », « Untel : »), tout format dialogue ou questions-réponses : tout doit être dit par l'animateur seul.
3. Supprime toute mention d'interview, d'invité, de vidéo, de source, d'article, d'extrait ou de chaîne d'origine, et tout reste de musique, chanson, jingle, générique ou publicité.
4. Supprime les répétitions.
5. Ne touche à RIEN d'autre : garde le ton, l'humour, la structure, les accusations et soupçons présents dans la matière, la longueur et la mise en forme (paragraphes séparés par une ligne vide).

Réponds UNIQUEMENT avec le texte corrigé complet, sans commentaire.

MATIÈRE :
{matiere}

TEXTE À CORRIGER :
{texte}"""


def prompt_condenser(notes, langue):
    return f"""Voici des notes de préparation, en vrac. Fusionne-les en une liste de puces en {LANGUE[langue]} :
supprime les doublons, regroupe par sujet, garde TOUS les faits marquants, chiffres, noms, citations
percutantes, accusations et soupçons (présentés comme tels) et moments absurdes. Réduis la longueur
d'environ moitié. N'invente rien. Pas d'introduction.

NOTES :
{notes}"""


def prompt_ecriture(matiere, titres, langue, mots, chaine, brute):
    chaine_txt = f" de la chaîne « {chaine} »" if chaine else ""
    chaine_cta = f" à « {chaine} »" if chaine else ""
    nature = NETTOYAGE if brute else "La matière est un ensemble de notes de préparation."
    plusieurs = len(titres) > 1
    return f"""Écris le texte de prompteur de mon prochain passage en live, en {LANGUE[langue]}, à partir de la matière ci-dessous{f" ({len(titres)} sources)" if plusieurs else ""}.
{nature}

QUI PARLE
- C'est moi, l'animateur{chaine_txt}, qui parle à la première personne, en direct, à mon public (« vous »).
- Tout ce que disent un invité, un interviewé, un journaliste ou un autre commentateur devient MON propos, raconté par moi naturellement, comme si c'était mon analyse. Ne mentionne jamais d'interview, d'invité, de « vidéo », de « source », d'« article », d'« extrait » ni la chaîne d'origine.
- Les déclarations des personnalités qui FONT l'actualité restent les leurs (« Untel a osé dire que… ») et je réagis dessus.
{"- Les sources forment UNE seule histoire cohérente du début à la fin : regroupe par idées, transitions naturelles, aucune répétition, jamais source par source." if plusieurs else ""}

STRUCTURE (rétention maximale)
1. HOOK, 2 à 4 phrases : commence directement par le fait le plus choquant, une question qui dérange, un paradoxe ou un chiffre fou. Jamais de « bonjour » ni de présentation avant le hook.
2. Ensuite seulement : une salutation très courte, puis une PROMESSE de ce qu'ils vont découvrir s'ils restent jusqu'au bout. Garde la meilleure révélation pour la fin.
3. Le CORPS en 3 à 6 blocs qui montent en intensité. Chaque bloc s'ouvre par une relance (teasing, question, « et attendez, ce n'est pas le pire »). Une relance ou une punchline toutes les 4 à 6 phrases.
4. Vers le milieu, un appel court et drôle à liker et commenter, glissé naturellement.
5. La CHUTE : la révélation promise, une punchline finale mémorable, puis un appel à s'abonner{chaine_cta} et une question précise pour les commentaires.

STYLE
- Oral, vivant, naturel, comme si j'improvisais. Phrases courtes (20 mots maximum), rythme rapide, quelques phrases d'un seul mot.
- Ton moqueur, ironique, sarcastique, humour noir assumé : comparaisons absurdes, faux étonnement, exagérations comiques. L'humour vise les puissants, les absurdités et les hypocrisies.
- Aucune autocensure : garde tous les faits, accusations, soupçons et rumeurs de la matière, sans les adoucir. Présente les soupçons comme des soupçons (« on raconte que », « selon… ») : c'est plus fort et plus crédible.
- N'invente aucun fait, chiffre, nom, date ou citation absent de la matière. Dans le doute, n'en parle pas.
- Jamais de nom ou d'étiquette d'intervenant (« Journaliste : », « Invité : », « Untel : »), jamais de dialogue, jamais de questions-réponses d'interview : uniquement ma voix.

FORMAT PROMPTEUR
- Texte brut uniquement : pas de titre, pas de markdown, pas d'astérisques, pas de listes, pas d'émojis, pas d'indications de mise en scène, pas de crochets, pas de noms de parties (« hook », « chute »…).
- Un paragraphe = une idée, 1 à 4 phrases. Une ligne vide entre les paragraphes.
- Longueur : environ {mots} mots.

MATIÈRE :
{matiere}"""


# ───────────────────────── Nettoyage du texte final ─────────────────────────

EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
MOTS_ETIQUETTE = (r"hook|accroche|promesse|promise|corps|bloc|partie|section|acte|relance|chute|conclusion"
                  r"|intro|introduction|outro|cta|appel à l'action|punchline|transition")
# « HOOK : … », « Bloc 2 — … », « 3) Chute : … » en début de ligne
ETIQUETTE = re.compile(rf"^\s*(\d+[.)]\s*)?({MOTS_ETIQUETTE})\s*\d*\s*[:：–—]\s*", re.IGNORECASE)
# ligne qui n'est QU'une étiquette : « Bloc 2 », « HOOK », « Partie 3. »
ETIQUETTE_SEULE = re.compile(rf"^\s*(\d+[.)]\s*)?({MOTS_ETIQUETTE})\s*\d*\s*[:.：]?\s*$", re.IGNORECASE)


# « Journaliste : », « INVITÉ : », « Intervenant 2 : », « Jean Dupont : », « Q : »…
INTERVENANT = re.compile(
    r"^\s*(?:(?:intervenant|speaker|locuteur|orateur|journaliste|invitée?|animat(?:eur|rice)|présentat(?:eur|rice)"
    r"|interviewer|intervieweur|modérat(?:eur|rice)|question|réponse|host|guest|moi|q|r|ospite|conduttore"
    r"|المذيع|الضيف|المحاور|سؤال|جواب)\s*\d*"
    r"|[A-ZÀ-Ý][\wÀ-ÿ'’.-]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ'’.-]+){1,2}"
    r"|[A-ZÀ-Ý]{2,}(?:\s+[A-ZÀ-Ý]{2,}){0,2})\s*[:：]\s+",
    0,
)
INTERVENANT_MOTS = re.compile(
    r"^\s*(?:intervenant|speaker|locuteur|orateur|journaliste|invitée?|animat(?:eur|rice)|présentat(?:eur|rice)"
    r"|interviewer|intervieweur|modérat(?:eur|rice)|question|réponse|host|guest|q|r|ospite|conduttore"
    r"|المذيع|الضيف|المحاور|سؤال|جواب)\s*\d*\s*[:：]\s+",
    re.IGNORECASE,
)


def _majuscule(s):
    return s[:1].upper() + s[1:] if s[:1].islower() else s


def nettoyer(texte):
    """Texte brut prêt à coller au prompteur : une idée par paragraphe, ligne vide entre."""
    texte = re.sub(r"<think>.*?</think>", "", texte, flags=re.S)
    paras = []
    for l in texte.replace("\r", "").split("\n"):
        s = l.strip()
        if not s:
            continue
        if s.startswith("#") or re.fullmatch(r"[-*_=~]{3,}", s) or re.fullmatch(r"[\[(][^\])]*[\])]\.?", s):
            continue                                   # titre, séparateur, didascalie seule
        s = s.replace("**", "").replace("__", "")
        s = re.sub(r"^([-*•·>]|\d+[.)])\s+", "", s)   # puces et numéros
        if ETIQUETTE_SEULE.match(s):
            continue
        avant = s
        s = INTERVENANT_MOTS.sub("", s)
        s = INTERVENANT.sub("", s)
        s = ETIQUETTE.sub("", s)
        if s != avant:
            s = _majuscule(s)
        s = re.sub(r"(?<![\w*])\*(?=\S)([^*]+?)(?<=\S)\*(?![\w*])", r"\1", s)   # *italique*
        s = re.sub(r"\[[^\]]{1,60}\]|\((?:pause|rires?|silence|soupir|musique)[^)]*\)", "", s, flags=re.I)
        s = re.sub(r"</?[a-z][^>]{0,30}>", "", s)      # balises
        s = EMOJI.sub("", s)
        s = " ".join(s.split())
        s = re.sub(r"\s+([.,])", r"\1", s)
        if not s:
            continue
        # un paragraphe = 1 à 4 phrases
        phrases = re.split(r"(?<=[.!?…؟])\s+", s)
        for i in range(0, len(phrases), 4):
            morceau = " ".join(phrases[i:i + 4]).strip()
            if morceau:
                paras.append(morceau)
    return "\n\n".join(paras) + "\n"


def compter_mots(t):
    return len(t.split())


# ───────────────────────── Pipeline ─────────────────────────

def decouper(texte, max_tok, langue):
    """Coupe aux fins de paragraphe/phrase en morceaux d'environ max_tok tokens."""
    max_car = int(max_tok * (2.0 if langue == "ar" else 3.0))
    morceaux, cur = [], ""
    for para in texte.split("\n"):
        while len(para) > max_car:                  # paragraphe géant
            coupe = para.rfind(" ", 0, max_car)
            coupe = coupe if coupe > 0 else max_car
            if cur:
                morceaux.append(cur)
                cur = ""
            morceaux.append(para[:coupe])
            para = para[coupe:].strip()
        if len(cur) + len(para) + 1 > max_car and cur:
            morceaux.append(cur)
            cur = ""
        cur = (cur + "\n" + para).strip()
    if cur:
        morceaux.append(cur)
    return morceaux


def generer(srv, conf, meta, langue, mots, chaine):
    titres = [s["titre"] for s in meta["sources"]]
    textes = []
    for s in meta["sources"]:
        with open(s["fichier"], encoding="utf-8") as f:
            paras = json.load(f)["paragraphes"]
        textes.append((s, "\n".join(t for _, t in paras)))

    tpw = 2.6 if langue == "ar" else 1.8
    max_sortie = min(int(mots * tpw * 1.25) + 300, 7000)
    budget_entree = CTX - max_sortie - 1800
    brut = "\n\n".join(f"=== {s['titre']} ===\n{t}" for s, t in textes)
    lg_src = max(meta["sources"], key=lambda s: s["mots"])["langue"]

    if tokens_estimes(brut, lg_src) <= budget_entree:
        log("Matière assez courte : écriture directe depuis la transcription.")
        matiere, est_brut = brut, True
    else:
        notes = []
        for s, t in textes:
            morceaux = decouper(t, 4500, s["langue"])
            for k, m in enumerate(morceaux, 1):
                log(f"Extraction : source {s['n']} partie {k}/{len(morceaux)}")
                r, _ = srv.chat(SYS_EXTRACTION,
                                [{"role": "user", "content": prompt_extraction(m, s["titre"], k, len(morceaux), langue)}],
                                1200, conf["extraction"])
                if r.strip() and not re.fullmatch(r"\W*RIEN\W*", r.strip(), re.I):
                    notes.append(f"=== {s['titre']} ({k}/{len(morceaux)}) ===\n{r.strip()}")
        if not notes:
            raise RuntimeError("aucune matière utile trouvée dans les transcriptions")
        matiere = "\n\n".join(notes)
        tour = 0
        while tokens_estimes(matiere, langue) > budget_entree and tour < 4:
            tour += 1
            groupes = decouper(matiere, 5000, langue)
            log(f"Condensation des notes (tour {tour}, {len(groupes)} groupe(s))")
            condenses = []
            for g in groupes:
                r, _ = srv.chat(SYS_EXTRACTION, [{"role": "user", "content": prompt_condenser(g, langue)}],
                                2500, conf["extraction"])
                condenses.append(r.strip())
            matiere = "\n\n".join(condenses)
        est_brut = False

    msgs = [{"role": "user", "content": prompt_ecriture(matiere, titres, langue, mots, chaine, est_brut)}]
    log(f"Écriture du texte prompteur (~{mots} mots, {LANGUE[langue].split(' (')[0]})…")
    texte, fin = srv.chat(SYS_ECRITURE, msgs, max_sortie, conf["ecriture"])
    suites = 0
    while fin == "length" and suites < 2:
        suites += 1
        log("Texte coupé par la limite : suite demandée.")
        msgs += [{"role": "assistant", "content": texte},
                 {"role": "user", "content": "Continue exactement là où tu t'es arrêté, même style et même format, jusqu'à la chute finale."}]
        suite, fin = srv.chat(SYS_ECRITURE, msgs, max_sortie, conf["ecriture"])
        texte = texte.rstrip() + "\n\n" + suite.lstrip()
    # Relecture anti-hallucination si tout tient dans le contexte
    entree = tokens_estimes(matiere + texte, langue) + 1500
    sortie_relu = int(tokens_estimes(texte, langue) * 1.2) + 300
    if entree + sortie_relu <= CTX:
        log("Relecture anti-hallucinations…")
        try:
            relu, fin = srv.chat(SYS_EXTRACTION,
                                 [{"role": "user", "content": prompt_relecture(texte, matiere, langue)}],
                                 sortie_relu, conf["extraction"])
            if fin != "length" and not REFUS.search(relu[:400]) \
                    and compter_mots(relu) >= 0.7 * compter_mots(texte):
                texte = relu
            else:
                avertir("Relecture inexploitable : texte d'origine conservé.")
        except Exception as e:
            avertir(f"Relecture impossible ({e}) : texte d'origine conservé.")
    else:
        avertir("Texte trop long pour une relecture complète : relecture sautée.")
    return texte


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--langue", default="auto")
    p.add_argument("--duree", default="auto")
    p.add_argument("--ia", default="qwen3-8b")
    p.add_argument("--work", default="work")
    p.add_argument("--out", default="out")
    a = p.parse_args()

    with open(os.path.join(ICI, "config.json"), encoding="utf-8") as f:
        config = json.load(f)
    with open(os.path.join(a.work, "meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    meta["sources"] = [s for s in meta["sources"] if s["mots"] > 0]
    if not meta["sources"]:
        print("::error::Aucune parole transcrite : pas de texte prompteur possible.")
        sys.exit(1)

    if a.langue in LANGUE:
        langue = a.langue
    else:
        poids = {}
        for s in meta["sources"]:
            poids[s["langue"]] = poids.get(s["langue"], 0) + s["mots"]
        langue = max(poids, key=poids.get)
    total_mots = sum(s["mots"] for s in meta["sources"])
    mots = LONGUEURS.get(a.duree) or min(1600, max(250, int(total_mots * 0.3)))
    if a.duree not in LONGUEURS:
        mots = min(mots, max(150, total_mots))      # jamais plus long que la matière
    chaine = config["chaines"].get(langue, "")

    ordre = [a.ia] + [k for k in config["ia"] if k != a.ia]
    if a.ia not in config["ia"]:
        ordre = list(config["ia"])
    dernier_err = None
    for nom_ia in ordre[:2]:
        conf = config["ia"][nom_ia]
        srv = None
        try:
            log(f"── IA locale : {nom_ia}")
            chemin = telecharger_modele(conf)
            srv = Serveur(chemin, conf)
            srv.demarrer()
            for essai in range(2):
                brut = generer(srv, conf, meta, langue, mots, chaine)
                if REFUS.search(brut[:400]):
                    avertir(f"{nom_ia} a refusé d'écrire (essai {essai + 1}).")
                    continue
                texte = nettoyer(brut)
                n = compter_mots(texte)
                if n < mots * 0.35:
                    avertir(f"Texte trop court ({n} mots pour ~{mots}), nouvel essai.")
                    continue
                sortie = os.path.join(a.out, f"{meta['prefixe']}_prompteur.txt")
                with open(sortie, "w", encoding="utf-8") as f:
                    f.write(texte)
                log(f"Texte prompteur écrit : {sortie} — {n} mots ≈ {n / 150:.0f} min de lecture "
                    f"({LANGUE[langue].split(' (')[0]}, IA {nom_ia})")
                return
            dernier_err = RuntimeError(f"{nom_ia} n'a pas produit de texte exploitable")
        except Exception as e:
            dernier_err = e
            avertir(f"Échec avec {nom_ia} : {e}")
        finally:
            if srv:
                srv.arreter()
        if nom_ia != ordre[-1]:
            log("Bascule sur l'autre IA locale.")
    print(f"::error::Texte prompteur impossible : {dernier_err}. La transcription reste disponible.")
    sys.exit(1)


if __name__ == "__main__":
    main()
