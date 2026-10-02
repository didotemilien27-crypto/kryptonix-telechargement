"""
KRYPTONIX / noyau/vision.py
============================
Étude d'images : extraction du texte visible (OCR) et, si un modèle Ollama
multimodal est installé (ex: llava), description du contenu de l'image.

Honnêteté technique : mistral et gemma:2b (les modèles texte par défaut de
KRYPTONIX) NE VOIENT PAS les images. Pour une vraie description visuelle
("qu'y a-t-il sur cette photo ?"), il faut installer un modèle multimodal :

    ollama pull llava

Sans ce modèle, KRYPTONIX se rabat sur l'OCR (lecture du texte présent dans
l'image) et le dit clairement, plutôt que d'inventer une description.
"""

from __future__ import annotations

import base64
import logging
import os

import requests

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
MODELES_VISION = ("llava", "bakllava", "llava-llama3", "moondream")


class Vision:
    """Analyse d'images pour KRYPTONIX : OCR + description via modèle multimodal."""

    def __init__(self, config: dict, memoire=None):
        self.config = config
        self.memoire = memoire
        self.hote = config["ollama_hote"].rstrip("/")
        self.modele_vision = self._detecter_modele_vision()

    # ------------------------------------------------------------------
    def _detecter_modele_vision(self) -> str | None:
        """Vérifie si un modèle multimodal est installé dans Ollama."""
        try:
            reponse = requests.get(f"{self.hote}/api/tags", timeout=5)
            noms = [m.get("name", "").lower() for m in reponse.json().get("models", [])]
            for nom in noms:
                if any(nom.startswith(candidat) for candidat in MODELES_VISION):
                    return nom
        except requests.RequestException:
            pass
        return None

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
            LOG.warning("Le programme Tesseract OCR n'est pas installé sur le système.")
            return ""
        except Exception as erreur:
            LOG.warning("Échec OCR (%s) : %s", chemin_image, erreur)
            return ""

    # ------------------------------------------------------------------
    #  Description visuelle via modèle multimodal (si installé)
    # ------------------------------------------------------------------
    def decrire(self, chemin_image: str, question: str = "") -> str:
        if not self.modele_vision:
            return (
                "Je n'ai pas de modèle capable de \"voir\" les images installé. "
                "Ouvre un terminal et lance : ollama pull llava (environ 4,5 Go), "
                "puis redemande — je pourrai vraiment décrire le contenu visuel. "
                "En attendant, je peux lire le texte présent dans l'image si tu me le demandes."
            )
        try:
            with open(chemin_image, "rb") as fichier:
                image_b64 = base64.b64encode(fichier.read()).decode("utf-8")
        except OSError as erreur:
            return f"Impossible d'ouvrir l'image : {erreur}"

        instruction = question.strip() or (
            "Décris précisément ce que tu vois sur cette image, en français, "
            "en 3 à 5 phrases denses."
        )
        try:
            reponse = requests.post(
                f"{self.hote}/api/chat",
                json={
                    "model": self.modele_vision,
                    "messages": [{"role": "user", "content": instruction,
                                 "images": [image_b64]}],
                    "stream": False,
                },
                timeout=90,
            )
            reponse.raise_for_status()
            return reponse.json().get("message", {}).get("content", "").strip() or \
                "Le modèle n'a rien retourné d'exploitable."
        except requests.exceptions.ConnectionError:
            return "Ollama est hors ligne, je ne peux pas analyser l'image."
        except requests.exceptions.Timeout:
            return "L'analyse de l'image a pris trop de temps et a été interrompue."
        except requests.RequestException as erreur:
            LOG.error("Erreur d'analyse d'image : %s", erreur)
            return "L'analyse de l'image a échoué."

    # ------------------------------------------------------------------
    #  Point d'entrée combiné
    # ------------------------------------------------------------------
    def analyser(self, chemin_image: str, question: str = "") -> dict:
        """Combine OCR et description visuelle, retourne les deux résultats."""
        resultat = {"texte_ocr": "", "description": "", "chemin": chemin_image}
        extension = os.path.splitext(chemin_image)[1].lower()
        if extension not in EXTENSIONS_IMAGE:
            resultat["erreur"] = f"format d'image non pris en charge : {extension}"
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
        return " ".join(morceaux) or "Je n'ai rien pu tirer de cette image."
