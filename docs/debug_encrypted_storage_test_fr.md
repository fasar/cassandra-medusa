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

### Utilisation de l'outil de débogage d'un IDE (VSCode, PyCharm)

Si vous utilisez un IDE comme VSCode ou PyCharm :
1. Placez un point d'arrêt (breakpoint) visuel sur la ligne `if hasattr(self.config, 'key_secret_base64')` dans `medusa/storage/abstract_storage.py`.
2. Lancez le test en mode **Debug** depuis l'interface de votre IDE.
3. Inspectez les variables locales (`self.config`) dans le panneau d'inspection pour comprendre pourquoi la condition n'est pas remplie.

## 3. Remarque importante sur le comportement de _download_encrypted_blobs

Même si le code passe bien par `_download_encrypted_blobs`, certains fichiers (les métadonnées en texte brut) appelleront délibérément `_download_blob` pour un téléchargement standard, sans essayer de les déchiffrer.

Vérifiez dans `_download_encrypted_blob` :
```python
if PLAINTEXT_FILES_REGEX.match(src_path.name):
    await self._download_blob(src, dest)
    return
```

Si le nom de votre fichier correspond à `manifest.*\.json`, `schema.*\.cql`, etc., il est tout à fait normal qu'il contourne le déchiffrement et appelle `_download_blob` ! Assurez-vous que le chemin du blob que vous testez (ex: `backup/data/restored.txt`) ne correspond pas à une expression régulière de métadonnées.
