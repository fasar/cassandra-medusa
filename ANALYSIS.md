# Analyse des modifications

## 1. Explication pour un enfant de 7 ans

Imagine que tu as un coffre à jouets secret. Tu veux envoyer tes jouets chez ta grand-mère pour qu'elle les garde, mais tu ne veux pas que le facteur ou les voisins puissent voir ce qu'il y a dedans.

Avant, tu mettais tes jouets dans des boîtes en plastique transparent. Tout le monde pouvait voir que c'était ta voiture rouge préférée !

Maintenant, on a ajouté une machine magique. Avant de mettre le jouet dans la boîte, la machine l'enveloppe dans un papier cadeau spécial très solide. Personne ne peut le déchirer sans connaître la formule magique secrète.

Quand la boîte arrive chez grand-mère, elle utilise la formule magique pour ouvrir le papier cadeau et retrouver ton jouet exactement comme il était. Comme ça, pendant le voyage, personne n'a pu voir tes secrets !

## 2. Explication pour un développeur Java (15 ans d'expérience)

Cette branche implémente le **chiffrement côté client (Client-Side Encryption)** pour les sauvegardes Cassandra, en utilisant une approche symétrique avec **Fernet** (implémentation de AES-128-CBC avec signature HMAC-SHA256 pour l'intégrité, fournie par la librairie `cryptography`).

L'architecture a été modifiée selon le pattern Decorator/Middleware au niveau du stockage :

*   **EncryptionManager** : Une nouvelle composante qui gère le chiffrement de flux. Pour gérer les gros fichiers (SSTables) sans saturer la Heap/RAM, le chiffrement se fait par **chunks** (blocs). Chaque chunk chiffré est préfixé par sa taille, car Fernet ajoute un overhead (padding + signature) non constant ou du moins qui change la taille du bloc.
*   **Modèle de données (Manifest)** : C'est le point d'attention principal. Le chiffrement casse la correspondance directe entre le fichier local (plaintext) et l'objet stocké (cyphertext).
    *   Le `ManifestObject` a été étendu pour stocker deux jeux de métadonnées :
        1.  `size` / `MD5` : Correspondent à l'objet chiffré dans le Blob Storage (utile pour vérifier l'intégrité du stockage distant).
        2.  `source_size` / `source_MD5` : Correspondent au fichier original en clair.
*   **Sauvegardes Différentielles** : La logique de détection des fichiers existants (`check_already_uploaded`) a été patchée. Elle utilise désormais `source_MD5` pour comparer le fichier local (sur le disque) avec ce qui est déclaré dans le manifeste des backups précédents. Sans cela, le hash ne correspondrait jamais et on perdrait le bénéfice des sauvegardes incrémentales/différentielles.
*   **Performance** : Le chiffrement/déchiffrement utilise des fichiers temporaires sur le disque pour éviter de tout charger en mémoire, ce qui ajoute des I/O mais garantit la stabilité pour des nœuds avec beaucoup de données.

## 3. Détails du code ajouté

Voici les points clés des modifications techniques :

1.  **`medusa/storage/encryption.py`** :
    *   Classe `EncryptionManager` initialisée avec une clé secrète en base64.
    *   Méthode `encrypt_file` : Lit le fichier source par blocs (`CHUNK_SIZE`), chiffre chaque bloc avec `self.fernet.encrypt()`, et écrit `[taille_chunk_chiffré][chunk_chiffré]` dans le fichier destination. Elle calcule et retourne à la fois les hashs MD5 du fichier chiffré et du fichier source.
    *   Méthode `decrypt_file` : Fait l'inverse, en lisant la taille du bloc pour savoir combien d'octets lire et déchiffrer.

2.  **`medusa/storage/abstract_storage.py`** :
    *   La méthode `upload_blobs` vérifie si `key_secret_base64` est présent. Si oui, elle dérive vers `_upload_encrypted_blobs`.
    *   `_upload_encrypted_blobs` utilise un dossier temporaire pour chiffrer les fichiers avant de les uploader via la méthode standard. Elle construit ensuite le `ManifestObject` en peuplant les champs `source_size` et `source_MD5`.
    *   La méthode `download_blobs` fait l'inverse : télécharge dans un temp, déchiffre vers la destination finale.
    *   *Filtrage* : Les fichiers de métadonnées (`manifest.json`, `schema.cql`, `tokenmap.json`) sont exclus du chiffrement (via une regex) pour rester lisibles par Medusa sans clé (nécessaire pour lister les backups).

3.  **`medusa/backup_node.py`** :
    *   Dans `backup_snapshots`, si le chiffrement est actif, on récupère la liste des fichiers existants via `get_files_from_all_differential_backups()` (qui parse tous les manifestes précédents) plutôt que de lister simplement les objets du bucket S3. Cela permet d'avoir accès aux `source_MD5`.
    *   La fonction `check_already_uploaded` a été modifiée :
        ```python
        if item_in_storage.source_MD5:
             # Comparaison du fichier local avec le source_MD5 du manifeste
             # Si match -> on ne re-upload pas.
        ```

4.  **`medusa/storage/node_backup.py`** :
    *   Mise à jour pour supporter la lecture/écriture des nouveaux champs dans le JSON du manifeste.

## 4. Pull Request Description (English)

**Title:** feat(security): Add client-side encryption support using Fernet

**Description:**

This PR implements client-side encryption for Medusa backups, ensuring data confidentiality at rest in the storage backend. It uses the `cryptography` library with Fernet symmetric encryption.

**Key Features:**
*   **Transparent Encryption:** Files are encrypted on the node before upload and decrypted upon restoration.
*   **Chunk-based Processing:** Large SSTables are handled via streaming encryption/decryption using temporary files to minimize memory footprint.
*   **Differential Backup Support:** The manifest structure has been extended to track `source_MD5` and `source_size` (plaintext metadata). This allows Medusa to correctly identify unchanged files during differential backups even when the stored objects are encrypted.
*   **Metadata Exclusion:** Core metadata files (`manifest.json`, `schema.cql`, etc.) remain unencrypted to preserve indexability and allow backup listing without requiring the encryption key.

**Implementation Details:**
*   Added `medusa.storage.encryption.EncryptionManager` to handle Fernet operations.
*   Updated `AbstractStorage` to intercept uploads/downloads when `key_secret_base64` is configured.
*   Updated `NodeBackup` and `backup_node.py` logic to verify local files against `source_MD5` from previous manifests.
*   Added `source_size` and `source_MD5` fields to `ManifestObject`.

**Configuration:**
To enable, add the following to `medusa.ini`:
```ini
[storage]
key_secret_base64 = <your_fernet_key>
encryption_tmp_dir = /tmp  # Optional, defaults to system temp
```

**Testing:**
*   Added unit tests for `EncryptionManager`.
*   Added integration tests in `encrypted_storage_test.py` covering upload, download, and manifest aggregation.
*   Verified that plaintext files (like schema.cql) are correctly skipped during encryption/decryption.
