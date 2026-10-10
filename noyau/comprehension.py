"""
KRYPTONIX / noyau/comprehension.py
===================================
Comprendre un média déposé par l'utilisateur et en tirer un texte exploitable
(qui sera mémorisé comme n'importe quel document, résumé, puis interrogeable) :

  audio  → transcription de la parole (« message vocal », mémo, réunion…)
  image  → description par un modèle de vision (+ texte lu si OCR disponible)
  vidéo  → ce qui se dit (bande sonore) + ce qu'on voit (images clés décrites)

Ce que KRYPTONIX ne peut pas faire, il ne l'invente pas : il continue avec ce
qu'il peut (une vidéo sans modèle de vision reste comprise par sa bande sonore)
et signale la limite dans `avertissements`. Quand rien n'est possible, il lève
ErreurComprehension : l'interface répond alors avec douceur (noyau/repli.py).
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile

from .medias import (DUREE_MAX_SECONDES, EXT_AUDIO, EXT_IMAGE, EXT_VIDEO, LIMITES_OCTETS,
                     ErreurComprehension, Transcripteur, duree_lisible, extraire_audio,
                     extraire_images, horloge, sonder, type_de_fichier)

LOG = logging.getLogger("comprehension")


class Comprehension:
    """Transforme un fichier audio, image ou vidéo en texte compréhensible par le cerveau."""

    def __init__(self, config: dict, vision, repli=None):
        self.config = config
        self.vision = vision
        self.repli = repli
        self.transcripteur = Transcripteur(config)

    # ------------------------------------------------------------------
    def analyser(self, chemin: str, suivi=None) -> dict:
        """
        Retourne {type, contenu, avertissements, moteur, duree}.
        Lève ErreurComprehension (format, trop_gros, illisible, silence, vue_absente…).
        """
        suivi = suivi or (lambda pct, msg: None)
        genre = type_de_fichier(chemin)
        if genre is None:
            raise ErreurComprehension("format")
        if os.path.getsize(chemin) > LIMITES_OCTETS[genre]:
            raise ErreurComprehension("trop_gros")

        nom = os.path.basename(chemin)
        travail = tempfile.mkdtemp(prefix="kryptonix_media_")
        try:
            if genre == "image":
                return self._image(chemin, nom, suivi)
            sonde = sonder(chemin)
            if not sonde["valide"]:
                raise ErreurComprehension("illisible")
            # Un .webm ou un .mp4 sans image n'est qu'un enregistrement sonore.
            if genre == "video" and not sonde["video"]:
                genre = "audio"
            if genre == "audio":
                return self._audio(chemin, nom, sonde, travail, suivi)
            return self._video(chemin, nom, sonde, travail, suivi)
        finally:
            shutil.rmtree(travail, ignore_errors=True)

    # ------------------------------------------------------------------
    #  Image
    # ------------------------------------------------------------------
    def _image(self, chemin: str, nom: str, suivi) -> dict:
        suivi(10, "Je regarde l'image…")
        avertissements: list[str] = []
        ocr = self.vision.extraire_texte(chemin)
        try:
            description = self.vision.voir(chemin)
        except ErreurComprehension as erreur:
            if erreur.cle == "vue_absente" and ocr:
                description = ""
                avertissements.append("vue_absente")      # le texte lu est tout ce que je peux dire
            else:
                raise
        morceaux = [f"IMAGE « {nom} »"]
        if description:
            morceaux.append("[Ce qu'on voit]\n" + description)
        if ocr:
            morceaux.append("[Texte lu dans l'image]\n" + ocr)
        return {"type": "image", "contenu": "\n\n".join(morceaux),
                "avertissements": avertissements, "moteur": self.vision.modele_vision or "ocr",
                "duree": 0.0}

    # ------------------------------------------------------------------
    #  Audio
    # ------------------------------------------------------------------
    def _transcrire(self, chemin: str, sonde: dict, travail: str, suivi,
                    debut: int = 0, fin: int = 100) -> tuple[dict, list[str]]:
        """Bande sonore → transcription. Retourne (resultat, avertissements)."""
        if not sonde["audio"]:
            raise ErreurComprehension("illisible", "aucune piste sonore")
        avertissements: list[str] = []
        suivi(debut, "Je prépare le son…")
        wav = os.path.join(travail, "voix.wav")
        extraire_audio(chemin, wav)
        if sonde["duree"] > DUREE_MAX_SECONDES:
            avertissements.append("debut_seulement")
        duree = min(sonde["duree"], DUREE_MAX_SECONDES)

        def relayer(pct: int, message: str) -> None:
            suivi(debut + int(pct * (fin - debut) / 100), message)

        return self.transcripteur.transcrire(wav, duree, relayer), avertissements

    def _audio(self, chemin: str, nom: str, sonde: dict, travail: str, suivi) -> dict:
        transcription, avertissements = self._transcrire(chemin, sonde, travail, suivi)
        entete = (f"ENREGISTREMENT AUDIO « {nom} » — durée {duree_lisible(sonde['duree'])}, "
                  f"langue détectée : {transcription['langue']}")
        return {"type": "audio", "contenu": f"{entete}\n\n{transcription['texte']}",
                "avertissements": avertissements, "moteur": transcription["moteur"],
                "duree": sonde["duree"]}

    # ------------------------------------------------------------------
    #  Vidéo : ce qui se dit + ce qu'on voit
    # ------------------------------------------------------------------
    def _video(self, chemin: str, nom: str, sonde: dict, travail: str, suivi) -> dict:
        avertissements: list[str] = []
        parole, regard = "", ""
        moteur: list[str] = []
        erreur_son: ErreurComprehension | None = None
        voit = self.vision.vision_disponible()

        # --- 1. Ce qui se dit ---
        if sonde["audio"]:
            try:
                transcription, avert = self._transcrire(chemin, sonde, travail, suivi, 3, 60 if voit else 95)
                parole = transcription["texte"]
                avertissements += avert
                moteur.append(transcription["moteur"])
            except ErreurComprehension as erreur:
                if erreur.cle not in ("silence", "oreille_absente"):
                    raise
                erreur_son = erreur
                avertissements.append("son_inexploitable" if erreur.cle == "silence" else "oreille_absente")
        else:
            avertissements.append("video_muette")

        # --- 2. Ce qu'on voit ---
        if voit:
            nombre = int(self.config.get("video_images", 6))
            suivi(62, "Je choisis les images clés…")
            images = extraire_images(chemin, nombre, travail, sonde["duree"])
            lignes = []
            for indice, (instant, fichier) in enumerate(images):
                suivi(62 + int((indice + 1) / max(1, len(images)) * 36),
                      f"Je regarde l'image {indice + 1} sur {len(images)}…")
                try:
                    description = self.vision.voir(
                        fichier, "Décris en une ou deux phrases, en français, ce que montre cette image "
                                 "extraite d'une vidéo. Recopie tout texte lisible.")
                    lignes.append(f"[{horloge(instant)}] {description}")
                except ErreurComprehension as erreur:
                    LOG.info("Image %d de la vidéo non décrite : %s", indice + 1, erreur.cle)
            regard = "\n".join(lignes)
            if regard:
                moteur.append(self.vision.modele_vision or "vision")
            else:
                avertissements.append("images_non_decrites")
        else:
            avertissements.append("vue_absente")

        # --- 3. Rien d'exploitable : on ne fait pas semblant ---
        if not parole and not regard:
            if erreur_son is not None and erreur_son.cle == "oreille_absente":
                raise erreur_son
            # Pas de parole et pas de vue : la seule chose qui aiderait, c'est de lui donner des yeux.
            raise ErreurComprehension("vue_absente" if not voit else "silence")

        morceaux = [f"VIDÉO « {nom} » — durée {duree_lisible(sonde['duree'])}"]
        if parole:
            morceaux.append("[Ce qui se dit]\n" + parole)
        if regard:
            morceaux.append("[Ce qu'on voit, images clés]\n" + regard)
        return {"type": "video", "contenu": "\n\n".join(morceaux), "avertissements": avertissements,
                "moteur": " + ".join(moteur), "duree": sonde["duree"]}


# Messages d'accompagnement quand la compréhension n'est que partielle (affichés avec le résumé).
NOTES_PARTIELLES = {
    "vue_absente": "Je n'ai pas encore la vue : je me suis appuyé sur ce que j'ai pu entendre ou lire. "
                   "RÉGLAGES → Médias te permet de me l'installer en un clic.",
    "video_muette": "Cette vidéo n'a pas de bande sonore : je n'ai pu que regarder les images.",
    "son_inexploitable": "Je n'ai entendu aucune parole claire : je me suis appuyé sur les images.",
    "oreille_absente": "Mon oreille n'était pas prête (internet ?) : je me suis appuyé sur les images.",
    "images_non_decrites": "Je n'ai pas réussi à décrire les images : je me suis appuyé sur le son.",
    "debut_seulement": "C'est très long : je n'ai écouté que les 90 premières minutes.",
}
