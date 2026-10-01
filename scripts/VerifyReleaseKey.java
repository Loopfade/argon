import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.Signature;
import java.security.cert.X509Certificate;

/** Validate the actual release key without building an APK or exporting it. */
class VerifyReleaseKey {
    public static void main(String[] args) {
        try {
            var store = KeyStore.getInstance("PKCS12");
            try (var input = Files.newInputStream(Path.of(args[0]))) {
                store.load(input, System.getenv("TITANIUM_RU_STORE_PASSWORD").toCharArray());
            }
            var alias = System.getenv("TITANIUM_RU_KEY_ALIAS");
            var key = store.getKey(alias,
                    System.getenv("TITANIUM_RU_KEY_PASSWORD").toCharArray());
            if (!(key instanceof PrivateKey)) {
                throw new IllegalArgumentException("Alias must contain a private key");
            }
            var certificate = (X509Certificate) store.getCertificate(alias);
            certificate.checkValidity();
            var algorithm = switch (key.getAlgorithm()) {
                case "RSA" -> "SHA256withRSA";
                case "EC" -> "SHA256withECDSA";
                case "DSA" -> "SHA256withDSA";
                default -> throw new IllegalArgumentException("Unsupported signing key");
            };
            var challenge = new byte[32];
            new java.security.SecureRandom().nextBytes(challenge);
            var signer = Signature.getInstance(algorithm);
            signer.initSign((PrivateKey) key);
            signer.update(challenge);
            var signature = signer.sign();
            signer.initVerify(certificate.getPublicKey());
            signer.update(challenge);
            if (!signer.verify(signature)) {
                throw new IllegalArgumentException("Key and certificate do not match");
            }
            System.out.println("Release PKCS12 private key and certificate verified.");
        } catch (Exception error) {
            // Provider exceptions may contain user-supplied values. Do not log them.
            System.err.println("Release key validation failed: check PKCS12, passwords, "
                    + "alias and certificate validity.");
            System.exit(1);
        }
    }
}
