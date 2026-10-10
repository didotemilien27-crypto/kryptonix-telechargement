"""
KRYPTONIX / noyau/anticipation.py
==================================
Moteur d'anticipation : repère dans ce que dit l'utilisateur des intentions
implicites (préparer un voyage, rédiger un rapport, chercher un cadeau...) et
propose l'action correspondante avant qu'on la lui demande explicitement.

Approche volontairement simple et lisible : des motifs par catégorie, une
suggestion associée, et un anti-répétition pour ne pas harceler l'utilisateur
avec la même remarque à chaque message.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field


@dataclass
class Signal:
    categorie: str
    motifs: tuple[str, ...]
    suggestion: str
    # Si vrai, ne se déclenche que sur une intention explicite ("je dois", "il faut que")
    # combinée au motif, pour éviter les faux positifs sur une simple mention.
    exige_intention: bool = False


MOTS_INTENTION = (
    "je dois", "il faut que", "je vais devoir", "je devrais", "faut que",
    "j'ai besoin de", "je prépare", "je pense à", "j'organise", "je planifie",
)

SIGNAUX: tuple[Signal, ...] = (
    Signal(
        "voyage",
        ("voyage", "pars en vacances", "je pars à", "billet d'avion", "réserver un hôtel",
         "valise", "itinéraire", "je vais à l'étranger"),
        "Je peux te préparer une checklist de valise et un itinéraire jour par jour "
        "si tu me donnes la destination et les dates. Dis « crée un document itinéraire ».",
    ),
    Signal(
        "rapport_professionnel",
        ("rapport à rendre", "compte rendu à faire", "présentation pour le boulot",
         "réunion demain", "présenter à mon équipe", "deadline", "rendre mon rapport"),
        "Je peux rédiger une trame complète maintenant — dis-moi le sujet et le format "
        "voulu (Word, PDF, PowerPoint texte) et je m'en occupe.",
        exige_intention=False,
    ),
    Signal(
        "cadeau",
        ("anniversaire de", "trouver un cadeau", "offrir quelque chose", "idée de cadeau",
         "fête des mères", "fête des pères", "cadeau de noël", "cadeau de mariage"),
        "Donne-moi la personne, son âge et ses centres d'intérêt : je peux te sortir "
        "une liste d'idées et ouvrir les boutiques correspondantes.",
    ),
    Signal(
        "recherche_emploi",
        ("chercher du travail", "candidature", "lettre de motivation", "cv à jour",
         "entretien d'embauche", "postuler"),
        "Je peux rédiger la lettre de motivation ou remettre ton CV en forme "
        "dans un fichier Word — donne-moi le poste visé.",
    ),
    Signal(
        "etude_revision",
        ("réviser pour", "examen la semaine", "contrôle de", "partiel de", "concours de"),
        "Je peux te préparer une fiche de révision structurée sur ce sujet, "
        "prête à imprimer.",
    ),
    Signal(
        "budget_finances",
        ("faire mon budget", "suivre mes dépenses", "calculer mes économies",
         "comparer les prix", "tableau de dépenses"),
        "Un tableau Excel avec tes postes de dépense et des totaux automatiques "
        "serait utile ? Donne-moi tes catégories et je le monte.",
    ),
    Signal(
        "demenagement",
        ("déménager", "changer d'appartement", "nouveau logement", "état des lieux"),
        "Je peux générer une checklist de déménagement complète (tâches, "
        "démarches administratives, jour J) — tu veux que je la crée ?",
    ),
    Signal(
        "code_bug",
        ("mon code plante", "j'ai une erreur", "ça compile pas", "exception python",
         "traceback"),
        "Colle-moi le message d'erreur complet ou le code : je peux l'exécuter "
        "dans la sandbox pour reproduire le problème.",
    ),
    Signal(
        "machine_lente",
        ("mon pc rame", "mon ordinateur est lent", "ça freeze", "ventilateur à fond"),
        "Je regarde la télémétrie et les processus les plus gourmands tout de suite.",
    ),
    Signal(
        "document_mentionne_sans_action",
        ("j'ai un pdf", "j'ai un document", "un fichier à lire", "un rapport à lire",
         "des notes à trier"),
        "Dépose le fichier dans le dashboard ou dis « aspire » suivi du chemin : "
        "je le lis et je le résume.",
        exige_intention=False,
    ),
)


def _contient_intention(texte: str) -> bool:
    return any(mot in texte for mot in MOTS_INTENTION)


@dataclass
class Anticipation:
    """
    Analyse chaque message utilisateur et retourne, au maximum, une suggestion
    pertinente à ajouter à la réponse du cerveau.
    """
    memoire: object = None
    _derniere_categorie: str | None = field(default=None, init=False, repr=False)
    _derniere_fois: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    delai_repetition: float = 600.0  # ne resuggère pas la même chose avant 10 min

    def analyser(self, texte: str) -> str | None:
        minuscule = texte.lower()
        intention_presente = _contient_intention(minuscule)

        for signal in SIGNAUX:
            if signal.exige_intention and not intention_presente:
                continue
            if not any(motif in minuscule for motif in signal.motifs):
                continue

            derniere_fois = self._derniere_fois.get(signal.categorie, 0.0)
            if time.time() - derniere_fois < self.delai_repetition:
                return None  # déjà suggéré récemment, on n'insiste pas

            self._derniere_fois[signal.categorie] = time.time()
            self._derniere_categorie = signal.categorie
            if self.memoire:
                self.memoire.journaliser("anticipation", signal.categorie)
            return signal.suggestion

        return None

    def reinitialiser(self) -> None:
        """Utile après un « oublie tout » : on redonne une chance à chaque suggestion."""
        self._derniere_fois.clear()
        self._derniere_categorie = None
