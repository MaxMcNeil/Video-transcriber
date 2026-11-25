#!/usr/bin/env python3
"""Moteurs IA en ligne GRATUITS (optionnels) : Gemini et Groq.

Chaque moteur expose chat(systeme, messages, max_tokens, params) -> (texte, fin),
comme le serveur local llama.cpp. Une erreur (quota dépassé, panne, refus…) lève
une exception : prompteur.py passe alors au moteur suivant, puis à l'IA locale.
Les clés viennent des variables d'environnement GEMINI_API_KEY / GROQ_API_KEY
(secrets GitHub) et ne sont jamais écrites dans les logs.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request


def _log(m):
    print(m, flush=True)


def _requete(url, corps=None, entetes=None, timeout=600):
    data = json.dumps(corps).encode() if corps is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(entetes or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")[:300]
        detail = re.sub(r"(key=)[^&\s\"]+", r"\1***", detail)          # jamais la clé dans un log
        raise RuntimeError(f"HTTP {e.code} : {detail}") from None


SUITE = ("Ta réponse précédente a été coupée par la limite de longueur. Écris UNIQUEMENT la suite, en reprenant exactement "
         "au caractère où elle s'arrête : ne répète rien, n'ajoute ni introduction, ni explication, ni balise.")


def _jonction(a, b):
    """Recolle la suite sans doublon au point de jonction (le modèle répète parfois la fin)."""
    b = re.sub(r"^\s*```\w*\s*", "", b or "")
    for k in range(min(400, len(a), len(b)), 5, -1):
        if a.endswith(b[:k]):
            return a + b[k:]
    return a + b


def avec_suite(chat1, systeme, messages, max_tokens, params, maxi=4):
    """RÉPONSE COUPÉE : on ne redemande JAMAIS tout ; on rend au modèle ce qu'il a écrit et on demande SEULEMENT LA SUITE."""
    texte, fin = chat1(systeme, messages, max_tokens, params)
    n = 0
    while fin == "length" and texte and n < maxi:
        n += 1
        _log(f"   réponse coupée → demande de la suite seulement ({n}/{maxi})")
        try:
            suite, fin = chat1(systeme, list(messages) + [{"role": "assistant", "content": texte},
                                                          {"role": "user", "content": SUITE}], max_tokens, params)
        except Exception as e:
            _log(f"   suite impossible ({str(e)[:120]}) : texte gardé tel quel")
            break
        if not suite:
            break
        texte = _jonction(texte, suite)
    return texte, fin


def _nature(e):
    """Nature d'une erreur, pour ne JAMAIS condamner une clé ou un modèle sur un incident passager :
    passager (surcharge 5xx, délai, réseau, limite par minute) → on patiente et on réessaie le même modèle ;
    jour (quota du jour) / modele (modèle introuvable, requête refusée par CE modèle) → modèle suivant ;
    acces (401/403) → on VÉRIFIE la clé avant de conclure."""
    t = str(e)
    m = re.match(r"HTTP (\d{3})", t)
    if not m:                                   # réponse vide : CE modèle n'y arrive pas → modèle suivant
        return "modele" if "réponse vide" in t else "passager"     # délai dépassé, réseau coupé…
    code, low = int(m.group(1)), t.lower()
    if code >= 500:
        return "passager"
    if code == 429:
        return "jour" if re.search(r"per ?day|daily|perday|tpd|rpd", low) else "passager"
    if code in (401, 403):
        return "acces"
    return "modele"


def _version(nom):
    nums = re.findall(r"\d+(?:\.\d+)?", nom)
    return float(nums[0]) if nums else 0.0


class Gemini:
    nom = "gemini"
    contexte = 500000          # fenêtre très large : fusion des fiches en un seul appel
    ecrit_arabe = True         # écrit directement en arabe (pas besoin de passer par l'anglais)
    BASE = os.environ.get("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta")

    def __init__(self, cle):
        self.cle = cle
        self.modeles = self._choisir()
        if not self.modeles:
            raise RuntimeError("aucun modèle Gemini Flash disponible")
        _log(f"Gemini : modèles disponibles par ordre de préférence : {', '.join(self.modeles)}")

    def _choisir(self):
        r = _requete(f"{self.BASE}/models?pageSize=200", entetes={"x-goog-api-key": self.cle})
        exclus = ("image", "tts", "audio", "live", "embedding", "vision", "robotics", "native", "computer")
        flash, lite = [], []
        for m in r.get("models", []):
            nom = m.get("name", "").replace("models/", "")
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            if "flash" not in nom or any(x in nom for x in exclus):
                continue
            (lite if "lite" in nom else flash).append(nom)
        trier = lambda l: sorted(l, key=lambda n: (_version(n), "preview" not in n, "exp" not in n), reverse=True)
        return trier(flash)[:3] + trier(lite)[:2]

    def chat(self, systeme, messages, max_tokens, params):
        return avec_suite(self.chat1, systeme, messages, max_tokens, params)

    def chat1(self, systeme, messages, max_tokens, params):
        contenus = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                    for m in messages]
        corps = {
            "systemInstruction": {"parts": [{"text": systeme}]},
            "contents": contenus,
            "generationConfig": {"temperature": params.get("temperature", 0.7),
                                 "topP": params.get("top_p", 0.95),
                                 "maxOutputTokens": max(4096, max_tokens * 2)},   # marge pour la « réflexion »
            "safetySettings": [{"category": c, "threshold": "BLOCK_NONE"} for c in (
                "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")],
        }
        derniere = None
        for modele in list(self.modeles):
            try:
                t0 = time.time()
                r = _requete(f"{self.BASE}/models/{modele}:generateContent", corps,
                             entetes={"x-goog-api-key": self.cle})
                cand = (r.get("candidates") or [{}])[0]
                fin = cand.get("finishReason", "")
                texte = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", [])
                                if not p.get("thought"))
                if not texte.strip():
                    raise RuntimeError(f"réponse vide ({fin or r.get('promptFeedback', {})})")
                u = r.get("usageMetadata", {})
                _log(f"   Gemini {modele} : {u.get('promptTokenCount', '?')} → "
                     f"{u.get('candidatesTokenCount', '?')} tokens en {time.time() - t0:.0f} s")
                return texte.strip(), ("length" if fin == "MAX_TOKENS" else "stop")
            except Exception as e:
                derniere = e
                n = _nature(e)
                if n == "acces" and not self.cle_valide():
                    raise RuntimeError(f"clé Gemini refusée par Google (vérifiée) : {str(e)[:160]}") from None
                if n == "passager":                   # surcharge ou réseau : ni la clé ni le modèle ne sont en cause
                    _log(f"   Gemini {modele} momentanément indisponible ({str(e)[:120]}) : modèle suivant, gardé pour plus tard.")
                    continue
                _log(f"   Gemini {modele} indisponible ({str(e)[:160]}) : modèle écarté.")
                self.modeles.remove(modele)              # quota du jour, modèle retiré ou refusé à cette clé
        if derniere is not None and _nature(derniere) == "passager" and self.modeles and not getattr(self, "_repli", False):
            self._repli = True                           # tout est surchargé : une pause, puis un dernier tour
            try:
                _log("   Gemini : tous les modèles surchargés, pause de 60 s puis nouvel essai.")
                time.sleep(60)
                return self.chat1(systeme, messages, max_tokens, params)
            finally:
                self._repli = False
        raise RuntimeError(f"Gemini épuisé : {derniere}")

    def cle_valide(self):
        """La clé elle-même est-elle bonne ? (un 403 peut viser UN modèle seulement). Doute → oui."""
        try:
            _requete(f"{self.BASE}/models?pageSize=1", entetes={"x-goog-api-key": self.cle}, timeout=30)
            return True
        except RuntimeError as e:
            return not re.match(r"HTTP (400|401|403)", str(e))
        except Exception:
            return True


class Groq:
    nom = "groq"
    contexte = 6000            # limite gratuite ≈ 8 000 tokens/min : seulement l'écriture, pas la fusion
    ecrit_arabe = True
    BASE = os.environ.get("GROQ_API_BASE", "https://api.groq.com/openai/v1")
    PREFERES = ("openai/gpt-oss-120b", "moonshotai/kimi-k2-instruct", "llama-3.3-70b-versatile")

    def __init__(self, cle):
        self.cle = cle
        r = _requete(f"{self.BASE}/models", entetes={"Authorization": f"Bearer {cle}"})
        dispo = [m["id"] for m in r.get("data", []) if m.get("active", True)]
        # préférés d'abord, puis les autres GRANDS modèles de texte (chaque modèle a SON quota : plus de modèles = plus de capacité)
        texte_ok = [m for m in dispo if not re.search(r"whisper|tts|guard|orpheus|playai|vision|compound|embed|moderation", m, re.I)]
        grands = [m for m in texte_ok if m not in self.PREFERES and (re.search(r"gpt-oss-120b|kimi|maverick", m)
                  or int((re.search(r"(\d+)b\b", m) or [0, 0])[1]) >= 27)]
        self.modeles = [m for m in self.PREFERES if m in dispo] + grands
        if not self.modeles:
            raise RuntimeError("aucun grand modèle Groq disponible")
        _log(f"Groq : modèles disponibles par ordre de préférence : {', '.join(self.modeles)}")

    def chat(self, systeme, messages, max_tokens, params):
        return avec_suite(self.chat1, systeme, messages, max_tokens, params)

    def chat1(self, systeme, messages, max_tokens, params):
        """Modèle saturé (429) → AUSSITÔT un autre modèle Groq libre (chaque modèle a ses propres limites) ;
        pause seulement si TOUS sont saturés. Modèle retiré ou refusé → écarté pour tout le run."""
        derniere = None
        pause = getattr(self, "_pause", None)
        if pause is None:
            pause = self._pause = {}
        for _tour in range(8):
            libres = [m for m in self.modeles if pause.get(m, 0) <= time.time()]
            if not self.modeles:
                break
            if not libres:
                attente = min(pause.get(m, 0) for m in self.modeles) - time.time()
                if attente > 300:
                    break
                _log(f"   Groq : tous les modèles saturés, pause de {max(1, int(attente))} s.")
                time.sleep(max(1, attente))
                continue
            modele = libres[0]
            corps = {"model": modele, "messages": [{"role": "system", "content": systeme}] + messages,
                     "temperature": params.get("temperature", 0.7), "top_p": params.get("top_p", 0.95),
                     "max_tokens": min(8000, max(2048, max_tokens * 2))}
            if "gpt-oss" in modele:
                corps["reasoning_effort"] = "low"
            try:
                t0 = time.time()
                r = _requete(f"{self.BASE}/chat/completions", corps, entetes={"Authorization": f"Bearer {self.cle}"})
                ch = r["choices"][0]
                texte = (ch["message"].get("content") or "").strip()
                if not texte:
                    raise RuntimeError("réponse vide")
                u = r.get("usage", {})
                _log(f"   Groq {modele} : {u.get('prompt_tokens', '?')} → {u.get('completion_tokens', '?')} "
                     f"tokens en {time.time() - t0:.0f} s")
                return texte, ch.get("finish_reason")
            except Exception as e:
                derniere = e
                n = _nature(e)
                if n == "acces" and not self.cle_valide():
                    raise RuntimeError(f"clé Groq refusée (vérifiée) : {str(e)[:160]}") from None
                if n == "passager":
                    m_ = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", str(e))
                    attente = (int(m_.group(1) or 0) * 60 + float(m_.group(2)) + 2) if m_ else 60
                    pause[modele] = time.time() + attente
                    _log(f"   Groq {modele} saturé ({str(e)[:100]}) : autre modèle Groq tout de suite.")
                    continue
                if n == "jour":
                    pause[modele] = time.time() + 6 * 3600
                    _log(f"   Groq {modele} : quota du jour atteint → autre modèle Groq.")
                    continue
                _log(f"   Groq {modele} indisponible ({str(e)[:160]}) : modèle écarté pour ce run.")
                self.modeles.remove(modele)
        raise RuntimeError(f"Groq épuisé : {derniere}")

    def cle_valide(self):
        try:
            _requete(f"{self.BASE}/models", entetes={"Authorization": f"Bearer {self.cle}"}, timeout=30)
            return True
        except RuntimeError as e:
            return not re.match(r"HTTP (400|401|403)", str(e))
        except Exception:
            return True


def moteurs_disponibles():
    """Moteurs en ligne utilisables (clé présente ET service joignable), par ordre de préférence."""
    res = []
    for classe, var in ((Gemini, "GEMINI_API_KEY"), (Groq, "GROQ_API_KEY")):
        cle = os.environ.get(var, "").strip()
        if not cle:
            continue
        try:
            res.append(classe(cle))
        except Exception as e:
            _log(f"::warning::{classe.nom} non utilisable ({str(e)[:160]}) : ignoré.")
    return res
