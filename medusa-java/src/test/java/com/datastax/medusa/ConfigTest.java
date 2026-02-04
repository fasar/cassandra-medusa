package com.datastax.medusa;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.HashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

class ConfigTest {

    private Path medusaConfigFile;
    private MockEnvProvider envProvider;

    @BeforeEach
    void setUp() {
        medusaConfigFile = Paths.get("src/test/resources/config/medusa.ini");
        envProvider = new MockEnvProvider();
        ConfigLoader.envProvider = envProvider;
    }

    @AfterEach
    void tearDown() {
        ConfigLoader.envProvider = new EnvProvider.SystemEnvProvider();
    }

    @Test
    void testNoAuthEnvVariables() {
        Map<String, String> args = new HashMap<>();
        MedusaConfiguration config = ConfigLoader.loadConfig(args, medusaConfigFile);

        assertEquals("test_cql_username", config.cassandra.cqlUsername);
        assertEquals("test_cql_password", config.cassandra.cqlPassword);
        assertEquals("test_nodetool_username", config.cassandra.nodetoolUsername);
        assertEquals("test_nodetool_password", config.cassandra.nodetoolPassword);
        assertEquals("test_ts_password", config.cassandra.sstableloaderTspw);
        assertEquals("test_ks_password", config.cassandra.sstableloaderKspw);
    }

    @Test
    void testDifferentAuthEnvVariables() {
        envProvider.put("MEDUSA_CQL_USERNAME", "different_cql_username");
        envProvider.put("MEDUSA_CQL_PASSWORD", "different_cql_password");
        envProvider.put("MEDUSA_NODETOOL_USERNAME", "different_nodetool_username");
        envProvider.put("MEDUSA_NODETOOL_PASSWORD", "different_nodetool_password");
        envProvider.put("MEDUSA_SSTABLELOADER_TSPW", "different_sstableloader_tspw");
        envProvider.put("MEDUSA_SSTABLELOADER_KSPW", "different_sstableloader_kspw");

        Map<String, String> args = new HashMap<>();
        MedusaConfiguration config = ConfigLoader.loadConfig(args, medusaConfigFile);

        assertEquals("different_cql_username", config.cassandra.cqlUsername);
        assertEquals("different_cql_password", config.cassandra.cqlPassword);
        assertEquals("different_nodetool_username", config.cassandra.nodetoolUsername);
        assertEquals("different_nodetool_password", config.cassandra.nodetoolPassword);
        assertEquals("different_sstableloader_tspw", config.cassandra.sstableloaderTspw);
        assertEquals("different_sstableloader_kspw", config.cassandra.sstableloaderKspw);
    }

    @Test
    void testNewEnvVariablesOverrideDeprecatedOnes() {
        envProvider.put("MEDUSA_CQL_USERNAME", "new_cql_username");
        envProvider.put("MEDUSA_CQL_PASSWORD", "new_cql_password");
        envProvider.put("CQL_USERNAME", "deprecated_cql_username");
        envProvider.put("CQL_PASSWORD", "deprecated_cql_password");

        Map<String, String> args = new HashMap<>();
        MedusaConfiguration config = ConfigLoader.loadConfig(args, medusaConfigFile);

        assertEquals("new_cql_username", config.cassandra.cqlUsername);
        assertEquals("new_cql_password", config.cassandra.cqlPassword);
    }

    @Test
    void testCqlK8sSecretsPathOverride() throws IOException {
        Path tmpDir = Files.createTempDirectory("secrets");
        envProvider.put("MEDUSA_CQL_K8S_SECRETS_PATH", tmpDir.toString());

        Files.write(tmpDir.resolve("username"), "k8s_username".getBytes());
        Files.write(tmpDir.resolve("password"), "k8s_password".getBytes());

        Map<String, String> args = new HashMap<>();
        MedusaConfiguration config = ConfigLoader.loadConfig(args, medusaConfigFile);

        assertEquals("k8s_username", config.cassandra.cqlUsername);
        assertEquals("k8s_password", config.cassandra.cqlPassword);

        // Cleanup
        Files.delete(tmpDir.resolve("username"));
        Files.delete(tmpDir.resolve("password"));
        Files.delete(tmpDir);
    }

    @Test
    void testArgsSettingsOverride() {
        Map<String, String> args = new HashMap<>();
        args.put("bucket_name", "Hector");
        args.put("cql_username", "Priam");
        args.put("enabled", "True");
        args.put("file", "hera.log");
        args.put("monitoring_provider", "local");
        args.put("query", "SELECT * FROM greek_mythology");
        args.put("use_mgmt_api", "True");
        args.put("username", "Zeus");
        args.put("fqdn", "localhost");

        MedusaConfiguration config = ConfigLoader.loadConfig(args, medusaConfigFile);

        assertEquals("Hector", config.storage.bucketName);
        assertEquals("Priam", config.cassandra.cqlUsername);
        assertEquals("hera.log", config.logging.file);
        assertEquals("local", config.monitoring.monitoringProvider);
        assertEquals("SELECT * FROM greek_mythology", config.checks.query);
        assertEquals("True", config.kubernetes.useMgmtApi);
        assertEquals("Zeus", config.ssh.username);
        assertEquals("localhost", config.storage.fqdn);
    }

    @Test
    void testUseSudoDefault() {
        Map<String, String> args = new HashMap<>();
        MedusaConfiguration config = ConfigLoader.loadConfig(args, medusaConfigFile);
        assertTrue(Utils.evaluateBoolean(config.cassandra.useSudo));
        assertFalse(Utils.evaluateBoolean(config.kubernetes.enabled));
    }

    @Test
    void testUseSudoKubernetesDisabled() {
        Map<String, String> args = new HashMap<>();
        args.put("use_sudo", "True");
        MedusaConfiguration config = ConfigLoader.parseConfig(args, medusaConfigFile);
        assertEquals("True", config.cassandra.useSudo);

        args.put("use_sudo", "False");
        config = ConfigLoader.parseConfig(args, medusaConfigFile);
        assertEquals("False", config.cassandra.useSudo);
    }

    @Test
    void testUseSudoKubernetesEnabled() {
        Map<String, String> args = new HashMap<>();
        args.put("use_sudo", "true");
        Path k8sConfig = Paths.get("src/test/resources/config/medusa-kubernetes.ini");
        MedusaConfiguration config = ConfigLoader.parseConfig(args, k8sConfig);
        assertEquals("False", config.cassandra.useSudo);
    }

    @Test
    void testSlashInBucketName() {
        Map<String, String> args = new HashMap<>();
        args.put("bucket_name", "bucket/name");

        Exception exception = assertThrows(RuntimeException.class, () -> {
            ConfigLoader.loadConfig(args, medusaConfigFile);
        });

        assertTrue(exception.getMessage().contains("bucket_name"));
    }

    static class MockEnvProvider implements EnvProvider {
        private Map<String, String> map = new HashMap<>();

        void put(String key, String value) {
            map.put(key, value);
        }

        @Override
        public String getEnv(String name) {
            return map.get(name);
        }
    }
}
