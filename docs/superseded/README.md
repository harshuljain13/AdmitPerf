# Superseded

These describe AdmitPerf as a benchmark harness that owns a cluster: engine
adapters, a provisioner, experiment configs, two run paths. That design is gone —
see the commit that removed it, and the issues linked from it.

They are kept rather than deleted because the reasoning in them is still the
reasoning behind the project, and because `motivation.md` holds the survey
numbers the whole thing rests on. Read them as history.

**Current design:** the System Design and Low-Level Design pages on the AdmitPerf
Notion project page, and the README.

What changed, in one line each:

| Then | Now |
|---|---|
| AdmitPerf provisions a cluster and drives load | bring your own infra; it never fetches, serves, or generates load |
| `SystemState`, a closed struct of vLLM fields | raw metrics in, `Signal` converts them |
| An engine adapter per serving stack | no engine knowledge at all |
| Free-text refusal reasons, no status code | controlled vocabulary, status derived from the reason |
| Only the decided-on signal recorded | every signal recorded, so a report can say which one moved |
