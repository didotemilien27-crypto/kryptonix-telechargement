"""
KRYPTONIX / noyau/voix.py
==========================
Entrée/sortie vocale.

  - Écoute  (STT) : SpeechRecognition + micro, calibration du bruit ambiant.
  - Parole  (TTS) : trois moteurs, du plus naturel au plus robuste :
        1. edge-tts  — voix neuronale Microsoft (gratuite, sans clé, en ligne)
        2. piper     — voix neuronale 100 % hors ligne (optionnel, à installer)
        3. pyttsx3   — voix du système (hors ligne, plus robotique)
        (gTTS reste disponible en dernier secours)

Réalisme : le texte est d'abord nettoyé de tout ce qui ne se dit pas (#, *,
liens, emojis, code...) puis découpé en phrases synthétisées à la volée, ce qui
donne une diction plus naturelle et une réponse qui démarre plus vite.

Toutes les dépendances sont optionnelles : si le micro ou un moteur manque,
KRYPTONIX bascule silencieusement en mode texte au lieu de planter.
"""

from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time

from .texte_parle import decouper_en_phrases, nettoyer_pour_voix

LOG = logging.getLogger("voix")

# ------------------------------------------------------------------
#  Imports optionnels
# ------------------------------------------------------------------
try:
    import speech_recognition as sr
except ImportError:  # pragma: no cover
    sr = None

try:
    import pyttsx3
except ImportError:  # pragma: no cover
    pyttsx3 = None

try:
    import edge_tts
    import asyncio
except ImportError:  # pragma: no cover
    edge_tts = None

try:
    from gtts import gTTS
except ImportError:  # pragma: no cover
    gTTS = None

try:
    import pygame
except ImportError:  # pragma: no cover
    pygame = None

# Voix neuronales françaises gratuites (Microsoft Edge Read Aloud, sans clé,
# sans compte). Les voix « Multilingual » sont les plus naturelles.
VOIX_EDGE_TTS = {
    # --- les plus réalistes ---
    "vivienne": "fr-FR-VivienneMultilingualNeural",   # femme, chaleureuse
    "remy": "fr-FR-RemyMultilingualNeural",           # homme, posé
    # --- classiques ---
    "denise": "fr-FR-DeniseNeural",
    "henri": "fr-FR-HenriNeural",
    "eloise": "fr-FR-EloiseNeural",                   # voix jeune
    # --- accents francophones ---
    "sylvie_canada": "fr-CA-SylvieNeural",
    "jean_canada": "fr-CA-JeanNeural",
    "charline_belgique": "fr-BE-CharlineNeural",
    "ariane_suisse": "fr-CH-ArianeNeural",
    # --- anciens noms (compatibilité avec les anciens config.json) ---
    "femme": "fr-FR-VivienneMultilingualNeural",
    "homme": "fr-FR-RemyMultilingualNeural",
    "homme_calme": "fr-FR-RemyMultilingualNeural",
    "femme_energique": "fr-FR-VivienneMultilingualNeural",
}


class Voix:
    """Gestionnaire vocal : une file d'attente, un thread de parole, zéro collision audio."""

    def __init__(self, config: dict):
        self.config = config
        self.active = bool(config.get("voix_active", True))
        self.langue = config.get("langue_voix", "fr")
        self.langue_reco = config.get("langue_reconnaissance", "fr-FR")

        self.moteur_choisi = config.get("moteur_tts", "auto")
        self.voix_edge = self._resoudre_voix_edge()
        self.moteur_actif = "muet"
        self._interrompre = threading.Event()
        self._file: "queue.Queue[str | None]" = queue.Queue()
        self._parle = threading.Event()
        self._morceaux_dits = 0

        self.micro_disponible = False
        self._reconnaisseur = None

        self._initialiser_tts()
        self._initialiser_micro()

        self._thread = threading.Thread(target=self._boucle_parole, daemon=True,
                                        name="kryptonix-tts")
        self._thread.start()

    # ------------------------------------------------------------------
    #  Réglages
    # ------------------------------------------------------------------
    def _resoudre_voix_edge(self) -> str:
        style = str(self.config.get("voix_style", "vivienne")).lower()
        if style in VOIX_EDGE_TTS:
            return VOIX_EDGE_TTS[style]
        if style.count("-") >= 2:               # nom de voix complet, ex. fr-FR-DeniseNeural
            return style if style.startswith(("fr-",)) else VOIX_EDGE_TTS["vivienne"]
        return VOIX_EDGE_TTS["vivienne"]

    def recharger(self, config: dict) -> None:
        """Applique à chaud un changement de réglages vocaux (voix, vitesse, moteur)."""
        self.config = config
        self.active = bool(config.get("voix_active", True))
        self.voix_edge = self._resoudre_voix_edge()
        nouveau = config.get("moteur_tts", "auto")
        if nouveau != self.moteur_choisi:
            self.moteur_choisi = nouveau
            self._initialiser_tts()

    @staticmethod
    def voix_disponibles() -> dict:
        return dict(VOIX_EDGE_TTS)

    # ------------------------------------------------------------------
    #  Initialisation
    # ------------------------------------------------------------------
    def _piper_pret(self) -> bool:
        modele = self.config.get("piper_modele", "")
        executable = self.config.get("piper_executable", "piper")
        return bool(modele and os.path.isfile(modele) and shutil.which(executable))

    def _initialiser_tts(self) -> None:
        if not self.active or self.moteur_choisi == "muet":
            self.moteur_actif = "muet"
            return

        # Priorité 1 : edge-tts — voix neuronale, la plus humaine, gratuite et sans clé,
        # mais demande une connexion internet.
        if self.moteur_choisi in ("auto", "edge", "edge-tts") and edge_tts is not None:
            try:
                if pygame:
                    pygame.mixer.init()
                self.moteur_actif = "edge"
                LOG.info("Synthèse vocale : edge-tts (%s, voix neuronale en ligne).",
                         self.voix_edge)
                return
            except Exception as erreur:
                LOG.warning("edge-tts indisponible (%s).", erreur)

        # Priorité 2 : piper — voix neuronale 100 % hors ligne (si installée).
        if self.moteur_choisi in ("auto", "piper") and pygame is not None and self._piper_pret():
            try:
                pygame.mixer.init()
                self.moteur_actif = "piper"
                LOG.info("Synthèse vocale : piper (voix neuronale hors ligne).")
                return
            except Exception as erreur:
                LOG.warning("piper indisponible (%s).", erreur)

        # Priorité 3 : pyttsx3 — voix du système, hors ligne.
        if self.moteur_choisi in ("auto", "pyttsx3") and pyttsx3 is not None:
            try:
                moteur = pyttsx3.init()
                moteur.stop()
                self.moteur_actif = "pyttsx3"
                LOG.info("Synthèse vocale : pyttsx3 (hors ligne).")
                return
            except Exception as erreur:
                LOG.warning("pyttsx3 indisponible (%s).", erreur)

        if self.moteur_choisi in ("auto", "gtts") and gTTS is not None and pygame is not None:
            try:
                pygame.mixer.init()
                self.moteur_actif = "gtts"
                LOG.info("Synthèse vocale : gTTS (connexion requise).")
                return
            except Exception as erreur:
                LOG.warning("pygame.mixer indisponible (%s).", erreur)

        self.moteur_actif = "muet"
        LOG.warning("Aucun moteur vocal disponible : KRYPTONIX restera en mode texte.")

    def _initialiser_micro(self) -> None:
        if sr is None:
            LOG.info("SpeechRecognition absent : écoute vocale désactivée.")
            return
        try:
            noms = sr.Microphone.list_microphone_names()
            if not noms:
                raise OSError("aucun périphérique d'entrée")
            self._reconnaisseur = sr.Recognizer()
            self._reconnaisseur.dynamic_energy_threshold = True
            self._reconnaisseur.pause_threshold = 0.8
            with sr.Microphone() as source:
                self._reconnaisseur.adjust_for_ambient_noise(source, duration=1.0)
            self.micro_disponible = True
            LOG.info("Micro opérationnel (%d périphérique(s)).", len(noms))
        except Exception as erreur:
            self.micro_disponible = False
            LOG.warning("Micro indisponible (%s) : mode texte uniquement.", erreur)

    # ------------------------------------------------------------------
    #  Parole
    # ------------------------------------------------------------------
    def parler(self, texte: str, bloquant: bool = False) -> None:
        """
        Met le texte dans la file de parole. Ne bloque pas l'appelant par défaut.
        Le texte est nettoyé de tout ce qui ne se dit pas (#, *, liens, emojis...).
        """
        if self.moteur_actif == "muet" or not self.active:
            return
        texte = nettoyer_pour_voix(texte or "")
        if not texte:
            return
        self._interrompre.clear()
        self._file.put(texte)
        if bloquant:
            self.attendre_fin_parole()

    def _boucle_parole(self) -> None:
        while True:
            texte = self._file.get()
            if texte is None:  # sentinelle d'arrêt
                return
            if self._interrompre.is_set():
                continue
            self._parle.set()
            try:
                self._dire(texte)
            finally:
                self._parle.clear()

    def _dire(self, texte: str) -> None:
        """Lit le texte phrase par phrase ; en cas d'échec, bascule sur le moteur suivant
        et reprend là où la lecture s'est arrêtée, pour ne jamais rester muet."""
        morceaux = decouper_en_phrases(texte)
        self._morceaux_dits = 0
        for _tentative in range(4):
            restant = morceaux[self._morceaux_dits:]
            if not restant or self._interrompre.is_set():
                return
            try:
                if self.moteur_actif == "edge":
                    self._lire_par_fichiers(restant, self._synth_edge, ".mp3")
                elif self.moteur_actif == "piper":
                    self._lire_par_fichiers(restant, self._synth_piper, ".wav")
                elif self.moteur_actif == "gtts":
                    self._lire_par_fichiers(restant, self._synth_gtts, ".mp3")
                elif self.moteur_actif == "pyttsx3":
                    self._parler_pyttsx3(restant)
                else:
                    return
                return
            except Exception as erreur:
                LOG.warning("Échec de la synthèse vocale (%s) : %s", self.moteur_actif, erreur)
                if not self._basculer_moteur():
                    return

    def _basculer_moteur(self) -> bool:
        """Passe au moteur de repli suivant : edge → piper → pyttsx3."""
        ordre = ["edge", "piper", "pyttsx3"]
        if self.moteur_actif in ordre:
            for suivant in ordre[ordre.index(self.moteur_actif) + 1:]:
                if suivant == "piper" and not (pygame is not None and self._piper_pret()):
                    continue
                if suivant == "pyttsx3" and pyttsx3 is None:
                    continue
                LOG.info("Repli du moteur vocal : %s → %s.", self.moteur_actif, suivant)
                self.moteur_actif = suivant
                return True
        return False

    # --- moteurs qui produisent un fichier audio -----------------------
    def _chemin_temp(self, extension: str) -> str:
        return os.path.join(tempfile.gettempdir(),
                            f"kryptonix_voix_{int(time.time()*1000)}_{threading.get_ident()}{extension}")

    def _synth_edge(self, texte: str, chemin: str) -> None:
        async def _generer():
            communication = edge_tts.Communicate(
                texte, voice=self.voix_edge,
                rate=self._taux_edge(), pitch=self._hauteur_edge(), volume="+0%",
            )
            await communication.save(chemin)

        # edge_tts est asynchrone ; boucle dédiée à ce thread pour ne jamais
        # interférer avec d'éventuelles boucles asyncio ailleurs.
        boucle = asyncio.new_event_loop()
        try:
            boucle.run_until_complete(_generer())
        finally:
            boucle.close()

    def _synth_piper(self, texte: str, chemin: str) -> None:
        commande = [self.config.get("piper_executable", "piper"),
                    "--model", self.config["piper_modele"], "--output_file", chemin]
        resultat = subprocess.run(commande, input=texte.encode("utf-8"),
                                  capture_output=True, timeout=60)
        if resultat.returncode != 0 or not os.path.isfile(chemin):
            raise RuntimeError(resultat.stderr.decode("utf-8", "ignore")[-200:] or "piper a échoué")

    def _synth_gtts(self, texte: str, chemin: str) -> None:
        gTTS(text=texte, lang=self.langue, slow=False).save(chemin)

    def _lire_par_fichiers(self, morceaux: list[str], synthese, extension: str) -> None:
        """Synthétise les phrases dans un thread producteur pendant que la précédente
        est jouée : la première phrase part vite, les suivantes s'enchaînent sans trou."""
        if pygame is None:
            raise RuntimeError("pygame absent : impossible de lire l'audio")
        if not pygame.mixer.get_init():
            pygame.mixer.init()

        pret: "queue.Queue[object]" = queue.Queue()

        def producteur():
            for morceau in morceaux:
                if self._interrompre.is_set():
                    break
                chemin = self._chemin_temp(extension)
                try:
                    synthese(morceau, chemin)
                    pret.put(chemin)
                except Exception as erreur:
                    pret.put(erreur)
                    return
            pret.put(None)

        threading.Thread(target=producteur, daemon=True, name="kryptonix-synthese").start()

        while True:
            element = pret.get()
            if element is None:
                return
            if isinstance(element, Exception):
                raise element
            try:
                if not self._interrompre.is_set():
                    pygame.mixer.music.load(element)
                    pygame.mixer.music.play()
                    while pygame.mixer.music.get_busy():
                        if self._interrompre.is_set():
                            pygame.mixer.music.stop()
                            break
                        time.sleep(0.05)
                    pygame.mixer.music.unload()
                    if not self._interrompre.is_set():
                        self._morceaux_dits += 1
            finally:
                try:
                    os.remove(element)
                except OSError:
                    pass

    # --- pyttsx3 --------------------------------------------------------
    def _voix_systeme_francaise(self, moteur):
        """Choisit la voix française du système la plus agréable disponible."""
        preferees = ("hortense", "julie", "denise", "paul", "claude", "amelie", "thomas")
        candidates = []
        for voix in moteur.getProperty("voices"):
            etiquette = f"{getattr(voix, 'id', '')} {getattr(voix, 'name', '')}".lower()
            if "fr" in etiquette or "french" in etiquette or "français" in etiquette:
                candidates.append((voix, etiquette))
        for nom in preferees:
            for voix, etiquette in candidates:
                if nom in etiquette:
                    return voix.id
        return candidates[0][0].id if candidates else None

    def _parler_pyttsx3(self, morceaux: list[str]) -> None:
        # Un moteur neuf par lecture : c'est la seule façon fiable d'éviter le blocage
        # bien connu de pyttsx3 quand runAndWait() est appelé depuis plusieurs threads.
        moteur = pyttsx3.init()
        moteur.setProperty("rate", int(self.config.get("vitesse_voix", 185)))
        moteur.setProperty("volume", 1.0)
        identifiant = self._voix_systeme_francaise(moteur)
        if identifiant:
            moteur.setProperty("voice", identifiant)
        for morceau in morceaux:
            if self._interrompre.is_set():
                break
            moteur.say(morceau)
            moteur.runAndWait()
            self._morceaux_dits += 1
        try:
            moteur.stop()
        except Exception:
            pass

    # --- réglages de prosodie ---------------------------------------------
    def _taux_edge(self) -> str:
        """Convertit la vitesse (mots/minute) en pourcentage attendu par edge-tts."""
        base = 180
        vitesse = int(self.config.get("vitesse_voix", 185))
        delta = max(-50, min(50, round((vitesse - base) / base * 100)))
        return f"{'+' if delta >= 0 else ''}{delta}%"

    def _hauteur_edge(self) -> str:
        """Hauteur de voix en Hz (config « hauteur_voix », de -30 à +30)."""
        hauteur = max(-30, min(30, int(self.config.get("hauteur_voix", 0))))
        return f"{'+' if hauteur >= 0 else ''}{hauteur}Hz"

    def taire(self) -> None:
        """Coupe immédiatement la parole et vide la file (commande « silence »)."""
        self._interrompre.set()
        while not self._file.empty():
            try:
                self._file.get_nowait()
            except queue.Empty:
                break
        if pygame is not None:
            try:
                if pygame.mixer.get_init():
                    pygame.mixer.music.stop()
            except Exception:
                pass

    def attendre_fin_parole(self, delai_max: float = 30.0) -> None:
        debut = time.time()
        while (self._parle.is_set() or not self._file.empty()) and time.time() - debut < delai_max:
            time.sleep(0.05)

    @property
    def en_train_de_parler(self) -> bool:
        return self._parle.is_set() or not self._file.empty()

    # ------------------------------------------------------------------
    #  Écoute
    # ------------------------------------------------------------------
    def ecouter(self, timeout: float | None = None, duree_max: float | None = None) -> str:
        """
        Capture une phrase et la transcrit. Retourne "" si rien n'est compris.
        Utilise le moteur Google Web Speech (gratuit, sans clé, quota généreux).
        """
        if not self.micro_disponible or sr is None:
            return ""
        timeout = timeout or float(self.config.get("timeout_ecoute", 6))
        duree_max = duree_max or float(self.config.get("duree_max_phrase", 12))
        try:
            with sr.Microphone() as source:
                audio = self._reconnaisseur.listen(
                    source, timeout=timeout, phrase_time_limit=duree_max
                )
            return self._reconnaisseur.recognize_google(audio, language=self.langue_reco).strip()
        except sr.WaitTimeoutError:
            return ""
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as erreur:
            LOG.warning("Service de reconnaissance injoignable : %s", erreur)
            return ""
        except Exception as erreur:
            LOG.warning("Erreur d'écoute : %s", erreur)
            return ""

    def arreter(self) -> None:
        self.taire()
        self._file.put(None)
