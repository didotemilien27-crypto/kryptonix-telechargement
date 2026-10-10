"""
KRYPTONIX / noyau/extensions/personnalites.py
==============================================
Personnalités configurables : ton + voix. « Passe en mode sarcastique », « sois concis »...
Chaque personnalité modifie la consigne donnée au cerveau ET, si tu le souhaites, le réglage de
la voix (voix, vitesse, hauteur). Désactive le changement de voix avec `personnalite_voix: false`.
"""

from __future__ import annotations

import re

from .base import Module, Outil, normaliser

PERSONNALITES = {
    "majordome": {
        "libelle": "Majordome",
        "alias": ("majordome", "classique", "jarvis", "par defaut"),
        "consigne": "",   # le style d'origine de l'assistant : élégant, calme, humour sec
        "voix": {"voix_style": "remy", "vitesse_voix": 180, "hauteur_voix": -4},
    },
    "sarcastique": {
        "libelle": "Majordome sarcastique",
        "alias": ("sarcastique", "ironique", "sarcasme", "piquant"),
        "consigne": ("Tu es un majordome britannique sarcastique : politesse impeccable, ironie sèche, "
                     "une remarque piquante par réponse (jamais méchante, jamais sur la santé, la famille "
                     "ou les sujets sensibles), puis tu aides vraiment. Tu dis « Monsieur » avec une "
                     "pointe de lassitude élégante."),
        "voix": {"voix_style": "remy", "vitesse_voix": 172, "hauteur_voix": -8},
    },
    "neutre": {
        "libelle": "Neutre",
        "alias": ("neutre", "factuel", "sobre"),
        "consigne": ("Ton strictement neutre et factuel : aucune plaisanterie, aucune familiarité, "
                     "des phrases simples et précises."),
        "voix": {"voix_style": "denise", "vitesse_voix": 185, "hauteur_voix": 0},
    },
    "concis": {
        "libelle": "Concis",
        "alias": ("concis", "bref", "court", "telegraphique", "minimaliste"),
        "consigne": ("Réponds en UNE phrase courte, deux au maximum. Va droit au résultat, sans "
                     "introduction ni politesse superflue."),
        "voix": {"voix_style": "henri", "vitesse_voix": 205, "hauteur_voix": 0},
    },
    "chaleureux": {
        "libelle": "Chaleureux",
        "alias": ("chaleureux", "amical", "gentil", "sympa", "complice"),
        "consigne": ("Ton chaleureux et bienveillant, comme un ami proche : tu encourages, tu "
                     "tutoies, tu restes simple et sincère."),
        "voix": {"voix_style": "vivienne", "vitesse_voix": 180, "hauteur_voix": 4},
    },
    "coach": {
        "libelle": "Coach",
        "alias": ("coach", "motivant", "energique", "motivation"),
        "consigne": ("Ton énergique de coach : tu motives, tu fixes un prochain pas concret, "
                     "tu félicites les progrès, sans jamais culpabiliser."),
        "voix": {"voix_style": "vivienne", "vitesse_voix": 195, "hauteur_voix": 6},
    },
    "professeur": {
        "libelle": "Professeur",
        "alias": ("professeur", "prof", "pedagogue", "explicatif"),
        "consigne": ("Ton de professeur pédagogue : tu expliques pas à pas avec une analogie simple, "
                     "tu vérifies que c'est clair en fin de réponse."),
        "voix": {"voix_style": "denise", "vitesse_voix": 175, "hauteur_voix": 0},
    },
}


class Personnalites(Module):
    nom = "personnalites"
    description = "Personnalités et voix associées"
    exemples = ("passe en mode sarcastique", "sois concis", "quelles personnalités")

    @property
    def active(self) -> str:
        p = self.config.get("personnalite", "majordome")
        return p if p in PERSONNALITES else "majordome"

    def consigne(self) -> str:
        """Bloc ajouté au prompt système du cerveau (vide pour la personnalité d'origine)."""
        c = PERSONNALITES[self.active]["consigne"]
        return f"PERSONNALITÉ ACTIVE ({PERSONNALITES[self.active]['libelle']}) : {c}" if c else ""

    def resoudre(self, mot: str) -> str | None:
        mot = normaliser(mot)
        for cle, p in PERSONNALITES.items():
            if mot == cle or any(mot == normaliser(a) for a in p["alias"]):
                return cle
        return None

    def appliquer(self, cle: str) -> str:
        if cle not in PERSONNALITES:
            raise ValueError("personnalité inconnue")
        modif = {"personnalite": cle}
        if self.config.get("personnalite_voix", True):
            modif.update(PERSONNALITES[cle]["voix"])
        self.config.update(modif)
        # on persiste et on recharge la voix si elle a changé
        try:
            self.kx.recharger_config(modif)
        except Exception:
            pass
        return PERSONNALITES[cle]["libelle"]

    def intention(self, texte: str, commande: str) -> str | None:
        c = normaliser(commande)
        if re.search(r"\b(quelles?|liste|quelles sont) (?:les |mes )?personnalites?\b", c):
            return "Je peux être : " + ", ".join(p["libelle"].lower() for p in PERSONNALITES.values()) + "."
        m = re.search(r"\b(?:passe en|mets?[- ]toi en|active|prends? la|sois|mode|deviens|parle en)\s+(?:mode |personnalite |plus |le |un |une )?"
                      r"(majordome sarcastique|[a-z]+)\b", c)
        if m:
            mots = [m.group(1)] + c.split()
            cle = self.resoudre(m.group(1))
            if not cle:
                for mot in c.split():
                    cle = self.resoudre(mot)
                    if cle:
                        break
            if cle:
                libelle = self.appliquer(cle)
                self.kx.extensions.sons.jouer("succes")
                return f"Personnalité « {libelle} » activée."
        if re.search(r"\bquelle personnalite\b", c):
            return f"Je suis en mode {PERSONNALITES[self.active]['libelle'].lower()}."
        return None

    def statut(self) -> dict:
        return {"active": self.active, "disponibles": list(PERSONNALITES)}

    def outils(self) -> list[Outil]:
        return [Outil("personnalite_definir", "Changer de personnalité",
                      lambda a: f"Personnalité « {self.appliquer(a.get('nom', 'majordome'))} » activée.",
                      {"nom": {"type": "string", "enum": list(PERSONNALITES)}}, ("nom",), 1, False, "personnalite")]
