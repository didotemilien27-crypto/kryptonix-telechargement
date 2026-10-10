"""
KRYPTONIX / noyau/medias.py
============================
Outils de bas niveau pour comprendre les enregistrements audio, les images et
les vidéos :

  - ffmpeg (embarqué avec l'application) : sondage d'un fichier, extraction de
    la bande sonore, extraction d'images clés réparties dans une vidéo ;
  - Transcripteur : transforme la parole en texte.
        1. faster-whisper — local, hors ligne, très bon en français. Le modèle
           (75 à 480 Mo selon la précision choisie) est téléchargé UNE fois,
           au premier usage ou depuis RÉGLAGES → Médias.
        2. Google Web Speech — secours en ligne si faster-whisper est absent.

Aucune exception « brute » ne sort d'ici : tout échec est une
ErreurComprehension portant une clé (voir noyau/repli.py) pour que
l'interface réponde avec douceur plutôt que par un message technique.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import shutil
import subprocess
import threading
from typing import Callable

from .config import DOSSIER_MODELES

LOG = logging.getLogger("medias")

# Le téléchargement du modèle d'oreille ne doit rien envoyer d'autre que la requête du fichier,
# et Windows sans « mode développeur » ne sait pas faire de liens symboliques (copie à la place).
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

try:
    from faster_whisper import WhisperModel
except Exception:  # absent, ou DLL introuvable : on bascule sur le secours en ligne
    WhisperModel = None

try:
    import speech_recognition as sr
except ImportError:  # pragma: no cover
    sr = None

EXT_AUDIO = {".mp3", ".wav", ".m4a", ".ogg", ".oga", ".opus", ".flac", ".aac", ".wma", ".amr"}
EXT_IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
EXT_VIDEO = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".3gp"}
EXT_MEDIAS = EXT_AUDIO | EXT_IMAGE | EXT_VIDEO

LIMITES_OCTETS = {"audio": 200 * 1024 * 1024, "image": 30 * 1024 * 1024, "video": 500 * 1024 * 1024}
DUREE_MAX_SECONDES = 90 * 60            # au-delà, on n'écoute que les 90 premières minutes

PRECISIONS_AUDIO = {
    "tiny": "rapide (environ 75 Mo)",
    "base": "équilibrée (environ 145 Mo)",
    "small": "précise (environ 480 Mo)",
}

SANS_FENETRE = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

Suivi = Callable[[int, str], None]


class ErreurComprehension(Exception):
    """Échec de compréhension d'un média. `cle` désigne un cas de noyau/repli.py."""

    def __init__(self, cle: str, detail: str = ""):
        super().__init__(detail or cle)
        self.cle = cle
        self.detail = detail


def type_de_fichier(chemin: str) -> str | None:
    """'audio' | 'image' | 'video' d'après l'extension, sinon None (un .webm/.mp4 sans image devient audio plus tard)."""
    extension = os.path.splitext(chemin)[1].lower()
    if extension in EXT_AUDIO:
        return "audio"
    if extension in EXT_IMAGE:
        return "image"
    if extension in EXT_VIDEO:
        return "video"
    return None


# ======================================================================
#  ffmpeg
# ======================================================================
def ffmpeg() -> str | None:
    """Chemin de ffmpeg : celui embarqué par imageio-ffmpeg, sinon celui du système."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass
    return shutil.which("ffmpeg")


def _lancer(arguments: list[str], delai: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL, timeout=delai,
                          creationflags=SANS_FENETRE)


def sonder(chemin: str) -> dict:
    """{valide, duree (s), audio, video} d'après la sortie de `ffmpeg -i` (pas de ffprobe nécessaire)."""
    exe = ffmpeg()
    if not exe:
        raise ErreurComprehension("ffmpeg_absent")
    try:
        sortie = _lancer([exe, "-hide_banner", "-i", chemin], 60).stderr or ""
    except (subprocess.TimeoutExpired, OSError) as erreur:
        raise ErreurComprehension("illisible", str(erreur)) from erreur
    duree = 0.0
    trouve = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", sortie)
    if trouve:
        heures, minutes, secondes = trouve.groups()
        duree = int(heures) * 3600 + int(minutes) * 60 + float(secondes)
    audio = bool(re.search(r"Stream #[^\n]*Audio:", sortie))
    # Une pochette d'album (« attached pic ») n'est pas une vidéo.
    video = any("attached pic" not in ligne for ligne in sortie.splitlines()
                if re.search(r"Stream #[^\n]*Video:", ligne))
    return {"valide": bool(trouve or audio or video), "duree": duree, "audio": audio, "video": video}


def extraire_audio(chemin: str, sortie_wav: str, duree_max: int = DUREE_MAX_SECONDES) -> None:
    """Convertit n'importe quelle piste sonore en WAV mono 16 kHz (le format idéal pour la parole)."""
    exe = ffmpeg()
    if not exe:
        raise ErreurComprehension("ffmpeg_absent")
    try:
        resultat = _lancer([exe, "-y", "-hide_banner", "-loglevel", "error", "-i", chemin,
                            "-vn", "-ac", "1", "-ar", "16000", "-t", str(duree_max), sortie_wav], 900)
    except (subprocess.TimeoutExpired, OSError) as erreur:
        raise ErreurComprehension("illisible", str(erreur)) from erreur
    if resultat.returncode != 0 or not os.path.isfile(sortie_wav) or os.path.getsize(sortie_wav) < 100:
        raise ErreurComprehension("illisible", (resultat.stderr or "")[-300:])


def extraire_images(chemin: str, nombre: int, dossier: str, duree: float) -> list[tuple[float, str]]:
    """Extrait `nombre` images réparties régulièrement dans la vidéo : [(seconde, chemin_jpg), ...]."""
    exe = ffmpeg()
    if not exe:
        raise ErreurComprehension("ffmpeg_absent")
    nombre = max(1, min(12, int(nombre)))
    instants = [(i + 0.5) * duree / nombre for i in range(nombre)] if duree > 0 else [0.0]
    images: list[tuple[float, str]] = []
    for indice, instant in enumerate(instants):
        sortie = os.path.join(dossier, f"image_{indice:02d}.jpg")
        try:
            _lancer([exe, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{instant:.2f}",
                     "-i", chemin, "-frames:v", "1", "-vf", "scale='min(768,iw)':-2",
                     "-q:v", "4", sortie], 120)
        except (subprocess.TimeoutExpired, OSError):
            continue
        if os.path.isfile(sortie) and os.path.getsize(sortie) > 200:
            images.append((instant, sortie))
    return images


def horloge(secondes: float) -> str:
    secondes = int(max(0, secondes))
    return f"{secondes // 60}:{secondes % 60:02d}"


def duree_lisible(secondes: float) -> str:
    secondes = int(round(secondes))
    if secondes < 60:
        return f"{secondes} s"
    minutes, reste = divmod(secondes, 60)
    if minutes < 60:
        return f"{minutes} min {reste:02d} s" if reste else f"{minutes} min"
    heures, minutes = divmod(minutes, 60)
    return f"{heures} h {minutes:02d} min"


# ======================================================================
#  Transcription (parole → texte)
# ======================================================================
class Transcripteur:
    """Oreille de KRYPTONIX : faster-whisper en local, Google en secours."""

    def __init__(self, config: dict):
        self.config = config
        self._modele = None
        self._nom_charge = ""
        self._verrou = threading.Lock()

    # ------------------------------------------------------------------
    def moteur_local_disponible(self) -> bool:
        return WhisperModel is not None

    def moteur_secours_disponible(self) -> bool:
        return sr is not None

    def nom_modele(self) -> str:
        nom = str(self.config.get("modele_audio", "base"))
        return nom if nom in PRECISIONS_AUDIO else "base"

    def modele_present(self, nom: str | None = None) -> bool:
        """Le modèle est-il déjà sur le disque (donc utilisable sans internet) ?"""
        nom = nom or self.nom_modele()
        motif = os.path.join(DOSSIER_MODELES, f"models--Systran--faster-whisper-{nom}",
                             "snapshots", "*", "model.bin")
        return bool(glob.glob(motif))

    def oublier_modele(self) -> None:
        """Appelé quand la précision change : le prochain usage chargera l'autre modèle."""
        with self._verrou:
            self._modele, self._nom_charge = None, ""

    def etat(self) -> dict:
        nom = self.nom_modele()
        return {
            "moteur": "faster-whisper" if self.moteur_local_disponible()
                      else ("google" if self.moteur_secours_disponible() else None),
            "modele": nom, "precision": PRECISIONS_AUDIO[nom],
            "pret": self.modele_present(nom) if self.moteur_local_disponible()
                    else self.moteur_secours_disponible(),
            "hors_ligne": self.moteur_local_disponible() and self.modele_present(nom),
        }

    # ------------------------------------------------------------------
    def preparer(self) -> None:
        """Charge (et télécharge au besoin) le modèle. Lève ErreurComprehension('oreille_absente')."""
        if WhisperModel is None:
            raise ErreurComprehension("oreille_absente", "faster-whisper n'est pas disponible")
        nom = self.nom_modele()
        with self._verrou:
            if self._modele is not None and self._nom_charge == nom:
                return
            try:
                os.makedirs(DOSSIER_MODELES, exist_ok=True)
                self._modele = WhisperModel(nom, device="cpu", compute_type="int8",
                                            download_root=DOSSIER_MODELES)
                self._nom_charge = nom
                LOG.info("Oreille prête : modèle Whisper « %s ».", nom)
            except Exception as erreur:
                self._modele, self._nom_charge = None, ""
                LOG.warning("Modèle Whisper « %s » indisponible : %s", nom, erreur)
                raise ErreurComprehension("oreille_absente", str(erreur)) from erreur

    def transcrire(self, wav: str, duree: float, suivi: Suivi | None = None) -> dict:
        """
        Transcrit un WAV 16 kHz mono. Retourne
        {texte (avec repères [m:ss]), brut, langue, moteur}.
        Lève ErreurComprehension('silence' | 'oreille_absente').
        """
        suivi = suivi or (lambda pct, msg: None)
        langue = str(self.config.get("langue_reconnaissance", "fr-FR")).split("-")[0].lower()
        derniere = None

        if self.moteur_local_disponible():
            try:
                if not self.modele_present():
                    suivi(0, "Je prépare mon oreille (téléchargement unique)…")
                self.preparer()
                return self._transcrire_local(wav, duree, langue, suivi)
            except ErreurComprehension as erreur:
                if erreur.cle != "oreille_absente":
                    raise
                derniere = erreur
                LOG.info("Transcription locale impossible, essai du secours en ligne.")

        if self.moteur_secours_disponible():
            return self._transcrire_en_ligne(wav, duree, langue, suivi)
        raise derniere or ErreurComprehension("oreille_absente", "aucun moteur de transcription")

    # ------------------------------------------------------------------
    def _transcrire_local(self, wav: str, duree: float, langue: str, suivi: Suivi) -> dict:
        try:
            try:
                segments, info = self._modele.transcribe(wav, language=langue or None,
                                                         vad_filter=True, beam_size=3)
            except Exception:  # la détection de silence (onnxruntime) peut manquer : on s'en passe
                segments, info = self._modele.transcribe(wav, language=langue or None, beam_size=3)
            blocs: list[tuple[float, str]] = []
            courant, debut_bloc = [], 0.0
            for segment in segments:
                texte = (segment.text or "").strip()
                if not texte:
                    continue
                if not courant:
                    debut_bloc = float(segment.start)
                courant.append(texte)
                if float(segment.end) - debut_bloc >= 25:
                    blocs.append((debut_bloc, " ".join(courant)))
                    courant = []
                if duree > 0:
                    suivi(min(99, int(float(segment.end) / duree * 100)),
                          f"J'écoute… {horloge(float(segment.end))} sur {horloge(duree)}")
            if courant:
                blocs.append((debut_bloc, " ".join(courant)))
        except ErreurComprehension:
            raise
        except Exception as erreur:
            LOG.warning("Transcription locale en échec : %s", erreur)
            raise ErreurComprehension("oreille_absente", str(erreur)) from erreur
        return self._assembler(blocs, getattr(info, "language", langue), "faster-whisper")

    def _transcrire_en_ligne(self, wav: str, duree: float, langue: str, suivi: Suivi) -> dict:
        """Secours : Google Web Speech par tranches de 40 s (sans clé, demande internet)."""
        reconnaisseur = sr.Recognizer()
        code = {"fr": "fr-FR", "en": "en-US"}.get(langue, str(self.config.get("langue_reconnaissance", "fr-FR")))
        blocs: list[tuple[float, str]] = []
        position = 0.0
        try:
            with sr.AudioFile(wav) as source:
                while True:
                    audio = reconnaisseur.record(source, duration=40)
                    if not audio.frame_data:
                        break
                    try:
                        texte = reconnaisseur.recognize_google(audio, language=code).strip()
                        if texte:
                            blocs.append((position, texte))
                    except sr.UnknownValueError:
                        pass
                    position += 40
                    if duree > 0:
                        suivi(min(99, int(position / duree * 100)),
                              f"J'écoute… {horloge(min(position, duree))} sur {horloge(duree)}")
        except sr.RequestError as erreur:
            raise ErreurComprehension("oreille_absente", str(erreur)) from erreur
        except Exception as erreur:
            raise ErreurComprehension("illisible", str(erreur)) from erreur
        return self._assembler(blocs, langue, "google")

    @staticmethod
    def _assembler(blocs: list[tuple[float, str]], langue: str, moteur: str) -> dict:
        if not blocs:
            raise ErreurComprehension("silence")
        texte = "\n".join(f"[{horloge(debut)}] {contenu}" for debut, contenu in blocs)
        brut = " ".join(contenu for _debut, contenu in blocs)
        return {"texte": texte, "brut": brut, "langue": langue, "moteur": moteur}
