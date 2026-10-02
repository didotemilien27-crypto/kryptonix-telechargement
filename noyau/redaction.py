"""
KRYPTONIX / noyau/redaction.py
===============================
Rédaction et création de documents dans différents formats : TXT, Markdown,
HTML, Word (.docx), PDF, CSV et Excel (.xlsx).

Le cerveau local rédige le contenu (structuré en Markdown léger : # titres,
## sous-titres, - puces), et ce module le met en forme dans le format demandé.

Toutes les librairies de mise en forme sont optionnelles : si python-docx,
openpyxl ou fpdf2 manquent, KRYPTONIX bascule sur les formats qu'il peut
produire et le dit clairement, au lieu de planter.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import time

LOG = logging.getLogger("redaction")

# ------------------------------------------------------------------
#  Imports optionnels
# ------------------------------------------------------------------
try:
    import docx
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
except ImportError:  # pragma: no cover
    docx = None

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
except ImportError:  # pragma: no cover
    openpyxl = None

try:
    from fpdf import FPDF
except ImportError:  # pragma: no cover
    FPDF = None

FORMATS_TEXTE = {"txt", "md", "markdown", "html"}
FORMATS_BUREAUTIQUE = {"docx", "word"}
FORMATS_PDF = {"pdf"}
FORMATS_TABLEUR = {"csv", "xlsx", "excel"}

TOUS_FORMATS = FORMATS_TEXTE | FORMATS_BUREAUTIQUE | FORMATS_PDF | FORMATS_TABLEUR

ALIAS_FORMAT = {
    "word": "docx", "microsoft word": "docx", "doc": "docx",
    "excel": "xlsx", "tableur": "xlsx", "feuille de calcul": "xlsx",
    "markdown": "md", "texte": "txt", "texte brut": "txt",
    "page web": "html", "site": "html",
}

EXTENSIONS = {
    "txt": ".txt", "md": ".md", "html": ".html", "docx": ".docx",
    "pdf": ".pdf", "csv": ".csv", "xlsx": ".xlsx",
}


class Redaction:
    """Rédacteur multi-format de KRYPTONIX."""

    def __init__(self, config: dict, cerveau, memoire, dossier: str):
        self.config = config
        self.cerveau = cerveau
        self.memoire = memoire
        self.dossier = dossier
        os.makedirs(self.dossier, exist_ok=True)

    # ------------------------------------------------------------------
    #  Disponibilité
    # ------------------------------------------------------------------
    def formats_disponibles(self) -> list[str]:
        formats = ["txt", "md", "html", "csv"]
        if docx is not None:
            formats.append("docx")
        if openpyxl is not None:
            formats.append("xlsx")
        if FPDF is not None:
            formats.append("pdf")
        return formats

    def resoudre_format(self, mot: str) -> str | None:
        """Normalise un mot utilisateur ('word', 'excel'...) vers un format interne."""
        mot = mot.strip().lower().strip(".,;:!?")
        mot = ALIAS_FORMAT.get(mot, mot)
        return mot if mot in TOUS_FORMATS else None

    # ------------------------------------------------------------------
    #  Génération du contenu par le cerveau local
    # ------------------------------------------------------------------
    def generer_contenu(self, sujet: str, nature: str = "document",
                        contexte_documents: str = "") -> str:
        """
        Demande au cerveau un texte long et structuré en Markdown léger.
        Retourne toujours quelque chose d'exploitable, même si le cerveau
        est indisponible (contenu minimal de secours).
        """
        instruction = (
            f"Rédige, en français, le contenu complet d'un(e) {nature} sur : {sujet}.\n"
            "Structure impérativement le texte en Markdown léger :\n"
            "- une ligne '# Titre' au début ;\n"
            "- des sections avec '## Sous-titre' ;\n"
            "- des paragraphes en texte normal ;\n"
            "- des listes à puces avec '- ' quand c'est pertinent.\n"
            "Sois concret, dense, sans blabla d'introduction du type "
            "'Voici votre document'. Va directement au contenu. "
            "Longueur adaptée au sujet : ni bâclé, ni délayé."
        )
        if contexte_documents:
            instruction += (
                "\n\nAppuie-toi sur ces extraits déjà connus, cite-les si utile :\n"
                + contexte_documents
            )

        if not self.cerveau.disponible and not self.cerveau.verifier_connexion():
            return (
                f"# {sujet.capitalize()}\n\n"
                "Le cerveau local (Ollama) était hors ligne au moment de la rédaction. "
                "Ce document est un squelette vide : relance « ollama serve » et "
                "redemande la rédaction pour obtenir le contenu complet.\n"
            )

        contenu = self.cerveau.reflechir(instruction, registre="technique")
        if not contenu.lstrip().startswith("#"):
            contenu = f"# {sujet.capitalize()}\n\n{contenu}"
        return contenu

    # ------------------------------------------------------------------
    #  Analyse du Markdown léger produit par le cerveau
    # ------------------------------------------------------------------
    @staticmethod
    def _analyser(contenu: str) -> list[dict]:
        """
        Découpe le texte en blocs typés : titre, sous-titre, puce, paragraphe.
        Format simple et prévisible, suffisant pour nos quatre formats de sortie.
        """
        blocs = []
        for ligne in contenu.splitlines():
            ligne = ligne.rstrip()
            if not ligne.strip():
                continue
            if ligne.startswith("# "):
                blocs.append({"type": "titre", "texte": ligne[2:].strip()})
            elif ligne.startswith("## "):
                blocs.append({"type": "sous_titre", "texte": ligne[3:].strip()})
            elif ligne.startswith(("- ", "* ")):
                blocs.append({"type": "puce", "texte": ligne[2:].strip()})
            elif re.match(r"^\d+\.\s", ligne):
                blocs.append({"type": "puce", "texte": re.sub(r"^\d+\.\s", "", ligne).strip()})
            else:
                blocs.append({"type": "paragraphe", "texte": ligne.strip()})
        return blocs

    @staticmethod
    def _nom_fichier(titre: str) -> str:
        """Transforme un titre en nom de fichier sûr, sans extension."""
        nom = re.sub(r"[^\wÀ-ÿ -]", "", titre).strip()
        nom = re.sub(r"\s+", "_", nom)[:60] or "document"
        return f"{nom}_{int(time.time())}"

    # ------------------------------------------------------------------
    #  Point d'entrée : création dans le format demandé
    # ------------------------------------------------------------------
    def creer(self, titre: str, contenu: str, format_cible: str = "txt") -> dict:
        """
        Crée le fichier et l'enregistre en base. Retourne :
        {succes, nom_fichier, chemin, format, id, erreur}
        """
        reponse = {"succes": False, "nom_fichier": "", "chemin": "", "format": format_cible,
                  "id": None, "erreur": ""}

        format_cible = self.resoudre_format(format_cible) or "txt"
        if format_cible == "docx" and docx is None:
            reponse["erreur"] = "python-docx n'est pas installé (pip install python-docx)"
            format_cible = "txt"
        if format_cible == "xlsx" and openpyxl is None:
            reponse["erreur"] = "openpyxl n'est pas installé (pip install openpyxl)"
            format_cible = "csv"
        if format_cible == "pdf" and FPDF is None:
            reponse["erreur"] = "fpdf2 n'est pas installé (pip install fpdf2)"
            format_cible = "md"

        blocs = self._analyser(contenu)
        base = self._nom_fichier(titre)
        extension = EXTENSIONS[format_cible]
        nom_fichier = base + extension
        chemin = os.path.join(self.dossier, nom_fichier)

        try:
            if format_cible == "txt":
                self._creer_txt(chemin, blocs)
            elif format_cible == "md":
                self._creer_md(chemin, contenu)
            elif format_cible == "html":
                self._creer_html(chemin, blocs, titre)
            elif format_cible == "docx":
                self._creer_docx(chemin, blocs)
            elif format_cible == "pdf":
                self._creer_pdf(chemin, blocs)
            elif format_cible == "csv":
                self._creer_csv(chemin, blocs)
            elif format_cible == "xlsx":
                self._creer_xlsx(chemin, blocs, titre)
            else:
                raise ValueError(f"format inconnu : {format_cible}")

            taille = os.path.getsize(chemin)
            doc_id = self.memoire.ajouter_fichier_genere(
                nom=nom_fichier, chemin=chemin, format_fichier=format_cible,
                sujet=titre, taille=taille,
            )
            reponse.update(succes=True, nom_fichier=nom_fichier, chemin=chemin,
                          format=format_cible, id=doc_id, taille=taille)
            if not reponse["erreur"]:
                reponse["erreur"] = ""
            self.memoire.journaliser("redaction", f"{nom_fichier} ({format_cible})")
            LOG.info("Document créé : %s", chemin)
        except Exception as erreur:
            reponse["erreur"] = f"échec de création : {erreur}"
            LOG.error("Échec de création (%s) : %s", format_cible, erreur)

        return reponse

    # ------------------------------------------------------------------
    #  TXT
    # ------------------------------------------------------------------
    def _creer_txt(self, chemin: str, blocs: list[dict]) -> None:
        lignes = []
        for bloc in blocs:
            if bloc["type"] == "titre":
                lignes.append(bloc["texte"].upper())
                lignes.append("=" * len(bloc["texte"]))
            elif bloc["type"] == "sous_titre":
                lignes.append("")
                lignes.append(bloc["texte"])
                lignes.append("-" * len(bloc["texte"]))
            elif bloc["type"] == "puce":
                lignes.append(f"  • {bloc['texte']}")
            else:
                lignes.append(bloc["texte"])
        with open(chemin, "w", encoding="utf-8") as fichier:
            fichier.write("\n".join(lignes) + "\n")

    # ------------------------------------------------------------------
    #  Markdown (sortie brute, déjà dans ce format)
    # ------------------------------------------------------------------
    def _creer_md(self, chemin: str, contenu: str) -> None:
        with open(chemin, "w", encoding="utf-8") as fichier:
            fichier.write(contenu.strip() + "\n")

    # ------------------------------------------------------------------
    #  HTML
    # ------------------------------------------------------------------
    def _creer_html(self, chemin: str, blocs: list[dict], titre: str) -> None:
        corps = []
        en_liste = False
        for bloc in blocs:
            if bloc["type"] == "puce" and not en_liste:
                corps.append("<ul>")
                en_liste = True
            elif bloc["type"] != "puce" and en_liste:
                corps.append("</ul>")
                en_liste = False

            if bloc["type"] == "titre":
                corps.append(f"<h1>{echapper(bloc['texte'])}</h1>")
            elif bloc["type"] == "sous_titre":
                corps.append(f"<h2>{echapper(bloc['texte'])}</h2>")
            elif bloc["type"] == "puce":
                corps.append(f"<li>{echapper(bloc['texte'])}</li>")
            else:
                corps.append(f"<p>{echapper(bloc['texte'])}</p>")
        if en_liste:
            corps.append("</ul>")

        page = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>{echapper(titre)}</title>
<style>
  body{{font-family:Georgia,'Times New Roman',serif;max-width:760px;margin:48px auto;
       padding:0 24px;color:#20242b;line-height:1.65;background:#fdfdfb}}
  h1{{font-size:1.9rem;border-bottom:3px solid #20242b;padding-bottom:10px}}
  h2{{font-size:1.25rem;color:#3a4250;margin-top:34px}}
  p{{margin:14px 0}}
  ul{{margin:10px 0}}
  li{{margin:6px 0}}
  footer{{margin-top:50px;font-size:.8rem;color:#9098a3;border-top:1px solid #e2e5ea;
         padding-top:12px}}
</style>
</head>
<body>
{chr(10).join(corps)}
<footer>Rédigé par KRYPTONIX — assistant local.</footer>
</body>
</html>"""
        with open(chemin, "w", encoding="utf-8") as fichier:
            fichier.write(page)

    # ------------------------------------------------------------------
    #  Word (.docx)
    # ------------------------------------------------------------------
    def _creer_docx(self, chemin: str, blocs: list[dict]) -> None:
        document = docx.Document()
        style_normal = document.styles["Normal"]
        style_normal.font.name = "Calibri"
        style_normal.font.size = Pt(11)

        for bloc in blocs:
            if bloc["type"] == "titre":
                titre = document.add_heading(bloc["texte"], level=0)
                titre.alignment = WD_ALIGN_PARAGRAPH.LEFT
            elif bloc["type"] == "sous_titre":
                document.add_heading(bloc["texte"], level=2)
            elif bloc["type"] == "puce":
                document.add_paragraph(bloc["texte"], style="List Bullet")
            else:
                document.add_paragraph(bloc["texte"])

        pied = document.add_paragraph()
        pied.add_run("Rédigé par KRYPTONIX").italic = True
        document.save(chemin)

    # ------------------------------------------------------------------
    #  PDF
    # ------------------------------------------------------------------
    def _creer_pdf(self, chemin: str, blocs: list[dict]) -> None:
        pdf = FPDF(format="A4")
        pdf.set_auto_page_break(auto=True, margin=18)
        pdf.add_page()
        pdf.set_margins(20, 18, 20)

        for bloc in blocs:
            texte = _translitterer(bloc["texte"])
            if bloc["type"] == "titre":
                pdf.set_font("Helvetica", "B", 20)
                pdf.ln(2)
                pdf.multi_cell(0, 10, texte)
                pdf.ln(2)
            elif bloc["type"] == "sous_titre":
                pdf.set_font("Helvetica", "B", 14)
                pdf.ln(4)
                pdf.multi_cell(0, 8, texte)
            elif bloc["type"] == "puce":
                pdf.set_font("Helvetica", "", 11)
                pdf.multi_cell(0, 6.5, f"  -  {texte}")
            else:
                pdf.set_font("Helvetica", "", 11)
                pdf.multi_cell(0, 6.5, texte)
                pdf.ln(1)

        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(150, 150, 150)
        pdf.ln(6)
        pdf.cell(0, 6, "Redige par KRYPTONIX", align="C")
        pdf.output(chemin)

    # ------------------------------------------------------------------
    #  CSV
    # ------------------------------------------------------------------
    def _creer_csv(self, chemin: str, blocs: list[dict]) -> None:
        """
        Sans tableau explicite fourni, on transforme les puces et paragraphes
        en une colonne unique — reste exploitable dans un tableur.
        """
        with open(chemin, "w", newline="", encoding="utf-8-sig") as fichier:
            ecrivain = csv.writer(fichier, delimiter=";")
            section = "Général"
            for bloc in blocs:
                if bloc["type"] in ("titre", "sous_titre"):
                    section = bloc["texte"]
                    ecrivain.writerow([])
                    ecrivain.writerow([section])
                else:
                    ecrivain.writerow(["", bloc["texte"]])

    def creer_tableau_csv(self, titre: str, en_tetes: list[str],
                          lignes: list[list[str]]) -> dict:
        """Crée un CSV structuré à partir de données tabulaires explicites."""
        base = self._nom_fichier(titre)
        nom_fichier = base + ".csv"
        chemin = os.path.join(self.dossier, nom_fichier)
        with open(chemin, "w", newline="", encoding="utf-8-sig") as fichier:
            ecrivain = csv.writer(fichier, delimiter=";")
            ecrivain.writerow(en_tetes)
            ecrivain.writerows(lignes)
        taille = os.path.getsize(chemin)
        doc_id = self.memoire.ajouter_fichier_genere(
            nom=nom_fichier, chemin=chemin, format_fichier="csv",
            sujet=titre, taille=taille,
        )
        return {"succes": True, "nom_fichier": nom_fichier, "chemin": chemin,
                "format": "csv", "id": doc_id, "taille": taille, "erreur": ""}

    # ------------------------------------------------------------------
    #  Excel (.xlsx)
    # ------------------------------------------------------------------
    def _creer_xlsx(self, chemin: str, blocs: list[dict], titre: str) -> None:
        classeur = openpyxl.Workbook()
        feuille = classeur.active
        feuille.title = titre[:28] or "Document"

        entete = Font(bold=True, size=13, color="FFFFFF")
        fond_entete = PatternFill("solid", fgColor="20242B")
        sous_titre_style = Font(bold=True, size=11, color="20242B")

        ligne = 1
        feuille.column_dimensions["A"].width = 90
        for bloc in blocs:
            cellule = feuille.cell(row=ligne, column=1)
            if bloc["type"] == "titre":
                cellule.value = bloc["texte"]
                cellule.font = entete
                cellule.fill = fond_entete
                cellule.alignment = Alignment(vertical="center")
                feuille.row_dimensions[ligne].height = 26
            elif bloc["type"] == "sous_titre":
                cellule.value = bloc["texte"]
                cellule.font = sous_titre_style
            elif bloc["type"] == "puce":
                cellule.value = f"•  {bloc['texte']}"
            else:
                cellule.value = bloc["texte"]
                cellule.alignment = Alignment(wrap_text=True, vertical="top")
            ligne += 1
        classeur.save(chemin)

    def creer_tableau_xlsx(self, titre: str, en_tetes: list[str],
                           lignes: list[list]) -> dict:
        """Crée un classeur Excel structuré à partir de données tabulaires explicites."""
        if openpyxl is None:
            return self.creer_tableau_csv(titre, en_tetes, lignes)

        classeur = openpyxl.Workbook()
        feuille = classeur.active
        feuille.title = titre[:28] or "Données"

        style_entete = Font(bold=True, color="FFFFFF")
        fond_entete = PatternFill("solid", fgColor="20242B")
        for colonne, valeur in enumerate(en_tetes, start=1):
            cellule = feuille.cell(row=1, column=colonne, value=valeur)
            cellule.font = style_entete
            cellule.fill = fond_entete
            feuille.column_dimensions[get_column_letter(colonne)].width = max(14, len(str(valeur)) + 4)

        for numero_ligne, ligne_donnees in enumerate(lignes, start=2):
            for colonne, valeur in enumerate(ligne_donnees, start=1):
                feuille.cell(row=numero_ligne, column=colonne, value=valeur)

        base = self._nom_fichier(titre)
        nom_fichier = base + ".xlsx"
        chemin = os.path.join(self.dossier, nom_fichier)
        classeur.save(chemin)
        taille = os.path.getsize(chemin)
        doc_id = self.memoire.ajouter_fichier_genere(
            nom=nom_fichier, chemin=chemin, format_fichier="xlsx",
            sujet=titre, taille=taille,
        )
        return {"succes": True, "nom_fichier": nom_fichier, "chemin": chemin,
                "format": "xlsx", "id": doc_id, "taille": taille, "erreur": ""}


# ------------------------------------------------------------------
#  Utilitaires
# ------------------------------------------------------------------
def echapper(texte: str) -> str:
    return (texte.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;").replace('"', "&quot;"))


_TABLE_TRANSLITTERATION = str.maketrans({
    "œ": "oe", "Œ": "OE", "\u2019": "'", "\u2018": "'",
    "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-", "\u2026": "...",
})


def _translitterer(texte: str) -> str:
    """fpdf2 en mode noyau standard (latin-1) : on neutralise les caractères hors zone."""
    texte = texte.translate(_TABLE_TRANSLITTERATION)
    try:
        texte.encode("latin-1")
        return texte
    except UnicodeEncodeError:
        return texte.encode("latin-1", errors="replace").decode("latin-1")
