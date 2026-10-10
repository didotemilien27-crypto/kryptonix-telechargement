#!/usr/bin/env python3
"""
KRYPTONIX — SATELLITE de pièce (Raspberry Pi, Linux, Windows, macOS).

Le satellite ne fait que : écouter dans sa pièce, envoyer la phrase au cerveau central (ton PC),
dire la réponse sur place, et lire les annonces destinées à la pièce.
Aucune dépendance obligatoire (bibliothèque standard).

    python3 kryptonix_satellite.py --cerveau http://192.168.1.10:5055 --jeton XXXX --piece cuisine

Mode test sans micro :  --texte   (tu tapes les phrases au clavier)

Écoute (au choix, détectée automatiquement) : `arecord` (Linux/RPi, paquet alsa-utils) ou `sounddevice`.
Voix (au choix) : `espeak-ng -v fr`, `pico2wave`, `say` (macOS), `pyttsx3`, ou la voix neuronale du cerveau
(réglage `satellite_tts_serveur` sur le PC + lecteur mpg123 / ffplay).
Mot d'éveil : le cerveau exige « kryptonix » dans la phrase (réglage satellite_exige_mot_reveil).
"""
import argparse
import array
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave

TAUX = 16000


def requete(base, jeton, methode, chemin, corps=None, type_="application/json", timeout=60):
    donnees = corps if isinstance(corps, bytes) else (json.dumps(corps).encode() if corps is not None else None)
    req = urllib.request.Request(base.rstrip("/") + chemin, data=donnees, method=methode,
                                 headers={"Authorization": f"Bearer {jeton}", "Content-Type": type_})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def parler(texte, audio_b64=None):
    if audio_b64:
        chemin = tempfile.mktemp(suffix=".mp3")
        with open(chemin, "wb") as f:
            f.write(base64.b64decode(audio_b64))
        for lecteur in (["mpg123", "-q"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"], ["afplay"]):
            if shutil.which(lecteur[0]):
                subprocess.run(lecteur + [chemin], check=False)
                os.remove(chemin)
                return
        os.remove(chemin)
    if shutil.which("espeak-ng"):
        subprocess.run(["espeak-ng", "-v", "fr", "-s", "160", texte], check=False)
    elif shutil.which("say"):
        subprocess.run(["say", "-v", "Thomas", texte], check=False)
    elif shutil.which("pico2wave") and shutil.which("aplay"):
        w = tempfile.mktemp(suffix=".wav")
        subprocess.run(["pico2wave", "-l", "fr-FR", "-w", w, texte], check=False)
        subprocess.run(["aplay", "-q", w], check=False)
        os.remove(w)
    else:
        try:
            import pyttsx3
            m = pyttsx3.init()
            m.say(texte)
            m.runAndWait()
        except Exception:
            print("[voix indisponible] " + texte)


def enregistrer_phrase(seuil=500, silence_fin=1.0, duree_max=12.0):
    """Capture une phrase (WAV 16 kHz mono) : démarre au son, s'arrête après un silence. None si rien."""
    if shutil.which("arecord"):
        proc = subprocess.Popen(["arecord", "-q", "-f", "S16_LE", "-r", str(TAUX), "-c", "1", "-t", "raw"],
                                stdout=subprocess.PIPE)
        lire = lambda n: proc.stdout.read(n)
        fin = proc.terminate
    else:
        import sounddevice as sd
        flux = sd.RawInputStream(samplerate=TAUX, channels=1, dtype="int16")
        flux.start()
        lire = lambda n: bytes(flux.read(n // 2)[0])
        fin = lambda: (flux.stop(), flux.close())
    morceaux, parle, calme, debut = [], False, 0.0, time.time()
    try:
        while time.time() - debut < duree_max + 30:
            brut = lire(3200)               # 0,1 s
            if not brut:
                break
            a = array.array("h", brut)
            niveau = (sum(abs(x) for x in a) / max(1, len(a)))
            if niveau > seuil:
                parle, calme = True, 0.0
            elif parle:
                calme += 0.1
            if parle:
                morceaux.append(brut)
                if calme >= silence_fin or len(morceaux) * 0.1 >= duree_max:
                    break
    finally:
        fin()
    if not morceaux:
        return None
    chemin = tempfile.mktemp(suffix=".wav")
    with wave.open(chemin, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TAUX)
        w.writeframes(b"".join(morceaux))
    with open(chemin, "rb") as f:
        data = f.read()
    os.remove(chemin)
    return data


def surveiller_annonces(base, jeton, piece, arret):
    while not arret.is_set():
        try:
            for a in requete(base, jeton, "GET", f"/satellite/annonces?piece={piece}", timeout=10).get("annonces", []):
                print(f"[annonce] {a}")
                parler(a)
        except (urllib.error.URLError, OSError, ValueError):
            pass
        arret.wait(3)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cerveau", required=True, help="adresse du cerveau, ex. http://192.168.1.10:5055")
    p.add_argument("--jeton", default=os.environ.get("KRYPTONIX_JETON", ""), help="jeton réseau (panneau)")
    p.add_argument("--piece", default="salon")
    p.add_argument("--texte", action="store_true", help="mode clavier, sans micro")
    p.add_argument("--seuil", type=int, default=500, help="sensibilité du micro (plus petit = plus sensible)")
    a = p.parse_args()
    if not a.jeton:
        sys.exit("Il faut le jeton réseau (--jeton ou variable KRYPTONIX_JETON).")
    try:
        requete(a.cerveau, a.jeton, "POST", "/satellite/enregistrer", {"piece": a.piece}, timeout=10)
    except (urllib.error.URLError, OSError) as e:
        sys.exit(f"Cerveau injoignable ({e}). Le réseau local est-il activé sur le PC ?")
    print(f"Satellite « {a.piece} » connecté à {a.cerveau}.")
    arret = threading.Event()
    threading.Thread(target=surveiller_annonces, args=(a.cerveau, a.jeton, a.piece, arret), daemon=True).start()
    try:
        while True:
            try:
                if a.texte:
                    texte = input("> ").strip()
                    if not texte:
                        continue
                    rep = requete(a.cerveau, a.jeton, "POST", "/satellite/commande", {"piece": a.piece, "texte": texte})
                else:
                    wav = enregistrer_phrase(a.seuil)
                    if not wav:
                        continue
                    rep = requete(a.cerveau, a.jeton, "POST", f"/satellite/audio?piece={a.piece}", wav, "audio/wav", 90)
                if rep.get("ignore") or not rep.get("reponse"):
                    continue
                print("Kryptonix :", rep["reponse"])
                parler(rep["reponse"], rep.get("audio_b64"))
            except (urllib.error.URLError, OSError) as e:
                print("Connexion perdue, nouvel essai…", e)
                time.sleep(3)
    except (KeyboardInterrupt, EOFError):
        arret.set()


if __name__ == "__main__":
    main()
