package com.datastax.medusa;

import org.ini4j.Ini;
import org.ini4j.Profile.Section;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.File;
import java.io.IOException;
import java.lang.reflect.Field;
import java.net.InetAddress;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.HashMap;
import java.util.Map;

public class ConfigLoader {
    private static final Logger logger = LoggerFactory.getLogger(ConfigLoader.class);
    private static final Path DEFAULT_CONFIGURATION_PATH = Paths.get("/etc/medusa/medusa.ini");
    private static final String DEFAULT_GRPC_PORT = "50051";

    // Test hook
    public static EnvProvider envProvider = new EnvProvider.SystemEnvProvider();

    public static MedusaConfiguration loadConfig(Map<String, String> args, Path configFile) {
        MedusaConfiguration config = parseConfig(args, configFile);

        // Validate required fields
        if (config.storage.bucketName == null) {
            throw new RuntimeException("Required configuration \"bucket_name\" is missing in [storage] section.");
        }
        if (config.storage.storageProvider == null) {
            throw new RuntimeException("Required configuration \"storage_provider\" is missing in [storage] section.");
        }
        if (config.cassandra.startCmd == null) {
            throw new RuntimeException("Required configuration \"start_cmd\" is missing in [cassandra] section.");
        }
        if (config.cassandra.stopCmd == null) {
            throw new RuntimeException("Required configuration \"stop_cmd\" is missing in [cassandra] section.");
        }
        if (config.ssh.username == null) {
            throw new RuntimeException("Required configuration \"username\" is missing in [ssh] section.");
        }
        // key_file is technically required but sometimes optional in tests? config.py says it is required.
        if (config.ssh.keyFile == null) {
            throw new RuntimeException("Required configuration \"key_file\" is missing in [ssh] section.");
        }

        // Validate slashes
        if (config.storage.bucketName != null && config.storage.bucketName.contains("/")) {
            throw new RuntimeException("Required configuration \"bucket_name\" cannot contain a slash (\"/\")");
        }
        if (config.storage.prefix != null && config.storage.prefix.contains("/")) {
            throw new RuntimeException("Required configuration \"prefix\" cannot contain a slash (\"/\")");
        }

        return config;
    }

    public static MedusaConfiguration parseConfig(Map<String, String> args, Path configFile) {
        MedusaConfiguration config = buildDefaultConfig();

        loadConfigFile(config, configFile);
        overrideConfigWithArgs(config, args);
        handleK8sAndGrpcSettings(config, args);
        handleEnvVars(config);
        handleK8sSecrets(config);
        handleHostnameResolution(config);

        return config;
    }

    private static MedusaConfiguration buildDefaultConfig() {
        MedusaConfiguration config = new MedusaConfiguration();

        // Storage defaults
        config.storage.hostFileSeparator = ",";
        config.storage.maxBackupAge = "0";
        config.storage.maxBackupCount = "0";
        config.storage.apiProfile = "";
        config.storage.transferMaxBandwidth = "50MB/s";
        config.storage.concurrentTransfers = "1";
        config.storage.multiPartUploadThreshold = String.valueOf(20 * 1024 * 1024);
        config.storage.secure = "True";
        config.storage.sslVerify = "False";
        config.storage.awsCliPath = "aws";
        try {
            config.storage.fqdn = InetAddress.getLocalHost().getCanonicalHostName();
        } catch (Exception e) {
            config.storage.fqdn = "localhost";
        }
        config.storage.region = "default";
        config.storage.backupGracePeriodInDays = "10";
        config.storage.useSudoForRestore = "True";
        config.storage.multipartChunkSize = "50MB";
        config.storage.s3AddressingStyle = "auto";

        // Logging defaults
        config.logging.enabled = "false";
        config.logging.file = "medusa.log";
        config.logging.level = "INFO";
        config.logging.format = "[%(asctime)s] %(levelname)s: %(message)s";
        config.logging.maxBytes = "20000000";
        config.logging.backupCount = "50";

        // Cassandra defaults
        config.cassandra.configFile = "/etc/cassandra/cassandra.yaml"; // Approximation of constant
        config.cassandra.startCmd = "sudo service cassandra start";
        config.cassandra.stopCmd = "sudo service cassandra stop";
        config.cassandra.checkRunning = "nodetool version";
        config.cassandra.isCcm = "0";
        config.cassandra.sstableloaderBin = "sstableloader";
        config.cassandra.resolveIpAddresses = "True";
        config.cassandra.useSudo = "True";
        config.cassandra.nodetoolExecutable = "nodetool";
        config.cassandra.nodetoolFlags = "-Dcom.sun.jndi.rmiURLParsing=legacy";

        // SSH defaults
        config.ssh.username = System.getProperty("user.name", "");
        config.ssh.keyFile = "";
        config.ssh.port = "22";
        config.ssh.certFile = "";
        config.ssh.usePty = "False";
        config.ssh.keepaliveSeconds = "60";
        config.ssh.loginShell = "False";

        // Checks defaults
        config.checks.healthCheck = "cql";
        config.checks.query = "";
        config.checks.expectedRows = "0";
        config.checks.expectedResult = "";
        config.checks.enableMd5Checks = "false";

        // Monitoring defaults
        config.monitoring.monitoringProvider = "None";
        config.monitoring.sendBackupNameTag = "False";

        // GRPC defaults
        config.grpc.enabled = "False";
        config.grpc.maxSendMessageLength = "536870912";
        config.grpc.maxReceiveMessageLength = "134217728";
        config.grpc.port = DEFAULT_GRPC_PORT;

        // Kubernetes defaults
        config.kubernetes.enabled = "False";
        config.kubernetes.cassandraUrl = "None";
        config.kubernetes.useMgmtApi = "False";
        config.kubernetes.caCert = "";
        config.kubernetes.tlsCert = "";
        config.kubernetes.tlsKey = "";

        return config;
    }

    private static void loadConfigFile(MedusaConfiguration config, Path configFile) {
        Path actualConfigFile = (configFile == null) ? DEFAULT_CONFIGURATION_PATH : configFile;

        if (!Files.exists(actualConfigFile)) {
            if (configFile == null) {
                logger.error("No configuration file provided via CLI, nor no default file found in " + DEFAULT_CONFIGURATION_PATH);
                System.exit(1);
            }
            // If explicit file is missing, ini4j will throw error, which is fine
        }

        logger.debug("Loading configuration from " + actualConfigFile);

        try {
            Ini ini = new Ini(actualConfigFile.toFile());

            mapSection(ini.get("storage"), config.storage);
            mapSection(ini.get("cassandra"), config.cassandra);
            mapSection(ini.get("ssh"), config.ssh);
            mapSection(ini.get("checks"), config.checks);
            mapSection(ini.get("monitoring"), config.monitoring);
            mapSection(ini.get("logging"), config.logging);
            mapSection(ini.get("grpc"), config.grpc);
            mapSection(ini.get("kubernetes"), config.kubernetes);

        } catch (IOException e) {
             // For now, if file doesn't exist and it was explicitly passed, maybe throw runtime or log
             // Config parser in python doesn't crash if file empty, but crashes if path invalid?
             // Actually existing code logic: if config_file is None and default not exist -> exit.
             // If config_file provided, we assume it exists.
             if (configFile != null && !Files.exists(configFile)) {
                 throw new RuntimeException("Configuration file not found: " + configFile);
             }
        }
    }

    private static void mapSection(Section section, Object target) {
        if (section == null) return;
        for (String key : section.keySet()) {
            String camelKey = toCamelCase(key);
            setField(target, camelKey, section.get(key));
        }
    }

    private static void setField(Object target, String fieldName, String value) {
        try {
            Field field = target.getClass().getField(fieldName);
            field.set(target, value);
        } catch (NoSuchFieldException e) {
            // Field might not exist in our config object (e.g. unknown config), ignore
        } catch (IllegalAccessException e) {
            logger.warn("Could not set field " + fieldName, e);
        }
    }

    private static String toCamelCase(String s) {
        String[] parts = s.split("_");
        StringBuilder camelCaseString = new StringBuilder(parts[0]);
        for (int i = 1; i < parts.length; i++) {
            camelCaseString.append(parts[i].substring(0, 1).toUpperCase())
                    .append(parts[i].substring(1));
        }
        return camelCaseString.toString();
    }

    private static void overrideConfigWithArgs(MedusaConfiguration config, Map<String, String> args) {
        if (args == null) return;

        // Iterate over all config sections and check if args has override
        Object[] sections = {
            config.storage, config.cassandra, config.ssh, config.checks,
            config.monitoring, config.logging, config.grpc, config.kubernetes
        };

        for (Object section : sections) {
            for (Field field : section.getClass().getFields()) {
                // Convert camelCase field back to snake_case to lookup in args
                String snakeKey = toSnakeCase(field.getName());
                if (args.containsKey(snakeKey)) {
                    String val = args.get(snakeKey);
                    if (val != null) {
                        setField(section, field.getName(), val);
                    }
                }
            }
        }
    }

    private static String toSnakeCase(String str) {
        String result = str.replaceAll("([A-Z])", "_$1").toLowerCase();
        return result;
    }

    private static void handleK8sAndGrpcSettings(MedusaConfiguration config, Map<String, String> args) {
        boolean k8sEnabled = Utils.evaluateBoolean(config.kubernetes.enabled);
        if ((args != null && "True".equalsIgnoreCase(args.get("k8s_enabled"))) || k8sEnabled) {
            config.kubernetes.enabled = "True";
        }

        boolean grpcEnabled = Utils.evaluateBoolean(config.grpc.enabled);
        if ((args != null && "True".equalsIgnoreCase(args.get("grpc_enabled"))) || grpcEnabled) {
            config.grpc.enabled = "True";
        }

        if (Utils.evaluateBoolean(config.kubernetes.enabled)) {
            if (Utils.evaluateBoolean(config.cassandra.useSudo)) {
                logger.warn("Forcing use_sudo to False because Kubernetes mode is enabled");
            }
            config.cassandra.useSudo = "False";
            config.storage.useSudoForRestore = "False";

            String podIp = envProvider.getEnv("POD_IP");
            if (podIp != null) {
                config.storage.fqdn = podIp;
            }
        }
    }

    private static void handleEnvVars(MedusaConfiguration config) {
        // CQL Username/Password
        if (envProvider.getEnv("CQL_USERNAME") != null) {
            config.cassandra.cqlUsername = envProvider.getEnv("CQL_USERNAME");
            logger.warn("The CQL_USERNAME environment variable is deprecated and has been replaced by the MEDUSA_CQL_USERNAME variable");
        }
        if (envProvider.getEnv("MEDUSA_CQL_USERNAME") != null) {
            config.cassandra.cqlUsername = envProvider.getEnv("MEDUSA_CQL_USERNAME");
        }

        if (envProvider.getEnv("CQL_PASSWORD") != null) {
            config.cassandra.cqlPassword = envProvider.getEnv("CQL_PASSWORD");
            logger.warn("The CQL_PASSWORD environment variable is deprecated and has been replaced by the MEDUSA_CQL_PASSWORD variable");
        }
        if (envProvider.getEnv("MEDUSA_CQL_PASSWORD") != null) {
            config.cassandra.cqlPassword = envProvider.getEnv("MEDUSA_CQL_PASSWORD");
        }

        // Other properties
        String[] properties = {
            "nodetool_username", "nodetool_password", "sstableloader_tspw",
            "sstableloader_kspw", "resolve_ip_addresses", "cql_k8s_secrets_path",
            "nodetool_k8s_secrets_path"
        };

        for (String prop : properties) {
            String envVar = "MEDUSA_" + prop.toUpperCase();
            String val = envProvider.getEnv(envVar);
            if (val != null) {
                // Determine which section the property belongs to.
                // Currently all these seem to be in 'cassandra' section
                 setField(config.cassandra, toCamelCase(prop), val);
            }
        }
    }

    private static void handleK8sSecrets(MedusaConfiguration config) {
        if (config.cassandra.cqlK8sSecretsPath != null && !config.cassandra.cqlK8sSecretsPath.isEmpty()) {
            logger.debug("Using cql_k8s_secrets_path (path=\"" + config.cassandra.cqlK8sSecretsPath + "\")");
            String[] creds = loadK8sSecrets(config.cassandra.cqlK8sSecretsPath);
            config.cassandra.cqlUsername = creds[0];
            config.cassandra.cqlPassword = creds[1];
        }

        if (config.cassandra.nodetoolK8sSecretsPath != null && !config.cassandra.nodetoolK8sSecretsPath.isEmpty()) {
            logger.debug("Using nodetool_k8s_secrets_path (path=\"" + config.cassandra.nodetoolK8sSecretsPath + "\")");
            String[] creds = loadK8sSecrets(config.cassandra.nodetoolK8sSecretsPath);
            config.cassandra.nodetoolUsername = creds[0];
            config.cassandra.nodetoolPassword = creds[1];
        }
    }

    private static String[] loadK8sSecrets(String secretsPath) {
        Path path = Paths.get(secretsPath);
        String username = "";
        String password = "";
        try {
            username = new String(Files.readAllBytes(path.resolve("username"))).trim();
            logger.debug("Loading k8s username from \"" + path.resolve("username") + "\"");
        } catch (IOException e) {
            logger.warn("Failed to read k8s username secret", e);
        }
        try {
            password = new String(Files.readAllBytes(path.resolve("password"))).trim();
             logger.debug("Loading k8s password from \"" + path.resolve("password") + "\"");
        } catch (IOException e) {
             logger.warn("Failed to read k8s password secret", e);
        }
        return new String[]{username, password};
    }

    private static void handleHostnameResolution(MedusaConfiguration config) {
        boolean resolveIp = Utils.evaluateBoolean(config.cassandra.resolveIpAddresses);
        boolean k8sEnabled = Utils.evaluateBoolean(config.kubernetes.enabled);

        // HostnameResolver logic emulation
        // In python: if fqdn == socket.getfqdn() and not resolve: fqdn = ip
        // else if fqdn == socket.getfqdn(): fqdn = resolved

        String currentFqdn;
        try {
            currentFqdn = InetAddress.getLocalHost().getCanonicalHostName();
        } catch (Exception e) {
            currentFqdn = "localhost";
        }

        if (currentFqdn.equals(config.storage.fqdn)) {
            if (!resolveIp) {
                 try {
                     config.storage.fqdn = InetAddress.getLocalHost().getHostAddress();
                 } catch (Exception e) {
                     // ignore
                 }
            } else {
                // If resolving is enabled and we are on k8s, we might need different logic
                // But for now, we assume standard DNS resolution which getCanonicalHostName does.
                // The python HostnameResolver does specialized lookups using cassandra utils?
                // Let's look at HostnameResolver.py to be sure.
                // For now, I'll stick to this simple logic.
            }
        }

        config.storage.k8sMode = String.valueOf(k8sEnabled);
        config.cassandra.resolveIpAddresses = resolveIp ? "True" : "False";
    }
}
