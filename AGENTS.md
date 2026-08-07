# Instructions pour l'agent (Devin)

## Règle principale : SPECIFICATIONS.md est la source de vérité

`SPECIFICATIONS.md` (à la racine du projet) est le document de référence du projet. **Toute nouvelle fonctionnalité ou toute modification d'une fonctionnalité existante doit être répercutée dans `SPECIFICATIONS.md`.**

Workflow à suivre pour chaque demande de feature/modification :

1. **Mettre à jour `SPECIFICATIONS.md` d'abord** (ou en même temps) : ajouter/modifier la section concernée avec la nouvelle description du comportement attendu.
2. Ajouter une entrée dans la section **"Historique des décisions"** en fin de document (une ligne résumant le changement).
3. Implémenter le changement dans le code pour qu'il corresponde à la spec mise à jour.
4. Si le changement touche la configuration (`config.yaml`, `config.local.yaml`, `config.prompt.yaml`), mettre à jour aussi les fichiers `.example` correspondants.
5. Si le changement touche le guide d'installation ou la maintenance (nouvel outil, nouvelle dépendance), mettre à jour les sections 7/8 de `SPECIFICATIONS.md`.

Ne jamais implémenter un changement de comportement sans que `SPECIFICATIONS.md` le reflète — le code et les specs doivent toujours rester synchronisés.

## Vérification

- Après toute modification de code Python, vérifier la compilation : `py -m py_compile <fichiers modifiés>`.
- Pas de suite de tests automatisée pour l'instant.
