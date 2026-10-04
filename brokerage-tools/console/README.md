# The console

An Angular front end for the tool registry: browse the catalog, read a descriptor, call a tool
without a model, read the ontology and the producer graph, talk to the agent and watch which tools
it chose, and approve what needs approving.

```bash
npm install
npm run build          # into ../agent-service/src/main/resources/static
npm start              # dev server on :4200, proxying /api to :8080
```

`npm run build` writes straight into the agent service's static resources, so
`./mvnw -pl agent-service spring-boot:run` serves the console at <http://localhost:8080/>.

Four pages, each answering a question that is otherwise hard to answer:

| Page | The question |
| --- | --- |
| **Catalog** | What is the model actually being shown, and what happens if I call it myself? |
| **Ontology** | What is this catalog *supposed* to cover, and what has to happen before what? |
| **Agent** | Which tools did it choose for that answer, and why those? |
| **Governance** | Does the catalog conform, what is the system prompt today, who called what? |

The chat page needs `ANTHROPIC_API_KEY` set on the service. Everything else works without it, which
is deliberate: a tool catalog you can only exercise through a language model is a catalog you
cannot debug.
