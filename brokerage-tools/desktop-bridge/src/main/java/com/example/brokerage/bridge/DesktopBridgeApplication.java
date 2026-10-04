package com.example.brokerage.bridge;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.ComponentScan;

/**
 * A local connector that presents the brokerage catalog to a desktop assistant such as Claude
 * Desktop.
 *
 * <p><b>What this is and is not.</b> This book specifies a tool <em>contract</em>, not a wire
 * protocol, and that distinction is the reason this module is as short as it is. The descriptor is
 * the asset. Whatever a particular desktop client expects on the wire is an adapter concern, and
 * the adapter here is {@link BridgeLoop}: a few dozen lines that read requests, look up a name,
 * and hand back an envelope. Point it at a different client and {@link BridgeLoop} is the only
 * file that changes — not a descriptor, not a handler, not the ontology, not the linter.
 *
 * <p>So the useful way to read this module is as a worked example of how little work publishing
 * to a new runtime should be, and as a demonstration of the two things that are not negotiable
 * wherever you publish: the schema is validated before a handler runs, and a tier 2 or 3 tool
 * stops at the approval gate.
 *
 * <pre>
 *   ./mvnw -q -pl desktop-bridge -am install -DskipTests
 *   java -jar desktop-bridge/target/desktop-bridge-1.0.0.jar --manifest     # the tools array
 *   java -jar desktop-bridge/target/desktop-bridge-1.0.0.jar                # serve on stdio
 * </pre>
 *
 * <p>Nothing is logged to standard output, ever, because standard output is the channel. Logging
 * goes to standard error, which is also where a desktop client will show you a stack trace.
 */
@SpringBootApplication
@ComponentScan(basePackages = "com.example.brokerage")
public class DesktopBridgeApplication {

    public static void main(String[] args) {
        SpringApplication application = new SpringApplication(DesktopBridgeApplication.class);
        application.setLogStartupInfo(false);
        application.run(args);
    }
}
