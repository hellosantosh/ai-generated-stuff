package com.example.brokerage.bridge;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;

import com.example.brokerage.domain.Accounts;
import com.example.brokerage.tooling.contract.DescriptorCodec;
import com.example.brokerage.tooling.contract.Principal;

import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;

/**
 * The transport: line-delimited JSON on standard input and output.
 *
 * <p>One JSON object per line in, one per line out, correlated by {@code id}:
 *
 * <pre>
 *   {"id":1,"method":"list_tools"}
 *   {"id":2,"method":"call_tool","params":{"name":"brokerage_accounts_list","arguments":{}}}
 *   {"id":3,"method":"approve","params":{"reference":"apr_..."}}
 * </pre>
 *
 * <p>This file is the entire adapter. Point the connector at a desktop client that expects
 * something else on the wire and this is what changes — not a descriptor, not a handler, not
 * the ontology, not the linter, not a test. The contract is the asset; the transport is a detail,
 * and keeping them in separate files is how that claim stays true rather than aspirational.
 *
 * <p>Nothing is written to standard output except protocol, ever, because standard output
 * <em>is</em> the channel. Logging goes to standard error, which is also where a desktop client
 * will show you a stack trace. It is the single most common way a connector fails on first run.
 */
@Component
public class BridgeLoop implements ApplicationRunner {

    private final BridgeProtocol protocol;

    public BridgeLoop(BridgeProtocol protocol) {
        this.protocol = protocol;
    }

    @Override
    public void run(ApplicationArguments args) throws Exception {
        boolean trading = args.containsOption("trading");
        Principal principal = new Principal("desktop:local", Accounts.DEMO_CUSTOMER,
                BridgeProtocol.entitlements(trading), "desktop-" + System.currentTimeMillis());

        if (args.containsOption("manifest")) {
            System.out.println(DescriptorCodec.mapper().writerWithDefaultPrettyPrinter()
                    .writeValueAsString(protocol.manifest(principal)));
            return;
        }
        if (args.containsOption("briefing")) {
            System.out.println(protocol.briefing(principal));
            return;
        }
        serve(principal);
    }

    private void serve(Principal principal) throws IOException {
        PrintStream out = new PrintStream(System.out, true, StandardCharsets.UTF_8);
        try (BufferedReader in = new BufferedReader(
                new InputStreamReader(System.in, StandardCharsets.UTF_8))) {
            String line;
            while ((line = in.readLine()) != null) {
                if (line.isBlank()) {
                    continue;
                }
                out.println(DescriptorCodec.json(protocol.handle(line, principal)));
            }
        }
    }
}
