# KRYPTONIX

Assistant IA personnel, **100 % local** : gratuit, sans compte, tes conversations restent sur ton PC.
Application Windows : on télécharge `Kryptonix-Setup.exe`, on double-clique, l'IA s'ouvre.
Au premier lancement l'application installe seule ce dont elle a besoin (moteur d'IA Ollama et modèle),
avec une barre de progression. Aucun terminal.

## Pour les utilisateurs
Page de téléchargement : https://didotemilien27-crypto.github.io/kryptonix-telechargement/

## Réglages (onglet RÉGLAGES)
Tout est réglable et appliqué immédiatement :
- **Apparence** : couleur, forme, taille.
- **Voix** : voix (voix neuronales Microsoft réalistes + voix du système hors ligne), vitesse, hauteur, volume,
  bouton « Écouter la voix » (chaque changement est rejoué pour entendre la différence).
- **Informations en direct** : vitesse de défilement du fil d'infos, ville de la météo, fréquence d'actualisation
  (météo, actualités, processeur/mémoire), sources RSS.
- **Assistant** : prénom, créativité, modèle d'IA (changer ou télécharger un modèle sans terminal).
- **Médias** : précision de l'oreille, installation de la vue, images regardées par vidéo, ouverture sur un autre sujet.
- **Bac à sable** : activer/désactiver, durée maximale.

## Messages vocaux, images, vidéos
Dépose-les (glisser-déposer n'importe où dans la fenêtre, ou panneau **Ingestion**) :
- **Audio** (mp3, wav, m4a, ogg, opus, flac, aac, wma, amr…) : transcrit **sur ton PC** (hors ligne) avec des repères de temps.
  Le modèle d'oreille se télécharge une fois (75 à 480 Mo selon la précision, réglable) : au premier message vocal,
  ou à l'avance via RÉGLAGES → Médias → *Préparer mon oreille*.
- **Image** (png, jpg, webp, gif, bmp) : décrite par un modèle de vision. Il faut l'installer une fois :
  RÉGLAGES → Médias → *Installer la vue* (Moondream, environ 1,7 Go). Sans lui, je le dis et j'explique comment faire.
- **Vidéo** (mp4, mov, mkv, avi, webm…) : ce qui s'y dit (bande sonore) + ce qu'on y voit (images clés, nombre réglable).
  Sans modèle de vision, je me limite à la bande sonore et je le précise.

Ensuite, interroge-moi : « que dit mon message vocal ? », « transcris mon dernier enregistrement »,
« résume ma vidéo », « décris ma dernière image », ou toute autre question, qui s'appuiera sur le contenu compris.

**Quand je ne peux pas répondre**, je le dis avec douceur, sans rien inventer, j'indique ce que je peux faire à la place,
puis je propose un autre sujet (désactivable dans RÉGLAGES → Médias).

## Le petit jeu (le sauteur)
Il apparaît sur le côté (colonne de droite) pendant que l'assistant réfléchit, ou quand tu cliques sur **jeu** en haut à droite.
Il prend les couleurs de tes réglages (couleur et forme) et ne rétrécit jamais la conversation.
- **Bouger** : flèches ← → (marchent même après l'envoi d'un message), Q / D ou A (après un clic sur le jeu),
  ou glisser avec la souris ou le doigt.
- **Espace** : pause. La pause est aussi automatique quand tu changes de fenêtre. **Entrée / espace / clic** : rejouer.
- Le niveau devient plus difficile au fil de la montée (plateformes plus étroites, plus espacées, qui bougent, ressorts).
- Ton **record** est gardé d'une fois sur l'autre. Une partie en cours n'est jamais coupée par l'arrivée de la réponse.
- La vitesse du jeu est la même sur tous les écrans (60, 144 Hz...).

## Bac à sable (onglet SANDBOX)
Python, JavaScript, Bash, C, C++, Java, Perl (si l'interpréteur est installé) et, sans rien installer :
**HTML** et **CSS** avec aperçu direct dans un cadre isolé (scripts autorisés, réseau coupé).

## Mettre à jour l'application (pour toi, le développeur)
1. Modifie les fichiers (sur github.com avec ✏️, ou avec GitHub Desktop puis *Commit* + *Push origin*).
2. Crée une release avec un numéro plus grand que le précédent : https://github.com/didotemilien27-crypto/kryptonix-telechargement/releases/new
   (tag `v1.1.0`, puis `v1.1.1`, `v1.2.0`...).
3. Attends 4 à 10 minutes que l'onglet **Actions** soit vert ✓ : `Kryptonix-Setup.exe` est ajouté à la release.

Les utilisateurs reçoivent la mise à jour tout seuls au prochain lancement ; leurs conversations,
documents et réglages (dans `%APPDATA%\Kryptonix`) sont conservés.

## Tester en local avant de publier
Double-clique sur **`lancer.bat`** : il prépare Python tout seul (première fois : 2 à 5 minutes), puis ouvre
KRYPTONIX. Il faut Python 3.12 installé (« Add python.exe to PATH » coché). Ferme l'application installée
avant de tester. En test, les données sont dans les dossiers `donnees` et `logs` du projet ; pour tout
réinstaller, supprime le dossier `.venv`.

## Modules « Jarvis » (nouveau)
Domotique Hue, agenda Google, mails, Discord/Instagram, téléphone Twilio, OBS, contrôle du PC, vision de l'écran,
navigateur piloté, hub de contenu, cockpit financier local, satellites multi-pièces, pont iPhone, serveur MCP,
agent délégué, gestes, modes IA local/hybride/qualité avec budget, personnalités, **46 effets sonores**.
Tout est expliqué dans [`docs/MODULES_JARVIS.md`](docs/MODULES_JARVIS.md). Panneau de contrôle : `http://127.0.0.1:5000/panneau`.

## Dépannage
| Symptôme | Solution |
|---|---|
| La voix reste robotique | La voix neuronale demande internet **et une horloge Windows à l'heure** (Microsoft vérifie l'heure). Clique sur « Écouter la voix » : l'état indique la voix réellement utilisée. |
| Windows : « a protégé votre ordinateur » | Application non signée (certificat payant) : *Informations complémentaires → Exécuter quand même*. |
| La préparation affiche une erreur | Bouton *Réessayer* (connexion coupée ou disque presque plein). |
| Un bug | Journaux dans `%APPDATA%\Kryptonix\logs\` (`console.log`). |

## Organisation du dossier
```
lancer.bat                   lance l'application en mode test (prépare Python tout seul)
app.py                       point d'entrée (fenêtre native, préparation, mises à jour)
noyau/                       cerveau, voix, sandbox, mémoire, météo, actualités, médias (oreille, vue), réponses douces...
interfaces/                  serveur web + pages (index.html, preparation.html)
installateur/kryptonix.iss   installateur Windows (Inno Setup)
kryptonix.spec               construction du .exe (PyInstaller)
.github/workflows/           construction automatique dans le cloud
docs/                        page de téléchargement (GitHub Pages)
ressources/                  icône
```
