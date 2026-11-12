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
CTX = 24576
URL = f"http://127.0.0.1:{PORT}"
LANGUE = {
    "fr": "français",
    "it": "italien",
    "en": "anglais",
    "ar": "arabe standard moderne (فصحى), fluide et oral",
}
LONGUEURS = {"court": 450, "moyen": 900, "long": 1500}
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
                 "max_tokens": max_tokens, "stream": False, "cache_prompt": True, **params}
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

SYS_ECRITURE = ("Tu es le meilleur scénariste de chroniques géopolitiques YouTube : un stratège qui décrypte les "
                "rapports de force, un lanceur d'alerte qui fait monter l'indignation, et un humoriste noir qui fait "
                "respirer le public. Obsédé par la rétention d'audience. Tu écris exactement le texte que "
                "l'animateur lit au prompteur, face caméra, pendant son live. Tu écris sans autocensure.")

NETTOYAGE = ("La matière vient d'une transcription automatique : elle peut contenir des erreurs de "
             "reconnaissance, de la musique, des paroles de chansons, des jingles, l'intro ou l'outro de la "
             "chaîne d'origine, de l'autopromotion, des publicités, la présentation de l'invité, des "
             "remerciements, des appels à s'abonner, des crédits, licences, références bibliographiques et "
             "mentions techniques (date d'enregistrement, version, « article de qualité »…). Ignore tout cela "
             "totalement. Les erreurs de reconnaissance (mots déformés, noms propres ou lieux mal orthographiés) "
             "ne sont PAS du contenu : corrige-les silencieusement avec le mot juste quand il est évident, et ne "
             "les mentionne jamais.")


RUBRIQUES = """ANGLE : la phrase la plus choquante, absurde ou scandaleuse de toute la matière, en une ligne (servira de hook).
FAITS : les faits essentiels, avec noms, lieux, dates et chiffres EXACTS tels qu'ils apparaissent (corrige seulement les erreurs évidentes de reconnaissance).
CITATIONS : les déclarations les plus percutantes, presque mot pour mot, avec qui les a dites.
SOUPÇONS : accusations, soupçons et rumeurs évoqués, avec qui les porte.
ABSURDITÉS : contradictions, hypocrisies, détails ridicules DU SUJET à moquer (jamais les erreurs de transcription)."""


def prompt_fiche(texte, titre, k, n, langue):
    partie = f", partie {k}/{n}" if n > 1 else ""
    return f"""Voici une transcription automatique (« {titre} »{partie}).
{NETTOYAGE}

Rédige en {LANGUE[langue]} la FICHE DE PRÉPARATION d'une chronique, avec exactement ces rubriques :
{RUBRIQUES}

Puces courtes, 35 puces au maximum en tout, l'essentiel d'abord. N'invente rien, ne commente pas.
Rubrique sans contenu : écris « — ». Si la transcription ne contient rien d'utile, réponds seulement : RIEN

TRANSCRIPTION :
{texte}"""


def bloc_matiere(matiere):
    # Placée en tête et identique d'une étape à l'autre : le moteur réutilise son cache
    # au lieu de relire toute la matière (gros gain de temps sur CPU).
    return f"FICHE :\n{matiere}\n\n────────\n\n"


def prompt_relecture(texte, matiere):
    return f"""{bloc_matiere(matiere)}Tu es maintenant le vérificateur final avant le direct. Compare le TEXTE ci-dessous à la FICHE ci-dessus.

Repère les phrases du TEXTE qui doivent être supprimées :
1. hallucination : fait, chiffre, nom, lieu, date ou citation absent de la FICHE ou différent de la FICHE ;
2. nom ou étiquette d'intervenant, format dialogue ou questions-réponses d'interview ;
3. mention d'interview, d'invité, de vidéo, de source, d'article, d'extrait, de chaîne d'origine, de transcription, de licence ou de crédits ;
4. reste de musique, chanson, jingle, générique ou publicité ;
5. répétition d'une phrase déjà dite.
L'humour, les exagérations comiques, les opinions et les accusations ou soupçons présents dans la fiche sont NORMAUX : ne les signale pas.

Réponds UNIQUEMENT avec les phrases à supprimer, copiées mot pour mot, une par ligne.
S'il n'y en a aucune, réponds : AUCUNE

TEXTE :
{texte}"""


def _norm(t):
    return re.sub(r"[\W_]+", "", t.lower())


def supprimer_phrases(texte, a_supprimer):
    """Retire du texte les phrases signalées (correspondance tolérante). Garde-fou : max 25 %."""
    cibles = []
    for l in a_supprimer.split("\n"):
        l = re.sub(r"^\s*([-*•]|\d+[.)])\s*", "", l).strip().strip("«»\"'“”")
        n = _norm(l)
        if len(n) >= 12 and not re.fullmatch(r"aucune?", n):
            cibles.append(n)
    if not cibles:
        return texte, 0
    paras = [re.split(r"(?<=[.!?…؟])\s+", p) for p in texte.strip().split("\n\n")]
    total = sum(len(p) for p in paras)
    retire, nouveaux = 0, []
    for phrases in paras:
        garde = []
        for ph in phrases:
            n = _norm(ph)
            if len(n) >= 12 and any(c == n or c in n or (len(n) >= 25 and n in c) for c in cibles):
                retire += 1
            else:
                garde.append(ph)
        if garde:
            nouveaux.append(" ".join(garde))
    if retire > max(2, total * 0.25):
        avertir(f"Relecture : {retire}/{total} phrases signalées, trop pour être fiable — rien retiré.")
        return texte, 0
    return "\n\n".join(nouveaux) + "\n", retire


def prompt_fusion(fiches, langue):
    return f"""Voici plusieurs fiches de préparation tirées de sources différentes sur le même live.
Fusionne-les en UNE seule fiche cohérente, en {LANGUE[langue]}, avec exactement ces rubriques :
{RUBRIQUES}

Un seul ANGLE (le plus fort de tous). Supprime les doublons, regroupe par sujet, garde tous les noms,
chiffres, citations et soupçons importants. 40 puces au maximum. N'invente rien. Pas de commentaire.

FICHES :
{fiches}"""


def prompt_ecriture(fiche, nb_sources, langue, mots, chaine, citation=None):
    chaine_txt = f" de la chaîne « {chaine} »" if chaine else ""
    chaine_cta = f" à « {chaine} »" if chaine else ""
    vous = " Vouvoie toujours le public (« vous »), jamais de tutoiement." if langue == "fr" else ""
    return f"""{bloc_matiere(fiche)}Écris le texte de prompteur de mon prochain passage en live, en {LANGUE[langue]}, à partir de la FICHE ci-dessus{f" (tirée de {nb_sources} sources)" if nb_sources > 1 else ""}.

QUI PARLE
- C'est moi, l'animateur{chaine_txt}, qui parle à la première personne, en direct, à mon public.{vous}
- Tout ce qui vient d'un invité, d'un interviewé, d'un journaliste ou d'un autre commentateur devient MON analyse, racontée naturellement. Ne mentionne jamais d'interview, d'invité, de vidéo, de source, d'article, d'extrait, de fiche ni de chaîne d'origine.
- Les déclarations des personnalités qui FONT l'actualité restent les leurs (« Untel a osé dire que… ») et je réagis dessus.
{"- Une seule histoire cohérente du début à la fin, regroupée par idées, avec des transitions naturelles." if nb_sources > 1 else ""}

STRUCTURE (rétention maximale)
{f"0. Le texte s'ouvrira sur cette citation, déjà écrite automatiquement (NE LA RÉÉCRIS PAS) : « {citation[0]} » — {citation[1]}. Ta toute première phrase rebondit dessus pour lancer le hook." if citation else ""}
1. HOOK ULTRA-CHOC, 2 à 4 phrases, construit sur l'ANGLE : le fait le plus alarmant ou scandaleux, une question qui dérange, une conséquence directe pour le public. Il doit provoquer indignation et inquiétude immédiates. Jamais de « bonjour » avant le hook.
2. Une salutation très courte, puis une PROMESSE de ce qu'ils vont découvrir s'ils restent. Garde le meilleur pour la fin.
3. Le CORPS en 3 à 5 blocs qui montent en intensité. Chaque bloc s'ouvre par une relance. Une punchline ou une relance toutes les 4 à 6 phrases. Dans le corps : les faits, puis le DÉCRYPTAGE STRATÉGIQUE (qui gagne, qui perd, ce que chaque acteur cherche vraiment, le rapport de force caché).
4bis. Avant la chute : LE SCÉNARIO DE LA SUITE, présenté clairement comme mon analyse (« mon pari », « le scénario le plus probable », « si rien ne change ») et jamais comme un fait.
4. Au milieu exactement, un appel court et drôle à liker et commenter.
5. La CHUTE : la révélation promise, une punchline finale, puis un appel à s'abonner{chaine_cta} et UNE question précise pour les commentaires. Rien après.

STYLE
- Oral, vivant, comme si j'improvisais. Phrases courtes (20 mots maximum), quelques phrases d'un seul mot.
- Trois registres mêlés :
  ALARME et INDIGNATION : urgence, « ce qu'on ne vous dit pas », « regardez bien ce qui se passe », conséquences concrètes pour les gens ordinaires ;
  STRATÈGE GÉOPOLITIQUE : décryptage froid et lucide des intérêts, des alliances, des rapports de force et des calculs de chaque camp ;
  HUMOUR NOIR en contrepoint : ironie, sarcasme, faux étonnement, comparaisons absurdes visant les puissants et les hypocrisies, pour faire respirer entre deux coups de massue.
  Exemples de tournures (ne les copie pas toutes) : « Rassurez-vous, ça empire. » ; « Regardez bien la carte. » ; « Vous allez rire. Ou pleurer. Probablement les deux. »
- Aucune autocensure : garde tous les faits, citations et soupçons de la fiche, sans les adoucir. Les soupçons restent présentés comme des soupçons (« on raconte que », « selon… »).
- EXACTITUDE : utilise les noms, lieux, dates et chiffres EXACTEMENT comme dans la fiche. N'ajoute aucun fait, chiffre, nom, date ou citation qui n'y figure pas. L'humour et l'analyse portent sur les faits, ils n'en créent pas. N'ajoute aucune autre citation célèbre que celle d'ouverture.
- Chaque fait n'est dit qu'UNE fois. Aucune phrase répétée.
- Jamais de nom ou d'étiquette d'intervenant, jamais de dialogue ni de questions-réponses : uniquement ma voix.
- Ne parle jamais de transcription, d'erreurs de mots, de licence, de crédits ou de références : seulement du sujet.

FORMAT PROMPTEUR
- Texte brut : pas de titre, pas de markdown, pas d'astérisques, pas de listes, pas d'émojis, pas de didascalies, pas de crochets, pas de noms de parties.
- Un paragraphe = une idée, 1 à 4 phrases. Une ligne vide entre les paragraphes.
- Longueur : environ {mots} mots."""


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
        s = re.sub(r"(?<=[.!?…؟])\s*\b(HOOK|ACCROCHE|PROMESSE|CORPS|BLOC\s*\d*|RELANCE|CHUTE|CONCLUSION|CTA|SCÉNARIO)\s*[:：]\s*", " ", s)
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


def dedoublonner(texte):
    """Supprime les phrases répétées ou quasi identiques (défaut courant des petits modèles)."""
    vues, paras, retire = [], [], 0
    for p in texte.strip().split("\n\n"):
        garde = []
        for ph in re.split(r"(?<=[.!?…؟])\s+", p):
            mots_ph = set(re.findall(r"\w+", ph.lower()))
            n = _norm(ph)
            doublon = len(n) >= 20 and any(
                n == v[0] or (len(mots_ph) >= 6 and len(mots_ph & v[1]) / len(mots_ph | v[1]) >= 0.8)
                for v in vues)
            if doublon:
                retire += 1
                continue
            vues.append((n, mots_ph))
            garde.append(ph)
        if garde:
            paras.append(" ".join(garde))
    if retire:
        log(f"Dédoublonnage : {retire} phrase(s) répétée(s) retirée(s).")
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


def lister_morceaux(meta):
    """Liste DÉTERMINISTE des morceaux de transcription : (index global, source, k, n, texte).
    Identique dans chaque job : sert à répartir les fiches entre plusieurs serveurs."""
    res, g = [], 0
    for s in meta["sources"]:
        with open(s["fichier"], encoding="utf-8") as f:
            texte = "\n".join(t for _, t in json.load(f)["paragraphes"])
        morceaux = decouper(texte, 11000, s["langue"])
        for k, m in enumerate(morceaux, 1):
            res.append((g, s, k, len(morceaux), m))
            g += 1
    return res


def chemin_fiche(work, g):
    return os.path.join(work, "fiches", f"fiche_{g:03d}.txt")


def extraire_fiches(srv, conf, meta, langue, work="work", part=None, parts=1):
    """Fiche de chaque morceau ; réutilise celles déjà calculées (par un autre serveur)."""
    os.makedirs(os.path.join(work, "fiches"), exist_ok=True)
    morceaux = lister_morceaux(meta)
    for g, s, k, n, m in morceaux:
        if part is not None and g % parts != part:
            continue
        chemin = chemin_fiche(work, g)
        if os.path.exists(chemin):
            continue
        log(f"Fiche {g + 1}/{len(morceaux)} : source {s['n']}/{len(meta['sources'])}, partie {k}/{n}")
        r, _ = srv.chat(SYS_EXTRACTION,
                        [{"role": "user", "content": prompt_fiche(m, s["titre"], k, n, langue)}],
                        1500, conf["extraction"])
        r = r.strip()
        if re.fullmatch(r"\W*RIEN\W*", r, re.I):
            r = ""                                # rien d'utile : fichier vide = déjà traité
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(r)


def preparer_fiche(srv, conf, meta, langue, work="work"):
    # 1) une FICHE par morceau (déjà faites en parallèle si le workflow l'a permis)
    extraire_fiches(srv, conf, meta, langue, work)
    fiches = []
    for g, *_ in lister_morceaux(meta):
        with open(chemin_fiche(work, g), encoding="utf-8") as f:
            t = f.read().strip()
        if t:
            fiches.append(t)
    if not fiches:
        raise RuntimeError("aucune matière utile trouvée dans les transcriptions")

    # 2) fusion en UNE fiche (cohérence multi-sources)
    while len(fiches) > 1:
        groupes, cur = [], []
        for f_ in fiches:
            if cur and tokens_estimes("\n\n".join(cur + [f_]), langue) > 9000:
                groupes.append(cur)
                cur = []
            cur.append(f_)
        groupes.append(cur)
        log(f"Fusion de {len(fiches)} fiches en {len(groupes)}…")
        nouvelles = []
        for g in groupes:
            if len(g) == 1:
                nouvelles.append(g[0])
                continue
            corps = "\n\n".join(f"--- FICHE {i} ---\n{x}" for i, x in enumerate(g, 1))
            r, _ = srv.chat(SYS_EXTRACTION, [{"role": "user", "content": prompt_fusion(corps, langue)}],
                            1800, conf["extraction"])
            nouvelles.append(r.strip() or g[0])
        if len(nouvelles) >= len(fiches):        # sécurité anti-boucle
            fiches = ["\n\n".join(nouvelles)]
            break
        fiches = nouvelles
    fiche = fiches[0]
    with open(os.path.join("out", f"{meta['prefixe']}_fiche.txt"), "w", encoding="utf-8") as f:
        f.write(fiche.strip() + "\n")              # pour vérifier d'où viennent les faits
    return fiche


def charger_citations():
    try:
        with open(os.path.join(ICI, "citations.json"), encoding="utf-8") as f:
            return [c for c in json.load(f) if all(k in c for k in ("fr", "ar", "it", "en"))]
    except Exception as e:
        avertir(f"Banque de citations illisible ({e}) : texte sans citation d'ouverture.")
        return []


def choisir_citation(srv, conf, fiche, langue, graine=""):
    """Citation célèbre VÉRIFIÉE (banque citations.json) la plus adaptée au sujet : jamais inventée."""
    banque = charger_citations()
    if not banque:
        return None
    liste = "\n".join(f"{i}. [{c['theme']}] « {c['fr'][0]} » — {c['fr'][1]}" for i, c in enumerate(banque, 1))
    prompt = f"""{bloc_matiere(fiche)}Voici une liste de citations célèbres numérotées, avec leur thème entre crochets.
Choisis celle qui colle le mieux à l'ANGLE et au sujet de la FICHE ci-dessus, pour ouvrir le live.
Réponds UNIQUEMENT par son numéro.

{liste}"""
    choix = None
    try:
        r, _ = srv.chat(SYS_EXTRACTION, [{"role": "user", "content": prompt}], 10, conf["extraction"])
        m = re.search(r"\d+", r)
        if m and 1 <= int(m.group()) <= len(banque):
            choix = int(m.group())
    except Exception as e:
        avertir(f"Choix de la citation impossible ({e}).")
    if choix is None:                                  # secours : choix stable mais varié
        choix = sum(map(ord, graine)) % len(banque) + 1
    c = banque[choix - 1]
    texte, auteur = c[langue if langue in c else "fr"]
    log(f"Citation d'ouverture : « {texte} » — {auteur}")
    return texte, auteur, [c[k][0] for k in ("fr", "ar", "it", "en")]


def ecrire(srv, conf, fiche, meta, langue, mots, chaine, citation=None):
    # 3) écriture depuis la fiche
    tpw = 2.6 if langue == "ar" else 1.8
    max_sortie = min(int(mots * tpw * 1.3) + 300, 6000)
    msgs = [{"role": "user", "content": prompt_ecriture(fiche, len(meta["sources"]), langue, mots, chaine, citation)}]
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
    return texte


def finaliser(srv, conf, texte, fiche):
    # 4) nettoyage, dédoublonnage, relecture contre la fiche (préfixe en cache)
    texte = dedoublonner(nettoyer(texte))
    log("Relecture anti-hallucinations…")
    try:
        liste, _ = srv.chat(SYS_ECRITURE, [{"role": "user", "content": prompt_relecture(texte, fiche)}],
                            900, conf["extraction"])
        if REFUS.search(liste[:300]):
            raise RuntimeError("refus")
        texte, n = supprimer_phrases(texte, liste)
        log(f"Relecture : {n} phrase(s) retirée(s).")
    except Exception as e:
        avertir(f"Relecture impossible ({e}) : texte conservé tel quel.")
    return texte


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--langue", default="auto")
    p.add_argument("--duree", default="auto")
    p.add_argument("--ia", default="qwen3-8b")
    p.add_argument("--work", default="work")
    p.add_argument("--out", default="out")
    p.add_argument("--etape", default="tout", choices=["tout", "fiches", "compter"],
                   help="fiches = seulement les fiches de la part --part sur --parts (travail parallèle)")
    p.add_argument("--part", type=int, default=0)
    p.add_argument("--parts", type=int, default=1)
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
            if s["langue"] in LANGUE:                 # prompteur seulement en fr/ar/it/en
                poids[s["langue"]] = poids.get(s["langue"], 0) + s["mots"]
        langue = max(poids, key=poids.get) if poids else "fr"
    total_mots = sum(s["mots"] for s in meta["sources"])
    mots = LONGUEURS.get(a.duree) or min(900, max(250, int(total_mots * 0.25)))
    if a.duree not in LONGUEURS:
        mots = min(mots, max(150, total_mots))      # jamais plus long que la matière
    chaine = config["chaines"].get(langue, "")

    if a.etape == "compter":                          # nb de fiches pour cette part
        print(sum(1 for g, *_ in lister_morceaux(meta) if g % a.parts == a.part))
        return
    if a.etape == "fiches":
        mes = [g for g, *_ in lister_morceaux(meta) if g % a.parts == a.part]
        if not mes:
            log("Aucune fiche pour cette part.")
            return
        log(f"Part {a.part + 1}/{a.parts} : {len(mes)} fiche(s) à préparer.")
        noms_ia = [a.ia] + [k for k in config["ia"] if k != a.ia]
        for nom_ia in noms_ia[:2]:
            srv = None
            try:
                conf = config["ia"][nom_ia]
                srv = Serveur(telecharger_modele(conf), conf)
                srv.demarrer()
                extraire_fiches(srv, conf, meta, langue, a.work, a.part, a.parts)
                return
            except Exception as e:
                avertir(f"Fiches avec {nom_ia} : {e}")
            finally:
                if srv:
                    srv.arreter()
        avertir("Fiches de cette part non terminées : elles seront refaites à l'étape d'écriture.")
        return

    ordre = [a.ia] + [k for k in config["ia"] if k != a.ia]
    if a.ia not in config["ia"]:
        ordre = list(config["ia"])
    t_debut = time.time()
    limite = float(os.environ.get("PROMPTEUR_LIMITE_MIN", "160"))
    if os.environ.get("JOB_START"):                   # le job entier est limité à 350 min
        limite = min(limite, 340 - (time.time() - float(os.environ["JOB_START"])) / 60)
    log(f"Temps disponible pour le prompteur : {limite:.0f} min")

    def reste_min():
        return limite - (time.time() - t_debut) / 60

    sortie = os.path.join(a.out, f"{meta['prefixe']}_prompteur.txt")

    def publier(texte, nom_ia, note=""):
        if citation:                                   # citation d'ouverture, mot pour mot
            variantes = [_norm(v) for v in citation[2] if len(_norm(v)) >= 12]
            paras = [p for p in texte.strip().split("\n\n") if not any(v in _norm(p) for v in variantes)]
            texte = f"« {citation[0]} »\n— {citation[1]}\n\n" + "\n\n".join(paras) + "\n"
        with open(sortie, "w", encoding="utf-8") as f:
            f.write(texte)
        n = compter_mots(texte)
        log(f"Texte prompteur écrit : {sortie} — {n} mots ≈ {max(1, round(n / 150))} min de lecture "
            f"({LANGUE[langue].split(' (')[0]}, IA {nom_ia}){note}")

    fiche, citation, meilleur, dernier_err = None, None, ("", ""), None
    for nom_ia in ordre[:2]:
        conf = config["ia"][nom_ia]
        srv = None
        try:
            log(f"── IA locale : {nom_ia}")
            srv = Serveur(telecharger_modele(conf), conf)
            srv.demarrer()
            if fiche is None:
                fiche = preparer_fiche(srv, conf, meta, langue, a.work)   # calculée une seule fois
                citation = choisir_citation(srv, conf, fiche, langue, meta["prefixe"])
            for essai in range(2):
                if essai and reste_min() < 25:
                    avertir("Temps insuffisant pour un nouvel essai.")
                    break
                brut = ecrire(srv, conf, fiche, meta, langue, mots, chaine, citation)
                if REFUS.search(brut[:400]):
                    avertir(f"{nom_ia} a refusé d'écrire (essai {essai + 1}).")
                    continue
                texte = finaliser(srv, conf, brut, fiche)
                n = compter_mots(texte)
                if n > compter_mots(meilleur[0]):
                    meilleur = (texte, nom_ia)
                if n >= mots * 0.35:
                    publier(texte, nom_ia)
                    return
                avertir(f"Texte trop court ({n} mots pour ~{mots}).")
            dernier_err = RuntimeError(f"{nom_ia} n'a pas produit de texte satisfaisant")
        except Exception as e:
            dernier_err = e
            avertir(f"Échec avec {nom_ia} : {e}")
        finally:
            if srv:
                srv.arreter()
        if reste_min() < 30:
            avertir("Temps insuffisant pour essayer l'autre IA.")
            break
        if nom_ia != ordre[1]:
            log("Bascule sur l'autre IA locale.")
    if meilleur[0].strip():
        publier(meilleur[0], meilleur[1], " — plus court que prévu")
        avertir("Texte prompteur publié mais plus court que la longueur visée.")
        return
    print(f"::error::Texte prompteur impossible : {dernier_err}. La transcription reste disponible.")
    sys.exit(1)


if __name__ == "__main__":
    main()
