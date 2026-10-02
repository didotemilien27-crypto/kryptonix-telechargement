"""
KRYPTONIX / noyau/texte_parle.py
=================================
Prépare un texte pour la lecture à voix haute : tout ce qui se lit à l'écran
mais ne se dit pas (dièses, astérisques, tirets de liste, adresses web, emojis,
blocs de code...) est retiré ou converti en mots naturels.

Le texte affiché dans l'interface n'est jamais modifié : seule la version
envoyée au moteur vocal est nettoyée.
"""

from __future__ import annotations

import re
import unicodedata

# Symboles courants → façon de les dire à voix haute
_SYMBOLES = [
    (r"(\d)\s*%", r"\1 pour cent"),
    (r"(\d)\s*°\s*C\b", r"\1 degrés"),
    (r"(\d)\s*°", r"\1 degrés"),
    (r"(\d)\s*€", r"\1 euros"),
    (r"€\s*(\d+)", r"\1 euros"),
    (r"(\d)\s*\$", r"\1 dollars"),
    (r"\bkm/h\b", "kilomètres par heure"),
    (r"\bm/s\b", "mètres par seconde"),
    (r"(\d)\s*Go\b", r"\1 gigaoctets"),
    (r"(\d)\s*Mo\b", r"\1 mégaoctets"),
    (r"(\d)\s*Ko\b", r"\1 kilooctets"),
    (r"(\d)\s*To\b", r"\1 téraoctets"),
    (r"(\d)\s*GHz\b", r"\1 gigahertz"),
    (r"(\d)\s*MHz\b", r"\1 mégahertz"),
    (r"\bCPU\b", "processeur"),
    (r"\bRAM\b", "mémoire vive"),
    (r"\betc\.", "et cetera"),
    (r"\bex\.\s", "par exemple "),
    (r"\bM\.\s(?=[A-ZÉÈ])", "monsieur "),
    (r"\bMme\b", "madame"),
    (r"\bDr\.?\s(?=[A-ZÉÈ])", "docteur "),
    (r"\b1er\b", "premier"),
    (r"\b1re\b", "première"),
    (r"\s[—–·•]\s", ", "),
    (r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", r"\1 \2 \3"),
    (r"(\d)\s*/\s*(\d)", r"\1 sur \2"),
    (r"&", " et "),
    (r"\s\+\s", " plus "),
    (r"\s=\s", " égale "),
    (r"\s→\s|\s->\s|\s=>\s", ", "),
    (r"\s/\s", " ou "),
]

# Caractères décoratifs à supprimer purement et simplement
_DECORATIFS = re.compile(r"[#*_`~^|<>{}\[\]\\●○■□▪▫◆◇★☆▶►◀◄•·—–…→←↑↓✓✔✗✘⚠]+")


def _sans_emoji(texte: str) -> str:
    resultat = []
    for caractere in texte:
        categorie = unicodedata.category(caractere)
        if categorie in ("So", "Sk", "Cs", "Co", "Cn"):
            continue
        if 0x1F000 <= ord(caractere) <= 0x1FAFF or 0x2600 <= ord(caractere) <= 0x27BF:
            continue
        if caractere in ("\u200d", "\ufe0f"):
            continue
        resultat.append(caractere)
    return "".join(resultat)


_ORDINAUX = {2: "deuxième", 3: "troisième", 4: "quatrième", 5: "cinquième", 6: "sixième",
             7: "septième", 8: "huitième", 9: "neuvième", 10: "dixième"}


def _ordinaux(texte: str) -> str:
    def remplacer(m):
        n = int(m.group(1))
        return _ORDINAUX.get(n, f"{n}ième")
    return re.sub(r"\b(\d+)(?:e|ème|eme)\b", remplacer, texte)


def _decimales(texte: str) -> str:
    """6.9 → 6 virgule 9 (uniquement entre deux chiffres, pas les versions 1.2.3)."""
    return re.sub(r"(?<![\d.])(\d+)\.(\d{1,3})(?![\d.])", r"\1 virgule \2", texte)


def nettoyer_pour_voix(texte: str, limite: int = 1400) -> str:
    """Retourne une version « dite » du texte : sans symboles, sans code, sans liens."""
    if not texte:
        return ""

    # 1. Blocs de code : on ne les lit pas
    texte = re.sub(r"```[\s\S]*?```", " Le code est affiché à l'écran. ", texte)
    texte = re.sub(r"`([^`]*)`", r"\1", texte)

    # 2. Liens markdown [texte](url) → texte, PUIS adresses web et e-mails restantes
    texte = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", texte)
    texte = re.sub(r"https?://\S+|www\.\S+", " lien ", texte)
    texte = re.sub(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b", " adresse e-mail ", texte)

    # 3. Tableaux : lignes de séparation supprimées, barres → pauses
    texte = re.sub(r"(?m)^[\s|:\-]*\|[\s|:\-]*$", "", texte)

    # 4. Structure markdown : titres, citations, puces
    texte = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", texte)
    texte = re.sub(r"(?m)^\s*>+\s?", "", texte)
    texte = re.sub(r"(?m)^\s*[-*+•]\s+", "", texte)
    texte = re.sub(r"(?m)^\s*\d+[.)]\s+", "", texte)
    texte = re.sub(r"(?m)^\s*[-=_*]{3,}\s*$", "", texte)
    texte = re.sub(r"\|", ", ", texte)

    # 5. Symboles utiles convertis en mots (AVANT les emojis : « ° » en fait partie)
    for motif, remplacement in _SYMBOLES:
        texte = re.sub(motif, remplacement, texte)
    texte = _ordinaux(texte)

    # 6. Emojis
    texte = _sans_emoji(texte)

    # 7. Décimales à la française
    texte = _decimales(texte)

    # 8. Symboles décoratifs restants
    texte = _DECORATIFS.sub(" ", texte)
    texte = re.sub(r"(?<=\w)[-_](?=\w)", " ", texte)  # snake_case, tirets internes

    # 9. Ponctuation et espaces : retours à la ligne → pauses naturelles
    texte = re.sub(r"\n{2,}", ". ", texte)
    texte = texte.replace("\n", ", ")
    texte = re.sub(r"\s*([,;:.!?])\s*(?:[,;:.]\s*)+", r"\1 ", texte)
    texte = re.sub(r"\s+([,;:.!?])", r"\1", texte)
    texte = re.sub(r"\s{2,}", " ", texte).strip(" ,;:")

    if len(texte) > limite:
        coupe = texte[:limite]
        fin = max(coupe.rfind(". "), coupe.rfind("! "), coupe.rfind("? "))
        texte = coupe[: fin + 1] if fin > limite // 2 else coupe
    return texte


def decouper_en_phrases(texte: str, max_caracteres: int = 220) -> list[str]:
    """Découpe en petits morceaux aux frontières de phrases, pour une diction
    plus naturelle et une réponse plus rapide (lecture pendant la synthèse)."""
    phrases = re.split(r"(?<=[.!?])\s+", texte.strip())
    morceaux: list[str] = []
    courant = ""
    for phrase in phrases:
        if not phrase:
            continue
        if len(courant) + len(phrase) + 1 <= max_caracteres:
            courant = f"{courant} {phrase}".strip()
        else:
            if courant:
                morceaux.append(courant)
            # phrase trop longue : coupe aux virgules
            while len(phrase) > max_caracteres:
                point = phrase.rfind(",", 0, max_caracteres)
                if point < 40:
                    point = phrase.rfind(" ", 0, max_caracteres)
                if point < 1:
                    point = max_caracteres
                morceaux.append(phrase[: point + 1].strip())
                phrase = phrase[point + 1:].strip()
            courant = phrase
    if courant:
        morceaux.append(courant)
    return morceaux
