"""
KRYPTONIX / noyau/extensions/securite.py
=========================================
Sécurité graduée : chaque outil a un niveau.

  N1  libre            : s'exécute tout de suite (lumière, météo, minuteur...)
  N2  confirmation     : « Je confirme ? » → il faut un OUI sans ambiguïté
  N3  confirmation stricte + délai annulable : extinction du PC, suppression, appel...

Confirmation vocale STRICTE : toute négation (« non », « pas », « annule », « surtout pas »...)
annule, même au milieu d'un « oui mais non ». Une réponse ambiguë annule aussi : dans le doute,
on n'exécute pas.

Rôles « toujours autoriser » : un outil N2 peut être autorisé durablement (« toujours autoriser
les lumières ») et révoqué à tout moment. Un outil N3 n'est JAMAIS autorisable en permanence.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid

from ..config import DOSSIER_DONNEES
from .base import ErreurModule, normaliser

LOG = logging.getLogger("securite")

AFFIRMATIFS = {"oui", "ouais", "yes", "ok", "okay", "confirme", "je confirme", "confirmer", "d'accord",
               "bien sur", "vas-y", "vas y", "fais-le", "fais le", "go", "affirmatif", "exactement",
               "oui je confirme", "oui vas-y", "oui vas y", "oui bien sur", "oui fais-le", "oui fais le",
               "carrement", "volontiers", "certainement", "tout a fait"}
NEGATIONS = re.compile(r"\b(non|nan|nope|pas|ne|n'|jamais|annule|annuler|annulation|stop|arrete|"
                       r"laisse tomber|abandonne|surtout|rien|aucun|finalement)\b")

DELAI_VALIDITE = 60.0      # secondes pour répondre à une demande de confirmation
DELAI_N3 = 10.0            # secondes d'annulation possible après un OUI sur un N3


def analyser_confirmation(texte: str) -> str:
    """Retourne 'oui', 'non' ou 'ambigu'. Les négations l'emportent toujours."""
    t = normaliser(texte)
    t = re.sub(r"[.!?,;]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return "ambigu"
    if NEGATIONS.search(t):
        return "non"
    if t in AFFIRMATIFS:
        return "oui"
    mots = t.split()
    if len(mots) <= 5 and mots[0] in {"oui", "ouais", "ok", "okay", "confirme", "go", "yes", "d'accord"}:
        return "oui"
    return "ambigu"


class Securite:
    def __init__(self, kx):
        self.kx = kx
        self._verrou = threading.RLock()
        self.chemin = os.path.join(DOSSIER_DONNEES, "permissions.json")
        self._attente: dict | None = None        # confirmation en cours
        self._minuteur_n3: dict | None = None    # exécution différée annulable
        self._donnees = {"toujours_autoriser": {}, "niveaux": {}}
        self._charger()

    # -- persistance ----------------------------------------------------
    def _charger(self) -> None:
        try:
            with open(self.chemin, "r", encoding="utf-8") as f:
                self._donnees.update(json.load(f))
        except (OSError, ValueError):
            pass

    def _sauver(self) -> None:
        try:
            with open(self.chemin, "w", encoding="utf-8") as f:
                json.dump(self._donnees, f, indent=2, ensure_ascii=False)
        except OSError as e:
            LOG.warning("permissions non sauvegardées : %s", e)

    # -- niveaux --------------------------------------------------------
    def niveau_de(self, nom: str, defaut: int = 1) -> int:
        with self._verrou:
            surcharge = self._donnees["niveaux"].get(nom)
        if surcharge in (1, 2, 3):
            return int(surcharge)
        outil = self.kx.extensions.outil(nom) if hasattr(self.kx, "extensions") else None
        return outil.niveau if outil else defaut

    def definir_niveau(self, nom: str, niveau: int) -> None:
        if niveau not in (1, 2, 3):
            raise ValueError("niveau 1, 2 ou 3")
        with self._verrou:
            self._donnees["niveaux"][nom] = niveau
            if niveau == 3:
                self._donnees["toujours_autoriser"].pop(nom, None)
            self._sauver()

    def autoriser_toujours(self, nom: str) -> bool:
        if self.niveau_de(nom) >= 3:
            return False          # un N3 ne devient jamais automatique
        with self._verrou:
            self._donnees["toujours_autoriser"][nom] = True
            self._sauver()
        return True

    def revoquer(self, nom: str | None = None) -> int:
        with self._verrou:
            if nom is None:
                n = len(self._donnees["toujours_autoriser"])
                self._donnees["toujours_autoriser"] = {}
            else:
                n = 1 if self._donnees["toujours_autoriser"].pop(nom, None) else 0
            self._sauver()
        return n

    def autorises(self) -> list[str]:
        with self._verrou:
            return sorted(self._donnees["toujours_autoriser"])

    def permissions(self) -> dict:
        outils = self.kx.extensions.outils() if hasattr(self.kx, "extensions") else []
        with self._verrou:
            return {
                "outils": [{"nom": o.nom, "domaine": o.domaine, "description": o.description,
                            "niveau": self.niveau_de(o.nom, o.niveau),
                            "toujours": bool(self._donnees["toujours_autoriser"].get(o.nom))}
                           for o in outils],
                "en_attente": self._attente["description"] if self._attente else "",
            }

    # -- exécution protégée --------------------------------------------
    def lancer(self, nom: str, args: dict, niveau: int | None = None, description: str = "") -> str:
        """Exécute l'outil `nom` en respectant son niveau de sécurité."""
        outil = self.kx.extensions.outil(nom)
        if outil is None:
            return f"L'action « {nom} » n'existe pas."
        niv = niveau or self.niveau_de(nom, outil.niveau)
        description = description or outil.description
        with self._verrou:
            libre = bool(self._donnees["toujours_autoriser"].get(nom)) and niv < 3
        if niv <= 1 or libre:
            return self._executer(outil, args)
        with self._verrou:
            self._attente = {"id": uuid.uuid4().hex[:8], "outil": outil, "args": args,
                             "niveau": niv, "description": description,
                             "expire": time.time() + DELAI_VALIDITE}
        self.kx.extensions.sons.jouer("confirmation_demande")
        if niv >= 3:
            return (f"Action sensible : {description}. Confirmation stricte requise — "
                    "réponds « oui, je confirme » pour lancer, ou « non » pour annuler.")
        return f"{description[:1].upper() + description[1:]} — je confirme ? (oui / non)"

    def _executer(self, outil, args: dict) -> str:
        try:
            resultat = outil.fonction(args or {})
            self.kx.memoire.journaliser("action", f"{outil.nom} N{outil.niveau}")
            return resultat or "C'est fait."
        except ErreurModule as e:
            self.kx.extensions.sons.jouer("erreur")
            return f"Je n'ai pas pu : {e}."
        except Exception as e:     # un module ne doit jamais faire tomber le noyau
            LOG.exception("outil %s en erreur", outil.nom)
            self.kx.extensions.sons.jouer("erreur")
            return f"Action « {outil.nom} » échouée ({type(e).__name__})."

    # -- réponses aux demandes de confirmation --------------------------
    @property
    def en_attente(self) -> bool:
        with self._verrou:
            if self._attente and time.time() > self._attente["expire"]:
                self._attente = None
            return self._attente is not None or self._minuteur_n3 is not None

    def repondre(self, texte: str) -> str | None:
        """Appelé en premier à chaque phrase : traite un oui/non en attente, sinon None."""
        with self._verrou:
            if self._minuteur_n3 is not None:
                if analyser_confirmation(texte) == "non" or re.search(r"\bannul", normaliser(texte)):
                    return self.annuler_differe()
                return None
            if self._attente and time.time() > self._attente["expire"]:
                self._attente = None
            attente = self._attente
            if attente is None:
                return None
            verdict = analyser_confirmation(texte)
            self._attente = None
        if verdict == "oui":
            self.kx.extensions.sons.jouer("confirmation_ok")
            if attente["niveau"] >= 3:
                return self._differer(attente)
            return self._executer(attente["outil"], attente["args"])
        self.kx.extensions.sons.jouer("annule")
        if verdict == "non":
            return "C'est annulé, rien n'a été fait."
        if len(normaliser(texte).split()) > 4:
            return None        # c'est une nouvelle demande : la confirmation est abandonnée, la phrase est traitée normalement
        return "Je n'ai pas compris sans ambiguïté, donc par sécurité j'annule. Redemande-moi si besoin."

    def _differer(self, attente: dict) -> str:
        delai = float(self.kx.config.get("delai_securite_n3", DELAI_N3))
        sons = self.kx.extensions.sons

        def declencher():
            with self._verrou:
                self._minuteur_n3 = None
            sons.jouer("succes")
            reponse = self._executer(attente["outil"], attente["args"])
            self.kx._diffuser("message", {"role": "assistant", "contenu": reponse, "source": "securite"})
            self.kx.voix.parler(reponse)

        minuteur = threading.Timer(delai, declencher)
        minuteur.daemon = True
        with self._verrou:
            self._minuteur_n3 = {"minuteur": minuteur, "description": attente["description"]}
        sons.jouer("securite_n3")
        minuteur.start()
        return (f"Confirmé. Je lance dans {int(delai)} secondes — dis « annule » pour arrêter.")

    def annuler_differe(self) -> str:
        with self._verrou:
            m, self._minuteur_n3 = self._minuteur_n3, None
        if m:
            m["minuteur"].cancel()
            self.kx.extensions.sons.jouer("annule")
            return "Annulé à temps. Rien n'a été exécuté."
        return "Il n'y avait rien à annuler."

    # -- commandes vocales sur les permissions -------------------------
    def intention(self, commande: str) -> str | None:
        c = normaliser(commande)
        m = re.search(r"\btoujours autoriser (?:les? |la |l'|des? )?(.+)", c)
        if m:
            cible = m.group(1).strip()
            trouves = [o for o in self.kx.extensions.outils()
                       if cible in normaliser(o.nom) or cible in normaliser(o.domaine)]
            if not trouves:
                return f"Je ne connais pas d'action « {cible} »."
            ok = [o.nom for o in trouves if self.autoriser_toujours(o.nom)]
            refuses = [o.nom for o in trouves if o.nom not in ok]
            msg = f"{len(ok)} action(s) autorisée(s) en permanence." if ok else ""
            if refuses:
                msg += " Les actions de niveau 3 (extinction, suppressions...) restent toujours confirmées."
            return msg.strip()
        m = re.search(r"\b(?:revoque|retire|supprime) (?:l'|toutes? les? )?autorisations?(?: de| des| du| pour)? ?(.*)", c)
        if m:
            cible = m.group(1).strip()
            if not cible or cible in ("toutes", "tout"):
                return f"{self.revoquer()} autorisation(s) révoquée(s)."
            noms = [o.nom for o in self.kx.extensions.outils()
                    if cible in normaliser(o.nom) or cible in normaliser(o.domaine)]
            return f"{sum(self.revoquer(n) for n in noms)} autorisation(s) révoquée(s)."
        if re.search(r"\b(mes permissions|quelles autorisations|autorisations actives)\b", c):
            a = self.autorises()
            return ("Autorisées en permanence : " + ", ".join(a) + ".") if a else \
                   "Aucune action n'est autorisée en permanence : tout passe par le niveau prévu."
        return None
