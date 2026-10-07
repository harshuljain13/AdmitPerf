"""Every term a report can print, defined once.

This exists because two pairs of terms cause nearly all the confusion in this area,
and in both cases the two members look interchangeable and are not:

  *offered* versus *admitted* as a denominator — the difference grows with the
  rejection rate, and picking the flattering one is how a policy gets credit for
  refusing more rather than refusing better.

  *inert* versus *unknown* — a signal that existed and never moved is a different
  finding from a policy that reads nothing. A baseline is the second, correctly.

Definitions marked `survey` are the survey's own wording, so the page explains the
concept rather than paraphrasing it into something subtly different.
"""

from __future__ import annotations

#: term -> (one-line gloss, why it matters, source)
TERMS: dict[str, tuple[str, str, str]] = {
    # --- the three classes --------------------------------------------
    "metrics": (
        "Whatever your stack exports, under its own names — an engine gauge, a GPU "
        "exporter, a counter of your own. See the Signals page for the ones AdmitPerf "
        "already reads.",
        "Yours, not AdmitPerf's. The package never fetches them; your gateway hands "
        "them over, which is what lets it sit in front of any engine.",
        "admitperf",
    ),
    "signal": (
        "A named quantity a policy decides on, together with the ways to read it out of "
        "your raw metrics — `kv_pressure`, `queue_depth`, `gpu_util`.",
        "The conversion lives in AdmitPerf rather than in each gateway. If every host "
        "computed `kv_pressure` itself, two of them reporting 0.93 would mean different "
        "things and the shared name would be worth nothing.",
        "admitperf",
    ),
    "policy": (
        "A function from metrics to a verdict: admit, defer, or reject.",
        "Yours or ours, treated identically — same base class, same log, same report. "
        "If built-ins were privileged, nobody would trust the standard.",
        "admitperf",
    ),
    # --- the structure of a measurement ------------------------------
    "experiment": (
        "A named question, and the policies measured to answer it.",
        "Grouping IS the claim: two policies in one experiment assert they faced the same "
        "conditions. Declared by whoever ran it, never inferred from a directory — "
        "otherwise moving a file changes what the result says.",
        "admitperf",
    ),
    "policy under test": (
        "One policy within one experiment, with all of its runs.",
        "A comparison takes one side from the baseline and one from a candidate. Runs "
        "live inside a policy rather than beside it, because a single run has no error "
        "bar. Clinical trials call this an *run*; this package does not, because the "
        "thing on screen is a policy.",
        "admitperf",
    ),
    "run": (
        "One execution of one policy — `r1`, `r2`.",
        "Named so repeats can be told apart and the spread between them computed.",
        "admitperf",
    ),
    "baseline": (
        "The run that admits everything.",
        "Not a placeholder. Without it “the policy refused 27%” has nothing to be "
        "27% of, and no latency figure is interpretable. It is also why `no_admission` "
        "refusing nothing is its job rather than a failure.",
        "survey",
    ),
    # --- verdicts -----------------------------------------------------
    "admit": ("The request goes to the engine.", "Status 200 from your gateway.", "admitperf"),
    "defer": (
        "Hold the request; come back in `retry_after_ms`.",
        "Different instruction to a caller than “no”. A gateway with no queue may "
        "treat it as a refusal, which is why `Decision.admitted` is the question to ask "
        "rather than the verdict itself.",
        "admitperf",
    ),
    "reject": (
        "Refuse the request now, with a reason from the vocabulary.",
        "The reason determines the status code — 503 for server capacity, 429 for "
        "client-attributable causes — so a policy never picks one and two teams' "
        "refusals group together.",
        "admitperf",
    ),
    # --- the findings -------------------------------------------------
    "signal liveness": (
        "The observed range of the quantity the policy reads, over the evaluation.",
        "Not one of the sixteen admission-primary papers surveyed reports it. Without "
        "it, a policy whose signal never approached its threshold cannot be "
        "distinguished from one that evaluated a varying signal and declined to reject. "
        "It is reporting item 3, and the reason this package exists.",
        "survey",
    ),
    "LIVE": (
        "The signal reached its threshold, so the policy had the chance to act.",
        "A run that can support a claim about the policy.",
        "admitperf",
    ),
    "INERT": (
        "The signal never reached the threshold, so the policy could not have fired.",
        "The run's numbers are indistinguishable from no policy at all. The fix is "
        "usually the LOAD: concurrency is arrival rate times request duration, so short "
        "requests cannot fill a cache at any rate.",
        "admitperf",
    ),
    "UNKNOWN": (
        "Nothing was recorded to judge.",
        "Distinct from INERT. A signal that existed and stayed flat is a different "
        "finding from a policy that reads nothing — a baseline is the second.",
        "admitperf",
    ),
    # --- the numbers --------------------------------------------------
    "offered": (
        "Every request that arrived, whether admitted or refused.",
        "The denominator. Dividing by admitted instead rewards a policy for refusing "
        "more rather than refusing better: refuse 95%, serve the rest perfectly, and "
        "the number reads 1.00.",
        "survey",
    ),
    "goodput": (
        "Admitted requests that met their SLO, over everything OFFERED. A refusal "
        "counts as a miss.",
        "The survey's Definition 2, and the metric an admission policy is supposed to "
        "improve. Which denominator a paper used is reporting item 6, because the two "
        "conventions give different numbers for the same run and the gap grows with the "
        "rejection rate.",
        "survey",
    ),
    "TTFT": (
        "Time to first token, for requests that were served.",
        "Of admitted requests only, so it improves whenever a policy sheds — which is "
        "exactly why it must be read beside goodput and never alone.",
        "admitperf",
    ),
    "spread": (
        "The observed range across repeats of one policy.",
        "A gap between two policies smaller than their own spread is not a result. Reporting item 4.",
        "survey",
    ),
    # --- modes --------------------------------------------------------
    "shadow mode": (
        "Calling the policy and ignoring its verdict.",
        "Not a flag: recording is unconditional and enforcement is the host's, so "
        "ignoring the answer is already a complete shadow deployment. It is how you "
        "learn what a policy would have done before letting it do it.",
        "admitperf",
    ),
    "enforce": (
        "The fraction of traffic actually subject to the verdict — `enforce=0.5`.",
        "The rest is measured but not governed, so one run holds both sides under "
        "identical conditions. Split deterministically by request id, so a replay "
        "reproduces it and a retry is treated the same way twice.",
        "admitperf",
    ),
    "fail open": (
        "A policy that raises admits the request, and the fault is recorded.",
        "Failing closed on a bug sheds all traffic, which is worse than the bug.",
        "admitperf",
    ),
    # --- the taxonomy -------------------------------------------------
    "unit": (
        "What is admitted: request, agent-session, or tenant.",
        "Taxonomy axis 1. A session-level policy and a request-level one are not "
        "comparable even when both read the same signal.",
        "survey",
    ),
    "signal quantity": (
        "The taxonomy's term for what a policy watches — `kv_pressure`, `queue_depth`, "
        "`deadline_slack`, `wait_estimate`, `predicted_length`, `batch_state`, `rate`, "
        "`analytic`.",
        "Deliberately separate from a signal's implementation name: the quantity is "
        "what makes two policies comparable across papers.",
        "survey",
    ),
    "signal structure": (
        "scalar, dual-gate, lp-composite, or formal-bound.",
        "Decides what liveness can mean. A dual gate can hold its first signal above "
        "threshold for an entire run and still never fire, so one signal's range does "
        "not establish liveness for it.",
        "survey",
    ),
    "portability": (
        "Class A decides at ingress from observable state; Class B needs control of "
        "batch formation.",
        "Ten of the sixteen surveyed policies are Class A. The other six cannot sit "
        "behind an ingress hook at all, so shipping an approximation under the paper's "
        "name would misattribute the result.",
        "survey",
    ),
}


def groups() -> dict[str, list[str]]:
    """Terms by the question they answer, because an alphabetical list of thirty
    definitions is a glossary nobody reads."""
    return {
        "The three things": ["metrics", "signal", "policy"],
        "How a measurement is organised": ["experiment", "policy under test", "run", "baseline"],
        "What a policy answers": ["admit", "defer", "reject"],
        "The finding": ["signal liveness", "LIVE", "INERT", "UNKNOWN"],
        "The numbers": ["offered", "goodput", "TTFT", "spread"],
        "Ways to run it": ["shadow mode", "enforce", "fail open"],
        "Placing it in the survey": ["unit", "signal quantity", "signal structure", "portability"],
    }
