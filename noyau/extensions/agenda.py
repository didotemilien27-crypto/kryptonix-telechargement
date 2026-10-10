"""
KRYPTONIX / noyau/extensions/agenda.py
=======================================
Agenda complet, trois sources fusionnées :

  1. Google Agenda (API REST, OAuth) : tous tes agendas, création et suppression ;
  2. Agendas iCal (adresses secrètes .ics, y compris agendas auxquels tu es abonné) : lecture ;
  3. Agenda local (SQLite) : fonctionne sans aucun compte ; sert aussi de repli.

CRÉATION et SUPPRESSION passent par la confirmation vocale STRICTE du coffre de sécurité :
un « non », « pas du tout », « annule » (même dans « oui mais non ») n'exécute rien.

Réglages : google_client_id, google_client_secret, google_refresh_token (voir
scripts/google_autorisation.py), google_calendriers (liste de noms, vide = tous),
agenda_ical = [{"nom": "...", "url": "https://.../basic.ics"}].
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import time
import uuid

from ..config import DOSSIER_DONNEES
from .base import ErreurModule, Module, Outil, http, liste_parlee, normaliser, sans_accents
from .stockage import Stockage

LOG = logging.getLogger("agenda")

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout", "septembre",
        "octobre", "novembre", "decembre"]
MOIS_PARLES = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
               "octobre", "novembre", "décembre"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS agenda_local (
    id TEXT PRIMARY KEY, titre TEXT NOT NULL, debut REAL NOT NULL, fin REAL NOT NULL,
    journee INTEGER DEFAULT 0, lieu TEXT DEFAULT ''
);
"""


# ---------------------------------------------------------------------------
#  Dates en français
# ---------------------------------------------------------------------------
def parse_date_heure(c: str, maintenant: dt.datetime | None = None):
    """
    Lit une date et une heure dans une phrase.
    Retourne (debut: datetime | None, heure_precisee: bool, reste: str) — `reste` = la phrase
    privée des expressions de date/heure (le titre probable de l'événement).
    """
    now = maintenant or dt.datetime.now()
    t = normaliser(c)
    jour: dt.date | None = None

    def retire(motif: str):
        nonlocal t
        t = re.sub(motif, " ", t, count=1)

    if re.search(r"\bapres[- ]demain\b", t):
        jour = now.date() + dt.timedelta(days=2); retire(r"\bapres[- ]demain\b")
    elif re.search(r"\bdemain\b", t):
        jour = now.date() + dt.timedelta(days=1); retire(r"\bdemain\b")
    elif re.search(r"\baujourd'hui\b|\bce jour\b", t):
        jour = now.date(); retire(r"\baujourd'hui\b|\bce jour\b")
    elif re.search(r"\bce soir\b", t):
        jour = now.date(); retire(r"\bce soir\b")
    else:
        m = re.search(r"\b(?:le )?(\d{1,2})(?:er)? (" + "|".join(MOIS) + r")(?: (\d{4}))?\b", t)
        if m:
            an = int(m.group(3)) if m.group(3) else now.year
            try:
                jour = dt.date(an, MOIS.index(m.group(2)) + 1, int(m.group(1)))
                if not m.group(3) and jour < now.date():
                    jour = dt.date(an + 1, jour.month, jour.day)
            except ValueError:
                jour = None
            retire(re.escape(m.group(0)))
        else:
            m = re.search(r"\b(?:ce |le |du |ce prochain )?(" + "|".join(JOURS) + r")(?: prochain)?\b", t)
            if m:
                cible = JOURS.index(m.group(1))
                delta = (cible - now.weekday()) % 7
                if delta == 0 and "prochain" in m.group(0):
                    delta = 7
                jour = now.date() + dt.timedelta(days=delta)
                retire(re.escape(m.group(0)))
            else:
                m = re.search(r"\ble (\d{1,2})(?!\s*(?:h|heures?|:))\b", t)
                if m and 1 <= int(m.group(1)) <= 31:
                    j = int(m.group(1))
                    try:
                        jour = now.date().replace(day=j)
                        if jour < now.date():
                            mois = now.month % 12 + 1
                            an = now.year + (1 if now.month == 12 else 0)
                            jour = dt.date(an, mois, j)
                    except ValueError:
                        jour = None
                    retire(re.escape(m.group(0)))

    heure = None
    if re.search(r"\bmidi\b", t):
        heure = (12, 0); retire(r"\bmidi\b")
    elif re.search(r"\bminuit\b", t):
        heure = (0, 0); retire(r"\bminuit\b")
    else:
        m = re.search(r"\b(?:a |vers )?(\d{1,2})\s*(?:h|heures?|:)\s*(\d{2})?\b", t)
        if m and int(m.group(1)) <= 24:
            heure = (int(m.group(1)) % 24, int(m.group(2) or 0))
            retire(re.escape(m.group(0)))
            if re.search(r"\b(du soir|de l'apres-midi|pm)\b", c.lower().replace("è", "e")) and heure[0] < 12:
                heure = (heure[0] + 12, heure[1])

    if jour is None and heure is None:
        return None, False, c.strip()
    if jour is None:
        jour = now.date()
        if dt.datetime.combine(jour, dt.time(*heure)) < now:
            jour += dt.timedelta(days=1)
    debut = dt.datetime.combine(jour, dt.time(*(heure or (0, 0))))
    reste = re.sub(r"\s+", " ", t).strip()
    return debut, heure is not None, reste


def lire_duree_evenement(c: str) -> int:
    """Durée en minutes (« pendant 2 heures », « 45 minutes »), 60 par défaut."""
    t = normaliser(c)
    m = re.search(r"(?:pendant|durant|de)?\s*(\d+)\s*(heures?|h|minutes?|min)\b(?! \d)", t)
    if m and re.search(r"\b(pendant|durant|dure|duree)\b", t):
        n = int(m.group(1))
        return n * 60 if m.group(2).startswith("h") else n
    return 60


# ---------------------------------------------------------------------------
#  iCal
# ---------------------------------------------------------------------------
def _deplier(texte: str) -> list[str]:
    lignes: list[str] = []
    for l in texte.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if l[:1] in (" ", "\t") and lignes:
            lignes[-1] += l[1:]
        else:
            lignes.append(l)
    return lignes


def _lire_date_ics(valeur: str, params: str):
    """→ (datetime naïf local, journée_entière)"""
    v = valeur.strip()
    if re.fullmatch(r"\d{8}", v):
        return dt.datetime.strptime(v, "%Y%m%d"), True
    m = re.fullmatch(r"(\d{8}T\d{6})(Z?)", v)
    if not m:
        return None, False
    d = dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")
    if m.group(2) == "Z":
        d = d.replace(tzinfo=dt.timezone.utc).astimezone().replace(tzinfo=None)
    else:
        tz = re.search(r"TZID=([^;:]+)", params)
        if tz:
            try:
                from zoneinfo import ZoneInfo
                d = d.replace(tzinfo=ZoneInfo(tz.group(1))).astimezone().replace(tzinfo=None)
            except Exception:
                pass
    return d, False


def _echapper(v: str) -> str:
    return v.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()


def parse_ics(texte: str, debut: dt.datetime, fin: dt.datetime, source: str = "ical") -> list[dict]:
    """Événements d'un fichier .ics compris dans [debut, fin], répétitions simples incluses."""
    evenements, cur = [], None
    for ligne in _deplier(texte):
        if ligne == "BEGIN:VEVENT":
            cur = {}
        elif ligne == "END:VEVENT" and cur is not None:
            evenements.extend(_developper(cur, debut, fin, source))
            cur = None
        elif cur is not None and ":" in ligne:
            cle, _, val = ligne.partition(":")
            nom, _, params = cle.partition(";")
            cur.setdefault(nom.upper(), (val, params))
            if nom.upper() == "EXDATE":
                cur.setdefault("_EXDATES", []).append(val)
    return sorted(evenements, key=lambda e: e["debut"])


def _developper(ev: dict, debut: dt.datetime, fin: dt.datetime, source: str) -> list[dict]:
    if "DTSTART" not in ev:
        return []
    d0, journee = _lire_date_ics(*ev["DTSTART"])
    if d0 is None:
        return []
    if "DTEND" in ev:
        d1, _ = _lire_date_ics(*ev["DTEND"])
    else:
        d1 = None
    duree = (d1 - d0) if d1 else (dt.timedelta(days=1) if journee else dt.timedelta(hours=1))
    titre = _echapper(ev.get("SUMMARY", ("(sans titre)", ""))[0])
    lieu = _echapper(ev.get("LOCATION", ("", ""))[0])
    uid = ev.get("UID", (str(uuid.uuid4()), ""))[0]
    exdates = set()
    for x in ev.get("_EXDATES", []):
        for part in x.split(","):
            d, _ = _lire_date_ics(part, "")
            if d:
                exdates.add(d.date())

    def fabrique(d):
        return {"id": uid, "titre": titre, "debut": d, "fin": d + duree, "journee": journee,
                "lieu": lieu, "source": source, "modifiable": False}

    rr = ev.get("RRULE")
    if not rr:
        return [fabrique(d0)] if d0 < fin and d0 + duree > debut else []

    regles = dict(p.split("=", 1) for p in rr[0].split(";") if "=" in p)
    freq = regles.get("FREQ", "")
    pas = int(regles.get("INTERVAL", 1) or 1)
    maxi = int(regles["COUNT"]) if regles.get("COUNT", "").isdigit() else None
    jusque = None
    if "UNTIL" in regles:
        jusque, _ = _lire_date_ics(regles["UNTIL"], "")
    jours_semaine = [JOURS_ICS.index(j[-2:]) for j in regles.get("BYDAY", "").split(",") if j[-2:] in JOURS_ICS]
    resultats, n, courant, garde = [], 0, d0, 0
    while garde < 4000:
        garde += 1
        if courant >= fin or (jusque and courant > jusque) or (maxi and n >= maxi):
            break
        candidats = [courant]
        if freq == "WEEKLY" and jours_semaine:
            lundi = courant - dt.timedelta(days=courant.weekday())
            candidats = [lundi + dt.timedelta(days=j) for j in sorted(jours_semaine)]
            candidats = [c.replace(hour=d0.hour, minute=d0.minute) for c in candidats if c >= d0]
        for cand in candidats:
            if maxi and n >= maxi:
                break
            if jusque and cand > jusque:
                break
            n += 1
            if cand.date() not in exdates and cand < fin and cand + duree > debut:
                resultats.append(fabrique(cand))
        if freq == "DAILY":
            courant += dt.timedelta(days=pas)
        elif freq == "WEEKLY":
            courant += dt.timedelta(weeks=pas)
        elif freq == "MONTHLY":
            m = courant.month - 1 + pas
            try:
                courant = courant.replace(year=courant.year + m // 12, month=m % 12 + 1)
            except ValueError:
                courant = courant.replace(year=courant.year + m // 12, month=m % 12 + 1, day=28)
        elif freq == "YEARLY":
            try:
                courant = courant.replace(year=courant.year + pas)
            except ValueError:
                courant = courant.replace(year=courant.year + pas, day=28)
        else:
            break
    return resultats


JOURS_ICS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


# ---------------------------------------------------------------------------
#  Google Agenda (REST)
# ---------------------------------------------------------------------------
class GoogleAgenda:
    def __init__(self, config: dict):
        self.config = config
        self._jeton = ""
        self._expire = 0.0

    def configure(self) -> bool:
        return all(self.config.get(k) for k in ("google_client_id", "google_client_secret", "google_refresh_token"))

    @property
    def _base(self) -> str:
        return (self.config.get("google_api_base") or "https://www.googleapis.com").rstrip("/")

    def _acces(self) -> str:
        if self._jeton and time.time() < self._expire - 30:
            return self._jeton
        url = self.config.get("google_token_url") or "https://oauth2.googleapis.com/token"
        r = http("POST", url, data={"client_id": self.config["google_client_id"],
                                    "client_secret": self.config["google_client_secret"],
                                    "refresh_token": self.config["google_refresh_token"],
                                    "grant_type": "refresh_token"})
        if r.status_code != 200:
            raise ErreurModule("l'autorisation Google a expiré, relance scripts/google_autorisation.py")
        d = r.json()
        self._jeton = d["access_token"]
        self._expire = time.time() + int(d.get("expires_in", 3600))
        return self._jeton

    def _appel(self, methode: str, chemin: str, **kw):
        r = http(methode, self._base + chemin, headers={"Authorization": f"Bearer {self._acces()}"}, **kw)
        if r.status_code == 401:
            self._jeton = ""
            raise ErreurModule("Google a refusé l'accès à l'agenda")
        if r.status_code >= 400:
            raise ErreurModule(f"Google Agenda a répondu {r.status_code}")
        return r

    def calendriers(self) -> list[dict]:
        items = self._appel("GET", "/calendar/v3/users/me/calendarList").json().get("items", [])
        voulus = [normaliser(n) for n in (self.config.get("google_calendriers") or [])]
        if voulus:
            items = [i for i in items if normaliser(i.get("summary", "")) in voulus]
        return items

    def evenements(self, debut: dt.datetime, fin: dt.datetime) -> list[dict]:
        sortie = []
        for cal in self.calendriers():
            r = self._appel("GET", f"/calendar/v3/calendars/{requests_quote(cal['id'])}/events",
                            params={"timeMin": _rfc3339(debut), "timeMax": _rfc3339(fin), "singleEvents": "true",
                                    "orderBy": "startTime", "maxResults": 100})
            for e in r.json().get("items", []):
                d0, journee = _date_google(e.get("start", {}))
                d1, _ = _date_google(e.get("end", {}))
                if d0 is None:
                    continue
                sortie.append({"id": e["id"], "titre": e.get("summary", "(sans titre)"), "debut": d0,
                               "fin": d1 or d0 + dt.timedelta(hours=1), "journee": journee,
                               "lieu": e.get("location", ""), "source": f"google:{cal.get('summary', '')}",
                               "calendrier": cal["id"],
                               "modifiable": cal.get("accessRole") in ("owner", "writer")})
        return sorted(sortie, key=lambda e: e["debut"])

    def creer(self, titre: str, debut: dt.datetime, fin: dt.datetime, lieu: str = "") -> str:
        corps = {"summary": titre, "start": {"dateTime": _rfc3339(debut)}, "end": {"dateTime": _rfc3339(fin)}}
        if lieu:
            corps["location"] = lieu
        r = self._appel("POST", "/calendar/v3/calendars/primary/events", json=corps)
        return r.json().get("id", "")

    def supprimer(self, calendrier: str, evenement: str) -> None:
        self._appel("DELETE", f"/calendar/v3/calendars/{requests_quote(calendrier)}/events/{evenement}")


def requests_quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")


def _rfc3339(d: dt.datetime) -> str:
    if d.tzinfo is None:
        d = d.astimezone()
    return d.isoformat(timespec="seconds")


def _date_google(champ: dict):
    if "dateTime" in champ:
        d = dt.datetime.fromisoformat(champ["dateTime"].replace("Z", "+00:00"))
        return d.astimezone().replace(tzinfo=None), False
    if "date" in champ:
        return dt.datetime.fromisoformat(champ["date"]), True
    return None, False


# ---------------------------------------------------------------------------
#  Module
# ---------------------------------------------------------------------------
class Agenda(Module):
    nom = "agenda"
    description = "Google Agenda, agendas iCal et agenda local"
    exemples = ("qu'est-ce que j'ai demain", "mon prochain rendez-vous", "ajoute un rendez-vous dentiste demain à 14h",
                "supprime le rendez-vous dentiste")

    def __init__(self, kx):
        super().__init__(kx)
        self.db = Stockage(os.path.join(DOSSIER_DONNEES, "extensions.db"), SCHEMA)
        self.google = GoogleAgenda(self.config)

    # -- lecture ----------------------------------------------------------
    def _icals(self) -> list[dict]:
        sortie = []
        for e in self.config.get("agenda_ical") or []:
            sortie.append(e if isinstance(e, dict) else {"nom": "iCal", "url": str(e)})
        return sortie

    def evenements(self, debut: dt.datetime, fin: dt.datetime) -> list[dict]:
        tous, erreurs = [], []
        if self.google.configure():
            try:
                tous += self.google.evenements(debut, fin)
            except ErreurModule as e:
                erreurs.append(f"Google ({e})")
        for cal in self._icals():
            try:
                r = http("GET", cal["url"], timeout=12)
                if r.status_code == 200:
                    tous += parse_ics(r.text, debut, fin, f"ical:{cal.get('nom', 'iCal')}")
                else:
                    erreurs.append(f"{cal.get('nom', 'iCal')} ({r.status_code})")
            except ErreurModule as e:
                erreurs.append(f"{cal.get('nom', 'iCal')} ({e})")
        for r in self.db.lignes("SELECT * FROM agenda_local WHERE debut < ? AND fin > ?",
                                (fin.timestamp(), debut.timestamp())):
            tous.append({"id": r["id"], "titre": r["titre"], "debut": dt.datetime.fromtimestamp(r["debut"]),
                         "fin": dt.datetime.fromtimestamp(r["fin"]), "journee": bool(r["journee"]),
                         "lieu": r["lieu"], "source": "local", "modifiable": True})
        self.erreurs_lecture = erreurs
        return sorted(tous, key=lambda e: e["debut"])

    @staticmethod
    def _heure(e: dict) -> str:
        if e["journee"]:
            return "toute la journée"
        d = e["debut"]
        return f"{d.hour}h" if d.minute == 0 else f"{d.hour}h{d.minute:02d}"

    def resume_parle(self, evenements: list[dict], libelle: str) -> str:
        if not evenements:
            return f"Rien de prévu {libelle}."
        morceaux = [f"{self._heure(e)} {e['titre']}" + (f" à {e['lieu']}" if e.get("lieu") else "")
                    for e in evenements[:8]]
        suite = f" et {len(evenements) - 8} autre(s)" if len(evenements) > 8 else ""
        return f"{libelle.capitalize()}, tu as : " + " ; ".join(morceaux) + suite + "."

    # -- écriture ---------------------------------------------------------
    def creer(self, titre: str, debut: dt.datetime, minutes: int = 60, lieu: str = "") -> str:
        fin = debut + dt.timedelta(minutes=minutes)
        if self.google.configure():
            self.google.creer(titre, debut, fin, lieu)
            cible = "ton Google Agenda"
        else:
            self.db.executer("INSERT INTO agenda_local (id, titre, debut, fin, journee, lieu) VALUES (?,?,?,?,0,?)",
                             (uuid.uuid4().hex, titre, debut.timestamp(), fin.timestamp(), lieu))
            cible = "l'agenda local"
        self.kx.extensions.sons.jouer("succes")
        return f"« {titre} » ajouté à {cible}, {self.jour_parle(debut)} à {self._heure({'journee': False, 'debut': debut})}."

    def supprimer(self, evenement: dict) -> str:
        if not evenement.get("modifiable", False):
            raise ErreurModule("cet événement vient d'un agenda en lecture seule")
        if str(evenement["source"]).startswith("google"):
            self.google.supprimer(evenement["calendrier"], evenement["id"])
        else:
            self.db.executer("DELETE FROM agenda_local WHERE id=?", (evenement["id"],))
        return f"« {evenement['titre']} » supprimé."

    @staticmethod
    def jour_parle(d: dt.datetime) -> str:
        auj = dt.date.today()
        if d.date() == auj:
            return "aujourd'hui"
        if d.date() == auj + dt.timedelta(days=1):
            return "demain"
        return f"le {JOURS[d.weekday()]} {d.day} {MOIS_PARLES[d.month - 1]}"

    # -- voix -------------------------------------------------------------
    def intention(self, texte: str, commande: str) -> str | None:
        c = normaliser(commande)
        if not re.search(r"\b(agenda|rendez[- ]?vous|rdv|evenement|calendrier|reunion|planning|programme)\b|"
                         r"\bqu'est-ce que j'ai\b|\bj'ai quoi\b|\bje fais quoi\b|\bmon emploi du temps\b", c):
            return None
        try:
            if re.search(r"\b(supprime|efface|annule|retire|enleve)\b", c):
                return self._supprimer_vocal(c)
            if re.search(r"\b(ajoute|cree|programme|planifie|prends?|note|mets?|fixe)\b.*\b(rendez[- ]?vous|rdv|evenement|reunion|agenda|calendrier)\b|"
                         r"\b(rendez[- ]?vous|rdv|reunion)\b.*\b(ajoute|cree|programme|planifie)\b|\bajoute .* (a|dans) (mon |l')?agenda\b", c):
                return self._creer_vocal(texte, c)
            return self._consulter(c)
        except ErreurModule as e:
            self.kx.extensions.sons.jouer("erreur")
            return f"Agenda : {e}."

    def _consulter(self, c: str) -> str:
        now = dt.datetime.now()
        if re.search(r"\bprochain\b", c):
            evs = [e for e in self.evenements(now, now + dt.timedelta(days=60)) if e["fin"] > now]
            if not evs:
                return "Aucun rendez-vous à venir dans les deux prochains mois."
            e = next((x for x in evs if not x["journee"]), evs[0])       # un rendez-vous horaire avant un « toute la journée »
            return f"Ton prochain rendez-vous : {e['titre']}, {self.jour_parle(e['debut'])} à {self._heure(e)}."
        if re.search(r"\b(cette semaine|semaine)\b", c):
            debut = now.replace(hour=0, minute=0, second=0, microsecond=0)
            evs = self.evenements(debut, debut + dt.timedelta(days=7))
            if not evs:
                return "Rien de prévu cette semaine."
            return "Cette semaine : " + " ; ".join(
                f"{self.jour_parle(e['debut'])} {self._heure(e)} {e['titre']}" for e in evs[:10]) + "."
        d, _, _ = parse_date_heure(c, now)
        jour = (d or now).replace(hour=0, minute=0, second=0, microsecond=0)
        evs = self.evenements(jour, jour + dt.timedelta(days=1))
        libelle = self.jour_parle(jour)
        reponse = self.resume_parle(evs, libelle)
        if getattr(self, "erreurs_lecture", None):
            reponse += " (Certains agendas n'ont pas répondu : " + liste_parlee(self.erreurs_lecture) + ".)"
        return reponse

    def _creer_vocal(self, texte: str, c: str) -> str:
        debut, avec_heure, reste = parse_date_heure(c)
        if debut is None:
            return "Pour quand ? Dis par exemple « demain à 14h »."
        if not avec_heure:
            return "À quelle heure ? Redis la phrase avec une heure, par exemple « demain à 14h »."
        minutes = lire_duree_evenement(c)
        titre = re.sub(r"\b(ajoute|cree|programme|planifie|prends?|note|mets?|fixe|un|une|le|la|les|mon|ma|a|de|dans|"
                       r"rendez[- ]?vous|rdv|evenement|reunion|agenda|calendrier|pendant|durant|\d+\s*(?:heures?|minutes?|min))\b",
                       " ", reste)
        titre = re.sub(r"\s+", " ", titre).strip(" ,.:") or "Rendez-vous"
        # on garde la casse d'origine si le titre est retrouvé dans la phrase d'origine
        i = sans_accents(texte.lower().replace("’", "'")).find(titre.lower())
        if i >= 0:
            titre = texte[i:i + len(titre)]          # retrouve les accents de la phrase d'origine
        titre = titre[:1].upper() + titre[1:]
        desc = f"ajouter « {titre} » {self.jour_parle(debut)} à {self._heure({'journee': False, 'debut': debut})} dans ton agenda"
        return self.securise("agenda_creer", {"titre": titre, "debut": debut.isoformat(), "minutes": minutes},
                             description=desc)

    def _supprimer_vocal(self, c: str) -> str:
        now = dt.datetime.now()
        d, _, reste = parse_date_heure(c, now)
        mots = [m for m in re.sub(r"\b(supprime|efface|annule|retire|enleve|le|la|les|mon|ma|de|du|des|un|une|rendez[- ]?vous|"
                                   r"rdv|evenement|reunion|agenda|calendrier|dans|a|au)\b", " ", reste).split() if len(m) > 2]
        evs = [e for e in self.evenements(now, now + dt.timedelta(days=90)) if e["fin"] > now]
        if d is not None:
            evs = [e for e in evs if e["debut"].date() == d.date()]
        if mots:
            evs = [e for e in evs if all(m in normaliser(e["titre"]) for m in mots)]
        if not evs:
            return "Je ne trouve aucun rendez-vous correspondant."
        if len(evs) > 1:
            return ("J'en trouve plusieurs : " + " ; ".join(f"{self.jour_parle(e['debut'])} {self._heure(e)} {e['titre']}"
                                                              for e in evs[:5]) + ". Précise lequel.")
        e = evs[0]
        if not e.get("modifiable"):
            return f"« {e['titre']} » vient d'un agenda en lecture seule, je ne peux pas le supprimer."
        self._cible_suppression = e
        return self.securise("agenda_supprimer", {"evenement": {**e, "debut": e["debut"].isoformat(), "fin": e["fin"].isoformat()}},
                             description=f"supprimer « {e['titre']} » ({self.jour_parle(e['debut'])} à {self._heure(e)})")

    # -- statut / outils --------------------------------------------------
    def statut(self) -> dict:
        return {"google": self.google.configure(), "ical": len(self._icals()),
                "local": (self.db.une("SELECT COUNT(*) AS n FROM agenda_local") or {}).get("n", 0)}

    def outils(self) -> list[Outil]:
        def creer(a):
            return self.creer(a["titre"], dt.datetime.fromisoformat(a["debut"]), int(a.get("minutes", 60)), a.get("lieu", ""))

        def supprimer(a):
            e = dict(a["evenement"])
            e["debut"] = dt.datetime.fromisoformat(e["debut"])
            e["fin"] = dt.datetime.fromisoformat(e["fin"])
            return self.supprimer(e)

        def aujourdhui(a):
            now = dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            return self.resume_parle(self.evenements(now, now + dt.timedelta(days=1)), "aujourd'hui")
        return [
            Outil("agenda_aujourdhui", "Lire l'agenda du jour", aujourdhui, {}, (), 1, True, "agenda"),
            Outil("agenda_creer", "Créer un événement", creer,
                  {"titre": {"type": "string"}, "debut": {"type": "string", "description": "ISO 8601"},
                   "minutes": {"type": "integer"}}, ("titre", "debut"), 2, False, "agenda"),
            Outil("agenda_supprimer", "Supprimer un événement", supprimer,
                  {"evenement": {"type": "object"}}, ("evenement",), 3, False, "agenda"),
        ]
