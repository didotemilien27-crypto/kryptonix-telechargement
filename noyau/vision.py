"""
KRYPTONIX / noyau/vision.py
============================
Étude d'images : description du contenu par un modèle Ollama multimodal et,
si Tesseract est installé, lecture du texte visible (OCR).

Honnêteté technique : mistral et gemma:2b (les modèles texte par défaut de
KRYPTONIX) NE VOIENT PAS les images. Pour décrire une photo il faut un modèle
multimodal, installable en un clic dans RÉGLAGES → Médias (Moondream, environ
1,7 Go, léger) ou RÉGLAGES → Assistant (Llava, Llama 3.2 Vision…).

Sans ce modèle, KRYPTONIX ne devine rien : il l'explique avec douceur et dit
comment lui donner des yeux (voir noyau/repli.py).

Le modèle est détecté à chaud : dès qu'il est téléchargé, il est utilisable,
sans redémarrer l'application.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import time

import requests

from .medias import ErreurComprehension
from .repli import Repli

LOG = logging.getLogger("vision")

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

try:
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None

EXTENSIONS_IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
MODELES_VISION = ("llava", "bakllava", "moondream", "llama3.2-vision", "minicpm-v",
                  "qwen2.5vl", "granite3.2-vision", "gemma3")
INSTRUCTION_PAR_DEFAUT = (
    "Décris précisément ce que tu vois sur cette image, en français, en 3 à 5 phrases. "
    "Si du texte est lisible, recopie-le fidèlement."
)
COTE_MAX = 1024          # une photo de téléphone est réduite : plus rapide, aussi lisible pour le modèle


def est_modele_vision(nom: str) -> bool:
    nom = nom.lower()
    if nom.startswith("gemma3"):                       # gemma3:1b et :270m sont des modèles texte seuls
        return ":1b" not in nom and ":270m" not in nom
    return any(nom.startswith(candidat) for candidat in MODELES_VISION)


class Vision:
    """Analyse d'images pour KRYPTONIX : description via modèle multimodal + OCR facultatif."""

    def __init__(self, config: dict, memoire=None, repli: Repli | None = None):
        self.config = config
        self.memoire = memoire
        self.repli = repli or Repli(config)
        self.hote = config["ollama_hote"].rstrip("/")
        self._modele: str | None = None
        self._detecte_le = 0.0

    # ------------------------------------------------------------------
    @property
    def modele_vision(self) -> str | None:
        """Premier modèle multimodal installé. Relu toutes les 15 s : un modèle fraîchement
        téléchargé est reconnu sans redémarrage."""
        if time.time() - self._detecte_le > 15 or (self._modele is None and time.time() - self._detecte_le > 3):
            self._detecte_le = time.time()
            try:
                reponse = requests.get(f"{self.hote}/api/tags", timeout=5)
                noms = [m.get("name", "") for m in reponse.json().get("models", [])]
                self._modele = next((n for n in noms if est_modele_vision(n)), None)
            except (requests.RequestException, ValueError):
                pass        # on garde la dernière réponse connue
        return self._modele

    def oublier_detection(self) -> None:
        self._detecte_le = 0.0

    def vision_disponible(self) -> bool:
        return self.modele_vision is not None

    def ocr_disponible(self) -> bool:
        return pytesseract is not None and Image is not None

    # ------------------------------------------------------------------
    #  OCR : lecture du texte visible dans l'image
    # ------------------------------------------------------------------
    def extraire_texte(self, chemin_image: str, langue: str = "fra") -> str:
        if not self.ocr_disponible():
            return ""
        try:
            image = Image.open(chemin_image)
            return pytesseract.image_to_string(image, lang=langue).strip()
        except pytesseract.TesseractNotFoundError:
            LOG.info("Le programme Tesseract OCR n'est pas installé sur le système.")
            return ""
        except Exception as erreur:
            LOG.warning("Échec OCR (%s) : %s", chemin_image, erreur)
            return ""

    # ------------------------------------------------------------------
    #  Description visuelle
    # ------------------------------------------------------------------
    @staticmethod
    def _preparer(chemin_image: str) -> str:
        """Image → JPEG réduit en base64. Lève ErreurComprehension('illisible')."""
        try:
            if Image is None:
                with open(chemin_image, "rb") as fichier:
                    return base64.b64encode(fichier.read()).decode("ascii")
            with Image.open(chemin_image) as image:
                image.seek(0)                                      # GIF : première image
                image = image.convert("RGB")
                image.thumbnail((COTE_MAX, COTE_MAX))
                tampon = io.BytesIO()
                image.save(tampon, format="JPEG", quality=85)
            return base64.b64encode(tampon.getvalue()).decode("ascii")
        except Exception as erreur:
            raise ErreurComprehension("illisible", str(erreur)) from erreur

    def voir(self, chemin_image: str, question: str = "") -> str:
        """Décrit l'image. Lève ErreurComprehension (vue_absente, illisible, hors_ligne, delai, vide…)."""
        modele = self.modele_vision
        if not modele:
            raise ErreurComprehension("vue_absente")
        image_b64 = self._preparer(chemin_image)
        try:
            reponse = requests.post(
                f"{self.hote}/api/chat",
                json={"model": modele, "stream": False,
                      "messages": [{"role": "user", "images": [image_b64],
                                    "content": question.strip() or INSTRUCTION_PAR_DEFAUT}]},
                timeout=int(self.config.get("vision_timeout", 150)),
            )
            reponse.raise_for_status()
        except requests.exceptions.ConnectionError as erreur:
            raise ErreurComprehension("hors_ligne", str(erreur)) from erreur
        except requests.exceptions.Timeout as erreur:
            raise ErreurComprehension("delai", str(erreur)) from erreur
        except requests.RequestException as erreur:
            LOG.error("Erreur d'analyse d'image : %s", erreur)
            raise ErreurComprehension("anomalie", str(erreur)) from erreur
        texte = (reponse.json().get("message", {}).get("content", "") or "").strip()
        if not texte:
            raise ErreurComprehension("vide")
        return texte

    def decrire(self, chemin_image: str, question: str = "") -> str:
        """Version « phrase prête à dire » : toujours un texte, jamais d'exception."""
        try:
            return self.voir(chemin_image, question)
        except ErreurComprehension as erreur:
            return self.repli.cas(erreur.cle)

    # ------------------------------------------------------------------
    #  Point d'entrée combiné (commande « décris cette image : … » et /api/image)
    # ------------------------------------------------------------------
    def analyser(self, chemin_image: str, question: str = "") -> dict:
        """Combine OCR et description visuelle, retourne les deux résultats."""
        resultat = {"texte_ocr": "", "description": "", "chemin": chemin_image}
        extension = os.path.splitext(chemin_image)[1].lower()
        if extension not in EXTENSIONS_IMAGE:
            resultat["erreur"] = self.repli.cas("format")
            return resultat

        resultat["texte_ocr"] = self.extraire_texte(chemin_image)
        resultat["description"] = self.decrire(chemin_image, question)

        if self.memoire:
            resume = resultat["description"][:500]
            self.memoire.journaliser("vision", f"{os.path.basename(chemin_image)} : {resume}")
        return resultat

    def rapport_parle(self, resultat: dict) -> str:
        morceaux = []
        if resultat.get("description"):
            morceaux.append(resultat["description"])
        if resultat.get("texte_ocr"):
            apercu = resultat["texte_ocr"][:300]
            morceaux.append(f"Texte lu dans l'image : « {apercu} »")
        return " ".join(morceaux) or self.repli.cas("illisible")
