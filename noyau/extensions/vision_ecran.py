"""
KRYPTONIX / noyau/extensions/vision_ecran.py
=============================================
Vision de l'écran : « c'est quoi cette erreur ? », « lis ça », « traduis ce que j'ai à l'écran »,
« décris mon écran ».

Je prends une capture sur demande UNIQUEMENT (jamais en continu), je la garde dans
`donnees/captures/`, puis :
  • lecture du texte (OCR Tesseract) → explication / traduction par le cerveau LOCAL ;
  • description visuelle si un modèle multimodal est installé (Moondream, Llava...).
Aucune image ne quitte ta machine.

Capture : `mss` (rapide, multi-écrans) ou Pillow (ImageGrab) en secours.
"""

from __future__ import annotations

import logging
import os
import re
import time

from ..config import DOSSIER_DONNEES
from .base import ErreurModule, Module, Outil, normaliser

LOG = logging.getLogger("vision_ecran")


def capturer(chemin: str, ecran: int = 0) -> str:
    """Enregistre une capture PNG. Lève ErreurModule si aucun moteur de capture ne fonctionne."""
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    try:
        import mss
        import mss.tools
        with mss.mss() as s:
            moniteurs = s.monitors
            m = moniteurs[min(max(ecran, 0) + 1, len(moniteurs) - 1)] if len(moniteurs) > 1 else moniteurs[0]
            img = s.grab(m)
            mss.tools.to_png(img.rgb, img.size, output=chemin)
        return chemin
    except ImportError:
        pass
    except Exception as e:
        LOG.debug("mss a échoué : %s", e)
    try:
        from PIL import ImageGrab
        ImageGrab.grab(all_screens=True).save(chemin)
        return chemin
    except Exception as e:
        raise ErreurModule("je n'arrive pas à capturer l'écran (installe `mss`, ou vérifie l'accès à l'écran)") from None


class VisionEcran(Module):
    nom = "vision_ecran"
    description = "Capture et analyse de l'écran à la demande"
    exemples = ("c'est quoi cette erreur", "lis ce qu'il y a à l'écran", "traduis ce texte à l'écran", "décris mon écran")

    def __init__(self, kx):
        super().__init__(kx)
        self.dossier = os.path.join(DOSSIER_DONNEES, "captures")
        os.makedirs(self.dossier, exist_ok=True)
        self.derniere = ""
        self._capture = capturer          # remplaçable pour les tests

    def prendre(self) -> str:
        chemin = os.path.join(self.dossier, f"capture_{time.strftime('%Y%m%d_%H%M%S')}.png")
        self._capture(chemin, int(self.config.get("ecran_index", 0)))
        self.derniere = chemin
        self.kx.extensions.sons.jouer("capture_ecran")
        self._nettoyer_anciennes()
        return chemin

    def _nettoyer_anciennes(self, garder: int = 20) -> None:
        try:
            fichiers = sorted((os.path.join(self.dossier, f) for f in os.listdir(self.dossier) if f.endswith(".png")),
                              key=os.path.getmtime)
            for f in fichiers[:-garder]:
                os.remove(f)
        except OSError:
            pass

    def lire(self, mode: str, consigne: str = "") -> str:
        chemin = self.prendre()
        vision = self.kx.vision
        texte = vision.extraire_texte(chemin).strip() if vision.ocr_disponible() else ""
        cerveau = self.kx.cerveau
        if mode == "decrire":
            return vision.rapport_parle(vision.analyser(chemin, consigne or "Décris ce qui est affiché à l'écran."))
        if not texte:
            if vision.vision_disponible():
                return vision.decrire(chemin, consigne or "Lis le texte visible à l'écran puis explique-le.")
            raise ErreurModule("je ne peux pas lire l'écran : installe Tesseract (OCR) ou un modèle de vision "
                               "(Moondream) dans Réglages → Médias")
        if mode == "lire":
            return "Je lis à l'écran : " + re.sub(r"\s+", " ", texte)[:700]
        if not (cerveau.disponible or cerveau.verifier_connexion()):
            return "J'ai lu le texte mais mon cerveau local est hors ligne : " + texte[:300]
        if mode == "erreur":
            consigne_llm = ("Voici le texte lu sur l'écran de l'utilisateur, il contient probablement un message d'erreur. "
                            "Explique en français, simplement : ce que signifie l'erreur, sa cause la plus probable, et la "
                            "solution à essayer en premier. Sois bref.")
        else:
            consigne_llm = "Traduis en français le texte suivant, lu à l'écran, fidèlement et sans commentaire."
        rep = cerveau.reflechir(f"{consigne_llm}\n\nTEXTE DE L'ÉCRAN :\n{texte[:3500]}", registre="technique", prive=True)
        return rep if not cerveau.derniere_erreur else "Texte lu : " + texte[:300]

    def intention(self, texte: str, commande: str) -> str | None:
        c = normaliser(commande)
        try:
            if re.search(r"\b(c'est quoi|qu'est-ce que c'est|explique|que veut dire|que signifie|aide[- ]moi avec)\b.*\b(erreur|bug|probleme|message|plantage)\b", c) \
                    and re.search(r"\b(cette|cet|ce|l'|mon|a l'ecran|ici)\b", c):
                return self.lire("erreur")
            if re.search(r"\btraduis\b.*\b(ecran|ca|ce texte|cette page|ce que je vois)\b|\btraduis[- ]moi (?:ca|ce)\b", c):
                return self.lire("traduire")
            if re.search(r"\b(lis|lit|lire)\b.*\b(ecran|ca|ce texte|ce que je vois)\b|\blis[- ]moi (?:ca|ce)\b|\bqu'est-ce qui est ecrit\b", c):
                return self.lire("lire")
            if re.search(r"\b(decris|regarde|analyse|que vois-tu sur|qu'est-ce que tu vois sur)\b.*\b(ecran)\b|\bcapture (?:d')?(?:mon )?ecran\b|\bprends? une capture\b", c):
                if re.search(r"\b(capture|prends)\b", c) and not re.search(r"\b(decris|analyse|regarde)\b", c):
                    self.prendre()
                    return f"Capture enregistrée : {os.path.basename(self.derniere)}."
                return self.lire("decrire")
        except ErreurModule as e:
            self.kx.extensions.sons.jouer("erreur")
            return f"Écran : {e}."
        return None

    def statut(self) -> dict:
        return {"ocr": self.kx.vision.ocr_disponible(), "vision": self.kx.vision.vision_disponible(),
                "derniere": os.path.basename(self.derniere)}

    def outils(self) -> list[Outil]:
        return [Outil("ecran_lire", "Lire le texte affiché à l'écran", lambda a: self.lire("lire"), {}, (), 2, False, "ecran"),
                Outil("ecran_erreur", "Expliquer l'erreur affichée à l'écran", lambda a: self.lire("erreur"), {}, (), 2, False, "ecran")]
