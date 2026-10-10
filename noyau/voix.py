"""
KRYPTONIX / noyau/voix.py
==========================
Entrée/sortie vocale.

  - Écoute  (STT) : SpeechRecognition + micro, calibration du bruit ambiant.
  - Parole  (TTS) : deux moteurs, du plus naturel au plus robuste :
        1. edge-tts — voix neuronales Microsoft (gratuites, sans clé, en ligne).
                      L'annuaire des voix est lu en direct : toutes les voix
                      françaises et multilingues proposées par Microsoft.
        2. pyttsx3  — voix installées sur Windows (hors ligne, plus robotiques).
                      Le choix de la voix est lui aussi réglable.

Tout réglage vocal est appliqué à chaud, phrase par phrase : voix, vitesse,
hauteur, volume. Si la voix neuronale est injoignable (pas d'internet), la voix
du système prend le relais, puis la voix neuronale est retentée automatiquement
dès que la connexion revient.

Le texte est d'abord nettoyé de tout ce qui ne se dit pas (#, *, liens, emojis,
code...) puis découpé en phrases synthétisées à la volée : la réponse démarre
vite et la diction reste naturelle.

Toutes les dépendances sont optionnelles : si le micro ou un moteur manque,
KRYPTONIX bascule silencieusement en mode texte au lieu de planter.
"""

from __future__ import annotations

import logging
import os
import queue
import re
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
    import pygame
except ImportError:  # pragma: no cover
    pygame = None

# ------------------------------------------------------------------
#  Voix neuronales
# ------------------------------------------------------------------
# Anciens noms courts (compatibilité avec les anciens config.json).
ALIAS_VOIX = {
    "vivienne": "fr-FR-VivienneMultilingualNeural",
    "remy": "fr-FR-RemyMultilingualNeural",
    "denise": "fr-FR-DeniseNeural",
    "henri": "fr-FR-HenriNeural",
    "eloise": "fr-FR-EloiseNeural",
    "sylvie_canada": "fr-CA-SylvieNeural",
    "jean_canada": "fr-CA-JeanNeural",
    "charline_belgique": "fr-BE-CharlineNeural",
    "ariane_suisse": "fr-CH-ArianeNeural",
    "femme": "fr-FR-VivienneMultilingualNeural",
    "homme": "fr-FR-RemyMultilingualNeural",
    "homme_calme": "fr-FR-RemyMultilingualNeural",
    "femme_energique": "fr-FR-VivienneMultilingualNeural",
}
VOIX_PAR_DEFAUT = "fr-FR-VivienneMultilingualNeural"
_FORMAT_VOIX = re.compile(r"^[a-z]{2,3}-[A-Za-z]{2,}(-[A-Za-z]+)?-[A-Za-z0-9]+Neural$")

REGIONS = {
    "fr-FR": "France", "fr-CA": "Canada", "fr-BE": "Belgique", "fr-CH": "Suisse",
    "en-US": "États-Unis", "en-GB": "Royaume-Uni", "en-AU": "Australie",
    "de-DE": "Allemagne", "it-IT": "Italie", "es-ES": "Espagne", "pt-BR": "Brésil",
}

# Annuaire de secours : utilisé quand Microsoft est injoignable (hors ligne).
# Quand la connexion fonctionne, l'annuaire réel le remplace automatiquement.
ANNUAIRE_SECOURS = [
    ("fr-FR-VivienneMultilingualNeural", "Femme"), ("fr-FR-RemyMultilingualNeural", "Homme"),
    ("fr-FR-DeniseNeural", "Femme"), ("fr-FR-HenriNeural", "Homme"), ("fr-FR-EloiseNeural", "Femme"),
    ("fr-CA-SylvieNeural", "Femme"), ("fr-CA-JeanNeural", "Homme"),
    ("fr-CA-AntoineNeural", "Homme"), ("fr-CA-ThierryNeural", "Homme"),
    ("fr-BE-CharlineNeural", "Femme"), ("fr-BE-GerardNeural", "Homme"),
    ("fr-CH-ArianeNeural", "Femme"), ("fr-CH-FabriceNeural", "Homme"),
]

PHRASE_ESSAI = ("Bonjour, voici ma voix. Je peux parler plus vite, plus lentement, "
                "plus grave ou plus aigu, selon tes réglages.")


def _decrire_voix(identifiant: str, genre: str) -> dict:
    """Transforme 'fr-FR-VivienneMultilingualNeural' en fiche lisible pour l'interface."""
    morceaux = identifiant.split("-")
    lieu = "-".join(morceaux[:2])
    nom = morceaux[-1].replace("MultilingualNeural", "").replace("Neural", "")
    multilingue = "Multilingual" in identifiant
    genre_fr = {"female": "Femme", "male": "Homme"}.get(str(genre).lower(), str(genre))
    region = REGIONS.get(lieu, lieu)
    libelle = f"{nom} — {genre_fr}, {region}"
    if multilingue:
        libelle += " · très naturelle"
    return {"id": identifiant, "nom": nom, "genre": genre_fr, "region": region,
            "lieu": lieu, "multilingue": multilingue, "libelle": libelle}


def _com_init() -> None:
    """Windows : un thread qui utilise les voix système doit initialiser COM."""
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass


class Voix:
    """Gestionnaire vocal : une file d'attente, un thread de parole, zéro collision audio."""

    def __init__(self, config: dict):
        self.config = config
        self.active = bool(config.get("voix_active", True))
        self.langue_reco = config.get("langue_reconnaissance", "fr-FR")

        self.moteur_choisi = config.get("moteur_tts", "auto")
        self.voix_edge = self._resoudre_voix_edge()
        self.moteur_actif = "muet"          # moteur préféré (edge | pyttsx3 | muet)
        self.dernier_moteur = ""            # moteur qui a réellement parlé en dernier
        self.derniere_erreur = ""
        self._edge_en_panne_jusqua = 0.0    # repli temporaire sur la voix système
        self._annuaire: list[dict] = []
        self._annuaire_le = 0.0
        self._annuaire_ok = False
        self._systeme: list[dict] | None = None

        self._interrompre = threading.Event()
        self._file: "queue.Queue[str | None]" = queue.Queue()
        self._parle = threading.Event()
        self._morceaux_dits = 0

        self.micro_disponible = False
        self._reconnaisseur = None
        self.stt_local = None            # branché par le noyau : transcription Whisper locale (facultative)

        self._initialiser_tts()
        self._initialiser_micro()

        self._thread = threading.Thread(target=self._boucle_parole, daemon=True,
                                        name="kryptonix-tts")
        self._thread.start()

    # ------------------------------------------------------------------
    #  Réglages appliqués à chaud
    # ------------------------------------------------------------------
    def _resoudre_voix_edge(self) -> str:
        style = str(self.config.get("voix_style", "vivienne")).strip()
        identifiant = ALIAS_VOIX.get(style.lower())
        if not identifiant and _FORMAT_VOIX.match(style):
            identifiant = style
        identifiant = identifiant or VOIX_PAR_DEFAUT
        self.config["voix_style"] = identifiant     # l'interface voit toujours le nom complet
        return identifiant

    def recharger(self, config: dict) -> None:
        """Applique à chaud un changement de réglages vocaux."""
        ancien_actif, ancien_moteur = self.active, self.moteur_choisi
        self.config = config
        self.active = bool(config.get("voix_active", True))
        self.moteur_choisi = config.get("moteur_tts", "auto")
        self.voix_edge = self._resoudre_voix_edge()
        if self.active != ancien_actif or self.moteur_choisi != ancien_moteur:
            self._initialiser_tts()
        self._edge_en_panne_jusqua = 0.0    # on retente la voix neuronale tout de suite

    # ------------------------------------------------------------------
    #  Catalogue des voix (pour l'interface)
    # ------------------------------------------------------------------
    def _lire_annuaire_edge(self) -> list[dict]:
        """Annuaire réel de Microsoft (voix françaises + multilingues). Cache 6 h."""
        maintenant = time.time()
        if self._annuaire and maintenant - self._annuaire_le < 6 * 3600:
            return self._annuaire
        if maintenant - self._annuaire_le < 60:           # échec récent : on n'insiste pas
            return self._annuaire
        self._annuaire_le = maintenant
        if edge_tts is None:
            return self._annuaire
        boucle = asyncio.new_event_loop()
        try:
            brut = boucle.run_until_complete(asyncio.wait_for(edge_tts.list_voices(), timeout=6))
        except Exception as erreur:
            LOG.info("Annuaire des voix indisponible (%s) : liste de secours.", erreur)
            self._annuaire_ok = False
            return self._annuaire
        finally:
            boucle.close()
        voix = []
        for entree in brut:
            identifiant = entree.get("ShortName", "")
            lieu = entree.get("Locale", "")
            if identifiant and (lieu.startswith("fr-") or "Multilingual" in identifiant):
                voix.append(_decrire_voix(identifiant, entree.get("Gender", "")))
        if voix:
            voix.sort(key=lambda v: (not v["lieu"].startswith("fr-"), not v["multilingue"],
                                     v["lieu"], v["nom"]))
            self._annuaire, self._annuaire_ok = voix, True
        return self._annuaire

    def voix_neuronales(self) -> list[dict]:
        if edge_tts is None:
            return []
        reel = self._lire_annuaire_edge()
        if reel:
            return reel
        return [_decrire_voix(identifiant, genre) for identifiant, genre in ANNUAIRE_SECOURS]

    def voix_systeme(self) -> list[dict]:
        """Voix françaises installées sur la machine (utilisées hors ligne)."""
        if pyttsx3 is None:
            return []
        if self._systeme is not None:
            return self._systeme
        liste: list[dict] = []
        try:
            _com_init()
            moteur = pyttsx3.init()
            toutes = list(moteur.getProperty("voices"))
            francaises = [v for v in toutes if self._est_francaise(v)]
            for voix in (francaises or toutes):
                nom = str(getattr(voix, "name", "") or getattr(voix, "id", ""))
                liste.append({"id": nom, "nom": nom, "libelle": nom})
            try:
                moteur.stop()
            except Exception:
                pass
        except Exception as erreur:
            LOG.info("Voix système illisibles : %s", erreur)
        self._systeme = liste
        return liste

    @staticmethod
    def _est_francaise(voix) -> bool:
        etiquette = f"{getattr(voix, 'id', '')} {getattr(voix, 'name', '')}".lower()
        return any(m in etiquette for m in ("fr-", "fr_", "french", "français", "francais"))

    def catalogue(self) -> dict:
        """Tout ce dont l'onglet RÉGLAGES a besoin pour proposer les voix."""
        neuronales = self.voix_neuronales()
        return {
            "neuronales": neuronales,
            "systeme": self.voix_systeme(),
            "annuaire_en_ligne": self._annuaire_ok,
            "edge_disponible": edge_tts is not None and pygame is not None,
            "systeme_disponible": pyttsx3 is not None,
            "moteur_actif": self.moteur_actif,
            "dernier_moteur": self.dernier_moteur,
        }

    # ------------------------------------------------------------------
    #  Initialisation
    # ------------------------------------------------------------------
    def _initialiser_tts(self) -> None:
        self.moteur_actif = "muet"
        if not self.active or self.moteur_choisi == "muet":
            return

        # 1. Voix neuronale : la plus humaine, mais demande internet.
        if self.moteur_choisi in ("auto", "edge") and edge_tts is not None and pygame is not None:
            try:
                if not pygame.mixer.get_init():
                    pygame.mixer.init()
                self.moteur_actif = "edge"
                LOG.info("Synthèse vocale : voix neuronale Edge (%s).", self.voix_edge)
                return
            except Exception as erreur:
                LOG.warning("Sortie audio indisponible pour la voix neuronale (%s).", erreur)

        # 2. Voix du système : hors ligne, plus robotique.
        if self.moteur_choisi in ("auto", "edge", "pyttsx3") and pyttsx3 is not None:
            try:
                _com_init()
                moteur = pyttsx3.init()
                moteur.stop()
                self.moteur_actif = "pyttsx3"
                LOG.info("Synthèse vocale : voix du système (hors ligne).")
                return
            except Exception as erreur:
                LOG.warning("Voix du système indisponible (%s).", erreur)

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

    def apercu(self, texte: str | None = None) -> dict:
        """Fait entendre la voix avec les réglages actuels et dit quel moteur a parlé."""
        if not self.active:
            return {"succes": False, "erreur": "La voix est désactivée (active-la dans les réglages)."}
        if self.moteur_actif == "muet":
            return {"succes": False, "erreur": "Aucun moteur vocal disponible sur cette machine."}
        self.taire()
        debut = time.time()
        while self._parle.is_set() and time.time() - debut < 2.5:   # laisse finir la coupure
            time.sleep(0.05)
        self.dernier_moteur, self.derniere_erreur = "", ""
        self.parler(texte or PHRASE_ESSAI)
        self.attendre_fin_parole(30)
        if not self.dernier_moteur:
            return {"succes": False,
                    "erreur": self.derniere_erreur or "Aucun son n'a pu être produit."}
        return {"succes": True, "moteur": self.dernier_moteur,
                "voulu": self.moteur_actif, "voix": self.voix_edge,
                "repli": self.dernier_moteur != self.moteur_actif,
                "erreur": self.derniere_erreur}

    def _boucle_parole(self) -> None:
        _com_init()
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

    def _moteur_du_moment(self) -> str:
        """Moteur à utiliser maintenant : la voix neuronale, sauf panne récente."""
        if self.moteur_actif == "edge":
            if time.time() >= self._edge_en_panne_jusqua:
                return "edge"
            return "pyttsx3" if pyttsx3 is not None else "edge"
        return self.moteur_actif

    def _dire(self, texte: str) -> None:
        """Lit le texte phrase par phrase ; en cas d'échec de la voix neuronale, la voix du
        système reprend exactement là où la lecture s'est arrêtée, pour ne jamais rester muet."""
        morceaux = decouper_en_phrases(texte)
        self._morceaux_dits = 0
        for _tentative in range(3):
            restant = morceaux[self._morceaux_dits:]
            if not restant or self._interrompre.is_set():
                return
            moteur = self._moteur_du_moment()
            try:
                if moteur == "edge":
                    self._lire_edge(restant)
                elif moteur == "pyttsx3":
                    self._parler_pyttsx3(restant)
                else:
                    return
                if not self._interrompre.is_set() or self._morceaux_dits:
                    self.dernier_moteur = moteur
                return
            except Exception as erreur:
                self.derniere_erreur = str(erreur)[:200]
                LOG.warning("Échec de la synthèse vocale (%s) : %s", moteur, erreur)
                if moteur != "edge":
                    return
                self._edge_en_panne_jusqua = time.time() + 45   # on retentera dans 45 s

    # --- voix neuronale -------------------------------------------------
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
            boucle.run_until_complete(asyncio.wait_for(_generer(), timeout=25))
        finally:
            boucle.close()

    def _volume(self) -> float:
        return max(0, min(100, int(self.config.get("volume_voix", 100)))) / 100.0

    def _lire_edge(self, morceaux: list[str]) -> None:
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
                chemin = self._chemin_temp(".mp3")
                try:
                    self._synth_edge(morceau, chemin)
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
                    pygame.mixer.music.set_volume(self._volume())
                    pygame.mixer.music.play()
                    while pygame.mixer.music.get_busy():
                        if self._interrompre.is_set():
                            pygame.mixer.music.stop()
                            break
                        pygame.mixer.music.set_volume(self._volume())   # volume réglable en direct
                        time.sleep(0.05)
                    pygame.mixer.music.unload()
                    if not self._interrompre.is_set():
                        self._morceaux_dits += 1
            finally:
                try:
                    os.remove(element)
                except OSError:
                    pass

    # --- voix du système -------------------------------------------------
    def _identifiant_voix_systeme(self, moteur):
        """Voix choisie dans les réglages, sinon la plus agréable des voix françaises."""
        voulue = str(self.config.get("voix_systeme", "")).strip().lower()
        voix = list(moteur.getProperty("voices"))
        if voulue:
            for v in voix:
                if voulue in (str(getattr(v, "name", "")).lower(), str(getattr(v, "id", "")).lower()):
                    return v.id
        candidates = [(v, f"{getattr(v, 'id', '')} {getattr(v, 'name', '')}".lower())
                      for v in voix if self._est_francaise(v)]
        for nom in ("hortense", "julie", "denise", "paul", "claude", "amelie", "thomas"):
            for v, etiquette in candidates:
                if nom in etiquette:
                    return v.id
        return candidates[0][0].id if candidates else None

    def _parler_pyttsx3(self, morceaux: list[str]) -> None:
        # Un moteur neuf par lecture : seule façon fiable d'éviter le blocage bien
        # connu de pyttsx3 quand runAndWait() est appelé depuis plusieurs threads.
        _com_init()
        moteur = pyttsx3.init()
        moteur.setProperty("rate", int(self.config.get("vitesse_voix", 185)))
        moteur.setProperty("volume", self._volume())
        identifiant = self._identifiant_voix_systeme(moteur)
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
        """Hauteur de voix en Hz (config « hauteur_voix », de -50 à +50)."""
        hauteur = max(-50, min(50, int(self.config.get("hauteur_voix", 0))))
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
            if self.stt_local is not None:
                texte_local = self.stt_local(audio)
                if texte_local is not None:          # None = Whisper indisponible → Google prend le relais
                    return texte_local
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
