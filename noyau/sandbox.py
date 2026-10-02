"""
KRYPTONIX / noyau/sandbox.py
=============================
Exécution de code dans un bac à sable raisonnablement isolé — plusieurs
langages, un seul garde-fou commun.

Garde-fous appliqués à chaque langage :
  - sous-processus séparé, sans accès à l'état de KRYPTONIX ;
  - répertoire de travail temporaire, détruit après exécution ;
  - délai d'exécution strict, processus tué au-delà ;
  - analyse statique préalable : refus des motifs destructeurs adaptés à
    chaque langage (accès disque, réseau, appels système, fork-bomb).

Langages pris en charge : Python, JavaScript (Node.js), Bash, C, C++, Java,
Perl. Un langage dont l'interpréteur/compilateur n'est pas installé sur la
machine est refusé avec un message clair plutôt qu'une erreur brute.

Ce n'est pas une machine virtuelle : c'est une barrière de bon sens.
N'exécute jamais de code dont tu ne comprends pas l'intention.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

LOG = logging.getLogger("sandbox")

ALIAS_LANGAGE = {
    "py": "python", "python3": "python",
    "js": "javascript", "node": "javascript", "nodejs": "javascript",
    "sh": "bash", "shell": "bash",
    "c++": "cpp", "cxx": "cpp",
    "pl": "perl",
}

PREAMBULE_PYTHON = (
    "# --- Préambule injecté par KRYPTONIX ---\n"
    "import builtins, math, random, statistics, itertools, functools, json, re, datetime\n"
    "try:\n"
    "    import sympy\n"
    "except ImportError:\n"
    "    sympy = None\n"
    "# ----------------------------------------\n"
)


def _motifs_communs() -> list[tuple[str, str]]:
    return [(r"curl\s|wget\s", "accès réseau interdit")]


# Chaque entrée : extension, commande(s) à exécuter, motifs interdits,
# et l'outil dont la présence est vérifiée avant de se lancer.
LANGAGES: dict[str, dict] = {
    "python": {
        "libelle": "Python", "extension": ".py", "outil": sys.executable,
        "preambule": PREAMBULE_PYTHON,
        "commande": lambda chemin, dossier: (
            # Une fois packagé, sys.executable est l'application elle-même :
            # app.py reconnaît ce drapeau et exécute le script sans ouvrir de fenêtre.
            [sys.executable, "--kx-python", chemin] if getattr(sys, "frozen", False)
            else [sys.executable, "-I", "-S", chemin]),
        "motifs": [
            (r"\bimport\s+(os|subprocess|shutil|socket|ctypes|winreg|multiprocessing)\b",
             "import système interdit"),
            (r"\bfrom\s+(os|subprocess|shutil|socket|ctypes|winreg)\b", "import système interdit"),
            (r"\b__import__\s*\(", "import dynamique interdit"),
            (r"\b(eval|exec|compile)\s*\(", "évaluation dynamique interdite"),
            (r"\bopen\s*\([^)]*['\"][wax]", "écriture de fichier interdite"),
            (r"\b(rmtree|remove|unlink|rmdir|system|popen|fork|kill)\s*\(", "appel destructeur interdit"),
            (r"\bwhile\s+True\s*:(?![\s\S]{0,400}\bbreak\b)", "boucle infinie sans sortie"),
            (r"\brequests\b|\burllib\b|\bhttp\b", "accès réseau interdit"),
        ],
    },
    "javascript": {
        "libelle": "JavaScript (Node.js)", "extension": ".js", "outil": "node",
        "commande": lambda chemin, dossier: ["node", chemin],
        "motifs": [
            (r"require\s*\(\s*['\"](fs|child_process|net|http|https|dgram|cluster|os|dns)['\"]\s*\)",
             "module système interdit"),
            (r"\bimport\s+.*from\s+['\"](fs|child_process|net|http|https)['\"]",
             "module système interdit"),
            (r"\bprocess\.(exit|binding|kill)\b", "appel système interdit"),
            (r"\bfetch\s*\(|\bXMLHttpRequest\b", "accès réseau interdit"),
            (r"\bnew\s+Function\s*\(|\beval\s*\(", "évaluation dynamique interdite"),
            (r"while\s*\(\s*true\s*\)(?![\s\S]{0,400}\bbreak\b)", "boucle infinie sans sortie"),
        ],
    },
    "bash": {
        "libelle": "Bash", "extension": ".sh", "outil": "bash",
        "commande": lambda chemin, dossier: ["bash", chemin],
        "motifs": [
            (r"\b(rm|mkfs|dd|shred)\b", "commande destructrice interdite"),
            (r"\b(curl|wget|nc|ncat|ssh|scp|telnet|ping)\b", "accès réseau interdit"),
            (r"\bsudo\b|\bsu\b", "élévation de privilèges interdite"),
            (r":\(\)\s*\{.*:\|:.*\}", "fork-bomb interdite"),
            (r">\s*/dev/(sd|nvme)", "écriture disque brute interdite"),
            (r"\bchmod\s+777\b|\bchown\b", "modification de permissions interdite"),
        ],
    },
    "c": {
        "libelle": "C", "extension": ".c", "outil": "gcc", "compilateur": True,
        "compiler": lambda chemin, binaire: ["gcc", chemin, "-O2", "-o", binaire, "-lm"],
        "commande": lambda binaire, dossier: [binaire],
        "motifs": [
            (r"#include\s*<(unistd|sys/socket|sys/fork|netinet|arpa|winsock)",
             "en-tête système/réseau interdit"),
            (r"\b(system|popen|fork|exec[lv]e?p?|remove|unlink|socket)\s*\(",
             "appel destructeur ou réseau interdit"),
        ],
    },
    "cpp": {
        "libelle": "C++", "extension": ".cpp", "outil": "g++", "compilateur": True,
        "compiler": lambda chemin, binaire: ["g++", chemin, "-O2", "-std=c++17", "-o", binaire],
        "commande": lambda binaire, dossier: [binaire],
        "motifs": [
            (r"#include\s*<(unistd|sys/socket|sys/fork|netinet|arpa|winsock)",
             "en-tête système/réseau interdit"),
            (r"\b(system|popen|fork|exec[lv]e?p?|remove|unlink|socket)\s*\(",
             "appel destructeur ou réseau interdit"),
        ],
    },
    "java": {
        "libelle": "Java", "extension": ".java", "outil": "javac", "compilateur": True,
        "nom_classe_obligatoire": "Principal",
        "compiler": lambda chemin, binaire: ["javac", chemin],
        "commande": lambda binaire, dossier: ["java", "-cp", dossier, "Principal"],
        "motifs": [
            (r"Runtime\.getRuntime\s*\(|ProcessBuilder", "appel système interdit"),
            (r"\bjava\.net\.|\bSocket\s*\(|\bURLConnection\b", "accès réseau interdit"),
            (r"new\s+File(Writer|OutputStream)?\s*\([^)]*\)\s*;?\s*\n[\s\S]{0,80}\.write",
             "écriture de fichier interdite"),
            (r"System\.exit\s*\(", "arrêt forcé interdit"),
        ],
    },
    "perl": {
        "libelle": "Perl", "extension": ".pl", "outil": "perl",
        "commande": lambda chemin, dossier: ["perl", chemin],
        "motifs": [
            (r"\bsystem\s*\(|\bexec\s*\(|`[^`]*`", "appel système interdit"),
            (r"\buse\s+Socket\b|\buse\s+LWP\b|\buse\s+Net::", "accès réseau interdit"),
            (r"\bopen\s*\([^)]*,\s*['\"]>", "écriture de fichier interdite"),
            (r"\bunlink\s*\(", "suppression de fichier interdite"),
        ],
    },
}


class Sandbox:
    """Exécuteur de code multi-langage à usage unique."""

    def __init__(self, config: dict):
        self.timeout = int(config.get("sandbox_timeout", 12))
        self.active = bool(config.get("sandbox_active", True))

    # ------------------------------------------------------------------
    @staticmethod
    def langages_disponibles() -> list[dict]:
        """Liste les langages pris en charge, avec leur disponibilité réelle sur la machine."""
        resultat = []
        for cle, spec in LANGAGES.items():
            resultat.append({
                "id": cle, "libelle": spec["libelle"],
                "disponible": shutil.which(spec["outil"]) is not None,
            })
        return resultat

    def _resoudre_langage(self, langage: str) -> str:
        langage = (langage or "python").strip().lower()
        return ALIAS_LANGAGE.get(langage, langage)

    def auditer(self, code: str, langage: str = "python") -> str | None:
        """Retourne la raison du refus, ou None si le code est acceptable."""
        spec = LANGAGES.get(self._resoudre_langage(langage))
        if not spec:
            return "langage non supporté"
        for motif, raison in spec["motifs"] + _motifs_communs():
            if re.search(motif, code, flags=re.IGNORECASE):
                return raison
        if len(code) > 20_000:
            return "code trop volumineux"
        return None

    def executer(self, code: str, langage: str = "python") -> dict:
        """
        Exécute le code et retourne :
        {succes, sortie, erreur, refus, duree, langage}
        """
        cle = self._resoudre_langage(langage)
        resultat = {"succes": False, "sortie": "", "erreur": "", "refus": None,
                   "duree": 0.0, "langage": cle}

        if not self.active:
            resultat["refus"] = "la sandbox est désactivée dans config.json"
            return resultat

        spec = LANGAGES.get(cle)
        if not spec:
            disponibles = ", ".join(LANGAGES)
            resultat["refus"] = f"langage « {langage} » non supporté (disponibles : {disponibles})"
            return resultat

        if shutil.which(spec["outil"]) is None:
            resultat["refus"] = (f"{spec['libelle']} n'est pas installé sur cette machine "
                                 f"(« {spec['outil']} » introuvable dans le PATH)")
            return resultat

        code = (code or "").strip()
        if not code:
            resultat["refus"] = "aucun code fourni"
            return resultat

        raison = self.auditer(code, cle)
        if raison:
            resultat["refus"] = raison
            LOG.warning("Exécution refusée (%s) : %s", cle, raison)
            return resultat

        dossier = tempfile.mkdtemp(prefix="kryptonix_bac_")
        nom_fichier = spec.get("nom_classe_obligatoire", "execution") + spec["extension"]
        chemin = os.path.join(dossier, nom_fichier)
        try:
            if cle == "java" and "class Principal" not in code:
                resultat["refus"] = "le code Java doit définir « class Principal » avec un main"
                return resultat
            contenu = spec.get("preambule", "") + code
            with open(chemin, "w", encoding="utf-8") as fichier:
                fichier.write(contenu + "\n")

            debut = time.time()
            environnement = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"}

            cible = chemin
            if spec.get("compilateur"):
                binaire = os.path.join(dossier, "programme.out" if cle in ("c", "cpp") else "")
                compilation = subprocess.run(
                    spec["compiler"](chemin, binaire), capture_output=True, text=True,
                    timeout=self.timeout, cwd=dossier, env=environnement,
                )
                if compilation.returncode != 0:
                    resultat["erreur"] = (compilation.stderr or "échec de compilation").strip()[:4000]
                    resultat["duree"] = round(time.time() - debut, 2)
                    return resultat
                cible = binaire

            processus = subprocess.run(
                spec["commande"](cible, dossier),
                capture_output=True, text=True, timeout=self.timeout,
                cwd=dossier, env=environnement,
            )
            resultat["duree"] = round(time.time() - debut, 2)
            resultat["sortie"] = (processus.stdout or "").strip()[:8000]
            resultat["erreur"] = (processus.stderr or "").strip()[:4000]
            resultat["succes"] = processus.returncode == 0
        except subprocess.TimeoutExpired:
            resultat["erreur"] = f"exécution interrompue après {self.timeout} s"
        except Exception as erreur:
            resultat["erreur"] = f"échec du lancement : {erreur}"
        finally:
            shutil.rmtree(dossier, ignore_errors=True)

        return resultat

    # ------------------------------------------------------------------
    @staticmethod
    def formater(resultat: dict) -> str:
        """Transforme un résultat brut en phrase lisible / parlable."""
        if resultat["refus"]:
            return f"Exécution refusée : {resultat['refus']}. Je protège la machine avant tout."
        if resultat["succes"]:
            sortie = resultat["sortie"] or "(aucune sortie)"
            return f"Exécuté en {resultat['duree']} s. Résultat : {sortie}"
        erreur = resultat["erreur"].splitlines()[-1] if resultat["erreur"] else "erreur inconnue"
        return f"Le code a échoué. {erreur}"
