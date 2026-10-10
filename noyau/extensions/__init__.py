"""
KRYPTONIX / noyau/extensions
=============================
Modules « Jarvis » : tout ce qui enrichit Kryptonix SANS rien retirer de l'existant.

Chaque module est indépendant et ne plante jamais le noyau : une panne devient une phrase.
Le chef d'orchestre `Extensions` :
  1. intercepte d'abord les confirmations (oui / non / annule) du coffre de sécurité ;
  2. propose chaque phrase aux modules, du plus précis au plus général ;
  3. tient le registre des outils (niveaux N1/N2/N3, serveur MCP, agent délégué) ;
  4. relie les événements de l'assistant aux effets sonores.

Si aucun module ne reconnaît la phrase, le noyau d'origine reprend la main (réflexes, agents,
cerveau...) exactement comme avant.
"""

from __future__ import annotations

import logging
import threading

from .agenda import Agenda
from .base import Module, Outil, normaliser
from .delegation import Delegation
from .domotique import Domotique
from .emails import Emails
from .finances import Finances
from .gestes import Gestes
from .hub_contenu import HubContenu
from .mcp_serveur import ServeurMCP
from .memoire_longue import MemoireLongue
from .navigateur import Navigateur
from .obs import OBS
from .pc_controle import PCControle
from .personnalites import Personnalites
from .reseau import Reseau
from .routeur_llm import RouteurLLM
from .securite import Securite
from .social import Social
from .sons import Sons
from .telephone import Telephone
from .utilitaires import Utilitaires
from .vision_ecran import VisionEcran
from .voix_avancee import VoixAvancee

LOG = logging.getLogger("extensions")

# Ordre de proposition des phrases : du plus spécifique au plus général.
ORDRE = [
    ("personnalites", Personnalites), ("routeur", RouteurLLM), ("utilitaires", Utilitaires),
    ("domotique", Domotique), ("pc", PCControle), ("obs", OBS),
    ("telephone", Telephone), ("emails", Emails), ("agenda", Agenda),          # « appelle... pour le rendez-vous » ≠ agenda
    ("memoire_longue", MemoireLongue), ("social", Social), ("navigateur", Navigateur),
    ("vision_ecran", VisionEcran), ("hub", HubContenu), ("finances", Finances), ("reseau", Reseau),
    ("gestes", Gestes), ("voix_avancee", VoixAvancee), ("delegation", Delegation), ("mcp", ServeurMCP),
]


class Extensions:
    def __init__(self, kx):
        self.kx = kx
        self.sons = Sons(kx.config)
        self.securite = Securite(kx)
        self.modules: dict[str, Module] = {}
        self.confidentiel_restant = 0          # tours pendant lesquels le cloud est interdit (finances...)
        self.contexte_piece = ""               # pièce d'où vient la commande (satellite), sinon ""
        self._verrou = threading.RLock()
        self._demarre = False
        self.pannes: dict[str, str] = {}

        for nom, classe in ORDRE:
            try:
                self.modules[nom] = classe(kx)
            except Exception as e:             # un module qui ne s'initialise pas n'empêche pas les autres
                LOG.exception("module %s non chargé", nom)
                self.pannes[nom] = f"{type(e).__name__}: {e}"
        self.sons.preparer_en_arriere_plan()
        kx.abonner(self._evenement)

    # -- accès --------------------------------------------------------------
    def module(self, nom: str):
        return self.modules.get(nom)

    @property
    def routeur(self) -> RouteurLLM:
        return self.modules["routeur"]

    def outils(self) -> list[Outil]:
        sortie: list[Outil] = []
        for m in self.modules.values():
            try:
                sortie += m.outils()
            except Exception:
                LOG.exception("outils de %s indisponibles", m.nom)
        return sortie

    def outil(self, nom: str) -> Outil | None:
        return next((o for o in self.outils() if o.nom == nom), None)

    def consigne_personnalite(self) -> str:
        p = self.modules.get("personnalites")
        return p.consigne() if p else ""

    def marquer_confidentiel(self, tours: int = 7) -> None:
        """Empêche l'envoi vers le cloud pendant les prochains échanges (le contexte contient des données privées)."""
        self.confidentiel_restant = max(self.confidentiel_restant, tours)

    # -- cycle de vie ---------------------------------------------------------
    def demarrer(self) -> None:
        if self._demarre:
            return
        self._demarre = True
        for nom, m in self.modules.items():
            try:
                m.demarrer()
            except Exception as e:
                LOG.exception("démarrage de %s", nom)
                self.pannes[nom] = f"{type(e).__name__}: {e}"
        self.sons.jouer("demarrage")

    def arreter(self) -> None:
        for m in self.modules.values():
            try:
                m.arreter()
            except Exception:
                pass
        self.sons.jouer("arret")

    # -- routage ------------------------------------------------------------
    def intercepter(self, texte: str) -> str | None:
        """Première étape de chaque phrase : une confirmation (oui / non / annule) est en attente ?"""
        if self.securite.en_attente:
            return self.securite.repondre(texte)
        return None

    def router(self, texte: str, commande: str) -> str | None:
        if self.confidentiel_restant > 0:
            self.confidentiel_restant -= 1
        rep = self.securite.intention(commande)
        if rep is not None:
            return rep
        rep = self.sons.intention(commande)
        if rep is not None:
            return rep
        for nom, m in self.modules.items():
            try:
                rep = m.intention(texte, commande)
            except Exception:
                LOG.exception("module %s en erreur sur « %s »", nom, texte[:60])
                continue
            if rep is not None:
                self.kx.memoire.journaliser("module", nom)
                return rep
        return None

    # -- événements -----------------------------------------------------------
    def _evenement(self, evenement: str, donnees: dict) -> None:
        if evenement == "etat":
            etat = donnees.get("etat")
            if etat == "reflexion" and not self.kx.config.get("sons_reflexion", False):
                return
            self.sons.joue_pour_etat(etat)

    # -- état ---------------------------------------------------------------
    def statut(self) -> dict:
        etat = {}
        for nom, m in self.modules.items():
            try:
                etat[nom] = {"description": m.description, "exemples": list(m.exemples), **m.statut()}
            except Exception as e:
                etat[nom] = {"description": m.description, "erreur": f"{type(e).__name__}"}
        return {"modules": etat, "pannes": self.pannes, "sons": len(self.sons.catalogue)}
