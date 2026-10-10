"""
KRYPTONIX / noyau/extensions/base.py
=====================================
Socle commun des modules « Jarvis » : classe Module, descripteur d'outil (exposable au
serveur MCP) et petit client HTTP tolérant aux pannes.

Principe : un module ne lève jamais d'exception vers le noyau. Une panne (réseau, clé
manquante, matériel absent) devient une phrase claire que l'assistant peut dire à voix haute.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable

import requests

LOG = logging.getLogger("extensions")


class ErreurModule(Exception):
    """Erreur « parlable » : le message est destiné à être dit à l'utilisateur."""


def sans_accents(texte: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texte or "")
                   if unicodedata.category(c) != "Mn")


def normaliser(texte: str) -> str:
    """Minuscule, sans accents, ponctuation légère retirée : sert à comparer des commandes."""
    texte = sans_accents((texte or "").lower())
    texte = texte.replace("’", "'")
    return re.sub(r"[^\w'\s:/.@-]", " ", texte).strip()


def suite_originale(commande: str, m: "re.Match", groupe: int = 1) -> str:
    """Texte d'origine (avec ses accents) correspondant à un groupe trouvé dans la version normalisée.
    Valable car normaliser() garde la même longueur (hors espaces de début/fin)."""
    brut = (commande or "").lower().replace("’", "'")
    t = sans_accents(brut)
    t = re.sub(r"[^\w'\s:/.@-]", " ", t)
    decalage = len(t) - len(t.lstrip())
    return brut[m.start(groupe) + decalage:m.end(groupe) + decalage].strip()


def texte_http(reponse) -> str:
    """Corps HTTP en texte : UTF-8 d'abord (les en-têtes text/* sans charset trompent `requests`)."""
    try:
        return reponse.content.decode("utf-8")
    except UnicodeDecodeError:
        return reponse.text


@dataclass
class Outil:
    """Une action que l'assistant (ou un client MCP) peut déclencher."""
    nom: str
    description: str
    fonction: Callable[[dict], str]
    proprietes: dict = field(default_factory=dict)   # schéma JSON des arguments
    requis: tuple = ()
    niveau: int = 1                # 1 = libre, 2 = confirmation vocale, 3 = confirmation stricte + délai
    mcp: bool = True               # exposé aux clients MCP ?
    domaine: str = ""


class Module:
    """Classe de base d'un module."""
    nom = "module"
    description = ""
    exemples: tuple = ()

    def __init__(self, kx):
        self.kx = kx
        self.config = kx.config

    # -- API commune ---------------------------------------------------
    def intention(self, texte: str, commande: str) -> str | None:
        """Retourne une réponse si la phrase concerne ce module, sinon None."""
        return None

    def statut(self) -> dict:
        return {"actif": True}

    def outils(self) -> list[Outil]:
        return []

    def demarrer(self) -> None:
        pass

    def arreter(self) -> None:
        pass

    # -- Aides ----------------------------------------------------------
    def cfg(self, cle: str, defaut=None):
        valeur = self.config.get(cle, defaut)
        return defaut if valeur in (None, "") else valeur

    def securise(self, outil: str, args: dict | None = None, niveau: int | None = None,
                 description: str = "") -> str:
        """Passe par le coffre de sécurité (N1/N2/N3) pour exécuter un outil de ce module."""
        return self.kx.extensions.securite.lancer(outil, args or {}, niveau, description)


def http(methode: str, url: str, *, timeout: float = 10, **kw):
    """Requête HTTP : retourne la réponse, ou lève ErreurModule avec un message parlable."""
    try:
        reponse = requests.request(methode, url, timeout=timeout, **kw)
        return reponse
    except requests.exceptions.ConnectionError:
        raise ErreurModule("je n'arrive pas à joindre le service (connexion impossible)") from None
    except requests.exceptions.Timeout:
        raise ErreurModule("le service met trop de temps à répondre") from None
    except requests.RequestException as erreur:
        raise ErreurModule(f"erreur réseau ({type(erreur).__name__})") from None


def extraire(commande: str, motif: str, flags=re.IGNORECASE):
    return re.search(motif, commande, flags)


def liste_parlee(elements: list[str], max_elements: int = 6) -> str:
    """« a, b et c » — pour que la liste se dise naturellement."""
    elements = [e for e in elements if e]
    reste = len(elements) - max_elements
    if reste > 0:
        elements = elements[:max_elements] + [f"{reste} autre{'s' if reste > 1 else ''}"]
    if len(elements) <= 1:
        return "".join(elements)
    return ", ".join(elements[:-1]) + " et " + elements[-1]
