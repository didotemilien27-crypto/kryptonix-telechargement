"""
KRYPTONIX / noyau/extensions/gestes.py
=======================================
Contrôle par gestes de la main devant la webcam — traitement 100 % LOCAL :
chaque image est analysée en mémoire puis jetée ; aucune image n'est enregistrée ni envoyée.

Gestes (main devant la caméra) :
  ✋ main ouverte qui monte / descend      → défilement de la fenêtre active
  🤏 pincement pouce-index qui monte / descend → volume + / −
  ✊ poing maintenu ~1,5 s                  → lecture / pause

Moteurs : MediaPipe Hands (précis, `pip install mediapipe==0.10.14 opencv-python`) ; à défaut,
repli OpenCV sur le MOUVEMENT seul (grand mouvement vertical devant la caméra → défilement).
Désactivé par défaut : dis « active les gestes » (ou réglage `gestes_actif`).
"""

from __future__ import annotations

import logging
import math
import platform
import re
import threading
import time

from .base import ErreurModule, Module, Outil, normaliser

LOG = logging.getLogger("gestes")

# Indices MediaPipe : pouce (4), index (8,6), majeur (12,10), annulaire (16,14), auriculaire (20,18), poignet (0), base majeur (9)
BOUTS = (8, 12, 16, 20)
ARTICULATIONS = (6, 10, 14, 18)


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def classer_main(pts: list[tuple[float, float]]) -> str | None:
    """pts : 21 points (x, y) normalisés (y vers le bas). → 'ouverte' | 'poing' | 'pincement' | 'autre'."""
    if not pts or len(pts) < 21:
        return None
    echelle = max(_dist(pts[0], pts[9]), 1e-6)                 # taille de la main
    etendus = sum(1 for b, a in zip(BOUTS, ARTICULATIONS) if _dist(pts[b], pts[0]) > _dist(pts[a], pts[0]) * 1.08)
    if etendus == 0:
        return "poing"
    if _dist(pts[4], pts[8]) < 0.28 * echelle:       # pouce et index qui se touchent (les autres doigts peuvent être tendus)
        return "pincement"
    if etendus >= 4:
        return "ouverte"
    return "autre"


class Interpreteur:
    """Transforme une suite (geste, position verticale) en événements."""

    def __init__(self, seuil=0.035, duree_poing=1.5, delai_evenement=0.12):
        self.seuil, self.duree_poing, self.delai = seuil, duree_poing, delai_evenement
        self._y: float | None = None
        self._geste: str | None = None
        self._debut_poing = 0.0
        self._dernier = 0.0
        self._poing_fait = False

    def pousser(self, geste: str | None, y: float | None, t: float | None = None) -> list[tuple[str, float]]:
        t = time.time() if t is None else t
        evenements: list[tuple[str, float]] = []
        if geste is None or y is None:
            self._y, self._geste, self._poing_fait = None, None, False
            return evenements
        if geste != self._geste:
            self._geste, self._y = geste, y
            self._debut_poing, self._poing_fait = t, False
            return evenements
        if geste in ("ouverte", "pincement") and self._y is not None:
            d = y - self._y
            if abs(d) >= self.seuil and t - self._dernier >= self.delai:
                evenements.append(("defiler" if geste == "ouverte" else "volume", -d))   # main qui monte → valeur positive
                self._y, self._dernier = y, t
        elif geste == "poing" and not self._poing_fait and t - self._debut_poing >= self.duree_poing:
            evenements.append(("pause", 0.0))
            self._poing_fait = True
        return evenements


class Gestes(Module):
    nom = "gestes"
    description = "Contrôle par gestes de la main (webcam, 100 % local)"
    exemples = ("active les gestes", "désactive les gestes")

    def __init__(self, kx):
        super().__init__(kx)
        self._arret = threading.Event()
        self._thread: threading.Thread | None = None
        self.moteur = ""
        self.dernier_geste = ""
        self.actif = False

    @staticmethod
    def dependances() -> dict:
        dispo = {}
        try:
            import cv2  # noqa: F401
            dispo["opencv"] = True
        except ImportError:
            dispo["opencv"] = False
        try:
            import mediapipe as mp
            dispo["mediapipe"] = hasattr(mp, "solutions")
        except ImportError:
            dispo["mediapipe"] = False
        return dispo

    # -- actions ----------------------------------------------------------
    def agir(self, evenement: str, valeur: float) -> None:
        pc = self.kx.extensions.module("pc")
        self.dernier_geste = evenement
        self.kx.extensions.sons.jouer("geste")
        try:
            if evenement == "defiler":
                self._molette(int(valeur * 4000))
            elif evenement == "volume" and pc:
                pc.volume(delta=10 if valeur > 0 else -10)
            elif evenement == "pause" and pc:
                pc.media("play_pause")
        except Exception as e:
            LOG.debug("geste sans effet : %s", e)

    @staticmethod
    def _molette(quantite: int) -> None:
        import subprocess
        if platform.system() == "Windows":
            import ctypes
            ctypes.windll.user32.mouse_event(0x0800, 0, 0, quantite, 0)
        elif platform.system() == "Linux":
            subprocess.Popen(["xdotool", "click", "4" if quantite > 0 else "5"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # -- boucle caméra ----------------------------------------------------
    def demarrer_gestes(self) -> str:
        if self.actif:
            return "Les gestes sont déjà actifs."
        dep = self.dependances()
        if not dep["opencv"]:
            raise ErreurModule("OpenCV est absent (pip install opencv-python)")
        self.moteur = "mediapipe" if dep["mediapipe"] else "mouvement"
        self._arret.clear()
        self.actif = True
        self._thread = threading.Thread(target=self._boucle, daemon=True, name="kryptonix-gestes")
        self._thread.start()
        self.kx.extensions.sons.jouer("succes")
        return ("Gestes actifs (suivi de la main)." if self.moteur == "mediapipe"
                else "Gestes actifs en mode simple : seul le mouvement vertical est détecté (installe mediapipe pour plus de précision).")

    def arreter_gestes(self) -> str:
        self._arret.set()
        self.actif = False
        return "Gestes désactivés, la caméra est libérée."

    def _boucle(self) -> None:
        import cv2
        cam = cv2.VideoCapture(int(self.config.get("gestes_camera", 0)))
        if not cam.isOpened():
            self.actif = False
            self.kx._diffuser("alerte", {"message": "Gestes : webcam inaccessible."})
            return
        interp = Interpreteur()
        mains = None
        if self.moteur == "mediapipe":
            import mediapipe as mp
            mains = mp.solutions.hands.Hands(max_num_hands=1, min_detection_confidence=0.6, min_tracking_confidence=0.5)
        precedent = None
        try:
            while not self._arret.is_set():
                ok, image = cam.read()
                if not ok:
                    time.sleep(0.1)
                    continue
                if mains is not None:
                    res = mains.process(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
                    if res.multi_hand_landmarks:
                        pts = [(p.x, p.y) for p in res.multi_hand_landmarks[0].landmark]
                        evs = interp.pousser(classer_main(pts), pts[9][1])
                    else:
                        evs = interp.pousser(None, None)
                else:
                    gris = cv2.GaussianBlur(cv2.cvtColor(cv2.resize(image, (160, 120)), cv2.COLOR_BGR2GRAY), (9, 9), 0)
                    evs = []
                    if precedent is not None:
                        diff = cv2.threshold(cv2.absdiff(precedent, gris), 25, 255, cv2.THRESH_BINARY)[1]
                        m = cv2.moments(diff)
                        if m["m00"] > 160 * 120 * 255 * 0.03:
                            evs = interp.pousser("ouverte", m["m01"] / m["m00"] / 120)
                        else:
                            evs = interp.pousser(None, None)
                    precedent = gris
                del image                                          # rien n'est conservé
                for e, v in evs:
                    self.agir(e, v)
                time.sleep(0.03)
        finally:
            cam.release()
            if mains is not None:
                mains.close()
            self.actif = False

    def arreter(self) -> None:
        self._arret.set()

    def demarrer(self) -> None:
        if self.config.get("gestes_actif", False):
            try:
                self.demarrer_gestes()
            except ErreurModule as e:
                LOG.warning("gestes : %s", e)

    def intention(self, texte: str, commande: str) -> str | None:
        c = normaliser(commande)
        if not re.search(r"\bgestes?\b", c):
            return None
        try:
            if re.search(r"\b(active|lance|demarre|allume)\b", c):
                return self.demarrer_gestes()
            if re.search(r"\b(desactive|coupe|arrete|eteins)\b", c):
                return self.arreter_gestes()
        except ErreurModule as e:
            return f"Gestes : {e}."
        return "Les gestes sont actifs." if self.actif else "Les gestes sont désactivés."

    def statut(self) -> dict:
        return {"actif": self.actif, "moteur": self.moteur, **self.dependances(), "dernier": self.dernier_geste}

    def outils(self) -> list[Outil]:
        return []
