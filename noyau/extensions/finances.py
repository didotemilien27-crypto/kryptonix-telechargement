"""
KRYPTONIX / noyau/extensions/finances.py
=========================================
Cockpit financier LOCAL : abonnements, échéances, transactions.

  • Application web locale : http://127.0.0.1:<port>/finances (jamais exposée hors de ta machine).
  • Import de relevés CSV bancaires (colonnes détectées automatiquement ; « ; » ou « , »).
  • Détection automatique des abonnements : par récurrence dans les transactions (même libellé,
    montant proche, ~30 jours d'écart) et par reçus Gmail (si la boîte mail est configurée).
  • Prochaines échéances, total mensuel, abonnements oubliés.

CONFIDENTIALITÉ STRICTE :
  - base de données séparée (`donnees/finances.db`), jamais lue par le cerveau ni par un agent ;
  - AUCUN outil de ce module n'est exposé au serveur MCP ni à l'agent délégué ;
  - les réponses vocales sont calculées sans IA ; après une réponse financière, les prochains
    échanges restent traités en local (rien de ce contexte n'est envoyé au cloud) ;
  - les routes web refusent toute adresse autre que ta machine.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import logging
import os
import re
import time

from ..config import DOSSIER_DONNEES
from .base import ErreurModule, Module, Outil, liste_parlee, normaliser
from .stockage import Stockage

LOG = logging.getLogger("finances")

SCHEMA = """
CREATE TABLE IF NOT EXISTS abonnements (
    id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT NOT NULL, montant REAL NOT NULL,
    periodicite TEXT NOT NULL DEFAULT 'mensuel', prochaine TEXT, categorie TEXT DEFAULT '',
    actif INTEGER DEFAULT 1, source TEXT DEFAULT 'manuel', cree REAL
);
CREATE TABLE IF NOT EXISTS echeances (
    id INTEGER PRIMARY KEY AUTOINCREMENT, libelle TEXT NOT NULL, montant REAL DEFAULT 0,
    date TEXT NOT NULL, payee INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, libelle TEXT NOT NULL, montant REAL NOT NULL,
    cle TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS candidats (
    id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT NOT NULL, montant REAL, periodicite TEXT, source TEXT,
    occurrences INTEGER DEFAULT 1, statut TEXT DEFAULT 'a_confirmer', UNIQUE(nom, source)
);
"""
FACTEUR_MENSUEL = {"hebdo": 52 / 12, "mensuel": 1.0, "trimestriel": 1 / 3, "annuel": 1 / 12}


def lire_montant(s: str) -> float | None:
    s = (s or "").strip().replace("\u00a0", "").replace(" ", "").replace("€", "").replace("EUR", "")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def lire_date(s: str) -> str | None:
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s[:10] if "%Y-" in fmt else s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def libelle_canonique(l: str) -> str:
    l = normaliser(l)
    l = re.sub(r"\b(carte|cb|prlv|prelevement|paiement|vir|virement|sepa|facture|achat|x\d{2,}|\d{2}/\d{2}(?:/\d{2,4})?)\b", " ", l)
    l = re.sub(r"[\d*#]+", " ", l)
    return re.sub(r"\s+", " ", l).strip()[:40]


def prochaine_date(derniere: dt.date, periodicite: str) -> dt.date:
    if periodicite == "hebdo":
        return derniere + dt.timedelta(days=7)
    mois = {"mensuel": 1, "trimestriel": 3, "annuel": 12}.get(periodicite, 1)
    m = derniere.month - 1 + mois
    an, mo = derniere.year + m // 12, m % 12 + 1
    jour = min(derniere.day, 28 if mo == 2 else 30 if mo in (4, 6, 9, 11) else 31)
    return dt.date(an, mo, jour)


class Finances(Module):
    nom = "finances"
    description = "Cockpit financier local : abonnements et échéances"
    exemples = ("mes abonnements", "combien je paie par mois en abonnements", "prochaines échéances",
                "ouvre le cockpit financier")

    def __init__(self, kx):
        super().__init__(kx)
        self.db = Stockage(os.path.join(DOSSIER_DONNEES, "finances.db"), SCHEMA)

    # -- abonnements ------------------------------------------------------
    def ajouter_abonnement(self, nom: str, montant: float, periodicite: str = "mensuel",
                           prochaine: str | None = None, categorie: str = "", source: str = "manuel") -> int:
        if periodicite not in FACTEUR_MENSUEL:
            raise ErreurModule("périodicité inconnue (hebdo, mensuel, trimestriel, annuel)")
        return self.db.executer(
            "INSERT INTO abonnements (nom, montant, periodicite, prochaine, categorie, source, cree) VALUES (?,?,?,?,?,?,?)",
            (nom.strip(), float(montant), periodicite, prochaine, categorie, source, time.time())).lastrowid

    def abonnements(self, actifs: bool = True) -> list[dict]:
        sql = "SELECT * FROM abonnements" + (" WHERE actif=1" if actifs else "") + " ORDER BY montant DESC"
        return self.db.lignes(sql)

    def total_mensuel(self) -> float:
        return round(sum(a["montant"] * FACTEUR_MENSUEL.get(a["periodicite"], 1) for a in self.abonnements()), 2)

    def total_annuel(self) -> float:
        return round(self.total_mensuel() * 12, 2)

    def modifier_abonnement(self, ident: int, **champs) -> None:
        permis = {"nom", "montant", "periodicite", "prochaine", "categorie", "actif"}
        for k, v in champs.items():
            if k in permis:
                self.db.executer(f"UPDATE abonnements SET {k}=? WHERE id=?", (v, ident))

    def supprimer_abonnement(self, ident: int) -> None:
        self.db.executer("DELETE FROM abonnements WHERE id=?", (ident,))

    # -- échéances --------------------------------------------------------
    def ajouter_echeance(self, libelle: str, date: str, montant: float = 0) -> int:
        if not lire_date(date):
            raise ErreurModule("date invalide")
        return self.db.executer("INSERT INTO echeances (libelle, montant, date) VALUES (?,?,?)",
                                (libelle.strip(), float(montant or 0), lire_date(date))).lastrowid

    def prochaines_echeances(self, jours: int = 30) -> list[dict]:
        limite = (dt.date.today() + dt.timedelta(days=jours)).isoformat()
        sortie = [{"libelle": e["libelle"], "montant": e["montant"], "date": e["date"], "origine": "échéance", "id": e["id"]}
                  for e in self.db.lignes("SELECT * FROM echeances WHERE payee=0 AND date<=? ORDER BY date", (limite,))]
        for a in self.abonnements():
            if a["prochaine"] and a["prochaine"] <= limite:
                sortie.append({"libelle": a["nom"], "montant": a["montant"], "date": a["prochaine"], "origine": "abonnement", "id": a["id"]})
        return sorted(sortie, key=lambda x: x["date"])

    def marquer_payee(self, ident: int) -> None:
        self.db.executer("UPDATE echeances SET payee=1 WHERE id=?", (ident,))

    # -- import CSV -------------------------------------------------------
    def importer_csv(self, contenu: str) -> dict:
        contenu = contenu.lstrip("\ufeff")
        echantillon = contenu[:4000]
        sep = max(";,\t|", key=lambda s: echantillon.count(s))
        lecteur = list(csv.reader(io.StringIO(contenu), delimiter=sep))
        # première ligne qui ressemble à un en-tête (plusieurs colonnes non numériques)
        debut = next((i for i, l in enumerate(lecteur) if len(l) >= 3 and sum(1 for c in l if lire_montant(c) is None and c.strip()) >= 2), 0)
        entete = [normaliser(c) for c in lecteur[debut]]

        def col(*noms):
            for i, e in enumerate(entete):
                if any(n in e for n in noms):
                    return i
            return None
        i_date = col("date")
        i_lib = col("libelle", "label", "description", "intitule", "objet", "operation", "detail")
        i_mnt = col("montant", "amount", "valeur")
        i_deb, i_cre = col("debit"), col("credit")
        if i_date is None or i_lib is None or (i_mnt is None and i_deb is None):
            raise ErreurModule("je ne reconnais pas les colonnes du CSV (il faut : date, libellé, montant ou débit/crédit)")
        nouvelles = doublons = ignorees = 0
        for ligne in lecteur[debut + 1:]:
            if len(ligne) <= max(i for i in (i_date, i_lib, i_mnt, i_deb, i_cre) if i is not None):
                ignorees += 1
                continue
            d = lire_date(ligne[i_date])
            if i_mnt is not None:
                m = lire_montant(ligne[i_mnt])
            else:
                deb, cre = lire_montant(ligne[i_deb]) or 0, (lire_montant(ligne[i_cre]) or 0) if i_cre is not None else 0
                m = cre - abs(deb)
            if not d or m is None:
                ignorees += 1
                continue
            cle = hashlib.sha1(f"{d}|{ligne[i_lib]}|{m:.2f}".encode()).hexdigest()
            try:
                c = self.db.executer("INSERT OR IGNORE INTO transactions (date, libelle, montant, cle) VALUES (?,?,?,?)",
                                     (d, ligne[i_lib].strip(), m, cle))
                if c.rowcount:
                    nouvelles += 1
                else:
                    doublons += 1
            except Exception:
                ignorees += 1
        candidats = self.detecter_recurrences()
        return {"nouvelles": nouvelles, "doublons": doublons, "ignorees": ignorees, "candidats": candidats}

    # -- détection --------------------------------------------------------
    def detecter_recurrences(self) -> int:
        groupes: dict[str, list[dict]] = {}
        for t in self.db.lignes("SELECT * FROM transactions WHERE montant < 0 ORDER BY date"):
            cle = libelle_canonique(t["libelle"])
            if len(cle) >= 3:
                groupes.setdefault(cle, []).append(t)
        n = 0
        connus = {normaliser(a["nom"]) for a in self.abonnements(False)}
        for cle, ts in groupes.items():
            if len(ts) < 3 or any(cle in k or k in cle for k in connus):
                continue
            montants = sorted(abs(t["montant"]) for t in ts)
            median = montants[len(montants) // 2]
            if any(abs(m - median) > max(1.0, median * 0.15) for m in montants):
                continue
            dates = [dt.date.fromisoformat(t["date"]) for t in ts]
            ecarts = [(b - a).days for a, b in zip(dates, dates[1:])]
            moyen = sum(ecarts) / len(ecarts)
            if 26 <= moyen <= 35:
                per = "mensuel"
            elif 85 <= moyen <= 95:
                per = "trimestriel"
            elif 355 <= moyen <= 375:
                per = "annuel"
            elif 6 <= moyen <= 8:
                per = "hebdo"
            else:
                continue
            c = self.db.executer("INSERT OR IGNORE INTO candidats (nom, montant, periodicite, source, occurrences) VALUES (?,?,?,?,?)",
                                 (cle.title(), round(median, 2), per, "transactions", len(ts)))
            n += c.rowcount
        return n

    def detecter_depuis_gmail(self) -> int:
        mails = self.kx.extensions.module("emails")
        if not mails or not mails.configure():
            raise ErreurModule("la boîte mail n'est pas configurée")
        crit = '(OR OR SUBJECT "reçu" SUBJECT "facture" OR SUBJECT "receipt" SUBJECT "abonnement")'
        n = 0
        for m in mails.recherche(crit, 60):
            montant = re.search(r"(\d{1,4}(?:[.,]\d{2}))\s?(?:€|eur|euros)", m["extrait"], re.IGNORECASE)
            if not montant:
                continue
            nom = re.sub(r"\b(no[- ]?reply|noreply|billing|facturation|support)\b", "", m["de"], flags=re.IGNORECASE).strip() or m["adresse"]
            c = self.db.executer("INSERT OR IGNORE INTO candidats (nom, montant, periodicite, source) VALUES (?,?,?,?)",
                                 (nom[:40], lire_montant(montant.group(1)), "mensuel", "gmail"))
            n += c.rowcount
        return n

    def candidats(self) -> list[dict]:
        return self.db.lignes("SELECT * FROM candidats WHERE statut='a_confirmer' ORDER BY occurrences DESC, montant DESC")

    def confirmer_candidat(self, ident: int, accepter: bool = True) -> None:
        c = self.db.une("SELECT * FROM candidats WHERE id=?", (ident,))
        if not c:
            raise ErreurModule("candidat introuvable")
        if accepter:
            self.ajouter_abonnement(c["nom"], c["montant"] or 0, c["periodicite"] or "mensuel", None, "", c["source"])
        self.db.executer("UPDATE candidats SET statut=? WHERE id=?", ("accepte" if accepter else "rejete", ident))

    def resume(self) -> dict:
        return {"abonnements": self.abonnements(), "mensuel": self.total_mensuel(), "annuel": self.total_annuel(),
                "echeances": self.prochaines_echeances(45), "candidats": self.candidats(),
                "transactions": (self.db.une("SELECT COUNT(*) AS n FROM transactions") or {}).get("n", 0)}

    # -- voix (aucune IA) -------------------------------------------------
    def intention(self, texte: str, commande: str) -> str | None:
        c = normaliser(commande)
        if not re.search(r"\b(abonnements?|echeances?|finances?|budget|cockpit financier|prelevements?|depenses recurrentes)\b", c):
            return None
        self.kx.extensions.marquer_confidentiel()
        if re.search(r"\b(ouvre|lance|affiche|montre)\b.*\b(cockpit|finances?)\b", c):
            import webbrowser
            h, p = self.config.get("web_hote", "127.0.0.1"), self.config.get("web_port", 5000)
            if not self.config.get("simulation_systeme", False):
                webbrowser.open(f"http://{h}:{p}/finances")
            return "Cockpit financier ouvert dans ton navigateur. Tout reste sur ta machine."
        if re.search(r"\b(echeances?|prochains? prelevements?|a payer)\b", c):
            e = self.prochaines_echeances(30)
            if not e:
                return "Aucune échéance dans les 30 prochains jours."
            return "Dans les 30 jours : " + " ; ".join(f"{x['libelle']} {x['montant']:.0f} euros le {x['date'][8:]}/{x['date'][5:7]}" for x in e[:6]) + "."
        if re.search(r"\b(combien|total|cout|coute|paie|depense)\b", c):
            a = self.abonnements()
            return f"Tes {len(a)} abonnements coûtent {self.total_mensuel():.2f} euros par mois, soit {self.total_annuel():.0f} par an."
        if re.search(r"\b(detecte|analyse|cherche)\b.*\b(abonnements?|recus)\b", c):
            try:
                n = self.detecter_depuis_gmail()
            except ErreurModule:
                n = 0
            n += self.detecter_recurrences()
            return f"{n} abonnement(s) possible(s) détecté(s) : confirme-les dans le cockpit." if n else "Je n'ai rien trouvé de nouveau."
        a = self.abonnements()
        if not a:
            return "Aucun abonnement enregistré. Importe un relevé CSV dans le cockpit financier."
        return "Tes plus gros abonnements : " + liste_parlee([f"{x['nom']} {x['montant']:.2f} euros" for x in a[:4]]) + \
            f". Total {self.total_mensuel():.2f} euros par mois."

    def statut(self) -> dict:
        return {"abonnements": len(self.abonnements()), "mensuel": self.total_mensuel(), "candidats": len(self.candidats())}

    def outils(self) -> list[Outil]:
        return []        # volontairement : aucune action financière exposée à un agent externe
