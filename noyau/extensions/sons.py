"""
KRYPTONIX / noyau/extensions/sons.py
=====================================
Pack de sons : ~40 effets sonores SYNTHÉTISÉS localement (aucun fichier à télécharger, aucun
droit d'auteur). Ils sont fabriqués au premier lancement (en arrière-plan) dans
`donnees/sons/`, puis simplement rejoués.

Chaque événement de l'assistant a son signal : réveil, écoute, réflexion, succès, erreur,
alerte, minuteur, courriel, appel, lumières, présence, capture d'écran, sécurité N3...

Lecture : pygame (déjà utilisé par la voix) → winsound (Windows) → afplay / aplay / paplay.
"""

from __future__ import annotations

import logging
import math
import os
import random
import shutil
import struct
import subprocess
import sys
import threading
import wave

from ..config import DOSSIER_DONNEES

LOG = logging.getLogger("sons")
FE = 22050          # fréquence d'échantillonnage (suffisante pour des effets, 2× plus rapide à fabriquer)
TAU = 2 * math.pi

try:
    import pygame
except ImportError:  # pragma: no cover
    pygame = None

# ---------------------------------------------------------------------------
#  Mini synthétiseur
# ---------------------------------------------------------------------------
def _env(i: int, n: int, a: float, r: float) -> float:
    """Enveloppe : attaque a, déclin exponentiel, relâchement r (fractions de la durée)."""
    t = i / max(1, n)
    att = min(1.0, t / max(a, 1e-4))
    rel = min(1.0, (1 - t) / max(r, 1e-4))
    return att * rel * (0.35 + 0.65 * math.exp(-3.0 * t))


def ton(freq: float, duree: float, vol: float = 0.5, harm=(1.0,), a: float = 0.02, r: float = 0.3,
        vers: float | None = None, vibrato: float = 0.0, carre: bool = False) -> list[float]:
    """Une note : harmoniques, glissando (vers), vibrato."""
    n = int(FE * duree)
    out, phase = [], 0.0
    for i in range(n):
        f = freq if vers is None else freq + (vers - freq) * i / n
        if vibrato:
            f *= 1 + 0.012 * math.sin(TAU * vibrato * i / FE)
        phase += TAU * f / FE
        v = sum(h * math.sin(phase * (k + 1)) for k, h in enumerate(harm))
        if carre:
            v = math.copysign(0.6, math.sin(phase)) + 0.4 * v
        out.append(vol * v * _env(i, n, a, r) / max(1.0, sum(harm)))
    return out


def silence(duree: float) -> list[float]:
    return [0.0] * int(FE * duree)


def bruit(duree: float, vol: float = 0.3, grave: float = 0.0, a: float = 0.01, r: float = 0.6) -> list[float]:
    """Bruit blanc (grave > 0 : filtré passe-bas, plus doux)."""
    n, out, y = int(FE * duree), [], 0.0
    graine = random.Random(7)
    for i in range(n):
        x = graine.uniform(-1, 1)
        y = grave * y + (1 - grave) * x
        out.append(vol * y * _env(i, n, a, r))
    return out


def mixer(*pistes: list[float]) -> list[float]:
    n = max(len(p) for p in pistes)
    return [sum(p[i] for p in pistes if i < len(p)) for i in range(n)]


def suite(*morceaux: list[float]) -> list[float]:
    out: list[float] = []
    for m in morceaux:
        out.extend(m)
    return out


def decaler(piste: list[float], delai: float) -> list[float]:
    return silence(delai) + piste


def echo(piste: list[float], delai: float = 0.12, gain: float = 0.4, fois: int = 2) -> list[float]:
    pistes = [piste]
    for k in range(1, fois + 1):
        pistes.append([x * gain ** k for x in decaler(piste, delai * k)])
    return mixer(*pistes)


NOTES = {"do": 523.25, "re": 587.33, "mi": 659.25, "fa": 698.46, "sol": 783.99, "la": 880.0, "si": 987.77,
         "do2": 1046.5, "re2": 1174.7, "mi2": 1318.5, "sol2": 1568.0, "la_": 440.0, "sol_": 392.0,
         "mi_": 329.63, "do_": 261.63}
CLOCHE = (1.0, 0.5, 0.25, 0.12)
DOUX = (1.0, 0.3)
CUIVRE = (1.0, 0.6, 0.4, 0.3)


def arpege(notes, pas=0.07, duree=0.25, vol=0.45, harm=CLOCHE, r=0.5):
    return mixer(*[decaler(ton(NOTES[n], duree, vol, harm, r=r), pas * i) for i, n in enumerate(notes)])


# ---------------------------------------------------------------------------
#  Le catalogue : nom → (description, fabrique)
# ---------------------------------------------------------------------------
def _catalogue() -> dict:
    N = NOTES
    C: dict = {}

    def s(nom, desc, fabrique):
        C[nom] = (desc, fabrique)          # la fabrique n'est appelée qu'à la création du fichier (démarrage instantané)

    s("demarrage", "Démarrage de l'assistant", lambda: mixer(arpege(["do_", "mi_", "sol_", "do", "mi", "sol", "do2"], 0.09, 0.5, 0.4),
                                                  ton(N["do_"] / 2, 1.0, 0.25, DOUX, r=0.8)))
    s("arret", "Extinction", lambda: arpege(["do2", "sol", "mi", "do", "sol_", "do_"], 0.1, 0.4, 0.4))
    s("reveil", "Mot d'activation reconnu", lambda: suite(ton(N["mi"], 0.08, 0.5, CLOCHE), ton(N["la"], 0.14, 0.55, CLOCHE)))
    s("ecoute_debut", "Je t'écoute", lambda: suite(ton(N["la_"], 0.07, 0.4, DOUX, vers=N["la"]), ton(N["mi2"], 0.1, 0.45, CLOCHE)))
    s("ecoute_fin", "Fin de l'écoute", lambda: suite(ton(N["mi2"], 0.07, 0.4, CLOCHE), ton(N["la"], 0.1, 0.4, DOUX, vers=N["la_"])))
    s("comprehension", "Phrase comprise", lambda: suite(ton(N["sol"], 0.06, 0.4, CLOCHE), ton(N["do2"], 0.09, 0.4, CLOCHE)))
    s("reflexion", "Je réfléchis", lambda: mixer(ton(300, 0.35, 0.3, DOUX, vibrato=7, vers=420, a=0.3, r=0.5)))
    s("parole_debut", "Je parle", lambda: ton(N["do"], 0.08, 0.3, DOUX))
    s("succes", "Action réussie", lambda: arpege(["do", "mi", "sol", "do2"], 0.07, 0.3, 0.45))
    s("erreur", "Erreur", lambda: suite(ton(220, 0.16, 0.5, CUIVRE, carre=True), silence(0.03), ton(165, 0.28, 0.5, CUIVRE, carre=True)))
    s("avertissement", "Avertissement", lambda: suite(ton(N["la_"], 0.12, 0.45, CUIVRE), silence(0.05), ton(N["la_"], 0.12, 0.45, CUIVRE)))
    s("alerte", "Alerte matérielle", lambda: suite(*[suite(ton(880, 0.12, 0.5, CUIVRE, carre=True), ton(660, 0.12, 0.5, CUIVRE, carre=True)) for _ in range(3)]))
    s("notification", "Notification", lambda: suite(ton(880, 0.08, 0.4, CLOCHE), ton(1175, 0.14, 0.4, CLOCHE)))
    s("message_recu", "Message reçu", lambda: suite(ton(N["mi2"], 0.06, 0.4, CLOCHE), ton(N["sol2"], 0.1, 0.4, CLOCHE)))
    s("message_envoye", "Message envoyé", lambda: suite(ton(N["sol"], 0.05, 0.4, CLOCHE), ton(N["mi"], 0.08, 0.35, CLOCHE)))
    s("clic", "Clic discret", lambda: mixer(ton(1800, 0.02, 0.35, (1.0,), a=0.01, r=0.6), bruit(0.015, 0.2, 0.3)))
    s("minuteur", "Minuteur terminé", lambda: suite(*[arpege(["la", "do2", "mi2"], 0.05, 0.2, 0.5) if i % 2 == 0 else silence(0.15) for i in range(6)]))
    s("alarme", "Alarme", lambda: suite(*[suite(ton(1000, 0.18, 0.5, CUIVRE, carre=True), ton(1400, 0.18, 0.5, CUIVRE, carre=True)) for _ in range(5)]))
    s("rappel", "Rappel", lambda: echo(suite(ton(N["mi2"], 0.1, 0.45, CLOCHE), ton(N["do2"], 0.2, 0.45, CLOCHE)), 0.25, 0.5, 2))
    s("confirmation_demande", "Demande de confirmation", lambda: suite(ton(N["re"], 0.09, 0.4, CLOCHE), ton(N["la"], 0.16, 0.45, CLOCHE, vibrato=6)))
    s("confirmation_ok", "Confirmation reçue", lambda: suite(ton(N["sol"], 0.07, 0.4, CLOCHE), ton(N["do2"], 0.12, 0.45, CLOCHE)))
    s("annule", "Action annulée", lambda: suite(ton(N["la"], 0.08, 0.4, DOUX), ton(N["mi"], 0.08, 0.4, DOUX), ton(N["do_"], 0.16, 0.4, DOUX)))
    s("securite_n3", "Action sensible : délai d'annulation", lambda: suite(*[suite(ton(520, 0.2, 0.45, CUIVRE, vers=980), silence(0.05)) for _ in range(3)]))
    s("courriel", "Nouveau courriel", lambda: suite(ton(N["do2"], 0.06, 0.4, CLOCHE), ton(N["mi2"], 0.06, 0.4, CLOCHE), ton(N["sol2"], 0.14, 0.4, CLOCHE)))
    s("appel_entrant", "Appel entrant", lambda: suite(*[mixer(ton(440, 0.4, 0.4, DOUX), ton(480, 0.4, 0.4, DOUX)) if i % 2 == 0 else silence(0.2) for i in range(4)]))
    s("appel_sortant", "Appel sortant", lambda: suite(*[mixer(ton(425, 0.6, 0.35, (1.0,), a=0.05, r=0.1)) if i % 2 == 0 else silence(0.5) for i in range(3)]))
    s("raccroche", "Fin d'appel", lambda: suite(ton(480, 0.12, 0.4, DOUX), silence(0.05), ton(380, 0.2, 0.4, DOUX)))
    s("lumiere_on", "Lumière allumée", lambda: ton(N["do"], 0.25, 0.4, DOUX, vers=N["do2"], r=0.5))
    s("lumiere_off", "Lumière éteinte", lambda: ton(N["do2"], 0.25, 0.4, DOUX, vers=N["do"], r=0.5))
    s("scene_lumiere", "Ambiance lumineuse", lambda: arpege(["do", "mi", "sol", "si", "re2", "sol2"], 0.09, 0.5, 0.35, DOUX, 0.7))
    s("presence_retour", "Bon retour", lambda: arpege(["sol_", "do", "mi", "sol"], 0.1, 0.45, 0.45))
    s("presence_depart", "À bientôt", lambda: arpege(["sol", "mi", "do", "sol_"], 0.1, 0.45, 0.45))
    s("capture_ecran", "Capture d'écran", lambda: mixer(bruit(0.07, 0.5, 0.2, 0.02, 0.9), ton(2200, 0.03, 0.3), decaler(bruit(0.09, 0.35, 0.5), 0.09)))
    s("geste", "Geste reconnu", lambda: suite(ton(N["la"], 0.04, 0.35, CLOCHE), ton(N["mi2"], 0.07, 0.35, CLOCHE)))
    s("ouverture_app", "Ouverture d'application", lambda: ton(400, 0.18, 0.4, DOUX, vers=900, r=0.4))
    s("volume", "Réglage du volume", lambda: ton(N["la"], 0.07, 0.4, CLOCHE))
    s("enregistrement_debut", "Début d'enregistrement", lambda: suite(ton(N["la"], 0.1, 0.5, CUIVRE), ton(N["la"], 0.1, 0.5, CUIVRE), ton(N["do2"], 0.2, 0.55, CUIVRE)))
    s("enregistrement_fin", "Fin d'enregistrement", lambda: suite(ton(N["do2"], 0.1, 0.5, CUIVRE), ton(N["la"], 0.18, 0.5, CUIVRE)))
    s("direct_on_air", "Direct démarré", lambda: suite(arpege(["do", "sol", "do2"], 0.06, 0.2, 0.5, CUIVRE), ton(N["do2"], 0.3, 0.5, CUIVRE, vibrato=5)))
    s("direct_fin", "Direct terminé", lambda: suite(ton(N["do2"], 0.12, 0.45, CUIVRE), ton(N["sol"], 0.12, 0.45, CUIVRE), ton(N["do"], 0.3, 0.45, CUIVRE)))
    s("scan", "Analyse en cours", lambda: mixer(ton(300, 0.5, 0.3, DOUX, vers=1800, a=0.1, r=0.3), bruit(0.5, 0.08, 0.7)))
    s("ia_hybride", "Basculement de moteur IA", lambda: suite(ton(N["sol"], 0.06, 0.4, CLOCHE), ton(N["re2"], 0.06, 0.4, CLOCHE), ton(N["sol"], 0.1, 0.4, CLOCHE)))
    s("budget", "Plafond de budget atteint", lambda: suite(ton(330, 0.15, 0.5, CUIVRE), ton(262, 0.15, 0.5, CUIVRE), ton(196, 0.35, 0.5, CUIVRE)))
    s("satellite", "Annonce dans une pièce", lambda: echo(ton(N["sol"], 0.15, 0.45, CLOCHE), 0.18, 0.5, 2))
    s("document", "Document prêt", lambda: suite(ton(N["mi"], 0.06, 0.4, CLOCHE), ton(N["sol"], 0.06, 0.4, CLOCHE), ton(N["do2"], 0.12, 0.4, CLOCHE)))
    s("tic", "Tic de minuteur", lambda: mixer(ton(1500, 0.015, 0.3, (1.0,), a=0.01, r=0.7), bruit(0.01, 0.15, 0.4)))
    return C


def _ecrire_wav(chemin: str, piste: list[float], volume: float = 0.9) -> None:
    crete = max(1e-6, max(abs(x) for x in piste))
    gain = min(1.0, 0.95 / crete) * volume
    fondu = int(FE * 0.004)
    with wave.open(chemin, "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(FE)
        frames = bytearray()
        n = len(piste)
        for i, x in enumerate(piste):
            if i < fondu:
                x *= i / fondu
            elif i > n - fondu:
                x *= (n - i) / fondu
            frames += struct.pack("<h", int(max(-1.0, min(1.0, x * gain)) * 32767))
        f.writeframes(bytes(frames))


class Sons:
    """Fabrique et joue les effets sonores."""

    def __init__(self, config: dict, dossier: str | None = None):
        self.config = config
        self.dossier = dossier or os.path.join(DOSSIER_DONNEES, "sons")
        os.makedirs(self.dossier, exist_ok=True)
        self.catalogue = _catalogue()
        self._pret = threading.Event()
        self._lecteur = None
        self._verrou = threading.Lock()
        self.historique: list[str] = []          # derniers sons joués (visible dans /panneau)

    # -- fabrication ----------------------------------------------------
    def chemin(self, nom: str) -> str:
        return os.path.join(self.dossier, f"{nom}.wav")

    def fabriquer(self, forcer: bool = False) -> int:
        """Crée les fichiers manquants. Retourne le nombre de sons fabriqués."""
        n = 0
        for nom, (_desc, fabrique) in self.catalogue.items():
            c = self.chemin(nom)
            if forcer or not os.path.exists(c) or os.path.getsize(c) < 100:
                try:
                    _ecrire_wav(c, fabrique())
                    n += 1
                except Exception as e:
                    LOG.warning("son %s non fabriqué : %s", nom, e)
        self._pret.set()
        return n

    def preparer_en_arriere_plan(self) -> None:
        threading.Thread(target=self.fabriquer, daemon=True, name="kryptonix-sons").start()

    def liste(self) -> list[dict]:
        return [{"nom": n, "description": d, "pret": os.path.exists(self.chemin(n))}
                for n, (d, _p) in self.catalogue.items()]

    # -- lecture --------------------------------------------------------
    @property
    def actifs(self) -> bool:
        return bool(self.config.get("sons_actifs", True))

    def jouer(self, nom: str, force: bool = False) -> bool:
        """Joue un son sans bloquer. Retourne False si rien n'a pu être joué."""
        if nom not in self.catalogue or (not self.actifs and not force):
            return False
        self.historique = (self.historique + [nom])[-12:]
        chemin = self.chemin(nom)
        if not os.path.exists(chemin):
            if not self._pret.is_set():
                return False
            try:
                _ecrire_wav(chemin, self.catalogue[nom][1]())
            except Exception:
                return False
        threading.Thread(target=self._lire, args=(chemin,), daemon=True).start()
        return True

    def _volume(self) -> float:
        try:
            return max(0.0, min(1.0, int(self.config.get("sons_volume", 70)) / 100))
        except (TypeError, ValueError):
            return 0.7

    def _lire(self, chemin: str) -> None:
        try:
            if pygame is not None:
                with self._verrou:
                    if not pygame.mixer.get_init():
                        pygame.mixer.init()
                son = pygame.mixer.Sound(chemin)
                son.set_volume(self._volume())
                son.play()
                return
            if sys.platform.startswith("win"):
                import winsound
                winsound.PlaySound(chemin, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return
            for lecteur in ("afplay", "paplay", "aplay", "play"):
                if shutil.which(lecteur):
                    subprocess.Popen([lecteur, chemin], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
        except Exception as e:
            LOG.debug("lecture du son impossible : %s", e)

    def joue_pour_etat(self, etat: str) -> None:
        """Relie les états de l'assistant à des sons (appelé par l'abonnement aux événements)."""
        table = {"ecoute": "ecoute_debut", "reflexion": "reflexion", "ingestion": "scan"}
        if etat in table:
            self.jouer(table[etat])

    # -- commandes vocales ---------------------------------------------
    def intention(self, commande: str) -> str | None:
        from .base import normaliser
        c = normaliser(commande)
        if re.search(r"\b(coupe|desactive|mets? en sourdine) (?:les )?sons?\b|\bsans sons? \b", c):
            self.config["sons_actifs"] = False
            return "Effets sonores coupés. Dis « active les sons » pour les remettre."
        if re.search(r"\b(active|reactive|remets?) (?:les )?sons?\b", c):
            self.config["sons_actifs"] = True
            self.jouer("succes")
            return "Effets sonores activés."
        m = re.search(r"\b(?:joue|fais entendre|ecoute) (?:le )?son (?:de |du |d')?(.+)", c)
        if m:
            cible = m.group(1).strip()
            for nom, (desc, _p) in self.catalogue.items():
                if cible in nom.replace("_", " ") or cible in normaliser(desc):
                    self.jouer(nom, force=True)
                    return f"Voici le son « {desc} »."
            return "Je n'ai pas ce son. Dis « liste les sons » pour les connaître."
        if re.search(r"\b(liste|quels sont) (?:les |mes )?sons\b", c):
            return (f"J'ai {len(self.catalogue)} effets sonores : "
                    + ", ".join(d.lower() for d, _p in list(self.catalogue.values())[:14]) + " et d'autres.")
        return None


import re  # noqa: E402  (utilisé par intention)
