"""
KRYPTONIX / noyau/media.py
===========================
Génération de sons et de vidéos, 100% locale et gratuite.

HONNÊTETÉ TECHNIQUE (à lire avant d'étendre ce module) :
Il n'existe pas, à ce jour, de modèle de génération audio/vidéo par IA qui soit
à la fois gratuit, local, léger et de bonne qualité (le niveau Suno, Sora ou
Veo demande des serveurs et des GPU professionnels, pas un PC personnel).

Ce module fait donc, honnêtement :
  - des SONS procéduraux (bips, alertes, notifications) — pas de la musique
    composée, des tonalités générées mathématiquement ;
  - des VIDÉOS de type diaporama (texte sur fond coloré, image par image,
    encodées en .mp4) — pas de vidéo animée par IA.

C'est utile (notifications sonores, mini-présentations vidéo) mais ce n'est
pas de la génération créative par IA. KRYPTONIX le dit clairement à
l'utilisateur dans ses réponses plutôt que de laisser croire le contraire.
"""

from __future__ import annotations

import logging
import math
import os
import struct
import time
import wave

LOG = logging.getLogger("media")

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    Image = None

try:
    import imageio
except ImportError:  # pragma: no cover
    imageio = None

FREQUENCE_ECHANTILLONNAGE = 44100

# Quelques motifs de sons simples, définis comme des suites de (fréquence_Hz, durée_s)
MOTIFS_SONORES = {
    "notification": [(880, 0.09), (1175, 0.12)],
    "alerte": [(660, 0.15), (0, 0.05), (660, 0.15), (0, 0.05), (660, 0.15)],
    "succes": [(523, 0.1), (659, 0.1), (784, 0.15)],
    "erreur": [(400, 0.2), (300, 0.25)],
}


class Media:
    """Générateur de sons procéduraux et de vidéos-diaporamas pour KRYPTONIX."""

    def __init__(self, config: dict, memoire, dossier: str):
        self.config = config
        self.memoire = memoire
        self.dossier = dossier
        os.makedirs(self.dossier, exist_ok=True)

    # ------------------------------------------------------------------
    #  Disponibilité
    # ------------------------------------------------------------------
    def video_disponible(self) -> bool:
        return Image is not None and imageio is not None

    # ------------------------------------------------------------------
    #  Sons procéduraux (toujours disponibles : seule la bibliothèque
    #  standard `wave` est utilisée, aucune dépendance externe)
    # ------------------------------------------------------------------
    def generer_son(self, style: str = "notification") -> dict:
        """Crée un fichier .wav court à partir d'un motif de tonalités prédéfini."""
        motif = MOTIFS_SONORES.get(style, MOTIFS_SONORES["notification"])
        nom_fichier = f"son_{style}_{int(time.time())}.wav"
        chemin = os.path.join(self.dossier, nom_fichier)

        try:
            with wave.open(chemin, "w") as fichier_audio:
                fichier_audio.setnchannels(1)
                fichier_audio.setsampwidth(2)  # 16 bits
                fichier_audio.setframerate(FREQUENCE_ECHANTILLONNAGE)
                for frequence, duree in motif:
                    fichier_audio.writeframes(self._generer_tonalite(frequence, duree))

            taille = os.path.getsize(chemin)
            doc_id = self.memoire.ajouter_fichier_genere(
                nom=nom_fichier, chemin=chemin, format_fichier="wav",
                sujet=f"son {style}", taille=taille,
            )
            return {"succes": True, "nom_fichier": nom_fichier, "chemin": chemin,
                    "format": "wav", "id": doc_id, "taille": taille, "erreur": ""}
        except Exception as erreur:
            LOG.error("Échec de génération sonore : %s", erreur)
            return {"succes": False, "nom_fichier": "", "chemin": "", "format": "wav",
                    "id": None, "taille": 0, "erreur": str(erreur)}

    @staticmethod
    def _generer_tonalite(frequence: float, duree: float) -> bytes:
        """Génère une onde sinusoïdale pure (ou un silence si fréquence=0)."""
        nb_echantillons = int(FREQUENCE_ECHANTILLONNAGE * duree)
        trame = bytearray()
        for i in range(nb_echantillons):
            if frequence <= 0:
                valeur = 0
            else:
                # Enveloppe simple (fade in/out) pour éviter les clics audio
                enveloppe = min(1.0, i / 200, (nb_echantillons - i) / 200)
                valeur = int(32767 * 0.5 * enveloppe *
                           math.sin(2 * math.pi * frequence * i / FREQUENCE_ECHANTILLONNAGE))
            trame += struct.pack("<h", valeur)
        return bytes(trame)

    # ------------------------------------------------------------------
    #  Vidéo-diaporama (texte sur fond coloré, pas d'animation par IA)
    # ------------------------------------------------------------------
    def generer_video(self, titre: str, contenu_markdown: str,
                      duree_par_diapo: float = 4.0) -> dict:
        """
        Transforme un texte structuré (# titre, ## diapositives) en une vidéo
        .mp4 : chaque diapositive est une image statique affichée quelques
        secondes. Simple, mais entièrement local et sans dépendance système.
        """
        if not self.video_disponible():
            return {"succes": False, "erreur": "Pillow et/ou imageio ne sont pas installés "
                    "(pip install Pillow imageio imageio-ffmpeg)", "nom_fichier": "",
                    "chemin": "", "format": "mp4", "id": None, "taille": 0}

        diapositives = self._decouper_en_diapositives(contenu_markdown, titre)
        largeur, hauteur = 1280, 720
        fps = 24

        nom_fichier = f"video_{self._nom_sur(titre)}_{int(time.time())}.mp4"
        chemin = os.path.join(self.dossier, nom_fichier)

        try:
            palette = [(20, 24, 32), (30, 41, 59), (24, 34, 30), (37, 26, 39)]
            with imageio.get_writer(chemin, fps=fps, codec="libx264",
                                    quality=7, macro_block_size=None) as ecrivain:
                for index, texte_diapo in enumerate(diapositives):
                    image = self._rendre_diapositive(
                        texte_diapo, largeur, hauteur, palette[index % len(palette)]
                    )
                    import numpy as _np  # dépendance transitive d'imageio, déjà présente
                    trame = _np.array(image)
                    for _ in range(int(duree_par_diapo * fps)):
                        ecrivain.append_data(trame)

            taille = os.path.getsize(chemin)
            doc_id = self.memoire.ajouter_fichier_genere(
                nom=nom_fichier, chemin=chemin, format_fichier="mp4",
                sujet=titre, taille=taille,
            )
            return {"succes": True, "nom_fichier": nom_fichier, "chemin": chemin,
                    "format": "mp4", "id": doc_id, "taille": taille, "erreur": ""}
        except Exception as erreur:
            LOG.error("Échec de génération vidéo : %s", erreur)
            return {"succes": False, "nom_fichier": "", "chemin": "", "format": "mp4",
                    "id": None, "taille": 0,
                    "erreur": f"{erreur} (le premier lancement télécharge un petit ffmpeg "
                             "portable, réessaie si ça vient d'échouer)"}

    @staticmethod
    def _decouper_en_diapositives(contenu: str, titre: str) -> list[str]:
        diapositives = [titre]
        for ligne in contenu.splitlines():
            ligne = ligne.strip()
            if ligne.startswith("## "):
                diapositives.append(ligne[3:].strip())
            elif ligne.startswith("# "):
                diapositives[0] = ligne[2:].strip()
        if len(diapositives) == 1:
            # pas de sous-titres détectés : on découpe le texte brut en phrases
            phrases = [p.strip() for p in contenu.replace("\n", " ").split(".") if p.strip()]
            diapositives.extend(phrases[:6])
        return diapositives[:8]  # une vidéo de diaporama reste courte, volontairement

    def _rendre_diapositive(self, texte: str, largeur: int, hauteur: int,
                            couleur_fond: tuple) -> "Image.Image":
        image = Image.new("RGB", (largeur, hauteur), couleur_fond)
        dessin = ImageDraw.Draw(image)

        try:
            police = ImageFont.truetype("DejaVuSans-Bold.ttf", 54)
        except Exception:
            police = ImageFont.load_default()

        lignes = self._decouper_texte(dessin, texte, police, largeur - 160)
        hauteur_ligne = 68
        hauteur_totale = len(lignes) * hauteur_ligne
        y = (hauteur - hauteur_totale) // 2

        for ligne in lignes:
            boite = dessin.textbbox((0, 0), ligne, font=police)
            largeur_texte = boite[2] - boite[0]
            x = (largeur - largeur_texte) // 2
            dessin.text((x, y), ligne, font=police, fill=(235, 240, 245))
            y += hauteur_ligne

        dessin.text((40, hauteur - 50), "KRYPTONIX", font=ImageFont.load_default(),
                   fill=(120, 130, 145))
        return image

    @staticmethod
    def _decouper_texte(dessin, texte: str, police, largeur_max: int) -> list[str]:
        mots = texte.split()
        lignes, ligne_courante = [], ""
        for mot in mots:
            essai = f"{ligne_courante} {mot}".strip()
            boite = dessin.textbbox((0, 0), essai, font=police)
            if boite[2] - boite[0] <= largeur_max:
                ligne_courante = essai
            else:
                if ligne_courante:
                    lignes.append(ligne_courante)
                ligne_courante = mot
        if ligne_courante:
            lignes.append(ligne_courante)
        return lignes or [texte]

    @staticmethod
    def _nom_sur(texte: str) -> str:
        import re
        nom = re.sub(r"[^\wÀ-ÿ -]", "", texte).strip()
        return re.sub(r"\s+", "_", nom)[:40] or "video"
