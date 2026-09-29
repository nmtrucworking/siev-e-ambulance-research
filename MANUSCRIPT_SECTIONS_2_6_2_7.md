# Canonical replacement for Manuscript Sections 2.6–2.7

This text is aligned with the current implementation in `src/policies.py`,
`src/run_experiment.py`, `src/analyze_paired.py`, `configs/experiment_30_paired.json`,
and `RUNBOOK_TO_30_PAIRED.md`. It is intended to replace manuscript descriptions
that still use B5, 54 scenario classes, Haversine routing as scientific evidence,
or a full Pareto/ablation claim that is not implemented in the core experiment.

## 2.6 Experimental design and policy comparison

The evaluation uses a rolling discrete-event simulation (DES) in which dispatch,
hospital destination, vehicle availability, battery state of charge (SOC), charging,
and synthetic hospital occupancy evolve endogenously within each policy run. The
exogenous realization is shared across policies using a common-random-numbers design:
for each `(scenario, replication)` pair, B0, B3, B4, and B6 receive the same incident
stream, random seed, common-random group, initial fleet realization, and exogenous
hospital-capacity trajectory. Policy-dependent vehicle locations, availability times,
SOC trajectories, charging events, and hospital occupancy are updated separately by
the DES and are therefore not frozen as common inputs after decisions begin to diverge.

The core experiment contains three scenarios (`normal`, `peak`, and `high_demand`),
with 30 replications per scenario and 100 incidents per replication. This produces
90 exogenous realizations and 360 policy runs:

\[
3\;\text{scenarios}\times 30\;\text{replications}=90\;\text{realizations},
\]

\[
90\;\text{realizations}\times 4\;\text{policies}=360\;\text{policy runs}.
\]

Final scientific runs use OSMnx network-routing artifacts. The Haversine routing mode
is restricted to engineering smoke tests and its numerical outputs are excluded from
the paper's final empirical results. Until locally calibrated traffic parameters are
available, OSMnx edge travel times are interpreted as a network/free-flow reference
rather than as validated real-time traffic conditions for Ho Chi Minh City.

Four policies form the implemented comparison ladder. **B0** is a naive nearest-dispatch
reference: it requires an available ambulance, route reachability, an emergency-receiving
hospital, and sufficient physical energy to reach that hospital, but deliberately does
not enforce specialty compatibility, modeled hospital capacity, or reserve-SOC rules.
**B3** adds hard hospital capability and modeled-capacity feasibility. **B4** further
adds deterministic EV feasibility, requiring sufficient energy for the vehicle-to-incident,
incident-to-hospital, and hospital-to-charger path while preserving the vehicle's reserve
SOC. **B6** is the integrated uncertainty-aware policy. It applies the same hospital hard
rules and an uncertainty-buffered SOC constraint, and then solves a binary ε-constraint
MILP over the currently feasible candidates.

For B6, exactly one candidate is selected. Its primary objective minimizes response time
plus urgency-weighted SLA lateness. Two pre-specified ε bounds constrain (i) transport time
plus modeled hospital waiting time and (ii) uncertainty-adjusted energy fraction. The
transport slack is priority dependent, whereas the energy slack is fixed by the runtime
configuration. If the MILP solver fails, the implementation records an ε-relaxed fallback
while retaining the hard feasibility filters. The core experiment therefore evaluates one
pre-specified B6 operating point. It does **not** constitute a full Pareto-frontier search;
any Pareto claim requires a separate ε-grid sweep with nondominated-solution extraction.

B0 is retained to quantify how a simple nearest-dispatch rule behaves when clinically and
operationally relevant constraints are omitted. Because B0 does not share the same hard
feasible set as B3/B4/B6, differences in capability, capacity, or reserve-SOC violations
against B0 are interpreted descriptively rather than as a like-for-like superiority test.
The more informative incremental comparisons for the integrated policy are B6 versus B3
and B6 versus B4, because these policies progressively add hospital and EV constraints.

## 2.7 Outcome measures and paired statistical analysis

The statistical unit is the scenario-level paired replication, not the individual incident.
Each scenario contributes 30 paired observations for each policy comparison. For example,
the B6-versus-B4 effect for a metric is computed from

\[
d_r = M_{B6,r}-M_{B4,r},\qquad r=1,\ldots,30,
\]

with the sign re-oriented when necessary so that a positive value consistently denotes
better performance by B6. The same pairing is applied to B6-B3 and, as a contextual naive
reference, B6-B0. Treating the 100 incidents inside a replication as 100 independent
experimental units would underestimate uncertainty and is therefore avoided.

Replication-level outcomes include served rate, overall feasible rate, SLA pass rate,
priority-specific feasible rates, average response time, P95 response time for P1 incidents,
time to a compatible hospital, unserved incidents, SOC-reserve violations, hospital-capacity
violations, hospital-capability violations, and B6 solver diagnostics such as average solver
time and maximum reported MIP gap. These metrics are computed from the policy-specific DES
trajectory after each 100-incident replication.

For every scenario, the paired analysis reports the mean and median oriented improvement,
a 95% bootstrap confidence interval for the mean paired improvement, and the paired
standardized effect size

\[
d_z=\frac{\bar d}{s_d},
\]

where \(\bar d\) and \(s_d\) are the mean and sample standard deviation of the 30 paired
differences. The current analysis uses 5,000 bootstrap resamples of the paired-difference
vector. Confidence intervals and effect sizes are therefore based on independent simulation
replications rather than on within-replication incident resampling.

The current core analyzer does not produce hypothesis-test p-values. Accordingly, the paper
should report estimation results (paired effects, confidence intervals, and effect sizes)
without claiming formal statistical significance unless a paired hypothesis test is added
to the pre-specified analysis pipeline before the final run.

Two analyses are explicitly outside the scope of the core 360-run experiment. First, a full
Pareto frontier requires a separate ε-grid sweep of B6. Second, the B0/B3/B4/B6 policy ladder
is not a controlled one-factor-at-a-time ablation study; any research question that attributes
the marginal contribution of urgency, hospital modeling, SOC, or uncertainty individually
requires dedicated `Full-minus-component` variants executed under the same paired seeds.

Finally, modeled hospital receiving capacity is a synthetic simulation state rather than
observed bed availability, and several charging, handover, occupancy-hold, and energy-buffer
parameters are explicit modeling assumptions. These quantities must be calibrated where
evidence is available and otherwise subjected to sensitivity analysis, including a low-SOC
or energy-stress regime if the baseline scenarios do not activate the EV constraints often
enough to identify their marginal effect.
