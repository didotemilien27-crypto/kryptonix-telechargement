"""
KRYPTONIX / noyau/sandbox.py
=============================
Exécution de code dans un bac à sable raisonnablement isolé — plusieurs
langages, un seul garde-fou commun.

Garde-fous appliqués à chaque langage :
  - sous-processus séparé, sans accès à l'état de KRYPTONIX ;
  - environnement réduit au strict nécessaire (aucun mot de passe ni jeton
    hérité de la session), entrée standard fermée ;
  - répertoire de travail temporaire, détruit après exécution ;
  - délai d'exécution strict, processus tué au-delà ;
  - analyse statique préalable : refus des motifs destructeurs adaptés à
    chaque langage (accès disque, réseau, appels système, fork-bomb).

Langages exécutés : Python, JavaScript (Node.js), Bash, C, C++, Java, Perl.
Un langage dont l'interpréteur/compilateur n'est pas installé sur la machine
est refusé avec un message clair plutôt qu'une erreur brute.

Langages d'aperçu : HTML et CSS. Ils ne s'« exécutent » pas : la sandbox
fabrique un document complet que l'interface affiche dans un cadre isolé
(scripts autorisés, mais aucun réseau, aucun accès à KRYPTONIX).

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
    "htm": "html", "xhtml": "html",
    "style": "css", "stylesheet": "css",
}

PREAMBULE_PYTHON = (
    "import builtins, math, random, statistics, itertools, functools, json, re, datetime\n"
)
PREAMBULE_SYMPY = (
    "try:\n"
    "    import sympy\n"
    "except ImportError:\n"
    "    sympy = None\n"
)

# Variables d'environnement transmises au programme exécuté. Sous Windows, Python
# refuse même de démarrer sans SYSTEMROOT : un environnement trop vide est une
# cause classique d'échec, d'où cette liste blanche plutôt qu'un dictionnaire minimal.
VARIABLES_UTILES = (
    "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
    "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "OS", "USERPROFILE",
    "HOME", "LANG", "LC_ALL", "JAVA_HOME",
)

# Contenu de démonstration sur lequel un CSS est appliqué dans l'aperçu.
PAGE_DEMO_CSS = """
<main id="principal">
  <h1>Titre principal</h1>
  <h2>Sous-titre</h2>
  <p>Un paragraphe avec <a href="#">un lien</a>, du <strong>gras</strong>
     et de l'<em>italique</em>.</p>
  <ul><li>Premier élément</li><li>Deuxième élément</li><li>Troisième élément</li></ul>
  <p><button>Un bouton</button> <input placeholder="Un champ de saisie"></p>
  <div class="carte"><h3>.carte</h3><p>Une boîte de contenu.</p></div>
  <div class="boite">.boite</div>
  <table><tr><th>Nom</th><th>Valeur</th></tr><tr><td>Alpha</td><td>1</td></tr>
         <tr><td>Bêta</td><td>2</td></tr></table>
</main>
"""

# Règles de sécurité du document d'aperçu : scripts et styles internes uniquement,
# images/polices en data: seulement, aucune requête réseau, aucun envoi de formulaire.
POLITIQUE_APERCU = ("default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; "
                    "script-src 'unsafe-inline'; font-src data:; media-src data: blob:; "
                    "form-action 'none'; base-uri 'none'")
BALISE_POLITIQUE = f'<meta http-equiv="Content-Security-Policy" content="{POLITIQUE_APERCU}">'


def _motifs_communs() -> list[tuple[str, str]]:
    return [(r"curl\s|wget\s", "accès réseau interdit")]


# Chaque entrée : extension, commande(s) à exécuter, motifs interdits,
# et l'outil dont la présence est vérifiée avant de se lancer (None = rien à installer).
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
            (r"\b(?:import|from)\s+(?:os|subprocess|shutil|socket|ctypes|winreg|multiprocessing|"
             r"pathlib|tempfile|importlib|signal|pty|glob|webbrowser|smtplib|ftplib|telnetlib|"
             r"http|urllib|requests)\b", "import système ou réseau interdit"),
            (r"\b__import__\s*\(", "import dynamique interdit"),
            (r"\b(eval|exec|compile)\s*\(", "évaluation dynamique interdite"),
            (r"\bopen\s*\([^)]*(?:,\s*|mode\s*=\s*)['\"][^'\"]*[wax+]", "écriture de fichier interdite"),
            (r"\b(rmtree|unlink|rmdir|popen)\s*\(", "appel destructeur interdit"),
            (r"\bwhile\s+(?:True|1)\s*:(?![\s\S]{0,400}\bbreak\b)", "boucle infinie sans sortie"),
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
    # --- langages d'aperçu : rien à installer, rien à exécuter côté serveur ---
    "html": {
        "libelle": "HTML (aperçu)", "extension": ".html", "outil": None, "apercu": "html",
        "motifs": [
            (r"<\s*(iframe|frame|frameset|object|embed|base)\b", "balise interdite dans l'aperçu"),
            (r"<\s*meta[^>]+http-equiv\s*=\s*['\"]?\s*refresh", "redirection interdite"),
            (r"\b(fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon)\b", "accès réseau interdit"),
            (r"\bsrc\s*=\s*['\"]?\s*(?:https?:)?//", "ressource externe interdite (utilise des images en data:)"),
            (r"<\s*link[^>]+href\s*=\s*['\"]?\s*(?:https?:)?//", "feuille de style externe interdite"),
            (r"@import\s+(?:url\()?\s*['\"]?\s*(?:https?:)?//", "import externe interdit"),
            (r"\bwindow\.(?:top|parent)\b|\bdocument\.cookie\b", "accès à la page hôte interdit"),
        ],
    },
    "css": {
        "libelle": "CSS (aperçu)", "extension": ".css", "outil": None, "apercu": "css",
        "motifs": [
            (r"@import\b", "@import interdit (aperçu hors ligne)"),
            (r"url\(\s*['\"]?\s*(?:https?:)?//", "ressource externe interdite (utilise des images en data:)"),
            (r"</\s*style", "balise </style> interdite dans une feuille de style"),
        ],
    },
}


def _environnement(dossier: str) -> dict:
    """Environnement du programme exécuté : liste blanche + dossier temporaire isolé."""
    env = {cle: os.environ[cle] for cle in VARIABLES_UTILES if cle in os.environ}
    env.update({
        "TEMP": dossier, "TMP": dossier, "TMPDIR": dossier,
        "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


def _adoucir_erreur(texte: str, decalage: int) -> str:
    """Rend un message d'erreur lisible : plus de chemin temporaire, numéros de ligne exacts."""
    def ligne(m):
        return f"Ligne {max(1, int(m.group(1)) - decalage)}"
    texte = re.sub(r'File "[^"]*execution\.py", line (\d+)', ligne, texte)
    texte = re.sub(r"[A-Za-z]:\\[^\s\"']*kryptonix_bac_[^\\\s\"']*\\", "", texte)
    texte = re.sub(r"/[^\s\"']*kryptonix_bac_[^/\s\"']*/", "", texte)
    return texte


def construire_apercu(code: str, genre: str) -> str:
    """Fabrique le document HTML complet affiché dans le cadre isolé de l'interface."""
    if genre == "css":
        return ("<!DOCTYPE html><html lang=\"fr\"><head><meta charset=\"utf-8\">"
                f"{BALISE_POLITIQUE}<style>{code}</style></head><body>{PAGE_DEMO_CSS}</body></html>")
    # HTML : si l'utilisateur fournit déjà un document complet, on y glisse seulement la politique.
    if re.search(r"<\s*html\b", code, flags=re.IGNORECASE):
        if re.search(r"<\s*head\b[^>]*>", code, flags=re.IGNORECASE):
            return re.sub(r"(<\s*head\b[^>]*>)", r"\1" + BALISE_POLITIQUE, code, count=1,
                          flags=re.IGNORECASE)
        return re.sub(r"(<\s*html\b[^>]*>)", r"\1<head>" + BALISE_POLITIQUE + "</head>", code,
                      count=1, flags=re.IGNORECASE)
    return ("<!DOCTYPE html><html lang=\"fr\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"{BALISE_POLITIQUE}</head><body>{code}</body></html>")


class Sandbox:
    """Exécuteur de code multi-langage à usage unique."""

    def __init__(self, config: dict):
        self.config = config   # même dictionnaire que le noyau : les réglages sont relus à chaud

    @property
    def timeout(self) -> int:
        try:
            return max(2, int(self.config.get("sandbox_timeout", 12)))
        except (TypeError, ValueError):
            return 12

    @property
    def active(self) -> bool:
        return bool(self.config.get("sandbox_active", True))

    # ------------------------------------------------------------------
    @staticmethod
    def langages_disponibles() -> list[dict]:
        """Liste les langages pris en charge, avec leur disponibilité réelle sur la machine."""
        resultat = []
        for cle, spec in LANGAGES.items():
            outil = spec["outil"]
            resultat.append({
                "id": cle, "libelle": spec["libelle"],
                "apercu": bool(spec.get("apercu")),
                "disponible": outil is None or shutil.which(outil) is not None,
            })
        return resultat

    def _resoudre_langage(self, langage: str) -> str:
        langage = (langage or "python").strip().lower()
        return ALIAS_LANGAGE.get(langage, langage)

    def auditer(self, code: str, langage: str = "python") -> str | None:
        """Retourne la raison du refus, ou None si le code est acceptable."""
        cle = self._resoudre_langage(langage)
        spec = LANGAGES.get(cle)
        if not spec:
            return "langage non supporté"
        motifs = list(spec["motifs"])
        if not spec.get("apercu"):
            motifs += _motifs_communs()
        for motif, raison in motifs:
            if re.search(motif, code, flags=re.IGNORECASE):
                return raison
        if len(code) > 20_000:
            return "code trop volumineux"
        return None

    def executer(self, code: str, langage: str = "python") -> dict:
        """
        Exécute le code et retourne :
        {succes, sortie, erreur, refus, duree, langage}
        (+ « apercu » : document HTML complet, pour les langages HTML et CSS)
        """
        cle = self._resoudre_langage(langage)
        resultat = {"succes": False, "sortie": "", "erreur": "", "refus": None,
                    "duree": 0.0, "langage": cle}

        if not self.active:
            resultat["refus"] = "la sandbox est désactivée (RÉGLAGES → Bac à sable)"
            return resultat

        spec = LANGAGES.get(cle)
        if not spec:
            disponibles = ", ".join(LANGAGES)
            resultat["refus"] = f"langage « {langage} » non supporté (disponibles : {disponibles})"
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

        # --- langages d'aperçu : pas de processus, un document à afficher ---
        if spec.get("apercu"):
            resultat["apercu"] = construire_apercu(code, spec["apercu"])
            resultat["sortie"] = "Aperçu généré."
            resultat["succes"] = True
            return resultat

        if shutil.which(spec["outil"]) is None:
            resultat["refus"] = (f"{spec['libelle']} n'est pas installé sur cette machine "
                                 f"(« {spec['outil']} » introuvable dans le PATH)")
            return resultat

        dossier = tempfile.mkdtemp(prefix="kryptonix_bac_")
        nom_fichier = spec.get("nom_classe_obligatoire", "execution") + spec["extension"]
        chemin = os.path.join(dossier, nom_fichier)
        decalage = 0
        try:
            if cle == "java" and "class Principal" not in code:
                resultat["refus"] = "le code Java doit définir « class Principal » avec un main"
                return resultat

            preambule = spec.get("preambule", "")
            if cle == "python" and "sympy" in code:
                preambule += PREAMBULE_SYMPY      # sympy est lourd : chargé seulement si utilisé
            decalage = preambule.count("\n")
            with open(chemin, "w", encoding="utf-8") as fichier:
                fichier.write(preambule + code + "\n")

            options = dict(
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=dossier, env=_environnement(dossier),
                stdin=subprocess.DEVNULL,   # sans cela, une appli sans console plante sous Windows
            )
            if os.name == "nt":
                options["creationflags"] = 0x08000000   # CREATE_NO_WINDOW : aucune fenêtre noire

            debut = time.time()
            cible = chemin
            if spec.get("compilateur"):
                binaire = os.path.join(dossier, "programme.out" if cle in ("c", "cpp") else "")
                compilation = subprocess.run(spec["compiler"](chemin, binaire),
                                             timeout=self.timeout, **options)
                if compilation.returncode != 0:
                    resultat["erreur"] = (compilation.stderr or "échec de compilation").strip()[:4000]
                    resultat["duree"] = round(time.time() - debut, 2)
                    return resultat
                cible = binaire

            processus = subprocess.run(spec["commande"](cible, dossier),
                                       timeout=self.timeout, **options)
            resultat["duree"] = round(time.time() - debut, 2)
            resultat["sortie"] = (processus.stdout or "").strip()[:8000]
            erreur = _adoucir_erreur((processus.stderr or "").strip(), decalage)[:4000]
            resultat["succes"] = processus.returncode == 0
            if not resultat["succes"] and not erreur:
                erreur = (f"Le programme s'est arrêté avec le code {processus.returncode} "
                          "sans message. Vérifie la syntaxe du code.")
                LOG.warning("Sandbox %s : code de sortie %s sans message d'erreur.",
                            cle, processus.returncode)
            resultat["erreur"] = erreur
        except subprocess.TimeoutExpired:
            resultat["erreur"] = f"exécution interrompue après {self.timeout} s"
        except Exception as erreur:
            LOG.exception("Sandbox : échec du lancement")
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
        if resultat.get("apercu"):
            return "Aperçu prêt : ouvre l'onglet SANDBOX pour le voir (choisis HTML ou CSS)."
        if resultat["succes"]:
            sortie = resultat["sortie"] or "(aucune sortie)"
            return f"Exécuté en {resultat['duree']} s. Résultat : {sortie}"
        erreur = resultat["erreur"].splitlines()[-1] if resultat["erreur"] else "erreur inconnue"
        return f"Le code a échoué. {erreur}"
