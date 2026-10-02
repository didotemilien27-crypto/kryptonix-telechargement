# KRYPTONIX — marche à suivre complète

Objectif atteint : l'utilisateur télécharge `Kryptonix-Setup.exe` sur ta page,
double-clique, et l'IA s'ouvre. Aucun terminal, aucune installation manuelle.

## Ce qui se passe chez l'utilisateur

1. Il clique sur **Télécharger** (page `docs/index.html`).
2. Il double-clique sur `Kryptonix-Setup.exe` : installation silencieuse dans son
   profil (pas de droits administrateur), icône Bureau + menu Démarrer, puis
   **l'application s'ouvre toute seule**.
3. Premier lancement uniquement, avec une barre de progression :
   - installation silencieuse d'**Ollama** (si absent) ;
   - démarrage du moteur ;
   - téléchargement du modèle (`mistral`, ou `gemma:2b` si le PC a moins de 8 Go de RAM).
4. La fenêtre bascule sur l'IA. Les fois suivantes : ouverture immédiate.
5. Fermer la fenêtre arrête tout proprement.

Aucune fenêtre noire n'apparaît à aucun moment.

---

## A. Publier ton application (une seule fois) — 15 minutes, sans PC Windows requis

GitHub construit l'installateur à ta place (dans le cloud), gratuitement.

### 1. Crée le dépôt
1. Compte gratuit sur **github.com**.
2. **New repository** → nom **`kryptonix`** → **Public** → Create.

### 2. Envoie les fichiers
1. Sur la page du dépôt vide : **uploading an existing file**.
2. Glisse **tout le contenu** de ce dossier (pas le dossier lui-même),
   **sans oublier le dossier caché `.github`** (si ton explorateur ne l'affiche pas :
   Affichage → Éléments masqués).
3. **Commit changes**.

> Astuce : avec Git installé, c'est plus simple :
> `git init && git add . && git commit -m "v1.0.0" && git branch -M main`
> `git remote add origin https://github.com/TON-PSEUDO/kryptonix.git && git push -u origin main`

### 3. Autorise l'automatisation
**Settings → Actions → General → Workflow permissions → Read and write permissions → Save.**

### 4. Active la page de téléchargement
**Settings → Pages → Source : Deploy from a branch → Branch : `main` / dossier `/docs` → Save.**
Après 1–2 minutes, ta page est en ligne :
`https://TON-PSEUDO.github.io/kryptonix/`
(Le pseudo est détecté automatiquement, rien à modifier dans le code.)

### 5. Fabrique et publie la version 1.0.0
Sur ton PC, ou directement sur github.com :
**Releases → Create a new release → Choose a tag → écris `v1.0.0` → Create new tag → Publish release.**

GitHub lance alors la construction (onglet **Actions**, environ 10 minutes).
Quand elle est verte, `Kryptonix-Setup.exe` apparaît dans la release et le
bouton de ta page fonctionne.

---

## B. Mettre à jour l'application (à chaque nouveauté)

1. Modifie tes fichiers (sur github.com : crayon ✏️ → Commit, ou `git push`).
2. Crée une nouvelle release avec un numéro plus grand : **`v1.1.0`**, `v1.2.0`…
3. Attends la construction (~10 min).

Chez tous les utilisateurs : au prochain démarrage, l'application voit la
nouvelle version, la télécharge discrètement, et l'installe quand ils la
ferment. **Mémoire, documents et réglages sont conservés.** Ta page de
téléchargement ne change jamais : elle pointe toujours vers la dernière version.

---

## C. Variante : construire sur ton propre PC Windows

1. Installe **Python 3.12** (case « Add python.exe to PATH ») et **Inno Setup 6** (jrsoftware.org/isdl.php).
2. Double-clique sur **`construire_windows.bat`** (5–10 min).
3. Résultat : `sortie\Kryptonix-Setup.exe`.

(Les mises à jour automatiques demandent que le dépôt GitHub soit renseigné :
mets `"depot_github": "TON-PSEUDO/kryptonix"` dans `config.json` avant de construire.)

---

## D. Changer le modèle d'IA

Dans `config.json` : `"ollama_modele": "mistral"` (ou `llama3.2:3b`, `gemma:2b`…).
Avec `"modele_auto": true`, un PC de moins de 8 Go de RAM reçoit automatiquement
`gemma:2b`. Mets `false` pour forcer ton choix.

---

## E. Problèmes courants

| Symptôme | Cause / solution |
|---|---|
| Windows : « a protégé votre ordinateur » | Application non signée (certificat payant). **Informations complémentaires → Exécuter quand même.** Signaler le fichier à Microsoft ou acheter un certificat de signature de code règle ça définitivement. |
| La préparation affiche une erreur | Bouton **Réessayer** (souvent : connexion coupée, ou disque presque plein). |
| Le bouton de la page ne télécharge rien | La release `v1.0.0` n'est pas terminée : regarde l'onglet **Actions** sur GitHub. |
| L'onglet Actions est rouge | Ouvre l'exécution en échec, copie le message d'erreur et envoie-le moi. |
| Logs | `%APPDATA%\Kryptonix\logs\` |

---

## Structure du dossier

```
app.py                  point d'entrée de l'application (fenêtre native, préparation, màj)
noyau/premier_lancement.py   installation auto d'Ollama + modèle
noyau/mise_a_jour.py         mises à jour automatiques
noyau/version.py             numéro de version (rempli par GitHub)
interfaces/templates/preparation.html   écran de première préparation
installateur/kryptonix.iss   installateur Windows (Inno Setup)
kryptonix.spec               construction du .exe (PyInstaller)
.github/workflows/publier.yml   construction automatique dans le cloud
docs/index.html              ta page « Kryptonix Download »
construire_windows.bat       construction locale (variante C)
lancer.bat / lancer.py       mode développeur (avec terminal)
```
