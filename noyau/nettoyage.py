"""
KRYPTONIX / noyau/nettoyage.py
===============================
Analyse et nettoyage du PC : fichiers temporaires, caches navigateurs,
corbeille, gros fichiers oubliés dans les Téléchargements.

Principe de sécurité strict : ce module ne supprime QUE dans des dossiers
connus et sans risque (temp système, caches applicatifs). Il ne touche
JAMAIS aux Documents, Images, Bureau ou tout dossier personnel — il les
signale seulement, pour que l'utilisateur décide lui-même.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import time

LOG = logging.getLogger("nettoyage")

# Taille au-delà de laquelle un fichier oublié dans les Téléchargements
# mérite d'être signalé (mais jamais supprimé automatiquement).
SEUIL_GROS_FICHIER = 200 * 1024 * 1024  # 200 Mo
AGE_FICHIER_OUBLIE_JOURS = 60


class Nettoyage:
    """Analyseur et nettoyeur sûr du disque."""

    def __init__(self, config: dict):
        self.config = config

    # ------------------------------------------------------------------
    #  Dossiers sûrs à nettoyer (jamais les documents personnels)
    # ------------------------------------------------------------------
    def _dossiers_temporaires(self) -> list[str]:
        dossiers = []
        temp = os.environ.get("TEMP") or os.environ.get("TMP")
        if temp and os.path.isdir(temp):
            dossiers.append(temp)

        if platform.system() == "Windows":
            local = os.environ.get("LOCALAPPDATA", "")
            candidats = [
                os.path.join(local, "Temp"),
                os.path.join(local, "Google", "Chrome", "User Data", "Default", "Cache"),
                os.path.join(local, "Microsoft", "Edge", "User Data", "Default", "Cache"),
                os.path.join(local, "Mozilla", "Firefox", "Profiles"),
                os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Temp"),
                os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "SoftwareDistribution",
                            "Download"),
            ]
        else:
            candidats = ["/tmp", os.path.expanduser("~/.cache")]

        for chemin in candidats:
            if os.path.isdir(chemin) and chemin not in dossiers:
                dossiers.append(chemin)
        return dossiers

    def _dossier_telechargements(self) -> str | None:
        chemin = os.path.join(os.path.expanduser("~"), "Downloads")
        if not os.path.isdir(chemin):
            chemin = os.path.join(os.path.expanduser("~"), "Téléchargements")
        return chemin if os.path.isdir(chemin) else None

    # ------------------------------------------------------------------
    #  Analyse (lecture seule, jamais de suppression ici)
    # ------------------------------------------------------------------
    def analyser(self) -> dict:
        """
        Scanne le disque et retourne un rapport structuré :
        {temporaires: {taille, fichiers}, gros_fichiers: [...], corbeille: {...}}
        Ne supprime jamais rien — c'est un état des lieux.
        """
        rapport = {
            "temporaires": {"dossiers": [], "taille_octets": 0, "nombre_fichiers": 0},
            "gros_fichiers_oublies": [],
            "corbeille": {"taille_octets": 0, "presente": False},
            "erreurs": [],
        }

        for dossier in self._dossiers_temporaires():
            taille, nombre = self._mesurer_dossier(dossier)
            if nombre > 0:
                rapport["temporaires"]["dossiers"].append(
                    {"chemin": dossier, "taille_octets": taille, "fichiers": nombre}
                )
                rapport["temporaires"]["taille_octets"] += taille
                rapport["temporaires"]["nombre_fichiers"] += nombre

        telechargements = self._dossier_telechargements()
        if telechargements:
            limite_age = time.time() - AGE_FICHIER_OUBLIE_JOURS * 86400
            try:
                for nom in os.listdir(telechargements):
                    chemin = os.path.join(telechargements, nom)
                    try:
                        if os.path.isfile(chemin):
                            info = os.stat(chemin)
                            if info.st_size >= SEUIL_GROS_FICHIER and info.st_mtime < limite_age:
                                rapport["gros_fichiers_oublies"].append({
                                    "nom": nom, "chemin": chemin,
                                    "taille_octets": info.st_size,
                                    "age_jours": round((time.time() - info.st_mtime) / 86400),
                                })
                    except OSError:
                        continue
            except OSError as erreur:
                rapport["erreurs"].append(str(erreur))

        if platform.system() == "Windows":
            lecteur = os.environ.get("SystemDrive", "C:") + "\\"
            corbeille = os.path.join(lecteur, "$Recycle.Bin")
            if os.path.isdir(corbeille):
                taille, _ = self._mesurer_dossier(corbeille, ignorer_permissions=True)
                rapport["corbeille"] = {"taille_octets": taille, "presente": taille > 0}

        return rapport

    @staticmethod
    def _mesurer_dossier(chemin: str, ignorer_permissions: bool = False) -> tuple[int, int]:
        """Retourne (taille_totale_octets, nombre_de_fichiers). Tolère les accès refusés."""
        taille = 0
        nombre = 0
        try:
            for racine, _sous_dossiers, fichiers in os.walk(chemin):
                for nom in fichiers:
                    try:
                        taille += os.path.getsize(os.path.join(racine, nom))
                        nombre += 1
                    except (OSError, PermissionError):
                        continue
        except (OSError, PermissionError):
            if not ignorer_permissions:
                LOG.debug("Accès refusé : %s", chemin)
        return taille, nombre

    # ------------------------------------------------------------------
    #  Nettoyage (suppression réelle, uniquement dans les dossiers sûrs)
    # ------------------------------------------------------------------
    def nettoyer_temporaires(self) -> dict:
        """
        Supprime le contenu des dossiers temporaires système identifiés par
        analyser(). Ignore silencieusement les fichiers verrouillés (normal :
        certains sont utilisés par Windows au moment même du nettoyage).
        """
        resultat = {"octets_liberes": 0, "fichiers_supprimes": 0, "fichiers_ignores": 0}
        for dossier in self._dossiers_temporaires():
            # topdown=False : on visite les sous-dossiers avant leurs parents,
            # ce qui permet de supprimer les dossiers vides au passage.
            for racine, sous_dossiers, fichiers in os.walk(dossier, topdown=False):
                for nom in fichiers:
                    chemin = os.path.join(racine, nom)
                    try:
                        taille = os.path.getsize(chemin)
                        os.remove(chemin)
                        resultat["octets_liberes"] += taille
                        resultat["fichiers_supprimes"] += 1
                    except (OSError, PermissionError):
                        resultat["fichiers_ignores"] += 1
                for sous in sous_dossiers:
                    try:
                        os.rmdir(os.path.join(racine, sous))
                    except OSError:
                        pass  # dossier non vide (fichier verrouillé) : on laisse tranquille
        return resultat

    # ------------------------------------------------------------------
    #  Restitution parlée
    # ------------------------------------------------------------------
    @staticmethod
    def formater_octets(octets: int) -> str:
        for unite in ("o", "Ko", "Mo", "Go"):
            if octets < 1024:
                return f"{octets:.0f} {unite}" if unite == "o" else f"{octets:.1f} {unite}"
            octets /= 1024
        return f"{octets:.1f} To"

    def rapport_parle(self, rapport: dict) -> str:
        phrases = []
        temp = rapport["temporaires"]
        if temp["nombre_fichiers"] > 0:
            phrases.append(
                f"J'ai trouvé {temp['nombre_fichiers']} fichiers temporaires representant "
                f"{self.formater_octets(temp['taille_octets'])} récupérables sans risque."
            )
        else:
            phrases.append("Aucun fichier temporaire significatif à nettoyer.")

        if rapport["gros_fichiers_oublies"]:
            plus_gros = sorted(rapport["gros_fichiers_oublies"],
                               key=lambda f: f["taille_octets"], reverse=True)[:3]
            noms = ", ".join(
                f"{f['nom']} ({self.formater_octets(f['taille_octets'])}, "
                f"oublié depuis {f['age_jours']} jours)" for f in plus_gros
            )
            phrases.append(
                f"Dans tes téléchargements, {len(rapport['gros_fichiers_oublies'])} gros "
                f"fichiers dorment depuis plus de {AGE_FICHIER_OUBLIE_JOURS} jours : {noms}. "
                "Je ne les supprime pas moi-même, ce sont peut-être des documents importants "
                "— jette un œil et supprime-les toi-même si besoin."
            )

        if rapport["corbeille"]["presente"]:
            phrases.append(
                f"La corbeille contient {self.formater_octets(rapport['corbeille']['taille_octets'])} "
                "— vide-la si tu veux regagner cette place."
            )

        phrases.append("Dis « nettoie les fichiers temporaires » pour libérer la place en sécurité.")
        return " ".join(phrases)
