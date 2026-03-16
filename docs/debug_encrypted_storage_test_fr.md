# Débogage des tests unitaires : `encrypted_storage_test.py`

Ce document explique comment déboguer les tests unitaires dans `encrypted_storage_test.py`, en particulier lorsque vous rencontrez des problèmes où `self.storage.download_blobs(srcs, dest)` semble appeler `_download_blob` (téléchargement standard) au lieu de `_download_encrypted_blobs` (téléchargement chiffré).

## 1. Pourquoi `download_blobs` n'appelle pas `_download_encrypted_blobs` ?

Dans `medusa/storage/abstract_storage.py`, la méthode `download_blobs` choisit la méthode de téléchargement à utiliser en vérifiant la configuration :

```python
def download_blobs(self, srcs, dest):
    loop = self.get_or_create_event_loop()
    # Condition cruciale ici :
    if hasattr(self.config, 'key_secret_base64') and self.config.key_secret_base64:
        loop.run_until_complete(self._download_encrypted_blobs(srcs, dest))
    else:
        loop.run_until_complete(self._download_blobs(srcs, dest))
```

Si le flux passe par `_download_blobs` (ou `_download_blob` en interne), c'est parce que **l'attribut `key_secret_base64` de l'objet `self.storage.config` est soit absent, soit vide (ou égal à `None`)**.

**Comment vérifier dans votre test ?**
Ajoutez simplement un `print` avant l'appel à `download_blobs` :

```python
print(f"Configuration de la clé : {getattr(self.storage.config, 'key_secret_base64', 'ATTRIBUT MANQUANT')}")
self.storage.download_blobs(srcs, dest)
```

Dans `encrypted_storage_test.py`, le `mock_config` est censé avoir cet attribut défini dans la méthode `setUp` :

```python
def setUp(self):
    self.key = base64.b64encode(os.urandom(32)).decode('utf-8')
    config_dict = {
        'storage_provider': 'mock',
        'bucket_name': 'test_bucket',
        'concurrent_transfers': '1',
        'key_secret_base64': self.key, # Vérifiez que ceci est bien présent !
        'encryption_tmp_dir': None
    }
    # ...
```

Assurez-vous que votre test n'écrase pas cette valeur de configuration par inadvertance.

## 2. Commandes utiles pour lancer et déboguer les tests

L'environnement de test utilise `poetry` et `pytest`. Pour que les tests de chiffrement s'exécutent, la dépendance optionnelle `aws-encryption-sdk` doit être installée.

### Installation des dépendances pour le test

Si vous n'avez pas installé les dépendances requises pour le chiffrement, les tests de `encrypted_storage_test.py` seront ignorés (à cause du décorateur `@unittest.skipIf(not HAS_AWS_CRYPT, ...)`).

```bash
# Installer toutes les dépendances de test, y compris celles pour l'environnement de chiffrement
poetry install --with test -E encryption
```

### Exécution basique d'un test spécifique

Pour lancer uniquement le fichier de test :
```bash
poetry run pytest tests/storage/encrypted_storage_test.py
```

Pour lancer une seule méthode de test (ex: `test_download_encrypted_blobs`) :
```bash
poetry run pytest tests/storage/encrypted_storage_test.py::EncryptedStorageTest::test_download_encrypted_blobs
```

### Affichage des logs et des impressions (prints)

Par défaut, `pytest` capture les `print` et les logs, et ne les affiche que si le test échoue. Pour forcer l'affichage de vos `print` et voir en temps réel ce qu'il se passe, ajoutez l'option `-s` :

```bash
poetry run pytest -s tests/storage/encrypted_storage_test.py
```

### Utilisation de PDB (Python Debugger) pour analyser le code pas à pas

Si vous voulez mettre le code en pause à l'endroit exact où la décision est prise, vous pouvez utiliser `pdb`.

1. Ajoutez `import pdb; pdb.set_trace()` (ou simplement `breakpoint()` en Python 3.7+) dans `medusa/storage/abstract_storage.py` :

```python
def download_blobs(self, srcs, dest):
    loop = self.get_or_create_event_loop()
    breakpoint() # L'exécution va s'arrêter ici
    if hasattr(self.config, 'key_secret_base64') and self.config.key_secret_base64:
        loop.run_until_complete(self._download_encrypted_blobs(srcs, dest))
    # ...
```

2. Lancez `pytest` en désactivant la capture de sortie de console (très important avec `pdb`) :
```bash
poetry run pytest -s tests/storage/encrypted_storage_test.py
```

3. Une fois l'exécution en pause dans la console, vous pouvez inspecter l'état :
- Tapez `self.config` pour voir l'objet de configuration.
- Tapez `hasattr(self.config, 'key_secret_base64')` pour vérifier si l'attribut existe.
- Tapez `self.config.key_secret_base64` pour afficher la valeur.
- Tapez `c` (continue) pour reprendre l'exécution, ou `n` (next) pour avancer ligne par ligne.

### Utilisation de l'outil de débogage sous Visual Studio Code (Windows)

Si vous utilisez Visual Studio Code (VS Code) sous Windows, voici la procédure détaillée pour configurer l'environnement de débogage avec Poetry et Pytest.

**Étape 1 : Sélectionner l'interpréteur Python (Poetry)**
Pour que VS Code reconnaisse vos dépendances, vous devez sélectionner l'environnement virtuel créé par Poetry :
1. Ouvrez un terminal dans VS Code (`Ctrl` + `\``) et tapez : `poetry env info --path`. Copiez le chemin affiché (ex: `C:\Users\VotreNom\AppData\Local\pypoetry\Cache\virtualenvs\medusa-...`).
2. Ouvrez la palette de commandes de VS Code (`Ctrl` + `Shift` + `P`).
3. Tapez et sélectionnez **Python: Select Interpreter**.
4. Cliquez sur **Enter interpreter path...** -> **Find...** et naviguez jusqu'au dossier `Scripts` de l'environnement copié (ex: `...\Scripts\python.exe`), puis sélectionnez-le.

**Étape 2 : Configurer les tests avec Pytest**
1. Ouvrez l'onglet de Test (icône de fiole sur la barre latérale gauche).
2. Cliquez sur **Configure Python Tests**.
3. Sélectionnez **pytest** comme framework de test.
4. Sélectionnez la racine de votre projet ou le dossier `tests/`.

**Étape 3 : Créer une configuration de débogage (`launch.json`)**
Si vous souhaitez lancer le fichier de test directement via le débogueur :
1. Allez dans l'onglet **Run and Debug** (icône de lecture avec un insecte) et cliquez sur **create a launch.json file**, ou bien créez le dossier `.vscode` à la racine de votre projet et ajoutez-y un fichier `launch.json`.
2. Ajoutez la configuration suivante :
```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Debug pytest (Medusa)",
            "type": "python",
            "request": "launch",
            "module": "pytest",
            "args": [
                "tests/storage/encrypted_storage_test.py",
                "-v",
                "-s"
            ],
            "console": "integratedTerminal",
            "justMyCode": false
        }
    ]
}
```

**Étape 4 : Déboguer le test**
1. Ouvrez le fichier `medusa/storage/abstract_storage.py`.
2. Allez à la méthode `download_blobs`.
3. Cliquez à gauche du numéro de ligne (ex: la ligne `if hasattr(self.config, 'key_secret_base64')...`) pour ajouter un **point d'arrêt** (un cercle rouge va apparaître).
4. Allez dans l'onglet **Run and Debug** et cliquez sur le bouton vert **Play** avec la configuration `Debug pytest (Medusa)`.
5. Le test se lancera et l'exécution s'arrêtera sur le point rouge.
6. Utilisez l'onglet **Variables** en haut à gauche pour inspecter `self.config`. Vous pourrez y voir les attributs et comprendre pourquoi `key_secret_base64` est manquant ou invalide.

## 3. Remarque importante sur le comportement de _download_encrypted_blobs

Même si le code passe bien par `_download_encrypted_blobs`, certains fichiers (les métadonnées en texte brut) appelleront délibérément `_download_blob` pour un téléchargement standard, sans essayer de les déchiffrer.

Vérifiez dans `_download_encrypted_blob` :
```python
if PLAINTEXT_FILES_REGEX.match(src_path.name):
    await self._download_blob(src, dest)
    return
```

Si le nom de votre fichier correspond à `manifest.*\.json`, `schema.*\.cql`, etc., il est tout à fait normal qu'il contourne le déchiffrement et appelle `_download_blob` ! Assurez-vous que le chemin du blob que vous testez (ex: `backup/data/restored.txt`) ne correspond pas à une expression régulière de métadonnées.
