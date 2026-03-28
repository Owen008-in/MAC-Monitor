# MAC Monitor Pro — Guide

Un robot animé dans la barre de menu macOS qui surveille ton système en temps réel.

---

## Installation

```bash
bash install.sh
```

Le script installe automatiquement Homebrew, Python, les dépendances, et configure le démarrage automatique.

---

## Le robot

L'icône dans la barre de menu est un robot animé dont la couleur change selon l'état du CPU :

| Couleur | État |
|---|---|
| 🟢 Vert | CPU < 35% — au repos |
| 🔵 Bleu | CPU 35–60% — occupé |
| 🟠 Orange | CPU 60–80% — chaud |
| 🔴 Rouge | CPU > 80% — surchargé |

Le titre à côté affiche le CPU % et la vitesse de téléchargement en temps réel.

---

## Les onglets

Cliquer sur le robot ouvre un panel avec 4 onglets.

### Système

- **CPU** — usage global + sparkline 60s + barre + fréquence + température
- **GPU** — usage GPU (si disponible)
- **RAM** — usage + pression mémoire macOS (Normal / Avertissement / Critique)
- **Batterie** — pourcentage + barre + temps restant + santé (%)
- **Fuseaux horaires** — UTC · NY · Tokyo en bas de la card

### Réseau

- **Vitesse temps réel** — téléchargement ↓ et upload ↑ mis à jour chaque seconde
- **Sparklines** — historique 60s des vitesses
- **WiFi** — SSID + barres de signal + ping vers 8.8.8.8
- **Totaux** — données téléchargées/envoyées depuis le démarrage
- **VPN** — affiche les VPNs actifs (WireGuard, Tailscale, OpenVPN…)
- **Stockage** — usage disque / + vitesses lecture/écriture

### Agenda

- **Calendrier** — 5 prochains événements sur 5 jours (Google Calendar ou Apple Calendar)
- **Musique** — titre et artiste en cours (Apple Music ou Spotify) + boutons ◀◀ ⏯ ▶▶
- **Météo** — conditions actuelles via wttr.in (géolocalisation automatique par IP)

### Process

- **Top processus** — 10 processus les plus gourmands, clic sur l'en-tête pour basculer CPU ↔ RAM
- **Boutons d'action** (en bas) :

| Bouton | Fonction |
|---|---|
| 💤 Veille | Active/désactive l'anti-veille (caffeinate) |
| ⏰ Pomo | Lance un timer Pomodoro de 25 minutes |
| 📋 Copier | Copie un snapshot complet des stats dans le presse-papiers |
| 🔒 Verrou | Verrouille l'écran (Cmd+Ctrl+Q) |
| Quitter | Ferme MAC Monitor |

---

## Notifications automatiques

Le monitor envoie des notifications macOS dans ces situations :

| Déclencheur | Seuil |
|---|---|
| CPU surchargé | > 90% |
| RAM saturée | > 90% |
| Batterie faible | ≤ 10% sans charge |
| Température CPU | > 90°C |
| Téléchargement terminé | Vitesse redescend sous 100 KB/s après un pic |
| Réunion imminente | 15 min avant, puis 5 min avant |

---

## Header

- **Heure et date** — en haut à gauche, mise à jour chaque seconde
- **Focus macOS** — quand un mode Focus est actif, "Pro" est remplacé par 🎯 DND
- **× (croix)** — ferme le panel sans quitter l'app
- **Barre CPU** — fine barre colorée sous le header, reflet instantané du CPU

---

## Google Calendar

Par défaut le calendrier utilise Apple Calendar. Pour connecter Google Calendar :

1. Lancer `gcalcli --config-folder ~/.config/gcalcli init`
2. Entrer ton `client_id` et `client_secret` Google (Google Cloud Console → API Calendar → Credentials OAuth Desktop)
3. S'authentifier dans le navigateur

Une fois configuré, les événements Google apparaissent automatiquement dans l'onglet Agenda.

---

## Démarrage automatique

L'app démarre automatiquement à chaque connexion via un LaunchAgent macOS.

```bash
# Vérifier l'état
launchctl list com.macmonitor.app

# Arrêter
launchctl unload ~/Library/LaunchAgents/com.macmonitor.app.plist

# Redémarrer
launchctl load -w ~/Library/LaunchAgents/com.macmonitor.app.plist

# Logs
tail -f /tmp/macmonitor.log
tail -f /tmp/macmonitor.err
```

L'app **MAC Monitor.app** dans `/Applications` sert aussi de bouton on/off.

---

## Fichiers

```
MAC/
├── menubar_monitor.py   — code principal
├── install.sh           — installation complète
├── requirements.txt     — dépendances Python
└── GUIDE.md             — ce fichier
```

---

## Dépendances

| Package | Rôle |
|---|---|
| `rumps` | Framework menubar macOS |
| `psutil` | Métriques système (CPU, RAM, réseau, disque) |
| `pyobjc-core` | Bridge Python → Objective-C |
| `pyobjc-framework-Cocoa` | APIs macOS (NSView, NSColor, NSTimer…) |
| `gcalcli` | Google Calendar (optionnel, via pipx) |
