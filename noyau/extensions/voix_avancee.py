"""
KRYPTONIX / noyau/extensions/voix_avancee.py
=============================================
Interaction vocale locale :

  • MOT D'ACTIVATION local avec openWakeWord (« Hey Jarvis » est fourni pré-entraîné par le projet ;
    pour « Hey Kryptonix » il faut entraîner un modèle perso : renseigne son chemin dans
    `wakeword_modele`). Sans openWakeWord, l'ancien mot d'éveil par transcription continue
    reste actif : RIEN n'est retiré.
  • TRANSCRIPTION locale Whisper (faster-whisper, déjà utilisé pour les messages vocaux) à la
    place du service Google : plus privé, fonctionne hors ligne. `stt_moteur` : auto | whisper | google.

Installation optionnelle : `pip install openwakeword faster-whisper`.
"""

from __future__ import annotations

import io
import logging
import os
import re
import tempfile
import threading
import time
import wave

from .base import ErreurModule, Module, normaliser

LOG = logging.getLogger("voix_avancee")


class VoixAvancee(Module):
    nom = "voix_avancee"
    description = "Wake word openWakeWord et transcription Whisper locale"
    exemples = ("active le mot d'activation local", "utilise whisper pour m'écouter")

    def __init__(self, kx):
        super().__init__(kx)
        self._modele_ww = None
        self.derniere_erreur = ""
        self.dernier_score = 0.0

    # -- disponibilité ----------------------------------------------------
    @staticmethod
    def openwakeword_dispo() -> bool:
        try:
            import openwakeword  # noqa: F401
            return True
        except ImportError:
            return False

    def whisper_dispo(self) -> bool:
        return self.kx.comprehension.transcripteur.moteur_local_disponible()

    def wake_actif(self) -> bool:
        return bool(self.config.get("wakeword_actif", False)) and self.openwakeword_dispo()

    def stt_whisper_actif(self) -> bool:
        mode = self.config.get("stt_moteur", "auto")
        return mode == "whisper" or (mode == "auto" and self.whisper_dispo())

    # -- transcription locale (appelée par Voix.ecouter) -------------------
    def transcrire_audio(self, audio) -> str | None:
        """`audio` : speech_recognition.AudioData. Retourne le texte, ou None pour laisser Google faire."""
        if not self.stt_whisper_actif():
            return None
        try:
            wav = audio.get_wav_data(convert_rate=16000, convert_width=2)
            chemin = tempfile.mktemp(suffix=".wav")
            with open(chemin, "wb") as f:
                f.write(wav)
            try:
                with wave.open(chemin, "rb") as w:
                    duree = w.getnframes() / float(w.getframerate() or 16000)
                from ..medias import ErreurComprehension
                try:
                    return self.kx.comprehension.transcripteur.transcrire(chemin, duree)["brut"].strip()
                except ErreurComprehension as e:
                    return "" if e.cle == "silence" else None
            finally:
                try:
                    os.remove(chemin)
                except OSError:
                    pass
        except Exception as e:
            self.derniere_erreur = str(e)[:120]
            LOG.debug("Whisper local indisponible, repli sur Google : %s", e)
            return None

    # -- mot d'activation -------------------------------------------------
    def _charger_wake(self):
        if self._modele_ww is not None:
            return self._modele_ww
        import openwakeword
        from openwakeword.model import Model
        chemin = self.config.get("wakeword_modele") or "hey_jarvis"
        try:
            if not os.path.exists(chemin):
                openwakeword.utils.download_models([chemin] if not chemin.endswith(".onnx") else [])
        except Exception as e:
            LOG.debug("téléchargement du modèle wake word : %s", e)
        self._modele_ww = Model(wakeword_models=[chemin], inference_framework="onnx")
        return self._modele_ww

    def attendre_mot(self, continuer, flux=None, modele=None) -> bool:
        """
        Bloque jusqu'à détection du mot d'activation (True) ou jusqu'à ce que `continuer()` soit faux (False).
        `flux` (facultatif) : objet avec .read(n) → octets PCM 16 bits 16 kHz mono ; `modele` : objet .predict(np) → dict.
        """
        import numpy as np
        seuil = float(self.config.get("wakeword_seuil", 0.5))
        modele = modele or self._charger_wake()
        fermer = None
        if flux is None:
            import pyaudio
            pa = pyaudio.PyAudio()
            flux_pa = pa.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=1280)

            class _Flux:
                def read(self, n):
                    return flux_pa.read(n, exception_on_overflow=False)
            flux = _Flux()

            def fermer():
                flux_pa.stop_stream()
                flux_pa.close()
                pa.terminate()
        try:
            while continuer():
                brut = flux.read(1280)
                if not brut:
                    time.sleep(0.01)
                    continue
                scores = modele.predict(np.frombuffer(brut, dtype=np.int16))
                self.dernier_score = max(scores.values()) if scores else 0.0
                if self.dernier_score >= seuil:
                    if hasattr(modele, "reset"):
                        modele.reset()
                    self.kx.extensions.sons.jouer("reveil")
                    return True
            return False
        finally:
            if fermer:
                fermer()

    # -- voix -------------------------------------------------------------
    def intention(self, texte: str, commande: str) -> str | None:
        c = normaliser(commande)
        if re.search(r"\b(active|utilise)\b.*\b(mot d'activation local|openwakeword|wake ?word)\b", c):
            if not self.openwakeword_dispo():
                return "openWakeWord n'est pas installé (pip install openwakeword). Je garde l'éveil par transcription."
            self.kx.recharger_config({"wakeword_actif": True})
            return "Mot d'activation local activé. Il sera pris en compte au prochain démarrage de l'écoute."
        if re.search(r"\b(desactive)\b.*\b(mot d'activation local|openwakeword|wake ?word)\b", c):
            self.kx.recharger_config({"wakeword_actif": False})
            return "Mot d'activation local désactivé."
        if re.search(r"\b(utilise|active|passe a) whisper\b", c):
            self.kx.recharger_config({"stt_moteur": "whisper"})
            return "J'écoute désormais avec Whisper en local." if self.whisper_dispo() else \
                "Whisper n'est pas installé (pip install faster-whisper) : je reste sur Google."
        if re.search(r"\b(utilise|repasse a|passe a) google (?:pour (?:ecouter|m'ecouter))?\b", c):
            self.kx.recharger_config({"stt_moteur": "google"})
            return "J'écoute avec le service Google."
        return None

    def statut(self) -> dict:
        return {"openwakeword": self.openwakeword_dispo(), "wake_actif": self.wake_actif(),
                "whisper": self.whisper_dispo(), "stt_moteur": self.config.get("stt_moteur", "auto"),
                "modele": self.config.get("wakeword_modele") or "hey_jarvis", "dernier_score": round(self.dernier_score, 2)}
