package com.datastax.medusa;

import java.util.Properties;

public class MedusaConfiguration {
    public StorageConfig storage = new StorageConfig();
    public CassandraConfig cassandra = new CassandraConfig();
    public SSHConfig ssh = new SSHConfig();
    public ChecksConfig checks = new ChecksConfig();
    public MonitoringConfig monitoring = new MonitoringConfig();
    public LoggingConfig logging = new LoggingConfig();
    public GrpcConfig grpc = new GrpcConfig();
    public KubernetesConfig kubernetes = new KubernetesConfig();
    public String filePath;

    public static class StorageConfig {
        public String bucketName;
        public String keyFile;
        public String prefix;
        public String fqdn;
        public String hostFileSeparator;
        public String storageProvider;
        public String storageClass;
        public String basePath;
        public String maxBackupAge;
        public String maxBackupCount;
        public String apiProfile;
        public String transferMaxBandwidth;
        public String concurrentTransfers;
        public String multiPartUploadThreshold;
        public String multipartChunkSize;
        public String host;
        public String region;
        public String port;
        public String secure;
        public String sslVerify;
        public String awsCliPath;
        public String kmsId;
        public String sseCKey;
        public String backupGracePeriodInDays;
        public String useSudoForRestore;
        public String k8sMode;
        public String readTimeout;
        public String s3AddressingStyle;
    }

    public static class CassandraConfig {
        public String startCmd;
        public String stopCmd;
        public String configFile;
        public String cqlUsername;
        public String cqlPassword;
        public String checkRunning;
        public String isCcm;
        public String sstableloaderBin;
        public String nodetoolUsername;
        public String nodetoolPassword;
        public String nodetoolPasswordFilePath;
        public String nodetoolHost;
        public String nodetoolExecutable;
        public String nodetoolPort;
        public String certfile;
        public String usercert;
        public String userkey;
        public String sstableloaderTs;
        public String sstableloaderTspw;
        public String sstableloaderKs;
        public String sstableloaderKspw;
        public String nodetoolSsl;
        public String resolveIpAddresses;
        public String useSudo;
        public String nodetoolFlags;
        public String cqlK8sSecretsPath;
        public String nodetoolK8sSecretsPath;
    }

    public static class SSHConfig {
        public String username;
        public String keyFile;
        public String port;
        public String certFile;
        public String usePty;
        public String keepaliveSeconds;
        public String loginShell;
    }

    public static class ChecksConfig {
        public String healthCheck;
        public String query;
        public String expectedRows;
        public String expectedResult;
        public String enableMd5Checks;
    }

    public static class MonitoringConfig {
        public String monitoringProvider;
        public String sendBackupNameTag;
    }

    public static class LoggingConfig {
        public String enabled;
        public String file;
        public String format;
        public String level;
        public String maxBytes;
        public String backupCount;
    }

    public static class GrpcConfig {
        public String enabled;
        public String maxSendMessageLength;
        public String maxReceiveMessageLength;
        public String port;
        public String caCert;
        public String tlsCert;
        public String tlsKey;
    }

    public static class KubernetesConfig {
        public String enabled;
        public String cassandraUrl;
        public String useMgmtApi;
        public String caCert;
        public String tlsCert;
        public String tlsKey;
    }
}
