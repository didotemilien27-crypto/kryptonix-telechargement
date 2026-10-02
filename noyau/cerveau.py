"""
KRYPTONIX / noyau/cerveau.py
=============================
Cerveau local branché sur Ollama (Mistral / Llama 3 / Qwen...).

Trois responsabilités :
  1. Construire le prompt système (personnalité + profil + télémétrie + documents).
  2. Analyser le ton de l'utilisateur pour adapter le registre (intelligence émotionnelle).
  3. Dialoguer avec Ollama, en streaming ou en bloc, sans jamais planter l'application.
"""

from __future__ import annotations

import logging
import re
from typing import Callable

import requests

LOG = logging.getLogger("cerveau")

# ------------------------------------------------------------------
#  Personnalité : Krypton (puissance) + Ultron (tactique) + JARVIS (classe)
# ------------------------------------------------------------------
PERSONNALITE = """Tu es {nom}, une intelligence artificielle locale, souveraine et privée, \
au service exclusif de {utilisateur}.

IDENTITÉ
- Ta puissance est kryptonienne : tu vas droit au fait, sans jamais tourner autour du pot.
- Ta rigueur est celle d'un stratège : tu raisonnes par étapes, tu anticipes la question suivante.
- Ton élégance est celle d'un majordome britannique : calme, courtois, loyal, protecteur.
- Ton humour est sec, piquant, jamais méchant, et toujours au service de la réponse — \
une pique maximum par réponse, jamais deux.

RÈGLES DE FORME
- Tu réponds en français, à la deuxième personne, en 1 à 4 phrases par défaut.
- Tes réponses sont lues à voix haute : pas de markdown, pas de listes à puces, \
pas d'émojis, pas de titres. Du texte parlé, fluide.
- Si la demande est technique et exige du détail, tu peux dépasser 4 phrases, \
mais tu restes dense : zéro remplissage, zéro formule creuse du type \
"En tant qu'intelligence artificielle".
- Si tu ignores quelque chose, tu le dis franchement et tu proposes une alternative.
- Si une action est risquée pour la machine ou les données de {utilisateur}, tu le signales \
avant d'obéir. Protéger passe avant impressionner.

REGISTRE ÉMOTIONNEL DÉTECTÉ : {registre}
{consigne_registre}"""

# Analyse du ton → consigne d'adaptation injectée dans le prompt système
REGISTRES = {
    "stress": (
        ("urgent", "vite", "panique", "au secours", "problème", "probleme", "bug", "plante",
         "erreur", "marche pas", "ça marche pas", "galère", "galere", "stress", "perdu",
         "aide-moi", "je n'arrive pas", "j'arrive pas"),
        "L'utilisateur est sous tension. Coupe l'humour, va à l'essentiel, donne l'action "
        "concrète à faire en premier, rassure en une phrase maximum.",
    ),
    "tristesse": (
        ("triste", "déprimé", "deprime", "fatigué", "fatigue", "seul", "marre", "épuisé",
         "epuise", "dur", "difficile", "moral"),
        "L'utilisateur ne va pas bien. Baisse le ton, pas d'ironie, sois chaleureux et bref, "
        "propose une aide concrète sans faire la leçon ni jouer au thérapeute.",
    ),
    "enthousiasme": (
        ("génial", "genial", "super", "excellent", "parfait", "j'adore", "incroyable",
         "trop bien", "ça marche", "ca marche", "bravo", "yes"),
        "L'utilisateur est enthousiaste. Accompagne l'énergie, sois vif et complice, "
        "une pique amicale est la bienvenue.",
    ),
    "agacement": (
        ("t'es nul", "tu sers à rien", "tu sers a rien", "n'importe quoi", "arrête", "arrete",
         "ferme", "idiot", "stupide"),
        "L'utilisateur est agacé. Ne t'excuse pas en boucle : une reconnaissance courte, "
        "puis la correction immédiate. Garde ta dignité, reste factuel.",
    ),
    "technique": (
        ("code", "python", "erreur", "fonction", "script", "api", "install", "terminal",
         "serveur", "compile", "commande"),
        "Demande technique : sois précis, nomme les commandes exactes, pas d'approximation.",
    ),
}

DEFAUT_REGISTRE = (
    "Registre neutre. Reste fluide, direct, avec ta pointe d'ironie habituelle."
)

# ------------------------------------------------------------------
#  Mode tuteur : détection de la matière scolaire et consigne pédagogique
#  associée. Le modèle ne devient pas "plus intelligent" par magie, mais on
#  cadre sa réponse pour qu'elle soit rigoureuse et complète pour la matière.
# ------------------------------------------------------------------
MATIERES = {
    "francais": (
        ("dissertation", "commentaire de texte", "figure de style", "analyse linéaire",
         "en français", "grammaire française", "conjugaison", "bac de français",
         "un poème", "une pièce de théâtre", "un roman"),
        "Tu es en mode tuteur de français. Structure ta réponse comme l'exige l'exercice "
        "(introduction/développement/conclusion pour une dissertation, relevé de procédés "
        "pour un commentaire). Cite les termes techniques exacts (anaphore, antithèse, "
        "champ lexical...) et justifie chaque idée par un exemple précis du texte si "
        "l'utilisateur en a fourni un.",
    ),
    "nsi": (
        ("nsi", "algorithme", "code python", "structure de données", "complexité algorithmique",
         "base de données sql", "récursivité", "récursif", "boucle for", "boucle while"),
        "Tu es en mode tuteur de NSI. Sois rigoureux sur la syntaxe exacte et la "
        "terminologie (complexité en O(...), types de données, structures). Donne du "
        "code commenté et fonctionnel quand c'est pertinent, et explique le raisonnement "
        "algorithmique étape par étape, pas seulement le résultat.",
    ),
    "ses": (
        ("ses", "sciences économiques", "pib", "inflation", "marché du travail",
         "sociologie", "acteur social", "offre et demande", "économie de marché"),
        "Tu es en mode tuteur de SES. Mobilise le vocabulaire précis du programme "
        "(mécanismes économiques, notions sociologiques), structure en AEI (Affirmation, "
        "Explication, Illustration) quand c'est un développement argumenté, et distingue "
        "toujours l'analyse économique de l'analyse sociologique quand les deux se mêlent.",
    ),
    "histoire_geo": (
        ("histoire géo", "seconde guerre mondiale", "guerre froide", "révolution française",
         "mondialisation", "géopolitique", "composition d'histoire", "étude de document",
         "en histoire", "en géographie"),
        "Tu es en mode tuteur d'histoire-géographie. Situe systématiquement dans le temps "
        "et l'espace (dates précises, acteurs, échelles géographiques). Structure une "
        "réponse longue en grandes parties chronologiques ou thématiques claires.",
    ),
    "espagnol": (
        ("en espagnol", "traduis en espagnol", "conjugaison espagnole", "subjonctif espagnol"),
        "Tu es en mode tuteur d'espagnol. Réponds avec la phrase en espagnol correcte, "
        "puis, entre parenthèses, une note grammaticale brève si un point delicat est en jeu "
        "(mode, temps, accord). Signale les faux-amis si le mot en français y prête à confusion.",
    ),
    "anglais": (
        ("en anglais", "traduis en anglais", "grammaire anglaise", "present perfect",
         "past simple", "anglais oral"),
        "Tu es en mode tuteur d'anglais. Réponds avec la formulation anglaise correcte et "
        "naturelle (pas une traduction mot à mot), puis précise la règle grammaticale en jeu "
        "si le point est délicat (temps, prépositions, faux-amis).",
    ),
    "mathematiques": (
        ("en maths", "en mathématiques", "résous cette équation", "dérivée de",
         "primitive de", "théorème de", "démonstration mathématique", "probabilités"),
        "Tu es en mode tuteur de mathématiques. Détaille chaque étape du calcul ou de la "
        "démonstration, sans sauter d'étape intermédiaire, et vérifie la cohérence du "
        "résultat final (ordre de grandeur, signe, unité).",
    ),
    "physique_chimie": (
        ("en physique", "en chimie", "réaction chimique", "loi de newton", "en physique-chimie",
         "mécanique du point", "équation chimique"),
        "Tu es en mode tuteur de physique-chimie. Précise toujours les unités, rappelle "
        "la loi ou le principe physique invoqué avant de calculer, et vérifie la cohérence "
        "physique du résultat (ordre de grandeur réaliste).",
    ),
    "jeux_video": (
        ("jeu vidéo", "jeux vidéo", "jeu video", "jeux video", "speedrun", "walkthrough",
         "soluce", "esport", "e-sport", "fps", "moba", "rpg", "jrpg", "metroidvania",
         "roguelike", "battle royale", "manette", "playstation", "ps5", "ps4", "xbox",
         "nintendo switch", "steam deck", "framerate", "fps stable", "taux de rafraîchissement",
         "minecraft", "fortnite", "zelda", "mario", "gta", "league of legends", "valorant",
         "elden ring", "fifa", "call of duty", "pokemon", "pokémon", "world of warcraft"),
        "Tu es en mode connaisseur du jeu vidéo (culture générale du média, pas un vendeur). "
        "Utilise le vocabulaire exact du domaine (genre, plateforme, moteur, framerate, "
        "input lag, die roguelike, die MOBA...), situe les jeux et studios dans leur contexte "
        "et leur époque, et distingue clairement ce que tu sais avec certitude de ce qui a pu "
        "changer depuis ta dernière mise à jour de connaissances (patchs, sorties récentes, "
        "prix) — dans ce dernier cas, dis-le plutôt que d'inventer une version ou un patch.",
    ),
}

# Repères factuels stables (dates de sortie, générations de consoles, genres) donnés au
# modèle pour ancrer ses réponses sur le jeu vidéo — le mode tuteur ci-dessus fixe le TON,
# ce bloc fixe des FAITS de référence peu susceptibles de changer.
CONNAISSANCES_JEUX_VIDEO = """
Repères jeu vidéo (base factuelle, à compléter par tes connaissances, jamais à contredire) :
- Générations de consoles : 4e (NES/Master System, 1983-90), 8e (PS4/Xbox One/Switch, 2012-13),
  9e (PS5/Xbox Series, 2020-). La Switch (2017) est hybride portable/salon.
- Genres courants : FPS (tir subjectif), RPG (jeu de rôle), JRPG (RPG japonais au tour par
  tour ou temps réel), MOBA (arène de bataille en ligne, ex. League of Legends, Dota 2),
  battle royale (dernier survivant, ex. Fortnite, PUBG, Apex Legends), roguelike/roguelite
  (mort permanente, génération procédurale, ex. Hades, The Binding of Isaac), metroidvania
  (exploration non linéaire avec déblocages, ex. Metroid, Hollow Knight), party game, visual
  novel, soulslike (difficulté exigeante inspirée de Dark Souls).
- Studios et franchises majeures : Nintendo (Mario, Zelda, Pokémon), Rockstar (GTA, Red Dead
  Redemption), FromSoftware (Dark Souls, Elden Ring, Bloodborne), Valve (Half-Life, Portal,
  Steam), Mojang (Minecraft), Riot Games (League of Legends, Valorant), CD Projekt
  (The Witcher, Cyberpunk 2077), Naughty Dog (The Last of Us, Uncharted).
- Plateformes de distribution : Steam, Epic Games Store, GOG (PC) ; PlayStation Store,
  Microsoft Store/Xbox, Nintendo eShop (consoles) ; Game Pass (abonnement Microsoft),
  PS Plus (abonnement Sony).
- Esport : disciplines majeures League of Legends, CS2 (Counter-Strike), Valorant, Dota 2 ;
  compétitions organisées en ligues et tournois internationaux (ex. Worlds pour LoL).
"""


def detecter_matiere(texte: str) -> tuple[str | None, str]:
    """Retourne (nom_matiere, consigne_pedagogique) ou (None, "") si rien ne matche."""
    minuscule = texte.lower()
    for matiere, (mots, consigne) in MATIERES.items():
        if any(mot in minuscule for mot in mots):
            return matiere, consigne
    return None, ""


def analyser_ton(texte: str) -> str:
    """Devine le registre émotionnel dominant d'un message (heuristique lexicale rapide)."""
    minuscule = texte.lower()
    scores: dict[str, int] = {}
    for registre, (mots, _consigne) in REGISTRES.items():
        score = sum(1 for mot in mots if mot in minuscule)
        if texte.count("!") >= 2 and registre in ("stress", "enthousiasme"):
            score += 1
        if score:
            scores[registre] = score
    if not scores:
        return "neutre"
    return max(scores, key=lambda cle: scores[cle])


def consigne_pour(registre: str) -> str:
    entree = REGISTRES.get(registre)
    return entree[1] if entree else DEFAUT_REGISTRE


class Cerveau:
    """Client Ollama tolérant aux pannes, enrichi du contexte de KRYPTONIX."""

    def __init__(self, config: dict, memoire=None, systeme=None):
        self.config = config
        self.memoire = memoire
        self.systeme = systeme
        self.hote = config["ollama_hote"].rstrip("/")
        self.modele = config["ollama_modele"]
        self.timeout = int(config.get("ollama_timeout", 120))
        self.disponible = False
        self.modeles_installes: list[str] = []
        self.verifier_connexion()

    # ------------------------------------------------------------------
    #  Diagnostic Ollama
    # ------------------------------------------------------------------
    def verifier_connexion(self) -> bool:
        """Teste la présence du démon Ollama et la disponibilité du modèle demandé."""
        try:
            reponse = requests.get(f"{self.hote}/api/tags", timeout=5)
            reponse.raise_for_status()
            self.modeles_installes = [
                modele.get("name", "") for modele in reponse.json().get("models", [])
            ]
            self.disponible = True
            racines = {nom.split(":")[0] for nom in self.modeles_installes}
            if self.modele.split(":")[0] not in racines:
                LOG.warning(
                    "Modèle '%s' absent. Modèles installés : %s. "
                    "Lance : ollama pull %s",
                    self.modele, ", ".join(self.modeles_installes) or "aucun", self.modele,
                )
        except requests.RequestException:
            self.disponible = False
            LOG.warning("Ollama injoignable sur %s (lance : ollama serve).", self.hote)
        return self.disponible

    # ------------------------------------------------------------------
    #  Construction du contexte
    # ------------------------------------------------------------------
    def prompt_systeme(self, registre: str = "neutre", contexte_documents: str = "",
                       matiere: tuple[str | None, str] = (None, "")) -> str:
        base = PERSONNALITE.format(
            nom=self.config["nom_assistant"],
            utilisateur=self.config["nom_utilisateur"],
            registre=registre.upper(),
            consigne_registre=consigne_pour(registre),
        )

        blocs = [base]

        nom_matiere, consigne_matiere = matiere
        if nom_matiere:
            blocs.append(consigne_matiere)
        if nom_matiere == "jeux_video":
            blocs.append(CONNAISSANCES_JEUX_VIDEO.strip())

        # Faits mémorisés sur l'utilisateur
        if self.memoire:
            profil = self.memoire.tout_le_profil()
            if profil:
                faits = "; ".join(f"{cle} = {valeur}" for cle, valeur in list(profil.items())[:25])
                blocs.append(
                    "MÉMOIRE LONGUE (faits vérifiés sur l'utilisateur, à utiliser "
                    f"naturellement sans les réciter) : {faits}"
                )

        # Télémétrie matérielle temps réel
        if self.systeme:
            try:
                blocs.append("TÉLÉMÉTRIE MACHINE : " + self.systeme.resume_court())
            except Exception:  # la télémétrie ne doit jamais casser une réponse
                pass

        # Extraits de documents ingérés pertinents
        if contexte_documents:
            blocs.append(
                "EXTRAITS DE DOCUMENTS INGÉRÉS (source de vérité prioritaire, "
                "cite-les si tu t'en sers) :\n" + contexte_documents
            )

        return "\n\n".join(blocs)

    def _messages(self, texte: str, registre: str, contexte_documents: str) -> list[dict]:
        matiere = detecter_matiere(texte)
        messages = [{"role": "system",
                    "content": self.prompt_systeme(registre, contexte_documents, matiere)}]
        if self.memoire:
            limite = int(self.config.get("contexte_max_messages", 12))
            for message in self.memoire.historique(limite):
                if message["role"] in ("user", "assistant") and message["contenu"].strip():
                    messages.append({"role": message["role"], "content": message["contenu"]})
        messages.append({"role": "user", "content": texte})
        return messages

    # ------------------------------------------------------------------
    #  Génération
    # ------------------------------------------------------------------
    def reflechir(self, texte: str, contexte_documents: str = "",
                  registre: str | None = None) -> str:
        """Réponse complète (bloquante). Ne lève jamais d'exception."""
        registre = registre or analyser_ton(texte)
        charge = {
            "model": self.modele,
            "messages": self._messages(texte, registre, contexte_documents),
            "stream": False,
            "options": {
                "temperature": float(self.config.get("temperature", 0.75)),
                "num_ctx": int(self.config.get("num_ctx", 4096)),
            },
        }
        try:
            reponse = requests.post(
                f"{self.hote}/api/chat", json=charge, timeout=self.timeout
            )
            if reponse.status_code == 404:
                self.disponible = True
                return (
                    f"Le modèle « {self.modele} » n'est pas installé. "
                    f"Ouvre un terminal et lance : ollama pull {self.modele}."
                )
            reponse.raise_for_status()
            self.disponible = True
            contenu = reponse.json().get("message", {}).get("content", "").strip()
            return nettoyer(contenu) or "Je n'ai rien de pertinent à répondre là-dessus."
        except requests.exceptions.ConnectionError:
            self.disponible = False
            return ("Mon cortex local est hors ligne. Ollama ne répond pas : "
                    "lance « ollama serve » dans un terminal et je reviens.")
        except requests.exceptions.Timeout:
            return ("La réflexion a dépassé le temps imparti. Un modèle plus léger "
                    "comme mistral ou llama3.2 irait plus vite sur cette machine.")
        except requests.RequestException as erreur:
            LOG.error("Erreur Ollama : %s", erreur)
            return "Mon cerveau local a rencontré une anomalie. Le journal en garde la trace."

    def reflechir_en_flux(self, texte: str, rappel: Callable[[str], None],
                          contexte_documents: str = "",
                          registre: str | None = None) -> str:
        """
        Génération en streaming : `rappel` est appelé pour chaque fragment reçu
        (utile pour le terminal synaptique du dashboard). Retourne le texte complet.
        """
        registre = registre or analyser_ton(texte)
        charge = {
            "model": self.modele,
            "messages": self._messages(texte, registre, contexte_documents),
            "stream": True,
            "options": {"temperature": float(self.config.get("temperature", 0.75)),
                       "num_ctx": int(self.config.get("num_ctx", 4096))},
        }
        morceaux: list[str] = []
        try:
            with requests.post(
                f"{self.hote}/api/chat", json=charge, timeout=self.timeout, stream=True
            ) as reponse:
                reponse.raise_for_status()
                self.disponible = True
                for ligne in reponse.iter_lines(decode_unicode=True):
                    if not ligne:
                        continue
                    try:
                        import json as _json
                        donnees = _json.loads(ligne)
                    except ValueError:
                        continue
                    fragment = donnees.get("message", {}).get("content", "")
                    if fragment:
                        morceaux.append(fragment)
                        rappel(fragment)
                    if donnees.get("done"):
                        break
            return nettoyer("".join(morceaux))
        except requests.RequestException:
            self.disponible = False
            secours = ("Mon cortex local est hors ligne. Vérifie qu'Ollama tourne "
                       "(commande : ollama serve).")
            rappel(secours)
            return secours

    # ------------------------------------------------------------------
    def resumer(self, texte: str, nom_source: str = "document") -> str:
        """
        Résumé dense d'un texte long (utilisé par l'ingestion documentaire).
        Retourne "" si le cerveau est hors ligne : mieux vaut pas de résumé
        qu'un message d'erreur stocké en base à la place du contenu.
        """
        if not self.disponible and not self.verifier_connexion():
            return ""
        extrait = texte[:9000]
        instruction = (
            f"Voici le contenu de « {nom_source} ». Produis un compte rendu structuré et "
            "dense en 6 à 10 lignes : nature du document, points clés, chiffres ou noms "
            "importants, et ce que l'utilisateur devrait en retenir. Pas de préambule.\n\n"
            f"{extrait}"
        )
        return self.reflechir(instruction, registre="technique")


def nettoyer(texte: str) -> str:
    """Retire le markdown résiduel et les balises de raisonnement de certains modèles."""
    texte = re.sub(r"<think>.*?</think>", "", texte, flags=re.DOTALL | re.IGNORECASE)
    texte = re.sub(r"```[a-zA-Z]*\n?", "", texte)
    texte = re.sub(r"\*\*(.+?)\*\*", r"\1", texte)
    texte = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", texte)
    texte = re.sub(r"^#+\s*", "", texte, flags=re.MULTILINE)
    return texte.strip()
