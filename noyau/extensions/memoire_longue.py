"""
KRYPTONIX / noyau/extensions/memoire_longue.py
===============================================
Mémoire à long terme structurée : PRÉFÉRENCES, PROCHES, PROJETS.
Elle s'appuie sur le profil existant (donc le cerveau en profite déjà dans ses réponses) en
classant les faits par préfixe : `proche:Léa`, `projet:Kryptonix`, `preference:musique`.

Phrases comprises : « retiens que ma sœur s'appelle Léa », « mon projet Atlas avance bien »,
« j'aime le café sans sucre », « qui est Léa ? », « mes projets », « mes proches », « oublie Léa ».
"""

from __future__ import annotations

import re

from .base import Module, Outil, liste_parlee, normaliser

LIENS = r"(?:soeur|sœur|frere|frère|mere|mère|pere|père|maman|papa|fils|fille|femme|mari|epouse|épouse|copine|copain|" \
        r"conjoint|conjointe|ami|amie|meilleur ami|meilleure amie|collegue|collègue|patron|cousin|cousine|oncle|tante|" \
        r"grand-mere|grand-mère|grand-pere|grand-père|voisin|voisine|chat|chien)"


class MemoireLongue(Module):
    nom = "memoire_longue"
    description = "Préférences, proches et projets mémorisés"
    exemples = ("retiens que ma sœur s'appelle Léa", "j'aime le café sans sucre", "mon projet Atlas est en tournage",
                "qui est Léa ?", "mes projets")

    def _profil(self) -> dict:
        return self.kx.memoire.tout_le_profil()

    SEP = "||"

    def _par_prefixe(self, prefixe: str) -> dict[str, str]:
        """Le profil met les clés en minuscules : le nom d'origine (avec ses majuscules) est gardé dans la valeur."""
        sortie = {}
        for k, v in self._profil().items():
            if k.startswith(prefixe):
                nom, sep, valeur = str(v).partition(self.SEP)
                sortie[nom if sep else k[len(prefixe):]] = valeur if sep else str(v)
        return sortie

    def proches(self) -> dict[str, str]:
        return self._par_prefixe("proche:")

    def projets(self) -> dict[str, str]:
        return self._par_prefixe("projet:")

    def preferences(self) -> dict[str, str]:
        return self._par_prefixe("preference:")

    def retenir_proche(self, nom: str, relation: str) -> None:
        nom = nom.strip()
        self.kx.memoire.definir_profil(f"proche:{nom}", f"{nom[:1].upper() + nom[1:]}{self.SEP}{relation.strip()}")

    def retenir_projet(self, nom: str, etat: str) -> None:
        nom = nom.strip()
        self.kx.memoire.definir_profil(f"projet:{nom}", f"{nom}{self.SEP}{etat.strip() or 'en cours'}")

    def retenir_preference(self, sujet: str, valeur: str) -> None:
        self.kx.memoire.definir_profil(f"preference:{sujet.strip()}", f"{sujet.strip()}{self.SEP}{valeur.strip()}")

    def intention(self, texte: str, commande: str) -> str | None:
        c = texte.strip()
        n = normaliser(texte)

        # proche : « ma sœur s'appelle Léa » / « Léa est ma sœur »
        m = re.search(rf"\b(?:retiens(?: que)?|note(?: que)?|souviens[- ]toi que)?\s*(?:mon|ma|mes)\s+({LIENS})\s+(?:s'appelle|se nomme|c'est|est)\s+([A-ZÀ-Ý][\wÀ-ÿ'-]+)",
                      c, flags=re.IGNORECASE)
        if m and re.search(r"\b(retiens|note|souviens)", n):
            self.retenir_proche(m.group(2), m.group(1).lower())
            return f"Gravé : {m.group(2).capitalize()} est ton/ta {m.group(1).lower()}."
        m = re.search(rf"\b(?:retiens(?: que)?|note(?: que)?)\s+([A-ZÀ-Ý][\wÀ-ÿ'-]+)\s+est\s+(?:mon|ma|mes)\s+({LIENS})", c)
        if m:
            self.retenir_proche(m.group(1), m.group(2).lower())
            return f"Gravé : {m.group(1)} est ton/ta {m.group(2).lower()}."

        # projet : « mon projet Atlas est en montage »
        m = re.search(r"\b(?:retiens(?: que)?|note(?: que)?)?\s*(?:mon|notre)\s+projet\s+([\wÀ-ÿ' -]+?)\s+(?:est|en est|avance|c'est)\s+(.+)", c, flags=re.IGNORECASE)
        if m and (re.search(r"\b(retiens|note)", n) or "projet" in n):
            self.retenir_projet(m.group(1), m.group(2).strip(" ."))
            return f"Projet « {m.group(1).strip()} » mis à jour : {m.group(2).strip(' .')}."
        m = re.search(r"\b(?:nouveau projet|je lance un projet|ajoute (?:un )?projet)\s*:?\s*(.+)", c, flags=re.IGNORECASE)
        if m:
            self.retenir_projet(m.group(1).strip(" ."), "démarré")
            return f"Projet « {m.group(1).strip(' .')} » ajouté."

        # préférence : « j'aime X » / « je préfère X » / « retiens que je déteste X »
        m = re.search(r"\b(?:retiens que )?(?:j'aime(?: bien)?|j'adore|je prefere|je préfère|je deteste|je déteste|je n'aime pas)\s+(.+)", c, flags=re.IGNORECASE)
        if m and (re.search(r"\b(retiens|note)", n) or re.match(r"^\s*(j'|je )", c, flags=re.IGNORECASE)) and len(m.group(1).split()) <= 8:
            verbe = re.search(r"(j'aime(?: bien)?|j'adore|je pr[eé]f[eè]re|je d[eé]teste|je n'aime pas)", c, flags=re.IGNORECASE).group(1).lower()
            valeur = m.group(1).strip(" .")
            self.retenir_preference(valeur[:40], verbe)
            return f"Noté : {verbe} {valeur}."

        # consultation
        if re.search(r"\b(mes proches|ma famille|qui sont mes proches)\b", n):
            p = self.proches()
            return ("Tes proches : " + liste_parlee([f"{k} ({v})" for k, v in p.items()]) + ".") if p else \
                   "Je ne connais encore aucun de tes proches. Dis « retiens que ma sœur s'appelle Léa »."
        if re.search(r"\b(mes projets|quels projets|ou en sont mes projets)\b", n):
            p = self.projets()
            return ("Tes projets : " + " ; ".join(f"{k} — {v}" for k, v in p.items()) + ".") if p else \
                   "Aucun projet enregistré. Dis « nouveau projet : ... »."
        if re.search(r"\b(mes preferences|ce que j'aime)\b", n):
            p = self.preferences()
            return ("Tes goûts : " + " ; ".join(f"{v} {k}" for k, v in p.items()) + ".") if p else \
                   "Je ne connais pas encore tes préférences."
        m = re.search(r"\bqui est ([A-ZÀ-Ý][\wÀ-ÿ'-]+)", c)
        if m:
            rel = next((v for k, v in self.proches().items() if k.lower() == m.group(1).lower()), None)
            if rel:
                return f"{m.group(1).capitalize()} est ton/ta {rel}."
        m = re.search(r"\boublie (?:le projet |le proche |ma |mon )?([\wÀ-ÿ' -]+)$", c, flags=re.IGNORECASE)
        if m and not re.search(r"\b(tout|conversation)", n):
            cible = m.group(1).strip().lower()
            retires = 0
            for cle in list(self._profil()):
                if cle.split(":", 1)[-1].lower() == cible and ":" in cle:
                    self.kx.memoire.oublier_profil(cle)
                    retires += 1
            if retires:
                return f"J'ai oublié « {cible} »."
        return None

    def statut(self) -> dict:
        return {"proches": len(self.proches()), "projets": len(self.projets()), "preferences": len(self.preferences())}

    def outils(self) -> list[Outil]:
        return [
            Outil("memoire_retenir_proche", "Mémoriser un proche",
                  lambda a: (self.retenir_proche(a["nom"], a.get("relation", "proche")) or "Mémorisé."),
                  {"nom": {"type": "string"}, "relation": {"type": "string"}}, ("nom",), 2, False, "memoire"),
            Outil("memoire_projets", "Lister les projets",
                  lambda a: " ; ".join(f"{k} — {v}" for k, v in self.projets().items()) or "Aucun projet.",
                  {}, (), 1, True, "memoire"),
        ]
