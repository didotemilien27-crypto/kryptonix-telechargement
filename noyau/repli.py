"""
KRYPTONIX / noyau/repli.py
===========================
Quand KRYPTONIX ne peut pas répondre, il le dit avec douceur, sans s'excuser
en boucle ni inventer, explique simplement pourquoi, indique ce qui est
possible à la place… puis ouvre la conversation sur un autre sujet.

Tous les « je n'ai pas pu » de l'application passent par ici : fichier non
lu, image sans modèle de vision, cerveau hors ligne, réponse vide, code refusé…
Ainsi le ton reste cohérent partout, et chaque échec se termine par une porte
ouverte plutôt que par un mur.

Règles de ton :
  - jamais de jargon ni de message d'erreur brut ;
  - jamais deux fois la même formule d'affilée ;
  - si la personne est tendue ou triste, on reste sobre : pas de changement de
    sujet bruyant, juste une présence et une aide proposée ;
  - si le même échec se répète en quelques instants, on raccourcit.
"""

from __future__ import annotations

import random
import threading
import time
from datetime import datetime

# ----------------------------------------------------------------------
#  Les cas connus : (raison, piste, le cerveau est-il nécessaire pour l'ouverture ?)
#  La raison complète la phrase « Désolé, … ». La piste dit ce qui reste possible.
# ----------------------------------------------------------------------
CAS: dict[str, tuple[str, str]] = {
    "hors_ligne": (
        "mon moteur de réflexion ne répond pas pour le moment",
        "Redémarre l'application et je reviens en pleine forme.",
    ),
    "delai": (
        "ma réflexion prend trop de temps sur cette machine pour cette question",
        "Tu peux la reformuler plus simplement, ou choisir un modèle plus petit "
        "dans RÉGLAGES → Assistant.",
    ),
    "anomalie": (
        "j'ai rencontré un petit souci technique en cherchant ta réponse",
        "Réessaie dans un instant ; le détail est gardé dans le journal si besoin.",
    ),
    "vide": (
        "je n'ai pas de réponse fiable à te donner là-dessus, et je préfère ne rien inventer",
        "Si tu me donnes un peu plus de contexte, je réessaie volontiers.",
    ),
    "modele_absent": (
        "le modèle d'IA choisi n'est pas installé chez moi",
        "Choisis-en un autre, ou télécharge-le, dans RÉGLAGES → Assistant.",
    ),
    "format": (
        "je ne sais pas encore lire ce type de fichier",
        "Je comprends les PDF, les textes, les images, les enregistrements audio "
        "et les vidéos. Tu en as un dans l'un de ces formats ?",
    ),
    "trop_gros": (
        "ce fichier est trop volumineux pour que je le digère",
        "Avec une version plus courte ou plus légère, je m'y mets tout de suite.",
    ),
    "illisible": (
        "je n'arrive pas à ouvrir ce fichier, il semble abîmé ou incomplet",
        "Essaie de le réexporter ou de le télécharger à nouveau, puis redépose-le.",
    ),
    "vue_absente": (
        "je n'ai pas encore d'yeux pour regarder les images",
        "Dans RÉGLAGES → Médias, tu peux m'installer la vue en un clic "
        "(Moondream, environ 1,7 Go) : ensuite je pourrai décrire tes photos.",
    ),
    "oreille_absente": (
        "je n'arrive pas à préparer mon oreille pour comprendre l'audio",
        "Vérifie ta connexion internet (il ne faut la télécharger qu'une fois), "
        "puis réessaie depuis RÉGLAGES → Médias.",
    ),
    "silence": (
        "je n'entends aucune parole dans cet enregistrement",
        "Il est peut-être trop faible ou ne contient que du bruit : "
        "essaie avec un autre fichier ou rapproche le micro.",
    ),
    "pdf_scanne": (
        "ce PDF est une photo de page scannée, sans texte que je puisse lire",
        "Si tu as la version texte ou Word, je la lis volontiers ; sinon, dépose une photo "
        "nette de la page comme image : avec la vue installée, je la lirai.",
    ),
    "ffmpeg_absent": (
        "il me manque l'outil qui lit les sons et les vidéos",
        "Réinstalle l'application : il est normalement inclus.",
    ),
}

# ----------------------------------------------------------------------
#  Formules d'entrée (elles se terminent par « , » : la raison suit en minuscule)
# ----------------------------------------------------------------------
ENTREES_NEUTRES = (
    "Désolé, ", "Je préfère être franc : ", "Petit aveu : ", "Là, je dois te dire que ",
    "Je ne vais pas te raconter d'histoires : ", "Je suis navré, ",
)
ENTREES_DOUCES = ("Je suis navré, ", "Désolé, ", "Je te dois une précision : ")

# ----------------------------------------------------------------------
#  Ouvertures sur un autre sujet : (étiquette, proposition, nécessite le cerveau)
# ----------------------------------------------------------------------
OUVERTURES: tuple[tuple[str, str, bool], ...] = (
    ("meteo", "veux-tu que je te donne la météo du jour ?", False),
    ("actus", "je peux te lire les titres de l'actualité, si ça te dit.", False),
    ("redaction", "on pourrait rédiger ensemble une lettre, un CV ou un petit rapport.", True),
    ("cours", "je peux t'expliquer une notion de cours ou te poser quelques questions pour réviser.", True),
    ("pc", "je peux jeter un œil à la santé de ton PC : processeur, mémoire, disque.", False),
    ("code", "on pourrait tester un petit bout de code ou une page HTML dans le bac à sable.", False),
    ("culture", "je peux te raconter une anecdote ou te lancer une devinette.", True),
    ("memoire", "je peux aussi retenir pour toi un rendez-vous, une idée ou un détail important.", False),
    ("cuisine", "une idée de recette avec ce que tu as sous la main, ça te tente ?", True),
)
LIENS = ("En attendant, ", "Et si on changeait d'air ? ", "Pour ne pas rester sur un échec : ",
         "Pendant que j'y pense, ", "Autre chose, peut-être : ")

# Chaque tournure complète « veux-tu qu'… » (pas d'infinitif : « s'occupe de organiser » serait fautif).
SOIR = ("on prépare ta journée de demain", "on organise ta journée de demain")
MATIN = ("on organise ta journée", "on fasse le point sur ce que tu as à faire aujourd'hui")


class Repli:
    """Fabrique des réponses douces qui ouvrent une porte au lieu de fermer une conversation."""

    def __init__(self, config: dict):
        self.config = config
        self._verrou = threading.Lock()
        self._derniere_entree = ""
        self._derniere_ouverture = ""
        self._dernier_lien = ""
        self._vus: dict[str, float] = {}      # cas → instant du dernier échec

    # ------------------------------------------------------------------
    def cas(self, cle: str, registre: str = "neutre", *, ouvrir: bool = True) -> str:
        """Message complet pour un échec connu (voir CAS)."""
        raison, piste = CAS.get(cle, CAS["anomalie"])
        # Le cerveau n'est pas disponible : on ne propose que ce qui marche sans lui.
        return self.refuser(raison, piste, registre,
                            avec_cerveau=cle not in ("hors_ligne", "modele_absent"),
                            ouvrir=ouvrir, cle=cle)

    def refuser(self, raison: str, piste: str = "", registre: str = "neutre", *,
                avec_cerveau: bool = True, ouvrir: bool = True, cle: str = "") -> str:
        """
        Compose : entrée douce + raison + piste + ouverture sur un autre sujet.
        `raison` complète la phrase d'entrée (« Désolé, <raison>. »).
        """
        maintenant = time.time()
        with self._verrou:
            repete = bool(cle) and maintenant - self._vus.get(cle, 0) < 90
            if cle:
                self._vus[cle] = maintenant

            doux = registre in ("stress", "tristesse")
            entree = self._choisir(ENTREES_DOUCES if doux else ENTREES_NEUTRES, "_derniere_entree")
            raison = raison.strip().rstrip(".!? ")
            # Minuscule après « Désolé, » — sauf pour un sigle ou un nom propre (HTML, Moondream…).
            if raison[1:2].islower():
                raison = raison[:1].lower() + raison[1:]
            phrase = f"{entree}{raison}."
            phrase = phrase[:1].upper() + phrase[1:]

            morceaux = [phrase]
            if piste:
                morceaux.append(piste.strip())

            # Même échec répété en quelques instants : on reste bref, sans nouvelle ouverture.
            if repete:
                return " ".join(morceaux)

            if doux:
                morceaux.append("Je reste là : dis-moi ce dont tu as besoin et on s'y met.")
            elif ouvrir and self.config.get("ouverture_sujets", True):
                morceaux.append(self._ouverture(avec_cerveau))
            return " ".join(morceaux)

    # ------------------------------------------------------------------
    def _choisir(self, options: tuple[str, ...], memoire: str) -> str:
        candidats = [o for o in options if o != getattr(self, memoire)] or list(options)
        choix = random.choice(candidats)
        setattr(self, memoire, choix)
        return choix

    def _ouverture(self, avec_cerveau: bool) -> str:
        heure = datetime.now().hour
        propositions = [(e, t) for e, t, besoin in OUVERTURES if avec_cerveau or not besoin]
        # Un clin d'œil à l'heure de la journée, sans le répéter à chaque fois.
        if avec_cerveau and heure >= 19:
            propositions.append(("planning_soir",
                                 f"veux-tu qu'{random.choice(SOIR)} ?"))
        elif avec_cerveau and 6 <= heure < 11:
            propositions.append(("planning_matin",
                                 f"veux-tu qu'{random.choice(MATIN)} ?"))
        candidats = [p for p in propositions if p[0] != self._derniere_ouverture] or propositions
        etiquette, texte = random.choice(candidats)
        self._derniere_ouverture = etiquette
        lien = self._choisir(LIENS, "_dernier_lien")
        # Minuscule après une virgule ou deux-points (« En attendant, veux-tu… »),
        # majuscule seulement après une question (« Et si on changeait d'air ? Je peux… »).
        if lien.rstrip().endswith("?"):
            return lien + texte[:1].upper() + texte[1:]
        return lien + texte
