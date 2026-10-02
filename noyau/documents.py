"""
KRYPTONIX / noyau/documents.py
===============================
Ingestion documentaire : aspiration de fichiers ou de dossiers entiers
(PDF, TXT, LOG, CSV, JSON, MD, PY, HTML, SMS exportés), extraction du texte,
résumé par le cerveau local, stockage permanent dans SQLite, puis restitution
comme contexte lors des conversations.
"""

from __future__ import annotations

import json
import logging
import os
import re

LOG = logging.getLogger("documents")

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None

EXTENSIONS_TEXTE = {
    ".txt", ".log", ".md", ".csv", ".json", ".xml", ".html", ".htm",
    ".py", ".js", ".bat", ".sh", ".ini", ".cfg", ".yaml", ".yml", ".srt",
}
EXTENSIONS_SUPPORTEES = EXTENSIONS_TEXTE | {".pdf"}
TAILLE_MAX_OCTETS = 25 * 1024 * 1024  # 25 Mo


class Documents:
    """Aspirateur, analyste et bibliothécaire de KRYPTONIX."""

    def __init__(self, config: dict, memoire, cerveau):
        self.config = config
        self.memoire = memoire
        self.cerveau = cerveau

    # ------------------------------------------------------------------
    #  Extraction
    # ------------------------------------------------------------------
    def extraire_texte(self, chemin: str) -> tuple[str, str]:
        """Retourne (texte, type_detecte). Lève ValueError si le format est refusé."""
        extension = os.path.splitext(chemin)[1].lower()

        if extension not in EXTENSIONS_SUPPORTEES:
            raise ValueError(f"format non pris en charge : {extension or 'inconnu'}")
        if os.path.getsize(chemin) > TAILLE_MAX_OCTETS:
            raise ValueError("fichier trop volumineux (> 25 Mo)")

        if extension == ".pdf":
            if PdfReader is None:
                raise ValueError("pypdf n'est pas installé (pip install pypdf)")
            lecteur = PdfReader(chemin)
            pages = []
            for page in lecteur.pages:
                try:
                    pages.append(page.extract_text() or "")
                except Exception:
                    continue
            texte = "\n".join(pages)
            if not texte.strip():
                raise ValueError("PDF sans couche texte (document scanné, OCR nécessaire)")
            return nettoyer_texte(texte), "pdf"

        if extension == ".json":
            with open(chemin, "r", encoding="utf-8", errors="ignore") as fichier:
                brut = fichier.read()
            try:
                texte = json.dumps(json.loads(brut), indent=2, ensure_ascii=False)
            except ValueError:
                texte = brut
            return nettoyer_texte(texte), "json"

        with open(chemin, "r", encoding="utf-8", errors="ignore") as fichier:
            return nettoyer_texte(fichier.read()), extension.lstrip(".")

    # ------------------------------------------------------------------
    #  Ingestion
    # ------------------------------------------------------------------
    def ingerer_fichier(self, chemin: str, resumer: bool = True) -> dict:
        """Ingère un fichier unique. Retourne {succes, nom, resume, id, erreur}."""
        reponse = {"succes": False, "nom": os.path.basename(chemin),
                   "resume": "", "id": None, "erreur": ""}
        try:
            if not os.path.isfile(chemin):
                raise ValueError("fichier introuvable")

            texte, type_doc = self.extraire_texte(chemin)
            if len(texte.strip()) < 15:
                raise ValueError("contenu vide ou illisible")

            resume = ""
            if resumer and self.cerveau is not None:
                resume = self.cerveau.resumer(texte, reponse["nom"])

            doc_id = self.memoire.ajouter_document(
                nom=reponse["nom"], chemin=os.path.abspath(chemin),
                type_doc=type_doc, contenu=texte, resume=resume,
            )
            self.memoire.journaliser("ingestion", f"{reponse['nom']} ({len(texte)} caractères)")

            reponse.update(succes=True, resume=resume, id=doc_id, caracteres=len(texte))
            LOG.info("Document ingéré : %s (%d caractères).", reponse["nom"], len(texte))
        except Exception as erreur:
            reponse["erreur"] = str(erreur)
            LOG.warning("Ingestion échouée pour %s : %s", chemin, erreur)
        return reponse

    def aspirer_dossier(self, dossier: str, recursif: bool = True,
                        resumer: bool = False, limite: int = 200) -> dict:
        """
        Aspire tous les fichiers exploitables d'un dossier (typiquement l'ancien
        projet Ultron : logs, notes, PDF, scripts). Le résumé IA est désactivé par
        défaut pour ne pas saturer le modèle sur un gros volume.
        """
        bilan = {"analyses": 0, "ignores": 0, "echecs": 0, "fichiers": []}
        if not os.path.isdir(dossier):
            bilan["erreur"] = "dossier introuvable"
            return bilan

        for racine, _sous_dossiers, fichiers in os.walk(dossier):
            for nom in sorted(fichiers):
                if bilan["analyses"] >= limite:
                    return bilan
                chemin = os.path.join(racine, nom)
                if os.path.splitext(nom)[1].lower() not in EXTENSIONS_SUPPORTEES:
                    bilan["ignores"] += 1
                    continue
                if self.memoire.document_existe(os.path.abspath(chemin)):
                    bilan["ignores"] += 1
                    continue
                resultat = self.ingerer_fichier(chemin, resumer=resumer)
                if resultat["succes"]:
                    bilan["analyses"] += 1
                    bilan["fichiers"].append(resultat["nom"])
                else:
                    bilan["echecs"] += 1
            if not recursif:
                break
        return bilan

    # ------------------------------------------------------------------
    #  Restitution : contexte injecté dans le cerveau
    # ------------------------------------------------------------------
    def contexte_pertinent(self, question: str, budget_caracteres: int = 3500) -> str:
        """
        Récupère les passages des documents ingérés qui parlent de la question.
        Recherche par mots-clés + fenêtrage autour des occurrences : simple,
        prévisible, et suffisant pour une base locale.
        """
        mots = [mot for mot in re.findall(r"[\wÀ-ÿ'-]{4,}", question.lower())
                if mot not in MOTS_VIDES]
        if not mots:
            return ""

        extraits: list[str] = []
        vus: set[int] = set()
        for mot in mots[:5]:
            for document in self.memoire.rechercher_documents(mot, limite=3):
                if document["id"] in vus:
                    continue
                vus.add(document["id"])
                passage = fenetrer(document["contenu"], mot, 700)
                if passage:
                    extraits.append(f"[{document['nom']}] {passage}")
                elif document["resume"]:
                    extraits.append(f"[{document['nom']}] {document['resume']}")

        contexte = "\n---\n".join(extraits)
        return contexte[:budget_caracteres]

    def resumer_document(self, doc_id: int) -> str:
        document = self.memoire.document(doc_id)
        if not document:
            return "Ce document n'est pas dans ma mémoire."
        if document["resume"]:
            return document["resume"]
        resume = self.cerveau.resumer(document["contenu"], document["nom"])
        if not resume:
            return ("Aucun compte rendu disponible : le cerveau local est hors ligne. "
                    "Lance « ollama serve », puis redemande.")
        self.memoire.maj_resume_document(doc_id, resume)
        return resume

    def repondre_sur_document(self, doc_id: int, question: str) -> str:
        """
        Étude ciblée d'un document précis : la question est posée avec l'intégralité
        (ou un large extrait) du document comme contexte, plutôt qu'avec de simples
        fenêtres par mot-clé. Utile pour « résume-moi le chapitre 2 », « que dit ce
        contrat sur le préavis », etc.
        """
        document = self.memoire.document(doc_id)
        if not document:
            return "Ce document n'est pas dans ma mémoire."
        if not question.strip():
            return "Quelle est ta question sur ce document ?"
        if self.cerveau is None or not (self.cerveau.disponible or self.cerveau.verifier_connexion()):
            return ("Mon cerveau local est hors ligne : lance « ollama serve » puis "
                    "repose la question sur ce document.")

        extrait = document["contenu"][:14000]
        tronque = " (extrait — le document est plus long)" if len(document["contenu"]) > 14000 else ""
        instruction = (
            f"Voici le contenu de « {document['nom']} »{tronque} :\n\n{extrait}\n\n"
            f"Question précise sur ce document : {question}\n"
            "Réponds uniquement à partir de ce contenu ; si la réponse n'y figure pas, "
            "dis-le clairement plutôt que d'inventer."
        )
        return self.cerveau.reflechir(instruction, registre="technique")

    def inventaire(self) -> str:
        documents = self.memoire.documents(limite=15)
        if not documents:
            return "Ma bibliothèque est vide. Donne-moi des fichiers à aspirer."
        noms = ", ".join(document["nom"] for document in documents)
        total = self.memoire.statistiques()["documents"]
        return f"J'ai {total} document(s) en mémoire. Les plus récents : {noms}."


# ------------------------------------------------------------------
#  Utilitaires
# ------------------------------------------------------------------
MOTS_VIDES = {
    "dans", "pour", "avec", "cette", "comme", "mais", "donc", "quoi", "quel",
    "quelle", "peux", "peut", "dire", "dis-moi", "moi", "fais", "tout", "tous",
    "plus", "sont", "était", "etait", "leur", "elle", "vous", "nous", "document",
    "fichier", "parle", "parles", "souviens", "rappelle",
}


def nettoyer_texte(texte: str) -> str:
    texte = texte.replace("\x00", " ")
    texte = re.sub(r"[ \t]+", " ", texte)
    texte = re.sub(r"\n{3,}", "\n\n", texte)
    return texte.strip()


def fenetrer(contenu: str, mot: str, largeur: int = 700) -> str:
    """Extrait une fenêtre de texte centrée sur la première occurrence du mot."""
    position = contenu.lower().find(mot.lower())
    if position == -1:
        return ""
    debut = max(0, position - largeur // 2)
    fin = min(len(contenu), position + largeur // 2)
    prefixe = "..." if debut > 0 else ""
    suffixe = "..." if fin < len(contenu) else ""
    return f"{prefixe}{contenu[debut:fin].strip()}{suffixe}"
